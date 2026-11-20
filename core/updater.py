"""Aggiornamento automatico: controlla l'ultima release su GitHub, scarica il Setup e lo installa in silenzio."""
import json, os, subprocess, sys, tempfile, threading, time, urllib.request

REPO = "entonicorez-jpg/JarvisClipper"
STATE = {"running": False, "progress": 0.0, "msg": "", "error": None}
_cache = {"t": 0, "data": None}


def current_version():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        return open(os.path.join(base, "version.txt"), encoding="utf-8").read().strip()
    except Exception:
        return "dev"


def _vt(v):
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


def check(force=False):
    if not force and _cache["data"] and time.time() - _cache["t"] < 600:
        return _cache["data"]
    cur = current_version()
    res = {"current": cur, "latest": None, "available": False, "url": None, "error": None}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "JarvisClipper"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        latest = d.get("tag_name", "").lstrip("v")
        asset = next((a for a in d.get("assets", []) if a["name"].lower().endswith(".exe")), None)
        res.update(latest=latest, url=asset and asset["browser_download_url"], size=asset and asset["size"])
        res["available"] = bool(asset) and cur != "dev" and _vt(latest) > _vt(cur)
    except Exception as e:
        res["error"] = str(e)[:120]
    _cache.update(t=time.time(), data=res)
    return res


def install(on_exit):
    """Scarica il Setup, lo avvia in modalità silenziosa e chiude l'app (il Setup la riapre da solo)."""
    STATE.update(running=True, progress=0.0, error=None, msg="Scarico l'aggiornamento…")
    try:
        info = check(force=True)
        if not info.get("url"):
            raise RuntimeError("Nessun aggiornamento trovato")
        dst = os.path.join(tempfile.gettempdir(), f"JarvisClipperSetup_{info['latest']}.exe")
        req = urllib.request.Request(info["url"], headers={"User-Agent": "JarvisClipper"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
            tot = int(r.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b); done += len(b)
                if tot:
                    STATE.update(progress=0.95 * done / tot, msg=f"Scarico l'aggiornamento… {done / 1e6:.0f}/{tot / 1e6:.0f} MB")
        STATE.update(progress=1.0, msg="Installo: Jarvis si chiude e si riapre da solo tra pochi secondi…")
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([dst, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=flags, close_fds=True)
        threading.Timer(2.5, on_exit).start()
    except Exception as e:
        STATE.update(error=str(e), msg="Errore aggiornamento: " + str(e), running=False)
