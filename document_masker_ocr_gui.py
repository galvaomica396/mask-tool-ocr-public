#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
서울시 자치구 일반 패턴 기반 로컬 문서 마스킹 GUI v2
- TXT/PDF 입력 지원
- PDF는 로컬 OCR/추출 엔진으로 텍스트·레이아웃 추출 후 마스킹
- 기본값은 오프라인 처리 (외부 API 없음)

지원 엔진(우선순위):
1) marker-pdf (권장: 스캔/한글/표/레이아웃 상대적으로 강함)
2) paddleocr (한글 OCR + 레이아웃 보완)
3) pymupdf4llm (텍스트 PDF + 마크다운 레이아웃)
4) pypdf (순수 텍스트 추출)
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

APP_VERSION = "v3.2-product-gate"

OUTPUT_ARTIFACT_LABELS = [
    "txt만",
    "pdf만",
    "txt+pdf",
    "txt+report",
    "pdf+report",
    "txt+pdf+report",
]
OUTPUT_ARTIFACTS_MAP: dict[str, set[str]] = {
    "txt만": {"txt"},
    "pdf만": {"pdf"},
    "txt+pdf": {"txt", "pdf"},
    "txt+report": {"txt", "report"},
    "pdf+report": {"pdf", "report"},
    "txt+pdf+report": {"txt", "pdf", "report"},
}

MASK_TOKEN_LABELS: dict[str, str] = {
    "RRN": "주민등록번호",
    "PHONE": "전화번호",
    "BUSINESS_REG_NO": "사업자등록번호",
    "NAME": "이름",
    "ADDRESS": "주소",
    "PLACE": "지명",
    "ADDR_DETAIL": "상세주소",
    "LOT_NO": "지번",
    "LEGAL_PARTY": "당사자",
    "COMPANY": "회사명",
    "COURT": "법원명",
    "CASE_TITLE": "사건명",
    "CASE_NUMBER": "사건번호",
    "LAW_FIRM": "법무법인",
    "ATTORNEY": "변호사",
    "APPROVAL_LINE": "결재자",
    "APPROVAL_FLOW": "결재구분",
    "REGION": "지역",
    "DOC_META": "문서메타",
    "EMAIL": "이메일",
}


def _mask_token(tag: str) -> str:
    return f"[{MASK_TOKEN_LABELS.get(tag, tag)}]"


def _convert_mask_tokens_to_korean(text: str) -> str:
    for tag, label in MASK_TOKEN_LABELS.items():
        text = text.replace(f"[{tag}]", f"[{label}]")
    return text

# PaddleOCR 모델 호스트 연결 체크 생략(사내망/폐쇄망 지연 방지)
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


# -----------------------------
# 1) 마스킹 규칙
# -----------------------------
RRN_PAT = re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)")
PHONE_PAT = re.compile(
    r"(?<!\d)(?:"
    r"(?:\+?82[- ]?)?(?:0?\d{1,3})[- ]?\d{3,4}[- ]?\d{4}"  # +82/010/02/031/070/050x 포함
    r"|1[5-8]\d{2}[- ]?\d{4}"                         # 15xx~18xx 대표번호
    r")(?!\d)"
)
# 사업자등록번호: 123-45-67890 (하이픈/공백 생략 허용)
BUSINESS_REG_PAT = re.compile(r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{5}(?!\d)")

NAME_CONTEXT_PAT = re.compile(
    r"(?P<label>\b(?:이름|성명|민원인|신청인|담당자|보호자|대표자|제출인)\b(?:\s*[:：]\s*|\s+))(?P<name>(?!(?:제도|정보|접수|신청|처리|개선|등록|변경|작성|관리|담당|요청|서식|절차|안내|민원|직통)(?=\s|[:：]|$))[가-힣]{2,4})"
)

ADDRESS_CONTEXT_PAT = re.compile(
    r"(?P<label>\b주소\b\s*[:：]\s*)(?P<addr>[^\n,;]+)"
)

SEOUL_GU_PAT = re.compile(
    r"(?:서울특별시|서울시|서울)\s*(?:종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)"
)
SEOUL_GU_ONLY_PAT = re.compile(
    r"(?<![가-힣])(?:종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)(?![가-힣])"
)
SEOUL_GU_OFFICE_PAT = re.compile(
    r"(?<![가-힣])(?:종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)(?=청장|청)"
)

PLACE_PATS = [
    SEOUL_GU_PAT,
    SEOUL_GU_ONLY_PAT,
    SEOUL_GU_OFFICE_PAT,
    re.compile(r"(서울특별시|서울시|서울)\s*양천구"),
    re.compile(r"양천구"),
    re.compile(r"목\s*(?:[1-5]\s*)?동"),
    re.compile(r"신정\s*(?:(?:1|2|3|4|6|7)\s*)?동"),
    re.compile(r"신월\s*(?:[1-7]\s*)?동"),
]

# 지번은 '번지' 접미가 있을 때만 마스킹해 사건번호/연도 숫자 오탐을 줄임
LOT_NO_PAT = re.compile(r"(?<!\d)\d{1,4}(?:-\d{1,4})?\s*번지(?!\d)")
ROAD_NO_PAT = re.compile(r"[가-힣0-9·\-\s]{1,20}(?:로|길)\s*\d{1,4}(?:-\d{1,4})?")

LEGAL_PARTY_PAT = re.compile(
    r"(?P<label>\b(?:원고|피고|신청인|피신청인|상대방|항고인|피항고인|청구인|피청구인|채권자|채무자|고소인|피고소인|진정인|피진정인)\b\s*[:：]\s*)(?P<value>[^\n,;()]{2,40})"
)
LEGAL_PARTY_INLINE_NAME_PAT = re.compile(
    r"(?P<label>\b(?:원고|피고|신청인|피신청인|상대방|항고인|피항고인|청구인|피청구인|채권자|채무자|고소인|피고소인|진정인|피진정인)\b\s+)"
    r"(?P<value>(?!(?:측|측은|측이|대리인|소송대리인|법무법인|법률사무소)(?=\s|$))(?:[가-힣]{2,4}|[가-힣](?:\s*[가-힣]){1,3}))"
    r"(?=[^가-힣]|$)"
)
# 법률문서 헤더 보강: "피고 소송대리인 홍길동" / "원고 대리인 홍 길 동" 등
LEGAL_PARTY_REP_INLINE_PAT = re.compile(
    r"(?P<label>\b(?:원고|피고|신청인|피신청인|항고인|피항고인|청구인|피청구인|채권자|채무자)\b\s*"
    r"(?:소송\s*대리인|법률\s*대리인|대리인)\b\s*(?:[:：]\s*|\s+))"
    r"(?P<value>(?!(?:법무법인|법률사무소|담당변호사|변호사)(?=\s|$))(?:[가-힣]{2,4}|[가-힣](?:\s*[가-힣]){1,3}))"
    r"(?=[^가-힣]|$)"
)
COMPANY_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:회사명|법인명|상호|업체명|기관명)\b(?:\s*[:：]\s*|\s+))(?P<value>[^\n]{2,80})"
)
COMPANY_INLINE_PAT = re.compile(
    r"(?P<value>(?:주식회사|유한회사|합자회사|합명회사|의료법인|학교법인|사회복지법인|재단법인|사단법인)\s*[A-Za-z0-9가-힣&.,()\- ]{1,60}|[(（]주[)）]\s*[A-Za-z0-9가-힣&.,()\- ]{1,60})"
)
COURT_PAT = re.compile(
    r"(?<![가-힣])(?P<value>(?:대법원|특허법원|회생법원|행정법원|가정법원|고등법원|[가-힣]{2,12}(?:지방법원|고등법원|가정법원|행정법원|회생법원|지원))(?:\s*[가-힣0-9]{1,12}(?:지원|재판부))?)(?![가-힣])"
)
CASE_TITLE_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:사건명|사건제목|사건명칭|제목)\b\s*[:：]\s*)(?P<value>[^\n]+)"
)
CASE_LINE_TITLE_PAT = re.compile(
    r"(?m)^(?P<number>(?:19|20)\d{2}\s*[가-힣]{1,4}\s*\d{1,10})\s+(?P<title>[^\n]{2,100})$"
)
CASE_NUMBER_PAT = re.compile(r"(?<![\w\]])(?P<value>(?:19|20)\d{2}\s*(?!구상)[가-힣]{1,4}\s*\d{1,10})(?![\w\[])")
LAW_FIRM_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:법무법인|법률사무소|변호사사무실|소속법무법인)\b\s*[:：]?\s*)(?P<value>[^\n,;]{2,60})"
)
LAW_FIRM_INLINE_PAT = re.compile(
    r"(?P<value>(?:법무법인|법률사무소|변호사사무실)\s*[A-Za-z0-9가-힣&.,()\- ]{1,60})"
)
ATTORNEY_PAT = re.compile(
    r"(?P<label>\b(?:변호사|담당변호사|소송대리인|법률대리인|선임변호사)\b\s*[:：]\s*)(?P<value>[가-힣A-Za-z ]{2,30})"
)
# 한국 행정공문 결재선/기안 라벨
APPROVAL_LINE_PAT = re.compile(
    r"(?P<label>\b(?:기안자|기안|검토자|검토|협조자|협조|결재권자|최종결재권자|결재자|전결권자|대결권자|전결|대결|결재)\b(?:\s*[:：]\s*|\s+))(?P<value>(?!(?:지침|절차|기준|양식|규정|문서|작성|검토|결재|업무|계획|방법|지시|공문|시행|요청|사유|완료|안내|결과|권한|하여|위하여|대하여)(?=\s|$))[가-힣A-Za-z]{2,20})"
)
# 공직/사기업 직책·직급 + 이름 inline (예: "주무관 홍길동", "건축8급 김철수", "대리 박영수")
OFFICIAL_ROLE_NAME_PAT = re.compile(
    r"(?P<label>\b(?:"
    r"주무관|사무관|서기관|부이사관|이사관|관리관|"
    r"지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|담당자|담당|계장|행정팀장|민원팀장|복지팀장|건축과장|총무과장|"
    r"부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|과장|차장|부장|본부장|사업부장|파트장|매니저|"
    r"이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO"
    r")\b(?:\s*[:：]\s*|\s+))"
    r"(?P<value>(?!(?:지침|절차|기준|양식|규정|문서|작성|검토|결재|업무|계획|방법|지시|공문|시행|요청|사유|완료|안내|결과|권한|하여|위하여|대하여)(?=\s|$))(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))"
)
# 공문 지역/관할/소재지 라벨
REGION_CONTEXT_PAT = re.compile(
    r"(?P<label>\b(?:소재지|관할지역|관할구역|관할|해당지역|위치|지역|행정구역)\b\s*[:：]\s*)(?P<value>[^\n,;]{2,80})"
)
# 공문 헤더/결재 메타데이터 라벨
DOC_META_PAT = re.compile(
    r"(?P<label>(?:^|\s)(?:"
    r"수신|참조|경유|"
    r"문서\s*번호|문서번호|공문\s*번호|공문번호|방침\s*번호|방침번호|"
    r"시행\s*문서번호|시행문서번호|시행\s*번호|시행번호|시행|"
    r"접수\s*번호|접수번호|접수|"
    r"결재\s*일자|결재일자|작성\s*일자|작성일자|시행\s*일자|시행일자|"
    r"공개\s*여부|공개여부|공개\s*구분|공개구분|공개\s*등급|공개등급|"
    r"우편\s*번호|우편번호|우\.?|"
    r"전송|팩스|FAX|"
    r""
    r"홈페이지|누리집|웹사이트|"
    r"담당\s*부서|담당부서|부서|"
    r"담당자(?:\s*\(\s*직통(?:전화)?\s*\))?|담당자\s*직통(?:전화)?"
    r")\s*(?:[:：]\s*|\s+))(?P<value>[^\n]{1,140})"
)
# 본문 내 공문 참조표현 확장:
# - "건축과-1526(2026.4.22.)호", "건축과-1526호"
# - "건축과 제1526호", "건축과-1526호(2026.4.22.)", "건축과-1526('26.4.22.)호"
DOC_REF_INLINE_PAT = re.compile(
    r"(?P<value>(?:"
    r"[가-힣A-Za-z0-9]+과\s*[-–]\s*\d{1,6}(?:\s*\(\s*(?:(?:19|20)\d{2}|['’](?:\d{2}))\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?\s*호(?:\s*\(\s*(?:(?:19|20)\d{2}|['’](?:\d{2}))\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?"
    r"|[가-힣A-Za-z0-9]+과\s*제\s*\d{1,6}\s*호(?:\s*\(\s*(?:(?:19|20)\d{2}|['’](?:\d{2}))\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?"
    r"))"
)

