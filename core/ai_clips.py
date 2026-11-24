"""Clip virali scelte da Gemini: tra i momenti candidati trovati da Jarvis (picchi di voce, reazioni,
movimento) Gemini legge la trascrizione e sceglie i migliori (o quelli che chiedi tu), sistema inizio e
fine perché la clip parta col gancio e finisca sulla battuta, e scrive titolo e testo d'apertura."""
import json

from . import ai_edit

SYSTEM = """Sei l'esperto di short virali (TikTok, YouTube Shorts, Reels) di MrJake, streamer italiano di gaming.
Ti do dei momenti candidati presi da una sua live: per ognuno la finestra di tempo con la trascrizione (tempo di
inizio di ogni parola, secondi), i segnali misurati (urla, risate, azione) e la clip proposta (inizio/fine).
Scegli i migliori come short verticali: gancio nei primi 2 secondi, reazione genuina, comicità, tensione e
payoff, si capisce anche senza contesto. Se MrJake chiede un tipo preciso di momenti, scegli SOLO quelli.
Per ogni clip scelta dai:
- "id" del candidato, "score" 0-100 (quanto può andare virale),
- "start" e "end" in secondi DENTRO la finestra del candidato: inizia poco prima del gancio/della frase che
  introduce il momento e finisci subito dopo la battuta o la reazione (non a metà frase: ~0.1 s prima della
  prima parola, ~0.3 s dopo l'ultima). Durata tra MIN e MAX secondi indicati,
- "titolo" breve e accattivante per lo short, "hook" testo da sovraimpressione nei primi 2 secondi (anche
  con emoji), "motivo" una frase semplice sul perché.
Non scegliere momenti doppioni o sovrapposti. Ordina dal più virale."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {"clips": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": {"type": "INTEGER"}, "score": {"type": "INTEGER"}, "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
        "titolo": {"type": "STRING"}, "hook": {"type": "STRING"}, "motivo": {"type": "STRING"}},
        "required": ["id", "score", "start", "end"]}}},
    "required": ["clips"],
}


def pick(cands, opts, max_clips, min_len, max_len, dur, log=print):
    key = opts["gemini_key"].strip()
    want = (opts.get("clip_prompt") or "").strip()
    parts = []
    for c in cands:
        ws, we = c["_win"]
        sig = ", ".join(r["t"] for r in c.get("reasons", [])[:5])
        words = [w for w in c["_wwords"] if ws <= w["s"] <= we]
        parts.append(f"### Candidato {c['id']} · finestra {ws:.1f}-{we:.1f} s · clip proposta {c['start']:.1f}-{c['end']:.1f} s · "
                     f"punteggio automatico {c['score']} · segnali: {sig or '-'}\n{ai_edit.words_text(words) or '(nessun parlato)'}")
    text = (f"RICHIESTA DI MRJAKE: {want or 'trovami i momenti più virali'}\n"
            f"Quante clip al massimo: {max_clips} · durata MIN {min_len:.0f} s, MAX {max_len:.0f} s\n\n" + "\n\n".join(parts))
    body = {"system_instruction": {"parts": [{"text": SYSTEM + _stile("clips")}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.4}}
    res, last = None, None
    for m in ai_edit.best_models(key)[:6]:
        try:
            raw, _ = ai_edit._req(f"{ai_edit.API}/v1beta/models/{m}:generateContent", key, json.dumps(body).encode(),
                                  {"Content-Type": "application/json"}, "POST", timeout=600)
            d = json.loads(raw)
            res = ai_edit.loose_json("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]))
            log(f"Gemini ({m}) ha scelto {len(res.get('clips', []))} clip")
            break
        except RuntimeError as e:
            last = e
            if any(k in str(e).lower() for k in ("not found", "not supported", "no longer available", "deprecated", "not available", "high demand", "overloaded", "unavailable", "try again")):
                continue
            raise
    if res is None:
        raise last or RuntimeError("Gemini non risponde")
    by_id = {c["id"]: c for c in cands}
    out = []
    for o in res.get("clips", []):
        c = by_id.get(int(o.get("id", -1)))
        if not c or any(x is c for x in out):
            continue
        ws, we = c["_win"]
        st = max(ws, min(float(o.get("start", c["start"])), we - 1))
        en = max(st + 1, min(float(o.get("end", c["end"])), we))
        if en - st < min_len:
            en = min(dur, st + min_len)
        if en - st > max_len:
            en = st + max_len
        if any(min(en, x["end"]) - max(st, x["start"]) > 0.4 * (en - st) for x in out):
            continue
        c["score_regole"] = c["score"]
        c["score_ai"] = max(0, min(100, int(o.get("score", c["score"]))))
        c["score"] = c["score_ai"]
        c["start"], c["end"] = round(st, 2), round(en, 2)
        c["words"] = [w for w in c["_wwords"] if st <= w["s"] <= en]
        c["transcript"] = " ".join(w["w"] for w in c["words"])
        if o.get("titolo"):
            c["title"] = o["titolo"]
        c["hook"] = o.get("hook", "")
        if o.get("motivo"):
            c["reasons"].insert(0, {"t": "✨ " + o["motivo"], "p": 0})
        out.append(c)
        if len(out) >= max_clips:
            break
    if not out:
        raise RuntimeError("Gemini non ha scelto nessuna clip")
    return out


def _stile(kind):
    """Lo stile imparato dalle correzioni di Jake (vuoto se non c'è ancora o se è spento)."""
    try:
        from . import impara
        import json as _j, os as _o
        c = _o.path.join(_o.environ.get("LOCALAPPDATA", _o.path.expanduser("~")), "JarvisClipper", "config.json")
        if _o.path.exists(c) and _j.load(open(c, encoding="utf-8")).get("impara", True) is False:
            return ""
        return impara.prompt_text(kind)
    except Exception:
        return ""
