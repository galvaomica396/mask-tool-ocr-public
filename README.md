# Document Masker OCR (Public)

오프라인(로컬) 문서 마스킹 GUI 도구입니다.

## 핵심
- 외부 API 호출 없이 로컬 처리
- TXT/PDF 입력 지원
- 자동 마스킹 + PDF 수동 보정 지원

## 실행
### macOS
1) 터미널에서 프로젝트 폴더로 이동
```bash
cd /path/to/mask-tool-ocr-public
```
2) 실행
```bash
python3 document_masker_ocr_gui.py
```

또는 `run_masking_gui.command` 더블클릭

## 출력물
- `*.extracted.YYYYmmdd_HHMMSS.txt`
- `*.masked.YYYYmmdd_HHMMSS.txt`
- `*.masked.YYYYmmdd_HHMMSS.pdf`
- `*_manual_redacted.pdf`
- `*.report.YYYYmmdd_HHMMSS.json`

## 라이선스
MIT
