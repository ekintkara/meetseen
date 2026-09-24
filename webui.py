#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meetseen web arayüzü — yerel sunucu (yalnız 127.0.0.1, stdlib).

Pipeline'a dokunmaz: meetseen.py'yi alt süreç olarak çalıştırır ve
stdout'unu akıtarak ilerlemeyi sunar. Çift tık girişi: meetseen.command.
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BASE = Path(__file__).resolve().parent
UI = BASE / "ui"
OUT_ROOT = BASE / "meetseen-cikti"
UP_DIR = BASE / "yuklenenler"
PY = BASE / ".venv" / "bin" / "python"
URL_FILE = Path("/tmp/meetseen-web.url")
ALLOWED_SUFFIX = {".mp4", ".mov", ".m4v", ".mkv"}
MAX_UPLOAD = 2 * 1024 ** 3

STEP_RE = re.compile(r"\[(\d)/6\]")
DONE_RE = re.compile(r"✔ Bitti → (.+)")


# --------------------------------------------------------------------------
# Koşu yönetimi (tek etkin koşu)
# --------------------------------------------------------------------------

class Run:
    def __init__(self, path, title, keep_frames, project=""):
        self.id = uuid.uuid4().hex[:12]
        self.path, self.title, self.keep = path, title, keep_frames
        self.project = project
        self.status = "running"          # running|done|error|cancelled
        self.step = 0                    # 0..6 (0 = başlıyor)
        self.lines = []
        self.output_dir = None
        self.error = None
        self.started = time.time()
        self.proc = None


_state = {"active": None, "runs": {}, "lock": threading.Lock()}


def _reader(run):
    """Alt sürecin stdout'unu satır satır akıt; adım/bitiş işaretle."""
    for line in run.proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        with _state["lock"]:
            run.lines.append(line)
            del run.lines[:-300]
            m = STEP_RE.search(line)
            if m:
                run.step = max(run.step, int(m.group(1)))
            d = DONE_RE.search(line)
            if d:
                run.output_dir = d.group(1)
    rc = run.proc.wait()
    with _state["lock"]:
        if run.status == "running":
            run.status = "done" if rc == 0 else "error"
            if rc != 0:
                run.error = f"meetseen çıkış kodu {rc}"
        _state["active"] = None


def start_run(path, title, keep_frames, project=""):
    with _state["lock"]:
        if _state["active"] is not None:
            return None, "Zaten bir koşu sürüyor — önce bitmesini veya iptalini bekle."
        run = Run(path, title, keep_frames, project)
        cmd = [str(PY), "meetseen.py", str(path)]
        if title:
            cmd += ["--title", title]
        if keep_frames:
            cmd += ["--keep-frames"]
        if project:
            cmd += ["--project", project]
        env = dict(os.environ)
        env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
        env["PYTHONUTF8"] = "1"          # nohup ortamında Türkçe çıktı için
        run.proc = subprocess.Popen(
            cmd, cwd=str(BASE), env=env, text=True, errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)      # kendi süreç grubu → ffmpeg dahil iptal edilebilir
        _state["active"] = run
        _state["runs"][run.id] = run
    threading.Thread(target=_reader, args=(run,), daemon=True).start()
    return run, None


def cancel_run(run_id):
    with _state["lock"]:
        run = _state["runs"].get(run_id)
        if not run or run.status != "running":
            return False
        run.status = "cancelled"
    try:
        os.killpg(run.proc.pid, signal.SIGTERM)
        time.sleep(2)
        if run.proc.poll() is None:
            os.killpg(run.proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


def run_state(run):
    return {"id": run.id, "status": run.status, "step": run.step,
            "elapsed": int(time.time() - run.started),
            "lines": run.lines[-60:], "output_dir": run.output_dir,
            "error": run.error, "title": run.title}


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def file_info(path):
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    st = p.stat()
    return {"path": str(p.resolve()), "name": p.name,
            "size_mb": round(st.st_size / 1048576, 1),
            "mtime": time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime)),
            "suffix": p.suffix.lower()}


def out_child(name):
    """Yalnız meetseen-cikti altındaki klasör adına izin ver (gezinme yok)."""
    if not name or "/" in name or name.startswith("."):
        return None
    d = (OUT_ROOT / name).resolve()
    try:
        d.relative_to(OUT_ROOT.resolve())
    except ValueError:
        return None
    return d if d.is_dir() else None


