"""재수집 판본이 쌓이는 stage 표를 읽는 equity 규칙은 그 판본을 어떻게 접는지 여기에 적어 둔다(N-75).

stage 표 가운데 `write_mode='append_only'` 이고 `key_unique=False` 인 것은 같은 자연키가 여러 행일 수 있다 — 회사명이
바뀐 뒤 다시 받은 결정공시, 정정이 덧붙은 정기보고서, 다시 받은 일별 값. 그런 표를 접지 않고 조인하면 행이 판본 수만큼
분다. 2026-11-21 아침 확정판이 그렇게 멈췄다(`adj_factor` 가 `stg_event_cr` 판본 2행을 그대로 조인 — N-70).

새 SQL 이 그런 표를 읽기 시작하면 아래 목록에 없어 이 테스트가 실패한다. 통과시키려면 판본을 접고(또는 접지 않아도 되는
이유를 대고) 한 줄을 더한다. 정적 가드다 — 값이 맞는지는 표마다의 판본 중복 테스트가 본다.
"""
from __future__ import annotations

from collections.abc import Mapping

import equity.__main__  # noqa: F401  # reason: equity 규칙 등록 부작용
from equity.model import RULES, EquityTable
from stage import rules as stage_rules
from stage.model import TableRule

# (equity 규칙, stage 표) → (접는 방식, 그 SQL 에 있어야 하는 조각 — 접지 않으면 None)
FOLDS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("adj_factor", "stg_event_cr"): ("접수마다 유 · 무상 축을 bool_or 로 한 줄(N-70)", "GROUP BY rcept_no, corp_code"),
    ("audit_opinion", "stg_audit"): ("자연키마다 가장 이른 판본", "ORDER BY b.observed_date NULLS LAST, b.row_hash NULLS LAST) = 1"),
    ("corp", "stg_company"): ("법인마다 가장 늦은 관측", "PARTITION BY corp_code"),
    ("corp_event", "stg_capital"): (
        ("접지 않는다 — 통과 사건은 dedup 축(ticker · event_type · effective_date)이 접는다. 격리 행은 판본만큼 남지만 "
         "자연키에 row_hash 가 들어 같은 내용은 한 행이다(11-21 실측 판본 중복 0)"), None),
    ("corp_event", "stg_event_cr"): ("접수마다 가장 이른 판본(N-75)", "ev_cr AS ("),
    ("corp_event", "stg_event_cvbd_is"): ("접수마다 가장 이른 판본(N-75)", "ev_cvbd_is AS ("),
    ("corp_event", "stg_event_fric"): ("접수마다 가장 이른 판본(N-75)", "ev_fric AS ("),
    ("corp_event", "stg_event_pifric"): ("접수마다 가장 이른 판본(N-75)", "ev_pifric AS ("),
    ("corp_event", "stg_event_tsstk_aq"): ("접수마다 가장 이른 판본(N-75)", "ev_tsstk_aq AS ("),
    ("credit_daily", "stg_credit_daily"): ("(ticker, date)마다 판본 하나", "WHERE version_rn = 1"),
    ("credit_daily", "stg_units_kis"): ("수집 단위 로그 — 창으로만 읽어 행 수와 무관", None),
    ("disclosure_version", "stg_disclosure"): ("접수마다 가장 이른 판본", "PARTITION BY d.rcept_no"),
    ("disclosure_version", "stg_doc_index"): ("접수마다 가장 이른 판본", "QUALIFY row_number() OVER (PARTITION BY rcept_no"),
    ("dividend_event", "stg_dividend"): ("자연키마다 한 줄", "PARTITION BY corp_code, bsns_year, reprt_code, stock_knd, se"),
    ("fin_prelim", "stg_disclosure"): ("접수마다 가장 이른 판본", "PARTITION BY d.rcept_no"),
    ("fin_std", "stg_disclosure"): ("접수마다 가장 이른 판본(`dt`)", "QUALIFY row_number() OVER (PARTITION BY rcept_no"),
    ("fin_std", "stg_fin"): (
        "묶음(요청 키 + 보고서 기간)마다 최신 접수(`grp`, G-21 · N-74) + 그 접수의 줄은 자연키 8열마다 가장 이른 판본(`fin`)",
        "f.account_id, f.account_detail, f.ord"),
    ("flow_daily", "stg_flow_split_daily"): (
        ("접지 않는다 — KIS 수급 원장은 백필 전용이다(일일 수집 밖 · 백필 코드 동결 N-23 ③ · 마지막 수집 2026-08-26)라 새 "
         "판본이 쌓이지 않는다. 중복이 생기면 EG3_flow_daily 의 원장 보존 등식이 빌드를 멈춘다(조용히 불지 않는다)"), None),
    ("flow_daily", "stg_units_kis"): ("수집 단위 로그 — bool_or 집계라 행 수와 무관", "GROUP BY g.date, g.ticker"),
    ("holder_daily", "stg_holder_elestock"): ("(접수, 보고자)마다 가장 이른 판본", "observed_date"),
    ("holder_daily", "stg_holder_majorstock"): ("(접수, 보고자)마다 가장 이른 판본", "observed_date"),
    ("ownership_snapshot", "stg_hyslr"): ("자연키마다 가장 이른 판본", "folded AS ("),
    ("security", "stg_delisted_master"): ("종목마다 min 집계", "GROUP BY ticker"),
    ("shares_outstanding", "stg_shares"): ("자연키마다 한 줄", "PARTITION BY corp_code, bsns_year, reprt_code, se"),
    ("short_daily", "stg_loan_daily_kis"): ("(ticker, date)마다 가장 이른 판본", "PARTITION BY ticker, date"),
    ("short_daily", "stg_short_daily_kis"): ("(ticker, date)마다 가장 이른 판본", "PARTITION BY ticker, date"),
    ("short_daily", "stg_units_kis"): ("수집 단위 로그 — max 집계라 행 수와 무관", "GROUP BY u.dataset, u.ticker, c.date"),
    ("treasury_stock", "stg_tesstk"): ("자연키마다 한 줄", "acqs_mth1, acqs_mth2, acqs_mth3, stock_knd"),
    ("universe_daily", "stg_disclosure"): ("bool_or 집계라 행 수와 무관", "bool_or("),
}


