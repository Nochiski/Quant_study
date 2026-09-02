"""KRX 잔여 4테이블 손계산 빌드 — ETF·지수·상장마스터·수집로그 (DESIGN v2.2 §4 KRX).

픽스처 컬럼은 `survey_out/v2/krx.*.json` 의 원장 컬럼 전수와 같다(순서 무관, 전부 TEXT).
"""
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, rules, snapshot

COLLECTED = "2026-08-23T15:23:29"        # UTC 무표기 → KST 2026-08-24 (§3 observed_date)

ETF_COLS = ["BAS_DD", "ISU_CD", "ISU_NM", "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT", "NAV",
            "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP",
            "INVSTASST_NETASST_TOTAMT", "LIST_SHRS", "IDX_IND_NM", "OBJ_STKPRC_IDX",
            "CMPPREVDD_IDX", "FLUC_RT_IDX", "bas_dd_req", "collected_at"]
IDX_COLS = ["BAS_DD", "IDX_CLSS", "IDX_NM", "CLSPRC_IDX", "CMPPREVDD_IDX", "FLUC_RT",
            "OPNPRC_IDX", "HGPRC_IDX", "LWPRC_IDX", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP",
            "bas_dd_req", "collected_at"]
ISU_COLS = ["ISU_CD", "ISU_SRT_CD", "ISU_NM", "ISU_ABBRV", "ISU_ENG_NM", "LIST_DD", "MKT_TP_NM",
            "SECUGRP_NM", "SECT_TP_NM", "KIND_STKCERT_TP_NM", "PARVAL", "LIST_SHRS",
            "bas_dd_req", "collected_at"]
LOG_COLS = ["endpoint", "bas_dd", "n_rows", "status", "note", "collected_at"]

# ETF: 정상행 · 무거래일(O/H/L '0' → NULL) · 기초지수 빈값 · 영문 포함 티커(9999A9 실재)
ETF_ROWS = [
    ("20260820", "069500", "KODEX 200", "35000", "-150", "-0.43", "35012.34",
     "35100", "35200", "34900", "1000", "35000000", "350000000000",
     "351000000000", "10000000", "코스피 200", "3300.55", "-14.20", "-0.43",
     "20260820", COLLECTED),
    ("20260820", "0001A0", "ＡＢＣ　ETF", "10000", "0", "0.00", "10000.00",
     "0", "0", "0", "0", "0", "100000000",
     "100000000", "10000", "코스피 200", "", "", "",
     "20260820", COLLECTED),
]
# 지수: 업종지수명이 양시장에 중복 — IDX_CLSS 가 키에 없으면 충돌한다(실측 71,158쌍)
IDX_ROWS_KOSPI = [
    ("20260820", "KOSPI", "코스피", "3300.55", "-14.20", "-0.43", "3310.00", "3320.00",
     "3290.00", "500000000", "12000000000000", "2500000000000000", "20260820", COLLECTED),
    ("20260820", "KOSPI", "기계·장비", "1200.00", "5.00", "0.42", "1195.00", "1205.00",
     "1190.00", "1000000", "50000000000", "9000000000000", "20260820", COLLECTED),
]
IDX_ROWS_KOSDAQ = [
    ("20260820", "KOSDAQ", "기계·장비", "800.00", "-2.00", "-0.25", "802.00", "805.00",
     "799.00", "2000000", "30000000000", "4000000000000", "20260820", COLLECTED),
    # 지수 미산출일: 값 컬럼이 전부 빈값(실측 blank 4,094~24,930) → ledger_blank
    ("20260821", "KOSDAQ", "기계·장비", "", "", "", "", "", "", "", "", "",
     "20260821", COLLECTED),
]
# 상장마스터: ISU_CD 는 12자 ISIN · PARVAL 은 수치/비수치 혼재 · SECT_TP_NM 은 ksq 만 유효
ISU_ROWS_STK = [
    ("KR7005930003", "005930", "삼성전자", "삼성전자", "SAMSUNG ELECTRONICS", "19750611",
     "KOSPI", "주권", "", "보통주", "100", "5969782550", "20260820", COLLECTED),
    ("KR7950130008", "950130", "무액면사", "무액면사", "NO PAR CO", "20100104",
     "KOSPI", "주권", "", "보통주", "무액면", "1000000", "20260820", COLLECTED),
]
ISU_ROWS_KSQ = [
    ("KR7035720002", "035720", "카카오", "카카오", "KAKAO", "19991110",
     "KOSDAQ", "주권", "관리종목(소속부없음)", "보통주", "500.5", "89000000",
     "20260820", COLLECTED),
]
# G2: PARVAL 비수치(무액면) 1행 · G7: 1990 이전 상장일 1행 — 둘 다 3행 픽스처라 비율이 크다
_LISTING_THRESHOLDS: dict[str, float] = {"G2": 0.5, "G7": 0.5}
LOG_ROWS = [
    ("sto/stk_bydd_trd", "20260820", "944", "ok", None, COLLECTED),
    ("etp/etf_bydd_trd", "20260820", "0", "holiday", "skipped by price-probe", COLLECTED),
]


