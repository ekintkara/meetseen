#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meetseen — Teams toplantı kaydından (MP4) iş notu üretir.

Pipeline:
  MP4 ─► ses ─► mlx-whisper (TR/EN, kelime zaman damgalı) ─► ham transkript
        │
        └─► ekran ─► ffmpeg kare örnekleme ─► pHash tekilleştirme
                    ─► Apple Vision OCR (tr-TR+en-US, cihaz içi, ücretsiz)

  LLM Aşama A (harita):  parça parça → segment sınıflandırma (İŞ/SOHBET/BELİRSİZ)
                          + dolgu temizliği + sohbetten taşan taahhütler
  LLM Aşama B (indirgeme): temiz metin + ekran OCR → yapılandırılmış not JSON
  ─► Markdown arşiv (NOT.md + transkriptler + denetim logu)

Hiçbir aşamada veri dışarı çıkmaz — LLM aşaması "ollama" backend'inde tamamen
lokal çalışır; "claude"/"gemini" backend'leri seçilirse yalnızca transkript+OCR
metni seçilen API'ye gider.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

VERSION = "0.1.0"

# --------------------------------------------------------------------------
# Varsayılanlar
# --------------------------------------------------------------------------
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
INITIAL_PROMPT = (
    "Proje toplantısı transkripti. Türkçe ve İngilizce karışık konuşma: "
    "sprint, backlog, mimari, architecture, deployment, API gateway, "
    "database migration, rollout, karar, aksiyon maddesi, open items."
)
DEFAULTS = {
    "backend": "auto",                 # auto|ollama|claude|gemini|mock
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen3:30b-a3b",   # 24 GB RAM için önerilen MoE model
    "ollama_num_ctx": 32768,
    "claude_model": "",                # boşsa ANTHROPIC_DEFAULT_SONNET_MODEL / claude-sonnet-5
    "claude_thinking": "disabled",     # reasoning modellerde düşünme bütçesi yemesin
    "gemini_model": "gemini-3.8-flash",
    "anthropic_base_url": "",            # boşsa ANTHROPIC_BASE_URL env kullanılır
    "anthropic_auth_token": "",          # Bearer token (öncelik cfg'de; sonra env)
    "anthropic_api_key": "",             # x-api-key alternatifi
    "whisper_model": WHISPER_MODEL,
    "stt_lang": "tr",                   # "auto" da olabilir; TR/EN karışıkta fark etmiyor
    "glossary": [],                     # proje sözlüğü → Stage A yazım düzeltmesine ipucu
    "project_dir": "",                  # her koşuda bağlam olarak eklenen proje klasörü
    "project_digest_chars": 36000,      # proje bağlam bütçesi (karakter)
    "window_sec": 300,                 # Aşama A parça boyu (saniye)
    "frame_interval": 2,               # kaçıncı saniyede 1 kare
    "max_frames": 80,                  # OCR'a verilecek en fazla tekil kare
    "out_lang": "tr",
    "initial_prompt": INITIAL_PROMPT,
}

# --------------------------------------------------------------------------
# Küçük yardımcılar
# --------------------------------------------------------------------------

def run(cmd, **kw):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        die(f"'{cmd[0]}' bulunamadı — kurulu mu? (README 'Kurulum' bölümüne bak)")
    except subprocess.CalledProcessError as e:
        die(f"'{cmd[0]}' hata kodu {e.returncode}: {(e.stderr or str(e))[-500:].strip()}")


def fmt_ts(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def slugify(text, fallback="toplanti"):
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return (text or fallback)[:60].strip("-")


def banner(step, msg):
    print(f"\n[{step}] {msg}", flush=True)


def die(msg, code=1):
    print(f"\nHATA: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config(path):
    cfg = dict(DEFAULTS)
    for candidate in [Path.home() / ".meetseen.json", Path("meetseen.json")]:
        if candidate.is_file():
            try:
                cfg.update(json.loads(candidate.read_text(encoding="utf-8")))
            except (ValueError, OSError) as e:
                print(f"UYARI: {candidate} okunamadı: {e}")
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            die(f"--config dosyası yok: {p}")
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError) as e:
            die(f"--config okunamadı ({p}): {e}")
    return cfg


# --------------------------------------------------------------------------
# Medya aşamaları (tamamı lokal)
# --------------------------------------------------------------------------

def ffprobe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)]).stdout.strip()
    try:
        return float(out)
    except ValueError:
        die(f"Süre okunamadı — dosyada ses/video akışı olabilir: {out[:120]!r}")


def extract_audio(mp4, wav):
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-vn",
         "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)])


def transcribe_audio(wav, cfg):
    import mlx_whisper  # gecikmeli import: yalnız STT yapılacaksa da hızlı başlasın
    banner("2/6", "Whisper transkripsiyon (ilk çalıştırmada model iner, ~1.6 GB)...")
    t0 = time.time()
    kwargs = {}
    if cfg.get("stt_lang") and cfg["stt_lang"] != "auto":
        kwargs["language"] = cfg["stt_lang"]
    result = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=cfg["whisper_model"],
        word_timestamps=True,
        condition_on_previous_text=False,    # halüsinasyon zincirini kır
        no_speech_threshold=0.6,
        hallucination_silence_threshold=2.0,
        initial_prompt=cfg["initial_prompt"],
        **kwargs,
    )
    utts = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if text:
            utts.append({"start": float(seg["start"]),
                         "end": float(seg["end"]),
                         "speaker": None,
                         "text": text})
    print(f"      {len(utts)} bölüm, {time.time() - t0:.0f} sn")
    return utts


VTT_CUE = re.compile(
    r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})")
VTT_SPEAKER = re.compile(r"<v[^>]*?\s+([^>]+?)>")


