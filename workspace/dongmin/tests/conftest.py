"""workspace/dongmin 테스트 공통 설정 — survey/ 모듈을 import 경로에 올린다."""
import os
import sys

_SURVEY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "survey")
if _SURVEY_DIR not in sys.path:
    sys.path.insert(0, _SURVEY_DIR)
