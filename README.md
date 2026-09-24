# meetseen — Teams toplantısından iş notu 📝

> **meetseen** — local meeting notes for mixed Turkish/English Teams recordings:
> MP4 in → work-only structured notes (decisions, action items with owners,
> open questions, risks, **what was shown on screen**) out. Chit-chat is
> filtered but kept in an audit log. Apple Silicon Mac only (on-device Whisper
> STT + Apple Vision OCR).

⚠️ **Yalnız macOS (Apple Silicon):** ekran okuma Apple Vision'a, transkript
MLX'e bağlı — Linux/Windows şimdilik kapsam dışı.

Teams toplantı kaydını (MP4) sürükle → **sadece iş kısmını** içeren yapılandırılmış
notlar çıksın: yönetici özeti, kararlar, aksiyonlar (sahip + termin), açık
sorular, riskler ve **ekranda gösterilenler**. Günlük sohbet ayıklanır ama
denetim logunda tutulur — hiçbir iş içeriği sessizce kaybolmaz.

```
MP4 ─► ses ─► mlx-whisper (TR/EN, Apple Silicon'da hızlı) ─► ham transkript
      │
      └─► ekran ─► ffmpeg kare örnekleme ─► pHash tekilleştirme
                  ─► Apple Vision OCR (tr-TR+en-US, cihaz içi, ücretsiz)

      LLM Aşama A: İŞ / SOHBET / BELİRSİZ sınıflandırma + dolgu temizliği
                   ("eee, ııı, şey" gider; "ok, bunu yapalım" kararı KALIR)
      LLM Aşama B: yapılandırılmış not + ekranda gösterilenlerin özeti
      ─► NOT.md + transkriptler + sohbet-logu.md   (Markdown arşiv)
```

## En kolay kullanım

**Kurulum (bir kere):**

```bash
./install.sh          # bağımlılıklar + .venv (ffmpeg, Xcode CLT kontrolü)
./install.sh --app    # ayrıca meetseen.app üretir ve /Applications'a kopyalar
```

**Günlük kullanım** — üç giriş yolu:

1. **meetseen.app** (/Applications veya klasörde): çift tıkla → pencere açılır,
   sunucuyu kendisi başlatır; videoyu uygulama ikonuna bırakabilirsin.
2. **`meetseen.command`** (masaüstü kısayolu): çift tıkla → tarayıcıda arayüz
   açılır (sunucu arka planda çalışır, yalnız bu makineden erişilebilir).
3. **Terminal:** `.venv/bin/python meetseen.py toplantı.mp4` (bkz. "Diğer kullanımlar")

Arayüz akışı:

1. **Toplantı kaydını seç** — 📁 düğmesi macOS'un dosya penceresini açar;
   ya da videoyu sürükle-bırak / yol yapıştır. (Kayıt senden beklenir;
   arayüz diskteki dosyaları kendiliğinden taraMAZ.)
2. **Not ayarı** — başlık (boş = dosya adı), **proje klasörü (opsiyonel)**,
   "ekran karelerini kaydet" anahtarı.
3. **Proje bağlamı** — proje klasörü verirsen meetseen dizin ağacı +
   dokümanlar + kod sembollerini çıkarır; notlar bu bağlamla anlamlandırılır
   ("1703 ticket'ı", "Agenda modülü" gibi referanslar çözümlenir) ve
   transkriptteki fonetik terimler projedeki gerçek adlara düzeltilir.
   Projenin KÖK klasörünü ver (workspace değil; içinde bin/obj/node_modules
   otomatik atlanır). İçerik yalnızca transkript+OCR gibi LLM'e gider.
4. **Akış** — 6 adımlı gösterge + canlı log + geçen süre; gerekirse ■ İptal.
5. **Sonuç** — NOT.md tarayıcıda önizlenir; 📂 Klasörü Aç ile Finder'da açılır.
6. **Geçmiş notlar** — eski notlar listeden tıklayınca geri gelir.

Sunucuyu durdurmak için: `pkill -f webui.py` (`.command` yolunda Terminal
kapansa da sunucu yaşamaya devam eder; `meetseen.app` ise çıkışta **kendi
başlattığı** sunucuyu kapatır). meetseen.app pencerede "sunucu başlatılıyor"
dediyse: klasör seçimi sorulduğunda bu repoyu göster, ya da menüden
**meetseen ▸ Klasörü Yeniden Seç…** (⌘R).

Terminal'den doğrudan da çalıştırılabilir:

