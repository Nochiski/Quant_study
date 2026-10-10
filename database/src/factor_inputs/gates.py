"""factor_inputs 판 게이트 FG0~FG4 · FG-fresh · FG5 (플랜 `2026-09-24-v3-merge.md` M2 W1-b · T2.11 ·
컷오버 PR-5).

`build.py` 가 판을 임시 경로에 parquet 으로 쓴 뒤, 그 파일 위 뷰(`g_<표>`)로 여기 게이트를 돈다.
**FAIL 이 하나라도 있으면 판을 올리지 않는다**(MANIFEST 포인터를 바꾸지 않는다 — equity 규약).
결과 타입은 stage/equity 와 같은 `stage.gates.GateResult` 다.

  FG0 스키마   — 8표의 열 이름·순서·타입이 계약(`model.contracts.FI_TABLES`)과 같다. FAIL 이면
                 뒤 게이트는 `skip(upstream_failed)`.
  FG1 행수     — 유니버스 × 창: 모든 표의 종목 ⊆ fi_universe · eligible 은 D 가격·cur 컨센서스가
                 있다 · eligible 수 ≥ 하한 · 날짜가 창 안 · 연간 ≤ 2기 · 분기 ≤ 5기 ·
                 eligible 중 WISE 연간 재무가 있는 비율 ≥ 하한(원천 누락이 조용히 지나가지 않게).
                 WISE 연간 손익(op·ni, cF3002) 비율은 기록형(배포 묶음 7 D7-8 — 폐기형 승격은
                 `FIN_IS_COVERAGE_ENFORCED`).
  FG2 T 행 출처 — 아침판: fi_prices.price_source · fi_universe.mktcap_basis 가 전부 'krx'.
                 장 마감 판(evening): T 전 행 'krx' · T 행 `T_PRICE_SOURCE` · 시총 기준 전부
                 `T_MKTCAP_BASIS`(컷오버 PR-4). T 행 수는 기록만 한다(커버리지는 FG5).
  FG3 시총     — market_cap = round(shares × close(D) / 1e8) 정수 억원(상대 1e-6 — compat
                 `stocks.market_cap` 과 같은 반올림, 오케스트레이터 09-29). KRX 시총 대조는 기록형.
                 장 마감 판은 shares = D' 주식수, close = T 행 종가이고 KRX 대조는 대상이 없다.
  FG4 골든     — `fixtures/golden.json` 의 손계산 값과 정확히 같다. 창 밖·유니버스 밖 항목은 세지
                 않고, 셀 수 있는 항목이 0 이면 `skip(no_fixtures)` — 허용표 밖이라 층 판정은
                 FAIL 이다(K1-7a, `stage/skip_allow.py`).
  FG-fresh     — 신선도 상태 수를 기록하고, lapsed·none 은 eligible 이 아니며(require_estimates),
                 grace 나이 ≤ G, 마지막 수집일 D* 가 예상 수집일(asof — 아침판 D, 장 마감 판 D')
                 보다 COLLECTION_LAG_MAX 거래일 넘게 뒤처지지 않는다(수집 중단 허용치는 유예 G 와
                 따로 둔다).
  FG5 후보 커버리지 — 장 마감 판만(컷오버 PR-5 · N-42 Q4 '당일 행 없는 종목이 상한 넘으면 판 실패').
                 직전 판 모델 후보 중 T 가격이 없는 종목 비율 ≤ `T_CANDIDATE_MISSING_MAX`. 후보를 못
                 읽으면 FAIL. 아침판 판 기록에는 이 게이트가 없다(`GATE_ORDER` 그대로).

정보 시점 `asof`(컷오버 PR-4): 재무·연간 컨센서스의 available/fetched 상한은 asof 로 본다(아침판은
D 그대로). 신용 available_date 는 세션 축이라 D(장 마감 판 T)로 본다.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from model.contracts import FI_TABLES, UniverseRule
from stage import skip_allow
from stage.gates import GateResult, GateStatus

from .queries import (
    FIN_ANNUAL_PERIODS,
    FIN_QUARTERS,
    FLOW_SESSIONS,
    T_MKTCAP_BASIS,
    T_PRICE_SOURCE,
)

GATE_ORDER = ("FG0", "FG1", "FG2", "FG3", "FG4", "FG-fresh")
# 장 마감 판만 도는 게이트(컷오버 PR-5) — GATE_ORDER 뒤에 붙는다
EVENING_GATE_ORDER = (*GATE_ORDER, "FG5")
MKTCAP_REL_TOL = 1e-6
# eligible 중 WISE 연간 재무(per·eps 중 하나라도)가 있는 종목 비율 하한. 09-23 실측 결측 6/621
# (≈1%, 전부 DQ-7 lapsed) — 이 선 아래면 수집·파싱 쪽이 통째로 빠진 것이다.
FIN_COVERAGE_MIN = 0.9
# eligible 중 WISE 연간 손익(op 또는 ni — cF3002)이 있는 종목 비율. per·eps 비율은 cF4002 만 봐서
# cF3002 열만 비는 결함(배포 묶음 7 — 종목 한 날짜로 두 ep 를 고르던 쿼리)을 못 잡는다. 하한은
# FIN_COVERAGE_MIN 을 함께 쓴다. 지금은 기록형 — 10-11 서버 재연 6개 D 에서 비율 ≥ 0.95 를 확인한
# 뒤 True(폐기형)로 올린다(D7-8).
FIN_IS_COVERAGE_ENFORCED = False
# WISE 수집 중단 허용치(거래일). D* 가 D 보다 이만큼 넘게 뒤처지면 FAIL — 2거래일 이상 멈추면 판을
# 올리지 않는다(2026-10-05 사용자 결정 N-12 '1거래일'). 추정치 유예(coverage_grace_days)와 값을
# 공유하지 않는다 — 유예는 종목의 추정치가 사라진 경우, 이것은 수집 자체가 멈춘 경우다.
COLLECTION_LAG_MAX = 1
# 장 마감 판 후보 커버리지 상한(FG5) — 직전 판 모델 후보 중 T 가격이 없는 종목 비율이 이것을 **넘으면**
# 판 실패(N-42 Q4, 정본엔 숫자가 없어 PR-5 가 정했다). 근거:
#   · N-35 ③ 장 마감 프로브 3일(10-06~08): 후보 100종목 15:46 회차 100/100, 한도 초과·오류 0. ka10060
#     종목당 0.32초라 후보(약 600)를 먼저 받으면 약 3.2분 — 15:41 시작이면 16:00 컷오프 전에 끝난다.
#     정상 날의 결측은 0 에 가깝다.
#   · 크기는 저녁 키움 직행 게이트의 ka10060 커버 하한 0.98(`daily.kw_daily.COMMIT_MIN_RATIO` — 요청
#     종목 중 dt=D 를 받은 비율, T-28 이 06:00 보강에도 그대로 쓴다)과 같게 둔다 — 같은 TR 의 종목 단위
#     결측 허용치. 후보 600 이면 12 종목까지 통과한다.
T_CANDIDATE_MISSING_MAX = 0.02
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
    collection_lag_sessions: int | None     # (D*, asof] 거래일 수
    golden: list[dict[str, object]] = field(default_factory=list)
    view: Callable[[str], str] = lambda t: f"g_{t}"
    asof: str = ""                          # 정보 시점 — 비우면 date. 장 마감 판은 D'
    # 장 마감 판 FG5 대상 = 직전 판 모델 후보(`daily.postclose.fi_candidates`). 못 읽었으면 None
    t_candidates: tuple[str, ...] | None = None
    t_candidates_from: str = ""             # 후보를 읽은 fi 판 id, 못 읽었으면 그 사유

    def __post_init__(self) -> None:
        if not self.asof:
            self.asof = self.date


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
        con, f"SELECT count(*) FROM {fin} WHERE available_date > DATE '{ctx.asof}'")
    viol["fi_consensus.horizon_outside_vocab"] = _count(
        con, f"SELECT count(*) FROM {v('fi_consensus')} "
             "WHERE horizon NOT IN ('cur', '1w', '1m', '3m') OR horizon IS NULL")
    y = int(ctx.date[:4])
    ca_periods = ", ".join(f"'{yy}/12'" for yy in (y - 1, y, y + 1))
    viol["fi_consensus_annual.outside_window_or_vocab"] = _count(
        con, f"SELECT count(*) FROM {v('fi_consensus_annual')} WHERE period NOT IN "
             f"({ca_periods}) OR data_type NOT IN ('E', 'A') OR data_type IS NULL "
             f"OR fetched_date > DATE '{ctx.asof}'")
    n_fin = _count(
        con, f"SELECT count(*) FROM {uni} u WHERE u.eligible AND EXISTS ("
             f"SELECT 1 FROM {fin} f WHERE f.ticker = u.ticker AND f.period_type = 'annual' "
             "AND (f.per IS NOT NULL OR f.eps IS NOT NULL))")
    fin_ratio = (n_fin / n_eligible) if n_eligible else None
    if ctx.rule.require_estimates and n_eligible:
        viol["eligible_wise_fin_ratio_below_min"] = int(
            fin_ratio is not None and fin_ratio < FIN_COVERAGE_MIN)
    n_is = _count(
        con, f"SELECT count(*) FROM {uni} u WHERE u.eligible AND EXISTS ("
             f"SELECT 1 FROM {fin} f WHERE f.ticker = u.ticker AND f.period_type = 'annual' "
             "AND (f.op IS NOT NULL OR f.ni IS NOT NULL))")
    is_ratio = (n_is / n_eligible) if n_eligible else None
    if FIN_IS_COVERAGE_ENFORCED and ctx.rule.require_estimates and n_eligible:
        viol["eligible_wise_is_ratio_below_min"] = int(
            is_ratio is not None and is_ratio < FIN_COVERAGE_MIN)
    metrics: dict[str, object] = {"n_rows": n_rows, "n_eligible": n_eligible,
                                  "min_eligible": ctx.min_eligible,
                                  "n_eligible_with_wise_fin": n_fin,
                                  "eligible_wise_fin_ratio": fin_ratio,
                                  "fin_coverage_min": FIN_COVERAGE_MIN,
                                  "n_eligible_with_wise_is": n_is,
                                  "eligible_wise_is_ratio": is_ratio,
                                  "fin_is_coverage_enforced": FIN_IS_COVERAGE_ENFORCED}
    return _result("FG1", viol, metrics, "유니버스 × 창 정합")


# ── FG2 ──────────────────────────────────────────────────────────────────────
def fg2_overlay(ctx: GateContext) -> GateResult:
    """아침판 — T 행을 포함한 전 행이 KRX 다. 장 마감 판(evening) — T 전 행은 연구 판 그대로 KRX,
    T 행은 장 마감 원천(`T_PRICE_SOURCE`), 시총 기준은 전 종목 `T_MKTCAP_BASIS`(D' 주식수 × T
    종가). T 행 수(`n_t_price_rows`)는 기록만 한다 — 얹기·커버리지 판정은 PR-5."""
    if ctx.basis == "evening":
        prices, uni = ctx.view("fi_prices"), ctx.view("fi_universe")
        on_t = f"date = DATE '{ctx.date}'"
        ev = {
            "fi_prices.non_krx_before_t": _count(
                ctx.con, f"SELECT count(*) FROM {prices} WHERE date < DATE '{ctx.date}' "
                         "AND price_source IS DISTINCT FROM 'krx'"),
            "fi_prices.t_row_not_overlay": _count(
                ctx.con, f"SELECT count(*) FROM {prices} WHERE {on_t} "
                         f"AND price_source IS DISTINCT FROM '{T_PRICE_SOURCE}'"),
            "fi_universe.mktcap_basis_not_t1": _count(
                ctx.con, f"SELECT count(*) FROM {uni} "
                         f"WHERE mktcap_basis IS DISTINCT FROM '{T_MKTCAP_BASIS}'"),
        }
        n_t = _count(ctx.con, f"SELECT count(*) FROM {prices} WHERE {on_t}")
        return _result("FG2", ev, {"basis": ctx.basis, "n_t_price_rows": n_t},
                       "장 마감 판 — T 전 행 KRX · T 행 장 마감 원천 · 시총 D' 주식수 × T 종가")
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
    """시총 규칙. 장 마감 판은 shares = D' 주식수(universe 이월), close = fi_prices 의 T 행 종가라
    같은 식이 'D' 주식수 × T 종가' 를 잰다. 시총 기준 열은 basis 에 맞는 값이어야 한다."""
    v = ctx.view
    evening = ctx.basis == "evening"
    want_basis = T_MKTCAP_BASIS if evening else "krx"
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
        ("non_t1_basis" if evening else "non_krx_basis"): _count(
            ctx.con, f"SELECT count(*) FROM {v('fi_universe')} "
                     f"WHERE mktcap_basis <> '{want_basis}'"),
    }
    # 기록형 — KRX 가 준 시총(price_daily.mktcap_krw, compat 가 쓰는 값)을 같은 규칙으로 반올림한
    # 값과
    # 다른 종목 수. 원천 사실이라 FAIL 로 묶지 않지만, 0 이 아니면 compat 과 시총이 갈린 것이다.
    # 장 마감 판은 KRX 의 T 시총이 아직 없어 대조 대상이 없다 — 0(일치)으로 보이지 않게 NULL.
    n_krx_diff = None if evening else _count(
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
        "collection_lag_sessions": ctx.collection_lag_sessions,
        "collection_expected_date": ctx.asof,
        "collection_lag_max": COLLECTION_LAG_MAX}
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
    viol["collection_lag_over_max"] = int(lag > COLLECTION_LAG_MAX)
    return _result("FG-fresh", viol, metrics,
                   f"신선도 기록 · lapsed {n_lapsed_dropped}종목 제외 · D* {ctx.dstar}")


