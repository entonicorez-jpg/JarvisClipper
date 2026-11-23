"""Jarvis impara dal montaggio di Jake.

1. Quando Jarvis esporta in FilmCraft salva la sua PROPOSTA (tagli, zoom, inizio/fine delle clip) in
   %LOCALAPPDATA%\\JarvisClipper\\impara\\proposte.
2. FilmCraft Jarvis, mentre Jake monta, scrive la FOTOGRAFIA delle sequenze di Jarvis in
   %LOCALAPPDATA%\\JarvisClipper\\impara\\filmcraft.
3. Qui si confrontano: cosa ha tolto, cosa ha rimesso, quanto ha accorciato inizio e fine, quali zoom
   ha cancellato o aggiunto e quanto forti. Il risultato (stile.json) serve a:
   - ritoccare in automatico i prossimi montaggi (margini dei tagli, forza e numero degli zoom, inizio/fine clip);
   - spiegare a Gemini lo "stile di Jake", con esempi veri di frasi che toglie.
"""
import glob
import hashlib
import json
import os
import time

BASE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "impara")
PROP = os.path.join(BASE, "proposte")
SNAP = os.path.join(BASE, "filmcraft")
STYLE = os.path.join(BASE, "stile.json")
MAX_SESS = 40


def _norm(p):
    return os.path.normcase(os.path.abspath(p or "")).replace("\\", "/")


def _load(fp, default):
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(fp, d):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)


# ------------------------------------------------------------------ proposte
def save_edit_proposal(src, ed, seq_name):
    try:
        d = {"kind": "edit", "src": src, "seq": seq_name, "time": time.time(),
             "keep": [[round(a, 3), round(b, 3)] for a, b in sorted(ed["keep"])],
             "zooms": [{"s": z["s"], "e": z["e"], "lv": z.get("lv", 1)} for z in ed.get("zooms", [])],
             "zoom": ed.get("zoom", 1.32)}
        _save(os.path.join(PROP, hashlib.md5(seq_name.encode()).hexdigest()[:12] + ".json"), d)
    except Exception:
        pass


def save_clips_proposal(src, clips):
    try:
        for c in clips:
            nm = f"Clip {c['id']:02d} - {c['score']} - {c.get('title', '')}"[:80]
            d = {"kind": "clip", "src": src, "seq": nm, "time": time.time(), "start": c["start"], "end": c["end"],
                 "title": c.get("title", "")}
            _save(os.path.join(PROP, hashlib.md5((src + nm).encode()).hexdigest()[:12] + ".json"), d)
    except Exception:
        pass


# ------------------------------------------------------------------ confronto
def _main_items(snap, src):
    """Le clip del video sorgente sulle tracce video (la più bassa che lo contiene), in ordine."""
    for tr in snap.get("tracks", []):
        if not tr["name"].startswith("V"):
            continue
        its = [i for i in tr["items"] if i.get("kind") == "media" and i.get("enabled", True) and _norm(i.get("path")) == _norm(src)]
        if its:
            return sorted(its, key=lambda i: i["start"])
    return []


def _src_ranges(items):
    out = []
    for i in items:
        sp = abs(i.get("speed") or 1.0)
        out.append((i["in"], i["in"] + (i["end"] - i["start"]) * sp, i["start"], i["end"]))
    return out


def _to_src(t, ranges):
    for a, b, s, e in ranges:
        if s - 1e-3 <= t <= e + 1e-3:
            return a + (t - s) * ((b - a) / max(e - s, 1e-6))
    return None


def _overlap(a, b, c, d):
    return max(0.0, min(b, d) - max(a, c))


def _subtract(ranges, minus):
    out = []
    for a, b in ranges:
        segs = [(a, b)]
        for c, d in minus:
            nxt = []
            for x, y in segs:
                if d <= x or c >= y:
                    nxt.append((x, y)); continue
                if c > x:
                    nxt.append((x, c))
                if d < y:
                    nxt.append((d, y))
            segs = nxt
        out += [s for s in segs if s[1] - s[0] > 0.05]
    return out


def _words(src):
    d = _load(src + ".jarvis-words.json", {})
    return d.get("words", [])


def _text(words, a, b):
    return " ".join(w["w"] for w in words if a - 0.05 <= w["s"] and w["e"] <= b + 0.05).strip()


def _zoom_layers(snap):
    """Gli zoom finali: livelli di regolazione o clip ingrandite oltre il 103% (tempi della timeline)."""
    out = []
    for tr in snap.get("tracks", []):
        if not tr["name"].startswith("V"):
            continue
        for i in tr["items"]:
            z = i.get("zoom") or 0
            if i.get("enabled", True) and i.get("kind") in ("adjust", "media") and z > 103:
                out.append((i["start"], i["end"], z))
    return out


