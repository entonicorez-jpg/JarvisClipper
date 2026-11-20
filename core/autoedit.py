"""Montaggio automatico per video orizzontali: taglia i silenzi e fa zoom sulla cam quando reagisci.

Modello modificabile dall'editor (tempi assoluti nel VOD, secondi):
  keep  = [[a, b], ...]                       tratti tenuti
  zooms = [{"id", "s", "e", "lv"}, ...]       lv 1 = zoom verso la cam, lv 2 = cam quasi a tutto schermo
Lo zoom entra con un movimento rapido e motion blur vero (supercampionamento + media dei fotogrammi).
"""
import os, re, tempfile
import numpy as np
from . import media, analyze

RITMO = {  # posizione soglia tra rumore e voce (0-1), silenzio minimo s, margine s
    "tranquillo": (0.25, 1.0, 0.25),
    "normale": (0.35, 0.6, 0.15),
    "veloce": (0.45, 0.4, 0.08),
}
ZOOM = {"leggero": 1.18, "normale": 1.32, "forte": 1.5}
T_IN, T_OUT = 0.26, 0.18  # durata entrata/uscita dello zoom


# ------------------------------------------------------------------ analisi
def auto_threshold(half_db, pos):
    """Soglia del silenzio adattata al video: tra il rumore di fondo e il livello della voce."""
    x = half_db[half_db > -69]
    if len(x) < 20:
        return -35.0
    floor, voice = np.percentile(x, 10), np.percentile(x, 75)
    if voice - floor < 6:  # sempre rumoroso (musica/gioco forte): taglia solo i veri buchi
        return float(floor + 2)
    return float(np.clip(floor + pos * (voice - floor), -60, -22))


