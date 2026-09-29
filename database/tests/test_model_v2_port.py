"""v2 엔진 이식(M2 W1-d — T2.4 `engines/v2_percentrank`).

(a) 골든: 09-28 그림자 compat 입력 → 이식 엔진 = v2 원본 점수(`score_history_v2`)
    (619종목 × 21열, |Δ| ≤ 1e-9, NULL 일치, rank 동일)
(b) 결정성: 두 번 실행·입력 행 순서 섞기 → 비트 단위 동일
(c) 레지스트리: `v2_percentrank@1.0` 적재·검증, 값이 v3 `config.yaml` scoring_v2 와 같다
(d) 규칙 핀: 헷갈리기 쉬운 v2 규칙(합성 입력)

골든 입력은 v3 이식 테스트와 **같은 FactorInputs 한 판**이다. v2 는 컨센서스·확정 실적을
`fi_consensus_annual`(compat `consensus_annual`, WISE c1050001)에서만 읽으므로 v3 가 읽는
fi_consensus·fi_fin_summary 와 값이 달라도 두 원본을 동시에 맞춘다(`test_single_build_*`).
"""
from __future__ import annotations

import math
import os
import random
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from model import registry
from model.contracts import FI_TABLES, V2_SCORE_COLUMNS, FactorInputs, UniverseRule
from model.engines import v2_percentrank, v3_zscore

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
# tests/tools/ 는 패키지가 아니라 위 sys.path 주입 뒤에야 import 된다.
import compat_to_fi  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "model_golden" / "2026-09-28"
SPEC_ID = "v2_percentrank@1.0"
TOL = 1e-9
NUM_COLS = tuple(c for c in V2_SCORE_COLUMNS if c not in ("stock_code", "score_date", "rank"))
ENGINE = v2_percentrank.ENGINE
NAN = float("nan")


# ── (a) 골든 ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def golden_fi() -> FactorInputs:
    return compat_to_fi.load_golden(GOLDEN)


@pytest.fixture(scope="module")
def expected() -> dict[str, dict]:
    rows = compat_to_fi.read_parquet_rows(GOLDEN / "score_history_v2.parquet")
    return {str(r["stock_code"]): r for r in rows}


@pytest.fixture(scope="module")
def ported(golden_fi):
    return ENGINE.run(registry.get(SPEC_ID), golden_fi)


def test_golden_adapter_meets_contract(golden_fi) -> None:
    assert golden_fi.check("v2_percentrank") == []


def test_golden_same_universe(ported, expected) -> None:
    got = [r["stock_code"] for r in ported.scores]
    assert len(got) == len(set(got)) == len(expected) == 619
    assert set(got) == set(expected)


def test_golden_row_shape(ported) -> None:
    assert all(tuple(r) == V2_SCORE_COLUMNS for r in ported.scores)
    assert ported.indicators == []


def test_golden_numeric_columns_match(ported, expected) -> None:
    bad = []
    for r in ported.scores:
        e = expected[r["stock_code"]]
        for c in NUM_COLS:
            a, b = r[c], e[c]
            if a is None or b is None:
                if (a is None) != (b is None):
                    bad.append((r["stock_code"], c, a, b))
            elif not abs(a - b) <= TOL:
                bad.append((r["stock_code"], c, a, b))
    assert not bad, f"{len(bad)}건: {bad[:20]}"


def test_golden_rank_and_date_match(ported, expected) -> None:
    # GAP-7: v2 는 동점을 `set` 순회 순서(해시 시드)로 둔다. 09-28 골든에는 total_score 동점이
    # 없으므로(619종목 619값) v2 순위도 결정적이고, 이식판 순위와 전부 같아야 한다.
    assert len({e["total_score"] for e in expected.values()}) == len(expected)
    for r in ported.scores:
        e = expected[r["stock_code"]]
        assert r["score_date"] == e["score_date"] == "2026-09-28"
        assert r["rank"] == e["rank"], r["stock_code"]
    assert [r["rank"] for r in ported.scores] == list(range(1, 620))


