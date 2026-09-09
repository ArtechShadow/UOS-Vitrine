"""Windows desktop shell around the local dashboard, without a browser tab."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import threading


def run_desktop(*, port=8765, only=None):
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator
    from .serve import serve, VitrineHandler

    ready = queue.Queue()

    def start_service():
        try:
            serve(host="127.0.0.1", port=port, only=only,
                  on_ready=lambda url, server: ready.put((url, server)))
        except Exception as exc:
            ready.put(exc)

    worker = threading.Thread(target=start_service, daemon=True)
    worker.start()
    result = ready.get(timeout=30)
    if isinstance(result, Exception):
        raise result
    url, server = result
    storage = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Vitrine" / "WebView"
    storage.mkdir(parents=True, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    window = webview.create_window("Vitrine", url, width=1440, height=960,
                                   min_size=(720, 540), background_color="#181D20")

    def closing():
        if any(p.poll() is None for p in list(VitrineHandler.capture_processes.values()) + list(VitrineHandler.object_processes.values()) + list(VitrineHandler.mesh_processes.values())):
            return window.create_confirmation_dialog(
                "Reconstruction is running",
                "Close the window? Processing will continue. Reopen Vitrine to see progress.")
        return True

    window.events.closing += closing
    try:
        menu = [Menu("Vitrine", [
            MenuAction("Capture library", lambda: window.load_url(url)),
            MenuAction("Back", lambda: window.evaluate_js("history.back()")),
            MenuAction("Reload", lambda: window.evaluate_js("location.reload()")),
            MenuAction("Toggle fullscreen", window.toggle_fullscreen),
            MenuSeparator(),
            MenuAction("Close", window.destroy),
        ])]
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage), menu=menu)
    finally:
        server.shutdown()
        worker.join(timeout=5)
    return 0
