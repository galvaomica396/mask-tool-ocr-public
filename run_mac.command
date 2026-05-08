#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -x "/opt/homebrew/bin/python3" ]; then
  PYTHON_BIN="/opt/homebrew/bin/python3"
elif [ -x "/usr/local/bin/python3" ]; then
  PYTHON_BIN="/usr/local/bin/python3"
else
  PYTHON_BIN="python3"
fi

echo "[INFO] Python: $PYTHON_BIN"
"$PYTHON_BIN" -m pip install -r requirements.txt
exec "$PYTHON_BIN" document_masker_ocr_gui.py
