#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
호환 래퍼(legacy).
기존 파일명 `yangcheon_masker_ocr_gui.py` 참조 코드를 유지하기 위한 진입점입니다.
실제 구현은 `document_masker_ocr_gui.py`를 사용하세요.
"""

from document_masker_ocr_gui import *  # noqa: F401,F403

if __name__ == "__main__":
    app = MaskerApp()
    app.mainloop()
