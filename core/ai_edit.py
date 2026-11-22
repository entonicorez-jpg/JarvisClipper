"""Montaggio a parole con Gemini: tu dici (o scrivi) come vuoi il video, Gemini legge la trascrizione
con i tempi di ogni parola, i punti dove alzi la voce e il montaggio attuale (e, se vuoi, guarda una
versione piccola del video) e risponde con il piano: pezzi da tenere, zoom, censure."""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com"
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview"]
_BEST = {}


def best_models(key):
    """I modelli Flash che la tua chiave può usare, dal più nuovo (chiesti a Google, così non invecchia)."""
    if key in _BEST:
        return _BEST[key]
    import re
    found = []
    try:
        tok = ""
        for _ in range(5):
            raw, _ = _req(f"{API}/v1beta/models?pageSize=200" + (f"&pageToken={tok}" if tok else ""), key, timeout=30)
            d = json.loads(raw)
            for m in d.get("models", []):
                n = m.get("name", "").split("/")[-1]
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                if not re.fullmatch(r"gemini-\d+(\.\d+)?-flash(-preview)?(-\d\d-\d{4})?", n):
                    continue
                v = re.match(r"gemini-(\d+(?:\.\d+)?)", n).group(1)
                found.append((float(v), "preview" not in n, n))
            tok = d.get("nextPageToken", "")
            if not tok:
                break
    except Exception:
        pass
    found.sort(reverse=True)
    names = [n for _, _, n in found] or MODELS
    _BEST[key] = names
    return names

