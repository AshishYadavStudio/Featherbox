"""Start the Featherbox backend (and open the UI in the browser)."""
import os
import sys
import threading
import webbrowser

from app import config

if sys.stdout is None or sys.stderr is None:
    # Started without a console (installed app via pythonw): keep a log instead.
    _log = open(config.LOGS / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

# The installed app ships the Microsoft C++ runtime next to python.exe; putting that folder first on
# PATH lets the downloaded engines (llama.cpp, stable-diffusion.cpp, ffmpeg) find it on clean PCs too.
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")

import uvicorn  # noqa: E402  (after the stdout fix so uvicorn's log handlers pick up the log file)

if __name__ == "__main__":
    url = f"http://{config.API_HOST}:{config.API_PORT}"
    if "--no-browser" not in sys.argv and config.FRONTEND_DIST.exists():
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"{config.APP_NAME} running at {url}", flush=True)
    uvicorn.run("app.main:app", host=config.API_HOST, port=config.API_PORT, log_level="warning")
