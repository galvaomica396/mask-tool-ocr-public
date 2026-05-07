# Document Masker OCR (Public)

로컬(오프라인)에서 TXT/PDF 문서를 추출/마스킹하는 GUI 도구입니다.
외부 API 없이 동작하며, 행정/법률 문서용 규칙을 포함합니다.

## 이번 릴리즈 핵심 변경점
- 산출물 선택 추가
  - `txt만`, `pdf만`, `txt+pdf`, `txt+report`, `pdf+report`, `txt+pdf+report` 중 선택
  - 선택한 산출물만 생성하며 HTML 산출물은 더 이상 생성하지 않음
- PDF 수동 마스킹 보정
  - 자동 마스킹 후 `PDF 수동 보정` 탭에서 페이지 미리보기 위에 드래그 박스 지정
  - `실행취소`, `전체초기화`, 페이지 이동, `수동 마스킹 저장` 지원
  - 저장 파일명: `*_manual_redacted.pdf`
- UI/UX 개선
  - 좌우 대조 텍스트 미리보기
  - 텍스트 동기 스크롤 / PDF 동기 페이지 이동 토글
  - 가독성 중심의 현대적인 패널 레이아웃으로 개편
- 결재선 마스킹 강화
  - 직책+이름 패턴 대폭 확장(공직/사기업)
  - 직렬+급수 패턴 추가 (예: 건축8급, 시설8급, 행정7급, 토목9급)
  - OCR 오인식 보정 (예: 시설8긍, 건축B급)
  - 결재선 표 2줄형(직책 줄 + 이름 줄) 보정
  - 결재선 표 1줄 다중형(예: `주무관 홍길동 팀장 김철수 ...`) 보정
- 메타 영역 마스킹 강화
  - 문서번호/시행/접수/결재일자/공개여부/우편번호/전화/팩스/이메일 등
  - OCR 띄어쓰기/표기 변형 보정 (예: `문 서 번 호`, `전 화`, `e mail`)
- 과마스킹 방지
  - 결재선/메타 보정은 해당 영역 패턴에서만 동작하도록 제한

## 핫픽스 메모 (2026-05)
- 제목 과마스킹 완화: `제목` 라인을 DOC_META 통마스킹 대상에서 제외해 일반 제목 문구 보존.
- 결재구분 반영 강화: 결재선에서 `전결/대결/代決` 토큰을 `[APPROVAL_FLOW]`로 별도 마스킹.
- 하단 `시행` 문서번호 누락 보완: `시행 ○○과-1234` 변형(띄어쓰기/콜론/OCR)을 우선 인식해 `[DOC_META]` 처리.
- 문서 프로필 분리: `official`/`legal` 프로필 추가(법률 프로필은 공문 메타/결재선 규칙 기본 비활성화).
- 법률 사건번호 정책: "현재 사건번호"는 마스킹, "판례 인용 사건번호"(법원+선고+판결/결정 문맥)는 보존.

## 지원 입력
- 텍스트: `.txt`, `.md`, `.csv`, `.log`
- PDF: `.pdf`

## 추출 엔진
- `auto` (권장): marker → paddle → pymupdf → pypdf 순서
- `marker`: 스캔/표/레이아웃 강함
- `paddle`: 한글 OCR 보강
- `pymupdf`: 텍스트 PDF에 빠름
- `pypdf`: 경량 텍스트 추출

## 실행 (macOS)
### 1) 권장: 런처 더블클릭
- `run_masking_gui.command`

### 2) 직접 실행
```bash
python3 document_masker_ocr_gui.py
```

## 출력물
- `*.extracted.YYYYmmdd_HHMMSS.txt`
- `*.masked.YYYYmmdd_HHMMSS.txt`
- `*.masked.YYYYmmdd_HHMMSS.pdf` (PDF 네이티브 레닥션 성공 시)
- `*_manual_redacted.pdf` (수동 보정 저장 시)
- `*.report.YYYYmmdd_HHMMSS.json`

## 짧은 사용법
1. 입력 파일과 출력 폴더를 선택합니다.
2. `문서 프로필`, `PDF 추출 엔진`, `산출물 선택`을 지정합니다.
3. `마스킹 실행` 후 `텍스트 비교` 탭에서 결과를 확인합니다.
4. PDF 추가 보정이 필요하면 `PDF 수동 보정` 탭에서 박스를 그린 뒤 저장합니다.

## 주요 마스킹 범주
- 주민등록번호, 전화번호, 사업자등록번호
- 문맥 기반 이름/주소
- 지명(서울/자치구/동 패턴)
- 법률/행정 메타 (당사자, 회사/법인, 법원, 사건명/사건번호, 법무법인, 변호사)
- 결재선 직책/직급/직렬+급수 + 이름
- 공문 메타 라벨(문서번호/시행/접수/공개여부/연락처/이메일 등)

## 보안/운영 메모
- 외부 API 전송 없음(로컬 처리)
- 스캔 PDF는 엔진/해상도에 따라 품질 차이 큼
- 네이티브 PDF 레닥션은 검색 가능한 텍스트 PDF에서 가장 정확

## 배포 전 필수 정리
- 테스트/검증 데이터(JSON) 배포 제외
  - `cross_validation*.json`
  - `official_doc_validation*.json`
  - `regression_cases*.json`
- 로컬 실행 산출물 배포 제외
  - `*.masked.*`, `*.extracted.*`, `*.report.*`, `*_manual_redacted.pdf`
- 상세 체크리스트: `DEPLOY_SECURITY_CHECKLIST.txt`

## 클린 배포 번들 생성(권장)
```bash
python3 scripts/prepare_release_bundle.py
```
- 생성 폴더: `release_clean/`
- 포함 파일: 실행 스크립트/메인 코드/요구사항/README/보안체크리스트
- 제외 파일: tests, validation JSON, 캐시/빌드 산출물

## 참고
- 기존 상세 안내 TXT: `README_양천구_마스킹툴_OCR_v2.txt`
- 설계 문서: `DESIGN_LEGAL_MASKING_PIPELINE.md`


## Public Repo Note
This repository is a sanitized public distribution bundle with deployment-focused files only.