SYSTEM = """Sei l'editor video di MrJake, streamer italiano (Kick/TikTok). Ricevi la trascrizione di un pezzo di live
con i tempi di ogni parola (secondi assoluti del file), i picchi di voce (dove alza la voce/urla/ride), il montaggio
attuale fatto in automatico e le istruzioni di MrJake. Devi restituire il montaggio NUOVO come JSON.

Regole:
- "keep": i pezzi da TENERE, in ordine, senza sovrapposizioni, tutti dentro l'intervallo [inizio, fine] dato.
  Taglia sui confini delle parole: inizia ~0.08 s prima della prima parola e finisci ~0.12 s dopo l'ultima, così non
  si mangiano le sillabe. Niente pezzi più corti di 0.3 s. Se le istruzioni non parlano dei tagli, parti dal
  montaggio attuale e cambia solo quello che serve.
- "zooms": zoom sulla cam, ognuno DENTRO un pezzo tenuto (s >= inizio del pezzo, e <= fine del pezzo).
  "lv": 1 = zoom normale, 2 = zoom forte / cam piena (reazioni grosse). "fx": "blur" = entra con motion blur
  (reazioni), "cut" = stacco netto (ritmo mentre parla). Durata tipica 1–4 s.
- "censor": parolacce/bestemmie da coprire col bip, [inizio, fine] della parola (solo se lo chiede o se il
  montaggio attuale le censura già).
- "note": 1–3 frasi in italiano, semplici, su cosa hai fatto.
Segui le istruzioni di MrJake alla lettera; quando sono vaghe, fai scelte da short virale (ritmo, niente tempi
morti, zoom sulle reazioni vere, non su tutto)."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "keep": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "NUMBER"}}},
        "zooms": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "s": {"type": "NUMBER"}, "e": {"type": "NUMBER"}, "lv": {"type": "INTEGER"}, "fx": {"type": "STRING"}},
            "required": ["s", "e"]}},
        "censor": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "NUMBER"}}},
        "note": {"type": "STRING"},
    },
    "required": ["keep", "zooms", "note"],
}


def _req(url, key, data=None, headers=None, method=None, timeout=300):
    h = {"x-goog-api-key": key}
    h.update(headers or {})
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:300]
        raise RuntimeError(f"Gemini: {msg}") from None


def upload_video(path, key, log=print):
    """Carica il file con la Files API e aspetta che sia pronto. Ritorna (uri, mime)."""
    size = os.path.getsize(path)
    _, hd = _req(f"{API}/upload/v1beta/files", key, json.dumps({"file": {"display_name": "jarvis-proxy"}}).encode(),
                 {"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                  "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": "video/mp4",
                  "Content-Type": "application/json"}, "POST")
    up = {k.lower(): v for k, v in hd.items()}.get("x-goog-upload-url")
    if not up:
        raise RuntimeError("Gemini: caricamento del video non partito")
    with open(path, "rb") as f:
        body, _ = _req(up, key, f.read(), {"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                            "X-Goog-Upload-Command": "upload, finalize"}, "POST", timeout=900)
    info = json.loads(body)["file"]
    name, uri = info["name"], info["uri"]
    for _ in range(120):
        if info.get("state") == "ACTIVE":
            return uri, info.get("mimeType", "video/mp4")
        if info.get("state") == "FAILED":
            raise RuntimeError("Gemini non è riuscito a leggere il video")
        time.sleep(3)
        info = json.loads(_req(f"{API}/v1beta/{name}", key)[0])
    raise RuntimeError("Gemini ci mette troppo a preparare il video")


def words_text(words):
    """Una riga ogni ~8 parole: '12.34 ciao 12.60 ragazzi …' (+ la fine dell'ultima)."""
    out, line = [], []
    for i, w in enumerate(words):
        line.append(f"{w['s']:.2f} {w['w']}")
        if len(line) >= 8 or i == len(words) - 1:
            out.append(" ".join(line) + f" |{w['e']:.2f}")
            line = []
    return "\n".join(out)


def plan(key, model, instructions, rs, re_, words, peaks, ed, video=None, log=print):
    cur_keep = "\n".join(f"{a:.2f}-{b:.2f}" for a, b in ed.get("keep", []))
    cur_zoom = "\n".join(f"{z['s']:.2f}-{z['e']:.2f} lv{z.get('lv', 1)} {z.get('fx', 'blur')}" for z in ed.get("zooms", []))
    cur_cens = ", ".join(f"{c['s']:.2f}-{c['e']:.2f}" for c in ed.get("censor", []) if c.get("on", True))
    pk = ", ".join(f"{t:.1f}s(+{db:.0f}dB)" for t, db in peaks)
    text = (f"ISTRUZIONI DI MRJAKE:\n{instructions.strip()}\n\n"
            f"INTERVALLO: inizio {rs:.2f} s, fine {re_:.2f} s (durata {re_ - rs:.0f} s)\n\n"
            f"MONTAGGIO ATTUALE — pezzi tenuti:\n{cur_keep or '(nessuno)'}\nzoom:\n{cur_zoom or '(nessuno)'}\n"
            f"censure: {cur_cens or '(nessuna)'}\n\n"
            f"PICCHI DI VOCE (secondo, dB sopra il parlato normale): {pk or '(nessuno)'}\n\n"
            f"TRASCRIZIONE (tempo di inizio di ogni parola; dopo | la fine dell'ultima della riga):\n{words_text(words) or '(nessun parlato)'}")
    parts = []
    if video:
        parts.append({"file_data": {"file_uri": video[0], "mime_type": video[1]}})
        text = f"Il video allegato inizia al secondo {rs:.2f} del file (aggiungi {rs:.2f} ai tempi del video).\n\n" + text
    parts.append({"text": text})
    body = {"system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.3}}
    last = None
    cands = ([model] if model else []) + [x for x in best_models(key) if x != model]
    for m in cands[:6]:
        try:
            raw, _ = _req(f"{API}/v1beta/models/{m}:generateContent", key, json.dumps(body).encode(),
                          {"Content-Type": "application/json"}, "POST", timeout=600)
            d = json.loads(raw)
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            return loose_json(txt), m
        except RuntimeError as e:
            last = e
            low = str(e).lower()
            if any(k in low for k in ("not found", "not supported", "no longer available", "deprecated", "is not available", "not available to new")):
                log(f"Modello {m} non disponibile, provo il prossimo")
                continue
            raise
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            last = RuntimeError(f"Gemini ha risposto in modo strano: {e}")
    raise last or RuntimeError("Gemini non risponde")


def loose_json(txt):
    """JSON di Gemini anche se tagliato a metà (risposta troppo lunga): tiene le parti complete."""
    try:
        return json.loads(txt)
    except Exception:
        pass
    t = txt.strip()
    # taglia all'ultimo oggetto completo e chiude le parentesi rimaste aperte
    for cut in range(len(t), 0, -1):
        if t[cut - 1] not in "}]":
            continue
        head = t[:cut]
        stack, instr, esc = [], False, False
        for ch in head:
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
            elif ch == '"':
                instr = True
            elif ch in "[{":
                stack.append("]" if ch == "[" else "}")
            elif ch in "]}" and stack:
                stack.pop()
        if instr:
            continue
        try:
            return json.loads(head + "".join(reversed(stack)))
        except Exception:
            continue
    raise RuntimeError("Gemini ha risposto in modo incompleto: riprova con una richiesta più corta")


def clean(res, rs, re_):
    """Mette in ordine la risposta: pezzi dentro l'intervallo, zoom dentro i pezzi."""
    keep = []
    for k in res.get("keep") or []:
        if len(k) < 2:
            continue
        a, b = max(rs, float(k[0])), min(re_, float(k[1]))
        if b - a >= 0.3:
            keep.append([round(a, 3), round(b, 3)])
    keep.sort()
    merged = []
    for a, b in keep:
        if merged and a <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    zooms = []
    for z in res.get("zooms") or []:
        try:
            s, e = float(z["s"]), float(z["e"])
        except Exception:
            continue
        for a, b in merged:  # dentro il pezzo che lo contiene
            if a - 0.05 <= s < b:
                s, e = max(a, s), min(b, e)
                break
        else:
            continue
        if e - s >= 0.3:
            zooms.append({"id": len(zooms) + 1, "s": round(s, 3), "e": round(e, 3),
                          "lv": 2 if int(z.get("lv", 1) or 1) >= 2 else 1, "fx": "cut" if z.get("fx") == "cut" else "blur"})
    censor = None
    if res.get("censor") is not None:
        censor = [{"s": float(c[0]), "e": float(c[1]), "w": "", "on": True} for c in res["censor"] if len(c) >= 2 and float(c[1]) > float(c[0])]
    return merged, zooms, censor, str(res.get("note", ""))[:600]
