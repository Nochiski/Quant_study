"""추정치 소멸 유예 재판정(N-47) — fi 는 유예 창 `FI_COVERAGE_GRACE_MAX`(5)로 grace 종목을
eligible 에 두고, 엔진이 spec 의 `universe.coverage_grace_days` 로 다시 자른다.

(a) 공용 판정 `engines._common.estimates_lapsed`: grace 이고 나이 > spec 유예면
    'estimates_lapsed'(fi 제외 사유와 같은 코드). 나이 0 grace(WISE 를 휴장일에 수집 — 마지막
    신선일과 D* 사이에 거래일이 없다)는 유예 0 에도 남는다. 신선도 열이 빈 행(compat 어댑터
    골든)·require_estimates=false 는 보지 않고, 나이를 모르는 grace 는 뺀다.
(b) 엔진 셋(v3_zscore·v2_percentrank·v4_rank)이 같은 판정을 쓴다: 유예 0 spec 은 나이 ≥ 1 grace 를
    빼고 나이 0 은 남긴다 — 그 종목을 fi eligible=false 로 둔 판(옛 fi 유예 0)과 점수가 글자 그대로
    같다. 유예 5 spec 은 나이 5 까지 남긴다.
(c) MG1 이 엔진과 같은 규칙으로 유니버스를 센다(유예로 뺀 종목이 '점수 누락'으로 잡히지 않는다).
(d) spec 유예는 fi 창보다 길 수 없다(검증 오류 — 엔진은 fi 가 뺀 종목을 되살리지 못한다).
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from model import gates as mgates
from model import registry
from model.contracts import FI_COVERAGE_GRACE_MAX, FactorInputs, ModelSpec, UniverseRule
from model.engines import ENGINES
from model.engines._common import ESTIMATES_LAPSED, estimates_lapsed
from stage.gates import GateStatus
from test_model_build import board_fi

G0, G1, G5 = "100001", "100002", "100003"      # 유예 나이 0(휴장일 수집) · 1 · 5
GRACE = {G0: 0, G1: 1, G5: 5}


def _with_grace(spec: ModelSpec, days: int) -> ModelSpec:
    return replace(spec, universe=replace(spec.universe, coverage_grace_days=days))


def _grace_fi() -> FactorInputs:
    """보드 40종목 중 셋을 fi 유예 창 안 grace(eligible) 로 둔 판."""
    fi = board_fi()
    uni = [dict(r, coverage_state="grace", coverage_age_days=GRACE[str(r["ticker"])])
           if str(r["ticker"]) in GRACE else r for r in fi.tables["fi_universe"]]
    return FactorInputs(fi.date, fi.basis, fi.build_id, {**fi.tables, "fi_universe": uni})


def _old_fi_grace0(fi: FactorInputs) -> FactorInputs:
    """옛 fi 유예 0 판정 — 나이 ≥ 1 grace 는 lapsed · eligible=false · estimates_lapsed."""
    uni = [dict(r, coverage_state="lapsed", has_estimates=False, eligible=False,
                exclude_reason=ESTIMATES_LAPSED)
           if r["coverage_state"] == "grace" and r["coverage_age_days"] >= 1 else r
           for r in fi.tables["fi_universe"]]
    return FactorInputs(fi.date, fi.basis, fi.build_id, {**fi.tables, "fi_universe": uni})


def _scored(spec: ModelSpec, fi: FactorInputs) -> set[str]:
    col = mgates.ticker_col(spec)
    return {str(r[col]) for r in ENGINES[spec.engine].run(spec, fi).scores}


# ── (a) 공용 판정 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("state", "age", "grace", "want"), [
    ("fresh", 0, 0, None),
    ("grace", 0, 0, None),                  # 휴장일 수집 — 유예 0 에도 남는다(옛 fi 와 같다)
    ("grace", 1, 0, ESTIMATES_LAPSED),
    ("grace", 5, 0, ESTIMATES_LAPSED),
    ("grace", 5, 5, None),
    ("grace", 3, 2, ESTIMATES_LAPSED),
    ("grace", None, 5, ESTIMATES_LAPSED),   # 나이를 모르는 grace 는 뺀다(FG-fresh 가 막는 모양)
    (None, None, 0, None),                  # 신선도 열이 빈 행(compat 어댑터 골든) — 보지 않는다
])
def test_estimates_lapsed_cuts_grace_older_than_the_spec(state, age, grace, want) -> None:
    row = {"coverage_state": state, "coverage_age_days": age}
    assert estimates_lapsed(UniverseRule(coverage_grace_days=grace), row) == want
    assert ESTIMATES_LAPSED == "estimates_lapsed"           # fi 제외 사유와 같은 코드


def test_estimates_lapsed_ignores_specs_without_the_estimate_rule() -> None:
    row = {"coverage_state": "grace", "coverage_age_days": 3}
    assert estimates_lapsed(UniverseRule(require_estimates=False), row) is None


# ── (b) 엔진 셋 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("spec_id", ["scope@1.0", "v3_zscore@1.0", "v2_percentrank@1.0"])
def test_grace_zero_specs_drop_aged_grace_but_keep_the_holiday_case(spec_id: str) -> None:
    """유예 0 spec(원본 미러·scope_v1.0) — 나이 1·5 grace 는 빠지고 나이 0 은 남는다. 점수는 그
    종목을 fi eligible=false 로 둔 판(옛 fi 유예 0)과 글자 그대로 같다."""
    spec = registry.get(spec_id)
    assert spec.universe.coverage_grace_days == 0
    fi = _grace_fi()
    engine = ENGINES[spec.engine]
    got = engine.run(spec, fi).scores
    col = mgates.ticker_col(spec)
    tickers = {str(r[col]) for r in got}
    assert G0 in tickers and not tickers & {G1, G5}
    assert got == engine.run(spec, _old_fi_grace0(fi)).scores


@pytest.mark.parametrize("spec_id", ["scope@1.0", "v2_percentrank@1.0"])
def test_grace_five_keeps_grace_stocks_up_to_age_five(spec_id: str) -> None:
    spec = _with_grace(registry.get(spec_id), 5)
    fi = _grace_fi()
    assert {G0, G1, G5} <= _scored(spec, fi)
    assert _scored(spec, fi) == _scored(spec, board_fi())      # 유예 5 면 다 신선한 판과 같은 종목


def test_v4_applies_its_own_toml_grace_with_the_shared_rule() -> None:
    """v4 TOML 은 유예 5 를 명시한다 — 나이 5 까지 남는다. 유예 0 으로 바꾸면 옛 fi 유예 0
    판과 같다."""
    spec = registry.get("v4_rank@0.1")
    assert spec.universe.coverage_grace_days == 5
    fi = _grace_fi()
    assert {G0, G1, G5} <= _scored(spec, fi)
    zero = _with_grace(spec, 0)
    engine = ENGINES[spec.engine]
    got = engine.run(zero, fi)
    assert {G1, G5}.isdisjoint(_scored(zero, fi)) and G0 in _scored(zero, fi)
    assert got.scores == engine.run(zero, _old_fi_grace0(fi)).scores


# ── (c) MG1 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("spec_id", "grace", "n_out"), [
    ("scope@1.0", 0, 2), ("scope@1.0", 5, 0),
    ("v2_percentrank@1.0", 0, 2), ("v2_percentrank@1.0", 5, 0)])
def test_mg1_counts_the_universe_with_the_spec_grace(spec_id: str, grace: int, n_out: int) -> None:
    spec = _with_grace(registry.get(spec_id), grace)
    fi = _grace_fi()
    res = ENGINES[spec.engine].run(spec, fi)
    ctx = mgates.GateContext(spec=spec, date=str(fi.date)[:10], inputs=fi, result=res,
                             rerun=res, n_prices_on_d=len(res.scores))
    g = mgates.mg1_coverage(ctx)
    assert g.status is GateStatus.PASS, g.detail
    n_board = len(board_fi().tables["fi_universe"])
    assert g.metrics["n_eligible"] == g.metrics["n_covered"] == len(res.scores) == n_board - n_out
    assert g.metrics["n_outside_universe"] == 0


# ── (d) spec 유예 상한 ──────────────────────────────────────────────────────────
def test_spec_grace_cannot_exceed_the_fi_window() -> None:
    spec = registry.get("scope@1.0")
    assert _with_grace(spec, FI_COVERAGE_GRACE_MAX).validate() == []
    errs = _with_grace(spec, FI_COVERAGE_GRACE_MAX + 1).validate()
    assert any("coverage_grace_days" in e and "fi 유예 창" in e for e in errs), errs
