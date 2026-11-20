"""Analisi viralità: audio + movimento del gioco + parlato (Whisper) -> clip con punteggio 0-100."""
import json, re, threading, unicodedata, urllib.request
import numpy as np
from . import media

# ----------------------------------------------------------------- parole chiave
HYPE = ["oddio", "aiuto", "nooo", "noo", "ma che", "ma cosa", "ma come", "mamma mia", "raga", "ragazzi",
        "guarda", "guardate", "incredibile", "assurdo", "assurda", "pazzesco", "pazzesca", "impossibile",
        "sei serio", "ma sei", "basta", "scappa", "scappo", "corri", "corro", "vai vai", "daje", "godo",
        "goduria", "che schifo", "ho vinto", "abbiamo vinto", "ho perso", "sono morto", "morto", "che paura",
        "spavento", "oh mio dio", "non ci credo", "non e possibile", "cosa e successo", "che cosa",
        "fermo", "fermi", "aspetta", "ti prego", "vergogna", "leggendario", "leggenda", "epico", "gg",
        "bro", "fra", "che botta", "che colpo", "ha fatto", "ma perche", "porco", "porca", "madonna",
        "what", "wtf", "omg", "let's go", "lets go", "andiamo", "siii", "sii", "evvai", "grande"]
CLIP_WORDS = ["clippa", "clippate", "clippatelo", "clippala", "clip", "fate la clip", "registra"]
SWEARS = ["cazzo", "cazzi", "cazzata", "minchia", "merda", "stronzo", "stronza", "vaffanculo", "fanculo",
          "culo", "puttana", "troia", "coglione", "coglioni", "bastardo", "figa", "incazz", "porcodio",
          "porco dio", "dio cane", "diocane", "dio porco", "madonna", "fuck", "shit", "bitch"]
BLASPH = ["porcodio", "porco dio", "dio cane", "diocane", "dio porco", "porca madonna", "madonna puttana",
          "dio bestia", "dio boia", "porco gesu"]
LAUGH_RE = re.compile(r"\b(a?h(a|ah|ahah)+h?|(ah ?){3,}|(ha ?){3,}|(eh ?){3,}|ahaha\w*|hahaha\w*|ihih\w*)\b")


