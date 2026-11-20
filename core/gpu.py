"""Pacchetto GPU NVIDIA facoltativo: scarica le DLL CUDA (cuBLAS + cuDNN) da PyPI e le estrae
in %LOCALAPPDATA%\\JarvisClipper\\gpu. Niente pip, niente .bat: un clic dall'app."""
import glob, json, os, sys, urllib.request, zipfile

GPU_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "gpu")
PACKAGES = [("nvidia-cublas-cu12", None), ("nvidia-cudnn-cu12", "9."), ("nvidia-cuda-nvrtc-cu12", None)]
STATE = {"running": False, "progress": 0.0, "msg": "", "error": None}


def installed():
    return os.path.isdir(GPU_DIR) and bool(glob.glob(os.path.join(GPU_DIR, "cublas64_*.dll"))) \
        and bool(glob.glob(os.path.join(GPU_DIR, "cudnn*.dll")))


def add_dll_dirs():
    dirs = [GPU_DIR] if os.path.isdir(GPU_DIR) else []
    for sp in sys.path:
        dirs += glob.glob(os.path.join(sp, "nvidia", "*", "bin"))
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def has_nvidia():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _wheel_url(pkg, prefix):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
        data = json.loads(r.read())
    versions = [v for v in data["releases"] if (prefix is None or v.startswith(prefix))]

    def key(v):
        return [int(x) if x.isdigit() else 0 for x in v.replace("-", ".").split(".")]
    for v in sorted(versions, key=key, reverse=True):
        for f in data["releases"][v]:
            if f["filename"].endswith("win_amd64.whl") and not f.get("yanked"):
                return f["url"], f["size"], v
    raise RuntimeError(f"Nessun pacchetto Windows per {pkg}")


def install(stop=lambda: False):
    STATE.update(running=True, progress=0.0, error=None, msg="Cerco le librerie NVIDIA…")
    try:
        os.makedirs(GPU_DIR, exist_ok=True)
        tmp = os.path.join(GPU_DIR, "_download.whl")
        files = [_wheel_url(p, pre) for p, pre in PACKAGES]
        total = sum(s for _, s, _ in files) or 1
        done = 0
        for i, (url, size, ver) in enumerate(files):
            name = PACKAGES[i][0]
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
                while True:
                    if stop():
                        raise RuntimeError("Annullato")
                    b = r.read(1 << 20)
                    if not b:
                        break
                    out.write(b); done += len(b)
                    STATE.update(progress=0.95 * done / total,
                                 msg=f"Scarico {name} {ver}… {done / 1e6:.0f}/{total / 1e6:.0f} MB")
            STATE["msg"] = f"Estraggo {name}…"
            with zipfile.ZipFile(tmp) as z:
                for m in z.namelist():
                    if m.lower().endswith(".dll"):
                        with z.open(m) as src, open(os.path.join(GPU_DIR, os.path.basename(m)), "wb") as dst:
                            dst.write(src.read())
            os.remove(tmp)
        STATE.update(progress=1.0, msg="GPU attivata: dalla prossima analisi Whisper usa la scheda video")
    except Exception as e:
        STATE.update(error=str(e), msg="Errore: " + str(e))
    finally:
        STATE["running"] = False
