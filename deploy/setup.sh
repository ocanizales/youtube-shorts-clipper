#!/usr/bin/env bash
# One-shot Linux/VPS setup for youtube-shorts-clipper. Idempotent.
# Needs ffmpeg on PATH (apt) — it does all the cutting/encoding.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo "[setup] ffmpeg missing — sudo apt install ffmpeg"; exit 1; }
PY="$(command -v python3.11 || command -v python3.12 || command -v python3)"
echo "[setup] using $PY"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt
# Optional spoken-word captions:
#   .venv/bin/python -m pip install faster-whisper

# YouTube upload (--draft) needs the gitignored Google OAuth file copied in by
# hand: client_secret_*.json (+ any cached token). Clipping works without it.
ls client_secret_*.json >/dev/null 2>&1 || echo "[setup] note: no client_secret_*.json — uploads disabled until you copy it over"

echo "[setup] done. Web app: .venv/bin/python serve.py   (localhost:5000)"
echo "[setup] CLI:     .venv/bin/python clipper.py <args>"
