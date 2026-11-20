"""Ponte Jarvis -> CUTROOM (editor open source, API locale su http://127.0.0.1:8765).

Manda a CUTROOM la parte scelta del VOD con il montaggio di Jarvis già sulla timeline:
- tagli (silenzi, spam TikTok) come parti rimosse e chiuse
- zoom sulla cam come inquadrature (zoom + punto) sui tratti giusti
- parolacce già coperte dal suono di censura nell'audio caricato
"""
import http.client, json, os, tempfile, time, urllib.parse, urllib.request, uuid
from . import media, autoedit


class CutroomError(RuntimeError):
    pass


class Client:
    def __init__(self, base="http://127.0.0.1:8765"):
        self.base = base.rstrip("/")

    def _req(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message")
            except Exception:
                msg = str(e)
            raise CutroomError(f"CUTROOM: {msg}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise CutroomError("CUTROOM non risponde: aprilo (START CUTROOM) e riprova") from e

    def health(self):
        return self._req("GET", "/api/health", timeout=5)

    def create(self, name):
        return self._req("POST", "/api/projects", {"name": name[:120], "initial_settings": {"goal": "youtube", "aspect": "16:9"}})["project"]

    def project(self, pid):
        r = self._req("GET", f"/api/projects/{pid}")
        return r.get("project", r)

    def upload_source(self, pid, slot, path, progress=lambda f: None):
        """Upload multipart in streaming (anche file da diversi GB)."""
        u = urllib.parse.urlparse(self.base)
        boundary = "----jarvis" + uuid.uuid4().hex
        name = os.path.basename(path)
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: video/mp4\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        size = os.path.getsize(path)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=3600)
        conn.putrequest("POST", f"/api/projects/{pid}/sources/{slot}")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(len(head) + size + len(tail)))
        conn.endheaders()
        conn.send(head)
        sent = 0
        with open(path, "rb") as f:
            while True:
                b = f.read(4 << 20)
                if not b:
                    break
                conn.send(b); sent += len(b)
                progress(sent / max(1, size))
        conn.send(tail)
        r = conn.getresponse()
        body = r.read()
        if r.status >= 300:
            try:
                msg = json.loads(body).get("message")
            except Exception:
                msg = body[:200]
            raise CutroomError(f"CUTROOM non accetta il video: {msg}")
        return json.loads(body or b"{}")

    def edit(self, pid, revision, **payload):
        r = self._req("POST", f"/api/projects/{pid}/manual/edit", dict(payload, expected_revision=revision))
        p = r.get("project", r)
        return p.get("revision", revision + 1)


def make_part(path, info, ed, out, progress=lambda f: None):
    """La parte scelta come file a sé: video copiato (veloce), audio con il suono di censura già applicato."""
    rs, re_ = ed["range"]
    cens = [dict(c, s=c["s"] - rs, e=c["e"] - rs) for c in (ed.get("censor") or []) if c.get("on", True)]
    extra_in, afilt, amap = autoedit.bleep_args(cens, 0.0, re_ - rs, False)
    args = ["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{rs:.3f}", "-t", f"{re_ - rs:.3f}", "-i", path] + extra_in
    args += ["-map", "0:v:0"] + amap + afilt + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                                 "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out]
    r = media.run(args)
    if r.returncode != 0 or not os.path.exists(out):
        raise CutroomError("Non riesco a preparare il video per CUTROOM: " + r.stderr[-300:])
    return out


