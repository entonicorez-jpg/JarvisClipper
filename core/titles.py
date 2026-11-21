"""Titoli TikTok per FilmCraft Jarvis: testo bianco con bordo nero (font TikTok Sans) e le emoji
dell'iPhone a colori, in un PNG trasparente grande come la sequenza.

- `JarvisClipper.exe --emoji-setup out.json`: scarica (una volta) le emoji dell'iPhone e prepara le
  immagini per la griglia di FilmCraft in %LOCALAPPDATA%\\JarvisClipper\\titoli.
- `JarvisClipper.exe --tiktok-title job.json out.png`: disegna il titolo.

Le emoji non sono nel programma (sono di Apple): si scaricano dal pacchetto pubblico
emoji-datasource-apple, come faceva il generatore web."""
import io
import json
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(BASE, "assets", "titoli")
CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "titoli")
SHEET_URLS = [
    "https://cdn.jsdelivr.net/npm/emoji-datasource-apple@{v}/img/apple/sheets-clean/64.png",
    "https://unpkg.com/emoji-datasource-apple@{v}/img/apple/sheets-clean/64.png",
]


def index():
    with open(os.path.join(RES, "emoji.json"), encoding="utf-8") as f:
        return json.load(f)


def _progress(out, f, msg):
    if not out:
        return
    try:
        with open(out + ".progress", "w", encoding="utf-8") as fh:
            fh.write(f"{f:.3f} {msg}")
    except Exception:
        pass


def ready():
    return all(os.path.exists(os.path.join(CACHE, n)) for n in ("emoji64.png", "emoji40.png", "emoji.json"))


