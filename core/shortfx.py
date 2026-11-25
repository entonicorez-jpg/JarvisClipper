"""Montaggio degli short verticali: zoom su di te (entra veloce col motion blur, poi dezooma piano fino
alla clip normale), titolo TikTok a tempo e sottotitoli Jarvis (stili come in FilmCraft, font Provicali).

Ordine dei livelli, come in Premiere: video (cam + gioco) → sottotitoli → ZOOM → titolo.
Quindi lo zoom ingrandisce anche i sottotitoli; il titolo resta fermo sopra (a meno di "title_zoom").

I tempi (zoom, titolo) sono in secondi del VOD, come inizio/fine della clip."""
import glob
import math
import os
import subprocess

import numpy as np

from . import media, render as vrender

# ------------------------------------------------------------------ impostazioni
DEFAULTS = {
    "zoom_on": True, "zoom": 1.3, "zoom_len": 2.4,
    "title_on": True, "title_size": 82, "title_y": None, "title_upper": False, "title_zoom": False, "title_len": 4.0,
    "caps_on": True, "style": "jarvis", "font": "Provicali", "words": 0, "upper": None, "anim": "pop",
    "cap_y": 0.66, "cap_size": 100, "censor": True, "tilt": False,
}

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
YELLOW, GREEN, RED, CYAN, PURPLE = (255, 222, 0), (56, 255, 89), (255, 46, 56), (51, 217, 255), (168, 107, 255)
# id: (nome, font, riempimento, colori evidenziazione, scala evidenziata, bordo, spessore bordo, ombra, riquadro, parole, maiuscolo, pop)
STYLES = {
    "jarvis": ("Jarvis (giallo)", "Arial Black", WHITE, [YELLOW], 1.0, BLACK, 0.13, True, None, 3, True, True),
    "hormozi": ("Hormozi (verde)", "Arial Black", WHITE, [GREEN], 1.15, BLACK, 0.14, True, None, 3, True, True),
    "parola": ("Una parola alla volta", "Arial Black", WHITE, [], 1.0, BLACK, 0.14, True, None, 1, True, True),
    "beast": ("Beast (giallo + rosso)", "Arial Black", YELLOW, [RED], 1.1, BLACK, 0.16, True, None, 2, True, True),
    "box": ("Riquadro bianco", "Arial", BLACK, [RED], 1.0, None, 0.0, False, WHITE, 3, False, True),
    "neon": ("Neon viola", "Arial Black", WHITE, [PURPLE], 1.08, (41, 0, 77), 0.12, True, None, 3, True, True),
    "minimal": ("Minimal", "Inter", WHITE, [], 1.0, None, 0.0, True, None, 5, False, False),
}
ANIMS = [("pop_osc", "Pop + oscillazione"), ("pop", "Pop"), ("pop_su", "Pop dal basso (salta su)"),
         ("rimbalzo", "Rimbalzo dal basso"), ("scivola", "Scivola su"), ("tremolio", "Pop + tremolio continuo"),
         ("zoom", "Zoom lento"), ("nessuna", "Nessuna")]


def settings(c):
    """c = config di Jarvis Clipper: lo stile degli short sta in c["short_fx"]."""
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (c.get("short_fx") or {}).items() if k in DEFAULTS})
    return s


def cam_center(layout, W=vrender.OUT_W, H=vrender.OUT_H):
    """Dove sei tu nello short (centro dello zoom)."""
    cam_h, _ = vrender.heights(layout or {}, H)
    return (W / 2.0, cam_h / 2.0) if cam_h else (W / 2.0, H * 0.42)