def parse_vtt(path):
    """Teams .vtt transkripti → konuşmacı etiketli bölüm listesi (dayanıklı parser)."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    utts, cur = [], None
    for block in re.split(r"\n\s*\n", raw):
        m = VTT_CUE.search(block)
        if not m:
            continue
        g = [int(x) if x is not None else 0 for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        body = block[m.end():]
        sp = VTT_SPEAKER.search(body)
        speaker = sp.group(1).strip() if sp else None
        text = re.sub(r"<[^>]+>", "", body).replace("&nbsp;", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        # Aynı konuşmacı, kısa boşluk → birleştir (cümle bazlı bölüm)
        if (cur and cur["speaker"] is not None and cur["speaker"] == speaker
                and start - cur["end"] < 0.8 and len(cur["text"]) < 2000):
            cur["text"] += " " + text
            cur["end"] = end
        else:
            cur = {"start": start, "end": end, "speaker": speaker, "text": text}
            utts.append(cur)
    return utts


def extract_frames(mp4, outdir, interval):
    banner("3/6", f"Ekran kareleri çıkarılıyor ({interval} sn'de 1)...")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
         "-vf", f"fps=1/{interval},scale=1920:-2", "-fps_mode", "vfr",
         "-q:v", "2", str(outdir / "f%05d.jpg")])
    return sorted(outdir.glob("f*.jpg"))


def dedup_frames(files, hamming=8):
    """pHash ile ardışık tekilleştirme: slayt değişmedikçe kare tutma."""
    import imagehash
    from PIL import Image
    kept, last = [], None
    for f in files:
        with Image.open(f) as im:
            h = imagehash.phash(im)
        if last is None or (h - last) > hamming:
            kept.append(f)
            last = h
    return kept


def ocr_bin_path(base):
    binpath = base / "bin" / "ocr_frames"
    src = base / "ocr_frames.swift"
    stale = (not binpath.exists()) or (src.stat().st_mtime > binpath.stat().st_mtime)
    if stale:
        banner("•", "Apple Vision OCR aracı derleniyor (bir kere)...")
        binpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = binpath.with_suffix(".tmp")
        run(["swiftc", "-O", str(src), "-o", str(tmp)])
        os.replace(tmp, binpath)  # atomik: yarım ikili bırakma
    return binpath


def ocr_frames(files, base):
    if not files:
        return {}
    binpath = ocr_bin_path(base)
    result = {}
    for i in range(0, len(files), 96):  # argv'yi parçala
        batch = files[i:i + 96]
        proc = subprocess.run([str(binpath)] + [str(f) for f in batch],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"UYARI: OCR hatası: {proc.stderr[:300]}")
        for line in proc.stdout.splitlines():
            try:
                row = json.loads(line)
                result[Path(row["file"]).name] = row["text"]
            except ValueError:
                continue
    return result


def frame_time(name, interval):
    n = int(re.search(r"(\d+)", name).group(1))
    return (n - 1) * interval


# --------------------------------------------------------------------------
# LLM backend'leri (ollama = %100 lokal; claude/gemini = API)
# --------------------------------------------------------------------------

def http_json(url, body, headers=None, timeout=1800):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {e.code} {e.reason} — {url}\n{detail}") from e


def ollama_reachable(url):
    from urllib.parse import urlparse
    p = urlparse(url)
    try:
        with socket.create_connection((p.hostname, p.port or 11434), timeout=0.6):
            return True
    except OSError:
        return False


def llm_call(cfg, system, user, max_tokens=8192, retries=3):
    """429/5xx ve geçici hatalarda geri çekilerek yeniden dene."""
    last = None
    for attempt in range(retries):
        try:
            return _llm_call_once(cfg, system, user, max_tokens)
        except RuntimeError as e:
            last = e
            msg = str(e)
            transient = " 429 " in msg or " 5" in msg.split("—")[0] or "timed out" in msg.lower()
            if not transient or attempt == retries - 1:
                raise
            wait = (attempt + 1) * 12
            print(f"      ...geçici hata ({msg.splitlines()[0][:80]}), {wait} sn sonra tekrar")
            time.sleep(wait)
    raise last


def _llm_call_once(cfg, system, user, max_tokens=8192):
    backend, model = cfg["backend"], cfg.get("model")
    if backend == "ollama":
        resp = http_json(f"{cfg['ollama_url']}/api/chat", {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": max_tokens,
                        "num_ctx": cfg["ollama_num_ctx"]},
        })
        msg = resp.get("message") or {}
        if "content" not in msg:
            die(f"ollama boş yanıt: {str(resp)[:300]}")
        return msg["content"]

    if backend == "claude":
        base = (cfg.get("anthropic_base_url")
                or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")).rstrip("/")
        headers = {"anthropic-version": "2023-06-01"}
        # Öncelik: cfg (kalıcı, zshrc'den bağımsız) → ortam değişkeni
        token = cfg.get("anthropic_auth_token") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        key = cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif key:
            headers["x-api-key"] = key
        else:
            die("claude backend: ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN yok")
        body = {
            "model": model, "max_tokens": max_tokens, "temperature": 0.1,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        # Reasoning modeller (ör. glm-5.3) düşünmeye tüm max_tokens bütçesini
        # harcayıp metin bloğu üretmeden kesilebilir → düşünme default kapalı.
        if cfg.get("claude_thinking") in ("enabled", "disabled"):
            body["thinking"] = {"type": cfg["claude_thinking"]}
        resp = http_json(f"{base}/v1/messages", body, headers=headers)
        if resp.get("stop_reason") == "max_tokens":
            raise RuntimeError(
                f"LLM yanıtı max_tokens={max_tokens} sınırında kesildi "
                "(düşünme + çıktı bütçesi yetmedi) — parça küçült veya "
                "cfg'de claude_thinking'i 'disabled' yap")
        return "".join(b.get("text", "") for b in resp.get("content", []))

    if backend == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            die("gemini backend: GEMINI_API_KEY yok")
        resp = http_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}",
            {"systemInstruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens,
                                  "responseMimeType": "application/json"}})
        cands = resp.get("candidates") or []
        if not cands or "content" not in cands[0] or not cands[0]["content"].get("parts"):
            block = (resp.get("promptFeedback") or {}).get("blockReason")
            finish = cands[0].get("finishReason") if cands else None
            die(f"gemini içerik üretmedi (blockReason={block}, finishReason={finish})")
        return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])

    if backend == "mock":
        return mock_llm(system, user)

    die(f"Bilinmeyen backend: {backend}")


def mock_llm(system, user):
    """Kurulum testi için: gerçek LLM olmadan aşamaların işleyişini doğrular."""
    if "AŞAMA-A" in system:
        lines = [l for l in user.splitlines() if l.strip().startswith("[")]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\b(eee|ııı|iii|mm+)\b[,.\s]*", "", cleaned, flags=re.IGNORECASE)
        return json.dumps({
            "segments": [{"start": fmt_ts(0), "class": "UNCERTAIN",
                          "reason": "mock"}],
            "cleaned_work_text": cleaned,
            "casual_commitments": ["(mock) sohbette geçen taahhüt örneği"],
            }, ensure_ascii=False)
    if "AŞAMA-B" in system:
        return json.dumps({
            "executive_summary": ["(mock) Yönetici özeti — gerçek backend bağlanınca dolacak."],
            "decisions": [{"decision": "(mock) örnek karar", "ts": "00:00:00",
                           "source_quote": "—"}],
            "action_items": [{"item": "(mock) örnek aksiyon", "owner": "belirtilmemiş",
                              "due": "belirtilmemiş", "ts": "00:00:00",
                              "source_quote": "—"}],
            "open_questions": ["(mock) açık soru"],
            "risks": ["(mock) risk"],
            "screen_content": [{"ts": "00:00:00", "what": "(mock) ekran içeriği"}],
        }, ensure_ascii=False)
    return "{}"


def extract_json(text):
    raw = text.strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)  # düşünce modelleri
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    def as_dict(obj):
        if isinstance(obj, dict):
            return obj
        raise ValueError(f"JSON nesne değil ({type(obj).__name__})")

    try:
        return as_dict(json.loads(raw))
    except ValueError:
        pass
    lo, hi = raw.find("{"), raw.rfind("}")
    if lo >= 0 and hi > lo:
        return as_dict(json.loads(raw[lo:hi + 1]))
    raise ValueError(f"JSON bulunamadı: {raw[:200]!r}")


def llm_json(cfg, system, user, max_tokens=8192, attempts=2):
    """LLM çağrısı + JSON ayrıştırma + düzeltme denemesi. Başarısızlıkta RuntimeError."""
    text = llm_call(cfg, system, user, max_tokens)
    for _ in range(attempts):
        try:
            return extract_json(text)
        except ValueError:
            if len(text.strip()) < 50:  # onarılacak metin yok — istek boşa gitti
                raise RuntimeError(
                    f"LLM boş/çok kısa yanıt döndürdü ({len(text.strip())} karakter) — "
                    "düşünme bütçesi veya backend hatası olabilir")
            snippet = text[:3000] + "\n...\n" + text[-3000:]  # baş+kuyruk
            text = llm_call(cfg,
                            "Yalnızca geçerli JSON döndür. Açıklama, markdown, kod bloğu YOK.",
                            snippet, max_tokens)
    try:
        Path("not-dump.txt").write_text(text[:50000], encoding="utf-8")
    except OSError:
        pass
    raise RuntimeError("LLM geçerli JSON üretmedi (ham çıktı: not-dump.txt) — "
                       "backend/model değiştirmeyi dene")


# --------------------------------------------------------------------------
# Proje bağlamı (opsiyonel --project): not üretimini projeye göre anlamlandır
# --------------------------------------------------------------------------

PROJECT_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__",
                     "dist", "build", ".next", "target", "vendor", ".idea",
                     ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                     "coverage", "out", ".turbo", ".angular", ".svelte-kit",
                     "meetseen-cikti", ".tox", ".eggs", "Pods", "DerivedData",
                     ".gradle", "bower_components", "bin", "obj"}
DOC_SUFFIX = {".md", ".markdown", ".rst", ".txt"}
CODE_SUFFIX = {".py", ".js", ".jsx", ".ts", ".tsx", ".swift", ".java", ".kt",
               ".go", ".rs", ".cs", ".sql", ".sh", ".php", ".rb"}
# (uzantı yok → manifest/Makefile gibi isimle yakalanır)
SYM_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),                       # python
    re.compile(r"^\s*class\s+(\w+)"),                                  # py/java/kt/cs/ts
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*[:=]"),
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:interface|type|enum)\s+(\w+)"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)"),                  # go
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),             # rust
]
SYM_STOP = {"test", "main", "init", "new", "string", "int", "error", "props",
            "state", "data", "item", "index", "value", "type", "self"}
TR_STOP = {"için", "ile", "olarak", "göre", "daha", "çok", "gibi", "kadar",
           "sonra", "önce", "burada", "bunun", "şunun", "proje", "toplantı",
           "klasör", "dosya", "yapısı", "dokümanlar", "belirtilmemiş",
           "ında", "leri", "ları", "ının", "ında", "yor", "onayı", "riski",
           "sohbet", "transkript", "ekran", "iş", "yani", "şey", "işte",
           "hani", "cuma", "salı", "pazartesi", "çarşamba", "perşembe"}
EN_STOP = {"this", "that", "with", "from", "will", "have", "been", "there",
           "their", "which", "would", "should", "your", "them", "then",
           "than", "when", "what", "into", "some", "more", "also", "project",
           "meeting", "notes", "true", "false", "null", "const", "return",
           "import", "export", "function", "class", "string", "number",
           "test", "tests", "pilot", "flag", "tooling", "should", "every"}


def _iter_project_files(root, max_files=4000):
    """Proje dosyalarını göreli yollarla üret (atlamalar + sıralı).
    Dönüş: (liste, kesildi_mi)."""
    out, truncated = [], False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PROJECT_SKIP_DIRS
                             and not d.startswith("."))
        for f in sorted(filenames):
            if f.startswith(".") or f.endswith((".lock", ".bin", ".png", ".jpg",
                                                ".jpeg", ".gif", ".svg", ".ico",
                                                ".pdf", ".zip", ".mp4", ".wav",
                                                ".woff", ".woff2", ".ttf")):
                continue
            out.append(Path(dirpath, f).relative_to(root))
            if len(out) >= max_files:
                return out, True
    return out, truncated


def _project_tree(files, max_lines=120):
    """Dizin ağacı (derinlik ≤ 3, satır sınırlı, kalan sayısı doğru)."""
    lines, seen, shown = [], set(), 0
    for f in files:
        parts = f.parts[:3] if len(f.parts) > 3 else f.parts
        shown += 1
        for depth in range(len(parts)):
            mark = parts[depth]
            key = (depth, parts[:depth], mark)
            if key in seen:
                continue
            seen.add(key)
            lines.append("  " * depth + (mark + "/" if depth < len(parts) - 1 else mark))
            if len(lines) >= max_lines:
                lines.append(f"… (+{len(files) - shown} dosya daha)")
                return "\n".join(lines)
    return "\n".join(lines)


def build_project_digest(root, budget=24000):
    """Proje klasöründen kompakt bağlam: ağaç + dokümanlar + kod sembolleri."""
    root = Path(root).resolve()
    files, truncated = _iter_project_files(root)
    if not files:
        return ""

    n_label = (f"ilk {len(files)} dosya — tarama kesildi" if truncated
               else f"{len(files)} dosya")
    parts = [f"PROJE: {root.name} ({n_label})\n\n"
             f"## Dizin yapısı\n{_project_tree(files)}"]

    # Doküman seçimi: (0) kök kimlik dosyaları — AGENTS/CLAUDE/README, sınırlı sayıda;
    # (1) diğer dokümanlar EN YENİDEN ESKİYE (toplantı, güncel dosyaları konuşur);
    # (2) iç içe README'ler en son — repo mekaniklerini anlatırlar, toplantıyı değil.
    def doc_rank(p):
        name = p.name.lower()
        identity = name.startswith("readme") or name in ("agents.md", "claude.md")
        nested = len(p.parts) > 1
        try:
            m = (root / p).stat().st_mtime
        except OSError:
            m = 0
        if identity and not nested:
            return (0, 0)
        if identity:
            return (2, 0)
        return (1, -m)

    docs = sorted((p for p in files
                   if p.suffix.lower() in DOC_SUFFIX or p.name.lower() in
                   ("agents.md", "claude.md", "package.json", "pyproject.toml")),
                  key=doc_rank)
    doc_txt = []
    for p in docs:
        try:
            fp = root / p
            st = fp.stat()
            if st.st_size > 1_000_000:   # devasa dosya (log vb.) yükleme
                continue
            name = p.name.lower()
            identity = (name.startswith("readme")
                        or name in ("agents.md", "claude.md")) and len(p.parts) == 1
            cap = 2500 if identity else 4000    # kimlik dosyaları kısa versiyon
            t = fp.read_text(encoding="utf-8", errors="replace")[:cap]
        except OSError:
            continue
        doc_txt.append(f"### {p}\n{t.strip()}")
        if sum(len(x) for x in doc_txt) > budget * 0.65:
            break
    if doc_txt:
        parts.append("## Dokümanlar\n" + "\n\n".join(doc_txt))

    # Kod sembolleri: her dosyanın fonksiyon/class/type adları (API yüzeyi)
    sym_lines, sym_files = [], 0
    for p in files:
        if p.suffix.lower() not in CODE_SUFFIX:
            continue
        fp = root / p
        try:
            if fp.stat().st_size > 200_000:
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        syms, seen = [], set()
        for line in text.splitlines():
            for rx in SYM_PATTERNS:
                m = rx.match(line)
                if m:
                    s = m.group(1)
                    if s.lower() not in SYM_STOP and s not in seen:
                        seen.add(s)
                        syms.append(s)
                    break
            if len(syms) >= 25:
                break
        if syms:
            sym_lines.append(f"{p} → {', '.join(syms)}")
            sym_files += 1
        if sym_files >= 150 or sum(len(x) for x in sym_lines) > budget * 0.25:
            break
    if sym_lines:
        parts.append("## Kod sembolleri (fonksiyon/class/type adları)\n"
                     + "\n".join(sym_lines))

    digest = "\n\n".join(parts)
    if len(digest) > budget:
        digest = digest[:budget] + "\n… (bağlam bütçesi nedeniyle kısaltıldı)"
    return digest


def _tr_casefold(t):
    """Python'un 'İ'.lower() çıktısındaki U+0307 sorununu aşarak küçült."""
    return t.replace("İ", "i").replace("I", "ı").lower()


