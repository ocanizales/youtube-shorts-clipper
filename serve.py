"""
Local dev launcher: starts the job worker AND the web server together.

This keeps them as separate processes (the production model) while giving you a
single command for local use:  python serve.py  ->  http://localhost:5000

In production you run them independently and scale workers:
    python web/worker.py     (one per core/box)
    gunicorn web.app:app     (the stateless web tier)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    worker = subprocess.Popen([sys.executable, str(ROOT / "web" / "worker.py")])
    try:
        subprocess.run([sys.executable, str(ROOT / "web" / "app.py")])
    finally:
        worker.terminate()