def compare_edit(prop, snap):
    src = prop["src"]
    items = _main_items(snap, src)
    if not items:
        return None
    rng = _src_ranges(items)
    final = [(a, b) for a, b, _, _ in rng]
    keep = [tuple(k) for k in prop["keep"]]
    words = _words(src)
    res = {"kind": "edit", "seq": prop["seq"], "time": time.time()}
    # pezzi tolti / rimessi (oltre 1.2 s: scelta di contenuto, sotto: ritocco del margine)
    removed = [r for r in _subtract(keep, final)]
    added = [r for r in _subtract(final, keep)]
    big_rm = [r for r in removed if r[1] - r[0] >= 1.2]
    big_add = [r for r in added if r[1] - r[0] >= 1.2]
    tot = sum(b - a for a, b in keep) or 1
    res["removed_ratio"] = sum(b - a for a, b in big_rm) / tot
    res["added_ratio"] = sum(b - a for a, b in big_add) / tot
    res["removed_text"] = [t for t in (_text(words, a, b) for a, b in big_rm) if len(t) > 3][:12]
    res["added_text"] = [t for t in (_text(words, a, b) for a, b in big_add) if len(t) > 3][:8]
    # margini: per ogni pezzo finale, quanto inizio/fine si spostano rispetto al pezzo proposto più vicino
    ds, de = [], []
    for a, b in final:
        best = max(keep, key=lambda k: _overlap(a, b, k[0], k[1]), default=None)
        if not best or _overlap(a, b, *best) <= 0:
            continue
        if abs(a - best[0]) < 1.2:
            ds.append(a - best[0])
        if abs(b - best[1]) < 1.2:
            de.append(b - best[1])
    res["trim_start"] = sum(ds) / len(ds) if ds else 0.0
    res["trim_end"] = sum(de) / len(de) if de else 0.0
    res["n_trim"] = len(ds) + len(de)
    # zoom
    pz = [(z["s"], z["e"]) for z in prop.get("zooms", []) if z.get("lv", 1) == 1]
    fz = []
    for s, e, sc in _zoom_layers(snap):
        a, b = _to_src(s, rng), _to_src(e, rng)
        if a is None:
            a = _to_src(s + 0.1, rng)
        if b is None:
            b = _to_src(e - 0.1, rng)
        if a is not None and b is not None and b > a:
            fz.append((a, b, sc))
    kept = [z for z in pz if any(_overlap(z[0], z[1], a, b) > 0.4 * (z[1] - z[0]) for a, b, _ in fz)]
    new = [z for z in fz if not any(_overlap(z[0], z[1], a, b) > 0.4 * (z[1] - z[0]) for a, b in pz)]
    mins = max(sum(b - a for a, b in final) / 60, 0.3)
    res["zoom_proposed"] = len(pz)
    res["zoom_keep"] = len(kept) / len(pz) if pz else None
    res["zoom_added_min"] = len(new) / mins
    res["zoom_scale"] = sum(sc for _, _, sc in fz) / len(fz) if fz else None
    res["zoom_len"] = sum(b - a for a, b, _ in fz) / len(fz) if fz else None
    res["zoom_added_text"] = [t for t in (_text(words, a - 1.5, b) for a, b, _ in new) if len(t) > 3][:8]
    return res


def compare_clip(prop, snap):
    items = _main_items(snap, prop["src"])
    if not items:
        return None
    rng = _src_ranges(items)
    a, b = min(r[0] for r in rng), max(r[1] for r in rng)
    words = _words(prop["src"])
    res = {"kind": "clip", "seq": prop["seq"], "time": time.time(), "trim_start": a - prop["start"], "trim_end": b - prop["end"],
           "len": sum(r[1] - r[0] for r in rng), "proposed_len": prop["end"] - prop["start"]}
    if a - prop["start"] > 1.0:
        res["cut_head"] = _text(words, prop["start"], a)[:200]
    if prop["end"] - b > 1.0:
        res["cut_tail"] = _text(words, b, prop["end"])[:200]
    return res


# ------------------------------------------------------------------ apprendimento
def learn(log=None):
    """Confronta le fotografie nuove con le proposte e aggiorna stile.json. Torna quante sessioni ha visto."""
    st = _load(STYLE, {"sessions": {}})
    props = {}
    for fp in glob.glob(os.path.join(PROP, "*.json")):
        p = _load(fp, None)
        if p and p.get("seq"):
            props.setdefault(p["seq"], []).append(p)
    changed = 0
    for fp in glob.glob(os.path.join(SNAP, "*.json")):
        snap = _load(fp, None)
        if not snap:
            continue
        cands = props.get(snap.get("sequence", ""), [])
        if not cands:
            continue
        prop = max(cands, key=lambda p: p.get("time", 0))
        key = os.path.basename(fp)
        mt = os.path.getmtime(fp)
        old = st["sessions"].get(key)
        if old and old.get("mtime") == mt:
            continue
        try:
            r = compare_edit(prop, snap) if prop["kind"] == "edit" else compare_clip(prop, snap)
        except Exception as e:
            if log:
                log(f"Impara: {key}: {e}")
            continue
        if not r:
            continue
        r["mtime"] = mt
        st["sessions"][key] = r
        changed += 1
    if changed:
        ss = sorted(st["sessions"].items(), key=lambda kv: kv[1].get("time", 0))[-MAX_SESS:]
        st["sessions"] = dict(ss)
        st["profile"] = profile(st["sessions"])
        _save(STYLE, st)
    return changed


