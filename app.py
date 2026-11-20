"""Jarvis Clipper - trova le clip virali di un VOD Kick o di una registrazione.
Avvio: python app.py  (si apre il browser su http://127.0.0.1:7865)
"""
import json, os, re, sys, threading, time, traceback, webbrowser
from flask import Flask, jsonify, request, send_file, send_from_directory, abort

FROZEN = getattr(sys, "frozen", False)
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # file dell'app (sola lettura)
sys.path.insert(0, BASE)
from core import media, analyze, render, premiere, gpu, layout as lay  # noqa: E402

# dati scrivibili: accanto allo script, oppure in %LOCALAPPDATA%\JarvisClipper se è l'app installata
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper") if FROZEN else BASE
PROJ_DIR = os.path.join(DATA_DIR, "progetti")
CFG_PATH = os.path.join(DATA_DIR, "config.json")
WINDOW = None  # finestra pywebview quando gira come app desktop
USER_DIR = os.path.join(os.path.expanduser("~"), "Videos", "JarvisClipper")
os.makedirs(PROJ_DIR, exist_ok=True)
os.makedirs(USER_DIR, exist_ok=True)

DEFAULT_CFG = {"api_key": "", "ai_model": "claude-sonnet-5-5", "model": "small", "max_clips": 15,
               "split": 0.4, "min_len": 15, "max_len": 59, "pre": 14, "post": 8, "lang": "it"}

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
LOCK = threading.RLock()
JOBS = {}  # pid -> {"stop": bool}


# ------------------------------------------------------------------ storage
def cfg():
    c = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        try:
            c.update(json.load(open(CFG_PATH, encoding="utf-8")))
        except Exception:
            pass
    return c


def pdir(pid):
    if not re.fullmatch(r"[\w-]+", pid or ""):
        abort(404)
    return os.path.join(PROJ_DIR, pid)


def load(pid):
    with LOCK:
        f = os.path.join(pdir(pid), "project.json")
        if not os.path.exists(f):
            abort(404)
        return json.load(open(f, encoding="utf-8"))


def save(p):
    with LOCK:
        d = pdir(p["id"]); os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, "project.tmp")
        json.dump(p, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(d, "project.json"))


def update(pid, **kw):
    with LOCK:
        p = load(pid); p.update(kw); save(p); return p


def set_job(pid, **kw):
    with LOCK:
        p = load(pid); j = p.get("job") or {}
        if "log" in kw:
            j.setdefault("log", []).append(time.strftime("%H:%M:%S ") + kw.pop("log"))
            j["log"] = j["log"][-60:]
        j.update(kw); p["job"] = j; save(p); return p


def start_job(pid, name, fn):
    if pid in JOBS and JOBS[pid].get("alive"):
        return False
    JOBS[pid] = {"stop": False, "alive": True}
    set_job(pid, name=name, running=True, progress=0, msg="Avvio…", error=None)

    def wrap():
        try:
            fn()
            set_job(pid, running=False, progress=1)
        except Exception as e:
            traceback.print_exc()
            msg = ANSI.sub("", str(e)).replace("ERROR: ", "")
            set_job(pid, running=False, error=msg, msg="Errore: " + msg, log="ERRORE " + msg)
        finally:
            JOBS[pid]["alive"] = False
    threading.Thread(target=wrap, daemon=True).start()
    return True


def stopped(pid):
    return JOBS.get(pid, {}).get("stop", False)


# ------------------------------------------------------------------ fasi
def job_prepare(pid, source):
    p = load(pid)
    src = source.strip().strip('"')
    if re.match(r"https?://", src):
        set_job(pid, msg="Scarico il VOD…", log="Download: " + src)
        src = download(pid, src)
        update(pid, src_path=src, name=os.path.splitext(os.path.basename(src))[0])
    if not os.path.exists(src):
        raise RuntimeError("File non trovato: " + src)
    set_job(pid, msg="Leggo il video…", progress=0.95)
    info = media.probe(src)
    d = pdir(pid); frames = []
    for i, f in enumerate((0.12, 0.35, 0.6, 0.85)):
        fp = os.path.join(d, f"frame_{i}.jpg")
        if media.extract_frame(src, info["dur"] * f, fp):
            frames.append(f"frame_{i}.jpg")
    c = cfg()
    L = lay.default_layout(info, c["split"])
    cam = lay.auto_cam([os.path.join(d, f) for f in frames], info, c["split"])
    if cam:
        L["cam"], L["auto"] = cam, True
    update(pid, src_path=src, info=info, frames=frames, layout=L, status="layout",
           out_dir=os.path.join(os.path.dirname(src), os.path.splitext(os.path.basename(src))[0][:60] + "_JarvisClips"))
    set_job(pid, msg="Pronto: controlla dove sono cam e gioco", log=f"Video {info['w']}x{info['h']} {info['fps']:.2f}fps, {media.tc(info['dur'])}")


