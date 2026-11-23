"""Separa voce e musica (per FilmCraft Jarvis): `JarvisClipper.exe --separa job.json out.json`.

job = {"items": [{"path", "start", "dur", "voice", "music"}]} (secondi del file). Per ogni pezzo scrive
due WAV stereo 48 kHz: la voce e il resto (musica, gioco, effetti).

Modello: Kim_Vocal_2 (MDX-Net, UVR, licenza MIT), ONNX, scaricato la prima volta (circa 65 MB) in
%LOCALAPPDATA%\\JarvisClipper\\modelli. Gira con onnxruntime (già dentro Jarvis per faster-whisper).
"""
import json
import os
import subprocess
import urllib.request

import numpy as np

from . import media

MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx"
MODEL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper", "modelli")
MODEL = os.path.join(MODEL_DIR, "Kim_Vocal_2.onnx")
SR = 44100
N_FFT, HOP, DIM_F, DIM_T = 7680, 1024, 3072, 256
COMPENSATE = 1.009
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM
WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # hann periodica


def _progress(out, f, msg):
    try:
        with open(out + ".progress", "w", encoding="utf-8") as fh:
            json.dump({"f": round(float(f), 3), "msg": msg}, fh)
    except OSError:
        pass


def ensure_model(prog=lambda f, m: None):
    if os.path.exists(MODEL) and os.path.getsize(MODEL) > 50_000_000:
        return MODEL
    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = MODEL + ".part"
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "JarvisClipper"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        tot = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            got += len(b)
            if tot:
                prog(got / tot * 0.1, f"Scarico il modello per separare la voce (una volta sola)… {got * 100 // tot}%")
    os.replace(tmp, MODEL)
    return MODEL


def _stft(x):
    """x [n, CHUNK] → [n, 2(re,im), N_FFT//2+1, DIM_T], come torch.stft(center=True, reflect)."""
    p = N_FFT // 2
    xp = np.pad(x, ((0, 0), (p, p)), mode="reflect")
    idx = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None, :]
    frames = xp[:, idx] * WINDOW  # [n, T, N_FFT]
    spec = np.fft.rfft(frames, axis=-1)  # [n, T, bins]
    spec = np.transpose(spec, (0, 2, 1))  # [n, bins, T]
    return np.stack([spec.real, spec.imag], axis=1).astype(np.float32)


def _istft(s):
    """s [n, 2, bins, DIM_T] → [n, CHUNK] (inverso di _stft, overlap-add con normalizzazione)."""
    spec = s[:, 0] + 1j * s[:, 1]  # [n, bins, T]
    frames = np.fft.irfft(np.transpose(spec, (0, 2, 1)), n=N_FFT, axis=-1) * WINDOW  # [n, T, N_FFT]
    n = s.shape[0]
    L = N_FFT + HOP * (DIM_T - 1)
    out = np.zeros((n, L), np.float32)
    norm = np.zeros(L, np.float32)
    for t in range(DIM_T):
        out[:, t * HOP:t * HOP + N_FFT] += frames[:, t]
        norm[t * HOP:t * HOP + N_FFT] += WINDOW ** 2
    p = N_FFT // 2
    out = out[:, p:p + CHUNK] / np.maximum(norm[p:p + CHUNK], 1e-8)
    return out


def _run_model(sess, waves):
    """waves [2, CHUNK] stereo → voce [2, CHUNK]."""
    spec = _stft(waves)  # [2(canali), 2, bins, T]
    x = spec.reshape(1, 4, N_FFT // 2 + 1, DIM_T)[:, :, :DIM_F]
    y = sess.run(None, {"input": x})[0]
    full = np.zeros((1, 4, N_FFT // 2 + 1, DIM_T), np.float32)
    full[:, :, :DIM_F] = y
    return _istft(full.reshape(2, 2, N_FFT // 2 + 1, DIM_T))


def separate(mix, sess, prog=lambda f: None):
    """mix [2, n] a 44.1 kHz → (voce, resto)."""
    n = mix.shape[1]
    pad = GEN - n % GEN
    mp = np.concatenate([np.zeros((2, TRIM), np.float32), mix, np.zeros((2, pad + TRIM), np.float32)], axis=1)
    parts = []
    steps = list(range(0, n + pad, GEN))
    for k, i in enumerate(steps):
        w = mp[:, i:i + CHUNK]
        v = _run_model(sess, w)
        parts.append(v[:, TRIM:-TRIM])
        prog((k + 1) / len(steps))
    voice = np.concatenate(parts, axis=1)[:, :n] * COMPENSATE
    return voice, mix - voice


def _read(path, start, dur):
    r = subprocess.run([media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
                        "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, creationflags=media.CREATE_NO_WINDOW)
    a = np.frombuffer(r.stdout, np.float32)
    return a.reshape(-1, 2).T.copy() if a.size >= 2 else np.zeros((2, 0), np.float32)


def _write(path, a, dur):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    raw = np.clip(a.T, -1, 1).astype(np.float32).tobytes()
    tmp = path + ".part.wav"
    r = subprocess.run([media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-af", f"aresample=48000,apad,atrim=0:{dur:.4f}", "-c:a", "pcm_s16le", tmp],
                       input=raw, capture_output=True, creationflags=media.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-300:])
    os.replace(tmp, path)


def run(job_path, out):
    try:
        with open(job_path, encoding="utf-8") as f:
            job = json.load(f)
        items = job.get("items") or []
        if not items:
            raise RuntimeError("nessuna clip da separare")
        ensure_model(lambda f, m: _progress(out, f, m))
        import onnxruntime as ort
        provs = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in ort.get_available_providers()] + ["CPUExecutionProvider"]
        sess = ort.InferenceSession(MODEL, providers=provs)
        tot = sum(max(0.1, float(i["dur"])) for i in items)
        done = 0.0
        res = []
        for k, it in enumerate(items):
            dur = float(it["dur"])
            mix = _read(it["path"], float(it["start"]), dur)
            if mix.shape[1] < SR // 10:
                res.append({"clip": it.get("clip"), "error": "niente audio"})
                done += dur
                continue
            voice, rest = separate(mix, sess, lambda f: _progress(out, 0.1 + 0.9 * (done + f * dur) / tot,
                                                                  f"Separo voce e musica, clip {k + 1}/{len(items)}"))
            _write(it["voice"], voice, dur)
            _write(it["music"], rest, dur)
            res.append({"clip": it.get("clip"), "voice": it["voice"], "music": it["music"]})
            done += dur
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"items": res}, f, ensure_ascii=False)
        _progress(out, 1.0, "Fatto")
        return True
    except Exception as e:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"error": str(e)[:400]}, f, ensure_ascii=False)
        return False
