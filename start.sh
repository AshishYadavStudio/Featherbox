#!/usr/bin/env bash
# Featherbox on Linux: sets up Python + the web interface on first run, then starts the app and opens it
# in your browser (http://127.0.0.1:8420). Run it again to start Featherbox later.
#
# Needs: Python 3.10+ with venv, Node.js 18+ (first run only, to build the interface).
# Ubuntu / Debian:  sudo apt install python3 python3-venv nodejs npm ffmpeg
# AMD / Intel / NVIDIA graphics through Vulkan also need:  sudo apt install mesa-vulkan-drivers libvulkan1
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if [ ! -x backend/.venv/bin/python ]; then
  echo "[Featherbox] First run: creating the Python environment..."
  "$PY" -m venv backend/.venv
  backend/.venv/bin/python -m pip install --upgrade pip
  backend/.venv/bin/python -m pip install -r backend/requirements.txt
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "[Featherbox] Building the interface..."
  (cd frontend && npm install && npm run build)
fi

cd backend
exec .venv/bin/python run.py "$@"
