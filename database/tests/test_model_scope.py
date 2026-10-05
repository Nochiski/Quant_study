"""scope_v1.0(`scope@1.0`) — 원본 v3(`v3_zscore@1.0`)에서 밸류의 EV/EBITDA 만 뺀 메인 모델.

(a) 레지스트리: 엔진·버킷·하위 가중이 v3_zscore@1.0 과 글자 그대로 같고, 밸류 하위 가중만
    ev_ebitda 가 빠진다(나머지 키 순서 유지 — 합산 순서). 유니버스는 3개월 의견 기준
    (min_analysts = 1) 하나만 다르다(2026-10-05 사용자 결정).
(b) 골든 09-28 입력: 밸류·종합 밖의 팩터 점수는 v3_zscore@1.0 과 비트 단위로 같다.
(c) EV/EBITDA 값을 아무리 바꿔도 scope 점수는 그대로다(원값 표시 열 val_ev_ebitda 만 따라간다).
(d) 추정기관수(최근 3개월 투자의견을 낸 증권사 수) 0 은 유니버스 밖, 모름(NULL)·1 이상은 안.
(e) MG1 이 같은 규칙으로 유니버스를 센다(안 그러면 빠진 종목이 '점수 누락'으로 잡혀 판이 막힌다).
(f) 인계(엑셀)의 기본 주 모델은 scope@1.0 이다.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from model import gates, registry
from model.build import PRIMARY_DEFAULT
from model.contracts import V3_SCORE_COLUMNS, FactorInputs
from model.engines import ENGINES
from stage.gates import GateStatus

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
# tests/tools/ 는 패키지가 아니라 위 sys.path 주입 뒤에야 import 된다.
import compat_to_fi  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "model_golden" / "2026-09-28"
SCOPE, V3 = "scope@1.0", "v3_zscore@1.0"
ENGINE = ENGINES["v3_zscore"]
SAME_FACTORS = ("momentum_score", "revision_score", "flow_score", "quality_score")


@pytest.fixture(scope="module")
def golden_fi() -> FactorInputs:
    return compat_to_fi.load_golden(GOLDEN)


def _by_code(rows) -> dict[str, dict]:
    return {str(r["stock_code"]): r for r in rows}


# ── (a) 레지스트리 ─────────────────────────────────────────────────────────────
def test_scope_spec_is_v3_without_ev_ebitda() -> None:
    scope, v3 = registry.get(SCOPE), registry.get(V3)
    assert scope.validate() == [] and scope in registry.all_specs()
    assert (scope.model_id, scope.version, scope.engine) == ("scope", "1.0", "v3_zscore")
    assert list(scope.buckets.items()) == list(v3.buckets.items())
    assert replace(scope.universe, min_analysts=None) == v3.universe
    assert scope.universe.min_analysts == 1 and v3.universe.min_analysts is None
    assert ENGINE.output_columns(scope) == V3_SCORE_COLUMNS
    for f in ("momentum", "revision", "flow", "quality"):
        assert scope.params[f] == v3.params[f], f
    v3_val = dict(v3.params["valuation"]["sub_weights"])       # type: ignore[index]
    v3_val.pop("ev_ebitda")
    assert list(scope.params["valuation"]["sub_weights"].items()) == list(v3_val.items())  # type: ignore[index]


# ── (b) 골든 입력에서 v3_zscore@1.0 과의 차이 ────────────────────────────────────
def test_scope_differs_from_v3_only_in_valuation_and_composite(golden_fi) -> None:
    scope = _by_code(ENGINE.run(registry.get(SCOPE), golden_fi).scores)
    v3 = _by_code(ENGINE.run(registry.get(V3), golden_fi).scores)
    assert scope.keys() == v3.keys()
    for code in scope:
        for col in SAME_FACTORS:
            assert scope[code][col] == v3[code][col], (code, col)
    assert any(scope[c]["valuation_score"] != v3[c]["valuation_score"] for c in scope)
    ranks = sorted(r["rank"] for r in scope.values() if r["rank"] is not None)
    assert ranks == list(range(1, len(ranks) + 1))


# ── (c) EV/EBITDA 는 점수에 들어가지 않는다 ─────────────────────────────────────
def test_ev_ebitda_values_do_not_move_scope_scores(golden_fi) -> None:
    bent = {name: [dict(r, ev_ebitda=9_999.0) if name == "fi_fin_summary" else r for r in rows]
            for name, rows in golden_fi.tables.items()}
    bent_fi = FactorInputs(golden_fi.date, golden_fi.basis, golden_fi.build_id, bent)
    spec = registry.get(SCOPE)
    before = ENGINE.run(spec, golden_fi).scores
    after = ENGINE.run(spec, bent_fi).scores
    cols = [c for c in V3_SCORE_COLUMNS if c != "val_ev_ebitda"]
    assert [[r[c] for c in cols] for r in before] == [[r[c] for c in cols] for r in after]
    # 같은 조작이 v3_zscore@1.0 의 밸류는 움직인다 — 위 비교가 헛돌지 않는다는 대조
    v3 = registry.get(V3)
    assert [r["valuation_score"] for r in ENGINE.run(v3, golden_fi).scores] != \
        [r["valuation_score"] for r in ENGINE.run(v3, bent_fi).scores]


# ── (d)·(e) 3개월 의견 기준 ─────────────────────────────────────────────────────
def _with_analysts(fi: FactorInputs, counts: dict[str, int | None]) -> FactorInputs:
    uni = [dict(r, n_analysts=counts[str(r["ticker"])]) if str(r["ticker"]) in counts else r
           for r in fi.tables["fi_universe"]]
    return FactorInputs(fi.date, fi.basis, fi.build_id, {**fi.tables, "fi_universe": uni})


def test_scope_drops_stocks_without_opinions_in_three_months(golden_fi) -> None:
    codes = sorted(_by_code(ENGINE.run(registry.get(V3), golden_fi).scores))
    zero, unknown, one = codes[:3]
    fi = _with_analysts(golden_fi, {zero: 0, unknown: None, one: 1})
    scope = _by_code(ENGINE.run(registry.get(SCOPE), fi).scores)
    v3 = _by_code(ENGINE.run(registry.get(V3), fi).scores)
    assert zero not in scope and {unknown, one} <= scope.keys()
    assert set(scope) == set(v3) - {zero}          # v3_zscore@1.0 은 이 규칙이 없다
    ranks = sorted(r["rank"] for r in scope.values() if r["rank"] is not None)
    assert ranks == list(range(1, len(ranks) + 1))


def test_mg1_counts_scope_universe_with_the_same_rule(golden_fi) -> None:
    codes = sorted(_by_code(ENGINE.run(registry.get(V3), golden_fi).scores))
    fi = _with_analysts(golden_fi, dict.fromkeys(codes[:100], 0))
    spec = registry.get(SCOPE)
    res = ENGINE.run(spec, fi)
    ctx = gates.GateContext(spec=spec, date=str(fi.date)[:10], inputs=fi, result=res,
                            rerun=res, n_prices_on_d=len(codes))
    g = gates.mg1_coverage(ctx)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_eligible"] == g.metrics["n_covered"] == len(res.scores) == len(codes) - 100


# ── (f) 주 모델 ────────────────────────────────────────────────────────────────
def test_scope_is_the_default_primary_model() -> None:
    assert PRIMARY_DEFAULT == SCOPE
