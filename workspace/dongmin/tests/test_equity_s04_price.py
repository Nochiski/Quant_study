"""S04 `price_daily` — stage 절단본 위 실제 `build_table` 왕복
(DESIGN §4-2 · GATES §3 ⑧ · EG7-P01 · EG20).

손계산 기대값은 `stg_price_daily`·`stg_etf_price_daily`·`stg_listing_daily` 원자료를 직접 열어
확인한 값이다(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  주식 36,972 + ETF 4,094 = 41,066 (교집합 0, 캘린더 밖 0) · `volume_shr = 0` 346행(전부 O/H/L NULL)
  · listing 이 주식 (ticker, date) 전건을 덮고 같은 날 `list_shrs` 가 가격 원장과 전부 같다
  · 무액면 1종(900050, 1,916행) · `open IS NULL ∧ volume > 0` 0.
`price_daily` 는 equity `trading_calendar` 를 입력으로 읽으므로 같은 equity_root 에 그것을 먼저
짓는다.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s02, rules_s04
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = Baseline({**load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json").data,
                 **load(rules_s04.BASELINE_SEED).data})

N_STOCK = 36972
N_ETF = 4094
N_ROWS = N_STOCK + N_ETF            # 41,066 — 두 원천 합, 교집합 0
N_YEARS = 17                        # 2010 ~ 2026
N_REFERENCE = 346                   # volume_shr = 0 인 주식 행 (ETF 0)
N_005930_PRE_SPLIT = 2060           # 005930 의 2018-05-04 이전 행수 (부정 픽스처 손계산)
N_NO_PAR = 1916                     # 900050 무액면 전 행
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_price_daily", "EG20", "EG4", "EG5a"]


def _build_calendar(equity_root: Path) -> build.BuildResult:
    r = build.build_table(rules_s02.TRADING_CALENDAR, STAGE_SLICE, equity_root, SEED,
                          build_id="b_s02_cal")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    return r


def _build_price(equity_root: Path, rule: EquityTable | None = None) -> build.BuildResult:
    return build.build_table(rule or rules_s04.PRICE_DAILY, STAGE_SLICE, equity_root, SEED,
                             build_id="b_s04_price")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    """정상 왕복 1회 — 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
    eq = tmp_path_factory.mktemp("s04") / "equity"
    _build_calendar(eq)
    return _build_price(eq)


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rules_s04.PRICE_DAILY.__dict__, "name": name, "sql_path": p})