def send(path, info, cam, ed, name, base="http://127.0.0.1:8765", progress=lambda f, m: None, log=print, stop=lambda: False):
    c = Client(base)
    c.health()
    rs, re_ = ed["range"]
    length = re_ - rs
    W, H = info["w"], info["h"]

    progress(0.02, "Preparo la parte del VOD per CUTROOM…")
    part = os.path.join(tempfile.mkdtemp(prefix="jarvis_cutroom_"), (name[:60] or "jarvis") + ".mp4")
    make_part(path, info, ed, part)

    proj = c.create(name)
    pid = proj["id"]
    log(f"CUTROOM: progetto {pid}")
    c.upload_source(pid, "A", part, lambda f: progress(0.05 + 0.4 * f, f"Mando il video a CUTROOM… {f * 100:.0f}%"))
    try:
        os.remove(part)
    except OSError:
        pass

    # aspetta che CUTROOM abbia preparato il video (anteprima, forma d'onda)
    t0 = time.time()
    while True:
        if stop():
            raise RuntimeError("Annullato")
        p = c.project(pid)
        st = ((p.get("sources") or {}).get("A") or {}).get("preparation")
        if st in (None, "ready", "complete", "completed", "done"):
            break
        if st == "failed":
            raise CutroomError("CUTROOM non è riuscito a preparare il video")
        progress(0.47, "CUTROOM sta preparando il video…")
        if time.time() - t0 > 3600:
            raise CutroomError("CUTROOM ci mette troppo a preparare il video")
        time.sleep(2)
    p = c.project(pid)
    for _ in range(60):  # aspetta che non ci siano lavori attivi
        try:
            r = c._req("POST", f"/api/projects/{pid}/manual-draft", {"expected_revision": p["revision"]})
            p = r.get("project", r); break
        except CutroomError as e:
            if "preparing" not in str(e).lower() and "busy" not in str(e).lower() and "wait" not in str(e).lower():
                raise
            time.sleep(2); p = c.project(pid)
    rev = p["revision"]

    # tagli: rimuovo i buchi (dalla fine, tempi = tempi del file caricato), poi chiudo i vuoti
    keep = sorted([max(0.0, a - rs), min(length, b - rs)] for a, b in ed["keep"])
    gaps, t = [], 0.0
    for a, b in keep:
        if a - t > 0.03:
            gaps.append((t, a))
        t = max(t, b)
    if length - t > 0.03:
        gaps.append((t, length))
    for i, (a, b) in enumerate(reversed(gaps)):
        if stop():
            raise RuntimeError("Annullato")
        rev = c.edit(pid, rev, action="sequence_remove_range", slot="A", start=round(a, 3), end=round(b, 3))
        if i % 10 == 0:
            progress(0.5 + 0.3 * i / max(1, len(gaps)), f"Metto i tagli in CUTROOM… {i + 1}/{len(gaps)}")
    if gaps:
        rev = c.edit(pid, rev, action="sequence_close_gaps", slot="A")

    # zoom: tratti in tempo montato con inquadratura (zoom, x, y) come CUTROOM la intende
    pieces = autoedit.build_pieces(ed["keep"], ed["zooms"])
    acc, spans = 0.0, []
    for a, b, lv, zi, tin, tout in pieces:
        d = b - a
        if lv and cam:
            x0, y0, w, h, S = autoedit.view(info, cam, lv, ed["zoom"])
            fx = x0 / (W - w) if W - w > 1 else 0.5
            fy = y0 / (H - h) if H - h > 1 else 0.5
            crop = (round(min(3.0, S), 4), round(fx, 4), round(fy, 4))
            if spans and abs(spans[-1][1] - acc) < 0.002 and spans[-1][2] == crop:
                spans[-1][1] = acc + d
            else:
                spans.append([acc, acc + d, crop])
        acc += d
    for i, (s, e, (z, fx, fy)) in enumerate(spans):
        if stop():
            raise RuntimeError("Annullato")
        rev = c.edit(pid, rev, action="sequence_crop", slot="A", start=round(s, 3), end=round(e, 3), x=fx, y=fy, zoom=z)
        if i % 10 == 0:
            progress(0.82 + 0.17 * i / max(1, len(spans)), f"Metto gli zoom in CUTROOM… {i + 1}/{len(spans)}")
    progress(1.0, "Pronto in CUTROOM")
    return {"project": pid, "url": c.base + "/", "tagli": len(gaps), "zoom": len(spans)}


# ------------------------------------------------------------------ installazione e avvio di CUTROOM
REPO = "VanoPeradze/cutroom"
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "CUTROOM")
STATE = {"running": False, "progress": 0.0, "msg": "", "error": None}


