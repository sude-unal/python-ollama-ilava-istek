# Ürün Analizi (Ollama + Llava)

Yerel *Ollama* sunucusuna bağlanan küçük bir *Windows masaüstü* uygulaması. Seçili ürün metnini veya ekran görüntüsünü analiz eder; cevabı *Türkçe* üretmeye yönelik sistem istemi kullanır.

## Özellikler

- *F8* — Pano üzerinden seçili metni kopyalayıp analiz ister (metin modeli: llama3.2).
- *F7* — Panodaki ekran görüntüsünü (ör. Win+Shift+S) görüntü modeliyle analiz eder (llava).
- Sekmeler; her sekme bağımsız istek.
- Arayüz: Tkinter; koyu tema ve “kâğıt” stili çıktı alanı.

## Gereksinimler

- *Windows* (kısayol ve keyboard kullanımı buna göre; diğer işletim sistemlerinde test edilmemiştir).
- [Python](https://www.python.org/) 3.10 veya üzeri
- [Ollama](https://ollama.com/) kurulu ve çalışır durumda (varsayılan adres: http://127.0.0.1:11434)

Aşağıdaki modellerin Ollama’da yüklü olması gerekir (isimler app.py içinde değiştirilebilir):

bash
ollama pull llama3.2
ollama pull llava


## Kurulum

bash
git clone <bu-reponun-url-si> .
cd python-ollama-ilava-istek
python -m pip install -r requirements.txt


> python -m pip, pip ile aynı Python sürümünü kullanır; birden fazla Python kurulumunuz varsa tercih edin.

## Çalıştırma

1. Ollama servisini başlatın (Ollama arayüzünden veya arka planda çalışıyor olmalı).
2. Proje klasöründe:

bash
python app.py


## Kullanım

| Tuş / işlem | Açıklama |
|-------------|----------|
| *F8* | Önce metni seçin, sonra F8. Uygulama Ctrl+C gönderir ve panodaki metni alır, analiz ister. |
| *F7* | *Win+Shift+S* ile bölge seçip panoya ekran görüntüsü alın, ardından F7. |
| *Durdur* | Aktif sekmedeki devam eden isteği iptal eder. |
| *+ Yeni Sekme* | Yeni, bağımsız analiz alanı. |

Hedef API adresi ve model adları app.py başındaki sabitlerle ayarlanır:

- API_BASE — Ollama (genelde değiştirilmez)
- MODEL — metin: llama3.2
- VISION_MODEL — görüntü: llava

## Sorun giderme

- *Ollama bağlanmıyor* — Ollama’nın çalıştığını ve gerekirse ollama serve / sistem tepsisinden servisin açık olduğunu kontrol edin.
- *F8 / F7 hiç çalışmıyor* — keyboard kütüphanesi Windows’ta yönetici yetkisi isteyebilir. Uygulamayı “Yönetici olarak çalıştır”ı deneyin.
- *Zaman aşımı (timeout)* — Uzun metin veya yavaş donanım; app.py içinde TEXT_TIMEOUT / VISION_TIMEOUT değerlerini artırabilir veya daha küçük ekran görüntüsü / daha kısa metin deneyin.

## Bağımlılıklar

requests, keyboard, pyperclip, Pillow (detay: requirements.txt).

## Lisans

(Bu bölüme kendi lisans tercihinizi ekleyin — örn. MIT, GPL, veya “Tüm hakları saklıdır”.)