def test_single_build_serves_v3_and_v2_goldens(golden_fi, ported, expected) -> None:
    # 같은 FactorInputs 객체 하나로 v3 원본과 v2 원본을 동시에 맞춘다(fi_consensus_annual 분리).
    # v2 쪽 수치 대조는 위 테스트가 이 판으로 했고, 여기서는 같은 판으로 v3 를 돌려 대조한다.
    assert golden_fi.check("v3_zscore") == [] and golden_fi.check("v2_percentrank") == []
    v3 = {r["stock_code"]: r for r in v3_zscore.ENGINE.run(registry.get("v3_zscore@1.0"),
                                                          golden_fi).scores}
    v3_exp = {str(r["stock_code"]): r
              for r in compat_to_fi.read_parquet_rows(GOLDEN / "score_history.parquet")}
    assert set(v3) == set(v3_exp) and len(v3) == 579
    for code, e in v3_exp.items():
        r = v3[code]
        assert r["rank"] == e["rank"], code
        for c in ("momentum_score", "revision_score", "flow_score", "quality_score",
                  "valuation_score", "composite_score", "op_change_1w", "ni_change_1m"):
            a, b = r[c], e[c]
            assert (a is None) == (b is None) and (a is None or abs(a - b) <= TOL), (code, c)
    assert {r["stock_code"]: r["rank"] for r in ported.scores} == \
        {k: e["rank"] for k, e in expected.items()}


# ── (b) 결정성 ────────────────────────────────────────────────────────────────
def test_deterministic_and_row_order_independent(golden_fi) -> None:
    spec = registry.get(SPEC_ID)
    first = ENGINE.run(spec, golden_fi).scores
    again = ENGINE.run(spec, golden_fi).scores
    rng = random.Random(20260928)
    shuffled = {}
    for name, rows in golden_fi.tables.items():
        rows = list(rows)
        rng.shuffle(rows)
        shuffled[name] = rows
    mixed = ENGINE.run(spec, FactorInputs(golden_fi.date, golden_fi.basis, golden_fi.build_id,
                                          shuffled)).scores
    assert first == again == mixed


# ── (c) 레지스트리 ─────────────────────────────────────────────────────────────
def test_registry_v2_spec_matches_v2_config() -> None:
    spec = registry.get(SPEC_ID)
    assert spec.validate() == [] and spec in registry.all_specs()
    assert spec.engine == "v2_percentrank"
    assert ENGINE.output_columns(spec) == V2_SCORE_COLUMNS
    assert spec.universe == UniverseRule()               # v2 는 시총 하한이 없다
    # v3 `config.yaml:57-85`(scoring_v2) — 합산 순서가 곧 비트 동등성이라 순서까지 대조한다
    assert list(spec.buckets.items()) == [("momentum", 0.30), ("growth", 0.35),
                                          ("flow", 0.25), ("value", 0.10)]
    p = spec.params
    assert list(p["momentum"]["sub_weights"].items()) == [("r1m", 0.40), ("r3m", 0.35),
                                                          ("r6m", 0.25)]
    assert list(p["growth"]["sub_weights"].items()) == [
        ("op_yoy_cur", 0.20), ("op_yoy_next", 0.20), ("ni_yoy_cur", 0.30), ("ni_yoy_next", 0.30)]
    assert list(p["flow"]["sub_weights"].items()) == [("inst_5d", 0.30), ("inst_20d", 0.20),
                                                      ("frgn_5d", 0.30), ("frgn_20d", 0.20)]
    assert list(p["value"]["sub_weights"].items()) == [("per_cur", 0.50), ("per_next", 0.50)]
    # v2 코드에 박힌 창(`v2_data_loader.py:15·22·104·116`, `momentum.py:7`, `flow.py:8`)
    assert (p["momentum"]["price_days"], p["momentum"]["price_rows"]) == (200, 130)
    assert dict(p["momentum"]["windows"]) == {"r1m": 21, "r3m": 63, "r6m": 126}
    assert (p["flow"]["flow_days"], p["flow"]["flow_rows"]) == (40, 20)
    assert {k: (v["subject"], v["rows"]) for k, v in p["flow"]["windows"].items()} == {
        "inst_5d": ("institution_total", 5), "inst_20d": ("institution_total", 20),
        "frgn_5d": ("foreign_investor", 5), "frgn_20d": ("foreign_investor", 20)}


def test_engine_rejects_specs_it_cannot_honour(golden_fi) -> None:
    spec = registry.get(SPEC_ID)
    with pytest.raises(ValueError, match="min_market_cap|유니버스"):
        ENGINE.run(replace(spec, universe=UniverseRule(min_market_cap=1000.0)), golden_fi)
    with pytest.raises(ValueError, match="buckets"):
        ENGINE.run(replace(spec, buckets={"growth": 0.35, "momentum": 0.30, "flow": 0.25,
                                          "value": 0.10}), golden_fi)
    with pytest.raises(ValueError, match="engine"):
        ENGINE.run(replace(spec, engine="v3_zscore"), golden_fi)
    bad = {**spec.params, "value": {"sub_weights": {"per_ttm": 1.0}}}   # 모르는 하위 지표
    with pytest.raises(ValueError, match="value"):
        ENGINE.run(replace(spec, params=bad), golden_fi)


