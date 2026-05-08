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
권장 실행(충돌 방지):
```bash
/opt/homebrew/bin/python3 -m pip install -r requirements.txt
/opt/homebrew/bin/python3 document_masker_ocr_gui.py
```
또는 `run_mac.command` / `run_masking_gui.command` 더블클릭

macOS에서 앱이 바로 꺼지면(Abort trap / Tk 오류):
- `check_mac_env.command` 실행 후 결과 확인
- 시스템 Python(`/usr/bin/python3`) 대신 Homebrew Python(`/opt/homebrew/bin/python3`) 사용

### Windows 실행
- 가장 쉬운 방법(권장): GitHub Releases의 `DocumentMaskerOCR_windows_portable_v*.zip` 다운로드
  - 압축을 "전체 해제"한 뒤, `DocumentMaskerOCR.exe`와 `_internal` 폴더를 같은 위치에 둔 상태로 실행
  - `exe` 단독 실행 시 DLL 누락 오류가 발생할 수 있습니다.
- Python으로 직접 실행하려면:
```powershell
py -m pip install -r requirements.txt
py document_masker_ocr_gui.py
```
- 또는 `run_windows_python.bat` 더블클릭(의존성 자동 설치 + 실행)
- 주의: GitHub의 `Source code.zip/tar.gz`는 실행파일이 아니라 소스코드입니다.

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

## 4) 최신 릴리즈 (OS별 분리)
- 최신: v1.1.1
- Windows (포터블 실행파일):
  - `DocumentMaskerOCR_windows_portable_v1.1.1.zip`
- macOS (Python 소스 실행 번들):
  - `DocumentMaskerOCR_mac_python_bundle_v1.1.1.zip`
- 릴리즈 페이지:
  - https://github.com/galvaomica396/mask-tool-ocr-public/releases/tag/v1.1.1

## 5) 라이선스
MIT