def _body() -> str:
    return rules_s04.PRICE_DAILY.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW pd AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true)")
        for t in ("stg_price_daily", "stg_etf_price_daily", "stg_listing_daily"):
            con.execute(f"CREATE OR REPLACE TEMP VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{STAGE_SLICE / t}/**/*.parquet', hive_partitioning=true, "
                        "union_by_name=true)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    r = built
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == GATE_ORDER
    assert not [g for g in r.gates if g.status is GateStatus.FAIL]
    assert r.n_rows == N_ROWS and r.n_reject == 0
    eg1 = _gate(r, "EG1").metrics
    assert eg1["lhs"] == N_ROWS and eg1["rhs"] == N_ROWS
    assert _gate(r, "EG0").metrics["unpinned"] == []
    assert set(r.inputs) == {"stg_price_daily", "stg_etf_price_daily", "stg_listing_daily",
                             "trading_calendar"}
    assert r.inputs["trading_calendar"] == "b_s02_cal"   # equity 입력도 고정된다


def test_date_axis는_연도_디렉토리로_갈린다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert len(r.partitions) == N_YEARS
    assert sum(int(str(p["n_rows"])) for p in r.partitions) == N_ROWS
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE year <> year(date)") == [(0,)]


def test_원주가_거래량_거래대금은_stage와_전건_동일하다(built: build.BuildResult) -> None:
    """원칙 ②. 산출 6축 ↔ (주식 ∪ ETF) 원자료를 양방향 anti-join 해 짝 없는 행 0
    (EG20 과 독립 검사).

    EXCEPT 를 쓰지 않는 이유: duckdb 1.5.5 가 DECIMAL 폭이 다른 UNION ALL 위의 8컬럼 EXCEPT 에서
    같은 값의 행을 차집합으로 되돌린다(컬럼별 EXCEPT 는 전부 0). 조인은 값 비교라 그 함정이 없다.
    """
    r = built
    assert r.out_dir is not None
    stg = ("SELECT ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr, value_krw "
           "FROM stg_price_daily UNION ALL SELECT ticker, date, open_krw, high_krw, low_krw, "
           "close_krw, volume_shr, value_krw FROM stg_etf_price_daily")
    same = ("s.ticker = p.ticker AND s.date = p.date "
            "AND s.open_krw IS NOT DISTINCT FROM p.\"open\" "
            "AND s.high_krw IS NOT DISTINCT FROM p.high AND s.low_krw IS NOT DISTINCT FROM p.low "
            "AND s.close_krw IS NOT DISTINCT FROM p.\"close\" "
            "AND s.volume_shr IS NOT DISTINCT FROM p.volume_shr "
            "AND s.value_krw IS NOT DISTINCT FROM p.value_krw")
    assert _query(r.out_dir, f"SELECT count(*) FROM pd p WHERE NOT EXISTS "
                             f"(SELECT 1 FROM ({stg}) s WHERE {same})") == [(0,)]
    assert _query(r.out_dir, f"SELECT count(*) FROM ({stg}) s WHERE NOT EXISTS "
                             f"(SELECT 1 FROM pd p WHERE {same})") == [(0,)]
    # FX-2-002 — 50:1 분할 전후 불연속이 그대로 남는다
    assert _query(r.out_dir, "SELECT date, \"close\" FROM pd WHERE ticker = '005930' AND date IN "
                             "(DATE '2018-05-03', DATE '2018-05-04') ORDER BY date") == [
        (date(2018, 5, 3), Decimal(2650000)), (date(2018, 5, 4), Decimal(51900))]
    eg20 = _gate(r, "EG20")
    assert eg20.status is GateStatus.PASS and eg20.metrics["n_rows_checked"] == N_ROWS
    assert all(eg20.metrics[f"n_{c}_changed"] == 0 for c in eg20.metrics["raw_columns"])


def test_price_kind는_volume_부호로_갈리고_reference는_종가를_보존한다(
        built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT price_kind, count(*) FROM pd GROUP BY 1 ORDER BY 1") == [
        ("reference", N_REFERENCE), ("trade", N_ROWS - N_REFERENCE)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE (price_kind = 'reference') <> "
                             "(volume_shr = 0)") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE price_kind = 'reference' "
                             "AND (\"close\" IS NULL OR \"close\" <= 0)") == [(0,)]
    # FX-2-006 — 우리은행 폐지 전 거래정지 22일 · 분할 정지 05-03
    assert _query(r.out_dir, "SELECT \"close\", \"open\", volume_shr, price_kind FROM pd "
                             "WHERE ticker = '000030' AND date = DATE '2019-01-09'") == [
        (Decimal(14800), None, Decimal(0), "reference")]
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE ticker = '000030' "
                             "AND price_kind = 'reference'") == [(22,)]
    assert _query(r.out_dir, "SELECT price_kind FROM pd WHERE ticker = '005930' "
                             "AND date = DATE '2018-05-03'") == [("reference",)]
    m = _gate(r, "EG3_price_daily").metrics
    assert m["price_kind_counts"] == {"reference": N_REFERENCE, "trade": N_ROWS - N_REFERENCE}
    assert m["n_price_kind_null"] == 0 and m["n_reference_with_value"] == 0


