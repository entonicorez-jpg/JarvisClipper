"""FilmCraft Jarvis: FilmCraft con il comando "Aggiungi taglio e seleziona", compilato dal workflow
filmcraft.yml. Installazione in %LOCALAPPDATA%\\FilmCraftJarvis, aggiornamento e apertura dell'XML."""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request, zipfile

from core.updater import REPO

DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FilmCraftJarvis")
TAG_FILE = os.path.join(DIR, "jarvis_tag.txt")
STATE = {"running": False, "progress": 0.0, "msg": "", "error": None}
_cache = {"t": 0, "data": None}
_UA = {"Accept": "application/vnd.github+json", "User-Agent": "JarvisClipper"}


def exe():
    if not os.path.isdir(DIR):
        return None
    for root, _, files in os.walk(DIR):
        if "filmcraft.exe" in (f.lower() for f in files):
            return os.path.join(root, next(f for f in files if f.lower() == "filmcraft.exe"))
    return None


def has_cap(cap):
    """Funzioni della build installata (file jarvis_caps.txt messo dalla build di GitHub)."""
    e = exe()
    if not e:
        return False
    try:
        return cap in open(os.path.join(os.path.dirname(e), "jarvis_caps.txt"), encoding="utf-8").read().split()
    except Exception:
        return False


def installed_tag():
    try:
        return open(TAG_FILE, encoding="utf-8").read().strip()
    except Exception:
        return None


def latest(force=False):
    if not force and _cache["data"] and time.time() - _cache["t"] < 600:
        return _cache["data"]
    res = {"tag": None, "url": None, "size": 0, "error": None}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=100", headers=_UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            rels = json.loads(r.read())
        rels = [x for x in rels if x.get("tag_name", "").startswith("filmcraft-jarvis-") and not x.get("draft")]
        rel = max(rels, key=lambda x: x.get("published_at") or x.get("created_at") or "", default=None)
        if rel:
            a = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")), None)
            if a:
                res.update(tag=rel["tag_name"], url=a["browser_download_url"], size=a["size"])
    except Exception as e:
        res["error"] = str(e)[:120]
    _cache.update(t=time.time(), data=res)
    return res


def status():
    lt = latest()
    cur = installed_tag() if exe() else None
    return {"installed": bool(cur), "tag": cur, "latest": lt["tag"], "ready": bool(lt["url"]),
            "update": bool(cur and lt["tag"] and lt["tag"] != cur), "error": lt["error"], "job": dict(STATE)}


def _shortcut(target):
    """Collegamento nel menu Start e sul Desktop."""
    if sys.platform != "win32":
        return
    ps = ("$s=New-Object -ComObject WScript.Shell;"
          "foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){"
          "$l=$s.CreateShortcut((Join-Path $d 'FilmCraft Jarvis.lnk'));"
          f"$l.TargetPath='{target}';$l.WorkingDirectory='{os.path.dirname(target)}';$l.Save()}}")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   creationflags=0x08000000, timeout=60)


def install():
    STATE.update(running=True, progress=0.0, error=None, msg="Scarico FilmCraft Jarvis…")
    try:
        lt = latest(force=True)
        if not lt["url"]:
            raise RuntimeError("FilmCraft Jarvis non è ancora pronto su GitHub, riprova tra poco")
        zp = os.path.join(tempfile.gettempdir(), "FilmCraftJarvis.zip")
        req = urllib.request.Request(lt["url"], headers={"User-Agent": "JarvisClipper"})
        with urllib.request.urlopen(req, timeout=60) as r, open(zp, "wb") as f:
            tot = int(r.headers.get("Content-Length") or lt["size"] or 0); done = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b); done += len(b)
                if tot:
                    STATE.update(progress=0.9 * done / tot, msg=f"Scarico FilmCraft Jarvis… {done / 1e6:.0f}/{tot / 1e6:.0f} MB")
        STATE.update(progress=0.92, msg="Installo…")
        new = DIR + "_new"
        shutil.rmtree(new, ignore_errors=True)
        with zipfile.ZipFile(zp) as z:
            z.extractall(new)
        # niente modalità portatile: usa le stesse impostazioni e scorciatoie del FilmCraft ufficiale
        for r, _, fs in os.walk(new):
            for f in fs:
                if f.lower() == "portable.txt":
                    os.remove(os.path.join(r, f))
        shutil.rmtree(DIR, ignore_errors=True)
        if os.path.exists(DIR):
            raise RuntimeError("Chiudi FilmCraft e riprova")
        os.replace(new, DIR)
        open(TAG_FILE, "w", encoding="utf-8").write(lt["tag"])
        try:
            _shortcut(exe())
        except Exception:
            pass
        os.remove(zp)
        STATE.update(progress=1.0, msg="FilmCraft Jarvis installato", running=False)
    except Exception as e:
        STATE.update(error=str(e), msg="Errore: " + str(e), running=False)


def start_install():
    if STATE["running"]:
        return False
    threading.Thread(target=install, daemon=True).start()
    return True


_live = {"proc": None, "port": None, "token": None}


def _control(method, params, timeout=60):
    """Una richiesta al FilmCraft Jarvis aperto da noi (canale di controllo locale)."""
    import socket
    with socket.create_connection(("127.0.0.1", _live["port"]), timeout=timeout) as so:
        f = so.makefile("rw", encoding="utf-8")
        f.write(json.dumps({"id": 1, "method": method, "params": params, "token": _live["token"]}) + "\n")
        f.flush()
        r = json.loads(f.readline() or "{}")
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "FilmCraft non risponde")
    return r.get("result")


def _free_port():
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def open_xml(xml):
    """Apre il montaggio in FilmCraft Jarvis. Se l'abbiamo già aperto noi, il montaggio nuovo entra lì
    come sequenza nuova (niente seconda finestra); altrimenti lo avvia senza recuperare il lavoro
    vecchio. False se non è installato."""
    e = exe()
    if not e:
        return False
    pr = _live["proc"]
    if pr is not None and pr.poll() is None and _live["port"]:
        try:
            r = _control("ui.menu.invoke", {"command": "file.import", "params": {"paths": [xml]}}, timeout=300) or {}
            seqs = r.get("sequences") or []
            if seqs:
                _control("ui.menu.invoke", {"command": "sequence.open", "params": {"item": seqs[-1]}})
            try:
                _control("ui.focus", {})
            except Exception:
                pass
            return True
        except Exception:
            pass  # non risponde: ne apriamo uno nuovo
    import secrets
    _live.update(port=_free_port(), token=secrets.token_hex(32))
    env = dict(os.environ, FILMCRAFT_CONTROL_TOKEN=_live["token"])
    _live["proc"] = subprocess.Popen([e, "--control", str(_live["port"]), "--no-recover", "--empty", xml],
                                     cwd=os.path.dirname(e), env=env, close_fds=True,
                                     creationflags=0x00000200 if sys.platform == "win32" else 0)
    return True