# ── FG5 ──────────────────────────────────────────────────────────────────────
def fg5_t_coverage(ctx: GateContext) -> GateResult:
    """장 마감 판 후보 커버리지(N-42 Q4 · 컷오버 PR-5).

    대상 = 직전 판 모델 후보 — 수집기(PR-1)가 먼저 부르는 집합과 같다(`ctx.t_candidates`).
    'T 가격 없음' = fi_prices 에 그 종목의 T 종가 행이 없다: 장 마감 원장에 행이 없거나(no_row)
    price_valid 가 참이 아니거나(price_invalid — 16:00 뒤 응답) 그 밖(종가 0 이하·층 밖 — other).
    T-6 당일 기업행위 보류(corp_action_pending)는 T 행이 있으므로 세지 않는다 — 수집 결손이 아니라
    사건이다(후보 중 수 · 층 전체 갈래별 수는 기록만).
    FAIL: 결측 비율 > `T_CANDIDATE_MISSING_MAX` · 후보를 못 읽음(candidates_unavailable).
    """
    con = ctx.con
    if ctx.t_candidates is None:
        return GateResult("FG5", GateStatus.FAIL,
                          f"candidates_unavailable — 직전 판 모델 후보를 못 읽었다(커버리지를 잴 수 "
                          f"없다): {ctx.t_candidates_from}",
                          {"n_candidates": None, "candidates_from": ctx.t_candidates_from,
                           "missing_max": T_CANDIDATE_MISSING_MAX})
    con.execute("CREATE OR REPLACE TEMP TABLE _fg5_cand (ticker VARCHAR)")
    con.executemany("INSERT INTO _fg5_cand VALUES (?)", [(t,) for t in ctx.t_candidates])
    rows = con.execute(
        f"SELECT c.ticker, p.ticker IS NOT NULL, s.ticker IS NOT NULL, "
        f"coalesce(s.price_valid, false), u.exclude_reason "
        f"FROM (SELECT DISTINCT ticker FROM _fg5_cand) c "
        f"LEFT JOIN {ctx.view('fi_prices')} p ON p.ticker = c.ticker "
        f"AND p.date = DATE '{ctx.date}' AND p.close IS NOT NULL "
        f"LEFT JOIN _t_src s ON s.ticker = c.ticker "
        f"LEFT JOIN {ctx.view('fi_universe')} u ON u.ticker = c.ticker "
        f"ORDER BY c.ticker").fetchall()
    missing = [str(t) for t, priced, _, _, _ in rows if not priced]
    no_row = [str(t) for t, priced, has_row, _, _ in rows if not priced and not has_row]
    invalid = [str(t) for t, priced, has_row, valid, _ in rows
               if not priced and has_row and not valid]
    n = len(rows)
    ratio = len(missing) / n if n else None
    pending = {str(k): int(v) for k, v in con.execute(
        f"SELECT kind, count(*) FROM _t_pending WHERE ticker IN "
        f"(SELECT ticker FROM {ctx.view('fi_universe')}) GROUP BY kind ORDER BY kind").fetchall()}
    metrics: dict[str, object] = {
        "n_candidates": n, "n_missing": len(missing), "missing_ratio": ratio,
        "missing_max": T_CANDIDATE_MISSING_MAX, "n_no_row": len(no_row),
        "n_price_invalid": len(invalid), "n_missing_other": len(missing) - len(no_row) - len(invalid),
        "missing_tickers": missing[:50], "candidates_from": ctx.t_candidates_from,
        "n_candidates_corp_action_pending": sum(1 for *_, r in rows
                                                if r == "corp_action_pending"),
        "corp_action_pending": pending}
    if ratio is None or ratio > T_CANDIDATE_MISSING_MAX:
        return GateResult(
            "FG5", GateStatus.FAIL,
            f"t_price_missing_over_max — 후보 {len(missing)}/{n} 이 T 가격 없음"
            f"(no_row {len(no_row)} · price_invalid {len(invalid)}) > 상한 "
            f"{T_CANDIDATE_MISSING_MAX}: {','.join(missing[:20])}", metrics)
    return GateResult("FG5", GateStatus.PASS,
                      f"후보 {n} 중 T 가격 없음 {len(missing)} ≤ 상한 {T_CANDIDATE_MISSING_MAX}",
                      metrics)


GATES: tuple[Callable[[GateContext], GateResult], ...] = (
    fg0_schema, fg1_rows, fg2_overlay, fg3_mktcap, fg4_golden, fg_fresh)
EVENING_GATES: tuple[Callable[[GateContext], GateResult], ...] = (*GATES, fg5_t_coverage)


def run_all(ctx: GateContext) -> list[GateResult]:
    """GATE_ORDER 순서(장 마감 판은 EVENING_GATE_ORDER). FG0 이 FAIL 이면 나머지는
    `skip(upstream_failed)`.

    허용표(`stage/skip_allow.py`) 밖 SKIP 은 FAIL 로 센다(K1-7a — FG 는 전부 폐기형).
    """
    evening = ctx.basis == "evening"
    order, gates = ((EVENING_GATE_ORDER, EVENING_GATES) if evening else (GATE_ORDER, GATES))
    out = [fg0_schema(ctx)]
    if out[0].status is GateStatus.FAIL:
        out += [GateResult(name, GateStatus.SKIP, "upstream_failed — FG0", {})
                for name in order[1:]]
    else:
        for gate in gates[1:]:
            out.append(gate(ctx))
    return [skip_allow.judge("factor_inputs", g) for g in out]