# ── (d) 규칙 핀(합성 입력) ─────────────────────────────────────────────────────
D = "2026-09-28"
D0 = date.fromisoformat(D)


def _r(table: str, **values: object) -> dict[str, object]:
    return {c: values.get(c) for c in FI_TABLES[table].column_names}


def _fi(closes: dict[str, list[float]], *, step: int = 1, end_gap: dict[str, int] | None = None,
        caps: dict[str, float | None] | None = None, eligible: dict[str, bool] | None = None,
        flows: Sequence[dict] = (), annual: Sequence[dict] = (),
        consensus: Sequence[dict] = (), fins: Sequence[dict] = ()) -> FactorInputs:
    """수정종가 목록(마지막 값 = D − end_gap 일) → `step` 달력일 간격 가격 행.

    시총 기본 5,000억·적격.

    fi_prices.close 는 수정종가를 반올림한 정수로 둔다(엔진은 fi_adj_prices 를 먼저 본다).
    """
    prices, adj, uni = [], [], []
    for t, cs in closes.items():
        last = D0 - timedelta(days=(end_gap or {}).get(t, 0))
        for i, c in enumerate(cs):
            day = last - timedelta(days=step * (len(cs) - 1 - i))
            prices.append(_r("fi_prices", ticker=t, date=day, close=round(c)))
            adj.append(_r("fi_adj_prices", ticker=t, date=day, adj_close=float(c)))
        uni.append(_r("fi_universe", ticker=t, date=D0,
                      market_cap=(caps or {}).get(t, 5000.0),
                      eligible=(eligible or {}).get(t, True)))
    tables = {"fi_prices": prices, "fi_adj_prices": adj, "fi_universe": uni,
              "fi_flows": list(flows), "fi_consensus_annual": list(annual),
              "fi_consensus": list(consensus), "fi_fin_summary": list(fins)}
    return FactorInputs(D, "morning", "synthetic", tables)


def _run(fi: FactorInputs) -> dict[str, dict]:
    return {str(r["stock_code"]): r for r in ENGINE.run(registry.get(SPEC_ID), fi).scores}


def test_percentrank_is_excel_inc_with_ties_low_and_nan_kept() -> None:
    pr = v2_percentrank._percentrank_all
    got = pr({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0, "e": NAN})
    # PERCENTRANK.INC = (값보다 작은 개수) / (n − 1), 동점은 가장 낮은 자리(bisect_left)
    assert [got[k] for k in "abcd"] == [0.0, 1 / 3, 1 / 3, 1.0] and math.isnan(got["e"])
    one = pr({"a": 5.0, "b": NAN})
    assert one["a"] == 0.0 and math.isnan(one["b"])      # 유효값 2개 미만이면 0.0


def test_momentum_counts_rows_truncates_adj_and_keeps_no_price_on_d() -> None:
    a = [100.9] + [105.0] * 20 + [110.99]      # 22행 → r1m 있음. 절사: 110 / 100
    fi = _fi({"A": a, "B": [7.0] * 21,         # B: 21행 → r1m 없음(21 + 1 행 필요)
              "C": a, "Z": [5.0] * 22},
             end_gap={"C": 5})                  # C: D 가격 없음 — v2 는 그래도 점수를 낸다
    out = _run(fi)
    assert out["A"]["r1m"] == (110 / 100 - 1) * 100
    assert out["B"]["r1m"] is None and out["A"]["r3m"] is None
    assert out["C"]["r1m"] == out["A"]["r1m"] and out["C"]["rank"] is not None
    assert out["Z"]["r1m"] == 0.0
    # 백분위 모집단 = 이력이 있는 종목(결측은 NaN → 0 기여). r1m: Z 0 < A = C → 0, 0.5, 0.5
    assert out["Z"]["momentum_score"] == 0.0
    assert out["A"]["momentum_score"] == out["C"]["momentum_score"] == 0.40 * (1 / 2) * 100
    assert out["B"]["momentum_score"] == 0.0