def find_launcher(root=INSTALL_DIR):
    """Il file di avvio di CUTROOM:
    - zip ufficiale: 'START CUTROOM.bat' con accanto la cartella 'App'
    - codice sorgente: 'run_windows.bat' nella cartella che contiene server.py"""
    best = None
    for d, dirs, files in os.walk(root):
        low = {f.lower(): f for f in files}
        if "start cutroom.bat" in low and any(x.lower() == "app" for x in dirs):
            return os.path.join(d, low["start cutroom.bat"])
        if "run_windows.bat" in low and "server.py" in low and best is None:
            best = os.path.join(d, low["run_windows.bat"])
    return best


def status(base="http://127.0.0.1:8765"):
    try:
        h = Client(base).health()
        running, version = True, h.get("version_label") or h.get("version")
    except Exception:
        running, version = False, None
    return {"running": running, "version": version, "installed": bool(find_launcher()), "dir": INSTALL_DIR,
            "install": dict(STATE)}


def _gh(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}",
                                 headers={"User-Agent": "JarvisClipper", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def pick_download():
    """-> (url, nome, versione). Pacchetto Windows da una release (anche beta) oppure il codice sorgente dell'ultima versione."""
    try:
        for rel in _gh("releases?per_page=15"):
            zips = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")]
            win = [a for a in zips if "win" in a["name"].lower()] or [a for a in zips if "mac" not in a["name"].lower()]
            if win:
                return win[0]["browser_download_url"], win[0]["name"], rel.get("tag_name", "")
    except Exception:
        pass
    try:
        tags = _gh("tags?per_page=30")
        tag = next((t["name"] for t in tags if "beta" in t["name"].lower() or t["name"].lower().startswith("v")), tags[0]["name"])
        return f"https://codeload.github.com/{REPO}/zip/refs/tags/{tag}", f"cutroom-{tag}.zip", tag
    except Exception:
        return f"https://codeload.github.com/{REPO}/zip/refs/heads/master", "cutroom-master.zip", "master"


def install():
    """Scarica CUTROOM per Windows e lo scompatta in INSTALL_DIR (al primo avvio installa da solo Python e FFmpeg)."""
    import zipfile
    STATE.update(running=True, progress=0.0, error=None, msg="Cerco l'ultima versione di CUTROOM…")
    try:
        url, name, tag = pick_download()
        tmp = os.path.join(tempfile.gettempdir(), name)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "JarvisClipper"}), timeout=60) as r, open(tmp, "wb") as f:
            tot = int(r.headers.get("Content-Length") or 0); done = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b); done += len(b)
                STATE.update(progress=0.9 * done / tot if tot else 0.5, msg=f"Scarico CUTROOM {tag}… {done / 1e6:.0f} MB")
        STATE.update(msg="Scompatto CUTROOM…")
        os.makedirs(INSTALL_DIR, exist_ok=True)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(INSTALL_DIR)
        os.remove(tmp)
        if not find_launcher():
            raise CutroomError("CUTROOM scaricato ma non trovo il file di avvio")
        STATE.update(progress=1.0, msg=f"CUTROOM {tag} installato: ora premi Avvia")
    except Exception as e:
        STATE.update(error=str(e), msg="Errore: " + str(e))
    finally:
        STATE["running"] = False


def start(base="http://127.0.0.1:8765", wait=900, progress=lambda m: None):
    """Avvia CUTROOM se non è già aperto e aspetta che risponda (la prima volta installa Python/FFmpeg: ci vuole un po')."""
    if status(base)["running"]:
        return True
    launcher = find_launcher()
    if not launcher:
        raise CutroomError("CUTROOM non è installato: premi 'Installa CUTROOM'")
    try:
        os.startfile(launcher)  # apre la sua finestra (al primo avvio fa il setup da solo)
    except AttributeError:
        import subprocess
        subprocess.Popen(["bash", launcher], cwd=os.path.dirname(launcher))
    t0 = time.time()
    while time.time() - t0 < wait:
        progress(f"Avvio CUTROOM… {int(time.time() - t0)} s (la prima volta installa i suoi componenti)")
        if status(base)["running"]:
            return True
        time.sleep(3)
    raise CutroomError("CUTROOM non si è avviato in tempo: guarda la sua finestra nera, poi riprova")
