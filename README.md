# Document Masker OCR (Public)

오프라인(로컬) 문서 마스킹 GUI 도구입니다.

## 1) 빠른 설치

### 공통
```bash
git clone https://github.com/galvaomica396/mask-tool-ocr-public.git
cd mask-tool-ocr-public
python3 -m pip install -r requirements.txt
```

### macOS 실행
```bash
python3 document_masker_ocr_gui.py
```
또는 `run_masking_gui.command` 더블클릭

### Windows 실행
- 가장 쉬운 방법: GitHub Releases에서 `DocumentMaskerOCR.exe` 다운로드 후 실행
- Python으로 직접 실행하려면:
```powershell
py -m pip install -r requirements.txt
py document_masker_ocr_gui.py
```

## 2) 핵심 기능
- 외부 API 호출 없이 로컬 처리
- TXT/PDF 입력 지원
- 자동 마스킹 + PDF 수동 보정
- legal/official 프로필 분리

## 3) 출력물
- `*.extracted.YYYYmmdd_HHMMSS.txt`
- `*.masked.YYYYmmdd_HHMMSS.txt`
- `*.masked.YYYYmmdd_HHMMSS.pdf`
- `*_manual_redacted.pdf`
- `*.report.YYYYmmdd_HHMMSS.json`

## 4) 라이선스
MIT


Windows 최신코드 실행(권장, Python 방식)
1. 이 저장소를 ZIP으로 받아 압축 해제
2. 같은 폴더에서 `run_windows_python.bat` 더블클릭
3. 자동으로 requirements 설치 후 GUI 실행

주의: GitHub의 Source code.zip/tar.gz는 실행파일이 아니라 소스코드입니다.