def test_momentum_window_is_200_calendar_days_then_130_rows() -> None:
    daily = [float(100 + i) for i in range(300)]          # 매일 1행 → 창 안 201행 → 최근 130행
    every2 = [float(100 + i) for i in range(127)]         # 이틀 간격 127행 → 창 안 101행
    x = _run(_fi({"X": daily}))["X"]
    assert x["r6m"] == (399 / (399 - 126) - 1) * 100
    y = _run(_fi({"Y": every2}, step=2))["Y"]
    assert y["r3m"] == (226 / (226 - 63) - 1) * 100 and y["r6m"] is None


def test_momentum_zero_previous_close_is_missing_not_a_crash() -> None:
    # v2 는 ZeroDivisionError 로 전체 실행이 죽는다(`momentum.py:18`). 이식판은 결측(NaN)으로 둔다
    # — 출력이 없는 입력이라 맞출 원본 값이 없다(유일한 의도적 차이, 엔진 docstring).
    out = _run(_fi({"A": [0.4] + [3.0] * 21, "B": [2.0] * 22}))   # 0.4 → 절사 0
    assert out["A"]["r1m"] is None and out["B"]["r1m"] == 0.0


@pytest.mark.parametrize("cur, prev, want", [
    (None, 10.0, None), (10.0, None, None), (10.0, 0.0, None),
    (5.0, -10.0, None),                                  # 적자 → 흑자 전환은 NaN
    (-5.0, 10.0, (-5.0 / 10.0 - 1) * 100),               # 흑자 → 적자는 −150
    (-5.0, -10.0, (-5.0 / -10.0 - 1) * 100),             # 적자 축소가 −50(음의 성장)
    (0.0, -10.0, (0.0 / -10.0 - 1) * 100),
    (12.0, 10.0, (12.0 / 10.0 - 1) * 100),
])
def test_growth_yoy_sign_rules(cur, prev, want) -> None:
    got = v2_percentrank._yoy(cur, prev)
    assert (want is None and math.isnan(got)) or got == want


def _ann(t: str, period: str, data_type: str, **v: object) -> dict:
    return _r("fi_consensus_annual", ticker=t, period=period, data_type=data_type, **v)


def test_growth_value_pick_december_periods_estimate_first() -> None:
    annual = [
        _ann("A", "2025/12", "A", op=100.0, ni=50.0),
        _ann("A", "2026/12", "E", op=120.0, ni=60.0, per=10.0),
        _ann("A", "2026/12", "A", op=999.0, ni=999.0, per=99.0),     # E 가 있으면 A 는 무시
        _ann("A", "2027/12", "E", op=150.0, ni=90.0, per=8.0),
        _ann("A", "2026/06", "E", op=1.0, ni=1.0, per=1.0),           # 비12월 — 읽지 않는다
        _ann("B", "2025/12", "A", op=40.0, ni=20.0),
        _ann("B", "2026/12", "A", op=80.0, ni=-10.0, per=5.0),        # 당해 E 없음 → A 폴백
        _ann("B", "2027/12", "A", op=500.0, per=1.0),                  # 차년은 E 만 — A 무시
        _ann("C", "2025/12", "E", op=10.0, ni=5.0),                    # 전년도 E 가 A 보다 먼저
        _ann("C", "2025/12", "A", op=20.0, ni=5.0),
        _ann("C", "2026/12", "E", op=30.0, ni=None, per=-3.0),
        _ann("C", "2027/12", "E", op=None, ni=None, per=None),
        _ann("E", "2027/12", "A", op=1.0),                             # 차년 A 한 행뿐
        _ann("F", "2026/03", "E", op=1.0, per=1.0),                    # 비12월 결산 종목
    ]
    # v3 원천(fi_consensus·fi_fin_summary)은 읽지 않는다 — G 에만 실어 둔다
    cons = [_r("fi_consensus", ticker="G", target_period="2026/12", horizon="cur",
               op=1.0, ni=1.0, per=1.0)]
    fins = [_r("fi_fin_summary", ticker="G", period="2025/12", period_type="annual",
               op=1.0, ni=1.0, per=1.0)]
    one = [1.0] * 3
    out = _run(_fi({t: one for t in "ABCDEFG"}, annual=annual, consensus=cons, fins=fins))
    a, b, c = out["A"], out["B"], out["C"]
    assert (a["op_yoy_cur"], a["op_yoy_next"]) == ((120 / 100 - 1) * 100, (150 / 120 - 1) * 100)
    assert (a["ni_yoy_cur"], a["ni_yoy_next"]) == ((60 / 50 - 1) * 100, (90 / 60 - 1) * 100)
    assert (a["per_cur"], a["per_next"]) == (10.0, 8.0)
    assert (b["op_yoy_cur"], b["ni_yoy_cur"]) == ((80 / 40 - 1) * 100, (-10 / 20 - 1) * 100)
    assert (b["op_yoy_next"], b["per_cur"], b["per_next"]) == (None, 5.0, None)
    assert c["op_yoy_cur"] == (30 / 10 - 1) * 100 and c["ni_yoy_cur"] is None
    assert c["per_cur"] == -3.0                                         # 원값은 음수 그대로
    # PER ≤ 0·결측은 백분위 NaN → 0 으로 바뀐 뒤 (1 − 0) 이라 **만점** 기여(`value.py:33-37`).
    # per_cur 모집단 {A 10, B 5}: A 1.0 · B 0.0 → A 는 0.5·(1−1), B 는 0.5·(1−0)
    # per_next 는 유효값이 A 하나뿐 → 백분위 0.0(n < 2) → A 도 (1 − 0) 만점 기여
    assert a["value_score"] == (0.5 * (1 - 1.0) + 0.5 * (1 - 0.0)) * 100
    assert b["value_score"] == (0.5 * 1 + 0.5 * 1) * 100
    assert c["value_score"] == 100.0
    # 추정·확정 행이 하나도 없으면 성장·밸류 0.0, 차년 A 한 행만 있어도 "있는 종목" → 밸류 100
    assert (out["D"]["growth_score"], out["D"]["value_score"], out["D"]["per_cur"]) == \
        (0.0, 0.0, None)
    assert (out["E"]["growth_score"], out["E"]["value_score"]) == (0.0, 100.0)
    assert (out["F"]["growth_score"], out["F"]["value_score"]) == (0.0, 0.0)   # 비12월 무시
    g = out["G"]
    assert (g["growth_score"], g["value_score"], g["per_cur"], g["op_yoy_cur"]) == \
        (0.0, 0.0, None, None)


