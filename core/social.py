"""Pannellini social animati per FilmCraft Jarvis (come quelli di Premiere Composer):
- "yt_sub":   pannello YouTube con avatar, nome e tasto ISCRIVITI; arriva il cursore, clic, diventa ISCRITTO
- "ig_follow": pannello bianco Instagram con avatar, nome e tasto Segui; clic, diventa Segui già
- "yt_watch": scheda "Tempo di visualizzazione degli iscritti" con la barra e le percentuali che salgono

`JarvisClipper.exe --social job.json out.webm` disegna l'animazione (video WebM con trasparenza, grande
come la sequenza): sale dal basso, fa la sua animazione, riscende."""
import json
import math
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import media

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(BASE, "assets", "social")
SS = 2  # disegno a doppia risoluzione e rimpicciolisco: bordi lisci


# ---------------------------------------------------------------- easing
def clamp01(x):
    return max(0.0, min(1.0, x))


def ease_out_back(x, s=1.4):
    x = clamp01(x) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def ease_out_cubic(x):
    return 1 - (1 - clamp01(x)) ** 3


def ease_in_cubic(x):
    return clamp01(x) ** 3


def ease_in_out(x):
    x = clamp01(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def font(size, weight=500, family="Roboto"):
    path = os.path.join(RES, "Roboto.ttf") if family == "Roboto" else os.path.join(BASE, "assets", "titoli", "TikTokSans.ttf")
    f = ImageFont.truetype(path, int(size))
    try:
        vals = []
        for a in f.get_variation_axes():
            n = a.get("name", b"")
            n = (n.decode() if isinstance(n, bytes) else str(n)).lower()
            if "weight" in n or "wght" in n:
                vals.append(max(a["minimum"], min(a["maximum"], weight)))
            elif "optical" in n or "opsz" in n:
                vals.append(max(a["minimum"], min(a["maximum"], 24)))
            elif "width" in n or "wdth" in n:
                vals.append(100)
            else:
                vals.append(a.get("default", a["minimum"]))
        f.set_variation_by_axes(vals)
    except Exception:
        pass
    return f


def hexc(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def rounded(size, radius, fill, outline=None, width=0):
    """Rettangolo arrotondato liscio (disegnato grande e rimpicciolito)."""
    w, h = size
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius * SS, fill=fill, outline=outline, width=width * SS)
    return im.resize((w, h), Image.LANCZOS)


def shadow(img, blur, alpha=110, off=(0, 10)):
    a = img.split()[3].point(lambda p: int(p * alpha / 255))
    sh = Image.new("RGBA", (img.width + blur * 4, img.height + blur * 4), (0, 0, 0, 0))
    blk = Image.new("RGBA", img.size, (0, 0, 0, 255))
    blk.putalpha(a)
    sh.alpha_composite(blk, (blur * 2 + off[0], blur * 2 + off[1]))
    return sh.filter(ImageFilter.GaussianBlur(blur))


def avatar(path, d, name="?"):
    if path and os.path.exists(path):
        im = Image.open(path).convert("RGBA").resize((d * SS, d * SS), Image.LANCZOS)
    else:
        im = Image.new("RGBA", (d * SS, d * SS), hexc("#7c4dff"))
        dr = ImageDraw.Draw(im)
        f = font(d * SS * 0.5, 700)
        dr.text((d * SS / 2, d * SS / 2), (name or "?")[:1].upper(), font=f, fill="white", anchor="mm")
    m = Image.new("L", (d * SS, d * SS), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d * SS - 1, d * SS - 1), fill=255)
    im.putalpha(m)
    return im.resize((d, d), Image.LANCZOS)