# ------------------------------------------------------------------ zoom automatici
def auto_zooms(c, half, st):
    """Zoom sulle tue reazioni dentro la clip (loudness ogni 0.5 s del VOD intero)."""
    a, b = float(c["start"]), float(c["end"])
    Z, L = float(st["zoom"]), float(st["zoom_len"])
    picks = []
    if half is not None and len(half) > int(b * 2):
        from . import autoedit
        seg = np.asarray(half[int(a * 2):int(b * 2)], np.float32)
        if len(seg) > 8:
            try:
                for s, e, lv in autoedit.reactions(seg, 1.15):
                    picks.append((a + s + 0.15, lv))
            except Exception:
                pass
    pk = c.get("peak")
    if pk is not None and a + 0.5 < float(pk) < b - 0.8 and all(abs(t - float(pk)) > 2.0 for t, _ in picks):
        picks.append((float(pk) - 0.2, 2))
    picks.sort()
    out, last = [], -99.0
    nmax = max(1, int(math.ceil((b - a) / 7.0)))
    for t, lv in sorted(picks, key=lambda q: -q[1]):
        if len(out) >= nmax:
            break
        if any(abs(t - o["s"]) < L + 1.0 for o in out) or t < a or t > b - 0.6:
            continue
        out.append({"s": round(t, 2), "e": round(min(b, t + L), 2), "z": round(Z + (0.08 if lv >= 2 else 0), 2)})
    out.sort(key=lambda o: o["s"])
    for i, o in enumerate(out):
        o["id"] = i + 1
    return out


def default_fx(c, half, st):
    a, b = float(c["start"]), float(c["end"])
    text = (c.get("hook") or c.get("title") or "").strip()
    if text.startswith("Momento a "):
        text = ""
    return {"zooms": auto_zooms(c, half, st) if st["zoom_on"] else [],
            "title": {"on": bool(st["title_on"] and text), "text": text, "s": round(a, 2), "e": round(min(b, a + st["title_len"]), 2)},
            "caps": bool(st["caps_on"])}


def _ease_out3(p):
    return 1 - (1 - p) ** 3


def _ease_io(p):
    return 0.5 - 0.5 * math.cos(math.pi * p)


def zoom_at(zooms, t):
    """Scala dello zoom al tempo t (secondi VOD): entra in 0.16 s, tiene un attimo, poi dezooma piano."""
    best = 1.0
    for z in zooms:
        s, e, Z = float(z["s"]), float(z["e"]), float(z.get("z", 1.3))
        if not (s <= t < e) or Z <= 1.0:
            continue
        d = e - s
        tin, hold = min(0.16, d * 0.2), min(0.35, d * 0.2)
        u = t - s
        if u < tin:
            v = 1 + (Z - 1) * _ease_out3(u / tin)
        elif u < tin + hold:
            v = Z
        else:
            out = max(1e-3, d - tin - hold)
            v = Z - (Z - 1) * _ease_io(min(1.0, (u - tin - hold) / out))
        best = max(best, v)
    return best


def zoom_fast(zooms, t):
    """Siamo nell'entrata veloce di uno zoom (serve il motion blur)?"""
    for z in zooms:
        s, e = float(z["s"]), float(z["e"])
        if s - 0.02 <= t < s + min(0.16, (e - s) * 0.2) + 0.02:
            return True
    return False


# ------------------------------------------------------------------ font
_FONTS = None


def _font_dirs():
    out = []
    win = os.environ.get("WINDIR") or "C:\\Windows"
    out.append(os.path.join(win, "Fonts"))
    la = os.environ.get("LOCALAPPDATA")
    if la:
        out.append(os.path.join(la, "Microsoft", "Windows", "Fonts"))
    out += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts")]
    return [d for d in out if os.path.isdir(d)]


def _font_index():
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    from PIL import ImageFont
    idx = {}
    for d in _font_dirs():
        for f in glob.glob(os.path.join(d, "**", "*"), recursive=True):
            if not f.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            try:
                fam, sty = ImageFont.truetype(f, 12).getname()
            except Exception:
                continue
            key = (fam or "").lower().replace(" ", "")
            idx.setdefault(key, []).append((sty or "", f))
            # anche dal nome del file (es. "Provicali.otf")
            idx.setdefault(os.path.splitext(os.path.basename(f))[0].lower().replace(" ", "").replace("-regular", ""), []).append((sty or "", f))
    _FONTS = idx
    return idx