def norm_text(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9!? ']", " ", s)


def count_hits(text, words):
    n = 0
    for w in words:
        n += len(re.findall(r"(?<![a-z])" + re.escape(w), text))
    return n


def clip01(x):
    return float(max(0.0, min(1.0, x)))


# ----------------------------------------------------------------- segnali grezzi
def audio_energy(path, dur, cb=None, stop=None):
    """Loudness in dB ogni 0.5 s (streaming, poca RAM)."""
    sr, blk = 8000, 4000
    p = media.popen(["-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                     "-f", "s16le", "-"], stdout=media.subprocess.PIPE, stderr=media.subprocess.DEVNULL)
    out, total, buf = [], max(1, int(dur * 2)), b""
    while True:
        if stop and stop():
            p.kill(); raise RuntimeError("Annullato")
        chunk = p.stdout.read(blk * 2 * 64)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // (blk * 2)
        if n:
            a = np.frombuffer(buf[: n * blk * 2], dtype=np.int16).astype(np.float32).reshape(n, blk)
            buf = buf[n * blk * 2:]
            rms = np.sqrt((a ** 2).mean(axis=1)) / 32768.0
            out.extend((20 * np.log10(rms + 1e-6)).tolist())
            if cb and len(out) % 400 < n:
                cb(len(out) / total)
    p.wait()
    return np.maximum(np.array(out, dtype=np.float32), -70.0)


def motion_energy(path, dur, info, cam=None, cb=None, stop=None):
    """Quanto cambia il gioco tra un keyframe e l'altro (veloce: decodifica solo i keyframe)."""
    r = _motion(path, dur, info, cam, cb, stop, ["-fps_mode", "passthrough"])
    if r is None:  # ffmpeg vecchio senza -fps_mode
        r = _motion(path, dur, info, cam, cb, stop, ["-vsync", "0"])
    return r


def _motion(path, dur, info, cam, cb, stop, sync):
    W, H = 96, 54
    args = ["-hide_banner", "-skip_frame", "nokey", "-i", path, "-an", "-vf",
            f"scale={W}:{H},format=gray,showinfo"] + sync + ["-f", "rawvideo", "-"]
    p = media.popen(args, stdout=media.subprocess.PIPE, stderr=media.subprocess.PIPE)
    times = []

    def read_err():
        for line in iter(p.stderr.readline, b""):
            m = re.search(rb"pts_time:([\d.]+)", line)
            if m:
                times.append(float(m.group(1)))
    th = threading.Thread(target=read_err, daemon=True); th.start()
    mask = np.ones((H, W), bool)
    if cam:
        x0 = int(cam["x"] / info["w"] * W); y0 = int(cam["y"] / info["h"] * H)
        x1 = int((cam["x"] + cam["w"]) / info["w"] * W) + 1; y1 = int((cam["y"] + cam["h"]) / info["h"] * H) + 1
        mask[max(0, y0):y1, max(0, x0):x1] = False
    prev, vals, i = None, [], 0
    while True:
        if stop and stop():
            p.kill(); raise RuntimeError("Annullato")
        fr = p.stdout.read(W * H)
        if len(fr) < W * H:
            break
        f = np.frombuffer(fr, np.uint8).astype(np.float32)
        f = f.reshape(H, W)
        vals.append(0.0 if prev is None else float(np.abs(f - prev)[mask].mean()))
        prev = f; i += 1
        if cb and i % 200 == 0 and times:
            cb(times[-1] / max(1, dur))
    p.wait(); th.join(timeout=2)
    n = min(len(times), len(vals))
    if n < 3:
        return None
    t = np.array(times[:n]); v = np.array(vals[:n])
    grid = np.arange(int(dur) + 1)
    return np.interp(grid, t, v).astype(np.float32)


# ----------------------------------------------------------------- statistiche
def robust_z(x):
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826 + 1e-6
    return (x - med) / mad, mad


def rolling_median(x, win):
    if len(x) < win:
        return np.full_like(x, np.median(x))
    from numpy.lib.stride_tricks import sliding_window_view
    pad = win // 2
    xp = np.pad(x, pad, mode="edge")
    step = 5  # calcola ogni 5 punti e interpola (veloce anche su VOD di 6 ore)
    idx = np.arange(0, len(x), step)
    sw = sliding_window_view(xp, win)
    med = np.median(sw[idx], axis=1)
    return np.interp(np.arange(len(x)), idx, med).astype(np.float32)


def smooth(x, k):
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


# ----------------------------------------------------------------- Whisper
class Transcriber:
    def __init__(self, model_name="small", lang="it", log=print):
        self.model, self.lang, self.log, self.name = None, lang, log, model_name

    @staticmethod
    def add_nvidia_dlls():
        from . import gpu
        gpu.add_dll_dirs()

    def load_cpu(self):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.name, device="cpu", compute_type="int8")
        self.gpu = False
        self.log("Whisper su CPU (processore)")

    def load(self):
        from faster_whisper import WhisperModel
        self.gpu = False
        try:
            self.add_nvidia_dlls()
            self.model = WhisperModel(self.name, device="cuda", compute_type="float16")
            noise = (np.random.randn(16000 * 3) * 0.05).astype(np.float32)
            segs, _ = self.model.transcribe(noise, language=self.lang, without_timestamps=True)
            list(segs)  # forza davvero il calcolo sulla GPU
            self.gpu = True
            self.log("Whisper su GPU")
        except Exception as e:
            from . import gpu
            if gpu.has_nvidia() and not gpu.installed():
                self.log("Hai una scheda NVIDIA: vai in Impostazioni > Attiva GPU per un'analisi molto più veloce")
            else:
                self.log(f"GPU non utilizzabile ({str(e)[:80]}), uso il processore")
            self.model = None
            self.load_cpu()

    def transcribe(self, path, start, dur, beam=1):
        try:
            return self._transcribe(path, start, dur, beam)
        except Exception as e:
            if self.gpu:
                self.log(f"GPU in errore ({str(e)[:80]}), passo al processore")
                self.model = None
                self.load_cpu()
                return self._transcribe(path, start, dur, beam)
            raise

    def _transcribe(self, path, start, dur, beam=1):
        r = media.subprocess.run([media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}",
                                  "-i", path, "-t", f"{dur:.2f}", "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                                 capture_output=True, creationflags=media.CREATE_NO_WINDOW)
        audio = np.frombuffer(r.stdout, np.float32)
        if len(audio) < 1600:
            return []
        segs, _ = self.model.transcribe(audio, language=self.lang, word_timestamps=True, vad_filter=True,
                                        beam_size=beam, condition_on_previous_text=False)
        out = []
        for s in segs:
            words = [{"w": w.word.strip(), "s": start + w.start, "e": start + w.end} for w in (s.words or [])]
            out.append({"s": start + s.start, "e": start + s.end, "text": s.text.strip(), "words": words})
        return out


# ----------------------------------------------------------------- AI opzionale (Claude)
def ai_rerank(clips, api_key, model, log=print):
    items = []
    for c in clips:
        items.append({"id": c["id"], "durata_s": round(c["end"] - c["start"]),
                      "segnali": [r["t"] for r in c["reasons"]], "trascrizione": c["transcript"][:900]})
    prompt = (
        "Sei un esperto di short virali (TikTok, YouTube Shorts, Reels) per streamer italiani di gaming. "
        "Ti do dei momenti candidati presi da una live, con la trascrizione del parlato dello streamer e i segnali "
        "misurati (urla, risate, azione). Per ognuno valuta quanto può andare virale come short verticale: "
        "hook nei primi secondi, reazione genuina, comicità, tensione/payoff, comprensibile senza contesto. "
        "Rispondi SOLO con JSON: una lista di oggetti {\"id\":..., \"score\": 0-100, \"titolo\": \"titolo breve per lo short\", "
        "\"hook\": \"testo da mettere in sovraimpressione nei primi 2 secondi\", \"motivo\": \"una frase\"}.\n\n"
        + json.dumps(items, ensure_ascii=False))
    body = json.dumps({"model": model, "max_tokens": 4000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", []))
    m = re.search(r"\[.*\]", text, re.S)
    res = {str(o["id"]): o for o in json.loads(m.group(0))} if m else {}
    for c in clips:
        o = res.get(str(c["id"]))
        if not o:
            continue
        c["score_regole"] = c["score"]
        c["score_ai"] = int(o.get("score", c["score"]))
        c["score"] = int(round(0.5 * c["score"] + 0.5 * c["score_ai"]))
        c["title"] = o.get("titolo") or c["title"]
        c["hook"] = o.get("hook", "")
        if o.get("motivo"):
            c["reasons"].insert(0, {"t": "🤖 " + o["motivo"], "p": 0})
    log(f"AI: rivalutate {len(res)} clip")


# ----------------------------------------------------------------- pipeline
def analyze(path, info, layout, opts, progress, log, stop=lambda: False):
    """progress(frazione, messaggio). Ritorna lista di clip ordinate per punteggio."""
    dur = info["dur"]
    cam = layout.get("cam") if layout else None
    max_clips = int(opts.get("max_clips", 15))
    min_len, max_len = float(opts.get("min_len", 15)), float(opts.get("max_len", 59))

    # 1) segnali audio + movimento in parallelo
    res = {}
    prog = {"a": 0.0, "m": 0.0}

    def upd():
        progress(0.02 + 0.38 * (0.6 * prog["a"] + 0.4 * prog["m"]), "Ascolto l'audio e guardo il gioco…")

    def ta():
        res["a"] = audio_energy(path, dur, lambda f: (prog.__setitem__("a", f), upd()), stop)

    def tm():
        try:
            res["m"] = motion_energy(path, dur, info, cam, lambda f: (prog.__setitem__("m", f), upd()), stop)
        except Exception as e:
            log(f"Movimento non disponibile: {e}"); res["m"] = None
        prog["m"] = 1.0
    threads = [threading.Thread(target=ta), threading.Thread(target=tm)]
    [t.start() for t in threads]; [t.join() for t in threads]
    if stop():
        raise RuntimeError("Annullato")
    if "a" not in res or len(res["a"]) < 10:
        raise RuntimeError("Il video non ha audio utilizzabile.")
    half = res["a"]
    n = len(half) // 2
    L = half[: n * 2].reshape(n, 2).max(axis=1)  # dB per secondo (picco)
    Lm = half[: n * 2].reshape(n, 2).mean(axis=1)

    zg, mad = robust_z(L)
    zl = (L - rolling_median(L, 301)) / mad
    z = 0.5 * zg + 0.5 * zl
    before = np.array([Lm[max(0, i - 12):max(1, i - 2)].mean() for i in range(n)])
    after = np.array([Lm[i:i + 3].mean() for i in range(n)])
    contrast = (after - before) / mad
    M = res.get("m")
    if M is not None and len(M) >= n:
        Mz, _ = robust_z(M[:n])
    else:
        Mz = np.zeros(n, np.float32)
    signal = smooth(np.maximum(z, 0) + 0.4 * np.maximum(contrast, 0) + 0.3 * np.maximum(Mz, 0), 3)

    # 2) candidati = picchi
    n_cand = int(min(70, max(12, max_clips * 2.5, dur / 60 * 0.5)))
    s = signal.copy(); s[:5] = 0
    cands = []
    while len(cands) < n_cand:
        p = int(np.argmax(s))
        if s[p] <= 0.3:
            break
        cands.append(p)
        s[max(0, p - 40):p + 40] = 0
    if not cands:
        cands = [int(np.argmax(signal))]
    log(f"{len(cands)} momenti candidati")

    # 3) trascrizione dei candidati
    tr = None
    if opts.get("whisper", True):
        progress(0.42, "Carico Whisper (la prima volta scarica il modello)…")
        try:
            tr = Transcriber(opts.get("model", "small"), opts.get("lang", "it"), log)
            tr.load()
        except Exception as e:
            log(f"Whisper non disponibile, vado solo con audio/gioco: {e}")
            tr = None
    beam = 5 if opts.get("model") in ("medium", "large-v3") else 1

    clips = []
    for k, p in enumerate(cands):
        if stop():
            raise RuntimeError("Annullato")
        progress(0.45 + 0.45 * k / len(cands), f"Capisco cosa succede nel momento {k + 1}/{len(cands)}…")
        # picco preciso
        lo, hi = max(0, p - 4), min(n, p + 5)
        p = lo + int(np.argmax(L[lo:hi]))
        ws, we = max(0.0, p - 35.0), min(dur, p + 25.0)
        segs = []
        if tr:
            try:
                segs = tr.transcribe(path, ws, we - ws, beam)
            except Exception as e:
                log(f"Errore trascrizione a {media.tc(p)}: {e}")
        pre, post = float(opts.get("pre", 14)), float(opts.get("post", 8))
        st_t, en_t = p - pre, p + post
        starts = [x["s"] for x in segs if p - 28 <= x["s"] <= p - 3]
        ends = [x["e"] for x in segs if p + 2 <= x["e"] <= p + 22]
        st = min(starts, key=lambda v: abs(v - st_t)) - 0.3 if starts else st_t
        en = min(ends, key=lambda v: abs(v - en_t)) + 0.6 if ends else en_t
        if en - st < min_len:
            en = st + min_len
        if en - st > max_len:
            st = en - max_len
        st, en = max(0.0, st), min(dur, en)
        clips.append(score_clip(len(clips) + 1, p, st, en, segs, L, z, contrast, Mz, n, bool(tr)))

    # 4) togli i doppioni e ordina
    clips.sort(key=lambda c: -c["score"])
    kept = []
    for c in clips:
        if all(min(c["end"], o["end"]) - max(c["start"], o["start"]) < 0.4 * (c["end"] - c["start"]) for o in kept):
            kept.append(c)
    kept = kept[:max_clips]

    if opts.get("api_key"):
        progress(0.92, "Chiedo un parere all'AI…")
        try:
            ai_rerank(kept, opts["api_key"], opts.get("ai_model", "claude-sonnet-5-5"), log)
            kept.sort(key=lambda c: -c["score"])
        except Exception as e:
            log(f"AI non disponibile: {e}")
    for i, c in enumerate(kept):
        c["id"] = i + 1
    progress(1.0, "Fatto")
    return kept


def score_clip(cid, p, st, en, segs, L, z, contrast, Mz, n, has_tr):
    a, b = int(st), max(int(st) + 1, min(n, int(en)))
    peak_z = float(z[a:b].max())
    burst = int((z[a:b] > 2.5).sum())
    pay = float(contrast[max(0, p - 3):p + 4].max())
    mot = float(np.mean(Mz[a:b]))
    hook_z = float(z[a:min(b, a + 3)].max())

    words = [w for s in segs for w in s["words"] if st <= w["s"] <= en]
    text_raw = " ".join(s["text"] for s in segs if s["e"] >= st and s["s"] <= en)
    text = norm_text(text_raw)
    hype = count_hits(text, HYPE)
    laughs = len(LAUGH_RE.findall(text))
    swears = count_hits(text, SWEARS)
    blas = count_hits(text, BLASPH)
    clipc = count_hits(text, CLIP_WORDS)
    excl = text_raw.count("!")
    wps = len(words) / max(1.0, en - st)
    toks = text.split()
    rep = any(toks[i] == toks[i + 1] == toks[i + 2] for i in range(len(toks) - 2))

    parts = [
        (28 * clip01((peak_z - 1.5) / 4.5), f"🔊 Reazione forte / urlo a {media.tc(p)}"),
        (8 * clip01(burst / 8), "🌪️ Caos prolungato (tanti secondi sopra le righe)"),
        (14 * clip01((pay - 1.0) / 4), "💥 Calma → esplosione (setup e payoff)"),
        (8 * clip01(mot / 2.5), "🎮 Tanta azione nel gioco"),
        (6 * clip01((hook_z - 1) / 3), "🎣 Parte forte: hook nei primi 3 secondi"),
    ]
    if has_tr:
        parts += [
            (14 * clip01((hype + (2 if rep else 0)) / 5), "😱 Parole da reazione (\"ma che\", \"oddio\", \"nooo\"…)"),
            (10 * clip01(laughs / 2), "😂 Risate"),
            (6 if clipc else 0, "✂️ Hai detto \"clippate\" / clip"),
            (4 * clip01(swears / 2), "🤬 Momento \"spicy\" (parolacce)"),
            (4 * clip01((wps - 2) / 2) + 2 * clip01(excl / 3), "🗣️ Parlato energico"),
        ]
        total = sum(x for x, _ in parts)
    else:
        total = sum(x for x, _ in parts) * (100 / 64) * 0.9
    score = int(round(max(1, min(100, total))))
    reasons = [{"t": t, "p": round(x, 1)} for x, t in sorted(parts, key=lambda q: -q[0]) if x >= 2.5][:5]
    warns = []
    if blas:
        warns.append("⚠️ Bestemmia nella clip: su TikTok/YouTube rischi la rimozione, censurala")
    elif swears:
        warns.append("⚠️ Parolacce: valuta un bip")
    if not has_tr:
        warns.append("Trascrizione non disponibile: punteggio solo da audio e gioco")

    # titolo: la frase più "carica" vicino al picco
    title = ""
    if segs:
        near = [s for s in segs if s["e"] >= st and s["s"] <= en] or segs
        best = max(near, key=lambda s: (s["text"].count("!") + count_hits(norm_text(s["text"]), HYPE)) * 2
                   - abs((s["s"] + s["e"]) / 2 - p) / 10)
        title = best["text"][:70]
    return {"id": cid, "peak": p, "start": round(st, 2), "end": round(en, 2), "score": score,
            "reasons": reasons, "warnings": warns, "title": title or f"Momento a {media.tc(p)}", "hook": "",
            "transcript": text_raw.strip(), "words": words,
            "stats": {"peak_z": round(peak_z, 2), "payoff": round(pay, 2), "motion": round(mot, 2),
                      "hype": hype, "laughs": laughs, "swears": swears}}
