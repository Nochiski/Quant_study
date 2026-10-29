"""저녁 DART 판(D13 · N-62) — 18:05 DART 수집이 끝나면 그날 공시·재무를 모델 DB 루트에 다시 짓는다.

장 마감 체인 evening 모드(`scripts/postclose_chain.sh`)가 부른다. 원장 dart.db 를 한 번만 떠서(스냅숏) 그 판으로
stage DART 폐포(`STAGE_TABLES`)와 equity DART 폐포(`EQUITY_TABLES`)를 모델 DB 루트(`data/model_db/stage` ·
`data/model_db/equity`)에 저녁 판(e_)으로 짓고, 판 목록을 저녁 DART 인계(`handoff_path`)에 쓴다. 장 마감 fi 는 그
인계를 `--dart-builds-from` 으로 읽어 DART 정보 시점만 T 로 연다(`factor_inputs.build`). 엑셀 Events 시트도 같은
판을 읽는다. 연구 루트에는 쓰지 않는다 — 연구 인계 이력(stage_builds)에 저녁 판이 섞이면 안 된다(T-29 와 같은 이유).

폐포: 표 목록은 이 모듈 한 곳이다(fi 가 인계를 검사할 때도 여기서 읽는다).
  · stage 16표는 모두 원장 dart.db 하나만 읽는다. `stg_rcept_dt_map` 이 먼저다(공개일 조회표). 문서 표
    (`stg_doc_meta`·`stg_doc_correction`)는 같은 스냅숏의 문서 캐시가 있어야 지어진다.
  · equity 6표는 이 stage 표와 서로만 입력으로 쓴다(corp → disclosure_version → fin_std … 순서).
  · 수정주가 사슬(기업행위 → 계수 → 유니버스)은 넣지 않는다 — fi 의 T-6 당일 기업행위 보류가 맡는다.
"""
from __future__ import annotations

from pathlib import Path

# 원장 → stage 순서(run_stage_all.sh ORDER 의 상대 순서 — 공개일 조회표가 먼저, 문서 캐시 표가 끝)
STAGE_TABLES: tuple[str, ...] = (
    "stg_rcept_dt_map",
    "stg_fin", "stg_dividend", "stg_audit", "stg_holder_elestock", "stg_holder_majorstock",
    "stg_disclosure", "stg_company", "stg_corp_map", "stg_doc_index",
    "stg_event_tsstk_aq", "stg_event_piic", "stg_event_cvbd_is", "stg_event_tsstk_dp",
    "stg_doc_meta", "stg_doc_correction",
)
# stage → equity 순서(입력 의존 순 — corp·disclosure_version 이 fin_std·audit_opinion·holder_daily 앞)
EQUITY_TABLES: tuple[str, ...] = ("corp", "disclosure_version", "fin_std", "dividend_event",
                                  "audit_opinion", "holder_daily")
BASIS = "evening"


def handoff_path(model_db: Path, t: str) -> Path:
    """저녁 DART 인계 — `<model_db>/dart/<T>_evening.json`(T = YYYYMMDD)."""
    return Path(model_db) / "dart" / f"{t}_{BASIS}.json"
