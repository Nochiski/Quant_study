"""stage 규칙 레지스트리 — 소스별 선언 모듈을 모아 `RULES` 로 노출한다.

테이블 선언은 `rules_<source>.py`(krx·kiwoom·kis·dart·wise)에 두고 여기서는 등록만 한다.
소스별 병렬 작업의 충돌 지점을 이 파일 한 줄로 좁힌다. 타입·종류는 `model.py`.
"""
from __future__ import annotations

from . import rules_dart, rules_dart_events, rules_krx, rules_wise
from .model import TableRule

# db alias → 원장 파일명. survey/targets.py DBS 와 같아야 한다(테스트 대조). wise 만 다르다.
LEDGER_FILES: dict[str, str] = {"krx": "krx.db", "kiwoom": "kiwoom.db", "kis": "kis.db",
                                "dart": "dart.db", "wise": "wisereport.db"}

_MODULES = (rules_krx, rules_dart, rules_dart_events, rules_wise)
RULES: dict[str, TableRule] = {t.name: t for m in _MODULES for t in m.TABLES}
