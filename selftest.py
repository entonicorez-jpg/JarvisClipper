"""Autotest dell'app costruita: crea un video finto, lo analizza, fa anteprima, render e XML,
carica Whisper (modello tiny) e controlla tutte le librerie. Usato dalla build su GitHub."""
import os, sys, tempfile, time, traceback


def run(out_path):
    lines, ok = [], True

    def step(name, fn):
        nonlocal ok
        t = time.time()
        try:
            r = fn()
            lines.append(f"OK   {name} ({time.time() - t:.1f}s) {r if r is not None else ''}")
        except Exception:
            ok = False
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    import app as jarvis
    from core import media, analyze, render, premiere, layout
    d = tempfile.mkdtemp()
    vid = os.path.join(d, "test.mp4")
    step("ffmpeg", lambda: media.ffmpeg_exe())
    step("crea video", lambda: media.run([
        "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=60", "-f", "lavfi", "-i",
        "sine=f=500:d=60:r=48000", "-filter_complex",
        "[1:a]volume='if(between(t,30,33),0.9,0.01)':eval=frame,aformat=channel_layouts=stereo[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac", vid]).returncode)
    info = {}
    step("probe", lambda: info.update(media.probe(vid)) or info)
    L = layout.default_layout(info, 0.4)
    clips = []
    step("analisi + whisper tiny", lambda: clips.extend(analyze.analyze(
        vid, info, L, {"max_clips": 3, "model": "tiny"}, lambda f, m: None, lambda m: lines.append("     log: " + m))) or len(clips))
    if any("Whisper non disponibile" in l or "Errore trascrizione" in l for l in lines):
        ok = False
        lines.append("FAIL whisper non si carica (vedi log sopra)")
    step("anteprima", lambda: render.render(vid, info, L, clips[0]["start"], clips[0]["end"], os.path.join(d, "p.mp4"), preview=True))
    step("render 1080x1920", lambda: render.render(vid, info, L, clips[0]["start"], clips[0]["end"], os.path.join(d, "f.mp4")))
    step("xml premiere", lambda: premiere.build_xml(clips, vid, info, L, {clips[0]["id"]: os.path.join(d, "f.mp4")}, os.path.join(d, "x.xml")))
    from core import autoedit
    cam = {"x": 900, "y": 400, "w": 360, "h": 270}
    ed = {}
    step("montaggio: analisi parte 10-50s", lambda: ed.update(autoedit.analyze_edit(vid, info, cam, {"ritmo": "normale", "range": [10, 50]}, log=lambda m: None)) or ed["report"])
    step("montaggio: zoom aggiunto", lambda: ed["zooms"].append({"id": 99, "s": 20.0, "e": 23.0, "lv": 2}) or len(ed["zooms"]))
    fx = {}
    step("montaggio: zoom con motion blur", lambda: fx.update(autoedit.render_fx(vid, info, cam, ed, os.path.join(d, "fx"))) or len(fx))
    step("montaggio: xml", lambda: premiere.build_autoedit_xml(ed, vid, info, cam, os.path.join(d, "ae.xml"), fx=fx))
    step("montaggio: mp4", lambda: media.probe(autoedit.render(vid, info, cam, ed, os.path.join(d, "ae.mp4")))["dur"])
    step("rilevamento voce (VAD)", lambda: len(autoedit.speech_segments(vid, 0, 20, "jumpcut")))
    from core import updater
    step("versione inclusa", lambda: updater.current_version() if updater.current_version() != "dev" else (_ for _ in ()).throw(RuntimeError("version.txt mancante")))
    step("autocam (opencv)", lambda: layout.auto_cam([], info))
    for mod in ("yt_dlp", "curl_cffi", "cv2", "webview", "faster_whisper", "ctranslate2", "flask"):
        step("import " + mod, lambda m=mod: __import__(m) and None)
    step("flask route", lambda: jarvis.app.test_client().get("/api/config").status_code)
    lines.append("RISULTATO: " + ("TUTTO OK" if ok else "CI SONO ERRORI"))
    open(out_path, "w", encoding="utf-8").write("\n".join(lines))
    return ok


if __name__ == "__main__":
    sys.exit(0 if run(sys.argv[1] if len(sys.argv) > 1 else "selftest.txt") else 1)
