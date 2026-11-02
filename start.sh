#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${QUANT_STUDIO_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3.13 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.13)"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11+ is required. Set QUANT_STUDIO_PYTHON to its executable." >&2
  exit 1
fi

if [ ! -d .venv313 ]; then
  "$PYTHON_BIN" -m venv .venv313
fi
if [ ! -x .venv313/bin/uvicorn ]; then
  .venv313/bin/pip install -r backend/requirements.txt
fi
if [ ! -d node_modules ]; then
  npm install
fi

.venv313/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM
npm run dev -- --host 127.0.0.1
