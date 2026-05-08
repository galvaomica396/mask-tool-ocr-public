@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [오류] Python launcher(py)를 찾을 수 없습니다.
  echo Python 3.11 이상 설치 후 다시 실행하세요.
  pause
  exit /b 1
)

echo [1/2] 의존성 설치
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt
if errorlevel 1 (
  echo [오류] 의존성 설치 실패
  pause
  exit /b 1
)

echo [2/2] 프로그램 실행
py -3 document_masker_ocr_gui.py
endlocal