# 하단 메타 '시행 ○○과-1234' 전용 보강 (띄어쓰기/OCR 변형 포함)
SIHAENG_DOCNO_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)시\s*행(?:\s+|\s*[:：]\s*))(?P<value>[^\n]{0,60}?"
    r"(?:[가-힣A-Za-z0-9]{1,24}(?:과|팀|국|실|센터|사업소)?\s*[-–—]\s*\d{1,8}(?:-\d{1,8})?)"
    r"(?:\s*\(\s*(?:(?:19|20)\d{2}|['’]?\d{2})\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?)"
)

# 이메일
EMAIL_PAT = re.compile(r"(?P<value>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# 공개등급 단독 표기 (예: 부분공개(5), 비공개(6), 공개)
PUBLIC_LEVEL_PAT = re.compile(r"(?P<value>(?:부분공개\s*\(\s*\d{1,2}\s*\)|비공개\s*\(\s*\d{1,2}\s*\)|전부공개))")

# 부서/팀 + 내선 번호 (예: 급수운영2팀-8464, 건축과 1234)
TEAM_EXT_PAT = re.compile(
    r"(?P<value>(?:[가-힣A-Za-z0-9]{1,20}(?:팀|과|국|실|센터|사업소))\s*[-:]?\s*\d{3,5})"
)

# 부서명+직책+이름 결합형(공백 없거나 약한 구분) 보강
DEPT_ROLE_NAME_COMPACT_PAT = re.compile(
    r"(?P<value>(?:[가-힣A-Za-z0-9]{1,20}(?:팀|과|국|실|센터|사업소|본부|사업부))\s*"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO)\s*"
    r"(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))"
)

# 직책이 부서명에 붙은 결합형 + 이름 (예: 급수관리팀장 이한수)
OFFICIAL_COMBINED_ROLE_NAME_PAT = re.compile(
    r"(?P<value>(?:[가-힣A-Za-z0-9]{1,24}(?:"
    r"주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO))\s*"
    r"(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))"
)

# 결재선 표 전용: 직책행 다음 줄 이름행(2~4자)을 결재선으로 간주
APPROVAL_TABLE_LINE_PAT = re.compile(
    r"(?m)^(?P<label>(?:"
    r"주무관|사무관|서기관|부이사관|이사관|관리관|"
    r"지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9]\s*[급긍금급]|"
    r"[가-힣A-Za-z0-9]{1,20}(?:팀장|과장|국장|실장|센터장|본부장|사업부장|부시장|시장|부구청장|구청장)"
    r"))\s*$\n^(?P<value>[가-힣]{2,4})\s*$"
)

# 결재선 표 전용: 한 줄에 직책+이름이 여러 쌍으로 붙는 OCR 케이스
APPROVAL_TABLE_INLINE_MULTI_PAT = re.compile(
    r"(?P<value>"
    r"(?:"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
    r"\s*(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3})"
    r")"
    r"(?:\s*(?:\||/|,)\s*|\s+)"
    r"(?:"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
    r"\s*(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3})"
    r")"
    r"(?:\s*(?:\||/|,)\s*|\s+)"
    r"?(?:"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
    r"\s*(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3})"
    r")*"
    r")"
)

# 결재선 OCR 보정 전용: 직렬+급수 표기의 띄어쓰기/오인식 허용 (결재선 단계에서만 사용)
APPROVAL_GRADE_OCR_PAT = re.compile(
    r"(?m)^\s*(?P<label>(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])(?:\s*[:：]\s*|\s+)"
    r"(?P<value>(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))\s*$"
)

# 결재선 결재구분 토큰(전결/대결/代決) 보강
# 본문 일반문장 오탐 방지를 위해 결재선에서 자주 나오는 짧은 셀/구분자 맥락만 허용
APPROVAL_FLOW_PAT = re.compile(
    r"(?mi)(?<![가-힣A-Za-z0-9])(?P<value>(?:전\s*결|대\s*결|代\s*決|專\s*決|代))(?![가-힣A-Za-z0-9])"
)

# 결재선 하단 최종결재자 누락 보강: "04/30 홍길동" 형태에서 이름 마스킹
APPROVAL_DATE_NAME_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.?)(?:\s*[:：]?\s*))(?P<value>[가-힣]{2,4})(?=\s|$)"
)

# 결재선 직책+일자+최종결재자 결합형 보강 (예: 공공주택과장 04/30 하대근)
APPROVAL_ROLE_DATE_NAME_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)(?:[가-힣A-Za-z0-9]{1,24}(?:팀장|과장|국장|실장|센터장|본부장|사업부장|부시장|시장|부구청장|구청장)\s*)"
    r"(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.?)(?:\s*[:：]?\s*))(?P<value>[가-힣]{2,4})(?=\s|$)"
)

# 결재선 직책 뒤 괄호 이름형 보강 (예: 급수운영과장 (한현숙), 팀장(안승현))
APPROVAL_ROLE_PAREN_NAME_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)(?:[가-힣A-Za-z0-9]{0,24}(?:주무관|팀장|과장|국장|실장|센터장|본부장|사업부장|부시장|시장|부구청장|구청장))"
    r"(?:\s*(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.?))?\s*[\(\[]\s*)(?P<value>[가-힣]{2,6})(?=\s*[\)\]])"
)

# 메타 라벨 OCR 보정 전용: 라벨 변형(띄어쓰기/영문 대소문자/FAX) 허용
DOC_META_OCR_PAT = re.compile(
    r"(?P<label>(?:^|\s)(?:"
    r"문서\s*번호|공문\s*번호|방침\s*번호|"
    r"시행\s*문서\s*번호|시행\s*번호|시행|"
    r"접수\s*번호|접수|"
    r"결재\s*일자|작성\s*일자|시행\s*일자|"
    r"공개\s*여부|공개\s*구분|공개\s*등급|"
    r"우\.?|우편\s*번호|"
    r"전\s*송|팩\s*스|fax|FAX|"
    r""
    r"담당\s*부서|담당자\s*직통(?:전화)?"
    r")\s*(?:[:：]\s*|\s+))(?P<value>[^\n]{1,140})"
)


def _count_up(report: dict[str, int], key: str, n: int = 1) -> None:
    report[key] = report.get(key, 0) + n


@dataclass(frozen=True)
class RedactionMatch:
    tag: str
    text: str


@dataclass(frozen=True)
class ChunkProcessResult:
    masked_text: str
    counts: dict[str, int]
    matches: list[RedactionMatch]


@dataclass(frozen=True)
class LLMCandidateOccurrence:
    start: int
    end: int
    replacement: str
    context: str


@dataclass(frozen=True)
class LLMCandidate:
    tag: str
    label: str
    value: str
    normalized_value: str
    occurrences: tuple[LLMCandidateOccurrence, ...]
    score: int


@dataclass(frozen=True)
class ManualRedactionBox:
    page_index: int
    rect: tuple[float, float, float, float]


@dataclass(frozen=True)
class PreviewRenderState:
    page_index: int
    scale: float
    image_width: int
    image_height: int


def _record_redaction_match(matches: list[RedactionMatch], tag: str, value: str) -> None:
    cleaned = " ".join(value.split()).strip(" ,;:/")
    if len(cleaned) < 2 or cleaned.startswith("[") or cleaned.endswith("]"):
        return
    matches.append(RedactionMatch(tag=tag, text=cleaned))


def _sub_simple(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    matches: list[RedactionMatch] | None = None,
    value_group: str | None = None,
) -> str:
    def repl(_m: re.Match[str]) -> str:
        if matches is not None:
            value = _m.group(value_group) if value_group else _m.group(0)
            _record_redaction_match(matches, tag, value)
        _count_up(report, tag)
        return f"[{tag}]"

    return pat.sub(repl, text)


