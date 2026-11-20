"""Montaggio automatico per video orizzontali: taglia i silenzi e fa zoom sulla cam quando reagisci.
Livelli: 0 = inquadratura normale, 1 = zoom verso la cam, 2 = cam (quasi) a tutto schermo.
"""
import os, re, tempfile
import numpy as np
from . import media, analyze

RITMO = {  # posizione soglia tra rumore e voce (0-1), silenzio minimo s, margine s
    "tranquillo": (0.25, 1.0, 0.25),
    "normale": (0.35, 0.6, 0.15),
    "veloce": (0.45, 0.4, 0.08),
}


def auto_threshold(half_db, pos):
    """Soglia del silenzio adattata al video: tra il rumore di fondo e il livello della voce."""
    x = half_db[half_db > -69]
    if len(x) < 20:
        return -35.0
    floor, voice = np.percentile(x, 10), np.percentile(x, 75)
    if voice - floor < 6:  # sempre rumoroso (musica/gioco forte): taglia solo i veri buchi
        return float(floor + 2)
    return float(np.clip(floor + pos * (voice - floor), -60, -22))
ZOOM = {"leggero": 1.18, "normale": 1.32, "forte": 1.5}


# ------------------------------------------------------------------ analisi
def silences(path, thr, mind, stop=lambda: False):
    r = media.run(["-hide_banner", "-nostats", "-i", path, "-vn", "-af",
                   f"silencedetect=noise={thr}dB:d={mind}", "-f", "null", "-"])
    out, cur = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            cur = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, float(m.group(1)))); cur = None
    if cur is not None:
        out.append((cur, 1e12))
    return out


def keep_segments(sil, dur, pad, min_keep=0.25):
    keep, t = [], 0.0
    for s, e in sil:
        s2, e2 = s + pad, e - pad
        if e2 - s2 <= 0.05:
            continue
        if s2 > t:
            keep.append((t, min(s2, dur)))
        t = max(t, e2)
    if t < dur:
        keep.append((t, dur))
    return [(a, b) for a, b in keep if b - a >= min_keep]


def reactions(half_db, sens=1.0):
    """Momenti di reazione dalla loudness (valori ogni 0.5 s). Ritorna [(t, forza)] con forza 1 o 2."""
    x = half_db
    z, mad = analyze.robust_z(x)
    zl = (x - analyze.rolling_median(x, 601)) / mad  # rispetto ai 5 minuti attorno
    zz = analyze.smooth(0.4 * z + 0.6 * zl, 2)
    t1, t2 = 2.6 / sens, 4.2 / sens
    out, last = [], -99.0
    i = 0
    while i < len(zz):
        if zz[i] >= t1:
            j = i
            while j < len(zz) and zz[j] >= t1 * 0.7:
                j += 1
            peak = i + int(np.argmax(zz[i:j]))
            t = peak * 0.5
            if t - last >= 3.5:
                out.append((max(0.0, i * 0.5 - 0.25), (j * 0.5), 2 if zz[peak] >= t2 else 1))
                last = t
            i = j
        else:
            i += 1
    return out  # [(inizio, fine, livello)]


def zoom_intervals(reacts, hold=2.2, max_len=6.0):
    iv = []
    for s, e, lv in reacts:
        e2 = min(s + max_len, max(e, s + hold))
        if iv and s <= iv[-1][1] + 0.6:  # unisci reazioni vicine
            ps, pe, plv = iv[-1]
            iv[-1] = (ps, max(pe, e2), max(plv, lv))
        else:
            iv.append((s, e2, lv))
    return iv


def build_pieces(keep, zooms, min_piece=0.35):
    """Interseca i tratti tenuti con gli intervalli di zoom -> [(src_in, src_out, livello)]."""
    pieces = []
    zi = 0
    for a, b in keep:
        cuts = [(a, 0)]
        for s, e, lv in zooms:
            if e <= a or s >= b:
                continue
            cuts.append((max(a, s), lv)); cuts.append((min(b, e), 0))
        cuts.sort(key=lambda c: c[0])
        bounds = []
        for i, (t, lv) in enumerate(cuts):
            nxt = cuts[i + 1][0] if i + 1 < len(cuts) else b
            if nxt - t > 0.02:
                bounds.append([t, nxt, lv])
        for p in bounds:  # pezzi troppo corti si fondono col precedente
            if pieces and p[1] - p[0] < min_piece and pieces[-1][1] == p[0]:
                pieces[-1][1] = p[1]
            else:
                pieces.append(p)
    return [tuple(p) for p in pieces]