def glossary_from_digest(digest, limit=60):
    """Bağlamdan sık geçen özgün terimleri çıkar → Aşama A sözlüğüne ekle."""
    counts = {}
    display = {}
    for tok in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü][\w\-]{3,27}", digest):
        t = tok.strip("-_")
        if not t:
            continue
        k = _tr_casefold(t)
        if not k or k in TR_STOP or k in EN_STOP:
            continue
        counts[k] = counts.get(k, 0) + 1
        if k not in display:
            display[k] = t                     # ilk görülen yazım
        elif t[:1].isupper() and not display[k][:1].isupper():
            display[k] = t                     # büyük harfli yazımı yeğle
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return [display[t] for t, c in ranked[:limit] if c >= 3]


# --------------------------------------------------------------------------
# LLM aşamaları
# --------------------------------------------------------------------------

STAGE_A_SYSTEM = """AŞAMA-A — toplantı transkript analiz motoru.
Girdi: zaman damgalı, Türkçe-İngilizce KARIŞIK, ham toplantı transkripti
(dolgu sözleri ve günlük sohbet içerir; bu bir yazılım projesi toplantısıdır).
Görevler:
1) Her [ZAMAN] bölümünü sınıflandır:
   WORK = proje/iş içerikli (mimari, sprint, hata, teslim, müşteri, deployment/dağıtım, plan, karar, aksiyon...)
   SMALLTALK = selamlaşma, hava, hafta sonu, yemek, şakalaşma, teknik olmayan sohbet
   UNCERTAIN = emin değilsen → HER ZAMAN UNCERTAIN (asla iş içeriği silinmesin).
2) SMALLTALK içinde geçen taahhüt/iş ipuçlarını ("yarın bakarım" gibi)
   casual_commitments listesine yaz.
3) WORK ve UNCERTAIN bölüm içeriklerinden cleaned_work_text üret:
   - Dolgu sözlerini ayıkla: eee, ııı, mm, şey (boşta kullanımı), yani (boşta), hani,
     işte, tekrarlı "ok", yarım cümleler, söylenmiş ama yarıda kuran sözler.
   - "ok, bunu yapalım" gibi bir KARAR olan kullanımları ASLA ayıklama.
   - Transkriptte Türkçe okunuşla yazılmış İngilizce teknik terimleri standart
     yazıma DÜZELT (örn: "apı gata imigratyon" → "API gateway migration",
     "blojker" → "blocker", "Bajklog" → "backlog", "klastır" → "cluster").
     Bu bir yazım düzeltmesidir, çeviri DEĞİLDİR — anlamı değiştirme.
   - [HH:MM:SS] zaman damgalarını cleaned_work_text içinde bulundukları yerde
     KORU (sonraki aşama karar/aksiyon zamanlarını bunlardan alacak).
   - Anlamı, dili (Türkçe İngilizce karışık korunur) ve kronolojik sırayı KORU.
   - ÖZETLEME, ekleme yapma, sıralama yapma.
SADECE geçerli JSON döndür:
{"segments":[{"start":"HH:MM:SS","class":"WORK|SMALLTALK|UNCERTAIN","reason":"en fazla 8 kelime"}],
 "cleaned_work_text":"...",
 "casual_commitments":["..."]}"""