def text_img(txt, f, fill):
    l, t, r, b = f.getbbox(txt)
    im = Image.new("RGBA", (max(1, r - l + 4), max(1, b - t + 4)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((2 - l, 2 - t), txt, font=f, fill=fill)
    return im


def cursor_img(h):
    """Freccia del mouse bianca col bordo nero."""
    pts = [(0, 0), (0, 17), (4.2, 13.2), (7.2, 20), (10, 18.8), (7.2, 12.2), (12.6, 12.2)]
    k = h / 20 * SS
    im = Image.new("RGBA", (int(14 * k) + 8, int(22 * k) + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pp = [(4 + x * k, 4 + y * k) for x, y in pts]
    d.polygon(pp, fill="white", outline="black", width=int(1.6 * k))
    im = im.resize((im.width // SS, im.height // SS), Image.LANCZOS)
    return im


def bell_img(s, color):
    im = Image.new("RGBA", (s * SS, s * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    S = s * SS
    d.rounded_rectangle((S * 0.22, S * 0.18, S * 0.78, S * 0.78), S * 0.28, fill=color)
    d.rectangle((S * 0.14, S * 0.62, S * 0.86, S * 0.76), fill=color)
    d.ellipse((S * 0.42, S * 0.76, S * 0.58, S * 0.92), fill=color)
    d.ellipse((S * 0.45, S * 0.08, S * 0.55, S * 0.2), fill=color)
    return im.resize((s, s), Image.LANCZOS)


# ---------------------------------------------------------------- pannelli
def button(label, bg, fg, h, f, icon=None, pad=None):
    tw = int(f.getlength(label))
    pad = pad or int(h * 0.55)
    iw = int(h * 0.5) + int(h * 0.18) if icon else 0
    w = tw + pad * 2 + iw
    im = rounded((w, h), h // 2, bg)
    x = pad
    if icon:
        im.alpha_composite(icon, (x, (h - icon.height) // 2))
        x += iw
    t = text_img(label, f, fg)
    im.alpha_composite(t, (x - 2, (h - t.height) // 2))
    return im


def make_yt_sub(job, W):
    dark = job.get("theme", "dark") == "dark"
    k = W / 1080
    h = int(170 * k)
    name = job.get("name") or "MrJake"
    sub = job.get("subtitle") or "Nuovi video ogni settimana"
    av = avatar(job.get("avatar"), int(112 * k), name)
    fn, fs, fb = font(46 * k, 700), font(30 * k, 400), font(34 * k, 600)
    b_on = button("Iscriviti", hexc("#ff0033"), "white", int(84 * k), fb)
    b_off = button("Iscritto", hexc("#3a3a3a") if dark else hexc("#e5e5e5"), "white" if dark else "black", int(84 * k), fb,
                   icon=bell_img(int(40 * k), (255, 255, 255, 255) if dark else (0, 0, 0, 255)))
    tw = max(fn.getlength(name), fs.getlength(sub))
    w = int(40 * k + av.width + 28 * k + tw + 40 * k + max(b_on.width, b_off.width) + 36 * k)
    card = rounded((w, h), int(48 * k), hexc("#0f0f0f", 245) if dark else hexc("#ffffff", 250))
    card.alpha_composite(av, (int(30 * k), (h - av.height) // 2))
    tx = int(30 * k + av.width + 28 * k)
    t1 = text_img(name, fn, "white" if dark else "#0f0f0f")
    t2 = text_img(sub, fs, "#aaaaaa" if dark else "#606060")
    card.alpha_composite(t1, (tx, int(h / 2 - t1.height - 2 * k)))
    card.alpha_composite(t2, (tx, int(h / 2 + 6 * k)))
    bx = w - int(36 * k) - max(b_on.width, b_off.width)
    return card, (b_on, b_off, bx), None


def make_ig(job, W):
    k = W / 1080
    h = int(170 * k)
    name = job.get("name") or "Giacchetta"
    sub = job.get("subtitle") or "Instagram"
    av = avatar(job.get("avatar"), int(112 * k), name)
    fn, fs, fb = font(44 * k, 700), font(30 * k, 400), font(34 * k, 600)
    b_on = button("Segui", hexc("#0095f6"), "white", int(80 * k), fb)
    b_off = button("Segui già", hexc("#efefef"), "black", int(80 * k), fb)
    tw = max(fn.getlength(name), fs.getlength(sub))
    w = int(40 * k + av.width + 28 * k + tw + 40 * k + max(b_on.width, b_off.width) + 36 * k)
    card = rounded((w, h), int(44 * k), hexc("#ffffff", 252))
    # anello sfumato tipo storia attorno all'avatar
    ring = Image.new("RGBA", ((av.width + int(14 * k)) * SS,) * 2, (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    R = ring.width
    for i in range(36):
        a0, a1 = i * 10, i * 10 + 11
        t = i / 36
        col = (int(254 - 60 * t), int(200 - 170 * t), int(60 + 120 * t), 255)
        rd.pieslice((0, 0, R - 1, R - 1), a0, a1, fill=col)
    rd.ellipse((int(6 * k * SS), int(6 * k * SS), R - 1 - int(6 * k * SS), R - 1 - int(6 * k * SS)), fill=(0, 0, 0, 0))
    ring = ring.resize((ring.width // SS,) * 2, Image.LANCZOS)
    ax = int(24 * k)
    card.alpha_composite(ring, (ax, (h - ring.height) // 2))
    card.alpha_composite(av, (ax + (ring.width - av.width) // 2, (h - av.height) // 2))
    tx = int(ax + ring.width + 24 * k)
    t1 = text_img(name, fn, "#000000")
    t2 = text_img(sub, fs, "#737373")
    card.alpha_composite(t1, (tx, int(h / 2 - t1.height - 2 * k)))
    card.alpha_composite(t2, (tx, int(h / 2 + 6 * k)))
    bx = w - int(36 * k) - max(b_on.width, b_off.width)
    return card, (b_on, b_off, bx), None


def make_watch(job, W):
    dark = job.get("theme", "dark") == "dark"
    k = W / 1080
    w, h = int(940 * k), int(400 * k)
    card = rounded((w, h), int(40 * k), hexc("#0f0f0f", 248) if dark else hexc("#ffffff", 252))
    fg = "white" if dark else "#0f0f0f"
    mut = "#aaaaaa" if dark else "#606060"
    t1 = text_img(job.get("title") or "Tempo di visualizzazione degli iscritti", font(44 * k, 700), fg)
    t2 = text_img(job.get("subtitle") or "Tempo di visualizzazione (ore) • Ultimi 28 giorni", font(30 * k, 400), mut)
    card.alpha_composite(t1, (int(44 * k), int(40 * k)))
    card.alpha_composite(t2, (int(44 * k), int(40 * k) + t1.height + int(10 * k)))
    return card, None, {"bar": (int(44 * k), int(190 * k), w - int(88 * k), int(22 * k)), "fg": fg, "mut": mut, "k": k,
                        "value": float(job.get("value", 24.2)), "track": "#3f3f3f" if dark else "#e5e5e5"}


def run(job_path, out):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    W, H = int(job.get("w", 1080)), int(job.get("h", 1920))
    fps = float(job.get("fps", 30))
    fps = 60.0 if fps > 45 else 30.0
    kind = job.get("preset", "yt_sub")
    dur = float(job.get("seconds", 4.5 if kind != "yt_watch" else 5.0))
    maker = {"yt_sub": make_yt_sub, "ig_follow": make_ig, "yt_watch": make_watch}[kind]
    card, btn, watch = maker(job, W)
    sh = shadow(card, int(18 * W / 1080), 120, (0, int(10 * W / 1080)))
    cx = (W - card.width) // 2
    cy = int(float(job.get("y", 0.78)) * H - card.height / 2)
    cur = cursor_img(int(58 * W / 1080))
    n = int(round(dur * fps))
    t_in, t_out = 0.55, 0.45  # secondi per salire e scendere
    click_t = 1.55
    k = W / 1080
    cmd = [media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "3M",
           "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0", out]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=getattr(media, "CREATE_NO_WINDOW", 0))
    for i in range(n):
        t = i / fps
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # entrata dal basso con un piccolo rimbalzo, uscita verso il basso
        if t < t_in:
            p = ease_out_back(t / t_in, 1.2)
            dy = (1 - p) * (H - cy + 40 * k)
            alpha = clamp01(t / (t_in * 0.5))
        elif t > dur - t_out:
            p = ease_in_cubic((t - (dur - t_out)) / t_out)
            dy = p * (H - cy + 40 * k)
            alpha = 1 - clamp01((t - (dur - t_out * 0.4)) / (t_out * 0.4))
        else:
            dy, alpha = 0, 1
        layer = card.copy()
        if btn:
            b_on, b_off, bx = btn
            # clic: il tasto si schiaccia, poi cambia (rosso -> grigio) con un piccolo scatto
            press = 0.0
            if click_t - 0.08 <= t <= click_t + 0.18:
                press = math.sin(clamp01((t - (click_t - 0.08)) / 0.26) * math.pi)
            # dopo il clic cambia di colpo, con un rimbalzo (si gonfia un attimo e torna)
            bw = max(b_on.width, b_off.width)
            b = b_off if t >= click_t + 0.04 else b_on
            pop = 0.0
            if click_t + 0.04 <= t <= click_t + 0.34:
                pop = math.sin(clamp01((t - click_t - 0.04) / 0.3) * math.pi)
            s = 1 - 0.1 * press * (t < click_t + 0.04) + 0.08 * pop
            bb = b.resize((max(1, int(b.width * s)), max(1, int(b.height * s))), Image.LANCZOS)
            by = (layer.height - bb.height) // 2
            layer.alpha_composite(bb, (bx + bw - b.width + (b.width - bb.width) // 2, by))
        if watch:
            bx0, by0, bw0, bh0 = watch["bar"]
            kk = watch["k"]
            v = watch["value"]
            pr_ = ease_in_out((t - 0.7) / 1.6)
            cur_v = v * pr_
            other = (100 - v) * pr_
            d = ImageDraw.Draw(layer)
            trk = rounded((bw0, bh0), bh0 // 2, hexc(watch["track"]))
            layer.alpha_composite(trk, (bx0, by0))
            fw = int(bw0 * cur_v / 100)
            if fw > bh0:
                fill = rounded((fw, bh0), bh0 // 2, hexc("#3ea6ff") if watch["fg"] == "white" else hexc("#065fd4"))
                layer.alpha_composite(fill, (bx0, by0))
            f_big, f_small = font(64 * kk, 700), font(30 * kk, 400)
            l1 = f"{cur_v:.1f}%".replace(".", ",")
            l2 = f"{other:.1f}%".replace(".", ",")
            ty = by0 + bh0 + int(36 * kk)
            d.text((bx0, ty), l1, font=f_big, fill=watch["fg"])
            d.text((bx0, ty + int(78 * kk)), "Iscritti", font=f_small, fill=watch["mut"])
            d.text((bx0 + bw0, ty), l2, font=f_big, fill=watch["fg"], anchor="ra")
            d.text((bx0 + bw0, ty + int(78 * kk)), "Non iscritti", font=f_small, fill=watch["mut"], anchor="ra")
        if alpha < 1:
            a = layer.split()[3].point(lambda p_, al=alpha: int(p_ * al))
            layer.putalpha(a)
        y = int(cy + dy)
        frame.alpha_composite(sh, (cx - sh.width // 2 + card.width // 2, y - sh.height // 2 + card.height // 2 + int(6 * k)))
        frame.alpha_composite(layer, (cx, y))
        if btn and 0.75 <= t <= dur - t_out:
            # il cursore arriva dal basso a destra, clicca e se ne va
            b_on, b_off, bx = btn
            tx_, ty_ = cx + bx + max(b_on.width, b_off.width) * 0.62, y + card.height * 0.62
            sx, sy = W * 0.92, H * 1.02
            if t < click_t:
                q = ease_in_out((t - 0.75) / (click_t - 0.75))
            elif t < click_t + 0.6:
                q = 1
            else:
                q = 1 - ease_in_out((t - click_t - 0.6) / 0.5)
            px, py = sx + (tx_ - sx) * q, sy + (ty_ - sy) * q
            ck = cur
            if abs(t - click_t) < 0.1:
                ck = cur.resize((int(cur.width * 0.9), int(cur.height * 0.9)), Image.LANCZOS)
            frame.alpha_composite(ck, (int(px), int(py)))
        pr.stdin.write(frame.tobytes())
    pr.stdin.close()
    pr.wait()
    return pr.returncode == 0 and os.path.exists(out)