def version_input_pairs(equity_rules: Mapping[str, EquityTable],
                        stage: Mapping[str, TableRule]) -> set[tuple[str, str]]:
    """판본이 쌓이는 stage 표(append_only ∧ key_unique=False)를 입력으로 읽는 (equity 규칙, stage 표) 쌍."""
    return {(r.name, t) for r in equity_rules.values() for t in r.inputs
            if t in stage and stage[t].write_mode == "append_only" and not stage[t].key_unique}


def test_판본이_쌓이는_stage_표를_읽는_규칙은_접는_방식을_적어_둔다() -> None:
    pairs = version_input_pairs(RULES, stage_rules.RULES)
    new, gone = sorted(pairs - set(FOLDS)), sorted(set(FOLDS) - pairs)
    assert not new, (f"재수집 판본이 쌓이는 stage 표를 새로 읽는다 {new} — 그 SQL 에서 판본을 접고(접수 · 자연키마다 한 줄) "
                     "판본 중복 테스트를 쓴 뒤 FOLDS 에 한 줄을 더하라(N-70 · N-75)")
    assert not gone, f"FOLDS 에만 있고 지금은 읽지 않는 쌍 {gone} — 목록에서 지워라"


def test_적어_둔_접는_자리가_그_SQL_에_있다() -> None:
    missing = [(name, table, snippet) for (name, table), (_how, snippet) in FOLDS.items()
               if snippet is not None and snippet not in RULES[name].sql_path.read_text(encoding="utf-8")]
    assert not missing, f"FOLDS 가 가리키는 접는 자리가 SQL 에 없다 {missing} — SQL 을 바꿨으면 목록도 맞춰라"


def test_새로_읽는_쌍은_목록에_없어_드러난다() -> None:
    """자기 검사 — 가짜 규칙이 판본 표를 새로 읽으면 쌍이 하나 는다."""
    base = RULES["corp"]
    fake = {**RULES, "new_table": EquityTable(**{**base.__dict__, "name": "new_table",
                                                 "inputs": (*base.inputs, "stg_event_cr")})}
    extra = version_input_pairs(fake, stage_rules.RULES) - version_input_pairs(RULES, stage_rules.RULES)
    assert ("new_table", "stg_event_cr") in extra and {name for name, _ in extra} == {"new_table"}
