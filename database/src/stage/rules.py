"""stage 규칙 레지스트리 — 소스별 선언 모듈을 모아 `RULES` 로 노출한다.

테이블 선언은 `rules_<source>.py`(krx·kiwoom·kis·dart·wise)에 두고 여기서는 등록만 한다.
소스별 병렬 작업의 충돌 지점을 이 파일 한 줄로 좁힌다. 타입·종류는 `model.py`.
"""
from __future__ import annotations

from . import (
    rules_dart,
    rules_dart_events,
    rules_doc,
    rules_kis,
    rules_kiwoom,
    rules_krx,
    rules_wics,
    rules_wise,
)
from .model import TableRule

# db alias → 원장 파일명. survey/targets.py DBS 와 같아야 한다(테스트 대조). wise 만 다르다.
LEDGER_FILES: dict[str, str] = {"krx": "krx.db", "kiwoom": "kiwoom.db", "kis": "kis.db",
                                "dart": "dart.db", "wise": "wisereport.db",
                                "wiseindex": "wiseindex.db"}   # WICS 주간 스냅샷(2026-09-20, 플랜 wics-weekly T1)
# 연구 체인 스냅샷(`build_chain.sh` 가 위 LEDGER_FILES 전부를 한 세트로 뜬다) 밖 원장 — 그 원장을 읽는 표를
# 단독 빌드(`python -m stage --table …`)할 때만 뜬다. postclose.db = 15:41 장 마감 직후 수집 원장(컷오버
# PR-1·T-4). 연구 판은 이 원장 표를 쓰지 않고, 첫 수집 전에는 파일이 없어 연구 세트에 넣으면 연구 체인
# 스냅샷이 멈춘다(`make_snapshot` 은 없는 원장에서 예외).
SOLO_LEDGER_FILES: dict[str, str] = {"postclose": "postclose.db"}

_MODULES = (rules_krx, rules_kiwoom, rules_kis, rules_dart, rules_dart_events, rules_wise, rules_wics,
            rules_doc)
RULES: dict[str, TableRule] = {t.name: t for m in _MODULES for t in m.TABLES}
