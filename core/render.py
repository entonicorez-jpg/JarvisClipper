"""Render verticale 9:16: cam tagliata sopra, gioco sotto."""
import os
from . import media

OUT_W, OUT_H = 1080, 1920


def even(v):
    v = int(round(v))
    return v - (v % 2)


def heights(layout, H=OUT_H):
    if layout.get("mode") == "full" or not layout.get("cam"):
        return 0, H
    cam_h = even(H * float(layout.get("split", 0.4)))
    return cam_h, H - cam_h


def clamp_rect(r, info):
    x = max(0, min(info["w"] - 2, int(r["x"])))
    y = max(0, min(info["h"] - 2, int(r["y"])))
    w = even(min(r["w"], info["w"] - x)); h = even(min(r["h"], info["h"] - y))
    return {"x": x, "y": y, "w": max(2, w), "h": max(2, h)}


def filter_graph(info, layout, W=OUT_W, H=OUT_H):
    cam_h, game_h = heights(layout, H)
    g = clamp_rect(layout["game"], info)
    gf = f"crop={g['w']}:{g['h']}:{g['x']}:{g['y']},scale={W}:{game_h}:flags=lanczos,setsar=1"
    if cam_h == 0:
        return f"[0:v]{gf},format=yuv420p[v]"
    c = clamp_rect(layout["cam"], info)
    if layout.get("mode") == "bg":
        # cam ritagliata come vuoi tu, dentro il riquadro sopra; dietro il gioco ingrandito e sfumato
        m = float(layout.get("margin", 0.05))
        bw, bh = even(W * (1 - 2 * m)), even(cam_h * (1 - 2 * m))
        b = clamp_rect(layout.get("bg") or layout["game"], info)
        blur = max(4, int(W / 40))
        bgf = (f"crop={b['w']}:{b['h']}:{b['x']}:{b['y']},scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
               f"crop={W}:{cam_h},boxblur={blur}:2,eq=brightness=-0.06:saturation=1.1,setsar=1")
        cf = f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']},scale={bw}:{bh}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1"
        return (f"[0:v]split=3[a][b][s];[s]{bgf}[bg];[a]{cf}[c];[bg][c]overlay=(W-w)/2:(H-h)/2[top];"
                f"[b]{gf}[g];[top][g]vstack=inputs=2,format=yuv420p[v]")
    cf = f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']},scale={W}:{cam_h}:flags=lanczos,setsar=1"
    return f"[0:v]split=2[a][b];[a]{cf}[c];[b]{gf}[g];[c][g]vstack=inputs=2,format=yuv420p[v]"


def render(path, info, layout, start, end, out, preview=False, censor=None):
    W, H = (540, 960) if preview else (OUT_W, OUT_H)
    fg = filter_graph(info, layout, W, H)
    from .autoedit import bleep_args
    extra_in, afilt, amap = bleep_args(censor or [], start, end, preview)
    if afilt and afilt[0] == "-filter_complex":
        fg = fg + ";" + afilt[1]
        afilt = []
    args = ["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", path] + extra_in
    args += ["-filter_complex", fg, "-map", "[v]"] + amap + afilt
    if preview:
        args += ["-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-b:a", "96k"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    args += ["-movflags", "+faststart", out]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    r = media.run(args)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("Render fallito: " + r.stderr[-600:])
    return out
