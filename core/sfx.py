"""Effetti sonori per gli zoom: dai suoni che l'utente ha sul PC (Premiere Composer / Mister Horse,
"Premiere Composer Files" sul Desktop, %LOCALAPPDATA%\\FilmCraftJarvis\\sfx). Non sono nell'app."""
import os
from functools import lru_cache

EXT = (".wav", ".mp3", ".aif", ".aiff", ".flac", ".ogg")


def roots():
    la = os.environ.get("LOCALAPPDATA", "")
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    r = [os.path.join(la, "FilmCraftJarvis", "sfx"), os.path.join(la, "MisterHorse", "ProductManager", "Products"),
         os.path.join(home, "Desktop", "Premiere Composer Files"), os.path.join(home, "Documents", "Premiere Composer Files")]
    if os.environ.get("JARVIS_SFX_DIR"):
        r.append(os.environ["JARVIS_SFX_DIR"])
    return [x for x in r if x and os.path.isdir(x)]


@lru_cache(maxsize=1)
def library():
    out, seen = [], set()
    for r in roots():
        for dp, dn, fn in os.walk(r):
            if dp[len(r):].count(os.sep) > 6:
                continue
            for f in sorted(fn):
                if f.lower().endswith(EXT) and f.lower() not in seen:
                    seen.add(f.lower()); out.append(os.path.join(dp, f))
    return sorted(out)


def pick(kind, k=0):
    keys = {"swoosh": ["swoosh 0", "swoosh"], "transition": ["swoosh transition", "transition basic", "swoosh"],
            "pop": ["pop"], "boom": ["boom"]}.get(kind, ["swoosh"])
    lib = library()
    for key in keys:
        hits = [p for p in lib if key in os.path.basename(p).lower()]
        if hits:
            return hits[k % len(hits)]
    return None


def duration(path, default=1.0):
    try:
        import wave
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return default