def test_shares_out_par_는_같은날_listing이고_mktcap은_close_곱_shares(
        built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT date, shares_out, par_value_krw, mktcap_krw FROM pd "
                             "WHERE ticker = '005930' AND date IN (DATE '2018-05-03', "
                             "DATE '2018-05-04') ORDER BY date") == [
        (date(2018, 5, 3), Decimal(128386494), Decimal("5000.00"), Decimal(340224209100000)),
        (date(2018, 5, 4), Decimal(6419324700), Decimal("100.00"), Decimal(333162951930000))]
    # 시총은 KRX MKTCAP(stage 값)과 전건 일치 — 두 원장의 주식수가 같다는 독립 확인
    assert _query(r.out_dir, "SELECT count(*) FROM pd p "
                             "JOIN stg_price_daily s USING (ticker, date) "
                             "WHERE p.mktcap_krw IS DISTINCT FROM s.mktcap_krw "
                             "OR p.shares_out IS DISTINCT FROM s.list_shrs") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd p "
                             "JOIN stg_listing_daily l USING (ticker, date) "
                             "WHERE p.shares_out IS DISTINCT FROM l.list_shrs "
                             "OR p.par_value_krw IS DISTINCT FROM l.par_value_krw") == [(0,)]
    m = _gate(r, "EG3_price_daily").metrics
    assert m["n_shares_out_null_stock"] == 0 and m["n_shares_out_stage_mismatch"] == 0
    assert m["n_mktcap_stage_mismatch"] == 0


def test_ETF는_같은_테이블이고_shares는_ETF원장_par는_NULL(built: build.BuildResult) -> None:
    """FX-2-005. listing 원장에 ETF 는 없다(P8) — 상장좌수는 ETF 원장에서, 액면은 결측."""
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT \"close\", price_kind, shares_out, par_value_krw, mktcap_krw "
                             "FROM pd WHERE ticker = '069500' AND date = DATE '2010-01-04'") == [
        (Decimal(22710), "trade", Decimal(57300000), None, Decimal(1301283000000))]
    assert _query(r.out_dir, "SELECT count(*), count(*) FILTER (WHERE par_value_krw IS NULL), "
                             "count(*) FILTER (WHERE shares_out IS NULL) FROM pd "
                             "WHERE ticker = '069500'") == [(N_ETF, N_ETF, 0)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd p "
                             "JOIN stg_etf_price_daily e USING (ticker, date) "
                             "WHERE p.mktcap_krw IS DISTINCT FROM e.mktcap_krw") == [(0,)]
    assert _gate(r, "EG3_price_daily").metrics["n_shares_out_null_etf"] == 0


def test_결측은_결측으로_남는다(built: build.BuildResult) -> None:
    """무액면 par NULL(1,916) · 무거래일 O/H/L NULL(346). 채우면 원칙 ④ 위반이다."""
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE ticker = '900050' "
                             "AND par_value_krw IS NULL") == [(N_NO_PAR,)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE par_value_krw IS NULL "
                             "AND ticker <> '900050' AND ticker <> '069500'") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE \"open\" IS NULL") == [(N_REFERENCE,)]
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE \"open\" IS NULL "
                             "AND price_kind = 'trade'") == [(0,)]
    m = _gate(r, "EG3_price_daily").metrics
    assert m["n_par_value_null_stock"] == N_NO_PAR and m["n_close_null"] == 0


def test_PIT는_available_date가_date이고_basis는_default(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM pd WHERE available_date IS DISTINCT FROM date "
                             "OR available_basis IS DISTINCT FROM 'default'") == [(0,)]
    eg2 = _gate(r, "EG2")
    assert eg2.status is GateStatus.PASS
    assert eg2.metrics["n_available_null"] == 0 and eg2.metrics["n_available_before_content"] == 0
    assert eg2.metrics["basis_vocab"] == ["default"]
    assert eg2.metrics["content_date_column"] == "date"


