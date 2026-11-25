"""Dov'è la cam in un video, per "Trasforma in short" di FilmCraft Jarvis:
`JarvisClipper.exe --layout job.json out.json`. job = {"path", "start", "dur", "w", "h"}.

1. l'ultimo layout scelto in Jarvis Clipper per un video della stessa misura (di solito la scena OBS
   è sempre quella, e lì la cam l'hai sistemata tu);
2. se no, riconosce il viso in qualche fotogramma;
3. se no, la cam in basso a sinistra (come Jarvis Clipper di partenza)."""
import glob
import json
import os
import subprocess
import tempfile

from . import layout as lay
from . import media

DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "progetti")


def last_layout(w, h):
    files = sorted(glob.glob(os.path.join(DATA, "*", "*.json")), key=os.path.getmtime, reverse=True)
    for fp in files[:60]:
        try:
            p = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        info, L = p.get("info") or {}, p.get("layout") or {}
        if int(info.get("w", 0)) == int(w) and int(info.get("h", 0)) == int(h) and L.get("cam") and L.get("game"):
            return L
    return None


def run(job_path, out):
    try:
        job = json.load(open(job_path, encoding="utf-8"))
        w, h = float(job["w"]), float(job["h"])
        info = {"w": w, "h": h}
        L = last_layout(w, h)
        source = "progetto"
        if not L:
            L = lay.default_layout(info)
            source = "predefinito"
            d = tempfile.mkdtemp(prefix="jarvis_cam_")
            frames = []
            start, dur = float(job.get("start", 0)), float(job.get("dur", 30))
            for k in range(5):
                t = start + dur * (k + 0.5) / 5
                fp = os.path.join(d, f"f{k}.jpg")
                subprocess.run([media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", job["path"],
                                "-frames:v", "1", "-vf", "scale=960:-2", fp], capture_output=True, creationflags=media.CREATE_NO_WINDOW)
                if os.path.exists(fp):
                    frames.append(fp)
            cam = lay.auto_cam(frames, info, L.get("split", 0.4))
            if cam:
                L["cam"] = cam
                source = "volto"
        json.dump({"layout": L, "source": source}, open(out, "w", encoding="utf-8"))
        return True
    except Exception as e:
        json.dump({"error": str(e)[:300]}, open(out, "w", encoding="utf-8"))
        return False
