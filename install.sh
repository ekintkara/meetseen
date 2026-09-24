#!/bin/zsh
# meetseen kurulumu — macOS (Apple Silicon)
# Kullanım: ./install.sh          → bağımlılıklar + .venv
#           ./install.sh --app    → ayrıca meetseen.app üretir ve /Applications'a kopyalar
set -e
cd "$(dirname "$0")"

say()  { printf "\033[1;34m▸\033[0m %s\n" "$1"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
die()  { printf "\033[1;31mHATA:\033[0m %s\n" "$1"; exit 1; }

# --- platform kontrolü ---
[[ "$(uname -s)" == "Darwin" ]] || die "meetseen yalnız macOS'ta çalışır (ekran OCR'ı Apple Vision'a bağlı)."
[[ "$(uname -m)" == "arm64"  ]] || die "Yalnız Apple Silicon (M1/M2/M3/M4/M5) Mac desteklenir — mlx ve Vision OCR gerektirir."

# --- Homebrew + ffmpeg ---
if ! command -v ffmpeg >/dev/null; then
  command -v brew >/dev/null || die "Homebrew yok — https://brew.sh adresinden kur, sonra tekrar çalıştır."
  say "ffmpeg kuruluyor (brew)..."
  brew install ffmpeg
fi
ok "ffmpeg hazır"

# --- Xcode Command Line Tools (Swift derleyicisi — Vision OCR için) ---
if ! xcrun --find swiftc >/dev/null 2>&1; then
  say "Xcode Command Line Tools kuruluyor (Vision OCR derleyicisi)..."
  xcode-select --install || true
  die "Kurulum penceresi açıldı — bitince bu betiği tekrar çalıştır."
fi
ok "Swift derleyici hazır"

# --- Python ortamı ---
if [[ ! -x .venv/bin/python ]]; then
  say "Python ortamı oluşturuluyor (.venv)..."
  python3 -m venv .venv
fi
say "Bağımlılıklar kuruluyor..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
ok ".venv hazır"

# --- (isteğe bağlı) uygulama ---
if [[ "$1" == "--app" ]]; then
  say "meetseen.app derleniyor..."
  make app
  if [[ -w /Applications ]]; then
    rm -rf /Applications/meetseen.app
    cp -R meetseen.app /Applications/
    ok "/Applications/meetseen.app kopyalandı"
  else
    say "/Applications yazılabilir değil — uygulama klasörde kaldı: $(pwd)/meetseen.app"
  fi
fi

cat <<'BİTTİ'

Kurulum tamam. Sonraki adımlar:
  1) Ayarlar:  ~/.meetseen.json  (backend + anahtar — örn:
       {"backend":"claude","claude_model":"...","anthropic_auth_token":"...",
        "anthropic_base_url":"https://api.z.ai/api/anthropic"})
     veya arayüzdeki ⚙︎ Ayarlar ekranından model seçimi yapılabilir.
  2) Başlatma: meetseen.app  (--app kullandıysan /Applications/meetseen.app)
  3) İlk çalıştırmada Whisper modeli iner (~3 GB, bir kere).

BİTTİ
