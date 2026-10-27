"""연구 DB 읽기 — MANIFEST current_build 만 읽고, 이름·코드로 종목·지수를 찾는다."""

import json
from pathlib import Path

import duckdb
import numpy as np
import pytest

from technical_analysis import data


def write_table(root: Path, table: str, builds: dict[str, str], current: str) -> None:
    """builds: build_id → SELECT 문. 각 판을 v=<build>/part0.parquet 로 쓰고 MANIFEST 를 만든다."""
    con = duckdb.connect()
    parts = []
    for build, select in builds.items():
        d = root / table / f"v={build}"
        d.mkdir(parents=True)
        con.execute(f"COPY ({select}) TO '{d / 'part0.parquet'}' (FORMAT parquet)")
        parts.append({"build_id": build, "partitions": [{"path": f"v={build}"}]})
    (root / table / "MANIFEST.json").write_text(json.dumps({"table": table, "current_build": current, "builds": parts}))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_table(
        tmp_path,
        "security",
        {
            "s1": """SELECT * FROM (VALUES
                ('005930','삼성전자보통주','삼성전자','common', DATE '1975-06-11'),
                ('005935','삼성전자1우선주','삼성전자우','preferred', DATE '1989-09-25'),
                ('036220','오상헬스케어','오상헬스케어','common', DATE '2024-03-13'),
                ('069500','KODEX 200','KODEX 200','etf', NULL))
                t(ticker,name_current,name_abbrv_current,sec_type,list_date)"""
        },
        "s1",
    )
    rows = (
        "('005930', DATE '2026-10-21', NULL, NULL, NULL, NULL, 0.0), "  # 거래정지일 — 가격 빈 행
        "('005930', DATE '2026-10-22', 1.0, 2.0, 0.5, 1.5, 10.0), ('005930', DATE '2026-10-23', 1.5, 2.5, 1.0, 2.0, 20.0)"
    )
    old = "('005930', DATE '2026-10-22', 9.0, 9.0, 9.0, 9.0, 9.0)"
    reused = "('036220', DATE '2020-01-02', 5.0, 5.0, 5.0, 5.0, 1.0), ('036220', DATE '2024-03-13', 7.0, 8.0, 6.0, 7.5, 3.0)"
    cols = "t(ticker,date,adj_open,adj_high,adj_low,adj_close,adj_volume_shr)"
    write_table(
        tmp_path,
        "price_adj_daily",
        {"old": f"SELECT * FROM (VALUES {old}) {cols}", "new": f"SELECT * FROM (VALUES {rows}, {reused}) {cols}"},
        "new",
    )
    # 원주가: 마지막 날 종가 4.0(수정주가 2.0 의 2배), 거래량 10(수정 20 의 절반)
    write_table(
        tmp_path,
        "price_daily",
        {
            "p1": """SELECT * FROM (VALUES ('005930', DATE '2026-10-23', 4.0, 10.0), ('036220', DATE '2024-03-13', 7.5, 3.0))
                t(ticker,date,close,volume_shr)"""
        },
        "p1",
    )
    write_table(
        tmp_path,
        "index_daily",
        {
            "i1": """SELECT * FROM (VALUES
                ('KOSPI','코스피', DATE '2026-10-23', 10.0, 11.0, 9.0, 10.5, 100.0),
                ('KOSPI','코스피 200', DATE '2026-10-23', 1.0, 1.1, 0.9, 1.05, 50.0))
                t(index_class,index_name,date,open_idx,high_idx,low_idx,close_idx,volume_shr)"""
        },
        "i1",
    )
    return tmp_path


def test_reads_only_current_build_and_drops_rows_without_prices(root: Path) -> None:
    s = data.load(root, data.Match(kind="stock", code="005930", name="삼성전자"))
    assert s.dates.tolist() == [np.datetime64("2026-10-22"), np.datetime64("2026-10-23")]


def test_stock_prices_are_rescaled_so_last_bar_matches_raw_close(root: Path) -> None:
    # 원장 수정주가는 전방 조정이다. 비율은 그대로 두고 마지막 봉이 실제 종가가 되게 맞춘다
    s = data.load(root, data.Match(kind="stock", code="005930", name="삼성전자"))
    np.testing.assert_allclose(s.bars.close, [3.0, 4.0])
    np.testing.assert_allclose(s.bars.high, [4.0, 5.0])
    np.testing.assert_allclose(s.bars.volume, [5.0, 10.0])


def test_stock_series_starts_at_current_listing(root: Path) -> None:
    # 티커 재사용: 036220 의 2020 행은 지금 회사(2024-03-13 상장)가 아니다
    s = data.load(root, data.Match(kind="stock", code="036220", name="오상헬스케어"))
    assert s.dates.tolist() == [np.datetime64("2024-03-13")]


def test_index_series(root: Path) -> None:
    s = data.load(root, data.Match(kind="index", code="코스피", name="코스피"))
    np.testing.assert_allclose(s.bars.close, [10.5])


def test_lookup_exact_short_name_wins(root: Path) -> None:
    r = data.lookup(root, "삼성전자")
    assert r.status is data.LookupStatus.OK
    assert r.match is not None and r.match.code == "005930"


def test_lookup_by_code(root: Path) -> None:
    r = data.lookup(root, "005935")
    assert r.ok and r.match is not None and r.match.name == "삼성전자우"


def test_lookup_index_exact_name(root: Path) -> None:
    r = data.lookup(root, "코스피")
    assert r.ok and r.match is not None and r.match.kind == "index"


def test_lookup_ambiguous_lists_candidates(root: Path) -> None:
    r = data.lookup(root, "삼성")
    assert r.status is data.LookupStatus.AMBIGUOUS
    assert {m.code for m in r.candidates} == {"005930", "005935"}


def test_lookup_not_found(root: Path) -> None:
    r = data.lookup(root, "없는회사")
    assert r.status is data.LookupStatus.NOT_FOUND
    assert r.match is None