def font_path(family):
    if not family:
        return None
    ls = _font_index().get(family.lower().replace(" ", ""))
    if not ls:
        return None
    for want in ("regular", "bold", "black", ""):
        for sty, f in ls:
            if want in sty.lower():
                return f
    return ls[0][1]


def fonts_available(names):
    return {n: bool(font_path(n)) for n in names}


def _load_font(family, style_font, size):
    from PIL import ImageFont
    for fam in (family, style_font, "Arial Black", "Arial"):
        p = font_path(fam)
        if p:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    from . import titles
    return titles._font(size, 850)


# ------------------------------------------------------------------ sottotitoli
def _clean(w, upper):
    t = w.strip().strip(".,;:\"«»…")
    return t.upper() if upper else t


def _mask(w):
    idx = [i for i, ch in enumerate(w) if ch.isalnum()]
    if len(idx) < 3:
        return w
    a, b = idx[0], idx[-1]
    return "".join("*" if a < i < b and ch.isalnum() else ch for i, ch in enumerate(w))


def _groups(words, n, max_chars):
    out, cur, chars = [], [], 0
    for i, w in enumerate(words):
        ln = len(w["w"].strip())
        gap = bool(cur) and w["s"] - words[cur[-1]]["e"] > 0.45
        if cur and (len(cur) >= n or chars + 1 + ln > max_chars or gap):
            out.append(cur); cur, chars = [], 0
        chars += ln + (1 if cur else 0)
        cur.append(i)
        if n > 1 and w["w"].rstrip().endswith((".", "?", "!")):
            out.append(cur); cur, chars = [], 0
    if cur:
        out.append(cur)
    return out


def caption_pieces(words, st, W, H, a, b):
    """Pezzi dei sottotitoli: [(inizio, fine, [parole], indice evidenziata o -1, colore, entrata)]."""
    sty = STYLES.get(st["style"], STYLES["jarvis"])
    words = [w for w in words if a - 0.05 <= float(w["s"]) < b and w.get("w", "").strip()]
    if not words:
        return []
    n = int(st.get("words") or 0) or sty[9]
    n = max(1, min(8, n))
    upper = sty[10] if st.get("upper") is None else bool(st["upper"])
    size = min(W, H) * 0.085 * float(st.get("cap_size", 100)) / 100.0
    max_chars = 40 if n == 1 else max(8, int((W * 0.86) / (size * 0.72)))
    from . import words as WD
    texts = []
    for w in words:
        t = _clean(w["w"], upper)
        texts.append(_mask(t) if st.get("censor", True) and WD.is_swear(t) else t)
    hold, bridge = (0.12 if n == 1 else 0.25), 0.3
    gs = _groups(words, n, max_chars)
    hl = sty[3]
    pieces, ci = [], 0
    for gi, g in enumerate(gs):
        gstart = words[g[0]]["s"]
        nxt = words[gs[gi + 1][0]]["s"] if gi + 1 < len(gs) else None
        gend_max = words[g[-1]]["e"] + hold
        gend = nxt if (nxt is not None and nxt <= gend_max + bridge) else gend_max
        parts = [texts[i] for i in g]
        for k, i in enumerate(g):
            s0 = gstart if k == 0 else words[i]["s"]
            e0 = words[g[k + 1]]["s"] if k + 1 < len(g) else gend
            if n == 1:
                end = words[i]["e"] + hold
                e0 = nxt if (nxt is not None and nxt <= end + bridge) else min(end, e0)
            if not hl:
                pieces.append((s0, gend, parts, -1, None, True))
                break
            col = hl[ci % len(hl)]
            if n == 1:
                ci += 1
            pieces.append((s0, e0, parts, k, col, k == 0))
        if n > 1:
            ci += 1
    out, last = [], -1e9
    for s0, e0, parts, k, col, ent in pieces:
        s0 = max(s0, last, a)
        e0 = min(e0, b)
        if e0 - s0 > 0.02:
            out.append((s0, e0, parts, k, col, ent))
            last = e0
    return out


