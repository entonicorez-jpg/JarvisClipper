"""Montaggio automatico per video orizzontali: taglia i silenzi e fa zoom sulla cam quando reagisci.

Modello modificabile dall'editor (tempi assoluti nel VOD, secondi):
  keep  = [[a, b], ...]                       tratti tenuti
  zooms = [{"id", "s", "e", "lv"}, ...]       lv 1 = zoom verso la cam, lv 2 = cam quasi a tutto schermo
Lo zoom entra con un movimento rapido e motion blur vero (supercampionamento + media dei fotogrammi).
"""
import os, re, tempfile
import numpy as np
from . import media, analyze

RITMO = {  # ripiego sul volume: posizione soglia (0-1), silenzio minimo s, margine s
    "tranquillo": (0.25, 1.0, 0.25),
    "normale": (0.35, 0.6, 0.15),
    "veloce": (0.45, 0.4, 0.08),
    "jumpcut": (0.5, 0.25, 0.05),
}
VAD = {  # rilevamento voce: silenzio minimo ms, margine ms, pezzo minimo s
    "tranquillo": (700, 250, 0.5),
    "normale": (380, 150, 0.35),
    "veloce": (220, 90, 0.25),
    "jumpcut": (110, 45, 0.18),
}
DENSITA = {  # dB sopra la tua voce normale per: zoom, cam piena; ogni quante clip uno zoom "a ritmo" (0 = mai)
    "pochi": (10.0, 14.0, 0),
    "normali": (7.0, 11.0, 3),
    "tanti": (5.5, 9.5, 2),
    "ogni_frase": (5.0, 9.0, 1),
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


def speech_segments(path, start, length, ritmo, progress=lambda f: None, stop=lambda: False):
    """Dove parli (Silero VAD, incluso in faster-whisper, gira offline). -> [(a, b)] in secondi locali."""
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    min_sil, pad, _ = VAD.get(ritmo, VAD["normale"])
    opts = VadOptions(threshold=0.45, min_silence_duration_ms=min_sil, speech_pad_ms=pad, min_speech_duration_ms=120)
    chunk = 600.0
    segs, t = [], 0.0
    while t < length - 0.05:
        if stop():
            raise RuntimeError("Annullato")
        d = min(chunk, length - t)
        r = media.subprocess.run([media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{start + t:.3f}",
                                  "-i", path, "-t", f"{d:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                                 capture_output=True, creationflags=media.CREATE_NO_WINDOW)
        audio = np.frombuffer(r.stdout, np.float32)
        if len(audio) > 1600:
            for sgm in get_speech_timestamps(audio, opts):
                a, b = t + sgm["start"] / 16000, t + sgm["end"] / 16000
                if segs and a - segs[-1][1] < min_sil / 1000:
                    segs[-1] = (segs[-1][0], b)
                else:
                    segs.append((a, b))
        t += d
        progress(min(1.0, t / length))
    return segs


def merge_segments(segs, gap=0.0, min_len=0.0):
    out = []
    for a, b in sorted(segs):
        if out and a <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return [(a, b) for a, b in out if b - a >= min_len]


def loud_regions(half_db, k=2.5):
    """Tratti molto forti (urla, risate) da non tagliare mai, anche se il VAD non li vede come voce."""
    z, mad = analyze.robust_z(half_db)
    idx = np.where(z > k)[0]
    return merge_segments([(i * 0.5 - 0.2, i * 0.5 + 0.7) for i in idx], 0.3)


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


def voice_z(half, keep, rs):
    """dB sopra il tuo parlato normale (livello della voce misurato nei ~10 minuti attorno, solo dove parli)."""
    mask = np.zeros(len(half), bool)
    for a, b in keep:
        mask[max(0, int((a - rs) * 2)):max(0, int((b - rs) * 2))] = True
    v = half[mask] if mask.sum() > 20 else half
    med = float(np.median(v))
    base = analyze.rolling_median(np.where(mask, half, med).astype(np.float32), 1201)
    return analyze.smooth(half - base, 2)


def auto_zooms(keep, half, rs, density="normali", full_cam=True):
    """Zoom agganciati alle clip (pezzi tenuti).
    - reazione forte -> cam piena con motion blur; reazione -> zoom con blur
    - con densità alta: zoom "a ritmo" (stacco netto) su una clip ogni N mentre parli, stile YouTube."""
    t1, t2, every = DENSITA.get(density, DENSITA["normali"])
    zz = voice_z(half, keep, rs)
    zooms, since = [], 0
    for a, b in keep:
        i0, i1 = int((a - rs) * 2), max(int((a - rs) * 2) + 1, int((b - rs) * 2))
        seg = zz[max(0, i0):min(len(zz), i1)]
        peak = float(seg.max()) if len(seg) else -9
        d = b - a
        lv, fx = 0, "blur"
        if peak >= t2:
            lv = 2 if full_cam else 1
        elif peak >= t1:
            lv = 1
        elif every and d >= 1.0 and since + 1 >= every:
            lv, fx = 1, "cut"
        if lv:
            prev = zooms[-1] if zooms else None
            if prev and abs(prev["e"] - a) < 0.05 and prev["lv"] == lv and prev["fx"] == fx and fx == "blur":
                prev["e"] = b  # reazione che continua nella clip dopo: stesso zoom
            else:
                zooms.append({"s": a, "e": b, "lv": lv, "fx": fx})
            since = 0
        else:
            since += 1
    for i, q in enumerate(zooms):
        q["id"] = i + 1; q["s"] = round(q["s"], 3); q["e"] = round(q["e"], 3)
    return zooms


def analyze_edit(path, info, cam, opts, half_full=None, progress=lambda f, m: None, log=print, stop=lambda: False):
    rs, re_ = opts.get("range") or (0.0, info["dur"])
    rs, re_ = max(0.0, float(rs)), min(info["dur"], float(re_))
    length = re_ - rs
    ritmo = opts.get("ritmo", "veloce")
    progress(0.02, "Ascolto l'audio…")
    if half_full is not None and len(half_full) >= int(re_ * 2) - 2:
        half = half_full[int(rs * 2):int(re_ * 2)]
    else:
        half = analyze.audio_energy(path, length, lambda f: progress(0.02 + 0.3 * f, "Ascolto l'audio…"), stop, rs)
    keep = None
    try:
        progress(0.35, "Capisco dove stai parlando…")
        sp = speech_segments(path, rs, length, ritmo, lambda f: progress(0.35 + 0.5 * f, "Capisco dove stai parlando…"), stop)
        _, _, min_piece = VAD.get(ritmo, VAD["normale"])
        segs = merge_segments(sp + loud_regions(half), 0.02, min_piece)
        keep = [[round(max(0.0, a) + rs, 3), round(min(length, b) + rs, 3)] for a, b in segs]
        log(f"Voce trovata in {len(sp)} tratti (rilevamento voce)")
    except Exception as e:
        if "Annullato" in str(e):
            raise
        log(f"Rilevamento voce non disponibile ({str(e)[:60]}), uso il volume")
    if not keep:
        pos, mind, pad = RITMO.get(ritmo, RITMO["normale"])
        thr = auto_threshold(half, pos)
        keep = [[round(a + rs, 3), round(b + rs, 3)] for a, b in keep_segments(silences(path, thr, mind, rs, length), length, pad)]
    if not keep:
        raise RuntimeError("Non ho trovato parlato in questa parte")
    spam, censor = [], []
    segs = None
    if opts.get("censor") or opts.get("nospam"):
        try:
            segs = transcribe_range(path, rs, length, opts, lambda f, m: progress(0.86 + 0.08 * f, m), log, stop)
        except Exception as e:
            if "Annullato" in str(e):
                raise
            log(f"Non riesco a capire il parlato ({str(e)[:80]}): niente bip né taglio spam questa volta")
    if segs is not None:
        from . import words as W
        if opts.get("nospam"):
            spam = W.find_spam(segs)
            _, _, min_piece = VAD.get(ritmo, VAD["normale"])
            keep = W.subtract(keep, spam, min_piece)
            log(f"Spam TikTok tagliato: {len(spam)} frasi")
        if opts.get("censor"):
            censor = W.censor_ranges([w for sg in segs for w in sg["words"]])
            log(f"Parolacce da censurare: {len(censor)}")
    progress(0.95, "Decido dove mettere gli zoom…")
    zooms = auto_zooms(keep, half, rs, opts.get("densita", "normali"), opts.get("full_cam", True)) if cam else []
    ed = {"keep": keep, "zooms": zooms, "range": [rs, re_], "zoom": ZOOM.get(opts.get("zoom", "normale"), 1.32),
          "blur": bool(opts.get("blur", True)), "out": opts.get("out", "blur"), "opts": opts,
          "spam": spam, "censor": censor}
    ed["report"] = report(ed)
    log(f"Montaggio: {media.tc(length)} -> {media.tc(ed['report']['durata_finale'])}, {len(keep)} pezzi, {len(zooms)} zoom")
    progress(1.0, "Pronto")
    return ed


def transcribe_range(path, rs, length, opts, progress, log, stop):
    """Trascrive tutta la parte scelta a blocchi di 5 minuti (tempi assoluti)."""
    tr = analyze.Transcriber(opts.get("model", "small"), opts.get("lang", "it"), log)
    progress(0.0, "Carico Whisper per capire cosa dici…")
    tr.load()
    segs, t, step = [], 0.0, 300.0
    while t < length - 0.1:
        if stop():
            raise RuntimeError("Annullato")
        d = min(step, length - t)
        progress(t / length, f"Ascolto cosa dici… {media.tc(t)} / {media.tc(length)}")
        segs += tr.transcribe(path, rs + t, d, 1)
        t += d
    return segs


def report(ed):
    rs, re_ = ed["range"]
    tot = sum(b - a for a, b in ed["keep"])
    return {"durata_originale": round(re_ - rs, 1), "durata_finale": round(tot, 1), "tagliato": round(re_ - rs - tot, 1),
            "tagli": len(ed["keep"]), "zoom": sum(1 for z in ed["zooms"] if z["lv"] == 1),
            "cam_piena": sum(1 for z in ed["zooms"] if z["lv"] == 2),
            "spam": len(ed.get("spam") or []), "bip": sum(1 for c in ed.get("censor") or [] if c.get("on", True))}


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
    """Spezza ogni pezzo zoomato in entrata animata / tenuta / uscita -> [(a, b, lv, kind)].
    Gli zoom con fx="cut" entrano ed escono con uno stacco netto (zoom a ritmo)."""
    zs = sorted(ed["zooms"], key=lambda z: z["s"])
    res = []
    for a, b, lv, zi, tin, tout in pieces:
        if lv == 0:
            res.append((a, b, 0, "static")); continue
        d = b - a
        anim = ed.get("blur", True) and zs[zi].get("fx", "blur") == "blur"
        ti = min(T_IN, d * 0.45) if (tin and anim) else 0
        to = min(T_OUT, d * 0.3) if (tout and ed.get("out", "blur") == "blur" and anim) else 0
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
def bleep_args(ranges, a, b, preview):
    """Argomenti ffmpeg per l'audio con i bip sulle parole censurate nel tratto [a, b]."""
    rel = [(max(0.0, c["s"] - a), min(b - a, c["e"] - a)) for c in ranges
           if c.get("on", True) and c["e"] > a and c["s"] < b]
    rel = [(x, y) for x, y in rel if y - x > 0.02]
    base = "aresample=48000,aformat=channel_layouts=stereo"
    if not rel:
        return [], ["-af", base], ["-map", "0:a?"]
    en = "+".join(f"between(t\\,{x:.3f}\\,{y:.3f})" for x, y in rel)
    fc = (f"[0:a]{base},volume=0:enable='{en}'[m];"
          f"[1:a]aformat=channel_layouts=stereo,volume=2.4,volume=0:enable='not({en})'[bp];"
          f"[m][bp]amix=inputs=2:normalize=0:duration=first[aout]")
    return (["-f", "lavfi", "-t", f"{b - a:.3f}", "-i", "sine=f=1000:sample_rate=48000"],
            ["-filter_complex", fc], ["-map", "[aout]"])


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
    extra_in, afilt, amap = bleep_args(ed.get("censor") or [], a, b, preview) if with_audio else ([], [], [])
    args = ["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", path] + extra_in
    args += ["-vf", vf, "-map", "0:v"]
    args += (afilt + amap + ["-c:a", "aac", "-b:a", "96k" if preview else "192k"]) if with_audio else ["-an"]
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
