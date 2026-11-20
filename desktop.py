"""Jarvis Clipper come app desktop: server interno + finestra propria (Edge WebView2), niente browser."""
import os, socket, sys, threading, traceback

# Nell'app senza console stdout/stderr non esistono: li mandiamo su un file di log
_data = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JarvisClipper")
os.makedirs(_data, exist_ok=True)
if getattr(sys, "frozen", False) or sys.stdout is None:
    _log = open(os.path.join(_data, "jarvis.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

import app as jarvis  # noqa: E402
import webview  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def main():
    port = free_port()
    srv = make_server("127.0.0.1", port, jarvis.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    win = webview.create_window("Jarvis Clipper", f"http://127.0.0.1:{port}/", width=1440, height=920,
                                min_size=(1000, 680), background_color="#0b0d10")
    jarvis.WINDOW = win
    webview.start()
    os._exit(0)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import selftest
        out = sys.argv[sys.argv.index("--selftest") + 1]
        os._exit(0 if selftest.run(out) else 1)
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, traceback.format_exc()[-900:], "Jarvis Clipper - errore", 0x10)
        except Exception:
            pass
        raise