def _sprite(parts, k, col, st, W, H, font_cache):
    """Disegna un sottotitolo: immagine RGBA premoltiplicata (numpy) + centro."""
    from PIL import Image, ImageDraw, ImageFilter
    sty = STYLES.get(st["style"], STYLES["jarvis"])
    size = int(min(W, H) * 0.085 * float(st.get("cap_size", 100)) / 100.0)
    key = (size, "n")
    if key not in font_cache:
        font_cache[key] = _load_font(st.get("font") or "Provicali", sty[1], size)
    font = font_cache[key]
    hsize = int(size * sty[4])
    if k >= 0 and hsize != size:
        kk = (hsize, "h")
        if kk not in font_cache:
            font_cache[kk] = _load_font(st.get("font") or "Provicali", sty[1], hsize)
    fh = font_cache.get((hsize, "h"), font)
    stroke = int(round(size * sty[6])) if sty[5] else 0
    gap = font.getlength(" ")
    runs = [(t, (col if i == k and col else sty[2]), fh if (i == k and col) else font) for i, t in enumerate(parts)]
    widths = [f.getlength(t) for t, _, f in runs]
    tw = sum(widths) + gap * (len(runs) - 1)
    # se è troppo largo va a capo (al massimo 2 righe)
    lines = [list(range(len(runs)))]
    if tw > W * 0.9 and len(runs) > 1:
        acc, cut = 0, 1
        for i, w in enumerate(widths):
            acc += w + gap
            if acc > tw / 2:
                cut = max(1, i)
                break
        lines = [list(range(cut)), list(range(cut, len(runs)))]
    lh = int(size * 1.15)
    pad = stroke + int(size * 0.5)
    lws = [sum(widths[i] for i in ln) + gap * (len(ln) - 1) for ln in lines]
    iw = int(max(lws) + pad * 2)
    ih = int(lh * len(lines) + pad * 2)
    img = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    if sty[8]:
        d = ImageDraw.Draw(img)
        bp, rad = int(size * 0.28), int(size * 0.22)
        for li, ln in enumerate(lines):
            x0 = (iw - lws[li]) / 2 - bp
            y0 = pad + li * lh - bp * 0.4
            d.rounded_rectangle((x0, y0, x0 + lws[li] + 2 * bp, y0 + lh + bp * 0.8), rad, fill=sty[8] + (255,))
    txt = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    for li, ln in enumerate(lines):
        x = (iw - lws[li]) / 2
        cy = pad + li * lh + lh / 2
        for j, i in enumerate(ln):
            t, c, f = runs[i]
            d.text((x, cy), t, font=f, fill=c + (255,), anchor="lm",
                   stroke_width=stroke, stroke_fill=(sty[5] + (255,)) if sty[5] else None)
            x += widths[i] + gap
    if sty[7]:
        a = txt.split()[3].point(lambda v: int(v * 0.7))
        sh = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
        sh.putalpha(a)
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, size * 0.09)))
        off = int(size * 0.06)
        img.alpha_composite(sh, (off, off))
    img.alpha_composite(txt)
    return _premul(img), (iw / 2.0, ih / 2.0)


def _premul(img):
    a = np.asarray(img, np.float32)
    rgb = a[..., :3][..., ::-1] * (a[..., 3:4] / 255.0)  # RGB -> BGR
    return np.concatenate([rgb, a[..., 3:4]], axis=2)