STAGE_B_SYSTEM = """AŞAMA-B — kıdemli teknik program yöneticisi asistanı.
Girdiler: (1) zaman damgalı, temizlenmiş iş transkripti (Türkçe/İngilizce karışık),
(2) günlük sohbetten yakalanan taahhütler, (3) toplantıda EKRANDA gösterilen
içeriğin zaman damgalı OCR dökümü.
Çıktı YALNIZCA geçerli JSON'dur (kod bloğu yok, açıklama yok) ve şu anahtarları taşır:
{"executive_summary": [], "decisions": [], "action_items": [], "open_questions": [], "risks": [], "screen_content": []}
Alan kuralları:
- executive_summary: 5-10 madde, her biri tek cümle.
- decisions: her biri {"decision","ts","source_quote"} — ts = "HH:MM:SS".
- action_items: her biri {"item","owner","due","ts","source_quote"}.
- open_questions, risks: düz metin maddeleri.
- screen_content: her biri {"ts","what"} — OCR'daki her farklı ekran için bir satır,
  what = tek cümde ekranda ne olduğu.
Genel kurallar:
- TÜM serbest metin alanları Türkçe yazılır; teknik terimler (deployment, API,
  cluster, rollout...) İngilizce yazımıyla kalır.
- transkriptte/OCR'da OLMAYAN hiçbir bilgiyi uydurma.
- PROJE BAĞLAMI verilmişse: toplantıda geçen terim, modül, dosya ve iş öğesi
  adlarını bu bağlamla eşleştirip doğru yazımla kullan; ancak notun İÇERİĞİ
  yalnızca toplantıda söylenenlerden/ekranda gösterilenlerden üretilir.
- source_quote transkriptten gerçek bir kısa alıntıdır; birebir uygun alıntı
  yoksa "" yaz (uydurma).
- owner/due belli değilse "belirtilmemiş"; ts olarak en yakın [HH:MM:SS] damgasını ver."""