def _write(path: Path, tables: dict[str, tuple[list[str], Sequence[Sequence[object]]]]
           ) -> None:
    con = sqlite3.connect(path)
    for tbl, (cols, rows) in tables.items():
        con.execute(f"CREATE TABLE {tbl} ({', '.join(c + ' TEXT' for c in cols)})")
        con.executemany(f"INSERT INTO {tbl} VALUES ({','.join('?' * len(cols))})", rows)
    con.commit()
    con.close()


@pytest.fixture
def snap(tmp_path: Path) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir()
    _write(d / "krx.db", {
        "krx_etf_bydd_trd": (ETF_COLS, ETF_ROWS),
        "krx_kospi_dd_trd": (IDX_COLS, IDX_ROWS_KOSPI),
        "krx_kosdaq_dd_trd": (IDX_COLS, IDX_ROWS_KOSDAQ),
        "krx_stk_isu_base_info": (ISU_COLS, ISU_ROWS_STK),
        "krx_ksq_isu_base_info": (ISU_COLS, ISU_ROWS_KSQ),
        "ingest_log": (LOG_COLS, LOG_ROWS),
    })
    return snapshot.make_snapshot({"krx": d / "krx.db"}, tmp_path / "snapshots",
                                  snapshot_id="snap_krx")


def _build(name: str, snap: snapshot.Snapshot, tmp_path: Path, **kw: object
           ) -> build.BuildResult:
    return build.build_table(rules.RULES[name], snap, tmp_path / "stage", **kw)


def _read(tmp_path: Path, r: build.BuildResult, partitioned: bool = True
          ) -> duckdb.DuckDBPyConnection:
    root = tmp_path / "stage" / r.table / f"v={r.build_id}"
    glob = str(root / ("year=*/*.parquet" if partitioned else "*.parquet"))
    con = duckdb.connect()
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def _failed(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is gates.GateStatus.FAIL]


# ── stg_etf_price_daily ────────────────────────────────────────────────────────
def test_etf_rules_declare_etf_specific_columns_and_no_cross_check() -> None:
    rule = rules.RULES["stg_etf_price_daily"]
    mapped = {c.src for c in rule.columns}
    assert mapped == {c for c in ETF_COLS if c not in ("bas_dd_req", "collected_at")}
    assert rule.cross_check is None                       # 키움 ka10060 은 주식만 (§4)
    assert rule.column("nav_krw").src == "NAV"
    assert rule.column("netasset_krw").src == "INVSTASST_NETASST_TOTAMT"
    assert rule.partition_expr == "substr(BAS_DD, 1, 4)"
    assert rule.lag_known is True                          # 가격류 — 결정 ⑦


def test_etf_build_nulls_zero_ohl_and_keeps_close_and_blank_index(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_etf_price_daily", snap, tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT count(*) FROM t").fetchone() == (2,)
    row = con.execute("SELECT close_krw, open_krw, high_krw, low_krw, nav_krw, "
                      "miss_kind.open_krw, miss_kind.obj_close_idx, obj_close_idx "
                      "FROM t WHERE ticker = '0001A0'").fetchone()
    assert row == (10000, None, None, None, 10000.00, "ledger_zero", "ledger_blank", None)
    assert con.execute("SELECT open_krw, name, netasset_krw, observed_date, available_basis "
                       "FROM t WHERE ticker = '069500'").fetchall() == [
        (35100, "KODEX 200", 351000000000, date(2026, 8, 24), "default")]