def _keys(ks, f):
    """ks = [(fotogramma, valore, curva)] a 30 fps; f = fotogramma (float)."""
    if f <= ks[0][0]:
        return ks[0][1]
    for (f0, v0, c), (f1, v1, _) in zip(ks, ks[1:]):
        if f < f1:
            p = (f - f0) / max(1e-6, f1 - f0)
            p = 1 - (1 - p) ** 2 if c == "O" else (p * p * (3 - 2 * p) if c == "S" else p)
            return v0 + (v1 - v0) * p
    return ks[-1][1]


def anim_state(anim, entrance, u, dur, tilt):
    """-> (scala, rotazione°, spostamento y in 'size', opacità) dopo u secondi dall'inizio del pezzo."""
    f = u * 30.0
    sc, rot, dy, op = 1.0, tilt, 0.0, 1.0
    if anim == "pop" and entrance:
        sc = _keys([(0, .55, "O"), (3, 1.12, "O"), (6, 1.0, "O")], f)
    elif anim == "pop_osc" and entrance:
        sc = _keys([(0, .45, "O"), (3, 1.16, "S"), (6, .95, "S"), (8, 1.02, "S"), (10, 1.0, "L")], f)
        rot = _keys([(0, tilt - 9, "S"), (3, tilt + 6, "S"), (6, tilt - 3.5, "S"), (9, tilt + 1.5, "S"), (12, tilt, "L")], f)
    elif anim == "pop_su" and entrance:
        sc = _keys([(0, .4, "O"), (4, 1.12, "S"), (7, .97, "S"), (9, 1.0, "L")], f)
        dy = _keys([(0, .7, "O"), (4, -.1, "S"), (7, .03, "S"), (9, 0, "L")], f)
        op = _keys([(0, 0, "O"), (2, 1, "L")], f)
    elif anim == "rimbalzo" and entrance:
        dy = _keys([(0, .55, "O"), (4, -.12, "S"), (7, .04, "S"), (9, 0, "L")], f)
        op = _keys([(0, 0, "O"), (3, 1, "L")], f)
    elif anim == "scivola" and entrance:
        dy = _keys([(0, .4, "O"), (5, 0, "L")], f)
        op = _keys([(0, 0, "O"), (4, 1, "L")], f)
    elif anim == "tremolio":
        if entrance:
            sc = _keys([(0, .55, "O"), (3, 1.12, "O"), (6, 1.0, "O")], f)
        ph = (f % 8) / 8.0
        rot = tilt + 2.5 * math.cos(ph * 2 * math.pi)
    elif anim == "zoom":
        frames = max(1.0, dur * 30)
        sc = (0.9 if entrance else 1.0) + ((1.08) - (0.9 if entrance else 1.0)) * min(1.0, f / frames)
    return sc, rot, dy, op


