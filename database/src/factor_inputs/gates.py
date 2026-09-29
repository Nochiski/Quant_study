"""factor_inputs 판 게이트 FG0~FG4 · FG-fresh (플랜 `2026-09-24-v3-merge.md` M2 W1-b · T2.11).

`build.py` 가 판을 임시 경로에 parquet 으로 쓴 뒤, 그 파일 위 뷰(`g_<표>`)로 여기 게이트를 돈다.
**FAIL 이 하나라도 있으면 판을 올리지 않는다**(MANIFEST 포인터를 바꾸지 않는다 — equity 규약).
결과 타입은 stage/equity 와 같은 `stage.gates.GateResult` 다.

  FG0 스키마   — 8표의 열 이름·순서·타입이 계약(`model.contracts.FI_TABLES`)과 같다. FAIL 이면
                 뒤 게이트는 `skip(upstream_failed)`.
  FG1 행수     — 유니버스 × 창: 모든 표의 종목 ⊆ fi_universe · eligible 은 D 가격·cur 컨센서스가
                 있다 · eligible 수 ≥ 하한 · 날짜가 창 안 · 연간 ≤ 2기 · 분기 ≤ 5기 ·
                 eligible 중 WISE 연간 재무가 있는 비율 ≥ 하한(원천 누락이 조용히 지나가지 않게).
  FG2 T 행 출처 — 아침판: fi_prices.price_source · fi_universe.mktcap_basis 가 전부 'krx'.
  FG3 시총     — market_cap = round(shares × close(D) / 1e8) 정수 억원(상대 1e-6 — compat
                 `stocks.market_cap` 과 같은 반올림, 오케스트레이터 09-29). KRX 시총 대조는 기록형.
  FG4 골든     — `fixtures/golden.json` 의 손계산 값과 정확히 같다. 창 밖·유니버스 밖 항목은 세지
                 않고, 셀 수 있는 항목이 0 이면 `skip(no_fixtures)`.
  FG-fresh     — 신선도 상태 수를 기록하고, lapsed·none 은 eligible 이 아니며(require_estimates),
                 grace 나이 ≤ G, 마지막 수집일 D* 가 D 보다 G 거래일 넘게 뒤처지지 않는다.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from model.contracts import FI_TABLES, UniverseRule
from stage.gates import GateResult, GateStatus

from .queries import FIN_ANNUAL_PERIODS, FIN_QUARTERS, FLOW_SESSIONS

GATE_ORDER = ("FG0", "FG1", "FG2", "FG3", "FG4", "FG-fresh")
MKTCAP_REL_TOL = 1e-6
# eligible 중 WISE 연간 재무(per·eps 중 하나라도)가 있는 종목 비율 하한. 09-23 실측 결측 6/621
# (≈1%, 전부 DQ-7 lapsed) — 이 선 아래면 수집·파싱 쪽이 통째로 빠진 것이다.
FIN_COVERAGE_MIN = 0.9
DATE_KEYED = ("fi_prices", "fi_adj_prices", "fi_flows", "fi_credit")


@dataclass
class GateContext:
    con: duckdb.DuckDBPyConnection
    date: str                               # D (YYYY-MM-DD)
    basis: str
    price_from: str
    flow_from: str
    rule: UniverseRule
    min_eligible: int
    dstar: str | None                       # 마지막 수집일 D*
    collection_lag_sessions: int | None     # (D*, D] 거래일 수
    golden: list[dict[str, object]] = field(default_factory=list)
    view: Callable[[str], str] = lambda t: f"g_{t}"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    v = _scalar(con, sql)
    return 0 if v is None else int(str(v))


def _result(name: str, violations: dict[str, int], metrics: dict[str, object],
            ok_detail: str) -> GateResult:
    bad = {k: v for k, v in violations.items() if v}
    status = GateStatus.FAIL if bad else GateStatus.PASS
    detail = ok_detail if not bad else "; ".join(f"{k}={v}" for k, v in bad.items())
    return GateResult(name, status, detail, {**violations, **metrics})


# ── FG0 ──────────────────────────────────────────────────────────────────────
def fg0_schema(ctx: GateContext) -> GateResult:
    mism: list[str] = []
    for name, t in FI_TABLES.items():
        got = [(str(r[0]), str(r[1])) for r in
               ctx.con.execute(f"DESCRIBE SELECT * FROM {ctx.view(name)}").fetchall()]
        want = [(c.name, c.dtype) for c in t.columns]
        if got != want:
            mism.append(f"{name}: got={got} want={want}")
    if mism:
        return GateResult("FG0", GateStatus.FAIL, " | ".join(mism)[:2000],
                          {"n_tables_mismatch": len(mism)})
    return GateResult("FG0", GateStatus.PASS, "8표 열·타입이 계약과 같다",
                      {"n_tables_mismatch": 0})


# ── FG1 ──────────────────────────────────────────────────────────────────────
def fg1_rows(ctx: GateContext) -> GateResult:
    v = ctx.view
    con = ctx.con
    uni = v("fi_universe")
    n_rows = {t: _count(con, f"SELECT count(*) FROM {v(t)}") for t in FI_TABLES}
    viol: dict[str, int] = {}
    for t in FI_TABLES:
        if t == "fi_universe":
            continue
        viol[f"{t}.ticker_outside_universe"] = _count(
            con, f"SELECT count(DISTINCT ticker) FROM {v(t)} WHERE ticker NOT IN "
                 f"(SELECT ticker FROM {uni})")
    viol["fi_universe.duplicate_ticker"] = _count(
        con, f"SELECT count(*) - count(DISTINCT ticker) FROM {uni}")
    viol["fi_universe.date_not_d"] = _count(
        con, f"SELECT count(*) FROM {uni} WHERE date <> DATE '{ctx.date}'")
    viol["eligible_without_price_on_d"] = _count(
        con, f"SELECT count(*) FROM {uni} u WHERE u.eligible AND NOT EXISTS ("
             f"SELECT 1 FROM {v('fi_prices')} p WHERE p.ticker = u.ticker "
             f"AND p.date = DATE '{ctx.date}' AND p.close IS NOT NULL)")
    if ctx.rule.require_estimates:
        viol["eligible_without_cur_consensus"] = _count(
            con, f"SELECT count(*) FROM {uni} u WHERE u.eligible AND NOT EXISTS ("
                 f"SELECT 1 FROM {v('fi_consensus')} c WHERE c.ticker = u.ticker "
                 f"AND c.horizon = 'cur')")
    n_eligible = _count(con, f"SELECT count(*) FROM {uni} WHERE eligible")
    viol["eligible_below_min"] = int(n_eligible < ctx.min_eligible)
    for t in ("fi_prices", "fi_adj_prices"):
        viol[f"{t}.date_outside_window"] = _count(
            con, f"SELECT count(*) FROM {v(t)} WHERE date < DATE '{ctx.price_from}' "
                 f"OR date > DATE '{ctx.date}'")
    for t in ("fi_flows", "fi_credit"):
        viol[f"{t}.date_outside_window"] = _count(
            con, f"SELECT count(*) FROM {v(t)} WHERE date < DATE '{ctx.flow_from}' "
                 f"OR date > DATE '{ctx.date}'")
        viol[f"{t}.sessions_over_{FLOW_SESSIONS}"] = int(_count(
            con, f"SELECT count(DISTINCT date) FROM {v(t)}") > FLOW_SESSIONS)
    viol["fi_credit.available_after_d"] = _count(
        con, f"SELECT count(*) FROM {v('fi_credit')} WHERE available_date > DATE '{ctx.date}'")
    fin = v("fi_fin_summary")
    viol[f"fi_fin_summary.annual_over_{FIN_ANNUAL_PERIODS}"] = _count(
        con, f"SELECT count(*) FROM (SELECT ticker FROM {fin} WHERE period_type = 'annual' "
             f"GROUP BY ticker HAVING count(*) > {FIN_ANNUAL_PERIODS})")
    viol[f"fi_fin_summary.quarter_over_{FIN_QUARTERS}"] = _count(
        con, f"SELECT count(*) FROM (SELECT ticker FROM {fin} WHERE period_type = 'quarter' "
             f"GROUP BY ticker HAVING count(*) > {FIN_QUARTERS})")
    viol["fi_fin_summary.available_after_d"] = _count(
        con, f"SELECT count(*) FROM {fin} WHERE available_date > DATE '{ctx.date}'")
    viol["fi_consensus.horizon_outside_vocab"] = _count(
        con, f"SELECT count(*) FROM {v('fi_consensus')} "
             "WHERE horizon NOT IN ('cur', '1w', '1m', '3m') OR horizon IS NULL")
    y = int(ctx.date[:4])
    ca_periods = ", ".join(f"'{yy}/12'" for yy in (y - 1, y, y + 1))
    viol["fi_consensus_annual.outside_window_or_vocab"] = _count(
        con, f"SELECT count(*) FROM {v('fi_consensus_annual')} WHERE period NOT IN "
             f"({ca_periods}) OR data_type NOT IN ('E', 'A') OR data_type IS NULL "
             f"OR fetched_date > DATE '{ctx.date}'")
    n_fin = _count(
        con, f"SELECT count(*) FROM {uni} u WHERE u.eligible AND EXISTS ("
             f"SELECT 1 FROM {fin} f WHERE f.ticker = u.ticker AND f.period_type = 'annual' "
             "AND (f.per IS NOT NULL OR f.eps IS NOT NULL))")
    fin_ratio = (n_fin / n_eligible) if n_eligible else None
    if ctx.rule.require_estimates and n_eligible:
        viol["eligible_wise_fin_ratio_below_min"] = int(
            fin_ratio is not None and fin_ratio < FIN_COVERAGE_MIN)
    metrics: dict[str, object] = {"n_rows": n_rows, "n_eligible": n_eligible,
                                  "min_eligible": ctx.min_eligible,
                                  "n_eligible_with_wise_fin": n_fin,
                                  "eligible_wise_fin_ratio": fin_ratio,
                                  "fin_coverage_min": FIN_COVERAGE_MIN}
    return _result("FG1", viol, metrics, "유니버스 × 창 정합")


# ── FG2 ──────────────────────────────────────────────────────────────────────
def fg2_overlay(ctx: GateContext) -> GateResult:
    """아침판 — T 행을 포함한 전 행이 KRX 다. 저녁 오버레이(W1-a)가 붙으면 여기서 출처를 가른다."""
    viol = {
        "fi_prices.non_krx": _count(ctx.con, f"SELECT count(*) FROM {ctx.view('fi_prices')} "
                                             "WHERE price_source IS DISTINCT FROM 'krx'"),
        "fi_universe.non_krx_mktcap": _count(
            ctx.con, f"SELECT count(*) FROM {ctx.view('fi_universe')} "
                     "WHERE mktcap_basis IS DISTINCT FROM 'krx'"),
    }
    return _result("FG2", viol, {"basis": ctx.basis}, "아침판 전 행 KRX")


# ── FG3 ──────────────────────────────────────────────────────────────────────
def fg3_mktcap(ctx: GateContext) -> GateResult:
    v = ctx.view
    base = (f"FROM {v('fi_universe')} u LEFT JOIN {v('fi_prices')} p "
            f"ON p.ticker = u.ticker AND p.date = DATE '{ctx.date}'")
    # 기대값을 DECIMAL 곱으로 다시 잰다(DOUBLE 곱은 2^53 을 넘는 시총에서 1원 단위가 흔들린다).
    expect = ("CAST(round(CAST(u.shares AS DECIMAL(38, 0)) * CAST(p.close AS DECIMAL(38, 0)) "
              "/ 100000000.0) AS DOUBLE)")
    viol = {
        "market_cap_rule": _count(
            ctx.con, f"SELECT count(*) {base} WHERE u.market_cap IS NOT NULL AND "
                     f"(p.close IS NULL OR u.shares IS NULL OR abs(u.market_cap - {expect}) > "
                     f"{MKTCAP_REL_TOL} * greatest(abs({expect}), 1e-12))"),
        "market_cap_missing": _count(
            ctx.con, f"SELECT count(*) {base} WHERE u.market_cap IS NULL "
                     "AND u.shares IS NOT NULL AND p.close IS NOT NULL"),
        "non_krx_basis": _count(ctx.con, f"SELECT count(*) FROM {v('fi_universe')} "
                                         "WHERE mktcap_basis <> 'krx'"),
    }
    # 기록형 — KRX 가 준 시총(price_daily.mktcap_krw, compat 가 쓰는 값)을 같은 규칙으로 반올림한
    # 값과
    # 다른 종목 수. 원천 사실이라 FAIL 로 묶지 않지만, 0 이 아니면 compat 과 시총이 갈린 것이다.
    n_krx_diff = _count(
        ctx.con, f"SELECT count(*) FROM {v('fi_universe')} u JOIN price_daily k "
                 f"ON k.ticker = u.ticker AND k.date = DATE '{ctx.date}' AND k.basis = 'krx' "
                 f"WHERE u.market_cap IS NOT NULL AND k.mktcap_krw IS NOT NULL AND "
                 f"CAST(round(k.mktcap_krw / 100000000.0) AS DOUBLE) <> u.market_cap")
    n_checked = _count(ctx.con, f"SELECT count(*) FROM {v('fi_universe')} "
                                "WHERE market_cap IS NOT NULL")
    return _result("FG3", viol, {"n_checked": n_checked, "n_krx_mktcap_diff": n_krx_diff,
                                 "rel_tol": MKTCAP_REL_TOL}, "시총 = round(주식수 × 종가 / 1e8)")


# ── FG4 ──────────────────────────────────────────────────────────────────────
def load_golden(path: Path | None) -> list[dict[str, object]]:
    if path is None or not Path(path).exists():
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"골든 픽스처는 목록이어야 한다: {path}")
    return raw


def _same(got: object, expect: object) -> bool:
    if got is None or expect is None:
        return got is None and expect is None
    if isinstance(expect, bool) or isinstance(got, bool):
        return str(got).lower() == str(expect).lower()
    if isinstance(expect, int | float):
        try:
            g = float(str(got))
        except ValueError:
            return False
        return abs(g - float(expect)) <= 1e-9 * max(1.0, abs(float(expect)))
    return str(got) == str(expect)


def fg4_golden(ctx: GateContext) -> GateResult:
    if not ctx.golden:
        return GateResult("FG4", GateStatus.SKIP, "no_fixtures — 골든 픽스처 없음",
                          {"n_fixtures": 0})
    n_checked = n_out = 0
    bad: list[str] = []
    for fx in ctx.golden:
        table, key, col = str(fx["table"]), fx["key"], str(fx["column"])
        if table not in DATE_KEYED or not isinstance(key, dict):
            raise ValueError(f"골든 항목은 (ticker, date) 표만 받는다: {fx}")
        d = str(key["date"])
        lo = ctx.price_from if table in ("fi_prices", "fi_adj_prices") else ctx.flow_from
        in_uni = _count(ctx.con, f"SELECT count(*) FROM {ctx.view('fi_universe')} "
                                 f"WHERE ticker = '{key['ticker']}'")
        if not (lo <= d <= ctx.date) or not in_uni:
            n_out += 1
            continue
        n_checked += 1
        row = ctx.con.execute(
            f'SELECT "{col}" FROM {ctx.view(table)} WHERE ticker = ? AND date = CAST(? AS DATE)',
            [key["ticker"], d]).fetchone()
        got = None if row is None else row[0]
        if row is None or not _same(got, fx["expect"]):
            bad.append(f"{fx.get('case', '')} {table}{key}.{col}: expected {fx['expect']!r} "
                       f"got {'<no row>' if row is None else repr(got)}")
    metrics: dict[str, object] = {"n_fixtures": len(ctx.golden), "n_checked": n_checked,
                                  "n_out_of_window": n_out, "n_mismatch": len(bad)}
    if n_checked == 0:
        return GateResult("FG4", GateStatus.SKIP,
                          "no_fixtures — 창·유니버스 안의 골든 항목이 없다(갱신 필요)", metrics)
    if bad:
        return GateResult("FG4", GateStatus.FAIL, "; ".join(bad[:10]), metrics)
    return GateResult("FG4", GateStatus.PASS, "골든 픽스처 일치", metrics)


# ── FG-fresh ─────────────────────────────────────────────────────────────────
def fg_fresh(ctx: GateContext) -> GateResult:
    uni = ctx.view("fi_universe")
    rows = ctx.con.execute(
        f"SELECT coverage_state, count(*), sum(CASE WHEN eligible THEN 1 ELSE 0 END) "
        f"FROM {uni} GROUP BY 1").fetchall()
    counts = {str(s): int(n) for s, n, _ in rows}
    counts_eligible = {str(s): int(e or 0) for s, _, e in rows}
    n_lapsed_dropped = _count(ctx.con, f"SELECT count(*) FROM {uni} "
                                       "WHERE exclude_reason = 'estimates_lapsed'")
    g = ctx.rule.coverage_grace_days
    metrics: dict[str, object] = {
        "counts": counts, "counts_eligible": counts_eligible,
        "n_lapsed_dropped": n_lapsed_dropped, "grace_days": g,
        "last_collection_date": ctx.dstar,
        "collection_lag_sessions": ctx.collection_lag_sessions}
    viol = {
        "state_outside_vocab": _count(
            ctx.con, f"SELECT count(*) FROM {uni} WHERE coverage_state NOT IN "
                     "('fresh', 'grace', 'lapsed', 'none') OR coverage_state IS NULL"),
        "has_estimates_mismatch": _count(
            ctx.con, f"SELECT count(*) FROM {uni} WHERE has_estimates IS DISTINCT FROM "
                     "(coverage_state IN ('fresh', 'grace'))"),
        "fresh_age_not_zero": _count(
            ctx.con, f"SELECT count(*) FROM {uni} WHERE coverage_state = 'fresh' "
                     "AND coverage_age_days IS DISTINCT FROM 0"),
        "grace_age_over_limit": _count(
            ctx.con, f"SELECT count(*) FROM {uni} WHERE coverage_state = 'grace' "
                     f"AND (coverage_age_days IS NULL OR coverage_age_days > {g})"),
        "lapsed_age_within_limit": _count(
            ctx.con, f"SELECT count(*) FROM {uni} WHERE coverage_state = 'lapsed' "
                     f"AND (coverage_age_days IS NULL OR coverage_age_days <= {g})"),
    }
    if ctx.rule.require_estimates:
        viol["eligible_without_estimates"] = _count(
            ctx.con, f"SELECT count(*) FROM {uni} WHERE eligible "
                     "AND coverage_state IN ('lapsed', 'none')")
    if ctx.dstar is None:
        # 수집 기록이 없는 날(수집 시작 전) — 유예 판정의 기준 자체가 없다. 빈 유니버스는 FG1 의
        # eligible 하한이 잡으므로 여기서는 판정하지 않는다.
        bad = {k: n for k, n in viol.items() if n}
        if bad:
            return _result("FG-fresh", viol, metrics, "")
        return GateResult("FG-fresh", GateStatus.SKIP,
                          "no_collection — D 이전 추정치 수집 기록이 없다", {**viol, **metrics})
    lag = ctx.collection_lag_sessions or 0
    viol["collection_lag_over_grace"] = int(lag > g)
    return _result("FG-fresh", viol, metrics,
                   f"신선도 기록 · lapsed {n_lapsed_dropped}종목 제외 · D* {ctx.dstar}")


GATES: tuple[Callable[[GateContext], GateResult], ...] = (
    fg0_schema, fg1_rows, fg2_overlay, fg3_mktcap, fg4_golden, fg_fresh)


def run_all(ctx: GateContext) -> list[GateResult]:
    """GATE_ORDER 순서. FG0 이 FAIL 이면 나머지는 `skip(upstream_failed)`."""
    out = [fg0_schema(ctx)]
    if out[0].status is GateStatus.FAIL:
        return out + [GateResult(name, GateStatus.SKIP, "upstream_failed — FG0", {})
                      for name in GATE_ORDER[1:]]
    for gate in GATES[1:]:
        out.append(gate(ctx))
    return out