# ------------------------------------------------------------------ inquadrature
def view(info, cam, level, zoom):
    """Regione della sorgente da mostrare a schermo intero per il livello: (x0, y0, w, h, scala)."""
    W, H = info["w"], info["h"]
    if level == 0 or not cam:
        return 0.0, 0.0, float(W), float(H), 1.0
    cx, cy = cam["x"] + cam["w"] / 2, cam["y"] + cam["h"] / 2
    if level == 1:
        s = zoom
    else:
        s = max(zoom, min(3.2, H / (cam["h"] * 1.08)))  # cam alta quasi quanto lo schermo
    w, h = W / s, H / s
    x0 = min(max(0.0, cx - w / 2), W - w)
    y0 = min(max(0.0, cy - h / 2), H - h)
    return x0, y0, w, h, s


def plan(path, info, cam, opts, progress=lambda f, m: None, log=print, stop=lambda: False):
    dur = info["dur"]
    pos, mind, pad = RITMO.get(opts.get("ritmo", "normale"), RITMO["normale"])
    progress(0.02, "Ascolto l'audio…")
    half = analyze.audio_energy(path, dur, lambda f: progress(0.02 + 0.5 * f, "Ascolto l'audio…"), stop)
    thr = auto_threshold(half, pos)
    log(f"Soglia silenzio: {thr:.1f} dB")
    progress(0.55, "Cerco i silenzi…")
    sil = silences(path, thr, mind, stop)
    keep = keep_segments(sil, dur, pad)
    if not keep:
        raise RuntimeError("Non ho trovato parlato: prova il ritmo 'tranquillo'")
    progress(0.8, "Cerco le tue reazioni…")
    reacts = reactions(half, float(opts.get("sens", 1.0))) if cam else []
    if not opts.get("full_cam", True):
        reacts = [(s, e, 1) for s, e, _ in reacts]
    zooms = zoom_intervals(reacts)
    pieces = build_pieces(keep, zooms)
    total = sum(b - a for a, b, _ in pieces)
    rep = {"durata_originale": round(dur, 1), "durata_finale": round(total, 1),
           "tagliato": round(dur - total, 1), "tagli": len(keep), "zoom": sum(1 for z in zooms if z[2] == 1),
           "cam_piena": sum(1 for z in zooms if z[2] == 2)}
    log(f"Montaggio: {media.tc(dur)} -> {media.tc(total)}, {rep['tagli']} tagli, "
        f"{rep['zoom']} zoom, {rep['cam_piena']} cam a tutto schermo")
    progress(0.9, "Piano di montaggio pronto")
    return {"pieces": pieces, "report": rep, "zoom": ZOOM.get(opts.get("zoom", "normale"), 1.32)}


# ------------------------------------------------------------------ render MP4
def render(path, info, cam, pl, out, progress=lambda f, m: None, stop=lambda: False, limit=None, preview=False):
    """Codifica ogni pezzo e li unisce. limit = secondi massimi (per l'anteprima)."""
    W, H = (960, 540) if preview else (info["w"] - info["w"] % 2, info["h"] - info["h"] % 2)
    fps = info["fps"]
    tmp = tempfile.mkdtemp(prefix="jarvis_ae_")
    pieces = pl["pieces"]
    if limit:
        sel, acc = [], 0.0
        first = next((i for i, p in enumerate(pieces) if p[2] > 0), 0)
        for p in pieces[max(0, first - 3):]:
            sel.append(p); acc += p[1] - p[0]
            if acc >= limit:
                break
        pieces = sel
    lst = []
    for i, (a, b, lv) in enumerate(pieces):
        if stop():
            raise RuntimeError("Annullato")
        x0, y0, w, h, s = view(info, cam, lv, pl["zoom"])
        vf = (f"crop={int(w) - int(w) % 2}:{int(h) - int(h) % 2}:{int(x0)}:{int(y0)},"
              f"scale={W}:{H}:flags=lanczos,setsar=1,fps={fps:.3f},format=yuv420p")
        seg = os.path.join(tmp, f"{i:05d}.ts")
        args = ["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", path, "-t", f"{b - a:.3f}",
                "-vf", vf, "-af", "aresample=48000,aformat=channel_layouts=stereo",
                "-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast",
                "-crf", "28" if preview else "18", "-c:a", "aac", "-b:a", "96k" if preview else "192k",
                "-f", "mpegts", seg]
        r = media.run(args)
        if r.returncode != 0:
            raise RuntimeError("Errore nel render: " + r.stderr[-300:])
        lst.append(seg)
        progress((i + 1) / len(pieces), f"Monto il video… {i + 1}/{len(pieces)}")
    listf = os.path.join(tmp, "list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for s in lst:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")
    r = media.run(["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listf,
                   "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", out])
    for s in lst:
        try:
            os.remove(s)
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError("Errore nell'unione: " + r.stderr[-300:])
    return out
