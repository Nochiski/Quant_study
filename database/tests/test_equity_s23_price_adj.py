"""S23 `price_adj_daily` — 절단본 위 `build_table` 왕복 + 합성 재상장 트리 위 부정 픽스처
(DESIGN v1.2 §4-2 · GATES §3-㉓ · EG3_price_adj_daily · §9 S23).

상류 6테이블(`trading_calendar`·`security`·`security_span`·`corp_ticker`·`price_daily`·
`corp_event`) + `adj_factor` 를 같은 equity_root 에 먼저 짓고 그 위에 `price_adj_daily` 를 올린다.

절단본 실측(2026-09-06): **41,066행** = `price_daily` 행수(항등, 격리 0) · 조정이 걸린 행 5,083 ·
미조정 사건이 걸린 행 2,684(2종목 247540·900050, 6.54%) · `cum_share_factor` ∈ [1, 50] ·
매크로 `v_adj_price_fwd`·`v_adj_volume_fwd` 와 as-of 표본 222,610행 전건 **비트 동일**.

손계산의 정본은 KRX 원장이다:
  005930 2018-05-03 조정가 = 원주가 2,650,000 (계수는 05-04 부터 접힌다 — 전방 조정의 앵커)
         2018-05-04 조정가 = 51,900 × 50 = 2,595,000, 조정 거래량 = 39,565,391 × 0.02
  247540 2022-06-24 = 497,400 · 06-27 = 135,900 × 3.988773055332799 = 542,074.258…
         (share_factor 는 KRX 기준가 축 497,400 / 124,700 이지 배정비율 4.0 이 아니다 — S06-2)
  101970 2025-03-28(재상장 첫날) cum = 1 · n_unadjusted_events = 0

**구간 누출은 절단본으로 못 잡는다** — 재상장 2종(036220·101970)에 `factor_ok` 계수가 하나도
없어서 구간 규칙을 꺼도 산출이 같다(`n_rows_span_free_diff` = 0). 그래서 이 축의 부정 픽스처는
합성 equity 트리(§ 아래 「합성 재상장 트리」)에서 돈다.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import pytest
from equity import (
    build,
    inputs,
    rules_s01,
    rules_s02,
    rules_s04,
    rules_s05,
    rules_s06,
    rules_s19,
    rules_s23,
)
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import RULES, EquityTable
from stage import manifest

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SRC = Path(rules_s23.__file__).parent
ADJ_DAILY = rules_s23.PRICE_ADJ_DAILY
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT,
            rules_s06.ADJ_FACTOR)
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_price_adj_daily", "EG4", "EG5a"]

N_ROWS = 41_066                     # = 절단본 price_daily 행수 (EG1 항등)
N_ROWS_ADJUSTED = 5_083
N_ROWS_UNADJUSTED_EVENT = 2_684
N_TICKERS_UNADJUSTED_EVENT = 2      # 247540(2022-05-09~) · 900050(2011-02-16~)

SPLIT_EVE, SPLIT_DAY = dt.date(2018, 5, 3), dt.date(2018, 5, 4)
BONUS_EVE, BONUS_DAY = dt.date(2022, 6, 24), dt.date(2022, 6, 27)
RELIST_101970 = dt.date(2025, 3, 28)
SF_247540 = 497_400 / 124_700       # KRX 기준가 축(S06-2) — 배정비율 4.0 이 아니다


def seed() -> Baseline:
    """전 슬라이스 seed 병합 — 체인이 S01~S06 의 `_const` 를 전부 요구한다."""
    merged: dict[str, dict[str, object]] = {}
    for path in sorted(SRC.glob("baseline_seed_s*.json")):
        for k, v in load(path).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = seed()


def build_chain(equity_root: Path) -> None:
    for t in UPSTREAM:
        r = build.build_table(t, STAGE_SLICE, equity_root, SEED, build_id=f"b_{t.name}")
        assert r.ok, (t.name, [(g.name, g.status.value, g.detail) for g in r.gates])


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, build.BuildResult]:
    eq = tmp_path_factory.mktemp("s23") / "equity"
    build_chain(eq)
    r = build.build_table(ADJ_DAILY, STAGE_SLICE, eq, SEED, build_id="b_price_adj_daily")
    return eq, r


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _con(equity_root: Path, tables: tuple[str, ...] = ("price_adj_daily", "price_daily",
                                                       "adj_factor", "security_span")):
    con = duckdb.connect()
    for t in tables:
        pb = inputs.resolve(equity_root, t)
        globs = ", ".join(f"'{Path(g).resolve()}'" for g in pb.globs)
        con.execute(f'CREATE OR REPLACE VIEW "{t}" AS '
                    f"SELECT * FROM read_parquet([{globs}], hive_partitioning=false)")
    return con


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[object, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None, sql
    return row


# ── 빌드 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_체인_위에서_조정가_표가_지어지고_게이트가_전부_통과한다(built) -> None:
    _, r = built
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == GATE_ORDER
    assert r.n_rows == N_ROWS
    assert r.n_reject == 0


def test_EG1은_price_daily_행수와의_항등식이다(built) -> None:
    _, r = built
    m = _gate(r, "EG1").metrics
    assert m["lhs"] == m["rhs"] == N_ROWS and m["delta"] == 0
    assert m["n_reject"] == 0                    # 격리 사유 자체가 없다


def test_EG3_불변식이_전부_0이고_기록형_실측이_남는다(built) -> None:
    _, r = built
    m = _gate(r, "EG3_price_adj_daily").metrics
    for key in ("n_cum_nonpositive", "n_cum_product_off", "n_cum_product_recalc_off",
                "n_span_first_not_unit", "n_recompute_mismatch", "n_unadjusted_mismatch",
                "n_available_recompute_mismatch", "n_factor_count_mismatch",
                "n_cross_span_factor", "n_available_ne_date",
                "n_available_basis_not_derived", "n_ticker_malformed", "n_off_calendar",
                "n_macro_mismatch"):
        assert m[key] == 0, (key, m[key])
    assert m["n_rows"] == N_ROWS
    assert m["n_rows_adjusted"] == N_ROWS_ADJUSTED
    assert m["n_rows_without_span"] == 0
    assert m["n_rows_with_unadjusted_events"] == N_ROWS_UNADJUSTED_EVENT
    assert m["n_tickers_with_unadjusted_events"] == N_TICKERS_UNADJUSTED_EVENT
    assert m["cum_share_factor_min"] == 1.0 and m["cum_share_factor_max"] == 50.0
    assert m["max_cum_product_dev"] == 0.0        # 확정 계수는 전부 시총 불변이다
    assert m["product_tol_basis"] == "baseline"
    # 절단본은 구간 규칙을 꺼도 산출이 같다 — 재상장 2종에 ok 계수가 없다(합성 트리에서 검증)
    assert m["n_rows_span_free_diff"] == 0
    # 미조정 사건의 **사유별 내역** — "조정이 틀렸다" 와 "MVP 밖 축이다" 를 구별할 수 있어야 한다
    by_source = {row["factor_source"]: row for row in m["unadjusted_events_by_factor_source"]}
    assert set(by_source) == {"no_price_match", "ratio_null", "near_dup_suppressed",
                              "unknown_price_only"}
    # 101970 의 5건은 폐지 구간이라 어느 행에도 안 걸린다 — 걸리는 것은 unknown_price_only 2건뿐
    assert [s for s, r in by_source.items() if r["n_events_counted"]] == ["unknown_price_only"]
    assert by_source["unknown_price_only"]["n_events_counted"] == 2
    assert by_source["unknown_price_only"]["n_tickers"] == N_TICKERS_UNADJUSTED_EVENT
    assert (by_source["unknown_price_only"]["n_row_hits"]
            == m["n_rows_with_unadjusted_events"])      # 사건이 겹치지 않아 행 수와 같다
    q = m["cum_share_factor_quantiles_adjusted_rows"]
    assert set(q) == {"p10", "p50", "p90"} and all(v is not None and v > 0 for v in q.values())


def test_매크로_v_adj_price_fwd와_표가_as_of_표본에서_같은_값을_낸다(built) -> None:
    _, r = built
    m = _gate(r, "EG3_price_adj_daily").metrics
    assert m["macro_asof_basis"] == "baseline_asof_sample"
    assert m["macro_compared_rows"] > 0
    assert m["macro_row_set_diff"] == 0
    assert m["macro_max_rel_dev"] == 0.0          # 비트 동일
    by_view = m["macro_by_view"]
    assert isinstance(by_view, dict)
    assert {k.split("@", 1)[0] for k in by_view} == {"v_adj_price_fwd", "v_adj_volume_fwd"}
    assert all(v["n_diff"] == 0 and v["n_row_set_diff"] == 0 for v in by_view.values())


# ── 손계산 ───────────────────────────────────────────────────────────────────

def test_분할_전날은_원주가_그대로이고_분할일부터_계수가_접힌다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        eve = _one(con, "SELECT adj_close, cum_share_factor, n_factors_applied "
                        f"FROM price_adj_daily WHERE ticker='005930' AND date=DATE '{SPLIT_EVE}'")
        assert eve == (2_650_000.0, 1.0, 0)       # 앵커 — 계수는 fold_date 05-04 부터
        day = _one(con, "SELECT adj_close, adj_open, cum_share_factor, n_factors_applied "
                        f"FROM price_adj_daily WHERE ticker='005930' AND date=DATE '{SPLIT_DAY}'")
        assert day == (51_900 * 50.0, 53_000 * 50.0, 50.0, 1)
    finally:
        con.close()


def test_조정_수익률은_기준가_대비가_되어_원주가_불연속이_닫힌다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        raw_prev, adj_prev = _one(con, "SELECT p.close, a.adj_close FROM price_daily p "
                                       "JOIN price_adj_daily a USING (ticker, date) "
                                       f"WHERE p.ticker='005930' AND p.date=DATE '{SPLIT_EVE}'")
        raw, adj = _one(con, "SELECT p.close, a.adj_close FROM price_daily p "
                             "JOIN price_adj_daily a USING (ticker, date) "
                             f"WHERE p.ticker='005930' AND p.date=DATE '{SPLIT_DAY}'")
        assert float(str(raw)) / float(str(raw_prev)) - 1 == pytest.approx(-0.98041, abs=1e-5)
        assert adj / adj_prev - 1 == pytest.approx(51_900 / 53_000 - 1)   # KRX 기준가 대비
    finally:
        con.close()


def test_조정_거래량은_price_factor_축이라_분할_전_척도로_내려간다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        raw, adj, cpf = _one(con, "SELECT p.volume_shr, a.adj_volume_shr, a.cum_price_factor "
                                  "FROM price_daily p JOIN price_adj_daily a USING (ticker, date) "
                                  f"WHERE p.ticker='005930' AND p.date=DATE '{SPLIT_DAY}'")
        assert adj == pytest.approx(float(str(raw)) * 0.02)
        assert cpf == 0.02
        # 나눗셈이었다면 2,500배 어긋난다 — 방향 오류의 크기
        assert adj != pytest.approx(float(str(raw)) * 50.0)
    finally:
        con.close()


def test_무상증자_권리락은_KRX_기준가_축_계수를_쓴다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        eve = _one(con, "SELECT adj_close, cum_share_factor FROM price_adj_daily "
                        f"WHERE ticker='247540' AND date=DATE '{BONUS_EVE}'")
        assert eve == (497_400.0, 1.0)
        day = _one(con, "SELECT adj_close, cum_share_factor, n_unadjusted_events "
                        f"FROM price_adj_daily WHERE ticker='247540' AND date=DATE '{BONUS_DAY}'")
        assert day[0] == pytest.approx(135_900 * SF_247540)
        assert day[1] == pytest.approx(SF_247540)
        assert day[2] == 1                        # 2022-05-09 unknown_price_only(ok=false)
        # 배정비율 4.0 을 쓰면 543,600 — KRX 기준가 산식(자기주식 신주 미배정)과 어긋난다
        assert day[0] != pytest.approx(135_900 * 4.0)
    finally:
        con.close()


def test_재상장_구간의_첫_행은_누적이_1이고_이전_구간_사건을_물지_않는다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        row = _one(con, "SELECT cum_price_factor, cum_share_factor, n_factors_applied, "
                        "n_unadjusted_events FROM price_adj_daily "
                        f"WHERE ticker='101970' AND date=DATE '{RELIST_101970}'")
        assert row == (1.0, 1.0, 0, 0)
        # 미조정 사건 5건은 전부 폐지 구간(2015~2018)이라 어느 구간의 행에도 붙지 않는다
        assert _one(con, "SELECT count(*) FROM price_adj_daily "
                         "WHERE ticker='101970' AND n_unadjusted_events > 0")[0] == 0
        assert _one(con, "SELECT count(*) FROM adj_factor "
                         "WHERE ticker='101970' AND NOT factor_ok")[0] == 5
    finally:
        con.close()


def test_모든_구간의_첫_행에서_누적이_1이다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        assert _one(con, """
            WITH first_row AS (
                SELECT p.ticker, min(p.date) AS date
                FROM price_daily p
                ASOF LEFT JOIN security_span s ON s.ticker = p.ticker AND p.date >= s.first_date
                GROUP BY p.ticker, s.span_seq)
            SELECT count(*) FROM first_row f JOIN price_adj_daily a USING (ticker, date)
            WHERE a.cum_price_factor <> 1 OR a.cum_share_factor <> 1
               OR a.n_factors_applied <> 0""")[0] == 0
    finally:
        con.close()


def test_available_date는_전건_date다(built) -> None:
    eq, _ = built
    con = _con(eq)
    try:
        assert _one(con, "SELECT count(*) FROM price_adj_daily "
                         "WHERE available_date IS DISTINCT FROM date "
                         "OR available_basis <> 'derived'")[0] == 0
    finally:
        con.close()


def test_원주가는_싣지_않고_price_daily에도_컬럼을_더하지_않는다(built) -> None:
    """조정가를 `price_daily` 에 두면 price_daily → adj_factor → price_daily 순환이 된다."""
    assert not [c for c in rules_s04.PRICE_DAILY.columns if c.startswith("adj_")]
    assert "price_adj_daily" not in rules_s04.PRICE_DAILY.inputs
    assert not [c for c in ADJ_DAILY.columns if c in ("open", "high", "low", "close",
                                                      "volume_shr")]
    eq, _ = built
    con = _con(eq)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE price_daily").fetchall()}
        assert not {c for c in cols if c.startswith("adj_")}
    finally:
        con.close()


def test_같은_입력을_두_번_지으면_content_hash가_같다(built, tmp_path: Path) -> None:
    eq, first = built
    second = build.build_table(ADJ_DAILY, STAGE_SLICE, eq, SEED, build_id="b_price_adj_daily_2")
    assert second.ok, [(g.name, g.status.value, g.detail) for g in second.gates]
    assert second.content_hash == first.content_hash
    assert _gate(second, "EG5a").status is GateStatus.PASS


# ── 합성 재상장 트리 — 구간 누출 축 ──────────────────────────────────────────
# 절단본에는 "재상장 종목 + 폐지 전 구간의 ok 계수" 조합이 없다. 구간 규칙을 껐을 때 무엇이
# 어긋나는지 보이려면 그 조합을 손으로 만들어야 한다.
#   036220: 구간 1 [2020-01-02, 2020-01-06] · 구간 2 [2020-01-09, 2020-01-10]
#   ok 계수 1건 (fold 2020-01-03, share 10 / price 0.1) — 구간 1 안
#   not-ok 사건 1건 (apply 2020-01-06) — 구간 1 안
# 기대: 구간 2 의 두 행은 cum = 1 · n_unadjusted_events = 0.

SYNTH_SESSIONS = [dt.date(2020, 1, d) for d in (2, 3, 6, 7, 8, 9, 10)]
SYNTH_SPANS = [("036220", 1, dt.date(2020, 1, 2), dt.date(2020, 1, 6)),
               ("036220", 2, dt.date(2020, 1, 9), dt.date(2020, 1, 10))]
SYNTH_PRICE_DATES = [dt.date(2020, 1, 2), dt.date(2020, 1, 3), dt.date(2020, 1, 6),
                     dt.date(2020, 1, 9), dt.date(2020, 1, 10)]


def _write_equity_table(root: Path, table: str, rows_sql: str, *,
                        partition_expr: str | None = None) -> None:
    """equity 산출 규약(`v=<build>/…` + MANIFEST)대로 손 테이블 하나를 굽는다."""
    build_id = f"b_synth_{table}"
    table_root = root / table
    vdir = table_root / f"v={build_id}"
    vdir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"CREATE TEMP VIEW t AS {rows_sql}")
        if partition_expr is None:
            con.execute(f"COPY (SELECT * FROM t) TO '{vdir / 'part0.parquet'}' (FORMAT PARQUET)")
            parts = [("", vdir)]
        else:
            con.execute(f"CREATE TEMP VIEW tp AS SELECT *, {partition_expr} AS year FROM t")
            con.execute(f"COPY (SELECT * FROM tp) TO '{vdir}' (FORMAT PARQUET, "
                        "PARTITION_BY (year), OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'part')")
            parts = [(f"year={y}", vdir / f"year={y}") for (y,) in
                     con.execute("SELECT DISTINCT year FROM tp ORDER BY 1").fetchall()]
        records = []
        for label, pdir in parts:
            n = int(str(con.execute(
                f"SELECT count(*) FROM read_parquet('{pdir / '*.parquet'}')").fetchone()[0]))
            (pdir / "_meta.json").write_text(json.dumps(
                {"table": table, "build_id": build_id, "partition": label or "whole",
                 "n_rows": n, "lag_known": True, "gates": []}, ensure_ascii=False),
                encoding="utf-8")
            records.append({"path": f"v={build_id}" + (f"/{label}" if label else ""), "n_rows": n})
    finally:
        con.close()
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=build_id, snapshot_id="synth", rules_version="synth",
        built_at_utc="2026-09-06T00:00:00+00:00", n_rows=sum(r["n_rows"] for r in records),
        content_hash="synth", partitions=records, gates=[], inputs={}))


def _synth_root(root: Path) -> Path:
    """구간 2개짜리 재상장 종목 하나로 이뤄진 equity 입력 트리."""
    cal = ", ".join(f"(DATE '{d}')" for d in SYNTH_SESSIONS)
    _write_equity_table(root, "trading_calendar",
                        f"SELECT * FROM (VALUES {cal}) AS t(date)")
    spans = ", ".join(f"('{t}', {q}, DATE '{a}', DATE '{b}')" for t, q, a, b in SYNTH_SPANS)
    _write_equity_table(root, "security_span",
                        f"SELECT * FROM (VALUES {spans}) AS t(ticker, span_seq, first_date, "
                        "last_date)")
    px = ", ".join(
        f"('036220', DATE '{d}', CAST({100 + i} AS DECIMAL(9,0)), "
        f"CAST({110 + i} AS DECIMAL(9,0)), CAST({90 + i} AS DECIMAL(9,0)), "
        f"CAST({100 + i} AS DECIMAL(9,0)), CAST({1000 + i} AS DECIMAL(13,0)), 'trade')"
        for i, d in enumerate(SYNTH_PRICE_DATES))
    _write_equity_table(root, "price_daily",
                        f"SELECT * FROM (VALUES {px}) AS t(ticker, date, open, high, low, close, "
                        "volume_shr, price_kind)", partition_expr="year(date)")
    fac = ("('036220:split:2020-01-03', '036220', DATE '2020-01-03', DATE '2020-01-03', "
           "0.1, 10.0, TRUE, 'mktcap_neutral'), "
           "('036220:capred:2020-01-06', '036220', DATE '2020-01-06', DATE '2020-01-06', "
           "1.0, 1.0, FALSE, 'no_price_match')")
    _write_equity_table(root, "adj_factor",
                        f"SELECT * FROM (VALUES {fac}) AS t(event_id, ticker, apply_date, "
                        "available_date, price_factor, share_factor, factor_ok, factor_source)",
                        partition_expr="year(apply_date)")
    return root


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 안 건드린다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**ADJ_DAILY.__dict__, "name": name, "sql_path": p})


def _body() -> str:
    return ADJ_DAILY.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


# 합성 트리 전용 골든 픽스처 — 정본 픽스처는 절단본 키(005930·247540)라 여기서 안 맞는다.
SYNTH_FIXTURES = [
    {"case": "synth_span2_anchor", "key": {"ticker": "036220", "date": "2020-01-09"},
     "column": "cum_share_factor", "expect": "1.0", "source": "hand — 재상장 구간의 앵커"},
    {"case": "synth_span1_applied", "key": {"ticker": "036220", "date": "2020-01-03"},
     "column": "cum_share_factor", "expect": "10.0", "source": "hand — 구간 1 계수"},
]


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, build.BuildResult]:
    base = tmp_path_factory.mktemp("s23_synth")
    fx = base / "price_adj_daily.json"
    fx.write_text(json.dumps(SYNTH_FIXTURES, ensure_ascii=False), encoding="utf-8")
    eq = _synth_root(base / "equity")
    r = build.build_table(ADJ_DAILY, STAGE_SLICE, eq, SEED, build_id="b_synth_adj",
                          fixtures_path=fx)
    return eq, r


def test_합성_재상장_트리에서_구간2는_구간1_계수를_물지_않는다(synth) -> None:
    eq, r = synth
    # 골든 픽스처는 절단본 키라 합성 트리에서는 EG4 가 못 맞는다 — EG3 까지가 검증 대상이다
    assert _gate(r, "EG3_price_adj_daily").status is GateStatus.PASS, \
        _gate(r, "EG3_price_adj_daily").detail
    m = _gate(r, "EG3_price_adj_daily").metrics
    assert m["n_cross_span_factor"] == 0
    # 구간 규칙을 껐다면 달라질 행이 실제로 있다 — 이 트리는 그 축을 덮는다
    assert m["n_rows_span_free_diff"] > 0
    con = _con(eq, ("price_daily", "adj_factor", "security_span"))
    try:
        out = r.out_dir
        assert out is not None
        con.execute("CREATE OR REPLACE VIEW pad AS SELECT * FROM read_parquet("
                    f"'{out / 'year=*' / '*.parquet'}', hive_partitioning=false)")
        rows = con.execute("SELECT date, cum_share_factor, n_factors_applied, "
                           "n_unadjusted_events FROM pad ORDER BY date").fetchall()
        assert rows == [
            (dt.date(2020, 1, 2), 1.0, 0, 0),      # 구간 1 앵커
            (dt.date(2020, 1, 3), 10.0, 1, 0),     # 구간 1 계수 적용
            (dt.date(2020, 1, 6), 10.0, 1, 1),     # 미조정 사건이 같은 구간에서 잡힌다
            (dt.date(2020, 1, 9), 1.0, 0, 0),      # 구간 2 앵커 — 누적 초기화
            (dt.date(2020, 1, 10), 1.0, 0, 0),
        ]
    finally:
        con.close()


def test_부정_조정가를_나눗셈으로_뒤집으면_EG3가_폐기한다(synth, tmp_path: Path) -> None:
    eq, _ = synth
    sql = _body().replace("u.close      * u.cum_share_factor              AS adj_close",
                          "u.close      / u.cum_share_factor              AS adj_close")
    assert "u.close      / u.cum_share_factor" in sql
    r = build.build_table(_variant(tmp_path, "neg_divide", sql), STAGE_SLICE, eq, SEED,
                          build_id="b_neg_divide")
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_price_adj_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_recompute_mismatch"] > 0
    assert g.metrics["n_macro_mismatch"] > 0        # 매크로와도 갈린다


def test_부정_구간_제한을_없애면_이전_구간_계수가_새어_EG3가_폐기한다(synth,
                                                                  tmp_path: Path) -> None:
    """`oks` 의 구간 부여를 없애 계수를 티커 전체로 누적시킨다 — 재상장 2구간이 하나로 붙는다."""
    eq, _ = synth
    # 가격 행·계수 양쪽의 구간 부여를 같은 상수로 바꾼다 = 티커 전체를 한 구간으로 본다.
    # (한쪽만 바꾸면 조인 키가 안 맞아 "계수가 아예 안 접히는" 다른 결함이 된다)
    const_span = "    CROSS JOIN (SELECT CAST(0 AS BIGINT) AS span_seq) s"
    sql = _body().replace(
        """    ASOF LEFT JOIN security_span s
      ON s.ticker = p.ticker AND p.date >= s.first_date""", const_span).replace(
        """    ASOF LEFT JOIN security_span s
      ON s.ticker = o.ticker AND o.fold_date > s.first_date""", const_span)
    assert sql.count(const_span) == 2, sql
    r = build.build_table(_variant(tmp_path, "neg_span_leak", sql), STAGE_SLICE, eq, SEED,
                          build_id="b_neg_span_leak")
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_price_adj_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_cross_span_factor"] > 0     # 구간 안 계수 수보다 많이 접혔다
    assert g.metrics["n_factor_count_mismatch"] >= g.metrics["n_cross_span_factor"]
    assert g.metrics["n_recompute_mismatch"] > 0
    assert g.metrics["n_span_first_not_unit"] > 0   # 구간 2 첫 행의 누적이 1 이 아니다
    assert g.metrics["n_macro_mismatch"] > 0        # 구간을 지키는 매크로와도 갈린다


def test_부정_미조정_사건_수를_0으로_지우면_EG3가_폐기한다(synth, tmp_path: Path) -> None:
    eq, _ = synth
    sql = _body().replace("CAST(u.n_unadjusted_events AS BIGINT)          AS n_unadjusted_events",
                          "CAST(0 AS BIGINT)                              AS n_unadjusted_events")
    assert "CAST(0 AS BIGINT)" in sql
    r = build.build_table(_variant(tmp_path, "neg_unadjusted", sql), STAGE_SLICE, eq, SEED,
                          build_id="b_neg_unadjusted")
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_price_adj_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_unadjusted_mismatch"] > 0


# ── 선언 축 ──────────────────────────────────────────────────────────────────

def test_price_adj_close_필드는_이_표만_선언한다() -> None:
    owners = [t for t, rule in RULES.items()
              if any(fp.field_id == "price.adj_close" for fp in rule.field_profiles)]
    assert owners == ["price_adj_daily"]
    fp = rules_s23.FIELDS[0]
    assert fp.view_name is None and fp.coverage_table is None    # 뷰가 아니라 표의 컬럼이다
    assert fp.recommended_lag_sessions == 0 and fp.point_in_time is True
    # 조정 OHLC·거래량은 소비 어휘가 없어 선언하지 않는다(FIELD_MAP §2 · FACTORS 정본 54)
    assert [f.field_id for f in rules_s23.FIELDS] == ["price.adj_close"]


def test_dataset_profile_이_이_표를_훑는다() -> None:
    assert "price_adj_daily" in rules_s19.SOURCE_TABLES


def test_커널_어댑터는_조정가_표를_읽지_않는다() -> None:
    """커널은 원주가 + 기업행위 이벤트로 수량을 조정한다 — 조정가를 주면 이중 계산이다."""
    kernel = (Path(__file__).parents[2] / "backend" / "src" / "backtest_engine" / "adapters"
              / "equity_duckdb.py")
    assert kernel.exists(), kernel
    assert "price_adj_daily" not in kernel.read_text(encoding="utf-8")
