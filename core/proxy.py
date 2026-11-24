"""Proxy leggeri per la preview di FilmCraft Jarvis: `JarvisClipper.exe --proxy job.json out.json`.

job = {"items": [{"item", "path", "fps", "out"}]}. Per ogni video: metà risoluzione (540p), frame rate
costante uguale a quello che legge FilmCraft, un fotogramma chiave ogni mezzo secondo (così saltare
da un taglio all'altro è immediato), encoder della scheda video se c'è. L'audio resta uguale.
"""
import json
import os
import re
import subprocess

from . import media


def _progress(out, f, msg):
    try:
        with open(out + ".progress", "w", encoding="utf-8") as fh:
            json.dump({"f": round(float(f), 3), "msg": msg}, fh)
    except OSError:
        pass


def run(job_path, out):
    try:
        with open(job_path, encoding="utf-8") as f:
            items = json.load(f).get("items") or []
        if not items:
            raise RuntimeError("nessun video")
        durs = []
        for it in items:
            try:
                durs.append(max(1.0, float(media.probe(it["path"])["dur"])))
            except Exception:
                durs.append(60.0)
        tot = sum(durs)
        done = 0.0
        res = []
        for k, (it, dur) in enumerate(zip(items, durs)):
            fps = float(it.get("fps") or 60.0)
            dst = it["out"]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst) and os.path.getsize(dst) > 10000 and os.path.getmtime(dst) > os.path.getmtime(it["path"]):
                res.append({"item": it["item"], "out": dst})
                done += dur
                continue
            tmp = dst + ".part.mp4"
            args = [media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
                    "-i", it["path"], "-map", "0:v:0", "-map", "0:a?", "-vf", "scale=-2:540:flags=bilinear",
                    "-fps_mode", "cfr", "-r", f"{fps:.6f}"] + media.venc("preview", gop=max(2, round(fps / 2))) + \
                   ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", tmp]
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding="utf-8", errors="replace", creationflags=media.CREATE_NO_WINDOW)
            for line in p.stdout:
                m = re.match(r"out_time_us=(\d+)", line.strip())
                if m:
                    t = int(m.group(1)) / 1e6
                    _progress(out, (done + min(t, dur)) / tot, f"Preview fluida: video {k + 1}/{len(items)} {int(min(t, dur) / dur * 100)}%")
            err = p.stderr.read()
            if p.wait() != 0 or not os.path.exists(tmp):
                raise RuntimeError("proxy non creato: " + err[-300:])
            os.replace(tmp, dst)
            res.append({"item": it["item"], "out": dst})
            done += dur
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"items": res}, f, ensure_ascii=False)
        _progress(out, 1.0, "Fatto")
        return True
    except Exception as e:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"error": str(e)[:400]}, f, ensure_ascii=False)
        return False