```bash
.venv/bin/python meetseen.py toplantı.mp4 --title "Sprint 42 Planlama"
```

Çıktı: `meetseen-cikti/YYYY-AA-GG_SSDD-baslik/NOT.md` (+ ham/temiz transkript,
sohbet logu, `not.json`, `--keep-llm` yerine kareler kısayolla otomatik kaydedilir).
Bu klasörü Obsidian vault'u olarak açarsan tüm notların aranabilir olur.

## Kurulum

```bash
cd ~/Desktop/meetseen
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Bağımlılıklar (eksikse): `brew install ffmpeg` ve `xcode-select --install`
(Swift derleyicisi, Vision OCR için). Vision OCR aracı ilk çalıştırmada
otomatik derlenir.

## Diğer kullanımlar

```bash
# Teams'in kendi transkriptini kullan (konuşmacı etiketli olur; STT atlanır)
.venv/bin/python meetseen.py toplantı.mp4 --vtt Teams-Transkript.tr.vtt --title "Mimari Kurulu"

# %100 lokal: Ollama backend'i
.venv/bin/python meetseen.py toplantı.mp4 --backend ollama --model qwen3:30b-a3b

# proje bağlamıyla (kod + dokümanlar notları anlamlandırır)
.venv/bin/python meetseen.py toplantı.mp4 --project ~/Yollar/projem --title "Sprint 42"

# API: Claude veya Gemini (ücretli katman anahtarı)
.venv/bin/python meetseen.py toplantı.mp4 --backend claude
GEMINI_API_KEY=... .venv/bin/python meetseen.py toplantı.mp4 --backend gemini

