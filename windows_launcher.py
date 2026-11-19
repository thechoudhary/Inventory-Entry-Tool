"""Windows desktop launcher for the Inventory Entry & SKU Management app."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
VENV_DIR = APP_DIR / ".venv"
PYTHON = VENV_DIR / "Scripts" / "python.exe"
STREAMLIT = VENV_DIR / "Scripts" / "streamlit.exe"
URL = "http://localhost:8501"


def main() -> int:
    os.chdir(APP_DIR)
    if not (APP_DIR / "app.py").is_file():
        input("Could not find app.py. Place this launcher beside the app files. Press Enter to close.")
        return 1

    if not PYTHON.is_file():
        print("Setting up the app environment (first launch only)...")
        result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=APP_DIR)
        if result.returncode:
            input("Could not create the Python environment. Install Python 3.10+ and try again. Press Enter to close.")
            return result.returncode

    if not STREAMLIT.is_file():
        print("Installing app dependencies (first launch only)...")
        result = subprocess.run([str(PYTHON), "-m", "pip", "install", "--upgrade", "pip"], cwd=APP_DIR)
        if result.returncode == 0:
            result = subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", "requirements.txt"], cwd=APP_DIR)
        if result.returncode:
            input("Dependency installation failed. Check your internet connection and try again. Press Enter to close.")
            return result.returncode

    proc = subprocess.Popen(
        [str(PYTHON), "-m", "streamlit", "run", str(APP_DIR / "app.py"),
         "--server.headless=true", "--server.address=127.0.0.1", "--server.port=8501"],
        cwd=APP_DIR,
    )

    def open_when_ready() -> None:
        for _ in range(60):
            if proc.poll() is not None:
                return
            try:
                with urllib.request.urlopen(URL, timeout=1):
                    webbrowser.open(URL)
                    return
            except Exception:
                time.sleep(1)
        webbrowser.open(URL)

    threading.Thread(target=open_when_ready, daemon=True).start()
    print("Inventory app is running at", URL)
    print("Leave this window open while using the app. Close it to stop the app.")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
