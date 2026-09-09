# Vitrine desktop for Windows

Double-click the Vitrine desktop shortcut (`output/Vitrine.exe`). The launcher opens a dedicated Windows window with the existing interface rendered by Microsoft WebView2. It does not open a browser tab. This is a desktop shell, not a rewrite of the interface in Windows widgets, and is not yet a standalone installer.

Install reconstruction dependencies as usual, then install `requirements-desktop.txt` into the project environment. Microsoft Edge WebView2 Runtime must be installed. Rebuild the launcher using `powershell -File scripts/build_launcher.ps1`.

Manual launch: `.venv/Scripts/python.exe -m vitrine ui --desktop`. Browser mode remains available with `ui --open`. Desktop mode always binds to loopback, and uses the actual bound port when another dashboard is already running. Only one desktop instance should be opened at a time to avoid simultaneous capture management.

The Vitrine menu provides Library, Back, Reload, fullscreen and Close. Uploads use the embedded engine's file picker; downloads are enabled. Links remain inside the app; use Back or Capture library to return. Theme and browser storage persist under `%LOCALAPPDATA%/Vitrine/WebView`. No Python application API is exposed to page scripts.

Closing the window stops its dashboard server but does not terminate reconstruction subprocesses. The app asks before closing while a dashboard-launched capture is active. On reopening, such jobs are monitored through their recorded progress and heartbeats rather than a retained process handle. Avoid restarting Windows during reconstruction.

Launcher errors are recorded in `output/desktop-launcher.log`. Native window startup and Python syntax were verified on this PC. Real-data WebGL rendering, file upload/download dialogs and Parsec interaction still need an end-to-end rehearsal. The environment and source tree remain required; the EXE does not bundle Python, CUDA, Docker or training models.
