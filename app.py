import base64
import io
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

import keyboard
import pyperclip
import requests
from PIL import Image, ImageGrab

API_BASE = "http://127.0.0.1:11434"
API_GENERATE = f"{API_BASE}/api/generate"
API_CHAT = f"{API_BASE}/api/chat"
MODEL = "llama3.2"
VISION_MODEL = "llava"
TEXT_TIMEOUT = 240
VISION_TIMEOUT = 420
MAX_RETRIES = 1

SYSTEM_TURKCE_ONLY = (
    "Sen yardımcı bir asistansın. Tüm yanıtını yalnızca Türkçe yaz. "
    "Ingilizce kelime, baslik, etiket veya cumle kullanma."
)

COLORS = {
    "bg": "#09090b",
    "card": "#18181b",
    "border": "#3f3f46",
    "border_subtle": "#27272a",
    "text": "#fafafa",
    "muted": "#a1a1aa",
    "faint": "#52525b",
    "accent": "#6366f1",
    "on_accent": "#f8fafc",
    "bar_top": "#6366f1",
    "dot_ok": "#22c55e",
    "dot_busy": "#f59e0b",
    "overlay_scrim": "#0c0c0e",
    "paper": "#f6f0e1",
    "paper_shade": "#e8e4d9",
    "paper_ink": "#0c0a09",
    "paper_muted": "#6b6560",
    "paper_margin": "#b91c1c",
    "paper_ruling": "#1d4ed8",
}


def build_prompt(selected_text: str) -> str:
    return (
        "Aşağıdaki ürün metnini incele.\n"
        "Yanıtın tamamı Türkçe olsun.\n"
        "1) Avantajlar\n2) Dezavantajlar\n"
        "3) 5 kritik soru ve kısa cevapları (Soru:/Cevap:)\n\n"
        f"Ürün metni:\n{selected_text}"
    )


def build_image_prompt() -> str:
    return (
        "Bu ekran görüntüsündeki ürün/ürün sayfasını incele. "
        "Yazıları okuyup Türkçe analiz ver.\n"
        "1) Avantajlar\n2) Dezavantajlar\n"
        "3) 5 kritik soru ve kısa cevapları (Soru:/Cevap:)\n"
    )


def _post_with_retry(url: str, payload: dict, timeout_seconds: int) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return requests.post(url, json=payload, timeout=timeout_seconds)
        except requests.ReadTimeout as exc:
            last_exc = exc
            if attempt >= MAX_RETRIES:
                raise
            time.sleep(1.5 + attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("Beklenmeyen istek hatası")


def ask_model(user_text: str) -> str:
    payload = {
        "model": MODEL,
        "system": SYSTEM_TURKCE_ONLY,
        "prompt": user_text,
        "stream": False,
    }
    response = _post_with_retry(API_GENERATE, payload, TEXT_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    return data.get("response", "Modelden metin cevabı alınamadı.")


def _clipboard_b64_png_max_side(max_side: int = 1024) -> str | None:
    raw = ImageGrab.grabclipboard()
    if raw is None:
        return None

    img: Image.Image | None = None
    if isinstance(raw, Image.Image):
        img = raw
    elif isinstance(raw, list) and raw and isinstance(raw[0], str):
        try:
            img = Image.open(raw[0])
        except OSError:
            return None

    if img is None:
        return None

    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    m = max(w, h)
    if m > max_side and m > 0:
        scale = max_side / m
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample)

    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _ollama_error_body(response: requests.Response) -> str:
    try:
        j = response.json()
        if j.get("error"):
            return str(j["error"])
    except ValueError:
        pass
    return (response.text or "").strip() or f"(bos cevap, http {response.status_code})"


def ask_model_vision(user_prompt: str, image_b64: str) -> str:
    chat_body = {
        "model": VISION_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_TURKCE_ONLY},
            {"role": "user", "content": user_prompt, "images": [image_b64]},
        ],
    }
    r1 = _post_with_retry(API_CHAT, chat_body, VISION_TIMEOUT)
    if r1.status_code == 200:
        content = (r1.json().get("message") or {}).get("content") or ""
        return content.strip() or "Görselden yanıt alınamadı."

    gen_body = {
        "model": VISION_MODEL,
        "system": SYSTEM_TURKCE_ONLY,
        "prompt": user_prompt,
        "images": [image_b64],
        "stream": False,
    }
    r2 = _post_with_retry(API_GENERATE, gen_body, VISION_TIMEOUT)
    if r2.status_code == 200:
        content = r2.json().get("response") or ""
        return content.strip() or "Görselden yanıt alınamadı."

    raise RuntimeError(
        f"Görsel istek başarısız:\n"
        f"/api/chat -> {r1.status_code} {_ollama_error_body(r1)}\n"
        f"/api/generate -> {r2.status_code} {_ollama_error_body(r2)}"
    )