ANSI = re.compile(r"\x1b\[[0-9;]*m")
KICK_RE = re.compile(r"kick\.com/(?:(?P<ch>[\w-]+)/videos?|video)/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def http_json(url):
    """GET JSON passando Cloudflare (curl_cffi imita Chrome); ripiego su urllib."""
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=30)
        if r.status_code == 200:
            return r.json()
        err = f"HTTP {r.status_code}"
    except Exception as e:
        err = str(e)
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e2:
        raise RuntimeError(f"Kick non risponde ({err} / {e2})")


def kick_vod(url):
    """Ritorna (link m3u8, titolo) di un VOD Kick usando l'API di Kick."""
    m = KICK_RE.search(url)
    if not m:
        return None
    uuid, ch = m.group("uuid"), m.group("ch")
    src, title = None, None
    try:
        d = http_json(f"https://kick.com/api/v1/video/{uuid}")
        src = d.get("source")
        ls = d.get("livestream") or {}
        title = ls.get("session_title")
        ch = ch or (ls.get("channel") or {}).get("slug")
    except Exception:
        if not ch:
            raise
    if not src and ch:
        for v in http_json(f"https://kick.com/api/v2/channels/{ch}/videos"):
            if (v.get("video") or {}).get("uuid") == uuid:
                src, title = v.get("source"), v.get("session_title"); break
    if not src:
        raise RuntimeError("Kick non mi dà il link di questo VOD (è privato o è stato cancellato?)")
    return src, f"{ch or 'kick'} - {title or uuid}"


def ytdl(pid, url, tmpl):
    import yt_dlp

    def hook(d):
        if stopped(pid):
            raise RuntimeError("Annullato")
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = d.get("downloaded_bytes", 0) / tot if tot else 0
            if d.get("fragment_count"):
                frac = (d.get("fragment_index") or 0) / d["fragment_count"]
            set_job(pid, progress=0.9 * frac, msg=f"Scarico il VOD… {frac * 100:.0f}%")
    opts = {"outtmpl": tmpl,
            "format": "bv*+ba/b", "merge_output_format": "mp4", "concurrent_fragment_downloads": 8,
            "progress_hooks": [hook], "quiet": True, "noprogress": True, "windowsfilenames": True,
            "ffmpeg_location": media.ffmpeg_exe()}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        rd = info.get("requested_downloads") or []
        path = rd[0].get("filepath") if rd else y.prepare_filename(info)
    if not os.path.exists(path):
        base = os.path.splitext(path)[0]
        for ext in (".mp4", ".mkv", ".ts", ".webm"):
            if os.path.exists(base + ext):
                path = base + ext; break
    return path


def download(pid, url):
    out = os.path.join(USER_DIR, "VOD")
    os.makedirs(out, exist_ok=True)
    url = url.split("?")[0] if "kick.com" in url else url
    # 1) yt-dlp sul link originale (se il VOD è già scaricato lo riusa subito)
    try:
        return ytdl(pid, url, os.path.join(out, "%(uploader)s - %(title).70s [%(id).12s].%(ext)s"))
    except Exception as e:
        if "Annullato" in str(e) or not KICK_RE.search(url):
            raise
        first = ANSI.sub("", str(e))
    # 2) ripiego: link del video chiesto direttamente all'API di Kick
    set_job(pid, log="yt-dlp non ce la fa (" + first[:100] + "), provo con l'API di Kick")
    try:
        kv = kick_vod(url)
    except Exception as e:
        raise RuntimeError(f"Non riesco a scaricare questo VOD. yt-dlp: {first[:150]} | Kick: {e}")
    set_job(pid, msg="Trovato il VOD su Kick, scarico…", log="Kick: " + kv[1])
    name = re.sub(r'[\\/:*?"<>|]+', "", kv[1])[:90].strip()
    update(pid, name=name)
    return ytdl(pid, kv[0], os.path.join(out, name + ".%(ext)s"))