STAGE_B_MERGE = """AŞAMA-B-birleştirme — aynı toplantının ardışık parçalarından
üretilmiş not JSON'ları verilecek. Bunları TEK tutarlı not JSON'una birleştir:
- executive_summary: en fazla 10 madde; tekrarları erit, kronolojik akışı koru.
- decisions / action_items: aynı olanları birleştir; ts olarak en erken kaydı koru.
- open_questions / risks: dedupla.
- screen_content: ts sıralı birleştir.
Şema ve kurallar AŞAMA-B'ninkiyle aynı:
{"executive_summary": [], "decisions": [], "action_items": [], "open_questions": [], "risks": [], "screen_content": []}
SADECE geçerli JSON döndür."""


def split_long(utts, limit=2000):
    """Aşırı uzun bölümü cümle sınırından böl (VTT birleşme tuzağına karşı)."""
    out = []
    for u in utts:
        t = u["text"]
        while len(t) > limit:
            cut = max(t.rfind(". ", 0, limit), t.rfind("! ", 0, limit),
                      t.rfind("? ", 0, limit)) + 1 or limit
            out.append({**u, "text": t[:cut].strip()})
            t = t[cut:].strip()
        if t:
            out.append({**u, "text": t})
    return out


def chunk_utterances(utts, window):
    chunks, cur, t0 = [], [], None
    for u in split_long(utts):
        if t0 is None or u["start"] - t0 >= window:
            if cur:
                chunks.append(cur)
            cur, t0 = [], u["start"]
        cur.append(u)
    if cur:
        chunks.append(cur)
    return chunks


def uts_to_text(chunk):
    lines = []
    for u in chunk:
        sp = f"{u['speaker']}: " if u.get("speaker") else ""
        lines.append(f"[{fmt_ts(u['start'])}] {sp}{u['text']}")
    return "\n".join(lines)


def _stage_a_valid(data):
    """Şema kontrolü: error nesnesi veya içi boş yanıt geçersiz.

    Geçerli = dict + cleaned_work_text dolu YA DA segmentler sınıflandırılmış
    (bir parça meşru olarak tamamı SOHBET olabilir → cleaned boş, segments dolu).
    """
    if not isinstance(data, dict) or "error" in data:
        return False
    cleaned = data.get("cleaned_work_text")
    if isinstance(cleaned, str) and cleaned.strip():
        return True
    segs = data.get("segments")
    return isinstance(segs, list) and len(segs) > 0


