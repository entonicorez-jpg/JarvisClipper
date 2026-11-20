"""Layout cam/gioco: valori di partenza e riconoscimento automatico della cam (volto)."""
from .render import OUT_W, OUT_H, even


def aspects(split):
    cam_h = even(OUT_H * split)
    return OUT_W / cam_h, OUT_W / (OUT_H - cam_h)


def fit_rect(cx, cy, w, aspect, info):
    w = min(w, info["w"]); h = w / aspect
    if h > info["h"]:
        h = info["h"]; w = h * aspect
    x = min(max(0, cx - w / 2), info["w"] - w)
    y = min(max(0, cy - h / 2), info["h"] - h)
    return {"x": round(x), "y": round(y), "w": round(w), "h": round(h)}


def default_layout(info, split=0.4):
    ca, ga = aspects(split)
    game = fit_rect(info["w"] / 2, info["h"] / 2, info["w"], ga, info)
    cw = info["w"] * 0.26
    cam = fit_rect(cw / 2, info["h"] - cw / ca / 2, cw, ca, info)
    return {"mode": "split", "split": split, "cam": cam, "game": game, "auto": False}


def auto_cam(frame_paths, info, split=0.4):
    """Cerca un volto nei frame; la cam viene centrata sul volto. None se non trova niente."""
    try:
        import cv2
    except Exception:
        return None
    try:
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if casc.empty():
            return None
    except Exception:
        return None
    faces = []
    for fp in frame_paths:
        img = cv2.imread(fp)
        if img is None:
            continue
        k = info["w"] / img.shape[1]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in casc.detectMultiScale(gray, 1.1, 6, minSize=(24, 24)):
            faces.append((x * k, y * k, w * k, h * k))
    if not faces:
        return None
    faces.sort(key=lambda f: f[2])
    x, y, w, h = faces[len(faces) // 2]
    ca, _ = aspects(split)
    return fit_rect(x + w / 2, y + h / 2 + h * 0.25, w * 3.2, ca, info)
