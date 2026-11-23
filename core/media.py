"""Helper ffmpeg: trova ffmpeg, legge info video, estrae frame/audio."""
import os, re, shutil, subprocess, sys

_FFMPEG = None
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def ffmpeg_exe():
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = None
    if not exe:
        raise RuntimeError("ffmpeg non trovato. Installa ffmpeg oppure 'pip install imageio-ffmpeg'.")
    _FFMPEG = exe
    return exe


def run(args, **kw):
    return subprocess.run([ffmpeg_exe()] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW, **kw)


_NVENC = None


def nvenc():
    """C'è l'encoder H.264 della scheda NVIDIA (h264_nvenc)? Provato una volta con un fotogramma."""
    global _NVENC
    if _NVENC is None:
        try:
            r = run(["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                     "-frames:v", "2", "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
            _NVENC = r.returncode == 0
        except Exception:
            _NVENC = False
    return _NVENC


def venc(quality="high", gop=None):
    """Argomenti dell'encoder video: la scheda video se c'è (molto più veloce), se no x264."""
    g = ["-g", str(gop)] if gop else []
    if nvenc():
        cq = {"high": "19", "edit": "18", "preview": "30"}.get(quality, "21")
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
                "-pix_fmt", "yuv420p"] + g
    crf = {"high": "18", "edit": "17", "preview": "28"}.get(quality, "20")
    pre = "ultrafast" if quality == "preview" else ("veryfast" if quality == "edit" else "veryfast")
    return ["-c:v", "libx264", "-preset", pre, "-crf", crf, "-pix_fmt", "yuv420p"] + g


def popen(args, **kw):
    return subprocess.Popen([ffmpeg_exe()] + args, creationflags=CREATE_NO_WINDOW, **kw)


def probe(path):
    """Info del video leggendo l'output di 'ffmpeg -i' (non serve ffprobe)."""
    r = run(["-hide_banner", "-i", path])
    err = r.stderr
    info = {"dur": 0.0, "w": 1920, "h": 1080, "fps": 30.0, "sr": 48000, "ch": 2, "has_audio": False}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["dur"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in err.splitlines():
        if "Video:" in line and "attached pic" not in line:
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                info["w"], info["h"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"([\d.]+) fps", line) or re.search(r"([\d.]+) tbr", line)
            if m:
                info["fps"] = float(m.group(1))
            m = re.search(r"([\d.]+) tbr", line)
            if m:
                info["tbr"] = float(m.group(1))
            break
    for line in err.splitlines():
        if "Audio:" in line:
            info["has_audio"] = True
            m = re.search(r"(\d+) Hz", line)
            if m:
                info["sr"] = int(m.group(1))
            if "mono" in line:
                info["ch"] = 1
            break
    if info["dur"] <= 0:
        raise RuntimeError("Non riesco a leggere il video: " + path)
    # frame rate "pulito" (59.94 -> 59.94, 60 -> 60, 29.97...)
    return info


def extract_frame(path, t, out_jpg, width=1280):
    r = run(["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", out_jpg])
    return os.path.exists(out_jpg)


def tc(sec):
    sec = max(0, sec)
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def edit_rate(info):
    """Frame rate da usare nei progetti (XML Premiere/FilmCraft, Kdenlive): (fps esatto, timebase, ntsc).
    I VOD di Kick/OBS hanno spesso un frame rate variabile: ffmpeg riporta la media (es. 59.76) ma il
    file è a 60 nominali e gli editor lo leggono a 60. Contare i fotogrammi a 59.76 fa slittare tutti i
    tagli sempre di più (13 s dopo un'ora). Quindi: frame rate NTSC standard se ci siamo, altrimenti il
    nominale (tbr) se è standard, altrimenti l'intero subito sopra la media."""
    fps = float(info.get("fps") or 30.0)
    for tb in (24, 30, 60, 120):
        if abs(fps - tb * 1000 / 1001) < 0.02:
            return tb * 1000 / 1001, tb, True
    tbr = info.get("tbr")
    if tbr and abs(tbr - round(tbr)) < 0.01 and round(tbr) in (24, 25, 30, 48, 50, 60, 90, 100, 120, 144) and tbr >= fps - 0.05:
        return float(round(tbr)), int(round(tbr)), False
    import math
    n = max(1, int(math.ceil(fps - 0.05)))
    return float(n), n, False