def ensure_emoji(out=None, sheet_path=None):
    """Prepara le immagini delle emoji nella cache (scarica il foglio la prima volta)."""
    if ready():
        return True
    os.makedirs(CACHE, exist_ok=True)
    idx = index()
    if sheet_path:
        sheet = Image.open(sheet_path).convert("RGBA")
    else:
        data, last = None, None
        for u in SHEET_URLS:
            try:
                _progress(out, 0.05, "Scarico le emoji dell'iPhone (solo la prima volta)…")
                req = urllib.request.Request(u.format(v=idx["version"]), headers={"User-Agent": "JarvisClipper"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                break
            except Exception as e:
                last = e
        if data is None:
            raise RuntimeError(f"Non riesco a scaricare le emoji: {last}")
        sheet = Image.open(io.BytesIO(data)).convert("RGBA")
    _progress(out, 0.6, "Preparo le emoji…")
    cell, pad, S, P, cols = idx["cell"], idx["pad"], idx["size"], idx["psize"], idx["cols"]
    items = idx["items"]
    rows = (len(items) + cols - 1) // cols
    big = Image.new("RGBA", (cols * S, rows * S), (0, 0, 0, 0))
    small = Image.new("RGBA", (cols * P, rows * P), (0, 0, 0, 0))
    for i, e in enumerate(items):
        x0, y0 = e["x"] * cell + pad, e["y"] * cell + pad
        im = sheet.crop((x0, y0, x0 + S, y0 + S))
        gx, gy = i % cols, i // cols
        big.paste(im, (gx * S, gy * S))
        small.paste(im.resize((P, P), Image.LANCZOS), (gx * P, gy * P))
    big.save(os.path.join(CACHE, "emoji64.png"))
    small.save(os.path.join(CACHE, "emoji40.png"))
    with open(os.path.join(CACHE, "emoji.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    _progress(out, 1.0, "Emoji pronte")
    return True


# ---------------------------------------------------------------- disegno del titolo
def _tokens(text, idx):
    """Spezza il testo in pezzi: ("t", testo) oppure ("e", numero dell'emoji)."""
    look = {}
    for i, e in enumerate(idx["items"]):
        for v in e["u"]:
            look[v] = i
            look.setdefault(v.replace("️", ""), i)
    maxlen = max(len(k) for k in look)
    out, buf, i = [], "", 0
    while i < len(text):
        hit = None
        for n in range(min(maxlen, len(text) - i), 0, -1):
            s = text[i:i + n]
            if s in look and not (n == 1 and s.isascii()):
                hit = (n, look[s])
                break
        if hit:
            if buf:
                out.append(("t", buf))
                buf = ""
            out.append(("e", hit[1]))
            i += hit[0]
            # variation selector che segue
            while i < len(text) and text[i] in "️‍":
                i += 1
        else:
            if text[i] != "️":
                buf += text[i]
            i += 1
    if buf:
        out.append(("t", buf))
    return out


def _font(size, weight):
    f = ImageFont.truetype(os.path.join(RES, "TikTokSans.ttf"), size)
    try:
        axes = f.get_variation_axes()
        vals = []
        for a in axes:
            name = (a.get("name") or b"").decode() if isinstance(a.get("name"), bytes) else str(a.get("name", ""))
            n = name.lower()
            if "weight" in n or "wght" in n:
                vals.append(max(a["minimum"], min(a["maximum"], weight)))
            elif "optical" in n or "opsz" in n:
                vals.append(max(a["minimum"], min(a["maximum"], 36)))
            elif "width" in n or "wdth" in n:
                vals.append(max(a["minimum"], min(a["maximum"], 100)))
            elif "slant" in n or "slnt" in n:
                vals.append(0)
            else:
                vals.append(a.get("default", a["minimum"]))
        f.set_variation_by_axes(vals)
    except Exception:
        pass
    return f


def render(job, out):
    idx = json.load(open(os.path.join(CACHE, "emoji.json"), encoding="utf-8")) if ready() else index()
    atlas = Image.open(os.path.join(CACHE, "emoji64.png")).convert("RGBA") if ready() else None
    W, H = int(job.get("w", 1080)), int(job.get("h", 1920))
    k = W / 1080.0
    text = str(job.get("text", "")).strip() or "Titolo"
    if job.get("upper", False):
        text = text.upper()
    size = int(float(job.get("size", 86)) * k)
    stroke = max(0, int(float(job.get("stroke", 10)) * k))
    escale = float(job.get("emoji", 1.0))
    weight = int(job.get("weight", 800))
    fill = job.get("fill", "#ffffff")
    scol = job.get("stroke_color", "#000000")
    font = _font(size, weight)
    es = int(size * 1.05 * escale)
    cols, S = idx["cols"], idx["size"]

    def emo(i):
        if atlas is None:
            return None
        x, y = (i % cols) * S, (i // cols) * S
        return atlas.crop((x, y, x + S, y + S)).resize((es, es), Image.LANCZOS)

    # parole (un'emoji è una parola a sé se staccata)
    words = []
    for kind, val in _tokens(text, idx):
        if kind == "e":
            if words and words[-1][-1][0] != "sp":
                words[-1].append(("e", val))
            else:
                words.append([("e", val)])
            continue
        parts = val.split(" ")
        for j, ptxt in enumerate(parts):
            if j > 0:
                words.append([("sp", None)])
            if ptxt:
                if words and words[-1] and words[-1][-1][0] != "sp" and j == 0:
                    words[-1].append(("t", ptxt))
                else:
                    words.append([("t", ptxt)])
    words = [w for w in words if w != [("sp", None)]]
    gap = font.getlength(" ")

    def wlen(w):
        return sum(font.getlength(v) if kd == "t" else es + size * 0.06 for kd, v in w)

    maxw = W * float(job.get("max_width", 0.88))
    lines, cur, curw = [], [], 0.0
    for w in words:
        lw = wlen(w)
        if cur and curw + gap + lw > maxw:
            lines.append((cur, curw))
            cur, curw = [], 0.0
        curw += (gap if cur else 0) + lw
        cur.append(w)
    if cur:
        lines.append((cur, curw))
    lh = int(size * 1.12)
    total = lh * len(lines)
    cy = float(job.get("y", 0.18)) * H
    y = int(cy - total / 2)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    emojis = []
    for line, lw in lines:
        x = (W - lw) / 2
        for wi, w in enumerate(line):
            if wi:
                x += gap
            for kd, v in w:
                if kd == "t":
                    d.text((x, y + lh / 2), v, font=font, fill=fill, anchor="lm", stroke_width=stroke, stroke_fill=scol)
                    x += font.getlength(v)
                else:
                    x += size * 0.03
                    emojis.append((int(x), int(y + lh / 2 - es / 2), v))
                    x += es + size * 0.03
        y += lh
    if job.get("shadow", True):
        a = txt.split()[3].point(lambda p: int(p * 0.55))
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh.putalpha(a)
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, size * 0.06)))
        off = int(size * 0.05)
        img.alpha_composite(sh, (off, off))
    img.alpha_composite(txt)
    for x, ey, i in emojis:
        e = emo(i)
        if e is not None:
            img.alpha_composite(e, (max(0, x), max(0, ey)))
    img.save(out)
    return True


def run_title(job_path, out):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    try:
        ensure_emoji(out)
    except Exception:
        pass  # senza internet: il titolo esce senza emoji
    return render(job, out)
