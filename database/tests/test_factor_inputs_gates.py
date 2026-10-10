"""factor_inputs 게이트 FG0~FG4 · FG-fresh — 손으로 만든 `g_<표>` 위에서 판정만 본다.

빌드 왕복(`test_factor_inputs.py`)은 통과 경로를 보고, 여기서는 **각 게이트가 무엇에 FAIL 하는지**를
한 가지씩 망가뜨려 확인한다. 기준 상태 = 계약 스키마의 빈 표 7개 + eligible 종목 1개(가격·cur
컨센서스·연간 재무 보유)라 `run_all` 이 전부 통과한다.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import replace

import duckdb
import pytest
from factor_inputs import gates
from model.contracts import FI_TABLES, UniverseRule
from stage.gates import GateResult, GateStatus

D = "2026-09-28"
PRICE_FROM, FLOW_FROM = "2025-03-27", "2026-06-30"


def _insert(con: duckdb.DuckDBPyConnection, table: str, **values: object) -> None:
    cols = FI_TABLES[table].column_names
    extra = set(values) - set(cols)
    assert not extra, extra
    row = [values.get(c) for c in cols]
    con.execute(f"INSERT INTO g_{table} VALUES ({', '.join('?' * len(cols))})", row)


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect()
    for name, t in FI_TABLES.items():
        cols = ", ".join(f'"{col.name}" {col.dtype}' for col in t.columns)
        c.execute(f"CREATE TABLE g_{name} ({cols})")
    c.execute("CREATE TABLE price_daily (ticker VARCHAR, date DATE, basis VARCHAR, "
              "mktcap_krw DECIMAL(18, 0))")
    d = dt.date.fromisoformat(D)
    _insert(c, "fi_universe", ticker="000001", date=d, market="KOSPI", sec_type="common",
            shares=1_000_000, market_cap=100.0, mktcap_basis="krx", has_estimates=True,
            coverage_state="fresh", coverage_age_days=0, eligible=True)
    _insert(c, "fi_prices", ticker="000001", date=d, close=10_000, price_source="krx")
    _insert(c, "fi_consensus", ticker="000001", target_period="2026/12", horizon="cur", op=1.0)
    _insert(c, "fi_fin_summary", ticker="000001", period="2025/12", period_type="annual",
            per=10.0, available_date=d)
    c.execute(f"INSERT INTO price_daily VALUES ('000001', DATE '{D}', 'krx', 10000000000)")
    yield c
    c.close()


def _ctx(con: duckdb.DuckDBPyConnection, **over: object) -> gates.GateContext:
    base = gates.GateContext(con=con, date=D, basis="morning", price_from=PRICE_FROM,
                             flow_from=FLOW_FROM, rule=UniverseRule(), min_eligible=1,
                             dstar=D, collection_lag_sessions=0)
    return replace(base, **over)


def _by_name(results: list[GateResult]) -> dict[str, GateResult]:
    return {g.name: g for g in results}


def test_baseline_passes_every_gate(con) -> None:
    got = {g.name: g.status for g in gates.run_all(_ctx(con))}
    assert got == {"FG0": GateStatus.PASS, "FG1": GateStatus.PASS, "FG2": GateStatus.PASS,
                   "FG3": GateStatus.PASS, "FG4": GateStatus.SKIP, "FG-fresh": GateStatus.PASS}


def test_fg0_dtype_drift_fails_and_skips_the_rest(con) -> None:
    con.execute("ALTER TABLE g_fi_prices ALTER close TYPE DOUBLE")
    res = _by_name(gates.run_all(_ctx(con)))
    assert res["FG0"].status is GateStatus.FAIL and "fi_prices" in res["FG0"].detail
    assert all(res[n].status is GateStatus.SKIP for n in gates.GATE_ORDER[1:])


def test_fg0_column_order_matters(con) -> None:
    con.execute("CREATE OR REPLACE TABLE g_fi_credit AS SELECT date, ticker, credit_balance, "
                "credit_ratio, available_date FROM g_fi_credit")
    assert gates.fg0_schema(_ctx(con)).status is GateStatus.FAIL


@pytest.mark.parametrize("breaker, key", [
    ("INSERT INTO g_fi_flows (ticker, date) VALUES ('999999', DATE '2026-09-28')",
     "fi_flows.ticker_outside_universe"),
    ("DELETE FROM g_fi_prices", "eligible_without_price_on_d"),
    ("DELETE FROM g_fi_consensus", "eligible_without_cur_consensus"),
    ("INSERT INTO g_fi_flows (ticker, date) VALUES ('000001', DATE '2026-06-29')",
     "fi_flows.date_outside_window"),
    ("INSERT INTO g_fi_prices (ticker, date) VALUES ('000001', DATE '2025-03-26')",
     "fi_prices.date_outside_window"),
    ("INSERT INTO g_fi_credit (ticker, date, available_date) VALUES "
     "('000001', DATE '2026-09-25', DATE '2026-09-29')", "fi_credit.available_after_d"),
    ("INSERT INTO g_fi_fin_summary (ticker, period, period_type) VALUES "
     "('000001', '2024/12', 'annual'), ('000001', '2023/12', 'annual')",
     "fi_fin_summary.annual_over_2"),
    ("UPDATE g_fi_fin_summary SET per = NULL", "eligible_wise_fin_ratio_below_min"),
    ("UPDATE g_fi_consensus SET horizon = '1y'", "fi_consensus.horizon_outside_vocab"),
    ("INSERT INTO g_fi_universe (ticker, date) VALUES ('000001', DATE '2026-09-28')",
     "fi_universe.duplicate_ticker"),
    ("INSERT INTO g_fi_consensus_annual (ticker, period, data_type) VALUES "
     "('000001', '2024/12', 'A')", "fi_consensus_annual.outside_window_or_vocab"),
    ("INSERT INTO g_fi_consensus_annual (ticker, period, data_type) VALUES "
     "('000001', '2026/12', 'estimate')", "fi_consensus_annual.outside_window_or_vocab"),
])
def test_fg1_fails_on_each_breach(con, breaker: str, key: str) -> None:
    con.execute(breaker)
    r = gates.fg1_rows(_ctx(con))
    assert r.status is GateStatus.FAIL and r.metrics[key], (key, r.detail)


def test_fg1_wise_income_statement_ratio_is_record_only(con, monkeypatch) -> None:
    """배포 묶음 7 D7-8: eligible 중 연간 손익(op 또는 ni — WISE cF3002) 비율.
    per·eps(cF4002)만 있고 손익이 비면 per·eps 비율은 통과한다 — 그 결함 부류를 이 비율이
    잡는다. 지금은 기록형이고 `FIN_IS_COVERAGE_ENFORCED` 를 켜면 같은 하한
    (FIN_COVERAGE_MIN)으로 FAIL."""
    r = gates.fg1_rows(_ctx(con))
    assert r.status is GateStatus.PASS and not gates.FIN_IS_COVERAGE_ENFORCED
    assert (r.metrics["n_eligible_with_wise_fin"], r.metrics["n_eligible_with_wise_is"]) == (1, 0)
    assert r.metrics["eligible_wise_is_ratio"] == 0.0
    assert r.metrics["fin_is_coverage_enforced"] is False
    assert "eligible_wise_is_ratio_below_min" not in r.metrics
    monkeypatch.setattr(gates, "FIN_IS_COVERAGE_ENFORCED", True)
    r = gates.fg1_rows(_ctx(con))
    assert r.status is GateStatus.FAIL and r.detail == "eligible_wise_is_ratio_below_min=1"
    con.execute("UPDATE g_fi_fin_summary SET ni = 1.0")           # 손익 한 열이면 충분하다
    r = gates.fg1_rows(_ctx(con))
    assert r.status is GateStatus.PASS and r.metrics["eligible_wise_is_ratio"] == 1.0


def test_fg1_min_eligible(con) -> None:
    r = gates.fg1_rows(_ctx(con, min_eligible=2))
    assert r.status is GateStatus.FAIL and r.metrics["eligible_below_min"] == 1
    assert r.metrics["n_eligible"] == 1


def test_fg2_non_krx_rows_fail(con) -> None:
    con.execute("UPDATE g_fi_prices SET price_source = 'postclose'")
    assert gates.fg2_overlay(_ctx(con)).status is GateStatus.FAIL


def test_fg3_market_cap_rule_is_rounded_eok(con) -> None:
    # 1,000,000주 × 10,000원 = 100억 — 반올림 규칙값과 1e-6 넘게 다르면 FAIL
    con.execute("UPDATE g_fi_universe SET market_cap = 100.5")
    r = gates.fg3_mktcap(_ctx(con))
    assert r.status is GateStatus.FAIL and r.metrics["market_cap_rule"] == 1


def test_fg3_krx_mktcap_difference_is_record_only(con) -> None:
    con.execute("UPDATE price_daily SET mktcap_krw = 20000000000")
    r = gates.fg3_mktcap(_ctx(con))
    assert r.status is GateStatus.PASS and r.metrics["n_krx_mktcap_diff"] == 1


def test_fg3_missing_market_cap_fails(con) -> None:
    con.execute("UPDATE g_fi_universe SET market_cap = NULL")
    assert gates.fg3_mktcap(_ctx(con)).metrics["market_cap_missing"] == 1


def _fx(**over: object) -> dict[str, object]:
    fx: dict[str, object] = {"case": "t", "table": "fi_prices",
                             "key": {"ticker": "000001", "date": D}, "column": "close",
                             "expect": 10000}
    fx.update(over)
    return fx


def test_fg4_golden_pass_mismatch_and_window(con) -> None:
    assert gates.fg4_golden(_ctx(con, golden=[_fx()])).status is GateStatus.PASS
    bad = gates.fg4_golden(_ctx(con, golden=[_fx(expect=10001)]))
    assert bad.status is GateStatus.FAIL and bad.metrics["n_mismatch"] == 1
    missing = gates.fg4_golden(_ctx(con, golden=[_fx(key={"ticker": "000001",
                                                          "date": "2026-09-25"})]))
    assert missing.status is GateStatus.FAIL          # 창 안인데 행이 없다
    out = gates.fg4_golden(_ctx(con, golden=[_fx(key={"ticker": "000001",
                                                      "date": "2025-01-02"})]))
    assert out.status is GateStatus.SKIP and out.metrics["n_out_of_window"] == 1
    other = gates.fg4_golden(_ctx(con, golden=[_fx(key={"ticker": "005930", "date": D})]))
    assert other.status is GateStatus.SKIP            # 유니버스 밖 종목은 세지 않는다
    assert gates.fg4_golden(_ctx(con)).status is GateStatus.SKIP


def test_fg_fresh_records_counts(con) -> None:
    _insert(con, "fi_universe", ticker="000002", date=dt.date.fromisoformat(D),
            coverage_state="lapsed", coverage_age_days=6, has_estimates=False, eligible=False,
            exclude_reason="estimates_lapsed", mktcap_basis="krx")
    r = gates.fg_fresh(_ctx(con))
    assert r.status is GateStatus.PASS
    assert r.metrics["counts"] == {"fresh": 1, "lapsed": 1}
    assert r.metrics["n_lapsed_dropped"] == 1


@pytest.mark.parametrize("breaker, key", [
    ("UPDATE g_fi_universe SET coverage_state = 'lapsed', coverage_age_days = 6, "
     "has_estimates = false", "eligible_without_estimates"),
    ("UPDATE g_fi_universe SET coverage_state = 'grace', coverage_age_days = 6",
     "grace_age_over_limit"),
    ("UPDATE g_fi_universe SET coverage_age_days = 2", "fresh_age_not_zero"),
    ("UPDATE g_fi_universe SET has_estimates = false", "has_estimates_mismatch"),
])
def test_fg_fresh_fails_on_each_breach(con, breaker: str, key: str) -> None:
    con.execute(breaker)
    r = gates.fg_fresh(_ctx(con))
    assert r.status is GateStatus.FAIL and r.metrics[key], (key, r.detail)


def test_fg_fresh_collection_lag(con) -> None:
    """수집 중단 허용치는 1거래일(N-12) — 유예 G 와 무관하다."""
    assert gates.fg_fresh(_ctx(con, collection_lag_sessions=1)).status is GateStatus.PASS
    r = gates.fg_fresh(_ctx(con, collection_lag_sessions=2))
    assert r.status is GateStatus.FAIL and r.metrics["collection_lag_over_max"] == 1
    assert r.metrics["collection_lag_max"] == gates.COLLECTION_LAG_MAX == 1


# ── 장 마감 판(evening, 컷오버 PR-4 · T-2) ───────────────────────────────────
T = "2026-09-29"            # 장 마감 판의 오늘. 직전 거래일 D' = D


@pytest.fixture
def con_evening(con) -> duckdb.DuckDBPyConnection:
    """장 마감 판 기준 상태 — D' 행은 KRX(종가 9,000), T 행은 장 마감 원천(종가 12,000),
    시총 = D' 주식수 1,000,000 × T 종가 / 1e8 = 120억, 기준 't1_shares_x_t_close'.
    빌드가 남기는 임시 표 `_t_src`(장 마감 stage T 행)·`_t_pending`(T-6 보류)도 같은 모양으로 둔다."""
    con.execute(f"UPDATE g_fi_universe SET date = DATE '{T}', market_cap = 120.0, "
                "mktcap_basis = 't1_shares_x_t_close'")
    con.execute("UPDATE g_fi_prices SET close = 9000")
    _insert(con, "fi_prices", ticker="000001", date=dt.date.fromisoformat(T), close=12_000,
            price_source="postclose")
    con.execute("CREATE TABLE _t_src (ticker VARCHAR, price_valid BOOLEAN)")
    con.execute("INSERT INTO _t_src VALUES ('000001', true)")
    con.execute("CREATE TABLE _t_pending (ticker VARCHAR, kind VARCHAR)")
    return con


def _ectx(con: duckdb.DuckDBPyConnection, **over: object) -> gates.GateContext:
    over.setdefault("t_candidates", ("000001",))
    over.setdefault("t_candidates_from", "m_20260928T233000Z")
    return _ctx(con, date=T, basis="evening", asof=D, **over)


def test_evening_baseline_passes_every_gate(con_evening) -> None:
    res = _by_name(gates.run_all(_ectx(con_evening)))
    assert {n: g.status for n, g in res.items()} == {
        "FG0": GateStatus.PASS, "FG1": GateStatus.PASS, "FG2": GateStatus.PASS,
        "FG3": GateStatus.PASS, "FG4": GateStatus.SKIP, "FG-fresh": GateStatus.PASS,
        "FG5": GateStatus.PASS}
    assert res["FG2"].metrics["n_t_price_rows"] == 1
    assert (res["FG5"].metrics["n_candidates"], res["FG5"].metrics["n_missing"]) == (1, 0)
    # KRX 시총 대조는 장 마감 판에 대상이 없다(KRX 의 T 시총은 아직 없다) — 0 이 아니라 NULL
    assert res["FG3"].metrics["n_krx_mktcap_diff"] is None
    assert res["FG-fresh"].metrics["collection_expected_date"] == D


@pytest.mark.parametrize("breaker, gate, key", [
    (f"UPDATE g_fi_prices SET price_source = 'krx' WHERE date = DATE '{T}'",
     "FG2", "fi_prices.t_row_not_overlay"),
    (f"UPDATE g_fi_prices SET price_source = 'postclose' WHERE date < DATE '{T}'",
     "FG2", "fi_prices.non_krx_before_t"),
    ("UPDATE g_fi_universe SET mktcap_basis = 'krx'", "FG2", "fi_universe.mktcap_basis_not_t1"),
    ("UPDATE g_fi_universe SET mktcap_basis = 'krx'", "FG3", "non_t1_basis"),
    ("UPDATE g_fi_universe SET market_cap = 90.0", "FG3", "market_cap_rule"),   # D' 종가로 잰 시총
    (f"DELETE FROM g_fi_prices WHERE date = DATE '{T}'", "FG1", "eligible_without_price_on_d"),
    (f"UPDATE g_fi_fin_summary SET available_date = DATE '{T}'",
     "FG1", "fi_fin_summary.available_after_d"),
    ("INSERT INTO g_fi_consensus_annual (ticker, period, data_type, fetched_date) VALUES "
     f"('000001', '2026/12', 'E', DATE '{T}')",
     "FG1", "fi_consensus_annual.outside_window_or_vocab"),
])
def test_evening_gates_fail_on_each_breach(con_evening, breaker: str, gate: str,
                                           key: str) -> None:
    """장 마감 판 FG2(T 전 행 KRX · T 행 장 마감 원천 · 시총 기준) · FG3(D' 주식수 × T 종가) ·
    FG1(eligible 은 T 가격 · 재무·연간 컨센서스는 D' 까지 — 정보 시점)."""
    con_evening.execute(breaker)
    r = _by_name(gates.run_all(_ectx(con_evening)))[gate]
    assert r.status is GateStatus.FAIL and r.metrics[key], (key, r.detail)


def test_morning_fg2_refuses_the_evening_market_cap_basis(con) -> None:
    con.execute("UPDATE g_fi_universe SET mktcap_basis = 't1_shares_x_t_close'")
    r = gates.fg2_overlay(_ctx(con))
    assert r.status is GateStatus.FAIL and r.metrics["fi_universe.non_krx_mktcap"] == 1


def test_fg_fresh_without_collection_is_skip(con) -> None:
    con.execute("UPDATE g_fi_universe SET coverage_state = 'none', coverage_age_days = NULL, "
                "has_estimates = false, eligible = false, exclude_reason = 'estimates_none'")
    r = gates.fg_fresh(_ctx(con, dstar=None, collection_lag_sessions=None))
    assert r.status is GateStatus.SKIP


# ── FG5 후보 커버리지(장 마감 판, 컷오버 PR-5 · N-42 Q4) ───────────────────────────
def _candidates(con: duckdb.DuckDBPyConnection, n: int) -> tuple[str, ...]:
    """후보 n 종목(000001 + 000002…) — 전부 유니버스·T 가격·장 마감 원천 행이 있다."""
    t = dt.date.fromisoformat(T)
    for i in range(2, n + 1):
        code = f"{i:06d}"
        _insert(con, "fi_universe", ticker=code, date=t, market="KOSPI", sec_type="common",
                mktcap_basis="t1_shares_x_t_close", has_estimates=True,
                coverage_state="fresh", coverage_age_days=0, eligible=False,
                exclude_reason="estimates_none")
        _insert(con, "fi_prices", ticker=code, date=t, close=1_000, price_source="postclose")
        con.execute(f"INSERT INTO _t_src VALUES ('{code}', true)")
    return tuple(f"{i:06d}" for i in range(1, n + 1))


def _drop_t_price(con: duckdb.DuckDBPyConnection, code: str, *, row: bool) -> None:
    """T 가격을 없앤다 — row=True 면 원장 행째 없음, False 면 price_valid 거짓(16:00 뒤 응답)."""
    con.execute(f"DELETE FROM g_fi_prices WHERE ticker = '{code}' AND date = DATE '{T}'")
    if row:
        con.execute(f"DELETE FROM _t_src WHERE ticker = '{code}'")
    else:
        con.execute(f"UPDATE _t_src SET price_valid = false WHERE ticker = '{code}'")


@pytest.mark.parametrize(("n_missing", "passed"), [(0, True), (1, True), (2, False)])
def test_fg5_missing_ratio_boundary_is_inclusive(con_evening, n_missing: int,
                                                 passed: bool) -> None:
    """후보 50 중 T 가격 없음 1 = 0.02 = 상한 → 통과, 2 = 0.04 → FAIL(N-42 Q4 '상한 넘으면')."""
    cands = _candidates(con_evening, 50)
    for code in cands[-n_missing:] if n_missing else ():
        _drop_t_price(con_evening, code, row=True)
    r = gates.fg5_t_coverage(_ectx(con_evening, t_candidates=cands))
    assert r.metrics["missing_max"] == gates.T_CANDIDATE_MISSING_MAX == 0.02
    assert r.metrics["n_missing"] == n_missing and r.metrics["n_candidates"] == 50
    assert (r.status is GateStatus.PASS) is passed, r.detail
    if not passed:
        assert r.detail.startswith("t_price_missing_over_max")


def test_fg5_counts_no_row_and_invalid_price_but_not_corp_action_pending(con_evening) -> None:
    """'T 가격 없음' = 원장 행 없음(no_row) + price_valid 아님(price_invalid). T-6 보류(corp_action_pending)
    는 T 행이 있으므로 세지 않고 기록만 한다 — 수집 결손이 아니라 사건이다."""
    cands = _candidates(con_evening, 4)
    _drop_t_price(con_evening, "000002", row=True)
    _drop_t_price(con_evening, "000003", row=False)
    con_evening.execute("UPDATE g_fi_universe SET exclude_reason = 'corp_action_pending' "
                        "WHERE ticker = '000004'")
    con_evening.execute("INSERT INTO _t_pending VALUES ('000004', 'base_price')")
    r = gates.fg5_t_coverage(_ectx(con_evening, t_candidates=cands))
    assert r.status is GateStatus.FAIL
    assert (r.metrics["n_missing"], r.metrics["n_no_row"], r.metrics["n_price_invalid"],
            r.metrics["n_candidates_corp_action_pending"]) == (2, 1, 1, 1)
    assert r.metrics["missing_tickers"] == ["000002", "000003"]
    assert r.metrics["corp_action_pending"] == {"base_price": 1}
    assert r.metrics["candidates_from"] == "m_20260928T233000Z"


def test_fg5_without_candidates_fails(con_evening) -> None:
    """직전 판 모델 후보를 못 읽으면(None) 커버리지를 잴 수 없다 — FAIL(SKIP 이 아니다)."""
    r = gates.fg5_t_coverage(_ectx(con_evening, t_candidates=None,
                                   t_candidates_from="InputUnavailable: 없음"))
    assert r.status is GateStatus.FAIL
    assert r.detail.startswith("candidates_unavailable") and "없음" in r.detail


def test_fg5_runs_only_on_the_evening_board(con, con_evening) -> None:
    """아침판 판 기록에는 FG5 가 없다(GATE_ORDER 그대로). 장 마감 판은 FG0 실패 때도 FG5 를
    upstream_failed 로 적는다."""
    assert [g.name for g in gates.run_all(_ctx(con))] == list(gates.GATE_ORDER)
    assert "FG5" not in gates.GATE_ORDER
    con_evening.execute("ALTER TABLE g_fi_prices ALTER close TYPE DOUBLE")
    res = gates.run_all(_ectx(con_evening))
    assert [g.name for g in res] == [*gates.GATE_ORDER, "FG5"]
    assert res[-1].status is GateStatus.SKIP and res[-1].detail.startswith("upstream_failed")
