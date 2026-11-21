"""Trascrizione salvata accanto al video (<video>.jarvis-words.json): FilmCraft Jarvis la usa per
i sottotitoli invece di ritrascrivere (più precisa, fatta con la scheda video, e immediata)."""
import json
import os


def path_for(src):
    return src + ".jarvis-words.json"


def save(src, segs, a, b):
    """Aggiunge le parole di [a, b] (secondi del file); quelle vecchie in quel tratto vengono sostituite."""
    try:
        fp = path_for(src)
        d = {"words": [], "ranges": []}
        if os.path.exists(fp):
            try:
                with open(fp, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                pass
        words = [w for w in d.get("words", []) if not (a <= w["s"] < b)]
        for sg in segs:
            for w in sg.get("words", []):
                if a - 0.01 <= w["s"] < b + 0.01 and w.get("w", "").strip():
                    words.append({"w": w["w"].strip(), "s": round(w["s"], 3), "e": round(w["e"], 3)})
        words.sort(key=lambda w: w["s"])
        rs = sorted([list(r) for r in d.get("ranges", [])] + [[round(a, 3), round(b, 3)]])
        merged = []
        for r in rs:
            if merged and r[0] <= merged[-1][1] + 0.01:
                merged[-1][1] = max(merged[-1][1], r[1])
            else:
                merged.append(r)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "words": words, "ranges": merged}, f, ensure_ascii=False)
    except Exception:
        pass  # non è essenziale (cartella in sola lettura ecc.)
