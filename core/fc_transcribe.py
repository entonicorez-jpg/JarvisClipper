"""Trascrizione per FilmCraft Jarvis: `JarvisClipper.exe --transcribe job.json out.json`.
job = {"path": video, "ranges": [[a, b], ...] (secondi del file), "lang": "it"}
out = {"words": [{"w", "s", "e"}], "model": ..., "gpu": bool}; mentre lavora scrive `out.json.progress`
("0.42 messaggio"). Usa la scheda NVIDIA e il modello più preciso che c'è (large-v3-turbo), con i
tempi veri di ogni parola."""
import json
import os

import numpy as np

from . import analyze, media, sidecar

HALLU = ("sottotitoli a cura", "sottotitoli creati", "amara.org", "qtss", "grazie per la visione", "iscriviti al canale")


def _progress(out, f, msg):
    try:
        with open(out + ".progress", "w", encoding="utf-8") as fh:
            fh.write(f"{f:.3f} {msg}")
    except Exception:
        pass


def _load(lang, out):
    """Con la scheda NVIDIA il modello più preciso (large-v3-turbo); senza, small sul processore."""
    try:
        _progress(out, 0.0, "Carico il modello di trascrizione (la prima volta lo scarica, ~1,5 GB)…")
        from . import gpu
        if gpu.has_nvidia():
            from faster_whisper import WhisperModel
            tr = analyze.Transcriber("large-v3-turbo", lang, lambda *a: None)
            tr.add_nvidia_dlls()
            tr.model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
            list(tr.model.transcribe((np.random.randn(16000 * 2) * 0.05).astype(np.float32), language=lang, without_timestamps=True)[0])
            tr.gpu = True
            return tr
    except Exception:
        pass
    tr = analyze.Transcriber("small", lang, lambda *a: None)
    tr.load_cpu()
    return tr


# contesto per Whisper: parlato veloce e informale di una live, con parolacce e inglesismi del gaming
PROMPT = {"it": "Live di uno streamer italiano che parla veloce e in modo informale, con parole inglesi del gaming."}


def _clean(words):
    out = []
    n = len(words)
    kill = [False] * n
    for i in range(n):
        acc = ""
        for j in range(i, min(n, i + 8)):
            acc += " " + words[j]["w"].lower()
            if any(h in acc for h in HALLU):
                for k in range(i, j + 1):
                    kill[k] = True
                break
    for i, w in enumerate(words):
        if not kill[i]:
            out.append(w)
    return out


def run(job_path, out):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    path, lang = job["path"], job.get("lang", "it")
    ranges = sorted([(float(a), float(b)) for a, b in job.get("ranges", [])])
    if not ranges:
        info = media.probe(path)
        ranges = [(0.0, float(info["dur"]))]
    tr = _load(lang, out)
    total = sum(b - a for a, b in ranges) or 1.0
    done, words = 0.0, []
    for a, b in ranges:
        t = a
        while t < b - 0.05:
            d = min(240.0, b - t)
            _progress(out, done / total, f"Trascrivo con {'la scheda video' if tr.gpu else 'il processore'}… {int(done / total * 100)}%")
            r = media.subprocess.run([media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                                      "-t", f"{d:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-af", "highpass=f=80,dynaudnorm=f=150:g=15",
                                      "-f", "f32le", "-"], capture_output=True, creationflags=media.CREATE_NO_WINDOW)
            audio = np.frombuffer(r.stdout, np.float32)
            if len(audio) > 3200:
                segs, _ = tr.model.transcribe(audio, language=lang, word_timestamps=True, beam_size=5,
                                              vad_filter=True, vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 120},
                                              condition_on_previous_text=False, no_speech_threshold=0.5,
                                              initial_prompt=PROMPT.get(lang))
                part = []
                for s in segs:
                    for w in s.words or []:
                        txt = w.word.strip()
                        if txt and getattr(w, "probability", 1.0) > 0.08:
                            part.append({"w": txt, "s": round(t + w.start, 3), "e": round(t + w.end, 3)})
                words += part
                sidecar.save(path, [{"words": part}], t, t + d, hq=bool(tr.gpu))
            done += d
            t += d
    words = _clean(words)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"words": words, "model": tr.name, "gpu": bool(tr.gpu)}, f, ensure_ascii=False)
    _progress(out, 1.0, "Fatto")
    return True
