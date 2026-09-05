"""S06 뷰 매크로 — `v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap` + 카탈로그 실체화
+ `_asof/` + 카탈로그 단계 게이트 EG11·EG5c·EG3-P05
(DESIGN §2·§5 · GATES §6 EG11 · §1 EG5c · EG3-P05 · FX-2-010 · FX-N-006).

절단본 위에 S06 체인(5테이블)을 짓고 `catalog.publish` 로 `equity.duckdb` 를 만든 뒤 read_only 로
다시 열어 값을 본다. 손계산 기대값은 `stg_price_daily`·`stg_listing_daily` 원자료에서:
  005930 2018-05-03 종가 2,650,000 · 04-27 거래량 606,216 · 05-04 종가 51,900 거래량 39,565,391
  005935 2018-05-03 종가 2,125,000 · list_shrs 005930 128,386,494 / 005935 18,072,580
  247540 2022-06-24 종가 497,400 → 06-27 135,900 (1주당 3주 무상증자, 권리락일)
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, catalog, rules_s02, rules_s06, views
from equity.__main__ import main
from equity.gates import GateStatus
from test_equity_s06_adj import STAGE_SLICE, build_chain, seed

SAMPLE_DATES = ["2010-01-04", "2015-03-16", "2018-05-04", "2024-03-13", "2026-08-20"]
N_SAMPLE_TICKERS = 15
ASOF_ROWS = 111305          # Σ_as_of Σ_ticker (date ≤ as_of 인 가격 행) — 절단본 실측
MKTCAP_005930_0503 = 2650000 * 128386494 + 2125000 * 18072580     # 378,628,441,600,000


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, catalog.CatalogResult]:
    root = tmp_path_factory.mktemp("s06v") / "equity"
    r = build_chain(STAGE_SLICE, root, seed())
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    p = catalog.publish(root, seed())
    assert p.ok, [(g.name, g.status.value, g.detail) for g in p.gates]
    return root, p


@pytest.fixture
def ro(published: tuple[Path, catalog.CatalogResult], request: pytest.FixtureRequest
       ) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(published[0] / catalog.CATALOG_NAME), read_only=True)
    request.addfinalizer(con.close)
    return con


def _gate(r: catalog.CatalogResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _copy_root(published: tuple[Path, catalog.CatalogResult], tmp_path: Path) -> Path:
    """상태를 바꾸는 검사는 사본 위에서 — 매크로 경로는 publish 가 다시 렌더한다."""
    dst = tmp_path / "equity"
    shutil.copytree(published[0], dst)
    return dst


# ── 카탈로그 실체화 ───────────────────────────────────────────────────────────

def test_카탈로그는_매크로_4개이고_뷰_게이트를_통과한다(
        published: tuple[Path, catalog.CatalogResult]) -> None:
    root, p = published
    assert sorted(p.macros) == sorted(views.SIGNATURES.values()) and p.skipped == {}
    assert [g.name for g in p.gates] == ["EG11", "EG5c", "EG3_firm_mktcap"]
    assert _gate(p, "EG11").status is GateStatus.PASS
    assert _gate(p, "EG5c").status is GateStatus.SKIP
    assert _gate(p, "EG5c").detail == "no_previous_snapshot"
    assert _gate(p, "EG3_firm_mktcap").status is GateStatus.PASS
    meta = json.loads((root / catalog.META_NAME).read_text(encoding="utf-8"))
    assert meta["snapshot_id"] == p.snapshot_id == catalog.snapshot_id(catalog.table_builds(root))
    assert set(meta["builds"]) == {"trading_calendar", "security", "security_span", "corp_ticker",
                                   "price_daily", "corp_event", "adj_factor"}
    assert [g["name"] for g in meta["gates"]] == ["EG11", "EG5c", "EG3_firm_mktcap"]
    assert set(meta["asof"]) == set(catalog.ASOF_VIEWS)
    assert meta["asof"]["v_adj_price"]["n_rows"] == ASOF_ROWS


def test_매크로_본문의_parquet_경로는_절대경로다(
        published: tuple[Path, catalog.CatalogResult]) -> None:
    root, p = published
    body = p.macros["v_cum_adj(as_of, lag_override := NULL)"]
    assert str(root.resolve() / "price_daily" / "v=b_price_daily" / "year=2010") in body
    assert "_reject" not in body and "hive_partitioning=false" in body
    assert "coalesce(lag_override, 0)" in body                  # 가격 계열 세션 랙 0 (views 근거)


# ── 조정가 · 조정 거래량 ─────────────────────────────────────────────────────

def test_분할_전_adj_close는_close_나누기_50이고_분할일부터_1이다(
        ro: duckdb.DuckDBPyConnection) -> None:
    got = ro.execute("""
        SELECT date, close, cum_price_factor, adj_close FROM v_adj_price(DATE '2026-08-20')
        WHERE ticker = '005930' AND date BETWEEN '2018-04-26' AND '2018-05-08' ORDER BY date
        """).fetchall()
    assert [(d, float(c), f, a) for d, c, f, a in got] == [
        (date(2018, 4, 26), 2607000.0, 0.02, 52140.0),
        (date(2018, 4, 27), 2650000.0, 0.02, 53000.0),
        (date(2018, 4, 30), 2650000.0, 0.02, 53000.0),
        (date(2018, 5, 2), 2650000.0, 0.02, 53000.0),
        (date(2018, 5, 3), 2650000.0, 0.02, 53000.0),
        (date(2018, 5, 4), 51900.0, 1.0, 51900.0),
        (date(2018, 5, 8), 52600.0, 1.0, 52600.0)]
    # 원주가 컬럼은 그대로 함께 나온다(결정 6) — 소급 수정 아님
    assert ro.execute("SELECT close FROM v_adj_price(DATE '2026-08-20') WHERE ticker = '005930' "
                      "AND date = DATE '2018-05-03'").fetchone() == (2650000,)


def test_base는_as_of이고_as_of_이후_행은_없다(ro: duckdb.DuckDBPyConnection) -> None:
    assert ro.execute("SELECT cum_price_factor, cum_share_factor FROM v_cum_adj(DATE '2018-05-04') "
                      "WHERE ticker = '005930' AND date = DATE '2018-05-04'").fetchone() == (
        1.0, 1.0)
    assert ro.execute("SELECT cum_price_factor FROM v_cum_adj(DATE '2018-05-04') "
                      "WHERE ticker = '005930' AND date = DATE '2018-05-03'").fetchone() == (0.02,)
    # 분할이 아직 효력 전이면 계수가 없다 — 그날 기준으로는 원주가가 곧 조정가
    assert ro.execute("SELECT cum_price_factor, adj_close FROM v_adj_price(DATE '2018-05-03') "
                      "WHERE ticker = '005930' AND date = DATE '2018-05-03'").fetchone() == (
        1.0, 2650000.0)
    assert ro.execute("SELECT count(*) FROM v_adj_price(DATE '2018-05-03') "
                      "WHERE date > DATE '2018-05-03'").fetchone() == (0,)
    n_all, n_adj = ro.execute("SELECT count(*), count(*) FILTER (WHERE cum_price_factor <> 1) "
                              "FROM v_cum_adj(DATE '2026-08-20')").fetchone()
    assert n_all == 41066 and n_adj > 0


def test_lag_override는_계수의_available_date를_세션으로_민다(
        ro: duckdb.DuckDBPyConnection) -> None:
    """분할 계수 available 05-04. as_of 05-04 랙 1 세션 → 컷오프 05-03 → 계수 제외."""
    q = ("SELECT cum_price_factor FROM v_cum_adj(DATE '2018-05-04', lag_override := {lag}) "
         "WHERE ticker = '005930' AND date = DATE '2018-05-03'")
    assert ro.execute(q.format(lag=0)).fetchone() == (0.02,)
    assert ro.execute(q.format(lag=1)).fetchone() == (1.0,)
    # 다음 거래일 05-08 을 as_of 로 잡으면 랙 1 이어도 컷오프 05-04 라 계수가 들어온다
    assert ro.execute("SELECT cum_price_factor "
                      "FROM v_cum_adj(DATE '2018-05-08', lag_override := 1) "
                      "WHERE ticker = '005930' AND date = DATE '2018-05-03'").fetchone() == (0.02,)
    # 무상증자 계수 available = 공시일 06-14: as_of 06-27 랙 1 이어도 이미 알려져 있었다
    assert ro.execute("SELECT cum_price_factor "
                      "FROM v_cum_adj(DATE '2022-06-27', lag_override := 1) "
                      "WHERE ticker = '247540' AND date = DATE '2022-06-24'").fetchone() == (0.25,)


def test_FX_2_010_조정_거래량은_원거래량_곱하기_50이다(ro: duckdb.DuckDBPyConnection) -> None:
    """GATES §4 FX-2-010 — 키를 2018-04-27 로 옮겼다(05-03 은 분할 정지일이라 거래량 0, 방향 검증
    불가)."""
    got = ro.execute("""
        SELECT date, volume_shr, cum_share_factor, adj_volume FROM v_adj_volume(DATE '2018-06-01')
        WHERE ticker = '005930'
          AND date IN (DATE '2018-04-27', DATE '2018-05-03', DATE '2018-05-04')
        ORDER BY date""").fetchall()
    assert [(d, int(v), f, a) for d, v, f, a in got] == [
        (date(2018, 4, 27), 606216, 50.0, 606216 * 50.0),
        (date(2018, 5, 3), 0, 50.0, 0.0),
        (date(2018, 5, 4), 39565391, 1.0, 39565391.0)]


def test_무상증자_권리락_전일_조정가는_4분의_1이다(ro: duckdb.DuckDBPyConnection) -> None:
    got = ro.execute("""
        SELECT date, cum_price_factor, adj_close FROM v_adj_price(DATE '2022-06-30')
        WHERE ticker = '247540' AND date BETWEEN '2022-06-24' AND '2022-06-27' ORDER BY date
        """).fetchall()
    assert got == [(date(2022, 6, 24), 0.25, 124350.0), (date(2022, 6, 27), 1.0, 135900.0)]


def test_v_adj_price는_v_cum_adj와_같은_계수를_쓴다(ro: duckdb.DuckDBPyConnection) -> None:
    n = ro.execute("""
        SELECT count(*) FROM v_adj_price(DATE '2026-08-20') p
        JOIN v_cum_adj(DATE '2026-08-20') c USING (ticker, date)
        WHERE p.cum_price_factor <> c.cum_price_factor
           OR p.adj_close <> p.close * c.cum_price_factor""").fetchone()
    assert n == (0,)


# ── v_firm_mktcap ────────────────────────────────────────────────────────────

def test_v_firm_mktcap는_본주_우선주_시총의_합이다(ro: duckdb.DuckDBPyConnection) -> None:
    got = {r[1]: r for r in ro.execute("SELECT * FROM v_firm_mktcap(DATE '2018-05-03')").fetchall()}
    corp, firm, d, n_leg, mktcap = got["005930"]
    assert (corp, d, n_leg, int(mktcap)) == ("00126380", date(2018, 5, 3), 2, MKTCAP_005930_0503)
    assert got["003540"][3] == 3                                # 대신증권 보통·구형·신형
    assert got["069500"][0] is None and got["069500"][3] == 1   # ETF 단독, corp 없음
    assert "005935" not in got and "003545" not in got          # 종류주는 행이 아니라 leg
    # 그날 가격 행이 없는(폐지·미상장) 종목은 없다
    assert set(got) == {"000030", "000660", "003540", "005930", "069500", "161890"}


def test_EG3_firm_mktcap는_isin8_축_독립_재계산이다(
        published: tuple[Path, catalog.CatalogResult]) -> None:
    m = _gate(published[1], "EG3_firm_mktcap").metrics
    assert m["dates"] == SAMPLE_DATES
    assert m["n_missing_in_view"] == 0 and m["n_mismatch"] == 0
    assert m["n_multi_leg_groups"] == 10 and m["n_noncommon_legs"] == 15   # 2 그룹 × 5 날짜


def test_우선주를_빼면_EG3_firm_mktcap이_fail하고_카탈로그는_교체되지_않는다(
        published: tuple[Path, catalog.CatalogResult], tmp_path: Path, monkeypatch) -> None:
    root = _copy_root(published, tmp_path)
    before = (root / catalog.META_NAME).read_text(encoding="utf-8")
    monkeypatch.setitem(views.TEMPLATES, "v_firm_mktcap",
                        views.TEMPLATES["v_firm_mktcap"].replace(
                            "WHERE p.date = d", "WHERE ct.is_common AND p.date = d"))
    p = catalog.publish(root, seed())
    assert not p.ok and p.failed_report is not None
    st = {g.name: g.status.value for g in p.gates}
    assert st == {"EG11": "pass", "EG5c": "pass", "EG3_firm_mktcap": "fail"}
    assert _gate(p, "EG3_firm_mktcap").metrics["n_mismatch"] == 10
    assert (root / catalog.META_NAME).read_text(encoding="utf-8") == before
    report = json.loads(p.failed_report.read_text(encoding="utf-8"))
    assert report["first_failed_gate"] == "EG3_firm_mktcap"
    ro = duckdb.connect(str(root / catalog.CATALOG_NAME), read_only=True)
    try:    # 옛 카탈로그가 그대로 — 우선주 leg 가 살아 있다
        assert ro.execute("SELECT n_leg FROM v_firm_mktcap(DATE '2018-05-03') "
                          "WHERE firm_ticker = '005930'").fetchone() == (2,)
    finally:
        ro.close()


# ── EG11 · _asof/ · EG5c ─────────────────────────────────────────────────────

def test_EG11_같은_카탈로그를_두_연결로_호출한_해시가_같다(
        published: tuple[Path, catalog.CatalogResult]) -> None:
    root, p = published
    m = _gate(p, "EG11").metrics
    assert m["n_sample_dates"] == 5 and m["n_sample_tickers"] == N_SAMPLE_TICKERS
    for view in catalog.ASOF_VIEWS:
        h = m["hashes"][view]           # type: ignore[index]
        assert h["hash_1"] == h["hash_2"] and h["n_rows"] == h["n_rows_2"] == ASOF_ROWS
    # 별도 프로세스 경로(P1b): 새 read_only 연결로 같은 표본 SQL 을 다시 돌려도 같은 해시
    sql = catalog.sample_sql("v_adj_price", SAMPLE_DATES, [str(t) for t in seed().get(
        "trading_calendar", "asof_sample_tickers")])   # type: ignore[union-attr]
    con = duckdb.connect(str(root / catalog.CATALOG_NAME), read_only=True)
    try:
        n, h = con.execute(f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM ({sql}) t"
                           ).fetchone()
    finally:
        con.close()
    assert n == ASOF_ROWS and format(int(h), "x") == m["hashes"]["v_adj_price"]["hash_1"]


def test_asof_표본은_고정_날짜_종목의_뷰_결과다(
        published: tuple[Path, catalog.CatalogResult]) -> None:
    root, p = published
    part = Path(str(p.asof["v_adj_price"]["path"]))
    assert part == root / catalog.ASOF_DIR / "v_adj_price" / p.snapshot_id / catalog.ASOF_PART
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet('{part}')")
        assert con.execute("SELECT count(*), count(DISTINCT as_of), count(DISTINCT ticker) FROM s"
                           ).fetchone() == (ASOF_ROWS, 5, N_SAMPLE_TICKERS)
        assert con.execute("SELECT adj_close, cum_price_factor FROM s "
                           "WHERE as_of = DATE '2018-05-04' "
                           "AND ticker = '005930' AND date = DATE '2018-05-03'").fetchone() == (
            53000.0, 0.02)
        # 분할 전 as_of 에서는 원주가 그대로 — 절단본 stg_price_daily 005930 2015-03-16 종가
        # 1,470,000
        assert con.execute("SELECT adj_close, cum_price_factor FROM s "
                           "WHERE as_of = DATE '2015-03-16' "
                           "AND ticker = '005930' AND date = DATE '2015-03-16'").fetchone() == (
            1470000.0, 1.0)
        # as_of 별 행수 = 그날까지의 가격 행수 (2010-01-04 는 그날 상장 10종목 1행씩)
        per = dict(con.execute("SELECT as_of, count(*) FROM s GROUP BY 1 ORDER BY 1").fetchall())
        assert per[date(2010, 1, 4)] == 10 and per[date(2026, 8, 20)] == 41066
    finally:
        con.close()
    meta = json.loads((part.parent / catalog.ASOF_META).read_text(encoding="utf-8"))
    assert meta["snapshot_id"] == p.snapshot_id and meta["n_rows"] == ASOF_ROWS
    assert meta["asof_sample_dates"] == SAMPLE_DATES and meta["content_hash"].startswith(
        f"{ASOF_ROWS}:")


def test_두_번째_publish는_EG5c_pass이고_표본을_같은_snapshot에_덮어쓴다(
        published: tuple[Path, catalog.CatalogResult], tmp_path: Path) -> None:
    root = _copy_root(published, tmp_path)
    p2 = catalog.publish(root, seed())
    assert p2.ok and p2.snapshot_id == published[1].snapshot_id
    eg5c = _gate(p2, "EG5c")
    assert eg5c.status is GateStatus.PASS and eg5c.metrics["n_diff_total"] == 0
    v = eg5c.metrics["views"]["v_adj_price"]         # type: ignore[index]
    assert v["previous_snapshot_id"] == published[1].snapshot_id and v["n_diff"] == 0
    assert [d.name for d in (root / catalog.ASOF_DIR / "v_adj_price").iterdir()] == [
        p2.snapshot_id]


def test_EG5c_차이는_fail이고_rebase_asof로만_승인된다(
        published: tuple[Path, catalog.CatalogResult], tmp_path: Path) -> None:
    """직전 표본 parquet 을 한 행 빼고 다시 써서 '과거 as-of 가 바뀐' 상황을 만든다."""
    root = _copy_root(published, tmp_path)
    sid = published[1].snapshot_id
    part = root / catalog.ASOF_DIR / "v_adj_price" / sid / catalog.ASOF_PART
    con = duckdb.connect()
    try:
        con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{part}')")
        con.execute("DELETE FROM t WHERE as_of = DATE '2018-05-04' AND ticker = '005930' "
                    "AND date = DATE '2018-05-03'")
        con.execute("UPDATE t SET adj_close = adj_close * 2 WHERE as_of = DATE '2018-05-04' "
                    "AND ticker = '005935' AND date = DATE '2018-05-03'")
        con.execute(f"COPY t TO '{part}' (FORMAT PARQUET)")
    finally:
        con.close()
    meta_before = (root / catalog.META_NAME).read_text(encoding="utf-8")
    p = catalog.publish(root, seed())
    assert not p.ok
    eg5c = _gate(p, "EG5c")
    assert eg5c.status is GateStatus.FAIL and eg5c.metrics["n_diff_total"] == 2
    v = eg5c.metrics["views"]["v_adj_price"]         # type: ignore[index]
    assert v["diff_by_kind"] == {"changed": 1, "only_current": 1}
    assert v["diff_keys"] == ["2018-05-04|005930|2018-05-03|only_current",
                              "2018-05-04|005935|2018-05-03|changed"]
    assert _gate(p, "EG11").status is GateStatus.PASS
    assert (root / catalog.META_NAME).read_text(encoding="utf-8") == meta_before
    assert not list((root / "_tmp").glob("catalog_*"))
    # 사람 승인: 이번 표본이 새 기준이 된다
    p_ok = catalog.publish(root, seed(), rebase_asof=True)
    assert p_ok.ok and _gate(p_ok, "EG5c").detail.startswith("rebased")
    con = duckdb.connect()
    try:
        assert con.execute(f"SELECT count(*) FROM read_parquet('{part}')").fetchone() == (
            ASOF_ROWS,)
    finally:
        con.close()
    p3 = catalog.publish(root, seed())
    assert p3.ok and _gate(p3, "EG5c").metrics["n_diff_total"] == 0


def test_asof_스냅샷은_keep_개만_남는다(published: tuple[Path, catalog.CatalogResult],
                                 tmp_path: Path) -> None:
    """캘린더를 새 build_id 로 다시 지으면 snapshot_id 가 바뀐다 — 표본은 같아 EG5c pass."""
    root = _copy_root(published, tmp_path)
    sids = [published[1].snapshot_id]
    for i in range(3):
        r = build.build_table(rules_s02.TRADING_CALENDAR, STAGE_SLICE, root, seed(),
                              build_id=f"b_cal_{i}")
        assert r.ok
        p = catalog.publish(root, seed(), keep=3)
        assert p.ok and _gate(p, "EG5c").status is GateStatus.PASS
        sids.append(p.snapshot_id)
    assert len(set(sids)) == 4
    kept = sorted(d.name for d in (root / catalog.ASOF_DIR / "v_cum_adj").iterdir())
    assert kept == sorted(sids[1:])                    # 가장 오래된 첫 스냅샷만 지워졌다


# ── 부정 픽스처 FX-N-006 ─────────────────────────────────────────────────────

_DIVIDED = views.TEMPLATES["v_adj_volume"].replace(
    "p.volume_shr * c.cum_share_factor AS adj_volume",
    "p.volume_shr / c.cum_share_factor AS adj_volume")


def test_FX_N_006_거래량을_나누면_EG8만_fail하고_FX_2_010이_틀린다(
        published: tuple[Path, catalog.CatalogResult], tmp_path: Path, monkeypatch) -> None:
    """GATES §4 FX-N-006 — 방향 오류는 EG8-P03(조정 거래량 점프)에서만 잡힌다."""
    assert _DIVIDED != views.TEMPLATES["v_adj_volume"]
    root = _copy_root(published, tmp_path)
    monkeypatch.setitem(views.TEMPLATES, "v_adj_volume", _DIVIDED)
    r = build.build_table(rules_s06.ADJ_FACTOR, STAGE_SLICE, root, seed(), build_id="b_n006")
    assert r.status is build.BuildStatus.GATE_FAILED
    st = {g.name: g.status.value for g in r.gates}
    assert st["EG8"] == "fail" and st["EG3_adj_factor"] == "pass" and st["EG4"] == "skip"
    m = next(g for g in r.gates if g.name == "EG8").metrics
    # P03 집합 통계: 나눗셈이면 이벤트별 중앙값 비가 share_factor² (50:1 → 2,500 · 4 → 16) 로 튄다
    assert m["n_volume_ratio_out_of_band"] == 1 and m["adj_volume_ratio_median"] > 1000
    assert m["n_ok_volume_ratio_over_10"] == 3
    assert m["n_return_jump_over"] == 0                           # 가격 축은 멀쩡하다
    # FX-2-010: 같은 나눗셈 템플릿을 TEMP MACRO 로 올려 보면 04-27 조정 거래량이 606,216/50 이 된다
    con = duckdb.connect()
    try:
        src = {t: views.parquet_source(root, t) for t in ("price_daily", "adj_factor",
                                                          "trading_calendar")}
        views.install_temp_macros(con, src)
        assert con.execute("SELECT adj_volume FROM v_adj_volume(DATE '2018-06-01') WHERE "
                           "ticker = '005930' AND date = DATE '2018-04-27'").fetchone() == (
            606216 / 50,)
    finally:
        con.close()


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_CLI_catalog는_뷰_게이트와_asof를_출력한다(published: tuple[Path, catalog.CatalogResult],
                                       tmp_path: Path, capsys) -> None:
    root = _copy_root(published, tmp_path)
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps(seed().data, ensure_ascii=False), encoding="utf-8")
    code = main(["--root", str(root), "--stage-root", str(STAGE_SLICE), "--baseline", str(bl),
                 "catalog"])
    out = capsys.readouterr().out
    assert code == 0 and "ok catalog=" in out and "macros=4" in out
    assert "EG11  pass" in out and "EG5c  pass" in out and "EG3_firm_mktcap pass" in out
    assert f"_asof/v_adj_price: rows={ASOF_ROWS}" in out
