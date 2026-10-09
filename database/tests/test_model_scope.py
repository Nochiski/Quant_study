"""scope_v1.0(`scope@1.0`) — 원본 v3(`v3_zscore@1.0`)에서 밸류의 EV/EBITDA 만 뺀 메인 모델.

(a) 레지스트리: 엔진·버킷·유니버스·하위 가중이 v3_zscore@1.0 과 글자 그대로 같고, 밸류 하위
    가중만 ev_ebitda 가 빠진다(나머지 키 순서 유지 — 합산 순서). 3개월 의견 기준(min_analysts = 1)은
    10-05 에 넣었다가 10-08 사용자 결정으로 뺐다 — 의견 0 은 엑셀 비고로만 표시한다.
(b) 골든 09-28 입력: 밸류·종합 밖의 팩터 점수는 v3_zscore@1.0 과 비트 단위로 같다.
(c) EV/EBITDA 값을 아무리 바꿔도 scope 점수는 그대로다(원값 표시 열 val_ev_ebitda 만 따라간다).
(d) 추정기관수(최근 3개월 투자의견을 낸 증권사 수) 0 도 scope 유니버스 안(10-08). 엔진의
    min_analysts 지원은 남아 있어, spec 이 그 값을 주면 0 은 빠지고 모름(NULL)·1 이상은 남는다.
(e) MG1 이 spec 과 같은 규칙으로 유니버스를 센다(안 그러면 빠진 종목이 '점수 누락'으로 잡혀 판이
    막힌다).
(f) 인계(엑셀)의 기본 주 모델은 scope@1.0 이다.
(g) 짧은 첫 사업연도(G-28, N-25 Q5): scope 만 12개월 미만 연간 행을 퀄리티 손익 지표(gpa·roa·
    fcf_assets·gpa_change)에서 뺀다. v3_zscore@1.0 은 그대로(회귀 가드).
(h) 창 안 제한폭 초과 미해결 수정주가(T-9 · H1-4): scope 만 모멘텀(r1m~r12m)·20일 변동성 창이
    `fi_adj_prices.adj_jump_ok` 계단을 넘으면 그 지표를 결측으로 둔다(GH1-c 반사실). 판 메타에
    종목 수·지표별 칸 수. v3_zscore@1.0 은 그대로(회귀 가드).
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from model import gates, registry
from model.build import PRIMARY_DEFAULT
from model.contracts import FI_TABLES, V3_SCORE_COLUMNS, FactorInputs, ModelSpec
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
    assert scope.universe == v3.universe and scope.universe.min_analysts is None   # 10-08 해제
    assert ENGINE.output_columns(scope) == V3_SCORE_COLUMNS
    for f in ("momentum", "revision", "flow"):
        assert scope.params[f] == v3.params[f], f
    # 퀄리티는 하위 가중이 같고 짧은 첫 사업연도 규칙(min_period_months, G-28)만 더한다 — (g)
    s_q, v_q = scope.params["quality"], v3.params["quality"]
    assert isinstance(s_q, dict) and isinstance(v_q, dict)
    assert list(s_q["sub_weights"].items()) == list(v_q["sub_weights"].items())
    assert set(s_q) ^ set(v_q) == {"min_period_months"}
    v3_val = dict(v3.params["valuation"]["sub_weights"])       # type: ignore[index]
    v3_val.pop("ev_ebitda")
    assert list(scope.params["valuation"]["sub_weights"].items()) == list(v3_val.items())  # type: ignore[index]


# ── (b) 골든 입력에서 v3_zscore@1.0 과의 차이 ────────────────────────────────────
def test_scope_differs_from_v3_only_in_valuation_and_composite(golden_fi) -> None:
    # 전제: 골든(compat 어댑터) 입력의 period_months 는 모두 NULL(compat 에 원천이 없다)이라
    # scope 의 짧은 회계기간 규칙(G-28)이 작동하지 않는다 — 그래서 퀄리티 점수가 v3_zscore@1.0 과
    # 같다.
    assert all(r["period_months"] is None for r in golden_fi.tables["fi_fin_summary"])
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


def _with_min_analysts(n: int) -> ModelSpec:
    """scope 에 추정기관수 하한을 다시 건 spec — 엔진·MG1 의 min_analysts 지원을 시험한다(10-08 뒤
    이 값을 쓰는 등록 spec 은 없다)."""
    scope = registry.get(SCOPE)
    return replace(scope, universe=replace(scope.universe, min_analysts=n))


def test_scope_keeps_stocks_without_opinions_in_three_months(golden_fi) -> None:
    """10-08 사용자 결정: 의견 0 종목도 점수 대상(엑셀 비고 '최근 3개월 의견 없음'으로만 표시)."""
    codes = sorted(_by_code(ENGINE.run(registry.get(V3), golden_fi).scores))
    zero, unknown, one = codes[:3]
    fi = _with_analysts(golden_fi, {zero: 0, unknown: None, one: 1})
    scope = _by_code(ENGINE.run(registry.get(SCOPE), fi).scores)
    v3 = _by_code(ENGINE.run(registry.get(V3), fi).scores)
    assert {zero, unknown, one} <= scope.keys() and set(scope) == set(v3)
    assert scope[zero]["rank"] is not None and scope[zero]["composite_score"] is not None


def test_min_analysts_rule_still_works_when_a_spec_sets_it(golden_fi) -> None:
    codes = sorted(_by_code(ENGINE.run(registry.get(V3), golden_fi).scores))
    zero, unknown, one = codes[:3]
    fi = _with_analysts(golden_fi, {zero: 0, unknown: None, one: 1})
    got = _by_code(ENGINE.run(_with_min_analysts(1), fi).scores)
    v3 = _by_code(ENGINE.run(registry.get(V3), fi).scores)
    assert zero not in got and {unknown, one} <= got.keys()
    assert set(got) == set(v3) - {zero}
    ranks = sorted(r["rank"] for r in got.values() if r["rank"] is not None)
    assert ranks == list(range(1, len(ranks) + 1))


def test_mg1_counts_the_universe_with_the_spec_rule(golden_fi) -> None:
    codes = sorted(_by_code(ENGINE.run(registry.get(V3), golden_fi).scores))
    fi = _with_analysts(golden_fi, dict.fromkeys(codes[:100], 0))
    spec = _with_min_analysts(1)
    res = ENGINE.run(spec, fi)
    ctx = gates.GateContext(spec=spec, date=str(fi.date)[:10], inputs=fi, result=res,
                            rerun=res, n_prices_on_d=len(codes))
    g = gates.mg1_coverage(ctx)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_eligible"] == g.metrics["n_covered"] == len(res.scores) == len(codes) - 100


# ── (g) 짧은 첫 사업연도(G-28, N-25 Q5) ────────────────────────────────────────
_D = "2026-09-28"
_LOSS_PROFIT = ("qual_gpa", "qual_roa", "qual_fcf_assets", "qual_gpa_change")


def _short_year_fi() -> FactorInputs:
    """종목 4개 × 연간 2기. SHORT = 최신 기가 7개월 · PREV = 전기가 7개월 · NULL = 기간 모름 ·
    FULL = 둘 다 12개월. 퀄리티 원값이 다 서도록 가격 15행·재무 값을 채운다."""
    d0 = date.fromisoformat(_D)

    def r(table: str, **v: object) -> dict[str, object]:
        return {c: v.get(c) for c in FI_TABLES[table].column_names}

    months = {"FULL": (12, 12), "NULL": (None, None), "PREV": (12, 7), "SHORT": (7, 12)}
    prices, adj, uni, fins = [], [], [], []
    for i, (t, (m_cur, m_prev)) in enumerate(months.items()):
        for k in range(15):
            day = d0 - timedelta(days=14 - k)
            px = 100.0 + i + (k % 3)
            prices.append(r("fi_prices", ticker=t, date=day, close=px))
            adj.append(r("fi_adj_prices", ticker=t, date=day, adj_close=px))
        uni.append(r("fi_universe", ticker=t, date=d0, market_cap=5000.0, eligible=True))
        for period, m, gp in (("2025/12", m_cur, 30.0 + i), ("2024/12", m_prev, 20.0 + i)):
            fins.append(r("fi_fin_summary", ticker=t, period=period, period_type="annual",
                          gross_profit=gp, total_assets=100.0, roa=5.0 + i, fcf=10.0 + i,
                          debt_ratio=50.0 + i, period_months=m))
    tables = {"fi_prices": prices, "fi_adj_prices": adj, "fi_universe": uni, "fi_flows": [],
              "fi_consensus": [], "fi_fin_summary": fins}
    return FactorInputs(_D, "morning", "synthetic", tables)


def test_scope_spec_turns_on_the_short_year_rule_only_for_scope() -> None:
    s_q, v_q = registry.get(SCOPE).params["quality"], registry.get(V3).params["quality"]
    assert isinstance(s_q, dict) and isinstance(v_q, dict)
    assert s_q["min_period_months"] == 12 and "min_period_months" not in v_q


def test_scope_drops_short_year_rows_from_profit_quality_only() -> None:
    fi = _short_year_fi()
    scope = _by_code(ENGINE.run(registry.get(SCOPE), fi).scores)
    v3 = _by_code(ENGINE.run(registry.get(V3), fi).scores)
    # 최신 기가 짧으면 그 기의 손익 지표(gpa·roa·fcf_assets)와 gpa_change 를 다 뺀다
    assert [scope["SHORT"][c] for c in _LOSS_PROFIT] == [None] * 4
    # 전기가 짧으면 gpa_change 만 뺀다(전기 대비 변화율 분모가 7개월 값)
    assert scope["PREV"]["qual_gpa_change"] is None
    assert scope["PREV"]["qual_gpa"] == v3["PREV"]["qual_gpa"] == 0.32
    # 대차·가격 지표는 그대로 — 퀄리티 점수도 남는다
    for code in ("SHORT", "PREV"):
        assert scope[code]["qual_debt_ratio"] == v3[code]["qual_debt_ratio"]
        assert scope[code]["qual_std_20d"] == v3[code]["qual_std_20d"]
        assert scope[code]["quality_score"] is not None
    # 기간을 모르면(NULL) 빼지 않는다 · 12개월은 그대로
    for code in ("FULL", "NULL"):
        assert [scope[code][c] for c in _LOSS_PROFIT] == [v3[code][c] for c in _LOSS_PROFIT]
        assert scope[code]["qual_gpa_change"] is not None


def test_v3_zscore_keeps_short_year_rows_regression_guard() -> None:
    """v3_zscore@1.0(원본 대조용)은 기간 열을 보지 않는다 — 7개월 기의 값을 그대로 쓴다."""
    v3 = _by_code(ENGINE.run(registry.get(V3), _short_year_fi()).scores)
    assert v3["SHORT"]["qual_gpa"] == 0.33 and v3["SHORT"]["qual_roa"] == 8.0
    assert v3["SHORT"]["qual_fcf_assets"] == 0.13
    assert v3["SHORT"]["qual_gpa_change"] == (0.33 - 0.23) / 0.23
    assert v3["PREV"]["qual_gpa_change"] == (0.32 - 0.22) / 0.22


@pytest.mark.parametrize("bad", [{"min_period_month": 12},      # 오타 — 조용히 꺼지면 안 된다
                                 {"min_period_months": True},   # bool 은 정수로 치지 않는다
                                 {"min_period_months": 0}])
def test_quality_params_are_validated(bad: dict[str, object]) -> None:
    spec = registry.get(SCOPE)
    s_q = spec.params["quality"]
    assert isinstance(s_q, dict)
    quality = {"sub_weights": s_q["sub_weights"], **bad}
    bent = replace(spec, params={**spec.params, "quality": quality})
    with pytest.raises(ValueError, match="params.quality"):
        ENGINE.run(bent, _short_year_fi())


# ── (h) 창 안 제한폭 초과 미해결 수정주가(T-9 · H1-4) ───────────────────────────
_N = 260                                     # 가격 행 수 — r12m(240 세션) 창이 서고 남는다
_MOM = ("r1m", "r3m", "r6m", "r9m", "r12m")
# 점수(정규화 결과)가 아닌 원값 열 — 처리 대상 밖이면 바뀌면 안 된다(GH1-c ③)
_SCORES = ("stock_code", "score_date", "momentum_score", "revision_score", "flow_score",
           "valuation_score", "composite_score", "rank", "quality_score")
_RAW = tuple(c for c in V3_SCORE_COLUMNS if c not in _SCORES)
# 종목 → (adj_ok 가 False 로 바뀌는 행, adj_jump_ok 가 False 로 바뀌는 행). 행 = 오래된 쪽부터 0…N−1
_STEPS: dict[str, tuple[int | None, int | None]] = {
    "J5": (_N - 5, _N - 5),          # 점프가 모든 창 안 → 모멘텀 5개 · std_20d 결측
    "J21": (_N - 21, _N - 21),       # r1m(21행) · std_20d(21행) 창은 사건 행에서 시작 → 그대로
    "LIM": (_N - 5, None),           # 미해결이지만 제한폭 안(adj_ok 만 뒤집힘) → 그대로
    "OLD": (_N - 250, _N - 250),     # r12m 창(241행) 밖 → 그대로
    "N1": (None, None), "N2": (None, None), "N3": (None, None), "N4": (None, None)}


def _jump_fi() -> FactorInputs:
    """종목 8개 × 가격 260행(달력일 연속) + 연간 2기. J5 는 점프 행부터 가격이 1.5배다."""
    d0 = date.fromisoformat(_D)

    def r(table: str, **v: object) -> dict[str, object]:
        return {c: v.get(c) for c in FI_TABLES[table].column_names}

    prices, adj, uni, fins = [], [], [], []
    for i, (t, (ok_row, jump_row)) in enumerate(_STEPS.items()):
        for k in range(_N):
            day = d0 - timedelta(days=_N - 1 - k)
            px = 100.0 + 3 * i + 0.05 * (i + 1) * k + 0.7 * ((k * (i + 2)) % 5)
            if t == "J5" and k >= _N - 5:
                px *= 1.5
            prices.append(r("fi_prices", ticker=t, date=day, close=round(px)))
            adj.append(r("fi_adj_prices", ticker=t, date=day, adj_close=px,
                         adj_ok=ok_row is None or k < ok_row,
                         adj_jump_ok=jump_row is None or k < jump_row))
        uni.append(r("fi_universe", ticker=t, date=d0, market_cap=5000.0, eligible=True))
        for period, gp in (("2025/12", 30.0 + i), ("2024/12", 20.0 + i)):
            fins.append(r("fi_fin_summary", ticker=t, period=period, period_type="annual",
                          gross_profit=gp, total_assets=100.0, roa=5.0 + i, fcf=10.0 + i,
                          debt_ratio=50.0 + i, per=8.0 + i, pbr=1.0 + 0.1 * i,
                          period_months=12))
    tables = {"fi_prices": prices, "fi_adj_prices": adj, "fi_universe": uni, "fi_flows": [],
              "fi_consensus": [], "fi_fin_summary": fins}
    return FactorInputs(_D, "morning", "synthetic", tables)


def _rule_off(spec: ModelSpec) -> ModelSpec:
    return replace(spec, params={**spec.params, "adj_jump_missing": False})


def test_scope_spec_turns_on_the_adj_jump_rule_only_for_scope() -> None:
    scope, v3 = registry.get(SCOPE), registry.get(V3)
    assert scope.params["adj_jump_missing"] is True
    assert set(scope.params) ^ set(v3.params) == {"adj_jump_missing"}


def test_scope_nulls_exactly_the_windows_crossing_a_limit_breaking_unresolved_event() -> None:
    """GH1-c 반사실 — 같은 입력으로 규칙 있음·없음 두 번. 새로 NULL 이 된 칸 = 창이 adj_jump_ok
    계단을 넘는 칸 그대로, 그 밖의 원값은 하나도 바뀌지 않는다(점수는 정규화로만 움직인다).
    미해결이지만 제한폭 안(LIM)·창 밖 점프(OLD)·사건 행에서 시작하는 창(J21 r1m·std_20d)은 그대로."""
    fi = _jump_fi()
    res = ENGINE.run(registry.get(SCOPE), fi)
    on = _by_code(res.scores)
    cf = _by_code(ENGINE.run(_rule_off(registry.get(SCOPE)), fi).scores)
    assert on.keys() == cf.keys() == set(_STEPS)
    assert all(cf[c][k] is not None for c in cf for k in (*_MOM, "qual_std_20d"))   # 반사실은 다 선다
    newly_null = {(c, k) for c in on for k in _RAW if on[c][k] is None and cf[c][k] is not None}
    want = {("J5", k) for k in (*_MOM, "qual_std_20d")} | {("J21", k) for k in _MOM[1:]}
    assert newly_null == want
    for c in on:
        for k in _RAW:
            if (c, k) not in want:
                assert on[c][k] == cf[c][k], (c, k)
    assert on["J5"]["momentum_score"] != cf["J5"]["momentum_score"]    # 점수는 움직인다
    # 판 메타(GH1-c ④) — 종목 수와 지표별 칸 수. 규칙이 꺼진 spec 은 메타를 싣지 않는다
    assert res.meta == {"adj_jump_masked": {"n_tickers": 2, "r1m": 1, "r3m": 2, "r6m": 2,
                                            "r9m": 2, "r12m": 2, "std_20d": 1}}
    assert ENGINE.run(_rule_off(registry.get(SCOPE)), fi).meta == {}


def test_v3_zscore_ignores_adj_jump_ok_regression_guard() -> None:
    """v3_zscore@1.0(원본 대조용)은 표식을 읽지 않는다 — 원값이 규칙 없는 scope 와 같다."""
    fi = _jump_fi()
    v3_res = ENGINE.run(registry.get(V3), fi)
    v3 = _by_code(v3_res.scores)
    cf = _by_code(ENGINE.run(_rule_off(registry.get(SCOPE)), fi).scores)
    assert all(v3[c][k] == cf[c][k] for c in cf for k in _RAW)
    assert v3_res.meta == {}


def test_jump_masked_stock_follows_the_existing_missing_rules() -> None:
    """J5 는 모멘텀 지표가 전부 결측 → 원본 규칙대로 모멘텀 원점수 0.0 으로 2단계 정규화에 들어가
    모멘텀 점수가 남는다(`momentum.py:22-28`). 퀄리티는 std_20d 만 빠지고 남은 하위 지표로 비례
    재정규화한다. 종합점수·순위도 그대로 매긴다(v3 에는 제외 개념이 없다)."""
    on = _by_code(ENGINE.run(registry.get(SCOPE), _jump_fi()).scores)
    j5 = on["J5"]
    assert [j5[k] for k in (*_MOM, "qual_std_20d")] == [None] * 6
    assert j5["momentum_score"] is not None and j5["quality_score"] is not None
    assert j5["rank"] is not None
    assert sorted(r["rank"] for r in on.values()) == list(range(1, len(on) + 1))


@pytest.mark.parametrize("bad", [{"adj_jump_missing": 1},       # bool 만 받는다
                                 {"adj_jump_mising": True}])    # 오타 — 조용히 꺼지면 안 된다
def test_adj_jump_param_is_validated(bad: dict[str, object]) -> None:
    spec = registry.get(SCOPE)
    params = {k: v for k, v in spec.params.items() if k != "adj_jump_missing"} | bad
    with pytest.raises(ValueError, match="params"):
        ENGINE.run(replace(spec, params=params), _jump_fi())


# ── (f) 주 모델 ────────────────────────────────────────────────────────────────
def test_scope_is_the_default_primary_model() -> None:
    assert PRIMARY_DEFAULT == SCOPE