# sadece transkript + ekran OCR (LLM'siz)
.venv/bin/python meetseen.py toplantı.mp4 --skip-llm
```

## Yapılandırma — `~/.meetseen.json`

Ayarlar bu dosyadan okunur (CLI bayrakları önce gelir; `auto` backend sırası:
Anthropic anahtarı → Gemini anahtarı → Ollama → mock):

```json
{
  "backend": "claude",
  "claude_model": "glm-5.3",
  "anthropic_base_url": "https://api.z.ai/api/anthropic",
  "glossary": ["API gateway", "backlog", "deployment", "ProjeAdı-X"],
  "project_dir": "/Users/…/projem"
}
```

`glossary` (proje sözlüğü) önerilir: Whisper TR/EN karışık konuşmada Türkçe
cümle içindeki İngilizce terimleri fonetik yazabilir ("API gateway" →
"apı gata") — sözlükteki terimler LLM aşamasındaki yazım düzeltmesine ipucu
olur. `project_dir` her koşuda otomatik bağlam eklenecek proje klasörü
(CLI `--project` önceliklidir); `project_digest_chars` (öntanımlı 36000)
bağlam bütçesini sınırlar. Doküman seçimi **en yeniden eskiye** yapılır —
toplantılar güncel dosyaları konuşur; README yığını yerine bug planları,
hazırlık notları öne çıkar. `anthropic_base_url` gateway kullanan
Anthropic-uyumlu servisler için (ör. Z.ai).

`claude_thinking` reasoning (düşünen) modeller içindir: `"disabled"` (öntanımlı)
modelin tüm yanıt bütçesini düşünmeye harcayıp cevabı boş vermesini önler;
modelde düşünme istersen `"enabled"` yap. `anthropic_base_url` gateway kullanan
Anthropic-uyumlu servisler için (ör. Z.ai).

| Backend | Kurulum | Veri nereye gider | Not kalitesi (TR/EN karışık) |
|---|---|---|---|
| `claude` | `ANTHROPIC_API_KEY` (veya `ANTHROPIC_AUTH_TOKEN`) | transkript+OCR metni API'ye | en iyi |
| `gemini` | `GEMINI_API_KEY` (yalnız **ücretli** katman — ücretsiz katman veriyle eğitir!) | transkript+OCR metni API'ye | en iyi |
| `ollama` | `brew install ollama` → `ollama pull qwen3:30b-a3b` (~18 GB) | **hiçbir yere** (%100 lokal) | iyi (24 GB RAM'de 27B+ sınıf şart) |
| `mock` | — | — | test amaçlı, sahte çıktı |

**Şirket toplantısı gizliliği:** `ollama` backend'inde ses, transkript, ekran
kareleri dahil hiçbir veri makineni terk etmez. API backend'lerinde yalnızca
transkript + OCR metni gönderilir (ses/görüntü gönderilmez).

## Ayarlar (arayüzden)

Başlıktaki **⚙︎ Ayarlar** ekranından, dosyayı elden düzenlemeden:

- **Transkript modeli** — `whisper-large-v3` (en doğru, öntanımlı) /
  `whisper-large-v3-turbo` (en hızlı) / özel MLX repo adı
- **LLM backend ve modeli** — auto / claude / gemini / ollama / mock
- **Öntanımlı proje klasörü**, **sözlük (glossary)**, **düşünme modu**

Kaydet → `~/.meetseen.json` güncellenir; bir sonraki not üretiminde geçerli
olur (yeniden başlatma gerekmez). API anahtarı gibi gizli bilgiler bu ekranda
tutulmaz — `~/.meetseen.json`'a elle yazılır.

## 24 GB RAM (M5 Pro) notları

- **STT:** `whisper-large-v3-turbo` ~2 GB bellek, 1 saat ses ≈ 2-4 dk. ✓
- **Lokal LLM:** `qwen3:30b-a3b` (MoE, ~18 GB) veya `gemma3:27b` sığar ama
  not üretimi yavaşlar (1 saat toplantı ≈ 10-25 dk). 27B altı modeller
  Türkçe not kalitesini belirgin düşürür — mümkünse API backend'i kullan.
- API backend'inde RAM önemsiz; Whisper + OCR her zaman lokal.

## Teams ipuçları

- Kayıt: toplantı kaydı OneDrive `Kayıtlar/Recordings` klasörüne iner
  (~400 MB/saat), **varsayılan olarak 120 gün sonra silinir** — zamanında indir.
- Transkript (`.vtt`): toplantı sohbeti → **Recap** → Transcript → **İndir**.
  Teams transkripti konuşmacı etiketlidir; TR/EN karışık konuşmada Whisper
  genelde daha temiz sonuç verir — ikisini de dene.
- Teams'te dil tek seçilebildiği için cümle içi TR/EN geçişlerinde Teams'in
  kendi transkripti zayıf kalır (çok dilli özelliği Türkçe desteklemez).

## Sorun giderme

- **Arayüz açılmadı** → `tail -5 /tmp/meetseen-web.log`'a bak; port çakışması
  yoksa sunucu zaten çalışıyordur (badge'de backend görünüyorsa sorun değil).
- **İlk çalıştırma yavaş:** Whisper modeli iniyor (~1.6 GB, bir kere).
- **"kullanılabilir LLM backend'i bulunamadı"** → Ollama kur veya API
  anahtarı tanımla (üstteki tablo).
- **Not yalnızca ekran (OCR) içeriğinden üretildi, "transkript boş" dedi:**
  reasoning model yanıt bütçesini düşünmeye yiyip boş döndürmüş olabilir —
  `~/.meetseen.json`'a `"claude_thinking": "disabled"` ekle (v0.1.1'de
  öntanımlı) ve yeniden çalıştır. Ayıklama hiçbir parçaya uygulanamadıysa
  sohbet-logu.md'de "ham metin korundu" uyarısı görürsün — iş içeriği
  kaybolmaz, yalnızca dolgu temizliği atlanır.
- **429 / hız limiti:** aynı gateway'i başka bir oturum (ör. Claude Code)
  kullanıyorsa bekleme + otomatik yeniden deneme devreye girer; tek başına
  çalıştırınca olmaz.
- **OCR boş geldi** → kareler slayt değil webcam görüntüsü olabilir;
  `--keep-frames` ile ne çıkarıldığına bak, `--frame-interval 1` dene.
- **Transkript boş** → kayıtta ses kanalı olmayabilir: `ffprobe toplantı.mp4`
  ile `Stream #0:1 Audio` satırını kontrol et.

## Test

`tools/` altındaki betiklerle iki dilli sentetik bir toplantı üretip
pipeline'ı risksiz deneyebilirsin (test/ klasörüne bak).

## Geliştirici

```bash
make app        # meetseen.app derle + ad-hoc imzala
make clean      # derleme artifact'larını temizle
```

- `app/` — Swift kabuk (WKWebView + sunucu yönetimi; SPM ile derlenir)
- `webui.py` — yerel sunucu (yalnız stdlib, 127.0.0.1:8765-8769)
- `meetseen.py` — pipeline (meetseen.py'ye dokunmadan UI onu alt süreç olarak çalıştırır)
- Katkılar welcome — lisans Apache-2.0.