def _blend(frame, spr, center, pos, sc=1.0, rot=0.0, op=1.0):
    """Incolla lo sprite premoltiplicato (con scala/rotazione/opacità) sul fotogramma BGR uint8."""
    import cv2
    H, W = frame.shape[:2]
    if op <= 0.004 or sc <= 0.01:
        return
    M = cv2.getRotationMatrix2D(center, -rot, sc)
    M[0, 2] += pos[0] - center[0]
    M[1, 2] += pos[1] - center[1]
    h, w = spr.shape[:2]
    pts = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float32) @ M.T
    x0, y0 = max(0, int(pts[:, 0].min()) - 1), max(0, int(pts[:, 1].min()) - 1)
    x1, y1 = min(W, int(pts[:, 0].max()) + 2), min(H, int(pts[:, 1].max()) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    M2 = M.copy()
    M2[0, 2] -= x0
    M2[1, 2] -= y0
    roi = cv2.warpAffine(spr, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    hh, ww = roi.shape[:2]
    if hh <= 0 or ww <= 0:
        return
    dst = frame[y0:y0 + hh, x0:x0 + ww].astype(np.float32)
    a = roi[..., 3:4] * (op / 255.0)
    dst = dst * (1 - a) + roi[..., :3] * op
    frame[y0:y0 + hh, x0:x0 + ww] = np.clip(dst, 0, 255).astype(np.uint8)


def _zoom_frame(frame, z, cx, cy):
    import cv2
    H, W = frame.shape[:2]
    M = np.array([[z, 0, cx * (1 - z)], [0, z, cy * (1 - z)]], np.float32)
    return cv2.warpAffine(frame, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


# ------------------------------------------------------------------ titolo
def title_sprite(tt, st, layout, W, H):
    """PNG del titolo TikTok (testo bianco, bordo nero, emoji iPhone) -> (sprite premoltiplicato, centro, posizione)."""
    import tempfile
    from PIL import Image
    from . import titles
    try:
        titles.ensure_emoji()
    except Exception:
        pass
    y = st.get("title_y")
    if y is None:
        cam_h, _ = vrender.heights(layout or {}, 1920)
        y = (cam_h / 1920.0) if cam_h else 0.2
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        titles.render({"text": tt.get("text", ""), "w": W, "h": H, "size": st.get("title_size", 82),
                       "y": float(y), "upper": bool(st.get("title_upper")), "stroke": 11}, tmp)
        img = Image.open(tmp).convert("RGBA")
        img.load()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    bb = img.getbbox()
    if not bb:
        return None
    crop = img.crop(bb)
    c = (crop.width / 2.0, crop.height / 2.0)
    return _premul(crop), c, ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)


def title_anim(u, left):
    """Pop all'entrata, piccolo rimpicciolimento in uscita."""
    f = u * 30.0
    sc = _keys([(0, .5, "O"), (4, 1.08, "S"), (7, .98, "S"), (9, 1.0, "L")], f)
    op = _keys([(0, 0, "O"), (2, 1, "L")], f)
    if left < 0.12:
        k = max(0.0, left / 0.12)
        op *= k
        sc *= 0.9 + 0.1 * k
    return sc, op


# ------------------------------------------------------------------ render
def needs_fx(fx, st, words):
    if not fx:
        return False
    return bool(fx.get("zooms")) or bool((fx.get("title") or {}).get("on") and (fx.get("title") or {}).get("text")) or bool(fx.get("caps") and words)


def _remap_audio(extra_in, afilt, amap):
    af = []
    for x in afilt:
        af.append(x.replace("[1:a]", "[§2:a]").replace("[0:a]", "[1:a]").replace("[§2:a]", "[2:a]"))
    am = [x.replace("0:a?", "1:a?") for x in amap]
    return extra_in, af, am


def render(path, info, layout, start, end, out, fx, st, words=None, preview=False, censor=None,
           progress=lambda f: None, stop=lambda: False):
    import cv2
    W, H = (540, 960) if preview else (vrender.OUT_W, vrender.OUT_H)
    fps_src, _, _ = media.edit_rate(info)
    fps = 30.0 if preview else (fps_src if fps_src <= 61 else 60.0)
    dur = end - start
    nfr = max(1, int(round(dur * fps)))
    fg = vrender.filter_graph(info, layout, W, H) + f";[v]fps={fps:.5f},format=bgr24[vo]"
    dec = media.popen(["-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
                       "-filter_complex", fg, "-map", "[vo]", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * H * 3 * 2)
    from .autoedit import bleep_args
    extra_in, afilt, amap = _remap_audio(*bleep_args(censor or [], start, end, preview))
    args = ["-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
            "-r", f"{fps:.5f}", "-i", "-", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path] + extra_in
    args += afilt + ["-map", "0:v"] + amap
    args += (media.venc("preview") + ["-c:a", "aac", "-b:a", "96k"]) if preview else (media.venc("high") + ["-c:a", "aac", "-b:a", "192k"])
    args += ["-shortest", "-movflags", "+faststart", out]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    enc = media.popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    k = W / float(vrender.OUT_W)
    cx, cy = cam_center(layout, W, H)
    zooms = [z for z in (fx.get("zooms") or []) if float(z["e"]) > start and float(z["s"]) < end]
    # sottotitoli
    pieces = caption_pieces(words or [], st, W, H, start, end) if fx.get("caps") else []
    fcache, scache = {}, {}
    cpos = (W * 0.5, H * float(st.get("cap_y", 0.66)))
    csize = min(W, H) * 0.085 * float(st.get("cap_size", 100)) / 100.0
    tilt = 0.0
    anim = st.get("anim") or "pop"
    if not STYLES.get(st["style"], STYLES["jarvis"])[11] and anim in ("pop", "pop_osc"):
        anim = "nessuna"
    # titolo
    tt = fx.get("title") or {}
    tspr = title_sprite(tt, st, layout, W, H) if (tt.get("on") and tt.get("text", "").strip()) else None
    ts, te = float(tt.get("s", start)), float(tt.get("e", end))
    tz = bool(st.get("title_zoom"))
    sub = 4 if preview else 6
    pi = 0
    err = None
    try:
        for i in range(nfr):
            if stop():
                raise RuntimeError("Annullato")
            buf = dec.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                if i == 0:
                    raise RuntimeError("Non riesco a leggere il video della clip")
                buf = last if len(buf) == 0 else (buf + bytes(W * H * 3 - len(buf)))
            last = buf
            fr = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            t = start + i / fps

            def overlay_caps(img):
                nonlocal pi
                while pi < len(pieces) and pieces[pi][1] <= t:
                    pi += 1
                if pi < len(pieces) and pieces[pi][0] <= t < pieces[pi][1]:
                    s0, e0, parts, kk, col, ent = pieces[pi]
                    key = pi
                    if key not in scache:
                        scache.clear()
                        scache[key] = _sprite(parts, kk, col, st, W, H, fcache)
                    spr, c0 = scache[key]
                    # l'entrata vale per la prima parola del gruppo: misurata dall'inizio del gruppo
                    g0 = s0
                    if not ent:
                        j = pi
                        while j > 0 and not pieces[j][5]:
                            j -= 1
                        g0 = pieces[j][0]
                        ent2 = pieces[j][5] and (t - g0) < 0.4
                    else:
                        ent2 = True
                    sc, rot, dy, op = anim_state(anim, ent2, t - g0, e0 - g0, tilt)
                    _blend(img, spr, c0, (cpos[0], cpos[1] + dy * csize), sc, rot, op)

            def overlay_title(img):
                if tspr and ts <= t < te:
                    sc, op = title_anim(t - ts, te - t)
                    _blend(img, tspr[0], tspr[1], tspr[2], sc, 0.0, op)

            if pieces:
                overlay_caps(fr)
            if tz:
                overlay_title(fr)
            z = zoom_at(zooms, t)
            if z > 1.0005:
                if zoom_fast(zooms, t):
                    # motion blur: media di più istanti dentro il fotogramma (otturatore a 180°)
                    acc = np.zeros(fr.shape, np.float32)
                    for s_ in range(sub):
                        zt = zoom_at(zooms, t + (s_ / sub - 0.5) * 0.5 / fps)
                        acc += _zoom_frame(fr, max(1.0, zt), cx, cy)
                    fr = (acc / sub).astype(np.uint8)
                else:
                    fr = _zoom_frame(fr, z, cx, cy)
            if not tz:
                overlay_title(fr)
            enc.stdin.write(fr.tobytes())
            if i % 15 == 0:
                progress(i / nfr)
    except (BrokenPipeError, OSError) as e:
        err = e
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        try:
            dec.stdout.close()
        except Exception:
            pass
        dec.kill()
    rc = enc.wait()
    msg = enc.stderr.read().decode("utf-8", "replace") if enc.stderr else ""
    if rc != 0 or not os.path.exists(out):
        raise RuntimeError("Render dello short fallito: " + (msg[-500:] or str(err)))
    return out