def history():
    if not OUT_ROOT.is_dir():
        return []
    items = []
    for d in OUT_ROOT.iterdir():
        if d.is_dir():
            items.append({"name": d.name, "mtime": time.strftime(
                "%d.%m.%Y %H:%M", time.localtime(d.stat().st_mtime)),
                "has_notes": (d / "NOT.md").is_file()})
    return sorted(items, key=lambda x: x["name"], reverse=True)


_CONFIG_CACHE = {"t": 0, "v": None}

# Ayarlar ekranında okunup yazılmasına izin verilen anahtarlar (token ASLA).
SETTINGS_KEYS = ("backend", "claude_model", "gemini_model", "ollama_url",
                 "ollama_model", "whisper_model", "claude_thinking",
                 "project_dir", "project_digest_chars", "glossary")


def user_config_path():
    return Path.home() / ".meetseen.json"


def read_settings():
    try:
        cfg = json.loads(user_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return {k: cfg[k] for k in SETTINGS_KEYS if k in cfg}


def write_settings(updates):
    p = user_config_path()
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    for k, v in updates.items():
        if k in SETTINGS_KEYS:
            cfg[k] = v
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    _CONFIG_CACHE["t"] = 0        # backend rozetini tazele


def backend_info():
    """meetseen'in kendi yapılandırmasından backend/model (60 sn önbellek)."""
    if time.time() - _CONFIG_CACHE["t"] < 60 and _CONFIG_CACHE["v"]:
        return _CONFIG_CACHE["v"]
    try:
        r = subprocess.run(
            [str(PY), "-c",
             "import json,meetseen; c=meetseen.load_config(None); "
             "meetseen.detect_backend(c); "
             "print(json.dumps({'backend':c['backend'],'model':c.get('model','')}))"],
            cwd=str(BASE), capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONUTF8": "1"})
        v = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        v = {"backend": "?", "model": ""}
    _CONFIG_CACHE.update(t=time.time(), v=v)
    return v


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # Terminali kirletme
        pass

    # --- yanıt yardımcıları ---
    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def json_ok(self, obj):
        self.send(200, json.dumps(obj, ensure_ascii=False))

    def json_err(self, code, msg):
        self.send(code, json.dumps({"error": msg}, ensure_ascii=False))

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # --- yönlendirme ---
    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self.send(200, (UI / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/marked.min.js":
                self.send(200, (UI / "marked.min.js").read_bytes(), "application/javascript")
            elif u.path == "/api/config":
                self.json_ok(backend_info())
            elif u.path == "/api/settings":
                self.json_ok(read_settings())
            elif u.path == "/api/stat":
                info = file_info(parse_qs(u.query).get("path", [""])[0])
                if info:
                    self.json_ok(info)
                else:
                    self.json_err(404, "dosya bulunamadı")
            elif u.path == "/api/history":
                self.json_ok(history())
            elif u.path == "/api/run":  # tüm koşuların özeti
                with _state["lock"]:
                    runs = {k: {s: run_state(v)[s] for s in ("status", "step")}
                            for k, v in _state["runs"].items()}
                self.json_ok(runs)
            elif u.path.startswith("/api/run/"):
                rid = u.path.split("/api/run/", 1)[1].split("/")[0]
                with _state["lock"]:
                    run = _state["runs"].get(rid)
                if not run:
                    return self.json_err(404, "koşu yok")
                with _state["lock"]:
                    self.json_ok(run_state(run))
            elif u.path == "/api/notes":
                name = parse_qs(u.query).get("dir", [""])[0]
                d = out_child(name)
                if not d or not (d / "NOT.md").is_file():
                    return self.json_err(404, "not bulunamadı")
                self.json_ok({"name": name,
                              "markdown": (d / "NOT.md").read_text(encoding="utf-8")})
            else:
                self.json_err(404, "böyle bir uç nokta yok")
        except BrokenPipeError:
            pass
        except Exception as e:  # kullanıcıya ham traceback gösterme
            self.json_err(500, f"sunucu hatası: {e}")

    def do_POST(self):
        u = urlparse(self.path)
        # Yerel kaynak zorlaması: tarayıcıdaki başka bir site bu API'ye
        # (form/text-plain POST ile) yan etki yaratmasın.
        origin = self.headers.get("Origin")
        if origin:
            host = urlparse(origin).hostname or ""
            if host not in ("127.0.0.1", "localhost"):
                return self.json_err(403, "yalnız yerel arayüz")
        try:
            if u.path == "/api/pick":
                r = subprocess.run(
                    ["osascript", "-e",
                     'POSIX path of (choose file of type {"mp4","mov","m4v","mkv"} '
                     'with prompt "meetseen — toplantı kaydını seç")'],
                    capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    return self.json_ok({"cancelled": True})
                info = file_info(r.stdout.strip())
                return self.json_ok(info or {"error": "seçilen dosya okunamadı"})

            elif u.path == "/api/pickfolder":
                r = subprocess.run(
                    ["osascript", "-e",
                     'POSIX path of (choose folder '
                     'with prompt "meetseen — proje klasörünü seç (opsiyonel)")'],
                    capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    return self.json_ok({"cancelled": True})
                p = r.stdout.strip()
                if not p or p == "/":  # kök disk: taramak anlamsız
                    return self.json_ok({"error": "geçersiz seçim (kök disk)"})
                d = Path(p.rstrip("/"))
                if not d.is_dir():
                    return self.json_ok({"error": "seçilen klasör okunamadı"})
                return self.json_ok({"path": str(d.resolve()), "name": d.name})

            elif u.path == "/api/upload":
                name = unquote(self.headers.get("X-Filename", ""))
                n = int(self.headers.get("Content-Length") or 0)
                safe = re.sub(r"[^\w.\- çğıöşüÇĞİÖŞÜ]", "_", Path(name).name) or "kayit.mp4"
                p = Path(safe)
                if p.suffix.lower() not in ALLOWED_SUFFIX:
                    return self.json_err(400, "yalnız video dosyaları (mp4/mov)")
                if n > MAX_UPLOAD:
                    return self.json_err(400, "dosya çok büyük (>2 GB)")
                UP_DIR.mkdir(exist_ok=True)
                dest = UP_DIR / safe
                left = n
                with open(dest, "wb") as f:
                    while left:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                info = file_info(dest)
                self.json_ok(info) if info else self.json_err(500, "yüklenemedi")

            elif u.path == "/api/run":
                b = self.body_json()
                info = file_info(b.get("path", ""))
                if not info:
                    return self.json_err(400, "dosya bulunamadı — önce kaydı seç")
                if info["suffix"] not in ALLOWED_SUFFIX:
                    return self.json_err(400, "yalnız video dosyaları (mp4/mov)")
                project = (b.get("project") or "").strip()
                if project and not Path(project).expanduser().is_dir():
                    return self.json_err(400, "proje klasörü bulunamadı")
                run, err = start_run(info["path"], (b.get("title") or "").strip(),
                                     bool(b.get("keep_frames", True)), project)
                if err:
                    return self.json_err(409, err)
                self.json_ok({"run_id": run.id})

            elif u.path.startswith("/api/run/") and u.path.endswith("/cancel"):
                rid = u.path.split("/api/run/", 1)[1].split("/")[0]
                self.json_ok({"cancelled": cancel_run(rid)})

            elif u.path == "/api/settings":
                b = self.body_json()
                write_settings({k: v for k, v in b.items() if k in SETTINGS_KEYS})
                self.json_ok(read_settings())

            elif u.path == "/api/open":
                d = out_child(self.body_json().get("dir", ""))
                if not d:
                    return self.json_err(400, "yalnız not klasörleri açılabilir")
                subprocess.Popen(["open", str(d)])
                self.json_ok({"ok": True})

            else:
                self.json_err(404, "böyle bir uç nokta yok")
        except BrokenPipeError:
            pass
        except Exception as e:
            self.json_err(500, f"sunucu hatası: {e}")


def main():
    for port in (8765, 8766, 8767, 8768, 8769):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit("meetseen: 8765-8769 portlarının hepsi meşgul.")
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    URL_FILE.write_text(url, encoding="utf-8")
    print(f"meetseen arayüzü: {url} (durmak için Ctrl-C veya: pkill -f webui.py)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
