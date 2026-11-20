"""Parolacce da censurare e frasi "spam TikTok" da tagliare, riconosciute nella trascrizione."""
import re
from .analyze import norm_text

# parole (o radici di almeno 4 lettere: coprono le varianti, es. "incazz")
SWEARS = ["cazzo", "cazzi", "cazzata", "cazzate", "cazzone", "minchia", "merda", "merde", "stronzo", "stronza", "stronzi",
          "stronzate", "vaffanculo", "fanculo", "culo", "puttana", "puttane", "troia", "troie", "coglione", "coglioni",
          "bastardo", "bastarda", "figa", "fica", "incazz", "porcodio", "porcamadonna", "diocane", "dioporco", "madonna",
          "zoccola", "mignotta", "frocio", "ricchione", "sega", "seghe", "pompino", "scopare", "fottiti", "fottuto",
          "fuck", "fucking", "shit", "bitch", "motherfucker"]
# parole innocue che iniziano come una parolaccia
ALLOW = {"figata", "figate", "figo", "fighi", "figone", "figona", "fighissimo", "fighissima", "ficata", "ficate",
         "fichissimo", "fichissima", "culone", "culturale", "cultura", "segale", "segatura", "segare", "segnale",
         "merdina", "madonnina", "madonne"}
# coppie che diventano bestemmie (si censurano entrambe le parole)
PAIRS = [("porco", "dio"), ("dio", "cane"), ("dio", "porco"), ("porca", "madonna"), ("madonna", "puttana"),
         ("dio", "bestia"), ("dio", "boia"), ("porco", "gesu"), ("dio", "merda"), ("cristo", "santo")]
# frasi tipiche da live TikTok/Kick che negli short e nei video stonano
SPAM = ["tap tap", "tapp", "fate tap", "fate i tap", "mettete like", "lasciate un like", "like alla live", "cuoricin",
        "seguite", "seguitemi", "segui il profilo", "seguimi", "follow", "followat", "iscriviti", "iscrivetevi",
        "attivate la campanella", "condividete", "condividi la live", "share", "grazie per la rosa", "grazie per le rose",
        "rosa", "rose", "leone", "leoni", "universo", "galassia", "regalo", "regali", "regalino", "grazie per il regalo",
        "grazie mille", "grazie a", "grazie per", "ringrazio", "ti ringrazio", "vi ringrazio", "benvenut", "bentornat",
        "abbonat", "abbonamento", "sub ", "subscriber", "donazion", "donato", "superchat", "obiettivo della live",
        "goal della live", "classifica", "top fan", "supporto", "sostenete", "link in bio", "codice sconto",
        "sponsor", "kick punto com", "siamo in live", "sono in live", "ciao a tutti", "ciao chat", "ciao ragazzi"]


def _n(w):
    return re.sub(r"[^a-z0-9]", "", norm_text(w))


def is_swear(word):
    n = _n(word)
    if not n or n in ALLOW:
        return False
    return n in SWEARS or any(len(b) >= 4 and n.startswith(b) for b in SWEARS if not b.startswith("dio"))


def censor_ranges(words, pad=0.04):
    """words = [{"w","s","e"}] -> [{"s","e","w","on"}] parole da coprire con un bip."""
    out, ns = [], [_n(w["w"]) for w in words]
    for i, w in enumerate(words):
        hit = is_swear(w["w"])
        if not hit and i + 1 < len(words):
            if (ns[i], ns[i + 1]) in PAIRS:
                hit = True
                out.append({"s": round(words[i + 1]["s"] - pad, 3), "e": round(words[i + 1]["e"] + pad, 3), "w": words[i + 1]["w"], "on": True})
        if hit:
            out.append({"s": round(w["s"] - pad, 3), "e": round(w["e"] + pad, 3), "w": w["w"], "on": True})
    out.sort(key=lambda c: c["s"])
    merged = []
    for c in out:
        if merged and c["s"] <= merged[-1]["e"]:
            merged[-1]["e"] = max(merged[-1]["e"], c["e"]); merged[-1]["w"] += " " + c["w"]
        else:
            merged.append(dict(c))
    return merged


def find_spam(segs):
    """segs = segmenti Whisper [{"s","e","text"}] -> [{"s","e","text","why"}] frasi da tagliare."""
    out = []
    for sg in segs:
        t = " " + norm_text(sg["text"]) + " "
        why = next((p for p in SPAM if (" " + p) in t), None)
        if why:
            out.append({"s": round(sg["s"] - 0.1, 3), "e": round(sg["e"] + 0.1, 3), "text": sg["text"].strip()[:140], "why": why.strip()})
    return out


def subtract(keep, cuts, min_len=0.15):
    """Toglie gli intervalli cuts dai tratti keep."""
    res = []
    for a, b in keep:
        segs = [(a, b)]
        for c in cuts:
            cs, ce = c["s"], c["e"]
            nxt = []
            for x, y in segs:
                if ce <= x or cs >= y:
                    nxt.append((x, y)); continue
                if cs > x:
                    nxt.append((x, cs))
                if ce < y:
                    nxt.append((ce, y))
            segs = nxt
        res += [[round(x, 3), round(y, 3)] for x, y in segs if y - x >= min_len]
    return res