def job_analyze(pid, opts):
    p = load(pid)
    c = cfg(); c.update(opts or {})
    set_job(pid, log="Analisi avviata")
    clips = analyze.analyze(p["src_path"], p["info"], p["layout"], c,
                            lambda f, m: set_job(pid, progress=0.9 * f, msg=m),
                            lambda m: set_job(pid, log=m), lambda: stopped(pid))
    for cl in clips:
        cl["selected"] = cl["score"] >= 60
        cl["preview"] = None
    update(pid, clips=clips, status="clips", layout_ver=p.get("layout_ver", 0))
    set_job(pid, log=f"Trovate {len(clips)} clip")
    render_previews(pid, 0.9)


def render_previews(pid, base=0.0, only=None):
    p = load(pid)
    todo = [c for c in p["clips"] if (only is None or c["id"] in only)]
    for k, c in enumerate(todo):
        if stopped(pid):
            raise RuntimeError("Annullato")
        set_job(pid, progress=base + (1 - base) * k / max(1, len(todo)),
                msg=f"Preparo le anteprime verticali {k + 1}/{len(todo)}…")
        ver = int(time.time() * 1000) % 10 ** 8
        name = f"prev_{c['id']}_{ver}.mp4"
        render.render(p["src_path"], p["info"], p["layout"], c["start"], c["end"],
                      os.path.join(pdir(pid), name), preview=True)
        with LOCK:
            q = load(pid)
            for qc in q["clips"]:
                if qc["id"] == c["id"]:
                    old = qc.get("preview")
                    qc["preview"] = name
                    if old and old != name:
                        try:
                            os.remove(os.path.join(pdir(pid), old))
                        except OSError:
                            pass
            save(q)
    set_job(pid, msg="Anteprime pronte")


def job_export(pid, ids, do_render):
    p = load(pid)
    sel = [c for c in p["clips"] if c["id"] in ids]
    if not sel:
        raise RuntimeError("Nessuna clip selezionata")
    out = p["out_dir"]; os.makedirs(out, exist_ok=True)
    rendered = {}
    if do_render:
        for k, c in enumerate(sel):
            if stopped(pid):
                raise RuntimeError("Annullato")
            set_job(pid, progress=k / len(sel), msg=f"Renderizzo la clip {k + 1}/{len(sel)} in 1080x1920…")
            safe = re.sub(r'[\\/:*?"<>|]+', "", c.get("title", ""))[:40].strip()
            fp = os.path.join(out, f"Clip_{c['id']:02d}_{c['score']}_{safe}.mp4".replace(" ", "_"))
            render.render(p["src_path"], p["info"], p["layout"], c["start"], c["end"], fp)
            rendered[c["id"]] = fp
    stamp = time.strftime("%Y%m%d_%H%M")
    xml = os.path.join(out, f"{p.get('name', 'clips')[:50]}_Premiere_{stamp}.xml")
    premiere.build_xml(sel, p["src_path"], p["info"], p["layout"], rendered, xml)
    update(pid, last_export={"xml": xml, "dir": out, "n": len(sel)})
    set_job(pid, msg=f"Esportate {len(sel)} clip", log="XML: " + xml)
    try:
        os.startfile(out)  # apre la cartella (Windows)
    except Exception:
        pass


# ------------------------------------------------------------------ API
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        c = cfg(); c.update({k: v for k, v in request.json.items() if k in DEFAULT_CFG})
        json.dump(c, open(CFG_PATH, "w", encoding="utf-8"), indent=1)
    c = cfg(); c["api_key_set"] = bool(c.get("api_key")); c["api_key"] = ""
    return jsonify(c)


@app.route("/api/gpu", methods=["GET", "POST"])
def api_gpu():
    if request.method == "POST" and not gpu.STATE["running"]:
        gpu.STATE["running"] = True
        threading.Thread(target=gpu.install, daemon=True).start()
    return jsonify(dict(gpu.STATE, installed=gpu.installed(), nvidia=gpu.has_nvidia()))


@app.route("/api/projects")
def api_projects():
    out = []
    for pid in sorted(os.listdir(PROJ_DIR), reverse=True)[:20]:
        f = os.path.join(PROJ_DIR, pid, "project.json")
        if os.path.exists(f):
            try:
                p = json.load(open(f, encoding="utf-8"))
                out.append({"id": pid, "name": p.get("name"), "status": p.get("status"),
                            "n": len(p.get("clips") or []), "frame": (p.get("frames") or [None])[0]})
            except Exception:
                pass
    return jsonify(out)


