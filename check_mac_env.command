#!/bin/bash
set -e

echo "=== macOS GUI 실행 진단 ==="
echo "[1] OS"
sw_vers || true

echo "\n[2] python3 경로/버전"
which python3 || true
python3 --version || true

echo "\n[3] Homebrew python 경로/버전"
if [ -x "/opt/homebrew/bin/python3" ]; then
  /opt/homebrew/bin/python3 --version
else
  echo "/opt/homebrew/bin/python3 없음"
fi

echo "\n[4] Tk 버전(시스템 python3)"
python3 - <<'PY' || true
import tkinter
print('TkVersion=', tkinter.TkVersion)
PY

echo "\n[5] Tk 버전(Homebrew python3)"
if [ -x "/opt/homebrew/bin/python3" ]; then
  /opt/homebrew/bin/python3 - <<'PY' || true
import tkinter
print('TkVersion=', tkinter.TkVersion)
PY
fi

echo "\n[6] 실행 권장"
echo "  /opt/homebrew/bin/python3 document_masker_ocr_gui.py"