def test_기록형_metric이_meta에_남는다(built: build.BuildResult) -> None:
    """GAP-14(open NULL ∧ volume>0)·격리 건수·원천 교집합은 통과 조건이 아니라 기록이다."""
    g = _gate(built, "EG3_price_daily")
    assert g.status is GateStatus.PASS
    assert g.metrics["n_open_null_volume_pos"] == 0
    assert g.metrics["n_off_calendar"] == 0 and g.metrics["n_nonpositive_price"] == 0
    assert g.metrics["n_src_overlap"] == 0 and g.metrics["n_ticker_bad_width"] == 0
    assert g.metrics["price_kind_vocab"] == ["trade", "reference"]
    assert _gate(built, "EG3").metrics["declared_reject_reasons"] == ["nonpositive_price",
                                                                       "off_calendar"]


# ── 부정 픽스처 — 실 절단본 위 SQL 변종 (게이트가 fail 을 내야 통과) ────────────

def test_조정된_close를_넣으면_EG20이_폐기한다(tmp_path: Path) -> None:
    """005930 분할 전 종가를 /50 로 소급 조정한다 — 행수·키·PIT 전부 정상이라 EG1~EG3 는 통과한다.

    손계산: 005930 의 2018-05-04 이전 행 = 2,060.
    """
    eq = tmp_path / "equity"
    _build_calendar(eq)
    rule = _variant(tmp_path, "price_adj_close", f"""
        WITH base AS (SELECT * FROM ({_body()}))
        SELECT base.* REPLACE (
            CASE WHEN base.ticker = '005930' AND base.date < DATE '2018-05-04'
                 THEN CAST(base."close" / 50 AS DECIMAL(9, 0)) ELSE base."close" END AS "close")
        FROM base""")
    r = _build_price(eq, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    for name in ("EG1", "EG3", "EG3_price_daily"):
        assert _gate(r, name).status is GateStatus.PASS, name
    eg20 = _gate(r, "EG20")
    assert eg20.status is GateStatus.FAIL
    assert eg20.metrics["n_close_changed"] == N_005930_PRE_SPLIT
    assert eg20.metrics["n_volume_shr_changed"] == 0 and eg20.metrics["n_open_changed"] == 0
    assert "n_close_changed" in eg20.detail
    assert _gate(r, "EG4").detail == "upstream_failed"


def test_조정된_거래량을_넣으면_EG20이_폐기한다(tmp_path: Path) -> None:
    """거래량 ×50 소급 조정. 종가는 그대로라 close 축은 0, volume 축만 2,060."""
    eq = tmp_path / "equity"
    _build_calendar(eq)
    rule = _variant(tmp_path, "price_adj_volume", f"""
        WITH base AS (SELECT * FROM ({_body()}))
        SELECT base.* REPLACE (
            CASE WHEN base.ticker = '005930' AND base.date < DATE '2018-05-04'
                 THEN CAST(base.volume_shr * 50 AS DECIMAL(13, 0)) ELSE base.volume_shr END
                 AS volume_shr)
        FROM base""")
    r = _build_price(eq, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg20 = _gate(r, "EG20")
    assert eg20.status is GateStatus.FAIL
    assert eg20.metrics["n_volume_shr_changed"] == N_005930_PRE_SPLIT - 3   # 정지 3일은 0×50=0
    assert eg20.metrics["n_close_changed"] == 0


def test_price_kind_어휘_밖이면_EG3_price_daily가_폐기한다(tmp_path: Path) -> None:
    eq = tmp_path / "equity"
    _build_calendar(eq)
    rule = _variant(tmp_path, "price_bad_kind", f"""
        WITH base AS (SELECT * FROM ({_body()}))
        SELECT base.* REPLACE (
            CASE WHEN base.price_kind = 'reference' THEN 'halt' ELSE base.price_kind END
                 AS price_kind)
        FROM base""")
    r = _build_price(eq, rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3").status is GateStatus.PASS
    g = _gate(r, "EG3_price_daily")
    assert g.status is GateStatus.FAIL and g.metrics["n_price_kind_outside_vocab"] == N_REFERENCE
    assert _gate(r, "EG20").detail == "upstream_failed"


# ── 부정 픽스처 — 가짜 stage 입력 (SQL 의 격리·NULL 처리를 입력 데이터로 검증) ──

D1, D2, D3 = date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)
D_SAT = date(2020, 1, 4)            # 캘린더(stg_index_daily) 에 없는 날


def _price_row(ticker: str, d: date, o: int | None, h: int | None, lo: int | None, c: int,
               vol: int | None, shrs: int) -> dict[str, object]:
    return {"ticker": ticker, "date": d, "open_krw": o, "high_krw": h, "low_krw": lo,
            "close_krw": c, "volume_shr": vol,
            "value_krw": 0 if not vol else vol * c, "mktcap_krw": c * shrs, "list_shrs": shrs}


def _listing_row(ticker: str, d: date, par: float | None, shrs: int) -> dict[str, object]:
    return {"ticker": ticker, "date": d, "par_value_krw": par, "list_shrs": shrs}


def _fake_build(tmp_path: Path, make_stage_tree, price_rows: list[dict[str, object]],
                etf_rows: list[dict[str, object]], listing_rows: list[dict[str, object]],
                fixture_key: dict[str, str], fixture_close: str) -> build.BuildResult:
    """가짜 stage 4테이블 → trading_calendar → price_daily. 픽스처는 가짜 데이터에 맞춰 tmp 에."""
    tree = make_stage_tree(tmp_path, "stg_index_daily",
                           [{"index_class": "KOSPI", "index_name": "코스피", "date": d}
                            for d in (D1, D2, D3)], "date_axis")
    make_stage_tree(tmp_path, "stg_price_daily", price_rows, "date_axis")
    make_stage_tree(tmp_path, "stg_etf_price_daily", etf_rows, "date_axis")
    make_stage_tree(tmp_path, "stg_listing_daily", listing_rows, "date_axis")
    eq = tmp_path / "equity"
    cal_fx = tmp_path / "cal_fx.json"
    cal_fx.write_text(json.dumps([{"key": {"date": D1.isoformat()}, "column": "prev_td",
                                   "expect": None, "source": "hand — 가짜 캘린더 첫날"}]),
                      encoding="utf-8")
    cal = build.build_table(rules_s02.TRADING_CALENDAR, tree.stage_root, eq, Baseline({}),
                            build_id="b_cal", fixtures_path=cal_fx)
    assert cal.ok, [(g.name, g.status.value, g.detail) for g in cal.gates]
    price_fx = tmp_path / "price_fx.json"
    price_fx.write_text(json.dumps([{"key": fixture_key, "column": "close", "expect": fixture_close,
                                     "source": "hand — 가짜 stage 1행"}]), encoding="utf-8")
    return build.build_table(rules_s04.PRICE_DAILY, tree.stage_root, eq, Baseline({}),
                             build_id="b_price", fixtures_path=price_fx,
                             gate_thresholds={"EG7": 1.0})


def test_close가_0_open이_음수_캘린더_밖_행은_격리되고_등식은_유지된다(
        tmp_path: Path, make_stage_tree) -> None:
    """EG7-P01 nonpositive_price 2 + off_calendar 1 → `_reject`. 나머지 4행이 본체, EG1 = 7 − 3.

    함께 검증: GAP-14(open NULL ∧ volume>0)는 격리가 아니라 기록 · volume NULL → price_kind NULL ·
    listing 이 없는 날은 shares_out·mktcap NULL(행은 남는다).
    """
    price_rows = [
        _price_row("000001", D1, 100, 110, 90, 105, 10, 1000),      # 정상 trade
        _price_row("000001", D2, None, None, None, 105, 0, 1000),   # reference (O/H/L NULL)
        _price_row("000001", D3, 100, 110, 90, 0, 5, 1000),         # close 0 → nonpositive_price
        _price_row("000002", D1, -1, 110, 90, 105, 5, 2000),        # open < 0 → nonpositive_price
        _price_row("000002", D_SAT, 100, 110, 90, 105, 5, 2000),    # 캘린더 밖 → off_calendar
        _price_row("000003", D1, None, 110, 90, 105, 5, 3000),      # GAP-14: open NULL ∧ volume>0
        _price_row("000003", D2, 100, 110, 90, 105, None, 3000),    # volume NULL → price_kind NULL
    ]
    etf_rows = [_price_row("069500", D1, 200, 210, 190, 205, 7, 500)]
    listing_rows = [_listing_row("000001", D1, 500.0, 1000),
                    _listing_row("000001", D2, 500.0, 1000),
                    _listing_row("000001", D3, 500.0, 1000),
                    _listing_row("000002", D1, 500.0, 2000),
                    _listing_row("000002", D_SAT, 500.0, 2000),
                    _listing_row("000003", D1, None, 3000)]      # 000003 D2 는 listing 없음
    r = _fake_build(tmp_path, make_stage_tree, price_rows, etf_rows, listing_rows,
                    {"ticker": "000001", "date": D1.isoformat()}, "105")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == 5 and r.n_reject == 3
    eg7 = _gate(r, "EG7").metrics
    assert eg7["reject_by_reason"] == {"nonpositive_price": 2, "off_calendar": 1}
    eg1 = _gate(r, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["n_reject"], eg1["delta"]) == (5, 8, 3, 0)
    m = _gate(r, "EG3_price_daily").metrics
    assert m["n_nonpositive_price"] == 2 and m["n_off_calendar"] == 1
    assert m["n_open_null_volume_pos"] == 1          # 000003 D1 — 격리되지 않고 기록만
    assert m["n_price_kind_null"] == 1               # 000003 D2
    assert m["n_shares_out_null_stock"] == 1         # 000003 D2 — listing 없음
    assert m["price_kind_counts"] == {"None": 1, "reference": 1, "trade": 3}
    assert r.out_dir is not None
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT ticker, date, price_kind, shares_out, mktcap_krw, par_value_krw FROM "
            f"read_parquet('{r.out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true) "
            "ORDER BY ticker, date").fetchall()
    finally:
        con.close()
    assert rows == [
        ("000001", D1, "trade", 1000, Decimal(105000), 500.0),
        ("000001", D2, "reference", 1000, Decimal(105000), 500.0),
        ("000003", D1, "trade", 3000, Decimal(315000), None),
        ("000003", D2, None, None, None, None),
        ("069500", D1, "trade", 500, Decimal(102500), None)]


def test_두_원천이_겹치면_키_중복으로_EG3가_폐기한다(tmp_path: Path, make_stage_tree) -> None:
    """같은 (ticker, date) 가 주식·ETF 원장 양쪽에 있으면 dedup 하지 않는다 — 행수 등식(EG1)은
    양쪽을 다 세므로 통과하고, 키 중복(EG3-P01)이 잡는다. §3 ⑧ 두 번째 식의 프레임 판."""
    price_rows = [_price_row("000001", D1, 100, 110, 90, 105, 10, 1000),
                  _price_row("069500", D1, 100, 110, 90, 105, 10, 1000)]   # ETF 티커가 주식 원장에
    etf_rows = [_price_row("069500", D1, 200, 210, 190, 205, 7, 500)]
    listing_rows = [_listing_row("000001", D1, 500.0, 1000)]
    r = _fake_build(tmp_path, make_stage_tree, price_rows, etf_rows, listing_rows,
                    {"ticker": "000001", "date": D1.isoformat()}, "105")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS      # 3 = 3 − 0: 등식은 겹침을 못 본다
    eg3 = _gate(r, "EG3")
    assert eg3.status is GateStatus.FAIL and eg3.metrics["n_duplicate_keys"] == 1
    assert _gate(r, "EG3_price_daily").detail == "upstream_failed"


def test_trading_calendar가_없으면_빌드가_예외로_멈춘다(tmp_path: Path) -> None:
    """equity 입력은 앞서 커밋된 테이블이어야 한다 — 빈 캘린더로 전량 격리하는 대신 멈춘다."""
    with pytest.raises(FileNotFoundError, match="trading_calendar"):
        _build_price(tmp_path / "equity")