def _avg(vals, default=None):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else default


def profile(sessions):
    eds = [s for s in sessions.values() if s["kind"] == "edit"]
    cls = [s for s in sessions.values() if s["kind"] == "clip"]
    p = {"n_edit": len(eds), "n_clip": len(cls)}
    if eds:
        p["trim_start"] = _avg([s["trim_start"] for s in eds if s.get("n_trim")], 0.0)
        p["trim_end"] = _avg([s["trim_end"] for s in eds if s.get("n_trim")], 0.0)
        p["removed_ratio"] = _avg([s["removed_ratio"] for s in eds], 0.0)
        p["added_ratio"] = _avg([s["added_ratio"] for s in eds], 0.0)
        p["zoom_keep"] = _avg([s.get("zoom_keep") for s in eds])
        p["zoom_added_min"] = _avg([s.get("zoom_added_min") for s in eds], 0.0)
        p["zoom_scale"] = _avg([s.get("zoom_scale") for s in eds])
        p["zoom_len"] = _avg([s.get("zoom_len") for s in eds])
        p["removed_text"] = list(dict.fromkeys(t for s in reversed(eds) for t in s.get("removed_text", [])))[:25]
        p["added_text"] = list(dict.fromkeys(t for s in reversed(eds) for t in s.get("added_text", [])))[:10]
        p["zoom_added_text"] = list(dict.fromkeys(t for s in reversed(eds) for t in s.get("zoom_added_text", [])))[:12]
    if cls:
        p["clip_trim_start"] = _avg([s["trim_start"] for s in cls], 0.0)
        p["clip_trim_end"] = _avg([s["trim_end"] for s in cls], 0.0)
        p["clip_len"] = _avg([s["len"] for s in cls])
        p["cut_head"] = [s["cut_head"] for s in reversed(cls) if s.get("cut_head")][:8]
        p["cut_tail"] = [s["cut_tail"] for s in reversed(cls) if s.get("cut_tail")][:8]
    return p


def get_profile():
    return _load(STYLE, {}).get("profile") or {}


def reset():
    for fp in [STYLE] + glob.glob(os.path.join(SNAP, "*.json")) + glob.glob(os.path.join(PROP, "*.json")):
        try:
            os.remove(fp)
        except OSError:
            pass


# ------------------------------------------------------------------ uso
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def apply_edit(ed, log=None):
    """Ritocca un montaggio appena fatto secondo lo stile imparato (servono almeno 2 montaggi corretti)."""
    p = get_profile()
    if p.get("n_edit", 0) < 2:
        return ed
    ts, te = _clamp(p.get("trim_start", 0) * 0.8, -0.35, 0.35), _clamp(p.get("trim_end", 0) * 0.8, -0.35, 0.35)
    if abs(ts) > 0.03 or abs(te) > 0.03:
        keep = []
        for a, b in sorted(ed["keep"]):
            a2, b2 = a + ts, b + te
            if b2 - a2 < 0.4:
                a2, b2 = a, b
            if keep and a2 < keep[-1][1]:
                a2 = keep[-1][1]
            if b2 > a2:
                keep.append((a2, b2))
        ed["keep"] = keep
    msg = []
    if p.get("zoom_scale"):
        want = _clamp(p["zoom_scale"] / 100.0, 1.08, 1.8)
        ed["zoom"] = round(ed.get("zoom", 1.32) * 0.3 + want * 0.7, 3)
        msg.append(f"zoom al {round(ed['zoom'] * 100)}%")
    kr = p.get("zoom_keep")
    if kr is not None and kr < 0.6 and ed.get("zooms"):
        z1 = [z for z in ed["zooms"] if z.get("lv", 1) == 1]
        n = max(1, round(len(z1) * max(kr, 0.25) / 0.8))
        if n < len(z1):
            step = len(z1) / n
            keepz = {id(z1[int(i * step)]) for i in range(n)}
            ed["zooms"] = [z for z in ed["zooms"] if z.get("lv", 1) != 1 or id(z) in keepz]
            msg.append(f"meno zoom ({n} invece di {len(z1)})")
    if log and (msg or abs(ts) > 0.03 or abs(te) > 0.03):
        log("🧠 Stile imparato: " + ", ".join(msg + [f"margini {ts:+.2f}s / {te:+.2f}s"]))
    return ed