def test_consensus_annual_rejects_unknown_data_type() -> None:
    bad = [_ann("A", "2026/12", "estimate", op=1.0)]
    with pytest.raises(ValueError, match="data_type"):
        _run(_fi({"A": [1.0] * 3}, annual=bad))


def test_flow_window_rows_null_as_zero_and_no_rows_ranked() -> None:
    def f(t: str, days_ago: int, inst: float | None, frgn: float | None = 1.0) -> dict:
        return _r("fi_flows", ticker=t, date=D0 - timedelta(days=days_ago),
                  institution_total=inst, foreign_investor=frgn)
    rows = [f("A", i, float(i)) for i in range(45)]     # 45일 → 창 [D−40, D] 41행 → 최근 20행
    rows += [f("A", -1, 1e6)]                            # D 다음 날 — 읽지 않는다
    rows += [f("B", i, None if i == 0 else 2.0, None) for i in range(3)]
    rows += [f("D", 41, 1e6)]                            # 창 밖 — 행이 없는 것과 같다
    out = _run(_fi({t: [1.0] * 3 for t in "ABCD"}, caps={"A": 2000.0}, flows=rows))
    a = out["A"]
    # 최근 5행 = 0..4일 전, 20행 = 0..19일 전. 합 ÷ 시총(억원) × 100
    assert a["inst_5d"] == sum(range(5)) / 2000.0 * 100
    assert a["inst_20d"] == sum(range(20)) / 2000.0 * 100
    assert out["B"]["inst_5d"] == 4.0 / 5000.0 * 100 and out["B"]["frgn_5d"] == 0.0
    # 수급 행이 없는 종목도 원값 0.0 으로 백분위에 들어간다(`v2_engine.py:71-72`)
    assert out["C"]["inst_5d"] == out["D"]["inst_20d"] == 0.0
    assert out["C"]["flow_score"] == out["D"]["flow_score"] is not None


def test_universe_needs_eligible_positive_cap_and_ties_sort_by_code() -> None:
    same = [1.0] * 3
    fi = _fi({"C": same, "A": same, "B": same, "TINY": same, "ZERO": same, "NOCAP": same,
              "INEL": same},
             caps={"TINY": 1.0, "ZERO": 0.0, "NOCAP": None}, eligible={"INEL": False})
    rows = ENGINE.run(registry.get(SPEC_ID), fi).scores
    # 시총 하한 없음(1억도 들어간다) · 시총 0/NULL·비적격 제외 · 동점은 종목코드 오름차순(GAP-7)
    assert [(r["stock_code"], r["rank"]) for r in rows] == \
        [("A", 1), ("B", 2), ("C", 3), ("TINY", 4)]
    assert len({r["total_score"] for r in rows}) == 1