def silences(path, thr, mind, start=0.0, length=None):
    a, b = analyze._span(start, length)
    r = media.run(["-hide_banner", "-nostats"] + a + ["-i", path] + b + ["-vn", "-af",
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
    """Reazioni dalla loudness (valori ogni 0.5 s) -> [(inizio, fine, livello)] in secondi locali."""
    x = half_db
    z, mad = analyze.robust_z(x)
    zl = (x - analyze.rolling_median(x, 601)) / mad
    zz = analyze.smooth(0.4 * z + 0.6 * zl, 2)
    t1, t2 = 2.6 / sens, 4.2 / sens
    out, last, i = [], -99.0, 0
    while i < len(zz):
        if zz[i] >= t1:
            j = i
            while j < len(zz) and zz[j] >= t1 * 0.7:
                j += 1
            peak = i + int(np.argmax(zz[i:j]))
            if peak * 0.5 - last >= 3.5:
                out.append((max(0.0, i * 0.5 - 0.35), j * 0.5, 2 if zz[peak] >= t2 else 1))
                last = peak * 0.5
            i = j
        else:
            i += 1
    return out


def zoom_intervals(reacts, hold=2.4, max_len=6.0):
    iv = []
    for s, e, lv in reacts:
        e2 = min(s + max_len, max(e, s + hold))
        if iv and s <= iv[-1][1] + 0.6:
            ps, pe, plv = iv[-1]
            iv[-1] = (ps, max(pe, e2), max(plv, lv))
        else:
            iv.append((s, e2, lv))
    return iv


def analyze_edit(path, info, cam, opts, half_full=None, progress=lambda f, m: None, log=print, stop=lambda: False):
    rs, re_ = opts.get("range") or (0.0, info["dur"])
    rs, re_ = max(0.0, float(rs)), min(info["dur"], float(re_))
    length = re_ - rs
    pos, mind, pad = RITMO.get(opts.get("ritmo", "normale"), RITMO["normale"])
    progress(0.02, "Ascolto l'audio…")
    if half_full is not None and len(half_full) >= int(re_ * 2) - 2:
        half = half_full[int(rs * 2):int(re_ * 2)]
    else:
        half = analyze.audio_energy(path, length, lambda f: progress(0.02 + 0.5 * f, "Ascolto l'audio…"), stop, rs)
    thr = auto_threshold(half, pos)
    log(f"Soglia silenzio: {thr:.1f} dB")
    progress(0.55, "Cerco i silenzi…")
    keep = [[round(a + rs, 3), round(b + rs, 3)] for a, b in keep_segments(silences(path, thr, mind, rs, length), length, pad)]
    if not keep:
        raise RuntimeError("Non ho trovato parlato: prova il ritmo 'tranquillo'")
    progress(0.85, "Cerco le tue reazioni…")
    reacts = reactions(half, float(opts.get("sens", 1.0))) if cam else []
    if not opts.get("full_cam", True):
        reacts = [(s, e, 1) for s, e, _ in reacts]
    zooms = [{"id": i + 1, "s": round(s + rs, 3), "e": round(e + rs, 3), "lv": lv}
             for i, (s, e, lv) in enumerate(zoom_intervals(reacts))]
    ed = {"keep": keep, "zooms": zooms, "range": [rs, re_], "zoom": ZOOM.get(opts.get("zoom", "normale"), 1.32),
          "blur": bool(opts.get("blur", True)), "out": opts.get("out", "blur"), "opts": opts}
    ed["report"] = report(ed)
    log(f"Montaggio: {media.tc(length)} -> {media.tc(ed['report']['durata_finale'])}, {len(keep)} tagli, {len(zooms)} zoom")
    progress(1.0, "Pronto")
    return ed


def report(ed):
    rs, re_ = ed["range"]
    tot = sum(b - a for a, b in ed["keep"])
    return {"durata_originale": round(re_ - rs, 1), "durata_finale": round(tot, 1), "tagliato": round(re_ - rs - tot, 1),
            "tagli": len(ed["keep"]), "zoom": sum(1 for z in ed["zooms"] if z["lv"] == 1),
            "cam_piena": sum(1 for z in ed["zooms"] if z["lv"] == 2)}


# ------------------------------------------------------------------ pezzi
def build_pieces(keep, zooms):
    """-> [(a, b, lv, zi, tin, tout)]: tratti tenuti spezzati sugli zoom. tin/tout = entrata/uscita dello zoom zi."""
    zs = sorted(zooms, key=lambda z: z["s"])
    pieces = []
    for a, b in sorted(keep):
        cuts = {a, b}
        for z in zs:
            if z["e"] > a and z["s"] < b:
                cuts.add(max(a, z["s"])); cuts.add(min(b, z["e"]))
        pts = sorted(cuts)
        for x0, x1 in zip(pts, pts[1:]):
            if x1 - x0 < 0.04:
                continue
            mid = (x0 + x1) / 2
            zi = next((i for i, z in enumerate(zs) if z["s"] <= mid < z["e"]), -1)
            pieces.append([x0, x1, zs[zi]["lv"] if zi >= 0 else 0, zi])
    out = []
    for k, (a, b, lv, zi) in enumerate(pieces):
        tin = zi >= 0 and (k == 0 or pieces[k - 1][3] != zi)
        tout = zi >= 0 and (k == len(pieces) - 1 or pieces[k + 1][3] != zi)
        out.append((a, b, lv, zi, tin, tout))
    return out


def subpieces(pieces, ed):
    """Spezza ogni pezzo zoomato in entrata animata / tenuta / uscita -> [(a, b, lv, kind)]."""
    res = []
    for a, b, lv, zi, tin, tout in pieces:
        if lv == 0:
            res.append((a, b, 0, "static")); continue
        d = b - a
        ti = min(T_IN, d * 0.45) if (tin and ed.get("blur", True)) else 0
        to = min(T_OUT, d * 0.3) if (tout and ed.get("out", "blur") == "blur" and ed.get("blur", True)) else 0
        ti = ti if ti > 0.05 else 0
        to = to if to > 0.05 else 0
        if ti:
            res.append((a, a + ti, lv, "in"))
        res.append((a + ti, b - to, lv, "static"))
        if to:
            res.append((b - to, b, lv, "out"))
    return [r for r in res if r[1] - r[0] > 0.02]


# ------------------------------------------------------------------ inquadrature
def view(info, cam, level, zoom):
    """Regione della sorgente mostrata a schermo intero: (x0, y0, w, h, scala)."""
    W, H = info["w"], info["h"]
    if level == 0 or not cam:
        return 0.0, 0.0, float(W), float(H), 1.0
    cx, cy = cam["x"] + cam["w"] / 2, cam["y"] + cam["h"] / 2
    s = zoom if level == 1 else max(zoom, min(3.2, H / (cam["h"] * 1.08)))
    w, h = W / s, H / s
    x0 = min(max(0.0, cx - w / 2), W - w)
    y0 = min(max(0.0, cy - h / 2), H - h)
    return x0, y0, w, h, s


# ------------------------------------------------------------------ render
def _encode(path, info, cam, ed, a, b, lv, kind, out, W, H, preview, with_audio=True):
    fps = info["fps"]
    x0, y0, w, h, S = view(info, cam, lv, ed["zoom"])
    if kind == "static" or S <= 1.001:
        vf = (f"crop={int(w) - int(w) % 2}:{int(h) - int(h) % 2}:{int(x0)}:{int(y0)},"
              f"scale={W}:{H}:flags=lanczos,setsar=1,fps={fps:.4f},format=yuv420p")
    else:
        K = 4 if preview else 8                     # sotto-fotogrammi per fotogramma
        fk = fps * K
        n = max(2, int(round((b - a) * fk)))
        p = f"min(on/{n - 1}\\,1)"
        if kind == "in":   # entrata veloce che frena (ease-out)
            z = f"1+{S - 1:.5f}*(1-pow(1-{p}\\,3))"
        else:              # uscita che parte piano e accelera (ease-in)
            z = f"{S:.5f}-{S - 1:.5f}*pow({p}\\,2)"
        k = 1 - 1 / S
        x = f"{x0:.3f}*(1-1/zoom)/{k:.6f}"
        y = f"{y0:.3f}*(1-1/zoom)/{k:.6f}"
        vf = (f"fps={fk:.4f},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fk:.4f},"
              f"tmix=frames={K},select='not(mod(n\\,{K}))',setpts=N/({fps:.5f}*TB),setsar=1,format=yuv420p")
    args = ["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", path, "-t", f"{b - a:.3f}", "-vf", vf]
    args += (["-af", "aresample=48000,aformat=channel_layouts=stereo", "-c:a", "aac", "-b:a", "96k" if preview else "192k"]
             if with_audio else ["-an"])
    args += ["-r", f"{fps:.5f}", "-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast",
             "-crf", "26" if preview else "18", "-f", "mpegts" if out.endswith(".ts") else "mp4", out]
    r = media.run(args)
    if r.returncode != 0:
        raise RuntimeError("Errore nel render: " + r.stderr[-400:])
    return out


def render(path, info, cam, ed, out, progress=lambda f, m: None, stop=lambda: False, preview=False, window=None):
    """Video montato. window=(t0, t1) in tempo sorgente: renderizza solo quel tratto (anteprima)."""
    W, H = (960, 540) if preview else (info["w"] - info["w"] % 2, info["h"] - info["h"] % 2)
    keep = ed["keep"]
    if window:
        keep = [[max(a, window[0]), min(b, window[1])] for a, b in keep if b > window[0] and a < window[1]]
    subs = subpieces(build_pieces(keep, ed["zooms"]), ed)
    if not subs:
        raise RuntimeError("Non c'è niente da montare in questo tratto")
    tmp = tempfile.mkdtemp(prefix="jarvis_ae_")
    lst = []
    for i, (a, b, lv, kind) in enumerate(subs):
        if stop():
            raise RuntimeError("Annullato")
        lst.append(_encode(path, info, cam, ed, a, b, lv, kind, os.path.join(tmp, f"{i:05d}.ts"), W, H, preview))
        progress((i + 1) / len(subs), f"Monto il video… {i + 1}/{len(subs)}")
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


def render_fx(path, info, cam, ed, out_dir, progress=lambda f, m: None, stop=lambda: False):
    """Clip brevi con l'entrata/uscita dello zoom (motion blur) per la traccia V2 di Premiere.
    -> {(a, b): percorso} con i tempi sorgente del sotto-pezzo."""
    os.makedirs(out_dir, exist_ok=True)
    W, H = info["w"] - info["w"] % 2, info["h"] - info["h"] % 2
    subs = [s for s in subpieces(build_pieces(ed["keep"], ed["zooms"]), ed) if s[3] != "static"]
    res = {}
    for i, (a, b, lv, kind) in enumerate(subs):
        if stop():
            raise RuntimeError("Annullato")
        fp = os.path.join(out_dir, f"zoom_{kind}_{i + 1:03d}.mp4")
        _encode(path, info, cam, ed, a, b, lv, kind, fp, W, H, False, with_audio=False)
        res[(round(a, 3), round(b, 3))] = fp
        progress((i + 1) / max(1, len(subs)), f"Preparo gli zoom con motion blur per Premiere… {i + 1}/{len(subs)}")
    return res
