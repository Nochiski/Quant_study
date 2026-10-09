"""factor_inputs × 커밋된 stage 절단본(`tests/fixtures/stage_slice`) — 왕복 · 골든 · compat 동등.

절단본 위에 equity 체인(달력·종목·가격·수정주가·유니버스·수급·신용·재무·커버·배당·감사 17표)을 짓고
그 위에서 `factor_inputs.build` 를 돌린다. WICS 원천(`stg_wics_components`)은 절단본에 없어
`sector_snapshot` 만 합성한다(값 3행).

두 날짜를 쓴다 — 절단본 가격은 2026-08-20 에 끝나고 WISE 수집은 09-01·09-02 뿐이라 한 날에 둘 다
있는 D 가 없다.
  · D = 2026-08-20 : 판 빌드 전체 + FG4 골든(`src/factor_inputs/fixtures/golden.json`). 이날까지
                     추정치 수집 기록이 없으므로 전 종목 coverage_state='none' · eligible 0 이고
                     FG-fresh 는 skip(no_collection) — 빈 유니버스는 운영에서 FG1 하한이 막는다.
  · D = 2026-09-03 : `fi_fin_summary` 연간 행 = compat `financial_summary` SQL 출력(겹치는 열 전부)
                     — `test_compat_export._financial_summary_on_real_slice` 와 같은 틀이되
                     DART 쪽은 절단본에서 지은 실제 `fin_std` 다.
골든 기대값은 전부 stage 원장 1행에서 손으로 옮긴 값이다(주석에 출처).
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from conftest import _make_stage_tree
from equity import (
    build as eq_build,
)
from equity import (
    inputs,
    rules_s01,
    rules_s02,
    rules_s03,
    rules_s04,
    rules_s05,
    rules_s06,
    rules_s08,
    rules_s10,
    rules_s11,
    rules_s12,
    rules_s15,
    rules_s16,
    rules_s23,
    rules_s24,
)
from equity.baseline import Baseline, load
from factor_inputs import build, queries
from factor_inputs.build import GOLDEN_PATH, OPTIONAL_STAGE_SOURCES
from model.contracts import UniverseRule
from stage import manifest
from test_equity_s08_flow import _repaired_stage_root

CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.CORP, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
         rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT, rules_s06.ADJ_FACTOR,
         rules_s23.PRICE_ADJ_DAILY, rules_s03.UNIVERSE_DAILY, rules_s08.FLOW_DAILY,
         rules_s10.CREDIT_DAILY, rules_s11.DISCLOSURE_VERSION, rules_s12.FIN_STD,
         rules_s24.COVERAGE_DAILY, rules_s16.DIVIDEND_EVENT, rules_s15.AUDIT_OPINION)
D_BUILD = "20260820"
D_FIN = dt.date(2026, 9, 3)


def _seed() -> Baseline:
    merged: dict[str, dict[str, object]] = {}
    for path in sorted(Path(rules_s23.__file__).parent.glob("baseline_seed_s*.json")):
        for k, v in load(path).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


def _sector_rows() -> list[dict]:
    snap = dt.date(2026, 8, 14)
    return [{"ticker": t, "snapshot_date": snap, "wics_l1_cd": l1, "wics_l1_nm": l1n,
             "wics_l2_cd": l2, "wics_l2_nm": l2n, "available_date": snap}
            for t, l1, l1n, l2, l2n in (
                ("005930", "G45", "IT", "G4530", "WICS 반도체와반도체장비"),
                ("000660", "G45", "IT", "G4530", "WICS 반도체와반도체장비"),
                ("161890", "G30", "필수소비재", "G3030", "WICS 가정용품과개인용품"))]


@pytest.fixture(scope="module")
def chain(tmp_path_factory) -> tuple[Path, Path]:
    """(equity_root, stage_root) — 절단본 위 equity 17표 + 합성 sector_snapshot."""
    base = tmp_path_factory.mktemp("fi_slice")
    stage_root = _repaired_stage_root(base)
    eq = base / "eqroot" / "stage"          # _make_stage_tree 가 `<X>/stage/<표>` 에 쓴다
    seed = _seed()
    for rule in CHAIN:
        r = eq_build.build_table(rule, stage_root, eq, seed, build_id=f"b_{rule.name}")
        assert r.ok, (rule.name, [(g.name, g.detail) for g in r.gates
                                  if g.status.value == "fail"])
    _make_stage_tree(base / "eqroot", "sector_snapshot", _sector_rows(), build_id="b_sector")
    return eq, stage_root


@pytest.fixture(scope="module")
def built(chain, tmp_path_factory):
    out = tmp_path_factory.mktemp("fi_slice_out") / "factor_inputs"
    res = build(D_BUILD, "morning", out, chain[1], chain[0], min_eligible=0)
    return out, res


def q(out: Path, table: str, sql: str) -> list[tuple]:
    m = manifest.load(out / table / "MANIFEST.json")
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW t AS SELECT * FROM read_parquet("
                    f"'{out / table / f'v={m.current_build}' / 'part0.parquet'}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 판 빌드 · 골든 ───────────────────────────────────────────────────────────
def test_slice_build_passes_all_gates_with_golden(built) -> None:
    _, res = built
    status = {g.name: g.status.value for g in res.gates}
    assert res.ok, [(g.name, g.status.value, g.detail) for g in res.gates]
    assert status == {"FG0": "pass", "FG1": "pass", "FG2": "pass", "FG3": "pass",
                      "FG4": "pass", "FG-fresh": "skip"}
    fg4 = next(g for g in res.gates if g.name == "FG4")
    n_golden = len(json.loads(GOLDEN_PATH.read_text(encoding="utf-8")))
    assert fg4.metrics["n_checked"] == n_golden and fg4.metrics["n_mismatch"] == 0
    assert n_golden >= 12
    assert {e["key"]["ticker"] for e in json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))} == {
        "005930", "000660", "161890"}


def test_golden_fixture_mismatch_fails_the_build(chain, tmp_path: Path) -> None:
    fx = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    fx[0]["expect"] = fx[0]["expect"] + 1
    bad = tmp_path / "golden.json"
    bad.write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "fi"
    res = build(D_BUILD, "morning", out, chain[1], chain[0], min_eligible=0, golden_path=bad)
    assert not res.ok and [g.name for g in res.gates if g.status.value == "fail"] == ["FG4"]
    assert not (out / "fi_prices" / "MANIFEST.json").exists()


def test_slice_universe_on_d(built) -> None:
    out, _ = built
    got = {r[0]: r[1:] for r in q(out, "fi_universe",
           "SELECT ticker, market, sec_type, shares, market_cap, coverage_state, eligible, "
           "exclude_reason, sector_l1 FROM t")}
    # ETF(069500)는 층에 싣지 않는다. 우선주는 싣고 sec_type 으로 제외한다.
    assert "069500" not in got and "003545" in got
    # 005930 08-20: 상장주식수 5,846,278,608 × 종가 271,000 = 1,584,341,502,768,000원
    #   → round(15,843,415.02768) = 15,843,415 억원(stg_price_daily list_shrs·close_krw)
    assert got["005930"] == ("KOSPI", "common", 5_846_278_608, 15_843_415.0, "none", False,
                             "estimates_none", "G45")
    assert got["003545"][1] == "preferred" and got["003545"][6] == "sec_type"


def test_slice_adj_ok_ignores_events_outside_the_window(built) -> None:
    """247540 의 미해결 사건(2022-05-09 krx_base)은 550일 창 밖 — 창 안 비율을 깨지 않는다."""
    out, _ = built
    assert q(out, "fi_adj_prices", "SELECT bool_and(adj_ok), count(*) FROM t "
                                   "WHERE ticker = '247540'")[0][0] is True


def test_slice_windows(built) -> None:
    out, _ = built
    d = dt.date(2026, 8, 20)
    lo = q(out, "fi_prices", "SELECT min(date), max(date) FROM t")[0]
    assert lo[0] >= d - dt.timedelta(days=550) and lo[1] == d
    assert q(out, "fi_flows", "SELECT count(DISTINCT date) FROM t")[0][0] == 60
    # 신용은 실입수 T+3 세션 — 08-20 판에서 마지막 행은 08-14(08-17 휴장)
    assert q(out, "fi_credit", "SELECT max(date), max(available_date) FROM t")[0] == (
        dt.date(2026, 8, 14), d)


# ── compat financial_summary 동등(D = 2026-09-03) ────────────────────────────
_COMPARE = ("revenue", "op", "ni", "eps", "bps", "per", "pbr", "roe", "roa", "debt_ratio", "fcf",
            "capex", "op_margin", "ni_margin", "dividend_yield", "shares", "ev_ebitda", "yoy",
            "gross_profit", "total_assets")


def _fin_views(con: duckdb.DuckDBPyConnection, chain: tuple[Path, Path]) -> None:
    eq, st = chain
    for t in ("fin_std", "security", "dividend_event", "trading_calendar", "corp"):
        pb = inputs.resolve(eq, t)
        lit = ", ".join(f"'{g}'" for g in pb.globs)
        con.execute(f'CREATE VIEW "{t}" AS SELECT * FROM read_parquet([{lit}], '
                    "hive_partitioning=false)")
    # 절단본에는 WISE 분기(10-01 T-Q4)가 없다 — build 와 같은 빈 대역(선택 원천)을 쓴다
    con.execute('CREATE VIEW "stg_fin_wise_q" AS SELECT * FROM '
                + OPTIONAL_STAGE_SOURCES["stg_fin_wise_q"])
    for t in ("stg_fin_wise", "stg_consensus_annual"):
        pb = inputs.resolve(st, t)
        lit = ", ".join(f"'{g}'" for g in pb.globs)
        con.execute(f'CREATE VIEW "{t}" AS SELECT * FROM read_parquet([{lit}], '
                    "hive_partitioning=true, union_by_name=true)")


@pytest.fixture(scope="module")
def fin_pair(chain) -> tuple[dict, dict]:
    """(ours, compat) — (ticker, period) → 열 dict. 연간 확정 행만."""
    from compat.mappings import BY_TABLE
    con = duckdb.connect()
    try:
        _fin_views(con, chain)
        p = queries.Params(d=D_FIN.isoformat(), fy=f"{D_FIN.year}12",
                           price_from=(D_FIN - dt.timedelta(days=550)).isoformat(),
                           flow_from=D_FIN.isoformat(),
                           grace_days=UniverseRule().coverage_grace_days, credit_lag=3)
        con.execute(queries.calendar_sql())
        for sql in queries.coverage_sqls(p):
            con.execute(sql)
        con.execute("CREATE TEMP TABLE _fi_universe AS SELECT ticker FROM security")
        con.execute(queries.fin_summary_sql(p))
        cur = con.execute("SELECT * FROM _fi_fin_summary WHERE period_type = 'annual'")
        cols = [c[0] for c in cur.description or []]
        ours = {(r[0], r[1]): dict(zip(cols, r, strict=True)) for r in cur.fetchall()}
        sql = BY_TABLE["financial_summary"].sql.format(
            stg_fin_wise="stg_fin_wise", fin_std="fin_std", security="security",
            consensus_asof=D_FIN.isoformat(), date=D_FIN.isoformat())
        cur = con.execute(f"SELECT * FROM ({sql}) WHERE period_type = 'annual' "
                          "AND data_type IS NULL")
        cols = [c[0] for c in cur.description or []]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()
    # v3 창 = 종목별 최신 2기(`ORDER BY period DESC LIMIT 2`)
    by_t: dict[str, list[dict]] = {}
    for r in rows:
        by_t.setdefault(r["stock_code"], []).append(r)
    compat = {(t, r["period"]): r for t, rs in by_t.items()
              for r in sorted(rs, key=lambda x: x["period"], reverse=True)[:2]}
    return ours, compat


# 의도한 차이: 금융업 연간 매출(영업수익·순영업이익)은 이 층만 싣는다 — compat(v3 미러)은
# '매출액(수익)' 계정만 보고, v3 엔진은 매출을 읽지 않는다(배포 묶음 4-2b). 절단본에서는
# 대신증권 2기가 해당한다.
_FIN_REVENUE_ONLY_OURS = {("003540", "2025/12"), ("003540", "2024/12")}


def test_fin_summary_annual_equals_compat_on_slice(fin_pair) -> None:
    ours, compat = fin_pair
    assert set(ours) == set(compat)
    assert {t for t, _ in ours} >= {"005930", "000660", "003540", "161890", "247540"}
    only_ours = {k for k, c in compat.items()
                 if c["revenue"] is None and ours[k]["revenue"] is not None}
    assert only_ours == _FIN_REVENUE_ONLY_OURS
    for key, c in compat.items():
        o = ours[key]
        for col in _COMPARE:
            if col == "revenue" and key in only_ours:
                continue
            cv, ov = c[col], o[col]
            assert (cv is None and ov is None) or (
                cv is not None and ov is not None and float(cv) == float(ov)), (key, col, cv, ov)
        if c["accounting_standard"] is not None:
            assert o["fs_basis"] == c["accounting_standard"], key


def test_fin_summary_slice_golden_005930(fin_pair) -> None:
    """005930 2025/12 사업보고서 CFS(stg_fin 접수 20260310002820) 원장 값에서 손으로 옮긴 기대값."""
    ours, _ = fin_pair
    r = ours[("005930", "2025/12")]
    assets, liab, equity = 566_942_110_000_000, 130_621_773_000_000, 436_320_337_000_000
    ni, cfo, capex = 45_206_805_000_000, 85_315_148_000_000, 47_522_179_000_000
    assert r["total_assets"] == 5_669_421.0              # 566,942,110,000,000 / 1e8 반올림
    assert r["capex"] == round(capex / 1e8) == 475_222
    assert r["fcf"] == round((cfo - capex) / 1e8) == 377_930
    assert abs(r["roa"] - ni / assets * 100) < 1e-9
    assert abs(r["debt_ratio"] - liab / equity * 100) < 1e-9
    assert r["dps"] == 1_668.0                           # stg_dividend 보통주 주당 현금배당금
    assert r["capex_basis"] == "standard"
    assert r["available_date"] == dt.date(2026, 9, 2)    # WISE 판(09-02) > DART 접수(03-10)
    assert r["period_months"] == 12                      # fin_std 2025-01-01 ~ 2025-12-31(G-28)
    assert ours[("000660", "2025/12")]["dps"] == 3_000.0


def test_fin_summary_slice_financial_revenue_003540(fin_pair) -> None:
    """대신증권 2025/12 — WISE cF3002 최상위에 '매출액(수익)' 이 없고 '순영업이익' 8,846.07509 억원
    (stg_fin_wise 09-02 판 202520 행)이 매출 자리다. 연간 매출 = 그 값, 종류 net."""
    ours, _ = fin_pair
    r = ours[("003540", "2025/12")]
    assert (r["revenue"], r["revenue_basis"]) == (8_846.0, "net")


# ── compat consensus_annual 동등(v2 원천, D = 2026-09-03) ────────────────────
def test_consensus_annual_equals_compat_on_slice(chain) -> None:
    """compat 은 한 기에 E 하나(E 우선)만 남기고 이 표는 E·A 를 다 싣는다 — compat 행마다 같은
    종류의 우리 행이 같은 값이어야 하고, 우리만 가진 행은 compat 이 E 를 고른 기의 A 뿐이다."""
    from compat.mappings import BY_TABLE
    con = duckdb.connect()
    try:
        _fin_views(con, chain)
        p = queries.Params(d=D_FIN.isoformat(), fy=f"{D_FIN.year}12",
                           price_from=D_FIN.isoformat(), flow_from=D_FIN.isoformat(),
                           grace_days=5, credit_lag=3)
        con.execute("CREATE TEMP TABLE _fi_universe AS SELECT ticker FROM security")
        con.execute(queries.consensus_annual_sql(p))
        ours = {(r[0], r[1], r[2]): r[3:] for r in con.execute(
            "SELECT ticker, period, data_type, revenue, op, ni, eps, per "
            "FROM _fi_consensus_annual").fetchall()}
        sql = BY_TABLE["consensus_annual"].sql.format(
            stg_consensus_annual="stg_consensus_annual", consensus_asof=D_FIN.isoformat())
        compat = {(r[0], r[1], "E" if r[2] == "estimate" else "A"): r[3:] for r in con.execute(
            f"SELECT stock_code, period, data_type, revenue, op, ni, eps, per FROM ({sql}) "
            "WHERE period IN ('2025/12', '2026/12', '2027/12')").fetchall()}
    finally:
        con.close()
    assert compat and {t for t, _, _ in compat} == {"000660", "003540", "005930", "161890",
                                                   "247540"}
    for key, cv in compat.items():
        assert key in ours, key
        assert tuple(None if v is None else float(v) for v in cv) == ours[key], key
    extra = set(ours) - set(compat)
    assert all(k[2] == "A" and (k[0], k[1], "E") in compat for k in extra), extra


# ── 회귀 가드(배포 묶음 7-2): 접지 않은 절단본에서 WISE 소비 출력이 fi1.2.0 과 같다 ──
# stage 가 WISE 재무 두 표의 같은 원문을 접어도(7-1) fi 가 (종목, ep) 단위 최신 판을 읽도록 바꾼다.
# 접지 않은 절단본은 매 수집일 두 ep 가 다 있어 옛 쿼리(종목 한 날짜)와 새 쿼리의 답이 같아야 한다.
# 골든은 바꾸기 전 코드(fi1.2.0, faa41f7d)로 뽑아 고정했다 — 전 열·전 행 그대로 대조한다.
# `_cov`·fi_consensus·fi_consensus_annual 은 이번에 고치지 않는 쿼리라 함께 묶어 그대로임을 본다.
SLICE_WISE_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "fi_slice_wise_golden.json"
SLICE_WISE_DATES = (dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3))


def _plain(v: object) -> object:
    """JSON 골든과 같은 모양 — 날짜는 ISO 문자열, DECIMAL 은 문자열."""
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def _slice_wise_outputs(chain: tuple[Path, Path], d: dt.date) -> dict[str, dict[str, list]]:
    """D 의 `_cov`·fi_fin_summary·fi_consensus·fi_consensus_annual 전 행
    (유니버스 = security 전부)."""
    con = duckdb.connect()
    try:
        _fin_views(con, chain)
        pb = inputs.resolve(chain[1], "stg_consensus_matrix")
        lit = ", ".join(f"'{g}'" for g in pb.globs)
        con.execute('CREATE VIEW "stg_consensus_matrix" AS SELECT * FROM read_parquet('
                    f"[{lit}], hive_partitioning=true, union_by_name=true)")
        p = queries.Params(d=d.isoformat(), fy=f"{d.year}12",
                           price_from=(d - dt.timedelta(days=550)).isoformat(),
                           flow_from=d.isoformat(),
                           grace_days=UniverseRule().coverage_grace_days, credit_lag=3)
        con.execute(queries.calendar_sql())
        for sql in queries.coverage_sqls(p):
            con.execute(sql)
        # fi_consensus 가 n_analysts 를 universe 에서 붙인다 — 절단본 D 에는 universe_daily 행이
        # 없어 security 전부를 유니버스로 둔다(fin_pair 와 같은 틀)
        con.execute("CREATE TEMP TABLE _fi_universe AS "
                    "SELECT ticker, CAST(NULL AS INTEGER) AS n_analysts FROM security")
        for sql in (queries.fin_summary_sql(p), queries.consensus_sql(p),
                    queries.consensus_annual_sql(p)):
            con.execute(sql)
        out: dict[str, dict[str, list]] = {}
        for name, sql in (("_cov", "SELECT * FROM _cov ORDER BY ticker"),
                          ("fi_fin_summary", "SELECT * FROM _fi_fin_summary"),
                          ("fi_consensus", "SELECT * FROM _fi_consensus"),
                          ("fi_consensus_annual", "SELECT * FROM _fi_consensus_annual")):
            cur = con.execute(sql)
            out[name] = {"columns": [c[0] for c in cur.description or []],
                         "rows": [[_plain(v) for v in r] for r in cur.fetchall()]}
        return out
    finally:
        con.close()


@pytest.mark.parametrize("d", SLICE_WISE_DATES, ids=lambda d: d.isoformat())
def test_unfolded_slice_wise_outputs_equal_fi120_golden(chain, d: dt.date) -> None:
    golden = json.loads(SLICE_WISE_GOLDEN.read_text(encoding="utf-8"))[d.isoformat()]
    got = _slice_wise_outputs(chain, d)
    assert set(got) == set(golden)
    for name, want in golden.items():
        assert got[name]["columns"] == want["columns"], name
        assert len(got[name]["rows"]) == len(want["rows"]), name
        for g, w in zip(got[name]["rows"], want["rows"], strict=True):
            assert g == w, (name, dict(zip(want["columns"], zip(g, w, strict=True), strict=True)))
    # 골든이 비어 있으면 가드가 아니다 — WISE 연간 행·컨센서스 행이 실제로 들어 있어야 한다
    fin = golden["fi_fin_summary"]
    i_type, i_op = fin["columns"].index("period_type"), fin["columns"].index("op")
    assert sum(r[i_type] == "annual" and r[i_op] is not None for r in fin["rows"]) >= 10
    assert all(golden[t]["rows"] for t in ("_cov", "fi_consensus", "fi_consensus_annual"))