def stage_a(cfg, utts, project_digest=""):
    banner("4/6", "Aşama A — iş/sohbet ayrımı + dolgu temizliği (parça parça)...")
    chunks = chunk_utterances(utts, cfg["window_sec"])
    terms = list(cfg.get("glossary") or [])
    auto = glossary_from_digest(project_digest) if project_digest else []
    for t in auto:
        if t not in terms:
            terms.append(t)
    if auto:
        print(f"      proje sözlüğü: +{len(auto)} terim (bağlamdan)")
    glossary = ""
    if terms:
        glossary = ("\n\nPROJE SÖZLÜĞÜ (yazım düzeltmede kullan): "
                    + ", ".join(terms))
    maps = []
    for i, chunk in enumerate(chunks, 1):
        print(f"      parça {i}/{len(chunks)} ({fmt_ts(chunk[0]['start'])}-{fmt_ts(chunk[-1]['end'])})")
        raw = uts_to_text(chunk)
        data = None
        try:
            data = llm_json(cfg, STAGE_A_SYSTEM, raw + glossary)
            if not _stage_a_valid(data):
                print(f"      UYARI: parça {i} şema-dışı yanıt (anahtarlar: "
                      f"{list(data)[:4] if isinstance(data, dict) else type(data).__name__}) — tekrar deneniyor")
                data = llm_json(cfg, STAGE_A_SYSTEM,
                                raw + glossary
                                + "\n\nNOT: Önceki yanıtın şemaya uymadı. SADECE istenen "
                                  "JSON'u üret; cleaned_work_text ve segments alanları zorunlu.")
        except Exception as e:  # bir parça bozulsa bile koşu sürsün
            print(f"      UYARI: parça {i} LLM hatası ({str(e)[:80]}) — ham metinle devam")
        if not _stage_a_valid(data):
            if data is not None:
                print(f"      UYARI: parça {i} yine şema-dışı — ham metin korundu (ayıklama yapılamadı)")
            data = {"segments": [], "cleaned_work_text": raw,
                    "casual_commitments": []}
            data["_fallback"] = True
        data["_range"] = f"{fmt_ts(chunk[0]['start'])}-{fmt_ts(chunk[-1]['end'])}"
        maps.append(data)
    total = sum(len((m.get("cleaned_work_text") or "").strip()) for m in maps)
    if utts and not total:
        print("      UYARI: hiç iş metni kalmadı — tüm parçalar SOHBET çıktı (denetim loguna bak)")
    return maps


def stage_b(cfg, maps, screen_digest, project_digest=""):
    banner("5/6", "Aşama B — yapılandırılmış notlar...")
    pieces = []
    for m in maps:
        cleaned = (m.get("cleaned_work_text") or "").strip()
        casual = "\n".join(f"- {c}" for c in m.get("casual_commitments", []))
        piece = f"--- {m.get('_range', 'parça')} ---\n{cleaned}"
        if casual:
            piece += f"\nSOHBETTEN TAAHÜTLER:\n{casual}"
        pieces.append(piece)

    def make_user(parts, bare=False):
        user = "TEMİZ İŞ TRANSKRİPTİ:\n" + "\n\n".join(parts)
        if not bare:
            if screen_digest:
                user += f"\n\nEKRAN İÇERİĞİ (OCR, zaman damgalı):\n{screen_digest}"
            if project_digest:
                user += ("\n\nPROJE BAĞLAMI (proje klasöründen otomatik çıkarıldı; "
                         "terim/isim/modül çözümlemesinde KULLAN, transkripte "
                         "içerik uydurmak için değil):\n" + project_digest)
        return user

    # Bağlam sınırı: ollama'da num_ctx, API'de ~90k karakter güvenli tavan.
    limit = (cfg["ollama_num_ctx"] - 6000) * 3 if cfg["backend"] == "ollama" else 90000
    if len(make_user(pieces)) <= limit or len(pieces) == 1:
        try:
            return llm_json(cfg, STAGE_B_SYSTEM, make_user(pieces))
        except RuntimeError as e:
            die(f"Aşama B başarısız: {e}")

    # Büyük toplantı → parça parça not + birleştirme (map-reduce)
    print(f"      toplantı büyük → parça başına not + birleştirme ({len(pieces)} parça)")
    part_notes = []
    for i, p in enumerate(pieces, 1):
        print(f"      not parçası {i}/{len(pieces)}")
        try:
            part_notes.append(llm_json(cfg, STAGE_B_SYSTEM, make_user([p])))
        except RuntimeError:
            # parça + bağlam limiti aştıysa: bu parçayı bağlamsız dene
            print(f"      UYARI: parça {i} bağlamla sığmadı — bağlamsız deneniyor")
            part_notes.append(llm_json(cfg, STAGE_B_SYSTEM, make_user([p], bare=True)))
    try:
        return llm_json(cfg, STAGE_B_MERGE,
                        "PARÇA NOTLARI (kronolojik):\n"
                        + json.dumps(part_notes, ensure_ascii=False))
    except RuntimeError as e:
        die(f"Aşama B birleştirme başarısız: {e}")


# --------------------------------------------------------------------------
# Çıktı
# --------------------------------------------------------------------------

def normalize_note(note):
    """LLM çıktısındaki tip sapmalarını düzelt (string→dict, eksik alan)."""
    def as_list(key):
        v = note.get(key) or []
        if isinstance(v, (str, dict)):  # tekil değer geldi → tek elemanlı liste
            v = [v]
        out = []
        for x in v:
            if isinstance(x, str):
                x = ({"decision": x} if key == "decisions"
                     else {"item": x} if key == "action_items"
                     else {"what": x} if key == "screen_content"
                     else {"text": x})
            if isinstance(x, dict):
                out.append(x)
        return out

    for k in ("executive_summary", "decisions", "action_items",
              "open_questions", "risks", "screen_content"):
        note[k] = as_list(k)
    for k in ("executive_summary", "open_questions", "risks"):
        note[k] = [x if isinstance(x, str) else x.get("text", str(x)) for x in note[k]]
    return note