def apply_clips(clips, dur, log=None):
    p = get_profile()
    if p.get("n_clip", 0) < 3:
        return clips
    ts, te = _clamp(p.get("clip_trim_start", 0) * 0.7, -3, 3), _clamp(p.get("clip_trim_end", 0) * 0.7, -3, 3)
    if abs(ts) < 0.2 and abs(te) < 0.2:
        return clips
    for c in clips:
        a, b = max(0, c["start"] + ts), min(dur, c["end"] + te)
        if b - a >= 6:
            c["start"], c["end"] = round(a, 2), round(b, 2)
    if log:
        log(f"🧠 Stile imparato: inizio clip {ts:+.1f}s, fine {te:+.1f}s")
    return clips


def prompt_text(kind="edit"):
    """Il pezzo da aggiungere al prompt di Gemini."""
    p = get_profile()
    if not p or (p.get("n_edit", 0) + p.get("n_clip", 0)) == 0:
        return ""
    L = ["", "STILE DI JAKE (imparato da come ha corretto i tuoi montaggi precedenti, seguilo):"]
    if kind in ("edit", "fc") and p.get("n_edit"):
        if p.get("removed_ratio", 0) > 0.04:
            L.append(f"- Taglia di più: Jake toglie ancora circa il {round(p['removed_ratio'] * 100)}% di quello che lasci.")
        if p.get("added_ratio", 0) > 0.04:
            L.append(f"- Taglia di meno: Jake rimette circa il {round(p['added_ratio'] * 100)}% in più.")
        if abs(p.get("trim_start", 0)) > 0.05:
            L.append(f"- Gli inizi dei pezzi li sposta di {p['trim_start']:+.2f}s ({'più stretti' if p['trim_start'] > 0 else 'più larghi'}).")
        if abs(p.get("trim_end", 0)) > 0.05:
            L.append(f"- Le fini dei pezzi le sposta di {p['trim_end']:+.2f}s ({'più larghe' if p['trim_end'] > 0 else 'più strette'}).")
        kr = p.get("zoom_keep")
        if kr is not None:
            if kr < 0.6:
                L.append(f"- Zoom: ne tiene solo il {round(kr * 100)}%, mettine meno e solo nei momenti forti.")
            elif kr > 0.9:
                L.append("- Zoom: li tiene quasi tutti, vanno bene così.")
        if p.get("zoom_added_min", 0) > 0.5:
            L.append(f"- Aggiunge circa {p['zoom_added_min']:.1f} zoom al minuto che tu non avevi messo.")
        if p.get("zoom_scale"):
            L.append(f"- Forza dello zoom che preferisce: circa {round(p['zoom_scale'])}%.")
        if p.get("removed_text"):
            L.append("- Esempi di cose che Jake ha TOLTO (evitale): " + " | ".join(f'"{t[:90]}"' for t in p["removed_text"][:12]))
        if p.get("added_text"):
            L.append("- Esempi di cose che Jake ha RIMESSO (non toglierle): " + " | ".join(f'"{t[:90]}"' for t in p["added_text"][:6]))
        if p.get("zoom_added_text"):
            L.append("- Frasi dove Jake ha AGGIUNTO uno zoom: " + " | ".join(f'"{t[:80]}"' for t in p["zoom_added_text"][:8]))
    if kind in ("clips", "fc") and p.get("n_clip"):
        if abs(p.get("clip_trim_start", 0)) > 0.4:
            L.append(f"- Inizio delle clip: lo sposta di {p['clip_trim_start']:+.1f}s.")
        if abs(p.get("clip_trim_end", 0)) > 0.4:
            L.append(f"- Fine delle clip: la sposta di {p['clip_trim_end']:+.1f}s.")
        if p.get("clip_len"):
            L.append(f"- Durata finale media delle sue clip: {round(p['clip_len'])}s.")
        if p.get("cut_head"):
            L.append("- Inizi che ha tagliato via: " + " | ".join(f'"{t[:80]}"' for t in p["cut_head"][:5]))
        if p.get("cut_tail"):
            L.append("- Finali che ha tagliato via: " + " | ".join(f'"{t[:80]}"' for t in p["cut_tail"][:5]))
    return "\n".join(L) if len(L) > 2 else ""


def summary():
    """Per l'interfaccia."""
    p = get_profile()
    out = {"n_edit": p.get("n_edit", 0), "n_clip": p.get("n_clip", 0), "lines": []}
    t = prompt_text("fc")
    out["lines"] = [x[2:] for x in t.splitlines() if x.startswith("- ")]
    return out
