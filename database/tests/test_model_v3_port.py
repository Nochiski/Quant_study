"""v3 엔진 이식(M2 W1-c — T2.1 레지스트리 · T2.3 `engines/v3_zscore`).

(a) 골든: 09-28 그림자 compat 입력 → 이식 엔진 = v3 원본 점수
    (|Δ| ≤ 1e-9, NULL 일치, 플래그·rank 동일)
(b) 결정성: 두 번 실행·입력 행 순서 섞기 → 비트 단위 동일
(c) 레지스트리: `v3_zscore@1.0` 적재·검증, 값이 v3 `config.yaml`(meta.json) 과 같다
(d) 규칙 핀: 팩터마다 헷갈리기 쉬운 규칙 하나씩(합성 입력)
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest
from model import registry
from model.contracts import ALL_ENGINES, FI_TABLES, V3_SCORE_COLUMNS, FactorInputs
from model.engines import ENGINES, _common, v3_zscore

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
# tests/tools/ 는 패키지가 아니라 위 sys.path 주입 뒤에야 import 된다.
import compat_to_fi  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "model_golden" / "2026-09-28"
SPEC_ID = "v3_zscore@1.0"
TOL = 1e-9
FLAG_COLS = tuple(c for c in V3_SCORE_COLUMNS if c.endswith("_flag"))
NUM_COLS = tuple(c for c in V3_SCORE_COLUMNS
                 if c not in ("stock_code", "score_date", "rank", *FLAG_COLS))
ENGINE = ENGINES["v3_zscore"]


# ── (a) 골든 ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def golden_fi() -> FactorInputs:
    return compat_to_fi.load_golden(GOLDEN)


@pytest.fixture(scope="module")
def expected() -> dict[str, dict]:
    rows = compat_to_fi.read_parquet_rows(GOLDEN / "score_history.parquet")
    return {str(r["stock_code"]): r for r in rows}


@pytest.fixture(scope="module")
def ported(golden_fi):
    return ENGINE.run(registry.get(SPEC_ID), golden_fi)


def test_golden_adapter_meets_contract(golden_fi) -> None:
    assert golden_fi.check("v3_zscore") == []


def test_golden_same_universe(ported, expected) -> None:
    got = [r["stock_code"] for r in ported.scores]
    assert len(got) == len(set(got)) == len(expected) == 579
    assert set(got) == set(expected)


def test_golden_row_shape(ported) -> None:
    assert all(tuple(r) == V3_SCORE_COLUMNS for r in ported.scores)
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


def test_golden_flags_rank_date_match(ported, expected) -> None:
    for r in ported.scores:
        e = expected[r["stock_code"]]
        assert r["score_date"] == e["score_date"] == "2026-09-28"
        assert r["rank"] == e["rank"], r["stock_code"]
        assert {c: r[c] for c in FLAG_COLS} == {c: e[c] for c in FLAG_COLS}, r["stock_code"]
    assert [r["rank"] for r in ported.scores] == list(range(1, 580))


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
def test_registry_v3_spec_matches_v3_config() -> None:
    spec = registry.get(SPEC_ID)
    assert spec.validate() == []
    assert spec in registry.all_specs()
    assert spec.engine == "v3_zscore" and set(ENGINES) <= set(ALL_ENGINES)
    assert ENGINE.output_columns(spec) == V3_SCORE_COLUMNS
    assert spec.universe.sec_types == ("common", "spac") and spec.universe.require_estimates
    cfg = json.loads((GOLDEN / "meta.json").read_text(encoding="utf-8"))["v3_scoring_config"]
    assert spec.universe.min_market_cap == cfg["min_market_cap"]
    assert list(spec.buckets.items()) == [(f, v["weight"]) for f, v in cfg["factors"].items()]
    for f, v in cfg["factors"].items():
        # 합산 순서가 v3 config 순서와 같아야 비트 단위로 맞는다 → 순서까지 대조
        sub = spec.params[f]
        assert isinstance(sub, dict)
        assert {k: list(x.items()) for k, x in sub.items()} == \
            {k: list(x.items()) for k, x in v.items() if k != "weight"}, f


def test_registry_reports_all_errors_of_a_bad_spec(tmp_path: Path) -> None:
    (tmp_path / "bad.toml").write_text(
        'model_id = "x"\nversion = "1"\nengine = "v9"\n[buckets]\na = 0.5\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bad.toml") as ei:
        registry.load(tmp_path)
    assert "engine" in str(ei.value) and "가중 합" in str(ei.value)


def test_registry_rejects_unknown_key_and_duplicate(tmp_path: Path) -> None:
    body = 'model_id = "x"\nversion = "1"\nengine = "v3_zscore"\n[buckets]\na = 1.0\n'
    (tmp_path / "a.toml").write_text(body, encoding="utf-8")
    (tmp_path / "b.toml").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="중복"):
        registry.load(tmp_path)
    (tmp_path / "b.toml").write_text("weigths = 1\n" + body, encoding="utf-8")
    with pytest.raises(ValueError, match="모르는 키"):
        registry.load(tmp_path)
    (tmp_path / "b.toml").unlink()
    with pytest.raises(KeyError):
        registry.get("nope@0", tmp_path)


# ── (d) 규칙 핀(합성 입력) ─────────────────────────────────────────────────────
D = "2026-09-28"


def _r(table: str, **values: object) -> dict[str, object]:
    return {c: values.get(c) for c in FI_TABLES[table].column_names}


def _fi(closes: dict[str, list[float]], *, caps: dict[str, float] | None = None,
        eligible: dict[str, bool] | None = None, flows: Sequence[dict] = (),
        consensus: Sequence[dict] = (), fins: Sequence[dict] = ()) -> FactorInputs:
    """종가 목록(마지막 값 = D) → 달력일 연속 가격 행. 시총 기본 5,000억·적격."""
    d0 = date.fromisoformat(D)
    prices, adj, uni = [], [], []
    for t, cs in closes.items():
        for i, c in enumerate(cs):
            day = d0 - timedelta(days=len(cs) - 1 - i)
            prices.append(_r("fi_prices", ticker=t, date=day, close=c))
            adj.append(_r("fi_adj_prices", ticker=t, date=day, adj_close=float(c)))
        uni.append(_r("fi_universe", ticker=t, date=d0,
                      market_cap=(caps or {}).get(t, 5000.0),
                      eligible=(eligible or {}).get(t, True)))
    tables = {"fi_prices": prices, "fi_adj_prices": adj, "fi_universe": uni,
              "fi_flows": list(flows), "fi_consensus": list(consensus),
              "fi_fin_summary": list(fins)}
    return FactorInputs(D, "morning", "synthetic", tables)


def _run(fi: FactorInputs) -> dict[str, dict]:
    return {str(r["stock_code"]): r for r in ENGINE.run(registry.get(SPEC_ID), fi).scores}


def test_winsorized_z_uses_sample_std_and_unclipped_moments() -> None:
    z = _common.z_score_winsorized
    assert z([1.0]) == [0.0] and z([]) == [] and z([2.0, 2.0, 2.0]) == [0.0, 0.0, 0.0]
    vals = [0.0] * 19 + [100.0]              # 평균 5 · 표본 표준편차 √500(모집단이면 √475)
    out = z(vals)
    assert out[0] == pytest.approx(-5 / math.sqrt(500), abs=1e-15)
    # mean + 3σ 로 자른 뒤 **자르기 전** 평균·σ 로 표준화
    assert out[-1] == pytest.approx(3.0, abs=1e-12)


def test_momentum_lookback_counts_sessions_and_missing_gets_zero() -> None:
    up = [100.0 + i for i in range(21)]      # 21행 → r1m(20세션 전) 있음, r3m 없음
    fi = _fi({"A": up, "B": up[::-1], "C": [50.0] * 21, "E": [10.0] * 20})   # E: 20행 → r1m 없음
    out = _run(fi)
    assert out["A"]["r1m"] == (120.0 - 100.0) / 100.0 and out["A"]["r3m"] is None
    assert out["E"]["r1m"] is None
    zr = _common.z_score_winsorized([out[t]["r1m"] for t in "ABC"])
    # 하위 지표가 r1m 하나뿐이면 비례 재정규화로 모멘텀 원점수 = z(r1m), 지표가 없는 E 는 0.0 로
    # 2단계 정규화에 들어간다(v3 momentum.py:22-24)
    level2 = _common.z_score_winsorized([*zr, 0.0])
    assert [out[t]["momentum_score"] for t in "ABCE"] == level2
    # 다른 팩터가 없으므로 composite = momentum 단독(가중 재정규화)
    assert all(out[t]["composite_score"] == out[t]["momentum_score"] for t in "ABCE")


@pytest.mark.parametrize("cur, prev, change, flag", [
    (5.0, -10.0, 1.5, "흑전"), (-5.0, 10.0, -1.5, "적전"),
    (-20.0, -10.0, -1.0, "적확"), (-5.0, -10.0, 0.5, "적축"), (-10.0, -10.0, 0.0, "적축"),
    (0.0, 10.0, -1.0, None), (12.0, 10.0, 0.2, None),
    (10.0, 0.0, 0.0, None), (None, 10.0, 0.0, None), (10.0, None, 0.0, None),
])
def test_revision_change_and_flag_thresholds(cur, prev, change, flag) -> None:
    got = v3_zscore._change(cur, prev)
    assert got[0] == pytest.approx(change, abs=1e-15) and got[1] == flag


def test_revision_picks_nearest_open_period_and_needs_both_rows() -> None:
    def c(t: str, tp: str, h: str, op: float, ni: float) -> dict:
        return _r("fi_consensus", ticker=t, target_period=tp, horizon=h, op=op, ni=ni)
    cons = [
        c("A", "2026/06", "cur", 1.0, 1.0), c("A", "2026/06", "1w", 99.0, 99.0),  # 끝난 결산기
        c("A", "2026/12", "cur", -5.0, 12.0), c("A", "2026/12", "1w", -10.0, 10.0),
        c("A", "2027/12", "cur", 7.0, 7.0), c("A", "2027/12", "1w", 1.0, 1.0),
        c("B", "2026/12", "cur", 5.0, 5.0),                                          # compare 없음
    ]
    out = _run(_fi({"A": [1.0] * 5, "B": [1.0] * 5}, consensus=cons))
    a = out["A"]
    assert (a["op_change_1w"], a["op_1w_flag"]) == (0.5, "적축")
    assert (a["ni_change_1w"], a["ni_1w_flag"]) == (pytest.approx(0.2), None)
    # 1m·3m 행이 없으면 v3 처럼 0.0(값 없음 → _change 0.0)
    assert a["op_change_1m"] == a["ni_change_3m"] == 0.0 and a["op_1m_flag"] is None
    assert a["revision_score"] is not None
    assert out["B"]["op_change_1w"] is None and out["B"]["revision_score"] is None


def test_flow_raw_is_net_over_cap_and_counts_records() -> None:
    d0 = date.fromisoformat(D)

    def f(t: str, days_ago: int, inst: float | None) -> dict:
        return _r("fi_flows", ticker=t, date=d0 - timedelta(days=days_ago),
                  institution_total=inst, foreign_investor=1.0)
    rows = [f("A", i, float(i + 1)) for i in range(0, 12, 2)]      # 6행, 이틀 간격
    rows += [f("A", -1, 1000.0)]                                    # D 다음 날 — 읽지 않는다
    rows += [f("B", i, None if i == 0 else 3.0) for i in range(6)]  # NULL → 0
    fi = _fi({"A": [1.0] * 5, "B": [1.0] * 5, "C": [1.0] * 5}, caps={"A": 2000.0},
             flows=rows)
    out = _run(fi)
    # 최근 5 **행**(달력일 아님): 1+3+5+7+9 = 25 백만원 / 2,000 억원
    assert out["A"]["flow_inst_5d"] == 25.0 / 2000.0
    assert out["A"]["flow_inst_20d"] == 36.0 / 2000.0
    assert out["B"]["flow_inst_5d"] == 12.0 / 5000.0
    assert out["C"]["flow_inst_5d"] is None and out["C"]["flow_score"] is None
    assert out["A"]["flow_score"] is not None


def test_quality_valuation_sub_scores_reverse_and_single_value_zero() -> None:
    raw = {"A": {"gpa": 0.1, "roa": 5.0, "debt_ratio": 100.0},
           "B": {"roa": 1.0, "debt_ratio": 200.0}}
    w = {"gpa": 0.2, "roa": 0.15, "debt_ratio": 0.15}
    got = v3_zscore._score_subs(raw, w, reverse={"debt_ratio"})
    h = 1 / math.sqrt(2)                       # 두 값의 z = ±1/√2
    # gpa 는 A 하나뿐 → z 0.0(quality.py:44-47). 부채비율은 낮을수록 좋다(부호 반전).
    assert got["A"] == pytest.approx((0.2 * 0.0 + 0.15 * h + 0.15 * h) / 0.5, abs=1e-15)
    assert got["B"] == pytest.approx((0.15 * -h + 0.15 * -h) / 0.3, abs=1e-15)


def test_quality_needs_annual_rows_and_valuation_drops_nonpositive() -> None:
    def fin(t: str, period: str, **v: object) -> dict:
        return _r("fi_fin_summary", ticker=t, period=period, period_type="annual", **v)
    fins = [fin("A", "2025/12", per=-3.0, pbr=0.8, ev_ebitda=0.0, dividend_yield=0.0,
                debt_ratio=50.0),
            fin("A", "2024/12", per=9.0, pbr=0.9),
            fin("B", "2025/12", per=12.0, debt_ratio=80.0),
            _r("fi_fin_summary", ticker="C", period="2026/03", period_type="quarter", per=5.0)]
    closes = [100.0 + (i % 3) for i in range(15)]
    out = _run(_fi({"A": closes, "B": closes, "C": closes}, fins=fins))
    a = out["A"]
    # 최신 연간 1행만: PER ≤ 0·EV/EBITDA ≤ 0 은 버리고 배당수익률 0 은 남긴다(valuation.py:74-85)
    assert (a["val_per"], a["val_pbr"], a["val_ev_ebitda"], a["val_dividend_yield"]) == \
        (None, 0.8, None, 0.0)
    assert a["qual_std_20d"] is not None
    # 연간 재무 행이 없으면 가격이 있어도 std_20d 를 만들지 않는다(quality.py:78-79)
    assert out["C"]["qual_std_20d"] is None and out["C"]["quality_score"] is None
    assert out["C"]["val_per"] is None and out["C"]["valuation_score"] is None


def test_universe_min_cap_eligible_price_on_d_and_tie_order() -> None:
    same = [1.0] * 5
    fi = _fi({"C": same, "A": same, "B": same, "LOW": same, "INEL": same},
             caps={"B": 1000.0, "LOW": 999.0}, eligible={"INEL": False})
    tables = {k: list(v) for k, v in fi.tables.items()}
    tables["fi_prices"].append(_r("fi_prices", ticker="OLD",
                                  date=date.fromisoformat(D) - timedelta(days=1), close=1))
    tables["fi_universe"].append(_r("fi_universe", ticker="OLD", market_cap=9000.0,
                                    eligible=True))
    rows = ENGINE.run(registry.get(SPEC_ID), FactorInputs(D, "morning", "synthetic", tables)).scores
    # 시총 하한은 이상(≥) · 비적격 제외 · D 가격 없으면 제외 · 동점은 종목코드 오름차순
    assert [(r["stock_code"], r["rank"]) for r in rows] == [("A", 1), ("B", 2), ("C", 3)]
    assert all(r["composite_score"] == 0.0 for r in rows)