def render_notes(note, meta, outdir):
    md = []
    md.append("---")
    for k, v in meta.items():
        md.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    md.append("---\n")
    md.append(f"# {meta['title']}\n")

    md.append("## Yönetici Özeti\n")
    for s in note.get("executive_summary", []):
        md.append(f"- {s}")

    md.append("\n## Kararlar\n")
    for d in note.get("decisions", []):
        md.append(f"- **{d.get('decision','')}** `({d.get('ts','')})`")
        if d.get("source_quote"):
            md.append(f"  > _\"{d['source_quote']}\"_")

    md.append("\n## Aksiyon Maddeleri\n")
    md.append("| # | Aksiyon | Sahip | Termin | Zaman |")
    md.append("|---|---------|-------|--------|-------|")
    for i, a in enumerate(note.get("action_items", []), 1):
        md.append(f"| {i} | {a.get('item','')} | {a.get('owner','belirtilmemiş')} "
                  f"| {a.get('due','belirtilmemiş')} | {a.get('ts','')} |")

    md.append("\n## Açık Sorular\n")
    for q in note.get("open_questions", []):
        md.append(f"- [ ] {q}")

    md.append("\n## Riskler / Engeller\n")
    for r in note.get("risks", []):
        md.append(f"- ⚠️ {r}")

    if note.get("screen_content"):
        md.append("\n## Ekranda Gösterilenler\n")
        md.append("| Zaman | İçerik |")
        md.append("|-------|--------|")
        for s in note["screen_content"]:
            md.append(f"| {s.get('ts','')} | {s.get('what','')} |")

    md.append("\n---\n")
    md.append("### Kaynaklar\n")
    md.append("- [Temiz transkript](transkript-temiz.txt) · [Ham transkript](transkript-ham.txt) "
              "· [Atılan sohbet (denetim)](sohbet-logu.md)")
    return "\n".join(md)


def render_smalltalk_log(maps):
    md = ["# Atılan Sohbet — Denetim Logu\n",
          "Aşağıdaki segmentler SMALLTALK olarak sınıflandırıldı ve notlardan çıkarıldı.",
          "Yanlışlıkla atılmış iş içeriği görürsen transkript-ham.txt'ten kontrol et.\n"]
    for i, m in enumerate(maps, 1):
        if m.get("_fallback"):
            md.append(f"## {i}. parça ({m.get('_range', '')})\n"
                      "- ⚠️ LLM yanıtı şema-dışı geldi — **ham metin korundu**, "
                      "ayıklama bu parçaya uygulanamadı.\n")
            continue
        dropped = [s for s in m.get("segments", []) if s.get("class") == "SMALLTALK"]
        casual = m.get("casual_commitments", [])
        if not dropped and not casual:
            continue
        md.append(f"## {i}. parça\n")
        for s in dropped:
            md.append(f"- `[{s.get('start','')}]` **atıldı** — {s.get('reason','')}")
        for c in casual:
            md.append(f"- 💬 sohbetten taahhüt: {c}")
        md.append("")
    return "\n".join(md)


# --------------------------------------------------------------------------
# Ana akış
# --------------------------------------------------------------------------

def detect_backend(cfg):
    user_model = cfg.get("model")  # kullanıcının --model / config değeri korunur
    if cfg["backend"] == "auto":
        if os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
            cfg["backend"] = "claude"
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            cfg["backend"] = "gemini"
        elif ollama_reachable(cfg["ollama_url"]):
            cfg["backend"] = "ollama"
        else:
            cfg["backend"] = "mock"
            print("UYARI: kullanılabilir LLM backend'i bulunamadı (ollama yok, API anahtarı yok).\n"
                  "      Mock backend ile devam ediliyor — notlar gerçek olmayacak.\n"
                  "      Gerçek çıktı için: ollama kur (README) veya API anahtarı tanımla.")
    if not cfg.get("model"):
        default = {"claude": (cfg.get("claude_model")
                              or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
                              or "claude-sonnet-5"),
                   "gemini": cfg["gemini_model"],
                   "ollama": cfg["ollama_model"],
                   "mock": "mock"}.get(cfg["backend"], "mock")
        # bazı gateway'ler model adına bağlam eki koyar ("glm-5.3[1m]") → soy
        cfg["model"] = re.sub(r"\[[0-9a-zA-Z]+\]$", "", default)
    if user_model:
        cfg["model"] = user_model