@app.route("/api/pick_file")
def api_pick_file():
    if WINDOW is not None:
        try:
            import webview
            r = WINDOW.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=(
                "Video (*.mp4;*.mkv;*.mov;*.flv;*.ts;*.webm;*.avi)", "Tutti i file (*.*)"))
            return jsonify({"path": (r[0] if r else "") or ""})
        except Exception as e:
            return jsonify({"path": "", "error": str(e)})
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        f = filedialog.askopenfilename(title="Scegli il VOD o la registrazione", filetypes=[
            ("Video", "*.mp4 *.mkv *.mov *.flv *.ts *.webm *.avi"), ("Tutti", "*.*")])
        root.destroy()
        return jsonify({"path": f or ""})
    except Exception as e:
        return jsonify({"path": "", "error": str(e)})


@app.route("/api/project/new", methods=["POST"])
def api_new():
    source = (request.json or {}).get("source", "").strip()
    if not source:
        return jsonify({"error": "Incolla un link Kick o scegli un file"}), 400
    pid = time.strftime("%Y%m%d_%H%M%S")
    name = os.path.splitext(os.path.basename(source.strip('"')))[0] if not source.startswith("http") else "VOD Kick"
    save({"id": pid, "source": source, "name": name, "status": "prepare", "clips": [], "job": {}})
    start_job(pid, "prepare", lambda: job_prepare(pid, source))
    return jsonify({"id": pid})


@app.route("/api/project/<pid>")
def api_get(pid):
    p = load(pid)
    for c in p.get("clips", []):
        c.pop("words", None)
    return jsonify(p)


@app.route("/api/project/<pid>/layout", methods=["POST"])
def api_layout(pid):
    L = request.json
    p = load(pid)
    for k in ("cam", "game"):
        if L.get(k):
            L[k] = render.clamp_rect(L[k], p["info"])
    if L.get("mode") == "full":
        L["cam"] = None
    p["layout"] = L
    save(p)
    if p.get("clips") and request.args.get("rerender") == "1":
        start_job(pid, "previews", lambda: render_previews(pid))
    return jsonify({"ok": True, "layout": L})


@app.route("/api/project/<pid>/autocam", methods=["POST"])
def api_autocam(pid):
    p = load(pid); split = float(request.json.get("split", 0.4))
    cam = lay.auto_cam([os.path.join(pdir(pid), f) for f in p.get("frames", [])], p["info"], split)
    return jsonify({"cam": cam})


@app.route("/api/project/<pid>/analyze", methods=["POST"])
def api_analyze(pid):
    opts = request.json or {}
    update(pid, status="analyzing")
    ok = start_job(pid, "analyze", lambda: job_analyze(pid, opts))
    return jsonify({"ok": ok})


@app.route("/api/project/<pid>/clip/<int:cid>", methods=["POST"])
def api_clip(pid, cid):
    data = request.json or {}
    with LOCK:
        p = load(pid)
        for c in p["clips"]:
            if c["id"] == cid:
                if "start" in data:
                    c["start"] = max(0.0, float(data["start"]))
                if "end" in data:
                    c["end"] = min(p["info"]["dur"], float(data["end"]))
                if "selected" in data:
                    c["selected"] = bool(data["selected"])
                if "title" in data:
                    c["title"] = str(data["title"])[:120]
        save(p)
    if data.get("rerender"):
        start_job(pid, "previews", lambda: render_previews(pid, only=[cid]))
    return jsonify({"ok": True})


@app.route("/api/project/<pid>/export", methods=["POST"])
def api_export(pid):
    d = request.json or {}
    ids = [int(i) for i in d.get("ids", [])]
    ok = start_job(pid, "export", lambda: job_export(pid, ids, d.get("render", True)))
    return jsonify({"ok": ok})


@app.route("/api/project/<pid>/stop", methods=["POST"])
def api_stop(pid):
    if pid in JOBS:
        JOBS[pid]["stop"] = True
    return jsonify({"ok": True})


@app.route("/api/project/<pid>/open", methods=["POST"])
def api_open(pid):
    p = load(pid)
    try:
        os.makedirs(p["out_dir"], exist_ok=True); os.startfile(p["out_dir"])
    except Exception as e:
        return jsonify({"error": str(e)})
    return jsonify({"ok": True})


@app.route("/p/<pid>/<path:fname>")
def files(pid, fname):
    return send_file(os.path.join(pdir(pid), os.path.basename(fname)), conditional=True)


if __name__ == "__main__":
    port = 7865
    print(f"\n  JARVIS CLIPPER attivo su http://127.0.0.1:{port}\n  (chiudi questa finestra per spegnerlo)\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, threaded=True)