def _sub_keep_label(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    value_group: str,
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if matches is not None:
            _record_redaction_match(matches, tag, m.group(value_group))
        _count_up(report, tag)
        return f"{m.group('label')}[{tag}]"

    return pat.sub(repl, text)


def _sub_case_title_line(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if matches is not None:
            _record_redaction_match(matches, tag, m.group("title"))
        _count_up(report, tag)
        return f"{m.group('number')} [{tag}]"

    return pat.sub(repl, text)


def _sub_approval_table_line(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if matches is not None:
            _record_redaction_match(matches, tag, m.group("value"))
        _count_up(report, tag)
        return f"{m.group('label')}\n[{tag}]"

    return pat.sub(repl, text)


def _default_chunk_logger(message: str) -> None:
    return None


def _merge_counts(total: dict[str, int], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0) + value


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        if current and current_len + len(line) > chunk_size:
            chunks.append("".join(current))
            current = []
            current_len = 0

        if len(line) > chunk_size:
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            for start in range(0, len(line), chunk_size):
                chunks.append(line[start:start + chunk_size])
            continue

        current.append(line)
        current_len += len(line)

    if current:
        chunks.append("".join(current))

    return chunks or [text]


def _mask_text_chunk(chunk: str, opts: dict[str, Any]) -> ChunkProcessResult:
    profile = str(opts.get("profile", "official") or "official").lower()

    use_approval_line = bool(opts.get("approval_line", True))
    use_region_context = bool(opts.get("region_context", True))
    use_doc_meta = bool(opts.get("doc_meta", True))
    use_court = bool(opts.get("court", True))

    # 법률문서 전용: 공문 메타/결재선 규칙 비활성화
    if profile == "legal":
        use_approval_line = False
        use_region_context = False
        use_doc_meta = False

    masked, counts, matches = mask_text(
        chunk,
        use_rrn=bool(opts.get("rrn", True)),
        use_phone=bool(opts.get("phone", True)),
        use_business_reg=bool(opts.get("business_reg", True)),
        use_name=bool(opts.get("name", True)),
        use_address=bool(opts.get("address", True)),
        use_place=bool(opts.get("place", True)),
        use_legal_party=bool(opts.get("legal_party", True)),
        use_company=bool(opts.get("company", True)),
        use_court=use_court,
        use_case_title=bool(opts.get("case_title", True)),
        use_case_number=bool(opts.get("case_number", True)),
        use_law_firm=bool(opts.get("law_firm", True)),
        use_attorney=bool(opts.get("attorney", True)),
        use_approval_line=use_approval_line,
        use_region_context=use_region_context,
        use_doc_meta=use_doc_meta,
        profile=profile,
    )
    if bool(opts.get("korean_tokens", False)):
        masked = _convert_mask_tokens_to_korean(masked)

    return ChunkProcessResult(masked_text=masked, counts=counts, matches=matches)


def process_masking_queue(text: str, opts: dict[str, Any]) -> tuple[str, dict[str, int], list[RedactionMatch], dict[str, Any]]:
    chunk_size = max(int(opts.get("chunk_size", 4000) or 4000), 1)
    max_retries = max(int(opts.get("chunk_retries", 2) or 2), 0)
    logger = opts.get("log_callback") or _default_chunk_logger
    chunk_processor = opts.get("_chunk_processor") or _mask_text_chunk

    chunks = _chunk_text(text, chunk_size)
    masked_chunks: list[str] = []
    total_counts: dict[str, int] = {}
    total_matches: list[RedactionMatch] = []
    retried_chunks = 0
    failed_chunks = 0
    fallback_chunks = 0

    for idx, chunk in enumerate(chunks, 1):
        logger(f"[청크] {idx}/{len(chunks)} 처리 시작 (chars={len(chunk)})")
        attempts = 0
        while True:
            attempts += 1
            try:
                result = chunk_processor(chunk, opts)
                masked_chunks.append(result.masked_text)
                _merge_counts(total_counts, result.counts)
                total_matches.extend(result.matches)
                if attempts > 1:
                    retried_chunks += 1
                logger(f"[청크] {idx}/{len(chunks)} 처리 완료 (attempt={attempts})")
                break
            except Exception as e:
                if attempts <= max_retries:
                    logger(f"[청크] {idx}/{len(chunks)} 재시도 {attempts}/{max_retries} - {e}")
                    continue

                failed_chunks += 1
                logger(f"[청크] {idx}/{len(chunks)} 실패 - 기본 규칙 엔진으로 계속 진행: {e}")
                fallback = _mask_text_chunk(chunk, opts)
                fallback_chunks += 1
                masked_chunks.append(fallback.masked_text)
                _merge_counts(total_counts, fallback.counts)
                total_matches.extend(fallback.matches)
                logger(f"[청크] {idx}/{len(chunks)} 대체 처리 완료")
                break

    meta = {
        "enabled": True,
        "chunk_size": chunk_size,
        "max_retries": max_retries,
        "total_chunks": len(chunks),
        "retried_chunks": retried_chunks,
        "failed_chunks": failed_chunks,
        "fallback_chunks": fallback_chunks,
    }
    return "".join(masked_chunks), total_counts, total_matches, meta


def _mask_legal_courts_with_citation_preserve(
    text: str,
    report: dict[str, int],
    matches: list[RedactionMatch],
) -> str:
    """
    법률문서 전용 법원명 처리:
    - 판례 인용(법원 + 선고 + 판결/결정) 문맥의 법원명은 보존
    - 그 외 법원명은 마스킹
    """
    loose_court_pat = (
        r"(?:"
        r"대\s*법\s*원|헌\s*법\s*재\s*판\s*소|특\s*허\s*법\s*원|회\s*생\s*법\s*원|행\s*정\s*법\s*원|가\s*정\s*법\s*원|고\s*등\s*법\s*원|"
        r"(?:[가-힣]\s*){2,12}(?:지\s*방\s*법\s*원|고\s*등\s*법\s*원|가\s*정\s*법\s*원|행\s*정\s*법\s*원|회\s*생\s*법\s*원|지\s*원)"
        r")"
    )
    citation_court_pat = re.compile(
        rf"(?P<court>{loose_court_pat})"
        r"(?=\s*(?:19|20)\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*선\s*고\s*"
        r"(?:19|20)\d{2}\s*(?:[가-힣]\s*){1,4}\s*\d{1,10}\s*(?:판\s*결|결\s*정))"
    )
    legal_spaced_court_pat = re.compile(rf"(?<![가-힣])(?P<value>{loose_court_pat})(?![가-힣])")
    preserved: dict[str, str] = {}

    def _preserve(m: re.Match) -> str:
        idx = len(preserved)
        key = f"__CIT_COURT_{idx}__"
        court = m.group("court")
        preserved[key] = court
        return key

    work = citation_court_pat.sub(_preserve, text)
    work = _sub_simple(work, COURT_PAT, "COURT", report, matches=matches, value_group="value")
    work = _sub_simple(work, legal_spaced_court_pat, "COURT", report, matches=matches, value_group="value")

    for key, court in preserved.items():
        work = work.replace(key, court)

    return work


def _mask_legal_case_numbers_with_citation_preserve(
    text: str,
    report: dict[str, int],
    matches: list[RedactionMatch],
) -> str:
    """
    법률문서 전용 사건번호 처리:
    - 판례 인용 사건번호(대법원/법원 + 선고 + 판결/결정)는 보존
    - 현재 사건 메타(사건번호/사건/당해 사건/본건/이 사건)는 마스킹
    """
    citation_pat = re.compile(
        r"((?:대법원|헌법재판소|[가-힣]{2,12}(?:지방법원|고등법원|가정법원|행정법원|회생법원|지원))"
        r"\s*(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*"
        r"(?P<num>(?:19|20)\d{2}\s*[가-힣]{1,4}\s*\d{1,10})\s*(?:판결|결정))"
    )
    preserved: dict[str, str] = {}

    def _preserve(m: re.Match) -> str:
        idx = len(preserved)
        key = f"__CIT_CASE_NO_{idx}__"
        num = m.group("num")
        preserved[key] = num
        return m.group(0).replace(num, key)

    work = citation_pat.sub(_preserve, text)

    current_case_pat = re.compile(
        r"(?P<label>\b(?:사건번호|사건|당해\s*사건|이\s*사건|본건)\b\s*[:：]?\s*)"
        r"(?P<value>(?:19|20)\d{2}\s*[가-힣]{1,4}\s*\d{1,10})"
    )
    work = _sub_keep_label(work, current_case_pat, "CASE_NUMBER", report, value_group="value", matches=matches)

    # 라벨 없이 단독 등장하는 사건번호는 법률문서에서 과마스킹 위험이 커서 보존 (헤더 패턴에서 대부분 처리)

    for key, num in preserved.items():
        work = work.replace(key, num)

    return work


def mask_text(
    text: str,
    use_rrn: bool = True,
    use_phone: bool = True,
    use_business_reg: bool = True,
    use_name: bool = True,
    use_address: bool = True,
    use_place: bool = True,
    use_legal_party: bool = True,
    use_company: bool = True,
    use_court: bool = True,
    use_case_title: bool = True,
    use_case_number: bool = True,
    use_law_firm: bool = True,
    use_attorney: bool = True,
    use_approval_line: bool = True,
    use_region_context: bool = True,
    use_doc_meta: bool = True,
    profile: str = "official",
) -> tuple[str, dict[str, int], list[RedactionMatch]]:
    report: dict[str, int] = {}
    matches: list[RedactionMatch] = []

    if use_rrn:
        text = _sub_simple(text, RRN_PAT, "RRN", report, matches=matches)
    if use_phone:
        text = _sub_simple(text, PHONE_PAT, "PHONE", report, matches=matches)
    if use_business_reg:
        text = _sub_simple(text, BUSINESS_REG_PAT, "BUSINESS_REG_NO", report, matches=matches)
    if use_name:
        text = _sub_keep_label(text, NAME_CONTEXT_PAT, "NAME", report, value_group="name", matches=matches)
    if use_address:
        text = _sub_keep_label(text, ADDRESS_CONTEXT_PAT, "ADDRESS", report, value_group="addr", matches=matches)

    if use_place:
        for p in PLACE_PATS:
            text = _sub_simple(text, p, "PLACE", report, matches=matches)

    # 주소 상세는 주소/지명 문맥이 있을 때만
    if use_address and (("[PLACE]" in text) or ("주소" in text)):
        text = _sub_simple(text, ROAD_NO_PAT, "ADDR_DETAIL", report, matches=matches)
        text = _sub_simple(text, LOT_NO_PAT, "LOT_NO", report, matches=matches)

    if use_legal_party:
        text = _sub_keep_label(text, LEGAL_PARTY_PAT, "LEGAL_PARTY", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, LEGAL_PARTY_INLINE_NAME_PAT, "LEGAL_PARTY", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, LEGAL_PARTY_REP_INLINE_PAT, "LEGAL_PARTY", report, value_group="value", matches=matches)

        if profile == "legal":
            # OCR/PDF 추출에서 공백이 붙는 헤더형 보강: "사건원고홍길동피고김철수"
            head_len = min(len(text), 1200)
            head = text[:head_len]
            compact_party_pat = re.compile(
                r"(?P<label>(?:원\s*고|피\s*고|신\s*청\s*인|피\s*신\s*청\s*인|청\s*구\s*인|피\s*청\s*구\s*인|항\s*고\s*인|피\s*항\s*고\s*인|채\s*권\s*자|채\s*무\s*자))"
                r"\s*(?P<value>(?!(?:측|대리인|소송대리인|법무법인|법률사무소|주장|주장의|주장과|주장에))[가-힣]{2,6}?)"
                r"(?=(?:원\s*고|피\s*고|신\s*청\s*인|피\s*신\s*청\s*인|청\s*구\s*인|피\s*청\s*구\s*인|항\s*고\s*인|피\s*항\s*고\s*인|채\s*권\s*자|채\s*무\s*자|\s|\n|$))"
            )
            head = _sub_keep_label(head, compact_party_pat, "LEGAL_PARTY", report, value_group="value", matches=matches)
            text = head + text[head_len:]

            # 헤더에서 식별된 당사자 실명은 본문 전역에서 동일 토큰으로 추가 치환
            party_names = sorted(
                {
                    m.text for m in (matches or [])
                    if getattr(m, "tag", "") == "LEGAL_PARTY" and m.text and len(m.text) >= 2
                },
                key=len,
                reverse=True,
            )
            for name in party_names:
                if not name:
                    continue
                # 한글은 조사/어미가 붙는 경우가 많아 경계조건 없이 실명 문자열 자체를 치환
                text = text.replace(name, "[LEGAL_PARTY]")
                _count_up(report, "LEGAL_PARTY")

    if use_company:
        text = _sub_keep_label(text, COMPANY_LABEL_PAT, "COMPANY", report, value_group="value", matches=matches)
        text = _sub_simple(text, COMPANY_INLINE_PAT, "COMPANY", report, matches=matches, value_group="value")

    if use_court:
        if profile == "legal":
            text = _mask_legal_courts_with_citation_preserve(text, report, matches)
        else:
            text = _sub_simple(text, COURT_PAT, "COURT", report, matches=matches, value_group="value")

    if use_case_title:
        text = _sub_keep_label(text, CASE_TITLE_LABEL_PAT, "CASE_TITLE", report, value_group="value", matches=matches)
        text = _sub_case_title_line(text, CASE_LINE_TITLE_PAT, "CASE_TITLE", report, matches=matches)

    if use_case_number:
        if profile == "legal":
            text = _mask_legal_case_numbers_with_citation_preserve(text, report, matches)
        else:
            text = _sub_simple(text, CASE_NUMBER_PAT, "CASE_NUMBER", report, matches=matches, value_group="value")

    if use_law_firm:
        text = _sub_keep_label(text, LAW_FIRM_LABEL_PAT, "LAW_FIRM", report, value_group="value", matches=matches)
        text = _sub_simple(text, LAW_FIRM_INLINE_PAT, "LAW_FIRM", report, matches=matches, value_group="value")

    if use_attorney:
        text = _sub_keep_label(text, ATTORNEY_PAT, "ATTORNEY", report, value_group="value", matches=matches)

    if use_approval_line:
        text = _sub_keep_label(text, APPROVAL_LINE_PAT, "APPROVAL_LINE", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, OFFICIAL_ROLE_NAME_PAT, "APPROVAL_LINE", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, APPROVAL_GRADE_OCR_PAT, "APPROVAL_LINE", report, value_group="value", matches=matches)
        text = _sub_simple(text, DEPT_ROLE_NAME_COMPACT_PAT, "APPROVAL_LINE", report, matches=matches, value_group="value")
        text = _sub_simple(text, OFFICIAL_COMBINED_ROLE_NAME_PAT, "APPROVAL_LINE", report, matches=matches, value_group="value")
        text = _sub_approval_table_line(text, APPROVAL_TABLE_LINE_PAT, "APPROVAL_LINE", report, matches=matches)
        text = _sub_simple(text, APPROVAL_TABLE_INLINE_MULTI_PAT, "APPROVAL_LINE", report, matches=matches, value_group="value")
        text = _sub_keep_label(text, APPROVAL_ROLE_PAREN_NAME_PAT, "APPROVAL_LINE", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, APPROVAL_ROLE_DATE_NAME_PAT, "APPROVAL_LINE", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, APPROVAL_DATE_NAME_PAT, "APPROVAL_LINE", report, value_group="value", matches=matches)
        text = _sub_simple(text, APPROVAL_FLOW_PAT, "APPROVAL_FLOW", report, matches=matches, value_group="value")

    if use_region_context:
        text = _sub_keep_label(text, REGION_CONTEXT_PAT, "REGION", report, value_group="value", matches=matches)

    if use_doc_meta:
        # '시행 ○○과-1234'는 현장 누락이 잦아 DOC_META 일반룰보다 먼저 고정 처리
        text = _sub_keep_label(text, SIHAENG_DOCNO_PAT, "DOC_META", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, DOC_META_PAT, "DOC_META", report, value_group="value", matches=matches)
        text = _sub_keep_label(text, DOC_META_OCR_PAT, "DOC_META", report, value_group="value", matches=matches)
        text = _sub_simple(text, DOC_REF_INLINE_PAT, "DOC_META", report, matches=matches, value_group="value")
        text = _sub_simple(text, TEAM_EXT_PAT, "DOC_META", report, matches=matches, value_group="value")
        text = _sub_simple(text, EMAIL_PAT, "EMAIL", report, matches=matches, value_group="value")
        text = _sub_simple(text, PUBLIC_LEVEL_PAT, "DOC_META", report, matches=matches, value_group="value")

    return text, report, matches


# -----------------------------
# 2) 입력 추출 엔진
# -----------------------------


def read_text_file(path: str) -> str:
    encodings = ["utf-8", "cp949", "euc-kr"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    raise RuntimeError(f"파일 인코딩을 읽을 수 없습니다: {path}")


def write_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def resolve_output_artifacts(opts: dict[str, Any] | None = None) -> set[str]:
    opts = opts or {}
    label = str(opts.get("output_artifacts", "txt+pdf+report") or "txt+pdf+report").strip()
    if label not in OUTPUT_ARTIFACTS_MAP:
        label = "txt+pdf+report"
    return set(OUTPUT_ARTIFACTS_MAP[label])


@dataclass
class ExtractResult:
    text: str
    engine_used: str
    duration_sec: float
    notes: list[str]


def _run_cmd(cmd: list[str], timeout: int = 600) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr


def _extract_pdf_with_marker(pdf_path: str, work_dir: str) -> ExtractResult:
    start = time.time()
    marker_bin = shutil.which("marker_single")
    if not marker_bin:
        raise RuntimeError("marker_single 명령을 찾지 못했습니다. (pip install marker-pdf)")

    out_dir = Path(work_dir) / "marker_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        marker_bin,
        pdf_path,
        "--output_dir",
        str(out_dir),
        "--output_format",
        "markdown",
    ]
    code, _stdout, stderr = _run_cmd(cmd, timeout=1800)
    if code != 0:
        raise RuntimeError(f"marker-pdf 실패: {stderr.strip() or 'unknown error'}")

    md_candidates = sorted(out_dir.rglob("*.md"))
    if not md_candidates:
        raise RuntimeError("marker-pdf 출력에서 markdown 파일을 찾지 못했습니다.")

    md_text = md_candidates[0].read_text(encoding="utf-8", errors="ignore")
    return ExtractResult(
        text=md_text,
        engine_used="marker-pdf",
        duration_sec=time.time() - start,
        notes=["표/레이아웃 유지 목적 추출(markdown)"]
    )


def _extract_pdf_with_pymupdf4llm(pdf_path: str) -> ExtractResult:
    start = time.time()
    try:
        import pymupdf4llm  # type: ignore
    except Exception as e:
        raise RuntimeError(f"pymupdf4llm 미설치: {e}")

    md = pymupdf4llm.to_markdown(pdf_path)
    return ExtractResult(
        text=md,
        engine_used="pymupdf4llm",
        duration_sec=time.time() - start,
        notes=["텍스트 기반 PDF에 유리", "OCR 미포함"]
    )


def _extract_pdf_with_paddle(pdf_path: str) -> ExtractResult:
    """
    PaddleOCR 추출.
    - paddleocr가 PDF 직접 입력을 처리할 수 있는 버전에서는 pdf_path를 그대로 사용
    - 실패 시 명확한 에러 메시지 제공
    """
    start = time.time()
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception as e:
        raise RuntimeError(f"paddleocr 미설치: {e}")

    try:
        # paddleocr 3.x: use_textline_orientation 권장
        ocr = PaddleOCR(lang="korean", use_textline_orientation=True)
    except TypeError:
        # 구버전 호환
        ocr = PaddleOCR(lang="korean", use_angle_cls=True)
    except Exception as e:
        raise RuntimeError(f"PaddleOCR 초기화 실패: {e}")

    try:
        result = ocr.ocr(pdf_path, cls=True)
    except Exception as e:
        raise RuntimeError(
            "PaddleOCR PDF 처리 실패. "
            f"현재 환경/버전에서 PDF 직접 OCR이 어려울 수 있습니다: {e}"
        )

    lines: list[str] = []
    # result 형식: 페이지별 [[box, (text, score)], ...]
    for page_idx, page_items in enumerate(result or [], 1):
        lines.append(f"\n\n===== PAGE {page_idx} (paddle) =====")
        if not page_items:
            continue
        for item in page_items:
            try:
                text = item[1][0]
            except Exception:
                text = ""
            if text:
                lines.append(text)

    text = "\n".join(lines).strip()
    if not text:
        raise RuntimeError("PaddleOCR 결과 텍스트가 비어 있습니다.")

    return ExtractResult(
        text=text,
        engine_used="paddleocr",
        duration_sec=time.time() - start,
        notes=["한글 OCR 중심", "표/레이아웃 보존은 marker 대비 제한적일 수 있음"],
    )


def _extract_pdf_with_pypdf(pdf_path: str) -> ExtractResult:
    start = time.time()
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as e:
        raise RuntimeError(f"pypdf 미설치: {e}")

    reader = PdfReader(pdf_path)
    texts: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        t = page.extract_text() or ""
        texts.append(f"\n\n===== PAGE {i} =====\n{t}")

    return ExtractResult(
        text="".join(texts),
        engine_used="pypdf",
        duration_sec=time.time() - start,
        notes=["빠른 텍스트 추출", "표/레이아웃 보존 약함"]
    )


def extract_document(path: str, engine: str = "auto") -> ExtractResult:
    ext = Path(path).suffix.lower()
    if ext in {".txt", ".md", ".csv", ".log"}:
        t0 = time.time()
        return ExtractResult(
            text=read_text_file(path),
            engine_used="plain-text",
            duration_sec=time.time() - t0,
            notes=[]
        )

    if ext != ".pdf":
        raise RuntimeError("지원 형식은 현재 txt/md/csv/log/pdf 입니다.")

    if engine not in {"auto", "marker", "paddle", "pymupdf", "pypdf"}:
        raise RuntimeError(f"알 수 없는 엔진: {engine}")

    if engine == "marker":
        work = str(Path(path).with_suffix("")) + "_tmp"
        return _extract_pdf_with_marker(path, work)
    if engine == "paddle":
        return _extract_pdf_with_paddle(path)
    if engine == "pymupdf":
        return _extract_pdf_with_pymupdf4llm(path)
    if engine == "pypdf":
        return _extract_pdf_with_pypdf(path)

    # auto
    errors: list[str] = []
    try:
        work = str(Path(path).with_suffix("")) + "_tmp"
        return _extract_pdf_with_marker(path, work)
    except Exception as e:
        errors.append(f"marker 실패: {e}")

    try:
        return _extract_pdf_with_paddle(path)
    except Exception as e:
        errors.append(f"paddle 실패: {e}")

    try:
        return _extract_pdf_with_pymupdf4llm(path)
    except Exception as e:
        errors.append(f"pymupdf 실패: {e}")

    try:
        return _extract_pdf_with_pypdf(path)
    except Exception as e:
        errors.append(f"pypdf 실패: {e}")

    raise RuntimeError("PDF 추출 실패\n- " + "\n- ".join(errors))


# -----------------------------
# 3) PDF 네이티브 레닥션
# -----------------------------


def _redaction_search_terms(matches: list[RedactionMatch]) -> list[RedactionMatch]:
    seen: set[tuple[str, str]] = set()
    ordered: list[RedactionMatch] = []
    for item in sorted(matches, key=lambda x: (-len(x.text), x.tag, x.text)):
        key = (item.tag, item.text)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def redact_pdf_native(pdf_path: str, output_pdf_path: str, matches: list[RedactionMatch]) -> dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception as e:
        raise RuntimeError(f"PyMuPDF 미설치로 PDF 레닥션을 수행할 수 없습니다: {e}")

    search_terms = _redaction_search_terms(matches)
    if not search_terms:
        raise RuntimeError("PDF 레닥션 대상 문자열이 없어 레닥션을 건너뜁니다.")

    doc = fitz.open(pdf_path)
    try:
        annotations_added = 0
        terms_hit: list[str] = []

        for item in search_terms:
            found_for_term = False
            variants = [item.text]
            compact = re.sub(r"\s+", "", item.text)
            if compact and compact != item.text:
                variants.append(compact)

            for page_num in range(doc.page_count):
                page = doc[page_num]
                page_hits = 0
                for variant in variants:
                    if len(variant) < 2:
                        continue
                    rects = page.search_for(variant)
                    if rects:
                        found_for_term = True
                    for rect in rects:
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                        annotations_added += 1
                        page_hits += 1
                if page_hits:
                    continue

            if found_for_term:
                terms_hit.append(f"{item.tag}:{item.text}")

        if annotations_added == 0:
            raise RuntimeError("검색 가능한 PDF 텍스트를 찾지 못해 네이티브 레닥션을 적용하지 못했습니다.")

        for page_num in range(doc.page_count):
            doc[page_num].apply_redactions()

        doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    # 사후 무결성 검증: 결과 PDF에서 잔존 문자열 재검색
    verify_doc = fitz.open(output_pdf_path)
    try:
        residual_hits = 0
        residual_terms: list[str] = []
        for item in search_terms:
            variants = [item.text]
            compact = re.sub(r"\s+", "", item.text)
            if compact and compact != item.text:
                variants.append(compact)

            found = False
            for page_num in range(verify_doc.page_count):
                page = verify_doc[page_num]
                for variant in variants:
                    if len(variant) < 2:
                        continue
                    rects = page.search_for(variant)
                    if rects:
                        residual_hits += len(rects)
                        found = True
                if found:
                    break
            if found:
                residual_terms.append(f"{item.tag}:{item.text}")
    finally:
        verify_doc.close()

    verified = residual_hits == 0
    status = "applied" if verified else "failed"
    reason = "레닥션 적용 및 잔존 0건 검증 완료" if verified else "결과 PDF에 마스킹 대상 문자열이 일부 잔존합니다"

    return {
        "enabled": True,
        "status": status,
        "output_file": output_pdf_path,
        "targets_requested": len(search_terms),
        "targets_hit": len(terms_hit),
        "annotations_added": annotations_added,
        "matched_terms_preview": terms_hit[:20],
        "verification": {
            "residual_hits": residual_hits,
            "residual_terms_preview": residual_terms[:20],
            "verified": verified,
            "reason": reason,
        },
    }


def apply_manual_redactions(
    source_pdf_path: str,
    output_pdf_path: str,
    boxes: list[ManualRedactionBox],
) -> dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception as e:
        raise RuntimeError(f"PyMuPDF 미설치로 수동 레닥션을 수행할 수 없습니다: {e}")

    if not boxes:
        raise RuntimeError("저장할 수동 마스킹 박스가 없습니다.")

    doc = fitz.open(source_pdf_path)
    try:
        applied = 0
        grouped: dict[int, list[tuple[float, float, float, float]]] = {}
        for box in boxes:
            grouped.setdefault(box.page_index, []).append(box.rect)

        for page_index, rects in grouped.items():
            if page_index < 0 or page_index >= doc.page_count:
                continue
            page = doc[page_index]
            for rect in rects:
                x0, y0, x1, y1 = rect
                norm = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if norm.width < 2 or norm.height < 2:
                    continue
                page.add_redact_annot(norm, fill=(0, 0, 0))
                applied += 1

        if applied == 0:
            raise RuntimeError("유효한 수동 마스킹 영역이 없어 저장하지 못했습니다.")

        for page_index in grouped:
            if 0 <= page_index < doc.page_count:
                doc[page_index].apply_redactions()

        doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    return {
        "status": "applied",
        "output_file": output_pdf_path,
        "boxes_applied": applied,
        "pages_touched": sorted(grouped.keys()),
    }


# -----------------------------
# 4) 처리 파이프라인
# -----------------------------


def safe_output_paths(infile: str, outdir: str | None = None) -> dict[str, str]:
    base = os.path.basename(infile)
    name, _ = os.path.splitext(base)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = outdir or os.path.dirname(infile)
    return {
        "masked_txt": os.path.join(out_dir, f"{name}.masked.{ts}.txt"),
        "extracted_txt": os.path.join(out_dir, f"{name}.extracted.{ts}.txt"),
        "report_json": os.path.join(out_dir, f"{name}.report.{ts}.json"),
        "masked_pdf": os.path.join(out_dir, f"{name}.masked.{ts}.pdf"),
        "manual_pdf": os.path.join(out_dir, f"{name}_manual_redacted.pdf"),
    }


def _llm_bin_exists(cmd: str) -> bool:
    if not cmd.strip():
        return False
    argv = shlex.split(cmd)
    if not argv:
        return False
    return shutil.which(argv[0]) is not None


def _normalize_llm_candidate_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip(" ,;:/"))


def _score_llm_candidate(tag: str, value: str, frequency: int) -> int:
    context_weight = {
        "LEGAL_PARTY": 90,
        "APPROVAL_LINE": 70,
        "COMPANY": 50,
    }.get(tag, 0)

    compact = re.sub(r"\s+", "", value)
    length = len(compact)
    if tag in {"LEGAL_PARTY", "APPROVAL_LINE"}:
        if 2 <= length <= 4:
            length_weight = 28
        elif 5 <= length <= 6:
            length_weight = 18
        else:
            length_weight = 8
    else:
        if 3 <= length <= 12:
            length_weight = 24
        elif 13 <= length <= 24:
            length_weight = 14
        else:
            length_weight = 6

    frequency_weight = min(frequency, 5) * 12
    return context_weight + length_weight + frequency_weight


def _collect_llm_candidates(masked_text: str) -> list[LLMCandidate]:
    specs: list[tuple[re.Pattern[str], str, str]] = [
        (re.compile(r"(?P<label>\b(?:원고|피고|신청인|피신청인|청구인|피청구인|항고인|피항고인)\b\s+)(?P<value>[가-힣]{2,6})(?=[^가-힣]|$)"), "LEGAL_PARTY", "당사자명"),
        (re.compile(r"(?P<label>\b(?:기안자|검토자|협조자|결재자|담당자|주무관|팀장|과장|국장)\b\s+)(?P<value>[가-힣]{2,6})(?=[^가-힣]|$)"), "APPROVAL_LINE", "결재/담당자명"),
        (re.compile(r"(?P<label>\b(?:회사명|법인명|상호|기관명)\b\s*[:：]?\s*)(?P<value>[^\n\[\]]{2,40})"), "COMPANY", "기관/법인"),
    ]

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for pat, tag, label in specs:
        for m in pat.finditer(masked_text):
            candidate = _normalize_llm_candidate_value(m.group("value"))
            if not candidate or candidate.startswith("[") or candidate.endswith("]"):
                continue

            key = (tag, candidate)
            occurrence = LLMCandidateOccurrence(
                start=m.start(),
                end=m.end(),
                replacement=f"{m.group('label')}[{tag}]",
                context=m.group(0),
            )
            bucket = grouped.setdefault(
                key,
                {
                    "tag": tag,
                    "label": label,
                    "value": candidate,
                    "occurrences": [],
                },
            )
            bucket["occurrences"].append(occurrence)

    candidates: list[LLMCandidate] = []
    for bucket in grouped.values():
        occurrences = tuple(bucket["occurrences"])
        candidates.append(
            LLMCandidate(
                tag=bucket["tag"],
                label=bucket["label"],
                value=bucket["value"],
                normalized_value=bucket["value"],
                occurrences=occurrences,
                score=_score_llm_candidate(bucket["tag"], bucket["value"], len(occurrences)),
            )
        )

    candidates.sort(
        key=lambda item: (
            -item.score,
            -len(item.occurrences),
            item.occurrences[0].start,
            item.value,
        )
    )
    return candidates


def _apply_llm_candidate_replacements(text: str, occurrences: list[LLMCandidateOccurrence]) -> str:
    if not occurrences:
        return text

    out: list[str] = []
    cursor = 0
    for occ in sorted(occurrences, key=lambda item: (item.start, item.end)):
        if occ.start < cursor:
            continue
        out.append(text[cursor:occ.start])
        out.append(occ.replacement)
        cursor = occ.end
    out.append(text[cursor:])
    return "".join(out)


LLM_DECISION_LINE_PAT = re.compile(
    r"(?im)^\s*(?:FINAL\s+ANSWER|ANSWER|RESULT|DECISION|판정|결론)?\s*[:\-]?\s*(YES|NO)\b"
)
LLM_DECISION_TOKEN_PAT = re.compile(r"\b(YES|NO)\b")


def _parse_llm_yes_no_output(output: str) -> bool:
    out_upper = output.upper()

    line_match = LLM_DECISION_LINE_PAT.search(out_upper)
    if line_match:
        return line_match.group(1) == "YES"

    token_match = LLM_DECISION_TOKEN_PAT.search(out_upper)
    if token_match:
        return token_match.group(1) == "YES"

    raise ValueError("llm_yes_no_unparseable")


def _llm_yes_no_decision(cmd: str, model_path: str, label: str, value: str, context: str = "") -> bool:
    prompt = (
        "다음 값이 한국 행정/소송 문서에서 개인정보 또는 식별 메타데이터로 마스킹 대상인지 판단하세요.\n"
        f"라벨: {label}\n"
        f"값: {value}\n"
        f"문맥: {context}\n\n"
        "규칙:\n"
        "- 사람 이름, 법인명, 사건명, 법원명, 문서번호/공문번호 성격이면 YES\n"
        "- 일반 절차/지침/업무 용어면 NO\n"
        "출력은 반드시 한 단어로만: YES 또는 NO"
    )

    argv = shlex.split(cmd)
    full_cmd = argv + ["-m", model_path, "-ngl", "0", "-c", "1024", "-n", "8", "--temp", "0", "-p", prompt]
    p = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return _parse_llm_yes_no_output(out)


def _llm_failure_reason(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, ValueError):
        return "unparseable_response"
    if isinstance(exc, FileNotFoundError):
        return "llm_command_not_found"
    return "runtime_error"


def llm_refine_masking(masked_text: str, opts: dict[str, Any], counts: dict[str, int]) -> tuple[str, dict[str, Any]]:
    use_llm = bool(opts.get("local_llm_refine", False))
    llm_cmd = str(opts.get("local_llm_cmd", "llama-cli")).strip()
    llm_model = str(opts.get("local_llm_model", "")).strip()
    max_calls = max(int(opts.get("local_llm_max_calls", 25)), 0)

    meta = {
        "enabled": use_llm,
        "applied": False,
        "skipped_reason": None,
        "calls": 0,
        "accepted": 0,
        "rejected": 0,
        "total_candidates": 0,
        "selected_candidates": 0,
    }
    if not use_llm:
        meta["skipped_reason"] = "disabled"
        return masked_text, meta
    if not llm_model:
        meta["skipped_reason"] = "model_path_missing"
        return masked_text, meta
    if not _llm_bin_exists(llm_cmd):
        meta["skipped_reason"] = "llm_command_not_found"
        return masked_text, meta

    candidates = _collect_llm_candidates(masked_text)
    meta["total_candidates"] = len(candidates)
    selected = candidates[:max_calls]
    meta["selected_candidates"] = len(selected)

    accepted_occurrences: list[LLMCandidateOccurrence] = []
    for candidate in selected:
        meta["calls"] += 1
        try:
            ok = _llm_yes_no_decision(
                llm_cmd,
                llm_model,
                candidate.label,
                candidate.value,
                context=candidate.occurrences[0].context,
            )
        except Exception as exc:
            if not meta["skipped_reason"]:
                meta["skipped_reason"] = _llm_failure_reason(exc)
            meta["rejected"] += 1
            continue
        if ok:
            meta["accepted"] += 1
            counts[candidate.tag] = counts.get(candidate.tag, 0) + len(candidate.occurrences)
            accepted_occurrences.extend(candidate.occurrences)
            continue
        meta["rejected"] += 1

    text = _apply_llm_candidate_replacements(masked_text, accepted_occurrences)
    meta["applied"] = meta["accepted"] > 0
    return text, meta


def sanitize_for_logging(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize_for_logging(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_logging(v) for v in obj]
    if isinstance(obj, str):
        # 민감 원문 노출 방지: preview 류는 마스킹
        return re.sub(r"[가-힣A-Za-z0-9_]+:[^,\]\}\n]{2,80}", "[REDACTED_PREVIEW]", obj)
    return obj


def enforce_quality_gate_or_raise(report: dict[str, Any]) -> None:
    checks = report.get("product_checks", {})
    if checks.get("quality_gate_passed", False):
        return
    reason = report.get("pdf_redaction", {}).get("verification", {}).get("reason") or report.get("pdf_redaction", {}).get("reason") or "quality gate failed"
    report_path = report.get("outputs", {}).get("report_path", "")
    raise RuntimeError(f"[QUALITY_GATE_BLOCK] 배포 차단: {reason}. report={report_path}")


def process_file(
    infile: str,
    outdir: str | None = None,
    opts: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    opts = opts or {}
    artifacts = resolve_output_artifacts(opts)
    output_paths = safe_output_paths(infile, outdir=outdir)

    extract_engine = opts.get("extract_engine", "auto")
    extract_result = extract_document(infile, engine=extract_engine)

    masked, counts, redaction_matches, chunk_queue = process_masking_queue(extract_result.text, opts)

    masked, llm_refine = llm_refine_masking(masked, opts, counts)
    extracted_path = output_paths["extracted_txt"] if "txt" in artifacts else None
    masked_path = output_paths["masked_txt"] if "txt" in artifacts else None
    report_path = output_paths["report_json"] if "report" in artifacts else None
    masked_pdf_path = output_paths["masked_pdf"]

    if extracted_path:
        write_text_file(extracted_path, extract_result.text)
    if masked_path:
        write_text_file(masked_path, masked)

    pdf_redaction_enabled = bool(opts.get("pdf_redaction", True)) and "pdf" in artifacts
    preview_pdf_source_path: str | None = None
    pdf_redaction_result: dict[str, Any] = {
        "enabled": bool(opts.get("pdf_redaction", True)),
        "status": "skipped",
        "output_file": None,
        "reason": "입력 파일이 PDF가 아닙니다." if Path(infile).suffix.lower() != ".pdf" else "비활성화됨 또는 산출물 선택에서 제외됨",
    }
    if Path(infile).suffix.lower() == ".pdf" and bool(opts.get("pdf_redaction", True)):
        try:
            target_pdf_path = masked_pdf_path if "pdf" in artifacts else os.path.join(
                tempfile.mkdtemp(prefix="yangcheon_masker_pdf_preview_"),
                Path(masked_pdf_path).name,
            )
            pdf_redaction_result = redact_pdf_native(infile, target_pdf_path, redaction_matches)
            preview_pdf_source_path = target_pdf_path
            if "pdf" not in artifacts:
                pdf_redaction_result["output_file"] = None
        except Exception as e:
            pdf_redaction_result = {
                "enabled": bool(opts.get("pdf_redaction", True)),
                "status": "failed",
                "output_file": None,
                "reason": str(e),
            }
    elif Path(infile).suffix.lower() == ".pdf":
        preview_pdf_source_path = infile

    report = {
        "app_version": APP_VERSION,
        "input_file": infile,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "extract": {
            "engine_selected": extract_engine,
            "engine_used": extract_result.engine_used,
            "duration_sec": round(extract_result.duration_sec, 3),
            "notes": extract_result.notes,
            "chars": len(extract_result.text),
        },
        "rules": {
            "profile": str(opts.get("profile", "official") or "official"),
            "rrn": bool(opts.get("rrn", True)),
            "phone": bool(opts.get("phone", True)),
            "business_reg": bool(opts.get("business_reg", True)),
            "name_context": bool(opts.get("name", True)),
            "address_context": bool(opts.get("address", True)),
            "place_seoul_plus_yangcheon": bool(opts.get("place", True)),
            "legal_party": bool(opts.get("legal_party", True)),
            "company": bool(opts.get("company", True)),
            "court": bool(opts.get("court", True)),
            "case_title": bool(opts.get("case_title", True)),
            "case_number": bool(opts.get("case_number", True)),
            "law_firm": bool(opts.get("law_firm", True)),
            "attorney": bool(opts.get("attorney", True)),
            "approval_line": bool(opts.get("approval_line", True)),
            "region_context": bool(opts.get("region_context", True)),
            "doc_meta": bool(opts.get("doc_meta", True)),
            "pdf_redaction": pdf_redaction_enabled,
            "local_llm_refine": bool(opts.get("local_llm_refine", False)),
            "local_llm_cmd": str(opts.get("local_llm_cmd", "llama-cli")),
            "local_llm_model": str(opts.get("local_llm_model", "")),
            "local_llm_max_calls": int(opts.get("local_llm_max_calls", 25)),
            "output_artifacts": sorted(artifacts),
        },
        "counts": counts,
        "chunk_queue": chunk_queue,
        "local_llm_refine": llm_refine,
        "pdf_redaction": pdf_redaction_result,
        "product_checks": {
            "pdf_input": Path(infile).suffix.lower() == ".pdf",
            "native_redaction_verified": bool(pdf_redaction_result.get("verification", {}).get("verified", False)),
            "native_redaction_status": pdf_redaction_result.get("status"),
            "quality_gate_passed": (
                (Path(infile).suffix.lower() != ".pdf")
                or ("pdf" not in artifacts)
                or not bool(opts.get("pdf_redaction", True))
                or bool(pdf_redaction_result.get("verification", {}).get("verified", False))
            ),
        },
        "outputs": {
            "extracted_file": extracted_path,
            "masked_file": masked_path,
            "masked_pdf_file": pdf_redaction_result.get("output_file"),
            "report_path": report_path,
            "manual_pdf_path": output_paths["manual_pdf"],
            "preview_pdf_source_file": preview_pdf_source_path,
        },
    }

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    enforce_quality_gate_or_raise(report)

    return extracted_path, masked_path, report_path, report


def extract_result_text_for_preview(infile: str, opts: dict[str, Any]) -> str:
    return extract_document(infile, engine=opts.get("extract_engine", "auto")).text


def mask_text_for_preview(extracted_text: str, opts: dict[str, Any]) -> str:
    masked, _counts, _matches, _meta = process_masking_queue(extracted_text, opts)
    masked, _llm_meta = llm_refine_masking(masked, opts, {})
    return masked


# -----------------------------
# 4) GUI
# -----------------------------

class MaskerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("문서 마스킹 도구")
        self.geometry("1460x980")
        self.minsize(1240, 860)
        self.configure(bg="#eef2f7")

        self.selected_files: list[str] = []
        self.worker: threading.Thread | None = None
        self.stop_requested = False
        self._syncing_text_scroll = False
        self.current_output_dir: str | None = None
        self.current_manual_output_path: str | None = None
        self.current_input_pdf_path: str | None = None
        self.current_masked_preview_pdf_path: str | None = None
        self.original_pdf_doc: Any = None
        self.masked_pdf_doc: Any = None
        self.original_pdf_page_index = 0
        self.masked_pdf_page_index = 0
        self.preview_images: dict[str, tk.PhotoImage] = {}
        self.preview_layouts: dict[str, tuple[PreviewRenderState, float, float]] = {}
        self.manual_boxes: list[ManualRedactionBox] = []
        self.current_drag_rect_id: int | None = None
        self.drag_start_canvas: tuple[float, float] | None = None
        self.log_file_path = os.path.join(tempfile.gettempdir(), "document_masker_gui_latest.log")

        self.ui_font_family = self._resolve_font_family("Pretendard")
        self.ui_mono_family = self._resolve_font_family("D2Coding")
        self._apply_modern_style()

        self.var_rrn = tk.BooleanVar(value=True)
        self.var_phone = tk.BooleanVar(value=True)
        self.var_business_reg = tk.BooleanVar(value=True)
        self.var_name = tk.BooleanVar(value=True)
        self.var_address = tk.BooleanVar(value=True)
        self.var_place = tk.BooleanVar(value=True)
        self.var_legal_party = tk.BooleanVar(value=True)
        self.var_company = tk.BooleanVar(value=True)
        self.var_court = tk.BooleanVar(value=True)
        self.var_case_title = tk.BooleanVar(value=True)
        self.var_case_number = tk.BooleanVar(value=True)
        self.var_law_firm = tk.BooleanVar(value=True)
        self.var_attorney = tk.BooleanVar(value=True)
        self.var_approval_line = tk.BooleanVar(value=True)
        self.var_region_context = tk.BooleanVar(value=True)
        self.var_doc_meta = tk.BooleanVar(value=True)
        self.var_pdf_redaction = tk.BooleanVar(value=True)
        self.var_local_llm_refine = tk.BooleanVar(value=False)
        self.var_local_llm_cmd = tk.StringVar(value="llama-cli")
        self.var_local_llm_model = tk.StringVar(value="")
        self.var_local_llm_max_calls = tk.IntVar(value=25)
        self.var_engine = tk.StringVar(value="auto")
        self.var_profile = tk.StringVar(value="official")
        self.var_output_artifacts = tk.StringVar(value="txt+pdf+report")
        self.var_sync_scroll = tk.BooleanVar(value=True)
        self.var_sync_pdf_page = tk.BooleanVar(value=True)

        self._build_ui()
        self._apply_profile_preset(self.var_profile.get(), initial=True)
        self.bind("<Configure>", self._on_window_resize)

    def _resolve_font_family(self, preferred: str) -> str:
        try:
            families = set(tkfont.families(self))
        except Exception:
            return preferred
        if preferred in families:
            return preferred
        for fallback in [preferred, "Pretendard", "SUIT", "Spoqa Han Sans Neo", "Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic", "NanumGothic", "D2Coding", "Consolas"]:
            if fallback in families:
                return fallback
        return "TkDefaultFont"

    def _apply_modern_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.option_add("*Font", (self.ui_font_family, 10))
        self.option_add("*TCombobox*Listbox*Font", (self.ui_font_family, 10))
        style.configure(".", background="#eef2f7", foreground="#16212e")
        style.configure("TFrame", background="#eef2f7")
        style.configure("TLabelframe", background="#ffffff", borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#243447", font=(self.ui_font_family, 10, "bold"))
        style.configure("TLabel", background="#eef2f7", foreground="#243447")
        style.configure("Muted.TLabel", background="#eef2f7", foreground="#5b6b7a")
        style.configure("TCheckbutton", background="#ffffff", foreground="#243447")
        style.configure("TButton", font=(self.ui_font_family, 10, "bold"), padding=(12, 8), relief="flat", borderwidth=0, background="#ffffff", foreground="#142033")
        style.map("TButton", background=[("active", "#f4f7fb"), ("pressed", "#dce6f4")])
        style.configure("Accent.TButton", background="#1f6feb", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#2b7cff"), ("pressed", "#1858be")], foreground=[("active", "#ffffff")])
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#16212e", padding=6)
        style.configure("TCombobox", fieldbackground="#ffffff", foreground="#16212e", padding=4)
        style.configure("TNotebook", background="#eef2f7", borderwidth=0)
        # Windows 고배율(125~150%)에서 탭 라벨 잘림 방지: 탭 높이 여유 + 폰트 명시
        style.configure(
            "TNotebook.Tab",
            padding=(16, 14),
            background="#dde6f0",
            foreground="#3a4a5a",
            font=(self.ui_font_family, 10, "bold"),
        )
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#16212e")])
        style.configure("Horizontal.TProgressbar", troughcolor="#dbe4ef", background="#1f6feb", borderwidth=0)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True, padx=18, pady=18)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        hero = ttk.LabelFrame(shell, text="작업 준비")
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(4, weight=1)
        hero.columnconfigure(5, weight=1)
        ttk.Label(hero, text="TXT/PDF를 선택한 뒤 프로필과 산출물을 정하면, 좌우 대조 미리보기와 PDF 수동 보정을 한 화면에서 이어서 진행할 수 있습니다.", style="Muted.TLabel").grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(14, 6))
        ttk.Label(hero, text="입력 파일").grid(row=1, column=0, sticky="w", padx=16)
        self.entry_files = tk.Text(hero, height=4, wrap="word", bg="#f8fafc", fg="#132238", relief="flat", bd=0, highlightthickness=1, highlightbackground="#d7e1ee", font=(self.ui_font_family, 10))
        self.entry_files.grid(row=2, column=0, columnspan=6, sticky="ew", padx=16, pady=(4, 10))
        ttk.Button(hero, text="파일 선택", style="Accent.TButton", command=self.select_files).grid(row=3, column=0, sticky="w", padx=(16, 8), pady=(0, 14))
        ttk.Button(hero, text="목록 지우기", command=self.clear_files).grid(row=3, column=1, sticky="w", pady=(0, 14))
        ttk.Label(hero, text="출력 폴더").grid(row=1, column=4, sticky="w", padx=16)
        self.outdir_var = tk.StringVar(value="")
        ttk.Entry(hero, textvariable=self.outdir_var).grid(row=2, column=4, columnspan=2, sticky="ew", padx=(16, 16), pady=(4, 10))
        ttk.Button(hero, text="폴더 선택", command=self.select_outdir).grid(row=3, column=4, sticky="w", padx=16, pady=(0, 14))

        controls = ttk.Frame(shell)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        controls.columnconfigure(0, weight=4)
        controls.columnconfigure(1, weight=2)

        self.rules_card = ttk.LabelFrame(controls, text="마스킹 규칙")
        self.rules_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.rule_vars: dict[str, tk.BooleanVar] = {
            "rrn": self.var_rrn,
            "phone": self.var_phone,
            "business_reg": self.var_business_reg,
            "name": self.var_name,
            "address": self.var_address,
            "place": self.var_place,
            "legal_party": self.var_legal_party,
            "company": self.var_company,
            "court": self.var_court,
            "case_title": self.var_case_title,
            "case_number": self.var_case_number,
            "law_firm": self.var_law_firm,
            "attorney": self.var_attorney,
            "approval_line": self.var_approval_line,
            "region_context": self.var_region_context,
            "doc_meta": self.var_doc_meta,
        }
        self.profile_rule_presets: dict[str, dict[str, bool]] = {
            "official": {
                "approval_line": True,
                "region_context": True,
                "doc_meta": True,
                "court": True,
            },
            "legal": {
                "approval_line": False,
                "region_context": False,
                "doc_meta": False,
                "court": True,
            },
        }
        rule_specs = [
            ("rrn", "주민등록번호", self.var_rrn), ("phone", "전화번호", self.var_phone), ("business_reg", "사업자등록번호", self.var_business_reg), ("name", "이름(문맥)", self.var_name),
            ("address", "주소(문맥)", self.var_address), ("place", "서울+양천 지명", self.var_place), ("legal_party", "원고/피고 등 당사자명", self.var_legal_party), ("company", "회사/법인명", self.var_company),
            ("court", "법원명", self.var_court), ("case_title", "사건명", self.var_case_title), ("case_number", "사건번호", self.var_case_number), ("law_firm", "법무법인/사무소", self.var_law_firm),
            ("attorney", "변호사명", self.var_attorney), ("approval_line", "기안/검토/결재선", self.var_approval_line), ("region_context", "관할/소재지(지역)", self.var_region_context), ("doc_meta", "수신/참조/시행번호/담당부서", self.var_doc_meta),
        ]
        for idx, (key, label, variable) in enumerate(rule_specs):
            btn = tk.Checkbutton(
                self.rules_card,
                text=label,
                variable=variable,
                indicatoron=False,
                selectcolor="#1f6feb",
                activebackground="#2b7cff",
                activeforeground="#ffffff",
                bg="#e9eef6",
                fg="#1a2a3d",
                font=(self.ui_font_family, 10),
                relief="flat",
                bd=0,
                padx=10,
                pady=5,
                highlightthickness=0,
            )
            btn.grid(row=idx // 8, column=idx % 8, sticky="we", padx=5, pady=4)
            variable.trace_add("write", lambda *_args, b=btn, v=variable: self._refresh_rule_toggle_button(b, v))
            self._refresh_rule_toggle_button(btn, variable)

        mode_card = ttk.LabelFrame(controls, text="처리 모드 / 실행")
        mode_card.grid(row=0, column=1, sticky="nsew")
        ttk.Label(mode_card, text="문서 프로필").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        self.profile_cmb = ttk.Combobox(mode_card, textvariable=self.var_profile, values=["official", "legal"], state="readonly", width=16)
        self.profile_cmb.grid(row=1, column=0, sticky="ew", padx=12)
        self.profile_cmb.bind("<<ComboboxSelected>>", self._on_profile_changed)
        ttk.Label(mode_card, text="PDF 추출 엔진").grid(row=2, column=0, sticky="w", padx=12, pady=(12, 4))
        ttk.Combobox(mode_card, textvariable=self.var_engine, values=["auto", "marker", "paddle", "pymupdf", "pypdf"], state="readonly", width=16).grid(row=3, column=0, sticky="ew", padx=12)
        ttk.Label(mode_card, text="산출물 선택").grid(row=4, column=0, sticky="w", padx=12, pady=(12, 4))
        ttk.Combobox(mode_card, textvariable=self.var_output_artifacts, values=OUTPUT_ARTIFACT_LABELS, state="readonly", width=16).grid(row=5, column=0, sticky="ew", padx=12)
        ttk.Checkbutton(mode_card, text="자동 PDF 레닥션", variable=self.var_pdf_redaction).grid(row=6, column=0, sticky="w", padx=12, pady=(12, 4))
        ttk.Checkbutton(mode_card, text="텍스트 동기 스크롤", variable=self.var_sync_scroll).grid(row=7, column=0, sticky="w", padx=12, pady=4)
        ttk.Checkbutton(mode_card, text="PDF 동기 페이지 이동", variable=self.var_sync_pdf_page).grid(row=8, column=0, sticky="w", padx=12, pady=(4, 8))
        self.var_rules_visible = tk.BooleanVar(value=True)
        self.btn_toggle_rules = ttk.Button(mode_card, text="마스킹 규칙 숨기기", command=self._toggle_rules_panel)
        self.btn_toggle_rules.grid(row=9, column=0, sticky="ew", padx=12, pady=(0, 8))
        mode_card.columnconfigure(0, weight=1)

        ttk.Separator(mode_card, orient="horizontal").grid(row=10, column=0, sticky="ew", padx=12, pady=(8, 8))
        self.btn_run = ttk.Button(mode_card, text="마스킹 실행", style="Accent.TButton", command=self.run_masking)
        self.btn_run.grid(row=11, column=0, sticky="ew", padx=12, pady=(2, 6))
        ttk.Button(mode_card, text="중지 요청", command=self.request_stop).grid(row=12, column=0, sticky="ew", padx=12, pady=4)
        ttk.Button(mode_card, text="창 비우기", command=self.clear_texts).grid(row=13, column=0, sticky="ew", padx=12, pady=(4, 8))
        self.progress = ttk.Progressbar(mode_card, mode="determinate", length=220)
        self.progress.grid(row=14, column=0, sticky="ew", padx=12, pady=(2, 6))
        self.lbl_progress = ttk.Label(mode_card, text="0/0", style="Muted.TLabel")
        self.lbl_progress.grid(row=15, column=0, sticky="e", padx=(0, 12), pady=(0, 12))
        mode_card.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(shell)
        notebook.grid(row=2, column=0, sticky="nsew")
        text_tab = ttk.Frame(notebook)
        pdf_tab = ttk.Frame(notebook)
        notebook.add(text_tab, text="텍스트 비교")
        notebook.add(pdf_tab, text="PDF 수동 보정")

        text_tab.columnconfigure(0, weight=1)
        text_tab.columnconfigure(1, weight=1)
        text_tab.rowconfigure(0, weight=1)
        left_card = ttk.LabelFrame(text_tab, text="원문 / 추출 텍스트")
        right_card = ttk.LabelFrame(text_tab, text="마스킹 결과")
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        left_card.rowconfigure(0, weight=1)
        left_card.columnconfigure(0, weight=1)
        right_card.rowconfigure(0, weight=1)
        right_card.columnconfigure(0, weight=1)
        self.txt_input = tk.Text(left_card, wrap="word", bg="#fbfdff", fg="#15273b", relief="flat", bd=0, padx=16, pady=16, highlightthickness=1, highlightbackground="#d7e1ee", font=(self.ui_mono_family, 11))
        self.txt_output = tk.Text(right_card, wrap="word", bg="#fbfdff", fg="#15273b", relief="flat", bd=0, padx=16, pady=16, highlightthickness=1, highlightbackground="#d7e1ee", font=(self.ui_mono_family, 11))
        self.txt_input.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        self.txt_output.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        self.txt_input_scroll = ttk.Scrollbar(left_card, orient="vertical", command=lambda *args: self._on_text_scrollbar(self.txt_input, self.txt_output, *args))
        self.txt_output_scroll = ttk.Scrollbar(right_card, orient="vertical", command=lambda *args: self._on_text_scrollbar(self.txt_output, self.txt_input, *args))
        self.txt_input_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)
        self.txt_output_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)
        self.txt_input.configure(yscrollcommand=lambda first, last: self._on_text_yscroll("input", first, last))
        self.txt_output.configure(yscrollcommand=lambda first, last: self._on_text_yscroll("output", first, last))

        pdf_tab.columnconfigure(0, weight=1)
        pdf_tab.columnconfigure(1, weight=1)
        pdf_tab.rowconfigure(1, weight=1)
        toolbar = ttk.LabelFrame(pdf_tab, text="수동 보정 도구")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="우측 미리보기 위에서 드래그해 추가 레닥션 박스를 지정합니다. 저장 시 새 파일명은 *_manual_redacted.pdf 형식입니다.", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        nav_row = ttk.Frame(toolbar)
        nav_row.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 6))
        ttk.Button(nav_row, text="원문 이전", command=lambda: self._change_pdf_page("original", -1)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(nav_row, text="원문 다음", command=lambda: self._change_pdf_page("original", 1)).grid(row=0, column=1, padx=6)
        self.lbl_original_page = ttk.Label(nav_row, text="원문 0/0", style="Muted.TLabel")
        self.lbl_original_page.grid(row=0, column=2, padx=(4, 16))
        ttk.Button(nav_row, text="결과 이전", command=lambda: self._change_pdf_page("masked", -1)).grid(row=0, column=3, padx=6)
        ttk.Button(nav_row, text="결과 다음", command=lambda: self._change_pdf_page("masked", 1)).grid(row=0, column=4, padx=6)
        self.lbl_masked_page = ttk.Label(nav_row, text="결과 0/0", style="Muted.TLabel")
        self.lbl_masked_page.grid(row=0, column=5, padx=(4, 0))

        action_row = ttk.Frame(toolbar)
        action_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        ttk.Button(action_row, text="실행취소", command=self.undo_manual_box).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(action_row, text="전체초기화", command=self.reset_manual_boxes).grid(row=0, column=1, padx=6)
        ttk.Button(action_row, text="수동 마스킹 저장", style="Accent.TButton", command=self.save_manual_redactions).grid(row=0, column=2, padx=6)
        self.lbl_manual_state = ttk.Label(action_row, text="박스 0개", style="Muted.TLabel")
        self.lbl_manual_state.grid(row=0, column=3, padx=(12, 0), sticky="e")
        action_row.columnconfigure(3, weight=1)
        toolbar.columnconfigure(0, weight=1)

        original_panel = ttk.LabelFrame(pdf_tab, text="원문 PDF")
        masked_panel = ttk.LabelFrame(pdf_tab, text="자동/수동 마스킹 PDF")
        original_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        masked_panel.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        original_panel.rowconfigure(0, weight=1)
        original_panel.columnconfigure(0, weight=1)
        masked_panel.rowconfigure(0, weight=1)
        masked_panel.columnconfigure(0, weight=1)
        self.canvas_original = tk.Canvas(original_panel, bg="#edf2f8", highlightthickness=0)
        self.canvas_masked = tk.Canvas(masked_panel, bg="#edf2f8", highlightthickness=0, cursor="crosshair")
        self.canvas_original.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas_masked.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas_masked.bind("<ButtonPress-1>", self._on_masked_canvas_press)
        self.canvas_masked.bind("<B1-Motion>", self._on_masked_canvas_drag)
        self.canvas_masked.bind("<ButtonRelease-1>", self._on_masked_canvas_release)
        self._toggle_rules_panel()  # 기본값: 규칙 패널 접기(저해상도/고배율에서 미리보기 공간 우선)
        self._clear_pdf_preview()

    def _toggle_rules_panel(self) -> None:
        visible = bool(self.var_rules_visible.get())
        if visible:
            self.rules_card.grid_remove()
            self.var_rules_visible.set(False)
            self.btn_toggle_rules.configure(text="마스킹 규칙 펼치기")
        else:
            self.rules_card.grid()
            self.var_rules_visible.set(True)
            self.btn_toggle_rules.configure(text="마스킹 규칙 숨기기")

    def log(self, msg: str) -> None:
        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(timestamped + "\n")
        except Exception:
            pass
        print(timestamped)
        self.update_idletasks()

    def _refresh_rule_toggle_button(self, button: tk.Checkbutton, variable: tk.BooleanVar) -> None:
        if variable.get():
            button.configure(bg="#1f6feb", fg="#ffffff", activebackground="#2b7cff", activeforeground="#ffffff")
        else:
            button.configure(bg="#e9eef6", fg="#1a2a3d", activebackground="#dbe5f2", activeforeground="#1a2a3d")

    def _on_profile_changed(self, _event: tk.Event[Any] | None = None) -> None:
        self._apply_profile_preset(self.var_profile.get(), initial=False)

    def _apply_profile_preset(self, profile: str, initial: bool = False) -> None:
        profile_key = (profile or "official").strip().lower()
        preset = self.profile_rule_presets.get(profile_key)
        if not preset:
            return
        for key, value in preset.items():
            var = self.rule_vars.get(key)
            if var is not None:
                var.set(bool(value))
        if not initial:
            self.log(f"[프로필 적용] {profile_key} 기본 체크값 반영")

    def _set_busy(self, busy: bool) -> None:
        self.btn_run.config(state="disabled" if busy else "normal")

    def select_files(self) -> None:
        files = filedialog.askopenfilenames(title="마스킹할 파일 선택", filetypes=[("Supported", "*.txt *.pdf *.md *.csv *.log"), ("All files", "*.*")])
        if not files:
            return
        self.selected_files = list(files)
        self.entry_files.delete("1.0", "end")
        self.entry_files.insert("1.0", "\n".join(self.selected_files))
        fp = self.selected_files[0]
        try:
            if Path(fp).suffix.lower() == ".pdf":
                self._set_text_preview(f"[PDF 선택됨]\n{fp}\n\n실행 후 추출 텍스트와 마스킹 결과가 좌우 대조 미리보기에 표시됩니다.", "")
                self.current_output_dir = self.outdir_var.get().strip() or os.path.dirname(fp)
                self.current_manual_output_path = safe_output_paths(fp, outdir=self.current_output_dir)["manual_pdf"]
                self._open_pdf_documents(fp, fp)
            else:
                self._set_text_preview(read_text_file(fp), "")
                self._clear_pdf_preview()
        except Exception as e:
            messagebox.showerror("파일 읽기 오류", str(e))

    def clear_files(self) -> None:
        self.selected_files = []
        self.entry_files.delete("1.0", "end")
        self._clear_pdf_preview()

    def select_outdir(self) -> None:
        d = filedialog.askdirectory(title="출력 폴더 선택")
        if d:
            self.outdir_var.set(d)

    def get_opts(self) -> dict[str, Any]:
        return {
            "rrn": self.var_rrn.get(),
            "phone": self.var_phone.get(),
            "business_reg": self.var_business_reg.get(),
            "name": self.var_name.get(),
            "address": self.var_address.get(),
            "place": self.var_place.get(),
            "legal_party": self.var_legal_party.get(),
            "company": self.var_company.get(),
            "court": self.var_court.get(),
            "case_title": self.var_case_title.get(),
            "case_number": self.var_case_number.get(),
            "law_firm": self.var_law_firm.get(),
            "attorney": self.var_attorney.get(),
            "approval_line": self.var_approval_line.get(),
            "region_context": self.var_region_context.get(),
            "doc_meta": self.var_doc_meta.get(),
            "pdf_redaction": self.var_pdf_redaction.get(),
            "local_llm_refine": self.var_local_llm_refine.get(),
            "local_llm_cmd": self.var_local_llm_cmd.get().strip() or "llama-cli",
            "local_llm_model": self.var_local_llm_model.get().strip(),
            "local_llm_max_calls": int(self.var_local_llm_max_calls.get() or 25),
            "extract_engine": self.var_engine.get(),
            "profile": (self.var_profile.get().strip().lower() if self.var_profile.get() else "official"),
            "output_artifacts": (self.var_output_artifacts.get().strip() if self.var_output_artifacts.get() else "txt+pdf+report"),
            "korean_tokens": True,
            "log_callback": self.log,
        }

    def request_stop(self) -> None:
        self.stop_requested = True
        self.log("[안내] 중지 요청 수신. 현재 파일 처리 후 종료합니다.")

    def _run_worker(self, files: list[str], outdir: str | None, opts: dict[str, Any]) -> None:
        ok = 0
        fail = 0
        last_output = ""
        last_input = ""
        last_report: dict[str, Any] | None = None
        last_file_path: str | None = None
        total = len(files)
        self.progress.configure(maximum=max(total, 1), value=0)

        for idx, fp in enumerate(files, 1):
            if self.stop_requested:
                self.log("[중지] 사용자 요청으로 처리를 중단합니다.")
                break
            self.lbl_progress.config(text=f"{idx}/{total}")
            self.progress.configure(value=idx - 1)
            try:
                extracted_path, masked_path, report_path, report = process_file(fp, outdir=outdir, opts=opts)
                ok += 1
                self.log(f"[완료] {fp}")
                self.log(f"  - engine_used: {report['extract']['engine_used']} ({report['extract']['duration_sec']}s)")
                self.log(f"  - output_artifacts: {','.join(report['rules'].get('output_artifacts', []))}")
                self.log(f"  - extracted: {extracted_path or '-'}")
                self.log(f"  - masked   : {masked_path or '-'}")
                self.log(f"  - masked_pdf: {report['outputs'].get('masked_pdf_file', '-')}")
                self.log(f"  - manual_pdf: {report['outputs'].get('manual_pdf_path', '-')}")
                self.log(f"  - report   : {report_path or '-'}")
                self.log(f"  - counts   : {json.dumps(report['counts'], ensure_ascii=False)}")
                self.log(f"  - chunk_queue: {json.dumps(report.get('chunk_queue', {}), ensure_ascii=False)}")
                self.log(f"  - local_llm_refine: {json.dumps(sanitize_for_logging(report.get('local_llm_refine', {})), ensure_ascii=False)}")
                self.log(f"  - pdf_redaction: {json.dumps(sanitize_for_logging(report['pdf_redaction']), ensure_ascii=False)}")
                self.log(f"  - product_checks: {json.dumps(sanitize_for_logging(report.get('product_checks', {})), ensure_ascii=False)}")
                extracted_text = read_text_file(extracted_path) if extracted_path else extract_result_text_for_preview(fp, opts)
                masked_text = read_text_file(masked_path) if masked_path else mask_text_for_preview(extracted_text, opts)
                self._set_text_preview(extracted_text, masked_text)
                last_output = masked_text
                last_input = extracted_text
                last_report = report
                last_file_path = fp
            except Exception as e:
                fail += 1
                self.log(f"[실패] {fp} / {e}")
            self.progress.configure(value=idx)

        self._set_busy(False)
        self.lbl_progress.config(text=f"{ok + fail}/{total}")
        messagebox.showinfo("처리 결과", f"완료: {ok}건, 실패: {fail}건")
        if last_output:
            self._set_text_preview(last_input, last_output)
        if last_report and last_file_path:
            self._load_pdf_preview_from_report(last_file_path, last_report)

    def run_masking(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("안내", "이미 처리 중입니다.")
            return
        files_text = self.entry_files.get("1.0", "end").strip()
        files = [x.strip() for x in files_text.splitlines() if x.strip()] if files_text else self.selected_files
        if not files:
            messagebox.showwarning("안내", "먼저 입력 파일(.txt/.pdf)을 선택하세요.")
            return
        outdir = self.outdir_var.get().strip() or None
        opts = self.get_opts()
        self.stop_requested = False
        self._set_busy(True)
        self.worker = threading.Thread(target=self._run_worker, args=(files, outdir, opts), daemon=True)
        self.worker.start()

    def clear_texts(self) -> None:
        self.txt_input.delete("1.0", "end")
        self.txt_output.delete("1.0", "end")
        try:
            if os.path.exists(self.log_file_path):
                os.remove(self.log_file_path)
        except Exception:
            pass
        self._clear_pdf_preview()

    def _on_text_scrollbar(self, widget: tk.Text, peer: tk.Text, *args: str) -> None:
        widget.yview(*args)
        if self.var_sync_scroll.get() and not self._syncing_text_scroll:
            self._syncing_text_scroll = True
            peer.yview_moveto(widget.yview()[0])
            self._syncing_text_scroll = False

    def _on_text_yscroll(self, source: str, first: str, last: str) -> None:
        if source == "input":
            self.txt_input_scroll.set(first, last)
            peer = self.txt_output
        else:
            self.txt_output_scroll.set(first, last)
            peer = self.txt_input
        if self.var_sync_scroll.get() and not self._syncing_text_scroll:
            self._syncing_text_scroll = True
            peer.yview_moveto(float(first))
            self._syncing_text_scroll = False

    def _set_text_preview(self, extracted_text: str, masked_text: str) -> None:
        self.txt_input.delete("1.0", "end")
        self.txt_input.insert("1.0", extracted_text[:100000])
        self.txt_output.delete("1.0", "end")
        self.txt_output.insert("1.0", masked_text[:100000])

    def _load_pdf_preview_from_report(self, input_file: str, report: dict[str, Any]) -> None:
        if Path(input_file).suffix.lower() != ".pdf":
            self._clear_pdf_preview()
            return
        preview_pdf = report.get("outputs", {}).get("preview_pdf_source_file") or input_file
        self.current_output_dir = self.outdir_var.get().strip() or os.path.dirname(input_file)
        self.current_manual_output_path = report.get("outputs", {}).get("manual_pdf_path")
        self.current_input_pdf_path = input_file
        self.current_masked_preview_pdf_path = preview_pdf
        self._open_pdf_documents(input_file, preview_pdf)

    def _open_pdf_documents(self, original_pdf_path: str, masked_pdf_path: str) -> None:
        try:
            import fitz  # type: ignore
        except Exception:
            self._clear_pdf_preview(message="PyMuPDF 미설치로 PDF 미리보기를 열 수 없습니다.")
            return
        self._close_pdf_documents()
        self.manual_boxes = []
        try:
            self.original_pdf_doc = fitz.open(original_pdf_path)
            self.masked_pdf_doc = fitz.open(masked_pdf_path)
        except Exception as e:
            self._clear_pdf_preview(message=f"PDF 미리보기 로드 실패: {e}")
            return
        self.original_pdf_page_index = 0
        self.masked_pdf_page_index = 0
        self._render_pdf_canvases()

    def _close_pdf_documents(self) -> None:
        for attr in ("original_pdf_doc", "masked_pdf_doc"):
            doc = getattr(self, attr, None)
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            setattr(self, attr, None)

    def _clear_pdf_preview(self, message: str = "PDF를 처리하면 이 영역에서 수동 보정할 수 있습니다.") -> None:
        self._close_pdf_documents()
        self.preview_images.clear()
        self.preview_layouts.clear()
        self.manual_boxes = []
        self.current_input_pdf_path = None
        self.current_masked_preview_pdf_path = None
        self.current_manual_output_path = None
        self.original_pdf_page_index = 0
        self.masked_pdf_page_index = 0
        for canvas in (getattr(self, "canvas_original", None), getattr(self, "canvas_masked", None)):
            if canvas is None:
                continue
            canvas.delete("all")
            canvas.create_text(30, 30, anchor="nw", text=message, fill="#64748b", font=(self.ui_font_family, 11))
        if hasattr(self, "lbl_original_page"):
            self.lbl_original_page.config(text="원문 0/0")
            self.lbl_masked_page.config(text="결과 0/0")
        if hasattr(self, "lbl_manual_state"):
            self._update_manual_state_label()

    def _change_pdf_page(self, target: str, delta: int) -> None:
        if self.var_sync_pdf_page.get():
            page_count = min(self._page_count(self.original_pdf_doc), self._page_count(self.masked_pdf_doc))
            if page_count <= 0:
                return
            page_index = self._clamp_page_index(self.original_pdf_page_index + delta, page_count)
            self.original_pdf_page_index = page_index
            self.masked_pdf_page_index = page_index
        elif target == "original":
            self.original_pdf_page_index = self._clamp_page_index(self.original_pdf_page_index + delta, self._page_count(self.original_pdf_doc))
        else:
            self.masked_pdf_page_index = self._clamp_page_index(self.masked_pdf_page_index + delta, self._page_count(self.masked_pdf_doc))
        self._render_pdf_canvases()

    def _page_count(self, doc: Any) -> int:
        return int(getattr(doc, "page_count", 0) or 0)

    def _clamp_page_index(self, page_index: int, page_count: int) -> int:
        if page_count <= 0:
            return 0
        return max(0, min(page_index, page_count - 1))

    def _render_pdf_canvases(self) -> None:
        self._render_pdf_canvas("original", self.canvas_original, self.original_pdf_doc, self.original_pdf_page_index)
        self._render_pdf_canvas("masked", self.canvas_masked, self.masked_pdf_doc, self.masked_pdf_page_index)
        self.lbl_original_page.config(text=f"원문 {self.original_pdf_page_index + 1 if self._page_count(self.original_pdf_doc) else 0}/{self._page_count(self.original_pdf_doc)}")
        self.lbl_masked_page.config(text=f"결과 {self.masked_pdf_page_index + 1 if self._page_count(self.masked_pdf_doc) else 0}/{self._page_count(self.masked_pdf_doc)}")
        self._update_manual_state_label()

    def _render_pdf_canvas(self, key: str, canvas: tk.Canvas, doc: Any, page_index: int) -> None:
        canvas.delete("all")
        if doc is None or self._page_count(doc) == 0:
            canvas.create_text(30, 30, anchor="nw", text="표시할 PDF가 없습니다.", fill="#64748b", font=(self.ui_font_family, 11))
            return
        import fitz  # type: ignore
        page = doc[page_index]
        rect = page.rect
        canvas_width = max(canvas.winfo_width(), 520)
        canvas_height = max(canvas.winfo_height(), 680)
        scale = min((canvas_width - 40) / rect.width, (canvas_height - 40) / rect.height)
        scale = max(scale, 0.3)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        photo = tk.PhotoImage(data=base64.b64encode(pix.tobytes("png")).decode("ascii"), format="png")
        self.preview_images[key] = photo
        origin_x = max((canvas_width - photo.width()) / 2, 16)
        origin_y = max((canvas_height - photo.height()) / 2, 16)
        canvas.create_rectangle(origin_x - 1, origin_y - 1, origin_x + photo.width() + 1, origin_y + photo.height() + 1, outline="#d2dbe7", fill="#ffffff")
        canvas.create_image(origin_x, origin_y, anchor="nw", image=photo)
        self.preview_layouts[key] = (PreviewRenderState(page_index=page_index, scale=scale, image_width=photo.width(), image_height=photo.height()), origin_x, origin_y)
        if key == "masked":
            self._draw_manual_boxes()

    def _draw_manual_boxes(self) -> None:
        self.canvas_masked.delete("manual_box")
        layout = self.preview_layouts.get("masked")
        if not layout:
            return
        state, origin_x, origin_y = layout
        page_boxes = [box for box in self.manual_boxes if box.page_index == state.page_index]
        for idx, box in enumerate(page_boxes, 1):
            x0, y0, x1, y1 = box.rect
            self.canvas_masked.create_rectangle(origin_x + x0 * state.scale, origin_y + y0 * state.scale, origin_x + x1 * state.scale, origin_y + y1 * state.scale, outline="#ff5c5c", width=2, tags="manual_box")
            self.canvas_masked.create_text(origin_x + x0 * state.scale + 8, origin_y + y0 * state.scale + 8, anchor="nw", text=str(idx), fill="#ff5c5c", font=(self.ui_font_family, 10, "bold"), tags="manual_box")

    def _on_masked_canvas_press(self, event: tk.Event[Any]) -> None:
        if "masked" not in self.preview_layouts:
            return
        self.drag_start_canvas = (float(event.x), float(event.y))
        if self.current_drag_rect_id is not None:
            self.canvas_masked.delete(self.current_drag_rect_id)
        self.current_drag_rect_id = self.canvas_masked.create_rectangle(event.x, event.y, event.x, event.y, outline="#ff8b6b", dash=(4, 3), width=2)

    def _on_masked_canvas_drag(self, event: tk.Event[Any]) -> None:
        if self.drag_start_canvas is None or self.current_drag_rect_id is None:
            return
        x0, y0 = self.drag_start_canvas
        self.canvas_masked.coords(self.current_drag_rect_id, x0, y0, event.x, event.y)

    def _on_masked_canvas_release(self, event: tk.Event[Any]) -> None:
        if self.drag_start_canvas is None:
            return
        if self.current_drag_rect_id is not None:
            self.canvas_masked.delete(self.current_drag_rect_id)
            self.current_drag_rect_id = None
        layout = self.preview_layouts.get("masked")
        if not layout or self.masked_pdf_doc is None:
            self.drag_start_canvas = None
            return
        state, origin_x, origin_y = layout
        x0, y0 = self.drag_start_canvas
        self.drag_start_canvas = None
        page_rect = self.masked_pdf_doc[state.page_index].rect
        left = max(0.0, min(x0, event.x) - origin_x) / state.scale
        top = max(0.0, min(y0, event.y) - origin_y) / state.scale
        right = min(page_rect.width, max(x0, event.x) - origin_x) / state.scale
        bottom = min(page_rect.height, max(y0, event.y) - origin_y) / state.scale
        if right - left < 4 or bottom - top < 4:
            return
        self.manual_boxes.append(ManualRedactionBox(page_index=state.page_index, rect=(left, top, right, bottom)))
        self._update_manual_state_label()
        self._draw_manual_boxes()

    def undo_manual_box(self) -> None:
        if not self.manual_boxes:
            return
        self.manual_boxes.pop()
        self._update_manual_state_label()
        self._draw_manual_boxes()

    def reset_manual_boxes(self) -> None:
        if not self.manual_boxes:
            return
        self.manual_boxes = []
        self._update_manual_state_label()
        self._draw_manual_boxes()

    def save_manual_redactions(self) -> None:
        if not self.current_manual_output_path or not self.current_masked_preview_pdf_path:
            messagebox.showwarning("안내", "먼저 PDF 문서를 처리해 수동 보정 미리보기를 준비하세요.")
            return
        if not self.manual_boxes:
            messagebox.showwarning("안내", "저장할 수동 마스킹 박스가 없습니다.")
            return
        try:
            result = apply_manual_redactions(self.current_masked_preview_pdf_path, self.current_manual_output_path, self.manual_boxes)
            self.log(f"[수동 저장] {result['output_file']} / boxes={result['boxes_applied']}")
            self.current_masked_preview_pdf_path = result["output_file"]
            self._open_pdf_documents(self.current_input_pdf_path or result["output_file"], result["output_file"])
            messagebox.showinfo("저장 완료", f"수동 마스킹 PDF 저장 완료\n{result['output_file']}")
        except Exception as e:
            messagebox.showerror("수동 저장 실패", str(e))

    def _update_manual_state_label(self) -> None:
        page_info = f" / 현재 페이지 {self.masked_pdf_page_index + 1}" if self._page_count(self.masked_pdf_doc) else ""
        self.lbl_manual_state.config(text=f"박스 {len(self.manual_boxes)}개{page_info}")

    def _on_window_resize(self, _event: tk.Event[Any]) -> None:
        if self.original_pdf_doc is None and self.masked_pdf_doc is None:
            return
        self.after_idle(self._render_pdf_canvases)

def run_cli_mode(argv: list[str]) -> bool:
    if len(argv) <= 1:
        return False

    files = [a for a in argv[1:] if os.path.isfile(a)]
    if not files:
        return False

    opts = {
        "rrn": True,
        "phone": True,
        "business_reg": True,
        "name": True,
        "address": True,
        "place": True,
        "legal_party": True,
        "company": True,
        "court": True,
        "case_title": True,
        "case_number": True,
        "law_firm": True,
        "attorney": True,
        "approval_line": True,
        "region_context": True,
        "doc_meta": True,
        "pdf_redaction": True,
        "local_llm_refine": False,
        "local_llm_cmd": "llama-cli",
        "local_llm_model": "",
        "local_llm_max_calls": 25,
        "extract_engine": "auto",
        "output_artifacts": "txt+pdf+report",
    }

    opts["log_callback"] = print

    print("[CLI 모드] 파일 처리 시작")
    for fp in files:
        try:
            extracted_path, masked_path, report_path, report = process_file(fp, outdir=None, opts=opts)
            print(f"[완료] {fp}")
            print(f"  - engine_used: {report['extract']['engine_used']} ({report['extract']['duration_sec']}s)")
            print(f"  - output_artifacts: {','.join(report['rules'].get('output_artifacts', []))}")
            print(f"  - extracted: {extracted_path or '-'}")
            print(f"  - masked   : {masked_path or '-'}")
            print(f"  - masked_pdf: {report['outputs'].get('masked_pdf_file', '-')}")
            print(f"  - manual_pdf: {report['outputs'].get('manual_pdf_path', '-')}")
            print(f"  - report   : {report_path or '-'}")
            print(f"  - counts   : {json.dumps(report['counts'], ensure_ascii=False)}")
            print(f"  - chunk_queue: {json.dumps(report.get('chunk_queue', {}), ensure_ascii=False)}")
            print(f"  - local_llm_refine: {json.dumps(sanitize_for_logging(report.get('local_llm_refine', {})), ensure_ascii=False)}")
            print(f"  - pdf_redaction: {json.dumps(sanitize_for_logging(report['pdf_redaction']), ensure_ascii=False)}")
            print(f"  - product_checks: {json.dumps(sanitize_for_logging(report.get('product_checks', {})), ensure_ascii=False)}")
        except Exception as e:
            print(f"[실패] {fp}: {e}")

    return True


def main() -> None:
    if run_cli_mode(sys.argv):
        return

    app = MaskerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