def preflight_backend(cfg):
    """Medya işlerinden ÖNCE bariz bağlantı/anahtar hatalarını yakala."""
    b = cfg["backend"]
    if b == "claude" and not (cfg.get("anthropic_auth_token")
                              or cfg.get("anthropic_api_key")
                              or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                              or os.environ.get("ANTHROPIC_API_KEY")):
        die("claude backend: anahtar yok — ~/.meetseen.json'a \"anthropic_auth_token\" "
            "yaz veya ANTHROPIC_AUTH_TOKEN tanımla")
    if b == "gemini" and not (os.environ.get("GEMINI_API_KEY")
                              or os.environ.get("GOOGLE_API_KEY")):
        die("gemini backend: GEMINI_API_KEY yok")
    if b == "ollama":
        url = cfg["ollama_url"].rstrip("/")
        if not ollama_reachable(url):
            die("ollama çalışmıyor — `ollama serve` başlat (veya: brew services start ollama)")
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as r:
                names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        except (OSError, ValueError) as e:
            die(f"ollama sorgulanamadı: {e}")
        want = cfg.get("model", "")
        if want and not any(n == want or n.split(":")[0] == want.split(":")[0]
                            for n in names):
            die(f"ollama'da '{want}' modeli kurulu değil (kurulu: {', '.join(names) or 'yok'}) "
                f"→ `ollama pull {want}` çalıştır")


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        prog="meetseen",
        description="Teams toplantı kaydından (MP4) iş notu üretir — "
                    "ses transkripti + ekran OCR + LLM notları.")
    ap.add_argument("video", help="toplantı kaydı .mp4")
    ap.add_argument("--vtt", help="Teams .vtt transkripti (STT yerine; konuşmacı etiketli)")
    ap.add_argument("--out", default="meetseen-cikti", help="çıktı kök klasörü")
    ap.add_argument("--title", help="toplantı başlığı")
    ap.add_argument("--date", help="toplantı tarihi (YYYY-MM-DD HH:MM)")
    ap.add_argument("--backend", choices=["auto", "ollama", "claude", "gemini", "mock"])
    ap.add_argument("--model", help="backend model adı")
    ap.add_argument("--window", type=int, help="Aşama A parça boyu (sn)")
    ap.add_argument("--frame-interval", type=int, help="kare örnekleme aralığı (sn)")
    ap.add_argument("--max-frames", type=int)
    ap.add_argument("--keep-frames", action="store_true", help="kareleri not klasörüne kaydet")
    ap.add_argument("--skip-llm", action="store_true", help="sadece transkript + OCR üret")
    ap.add_argument("--project", help="proje klasörü — kod/dokümanlar notlara bağlam olur")
    ap.add_argument("--config", help="JSON yapılandırma dosyası")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe", "swiftc"):
        if not shutil.which(tool):
            die(f"{tool} kurulu değil — README 'Kurulum' bölümüne bak "
                "(brew install ffmpeg; xcode-select --install)")
    cfg = load_config(args.config)
    if args.backend:
        cfg["backend"] = args.backend
    if args.window is not None:
        cfg["window_sec"] = max(30, args.window)
    if args.frame_interval is not None:
        cfg["frame_interval"] = max(1, args.frame_interval)
    if args.max_frames is not None:
        cfg["max_frames"] = max(1, args.max_frames)
    if args.model:
        cfg["model"] = args.model
    cfg["ollama_url"] = cfg["ollama_url"].rstrip("/")

    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        die(f"Dosya yok: {video}")
    if args.vtt and not Path(args.vtt).expanduser().is_file():
        die(f"VTT dosyası yok: {args.vtt}")

    project = None
    if args.project or cfg.get("project_dir"):
        project = Path(args.project or cfg["project_dir"]).expanduser().resolve()
        if not project.is_dir():
            die(f"Proje klasörü yok: {project}")

    if args.skip_llm:
        cfg["backend"], cfg["model"] = "atlandı", "-"
    else:
        detect_backend(cfg)
        preflight_backend(cfg)  # kimlik/bağlantı hatası medya işlerinden ÖNCE çıksın

    duration = ffprobe_duration(video)
    title = args.title or video.stem
    dt = datetime.now()
    if args.date:
        try:
            dt = datetime.strptime(args.date, "%Y-%m-%d %H:%M")
        except ValueError:
            die("--date biçimi: YYYY-MM-DD HH:MM")
    stem = f"{dt:%Y-%m-%d_%H%M}-{slugify(title)}"
    outdir = Path(args.out).resolve() / stem
    n = 2
    while outdir.exists() and any(outdir.iterdir()):  # aynı dakika + başlık çakışması
        outdir = Path(args.out).resolve() / f"{stem}-{n}"
        n += 1
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"meetseen v{VERSION} | backend={cfg['backend']} model={cfg.get('model')}")
    print(f"toplantı: {video.name} ({duration / 60:.0f} dk) → {outdir}")

    # --- 1-2: ses → transkript
    if args.vtt:
        banner("2/6", f"Teams VTT okunuyor: {args.vtt}")
        utts = parse_vtt(Path(args.vtt).expanduser())
        if not utts:
            die("VTT'de bölüm bulunamadı")
    else:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio.wav"
            banner("1/6", "Ses ayrıştırılıyor (ffmpeg)...")
            extract_audio(video, wav)
            utts = transcribe_audio(wav, cfg)
        if not utts:
            die("Transkript boş — kayıtta konuşma algılanmadı")
    (outdir / "transkript-ham.txt").write_text(uts_to_text(utts) + "\n", encoding="utf-8")

    # --- 3: ekran kareleri + OCR
    with tempfile.TemporaryDirectory() as td:
        files = extract_frames(video, Path(td), cfg["frame_interval"])
        print(f"      {len(files)} kare → tekilleştirme...")
        keep = dedup_frames(files)
        if len(keep) > cfg["max_frames"]:
            step = len(keep) / cfg["max_frames"]
            idx = sorted({int(i * step) for i in range(cfg["max_frames"])})
            keep = [keep[i] for i in idx]
        ocr = ocr_frames(keep, here)
        digest = []
        for f in keep:
            t = ocr.get(f.name, "")
            if t.strip():
                digest.append(f"[{fmt_ts(frame_time(f.name, cfg['frame_interval']))}] {t[:600]}")
        if args.keep_frames:
            fdir = outdir / "kareler"
            fdir.mkdir(exist_ok=True)
            for f in keep:
                shutil.copy2(f, fdir / f.name)
    banner("•", f"ekran: {len(files)} kare → {len(keep)} tekil, {len(digest)} tanesi metin içeriyor")

    if args.skip_llm:
        (outdir / "ekran-ocr.txt").write_text("\n\n".join(digest), encoding="utf-8")
        print(f"\n✔ --skip-llm: transkript + OCR hazır → {outdir}")
        return

    # --- 4-5: LLM aşamaları
    project_digest = ""
    if project:
        banner("•", f"proje bağlamı: {project.name} taranıyor...")
        project_digest = build_project_digest(project,
                                              int(cfg["project_digest_chars"]))
        if project_digest:
            print(f"      {len(project_digest)} karakter bağlam "
                  f"(ağaç + dokümanlar + kod sembolleri)")
        else:
            print("      UYARI: klasörde bağlam çıkarılamadı (boş?) — bağlamsız devam")
    maps = stage_a(cfg, utts, project_digest)
    note = normalize_note(stage_b(cfg, maps, "\n".join(digest), project_digest))

    # --- 6: yaz
    banner("6/6", "Notlar yazılıyor...")
    meta = {
        "title": title,
        "date": f"{dt:%Y-%m-%d %H:%M}",
        "duration": fmt_ts(duration),
        "source": video.name,
        "backend": cfg["backend"],
        "model": cfg.get("model", ""),
        "segments_total": len(utts),
        "project": str(project) if project else "",
    }
    (outdir / "NOT.md").write_text(render_notes(note, meta, outdir) + "\n", encoding="utf-8")
    (outdir / "transkript-temiz.txt").write_text(
        "\n\n".join((m.get("cleaned_work_text") or "").strip() for m in maps) + "\n",
        encoding="utf-8")
    (outdir / "sohbet-logu.md").write_text(render_smalltalk_log(maps) + "\n", encoding="utf-8")
    (outdir / "not.json").write_text(
        json.dumps({"meta": meta, "note": note, "maps": maps},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✔ Bitti → {outdir}")
    print(f"  Önce şuna bak: {outdir / 'NOT.md'}")


if __name__ == "__main__":
    main()