def test_etf_build_normalizes_fullwidth_name_but_keeps_ticker_text(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_etf_price_daily", snap, tmp_path)
    con = _read(tmp_path, r)
    assert con.execute("SELECT name FROM t WHERE ticker = '0001A0'").fetchone() == ("ABC ETF",)


# ── stg_index_daily ────────────────────────────────────────────────────────────
def test_index_rules_key_includes_idx_clss_and_uses_the_real_column_name() -> None:
    rule = rules.RULES["stg_index_daily"]
    assert rule.natural_key == ("index_class", "index_name", "date")
    assert rule.column("index_name").src == "IDX_NM"       # `index_name` 은 원장에 없다
    assert rule.column("index_class").expected_len is None  # KOSPI 5 / KOSDAQ 6 — 길이 상이


def test_index_build_keeps_both_markets_for_a_shared_sector_index_name(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_index_daily", snap, tmp_path)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    assert con.execute("SELECT count(*) FROM t").fetchone() == (4,)
    same = con.execute("SELECT index_class, _src, close_idx FROM t "
                       "WHERE index_name = '기계·장비' AND date = DATE '2026-08-20' "
                       "ORDER BY index_class").fetchall()
    assert same == [("KOSDAQ", "kosdaq", 800.00), ("KOSPI", "kospi", 1200.00)]
    assert con.execute("SELECT count(*) FROM (SELECT index_class, index_name, date "
                       "FROM t GROUP BY ALL HAVING count(*) > 1)").fetchone() == (0,)


def test_index_build_marks_a_non_published_day_as_ledger_blank(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_index_daily", snap, tmp_path)
    con = _read(tmp_path, r)
    row = con.execute("SELECT close_idx, miss_kind.close_idx, volume_shr, _src_flag FROM t "
                      "WHERE date = DATE '2026-08-21'").fetchone()
    assert row == (None, "ledger_blank", None, "ok")       # 정상 결측이라 partial 이 아니다


# ── stg_listing_daily ──────────────────────────────────────────────────────────
def test_listing_rules_never_use_the_12_char_isin_as_ticker() -> None:
    rule = rules.RULES["stg_listing_daily"]
    assert rule.column("ticker").src == "ISU_SRT_CD" and rule.column("ticker").expected_len == 6
    assert rule.column("isin").src == "ISU_CD" and rule.column("isin").expected_len == 12
    assert rule.partition_expr == "substr(bas_dd_req, 1, 4)"
    assert rule.lag_known is False                         # 마스터 = 공표 시점 미상 (§6)


def test_listing_build_splits_par_value_into_number_and_kind(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_listing_daily", snap, tmp_path, gate_thresholds=_LISTING_THRESHOLDS)
    assert r.ok, _failed(r)
    con = _read(tmp_path, r)
    got = con.execute("SELECT ticker, par_value_krw, par_value_kind, miss_kind.par_value_krw, "
                      "_src_flag FROM t ORDER BY ticker").fetchall()
    assert got == [("005930", 100.00, "numeric", None, "ok"),
                   ("035720", 500.50, "numeric", None, "ok"),
                   ("950130", None, "무액면", "cast_failed", "partial")]
    assert next(g for g in r.gates if g.name == "G2").metrics["n_partial"] == 1


def test_listing_build_flags_sect_tp_availability_per_market(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_listing_daily", snap, tmp_path, gate_thresholds=_LISTING_THRESHOLDS)
    con = _read(tmp_path, r)
    assert con.execute("SELECT sect_available, sect_tp, isin, _src FROM t "
                       "WHERE ticker = '035720'").fetchone() == (
        True, "관리종목(소속부없음)", "KR7035720002", "ksq")
    assert con.execute("SELECT count(*) FROM t WHERE _src = 'stk' AND sect_available"
                       ).fetchone() == (0,)
    assert con.execute("SELECT list_date FROM t WHERE ticker = '035720'").fetchall() == [
        (date(1999, 11, 10),)]


def test_listing_build_isolates_a_pre_1990_list_date_as_out_of_range(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    """G7 내용일 축 하한 1990 이 1990 이전 상장일을 셀 격리한다 — 빌더 임계 결함(보고서 참조).

    삼성전자 상장일 19750611 은 원장의 정상값인데 NULL 이 된다. 규칙 선언이 아니라
    `gates.YEAR_RANGE_CONTENT` 의 하한 문제라 여기서는 현재 동작을 고정만 해 둔다.
    """
    r = _build("stg_listing_daily", snap, tmp_path, gate_thresholds=_LISTING_THRESHOLDS)
    con = _read(tmp_path, r)
    assert con.execute("SELECT list_date, miss_kind.list_date FROM t WHERE ticker = '005930'"
                       ).fetchone() == (None, "out_of_range")
    assert next(g for g in r.gates if g.name == "G7").metrics["n_out_of_range_cells"] == 1


# ── stg_ingest_krx ─────────────────────────────────────────────────────────────
def test_ingest_krx_builds_a_whole_partition_without_available_date(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_ingest_krx", snap, tmp_path)
    assert r.ok, _failed(r)
    assert (tmp_path / "stage" / "stg_ingest_krx" / f"v={r.build_id}" / "part0.parquet").exists()
    con = _read(tmp_path, r, partitioned=False)
    got = con.execute("SELECT endpoint, n_rows, status, note, available_date, available_basis "
                      "FROM t ORDER BY endpoint").fetchall()
    assert got == [("etp/etf_bydd_trd", 0, "holiday", "skipped by price-probe", None, None),
                   ("sto/stk_bydd_trd", 944, "ok", None, None, None)]
    assert next(g for g in r.gates if g.name == "G6").status is gates.GateStatus.SKIP
