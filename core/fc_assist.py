"""Gemini dentro FilmCraft Jarvis.

- `JarvisClipper.exe --fc-assist job.json out.json`: job = {"prompt", "context"} (la timeline di FilmCraft:
  clip, tracce, marcatori, parole con i tempi). Gemini risponde con una lista di operazioni che FilmCraft
  esegue (tagli, zoom, scossoni, transizioni, titoli, sottotitoli, volume, marcatori).
- `JarvisClipper.exe --listen stop_file out.json`: registra il microfono finché compare `stop_file`, poi
  scrive {"text"} con quello che hai detto (Whisper sul PC)."""
import json
import os
import re
import subprocess
import time

from . import ai_edit, media

SYSTEM = """Sei l'assistente di montaggio dentro FilmCraft (un editor tipo Premiere) di MrJake, streamer italiano.
Ricevi la timeline attuale (tracce, clip con inizio/fine in secondi della sequenza, marcatori, indicatore,
e se c'è la trascrizione con i tempi di ogni parola) e una richiesta a parole. Rispondi con le operazioni da
fare, in ordine, usando SOLO questi tipi ("op"):
- "cut": toglie il tratto [start, end] da tutte le tracce e chiude il buco (come un ripple delete).
- "zoom": zoom sulla clip video in [start, end]; "amount" 1 = normale, 2 = forte. "smooth": true = zoom lento.
- "shake": scossone sulla clip video in [start, end].
- "transition": transizione al taglio più vicino a "time"; "preset" uno tra zoom_blur, whip, cross_zoom, spin,
  push, shake, glitch, flash, light_leak, vhs, dissolve.
- "title": titolo TikTok (testo bianco con bordo) a "time" per "seconds" secondi con "text" (anche emoji).
- "captions": sottotitoli animati solo in [start, end] ("style": jarvis, hormozi, parola, beast, box, neon,
  minimal; "anim": pop_osc, pop, pop_su, rimbalzo, scivola, tremolio, zoom, nessuna).
- "volume": cambia il volume delle clip audio in [start, end] di "db" decibel (+ alza, - abbassa).
- "marker": marcatore a "time" con "name".
- "sfx": suono a "time"; "name" = il nome (esatto o quasi) di uno dei SUONI DISPONIBILI (es. vine boom); "db" volume.
- "image": immagine/meme sopra al video da "start" a "end" (o a "time" per "seconds"); "name" = una delle
  IMMAGINI DISPONIBILI.
- "bw": bianco e nero sulla clip video in [start, end].
Per un flash bianco tra due pezzi usa "transition" con preset "flash".
Una richiesta lunga va spezzata in tante operazioni, una per ogni cosa chiesta, nell'ordine giusto, ma SENZA
ripetere operazioni uguali: di solito bastano poche operazioni (raramente più di 30). Non riscrivere la
timeline e non elencare cose già presenti.
Tempi in secondi della SEQUENZA (quelli della timeline che ricevi). Per tagliare sulle parole usa i tempi della
trascrizione (inizia ~0.08 s prima della parola, finisci ~0.12 s dopo). Se la richiesta parla di "qui" o
"adesso", usa l'indicatore. Se qualcosa non si può fare con queste operazioni, non inventare: dillo nella nota.
"note": 1–2 frasi in italiano semplici su cosa hai fatto."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "ops": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "op": {"type": "STRING"}, "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}, "time": {"type": "NUMBER"},
            "seconds": {"type": "NUMBER"}, "text": {"type": "STRING"}, "preset": {"type": "STRING"}, "style": {"type": "STRING"},
            "anim": {"type": "STRING"}, "amount": {"type": "NUMBER"}, "smooth": {"type": "BOOLEAN"}, "db": {"type": "NUMBER"},
            "name": {"type": "STRING"}}, "required": ["op"]}},
        "note": {"type": "STRING"},
    },
    "required": ["ops", "note"],
}


def _cfg():
    p = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "config.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _context_text(ctx):
    lines = [f"Sequenza: {ctx.get('name', '')} · durata {ctx.get('duration', 0):.2f} s · {ctx.get('width')}x{ctx.get('height')} · indicatore a {ctx.get('playhead', 0):.2f} s"]
    for tr in ctx.get("tracks", []):
        items = ", ".join(f"[{c['start']:.2f}-{c['end']:.2f}] {c['name'][:40]}" for c in tr.get("clips", [])[:400])
        lines.append(f"{tr['name']} ({tr['kind']}): {items or '(vuota)'}")
    if ctx.get("markers"):
        lines.append("Marcatori: " + ", ".join(f"{m['time']:.2f} {m.get('name', '')}" for m in ctx["markers"]))
    if ctx.get("sounds"):
        lines.append("SUONI DISPONIBILI: " + " | ".join(ctx["sounds"][:600]))
    else:
        lines.append("(Nessuna cartella di suoni scelta: per i suoni dì di aggiungerla nella scheda Gemini.)")
    if ctx.get("images"):
        lines.append("IMMAGINI DISPONIBILI: " + " | ".join(ctx["images"][:600]))
    words = ctx.get("words") or []
    if words:
        lines.append("TRASCRIZIONE (inizio di ogni parola in secondi della sequenza; dopo | la fine dell'ultima):")
        lines.append(ai_edit.words_text(words))
    else:
        lines.append("(Non c'è ancora la trascrizione: per tagli sulle parole chiedi di creare prima i sottotitoli.)")
    return "\n".join(lines)


def run_assist(job_path, out):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    c = _cfg()
    key = (c.get("gemini_key") or "").strip()
    if not key:
        raise RuntimeError("Metti la chiave di Google Gemini in Jarvis Clipper › Impostazioni")
    text = f"RICHIESTA DI MRJAKE:\n{job['prompt'].strip()}\n\nTIMELINE:\n{_context_text(job.get('context') or {})}"
    body = {"system_instruction": {"parts": [{"text": SYSTEM + _stile("fc")}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.2, "maxOutputTokens": 16384}}
    last = None
    for m in ([c.get("gemini_model")] if c.get("gemini_model") else []) + ai_edit.best_models(key)[:6]:
        try:
            raw, _ = ai_edit._req(f"{ai_edit.API}/v1beta/models/{m}:generateContent", key, json.dumps(body).encode(),
                                  {"Content-Type": "application/json"}, "POST", timeout=600)
            d = json.loads(raw)
            res = ai_edit.loose_json("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]))
            # niente doppioni e al massimo 80 operazioni (a volte Gemini si ripete)
            seen, ops = set(), []
            for o in res.get("ops", []):
                k = json.dumps(o, sort_keys=True)
                if k not in seen:
                    seen.add(k); ops.append(o)
            res["ops"] = ops[:80]
            res["model"] = m
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(res, fh, ensure_ascii=False)
            return True
        except RuntimeError as e:
            last = e
            low = str(e).lower()
            if any(k in low for k in ("not found", "not supported", "no longer available", "deprecated", "not available")):
                continue
            break
        except Exception as e:
            last = e
            break
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"error": str(last)[:400]}, fh, ensure_ascii=False)
    return True


def _mic_device():
    r = media.run(["-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"])
    devs = re.findall(r'"([^"]+)"\s*\(audio\)', r.stderr)
    return devs[0] if devs else None


def run_listen(stop_file, out):
    """Registra dal microfono predefinito finché compare stop_file, poi trascrive."""
    dev = _mic_device()
    if not dev:
        json.dump({"error": "Non trovo il microfono"}, open(out, "w", encoding="utf-8"))
        return True
    wav = out + ".wav"
    pr = subprocess.Popen([media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "dshow", "-i", f"audio={dev}",
                           "-ac", "1", "-ar", "16000", wav], stdin=subprocess.PIPE, creationflags=media.CREATE_NO_WINDOW)
    t0 = time.time()
    while not os.path.exists(stop_file) and time.time() - t0 < 300:
        time.sleep(0.15)
    try:
        pr.communicate(b"q", timeout=5)
    except Exception:
        pr.kill()
    from . import analyze
    tr = analyze.Transcriber(_cfg().get("model", "small"), "it", lambda *a: None)
    tr.load()
    dur = os.path.getsize(wav) / 32000.0 if os.path.exists(wav) else 1.0
    segs = tr.transcribe(wav, 0.0, max(1.0, dur + 1), 5 if getattr(tr, "gpu", False) else 1)
    json.dump({"text": " ".join(s["text"] for s in segs).strip()}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return True


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
