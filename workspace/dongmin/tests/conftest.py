"""workspace/dongmin 테스트 공통 설정 — survey/·src/ 모듈을 import 경로에 올린다."""
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("survey", "src"):
    _p = os.path.join(_HERE, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