@dataclass
class TabState:
    key: str
    title: str
    frame: tk.Frame
    output_text: tk.Text
    loading_overlay: tk.Frame
    loading_label: tk.Label
    sub_label: tk.Label
    progress: ttk.Progressbar
    busy: bool = False
    request_id: int = 0
    last_status: str = "Hazır"


class HotkeyAssistantApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Ürün Analizi (F8/F7 - Sekmeli)")
        self.root.geometry("980x680")
        self.root.minsize(760, 520)
        self.root.configure(bg=COLORS["bg"])

        self.status_var = tk.StringVar(value="Hazır")
        self.ui_queue: queue.Queue[tuple[str, ...]] = queue.Queue()
        self._hotkey_ids: list[int] = []
        self._tabs: dict[str, TabState] = {}
        self._tab_count = 0

        self._setup_styles()
        self._build_shell()
        self._add_tab()

        self.register_hotkeys()
        self.root.after(80, self.process_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _resolve_ui_family(self) -> str:
        import tkinter.font as tkfont

        try:
            probe = tkfont.Font(root=self.root, family="Segoe UI Variable", size=12)
            if "segoe ui variable" in (probe.actual().get("family", "").lower()):
                return "Segoe UI Variable"
        except (tk.TclError, OSError, ValueError):
            pass
        return "Segoe UI"

    def _build_shell(self) -> None:
        self._ui_family = self._resolve_ui_family()

        shell = tk.Frame(self.root, bg=COLORS["bg"])
        shell.pack(fill=tk.BOTH, expand=True)
        tk.Frame(shell, bg=COLORS["bar_top"], height=2).pack(fill=tk.X)

        main = tk.Frame(shell, bg=COLORS["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=20, pady=(16, 16))

        head = tk.Frame(main, bg=COLORS["bg"])
        head.pack(fill=tk.X)
        tk.Label(
            head,
            text="Ürün Analizi",
            font=(self._ui_family, 20, "bold"),
            fg=COLORS["text"],
            bg=COLORS["bg"],
        ).pack(side=tk.LEFT, anchor="w")

        top_buttons = tk.Frame(head, bg=COLORS["bg"])
        top_buttons.pack(side=tk.RIGHT, anchor="e")
        tk.Button(
            top_buttons,
            text="+ Yeni Sekme",
            command=self._add_tab,
            bg=COLORS["accent"],
            fg=COLORS["on_accent"],
            borderwidth=0,
            padx=12,
            pady=6,
            font=(self._ui_family, 10, "bold"),
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            top_buttons,
            text="Durdur",
            command=self._on_user_stop,
            bg="#991b1b",
            fg=COLORS["on_accent"],
            borderwidth=0,
            padx=12,
            pady=6,
            font=(self._ui_family, 10, "bold"),
            cursor="hand2",
        ).pack(side=tk.LEFT)

        status_row = tk.Frame(main, bg=COLORS["bg"])
        status_row.pack(fill=tk.X, pady=(12, 12))
        self._status_dot = tk.Label(status_row, text="●", fg=COLORS["dot_ok"], bg=COLORS["bg"])
        self._status_dot.pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(
            status_row,
            textvariable=self.status_var,
            font=(self._ui_family, 11),
            fg=COLORS["text"],
            bg=COLORS["bg"],
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        card = tk.Frame(main, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["border"])
        card.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(card)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        tk.Label(
            main,
            text="F8: seçili metni gönderir | F7: panodaki ekran görüntüsünü gönderir | Her sekme bağımsızdır",
            font=(self._ui_family, 10),
            fg=COLORS["faint"],
            bg=COLORS["bg"],
        ).pack(fill=tk.X, pady=(10, 0))

    def _create_tab_page(self, parent: tk.Widget) -> tuple[tk.Text, tk.Frame, tk.Label, tk.Label, ttk.Progressbar]:
        frame = tk.Frame(parent, bg=COLORS["paper_shade"])
        frame.pack(fill=tk.BOTH, expand=True)

        paper = tk.Frame(frame, bg=COLORS["paper"], highlightthickness=1, highlightbackground=COLORS["border_subtle"])
        paper.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = tk.Frame(paper, bg=COLORS["paper"])
        row.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        tk.Frame(row, width=2, bg=COLORS["paper_margin"]).pack(side=tk.LEFT, fill=tk.Y)
        tk.Frame(row, width=1, bg=COLORS["paper_ruling"]).pack(side=tk.LEFT, fill=tk.Y)

        text_col = tk.Frame(row, bg=COLORS["paper"])
        text_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_col.grid_rowconfigure(0, weight=1)
        text_col.grid_columnconfigure(0, weight=1)

        output_text = tk.Text(
            text_col,
            wrap=tk.WORD,
            font=(self._ui_family, 13),
            bg=COLORS["paper"],
            fg=COLORS["paper_ink"],
            insertbackground=COLORS["paper_ink"],
            padx=20,
            pady=16,
            borderwidth=0,
            highlightthickness=0,
        )
        output_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(text_col, orient=tk.VERTICAL, style="Paper.Vertical.TScrollbar", command=output_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        output_text.configure(yscrollcommand=sb.set)

        loading_overlay = tk.Frame(frame, bg=COLORS["overlay_scrim"])
        block = tk.Frame(loading_overlay, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["border_subtle"])
        block.place(relx=0.5, rely=0.5, anchor="center")
        inner = tk.Frame(block, bg=COLORS["card"])
        inner.pack(padx=28, pady=22)

        loading_label = tk.Label(inner, text="Cevap bekleniyor...", font=(self._ui_family, 14, "bold"), fg=COLORS["text"], bg=COLORS["card"])
        loading_label.pack(pady=(0, 12))
        progress = ttk.Progressbar(inner, mode="indeterminate", length=280, style="Accent.Horizontal.TProgressbar")
        progress.pack()
        sub_label = tk.Label(inner, text="Ollama modeli yanıt üretiyor", font=(self._ui_family, 9), fg=COLORS["muted"], bg=COLORS["card"])
        sub_label.pack(pady=(10, 0))

        return output_text, loading_overlay, loading_label, sub_label, progress

    def _add_tab(self) -> None:
        self._tab_count += 1
        key = f"tab-{self._tab_count}"
        title = f"Sekme {self._tab_count}"
        tab_frame = tk.Frame(self.notebook, bg=COLORS["paper_shade"])
        out, overlay, loading_label, sub_label, progress = self._create_tab_page(tab_frame)

        state = TabState(
            key=key,
            title=title,
            frame=tab_frame,
            output_text=out,
            loading_overlay=overlay,
            loading_label=loading_label,
            sub_label=sub_label,
            progress=progress,
        )
        self._tabs[key] = state
        self.notebook.add(tab_frame, text=title)
        self.notebook.select(tab_frame)
        self._set_output(state, self._empty_text())
        state.last_status = f"{title} açıldı. Hazır."
        self.status_var.set(state.last_status)

    def _on_tab_changed(self, _event: tk.Event) -> None:
        active = self._active_tab()
        if active:
            self.status_var.set(active.last_status)

    def _active_tab(self) -> TabState | None:
        current = self.notebook.select()
        for tab in self._tabs.values():
            if str(tab.frame) == current:
                return tab
        return None

    def _empty_text(self) -> str:
        return (
            "Beklemede\n\n"
            "1) Metin: tarayicida metni sec ve F8\n"
            "2) Görsel: Win+Shift+S ile panoya al ve F7\n"
            "3) Durdur butonu aktif sekmenin istegini keser\n"
        )

    def _set_busy_dot(self, busy: bool) -> None:
        self._status_dot.config(fg=COLORS["dot_busy"] if busy else COLORS["dot_ok"])

    def _show_loading(self, tab: TabState, message: str, sub: str = "Ollama modeli yanıt üretiyor") -> None:
        tab.loading_label.config(text=message)
        tab.sub_label.config(text=sub)
        tab.loading_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        tab.progress.start(10)
        self._set_busy_dot(True)

    def _hide_loading(self, tab: TabState) -> None:
        tab.progress.stop()
        tab.loading_overlay.place_forget()
        self._set_busy_dot(False)

    def _set_output(self, tab: TabState, content: str) -> None:
        tab.output_text.configure(state=tk.NORMAL)
        tab.output_text.delete("1.0", tk.END)
        tab.output_text.insert(tk.END, content)
        tab.output_text.configure(state=tk.DISABLED)

    def _req_ok(self, tab: TabState, op_id: int) -> bool:
        return op_id == tab.request_id

    def _start_request(self, tab: TabState) -> int:
        tab.request_id += 1
        tab.busy = True
        return tab.request_id

    def _end_request_if_current(self, tab: TabState, op_id: int) -> None:
        if self._req_ok(tab, op_id):
            tab.busy = False

    def _on_user_stop(self) -> None:
        tab = self._active_tab()
        if not tab:
            return
        tab.request_id += 1
        tab.busy = False
        self._hide_loading(tab)
        self.ui_queue.put(("status", tab.key, f"{tab.title}: istek durduruldu."))

    def register_hotkeys(self) -> None:
        self._hotkey_ids = [
            keyboard.add_hotkey("f8", self.on_f8, suppress=False),
            keyboard.add_hotkey("f7", self.on_f7, suppress=False),
        ]

    def on_f8(self) -> None:
        tab = self._active_tab()
        if not tab or tab.busy:
            return
        op_id = self._start_request(tab)
        self.ui_queue.put(("loading_start", tab.key, "Metin alınıyor...", "F8"))
        self.ui_queue.put(("status", tab.key, f"{tab.title}: metin alınıyor..."))
        threading.Thread(target=self._worker_text, args=(tab.key, op_id), daemon=True).start()

    def on_f7(self) -> None:
        tab = self._active_tab()
        if not tab or tab.busy:
            return
        op_id = self._start_request(tab)
        self.ui_queue.put(("loading_start", tab.key, "Pano görseli okunuyor...", f"Ollama: {VISION_MODEL}"))
        self.ui_queue.put(("status", tab.key, f"{tab.title}: pano görseli aranıyor..."))
        threading.Thread(target=self._worker_image, args=(tab.key, op_id), daemon=True).start()

    def _worker_text(self, tab_key: str, op_id: int) -> None:
        tab = self._tabs.get(tab_key)
        if not tab:
            return
        try:
            prev = pyperclip.paste()
            keyboard.send("ctrl+c")
            time.sleep(0.2)
            if not self._req_ok(tab, op_id):
                return

            selected = pyperclip.paste().strip()
            if not selected:
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key, "Seçili metin bulunamadı. Tekrar seçip F8 bas."))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: metin seçimi bulunamadı."))
                return

            self.ui_queue.put(("loading_start", tab.key, "Modelden cevap bekleniyor...", "Metin modu"))
            if selected == prev:
                self.ui_queue.put(("status", tab.key, f"{tab.title}: aynı pano metni gönderiliyor..."))

            answer = ask_model(build_prompt(selected))
            if not self._req_ok(tab, op_id):
                return
            self.ui_queue.put(("loading_stop", tab.key))
            self.ui_queue.put(("result", tab.key, answer))
            self.ui_queue.put(("status", tab.key, f"{tab.title}: tamamlandı."))
        except requests.ReadTimeout:
            if self._req_ok(tab, op_id):
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key,
                    "Zaman aşımı oldu. Bu genelde modelin yavaş olması (CPU/free), uzun metin veya Ollama yoğunluğu.\n"
                    "Tekrar dene ya da daha kısa metin gönder."
                ))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: timeout (metin)."))
        except requests.RequestException as err:
            if self._req_ok(tab, op_id):
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key, f"İstek hatası: {err}"))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: istek hatası."))
        except Exception as err:  # noqa: BLE001
            if self._req_ok(tab, op_id):
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key, f"Hata: {err}"))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: beklenmeyen hata."))
        finally:
            self._end_request_if_current(tab, op_id)

    def _worker_image(self, tab_key: str, op_id: int) -> None:
        tab = self._tabs.get(tab_key)
        if not tab:
            return
        try:
            b64 = _clipboard_b64_png_max_side()
            if not b64:
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key,
                    "Panoda görsel yok. Win+Shift+S ile önce panoya ekran görüntüsü al, sonra F7."
                ))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: pano görseli yok."))
                return

            if not self._req_ok(tab, op_id):
                return
            self.ui_queue.put(("loading_start", tab.key, "Görsel analiz bekleniyor...", f"Model: {VISION_MODEL}"))
            answer = ask_model_vision(build_image_prompt(), b64)
            if not self._req_ok(tab, op_id):
                return
            self.ui_queue.put(("loading_stop", tab.key))
            self.ui_queue.put(("result", tab.key, answer))
            self.ui_queue.put(("status", tab.key, f"{tab.title}: görsel analiz tamamlandı."))
        except requests.ReadTimeout:
            if self._req_ok(tab, op_id):
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key,
                    "Görsel isteği timeout oldu (read timeout). Bu çoğunlukla model yavaşlığı/CPU yükünden olur.\n"
                    "Küçük ekran görüntüsü deneyin veya tekrar F7 basın."
                ))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: zaman aşımı (görsel)."))
        except Exception as err:  # noqa: BLE001
            if self._req_ok(tab, op_id):
                self.ui_queue.put(("loading_stop", tab.key))
                self.ui_queue.put(("result", tab.key, f"Hata: {err}"))
                self.ui_queue.put(("status", tab.key, f"{tab.title}: görsel hata."))
        finally:
            self._end_request_if_current(tab, op_id)

    def process_ui_queue(self) -> None:
        while not self.ui_queue.empty():
            item = self.ui_queue.get()
            event = item[0]
            if event == "status":
                if len(item) >= 3:
                    tab_key = str(item[1])
                    status_text = str(item[2])
                    tab_for_status = self._tabs.get(tab_key)
                    if tab_for_status:
                        tab_for_status.last_status = status_text
                    active = self._active_tab()
                    if active and active.key == tab_key:
                        self.status_var.set(status_text)
                else:
                    self.status_var.set(str(item[1]))
                continue

            if len(item) < 2:
                continue
            tab = self._tabs.get(str(item[1]))
            if not tab:
                continue

            if event == "loading_start":
                msg = str(item[2]) if len(item) >= 3 else "Cevap bekleniyor..."
                sub = str(item[3]) if len(item) >= 4 else "Ollama modeli yanıt üretiyor"
                self._show_loading(tab, msg, sub)
            elif event == "loading_stop":
                self._hide_loading(tab)
            elif event == "result":
                self._set_output(tab, str(item[2]))
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()

        self.root.after(80, self.process_ui_queue)

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=COLORS["card"],
            background=COLORS["accent"],
            bordercolor=COLORS["border_subtle"],
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
            thickness=6,
        )
        style.configure(
            "Paper.Vertical.TScrollbar",
            background=COLORS["paper_shade"],
            troughcolor=COLORS["paper"],
            bordercolor=COLORS["border_subtle"],
            arrowcolor=COLORS["paper_muted"],
        )
        style.map(
            "Paper.Vertical.TScrollbar",
            background=[("active", COLORS["paper_muted"])],
            arrowcolor=[("active", COLORS["paper_ink"])],
        )

    def on_close(self) -> None:
        try:
            for hid in self._hotkey_ids:
                keyboard.remove_hotkey(hid)
        finally:
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    HotkeyAssistantApp(root)
    root.mainloop()
