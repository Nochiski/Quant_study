"""stg_wics_components — wiseindex `wics_raw` blob 언네스트 (플랜 wics-weekly T2).

원문 모양은 2026-09-18 서버 스냅샷 실측(WICS_PROBE §8): zlib+JSON `{"info": {..., "CNT"}, "sector": [...], "list": [...]}`.
"""
import json
import sqlite3
import zlib
from pathlib import Path

import duckdb

from stage import build, gates, manifest, parsers, rules, snapshot

DT = "20260918"
AT = "2026-09-19T18:00:05"          # UTC → KST 09-20 03:00


def _z(obj: object) -> bytes:
    return zlib.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _row(cmp: str, idx_cd: str, idx_nm: str, sec_cd: str, sec_nm: str, mkt_val: int = 7096499, shr: int = 55011) -> dict:
    return {"IDX_CD": idx_cd, "IDX_NM_KOR": idx_nm, "ALL_MKT_VAL": 289313768, "CMP_CD": cmp, "CMP_KOR": "삼성SDI",
            "MKT_VAL": mkt_val, "WGT": 45.04, "S_WGT": 45.04, "CAL_WGT": 1, "SEC_CD": sec_cd, "SEC_NM_KOR": sec_nm,
            "SEC_WGT": 5.45, "TOP60": 2, "APT_SHR_CNT": shr}


def _body(rows: list[dict], cnt: int | None = None) -> bytes:
    return _z({"info": {"TRD_DT": "/Date(1789000000000)/", "MKT_VAL": 1, "TRD_AMT": 1, "CNT": len(rows) if cnt is None else cnt},
               "sector": [], "list": rows})


L1_IT = [_row("006400", "G45", "WICS IT", "G45", "IT"), _row("034220", "G45", "WICS IT", "G45", "IT"),
         _row("005930", "G45", "WICS IT", "G45", "IT")]
L2_4535 = [_row("006400", "G4535", "WICS 전자와 전기제품", "G45", "IT")]
L2_4540 = [_row("034220", "G4540", "WICS 디스플레이", "G45", "IT")]
L2_4530 = [_row("005930", "G4530", "WICS 반도체와반도체장비", "G45", "IT", mkt_val=3_500_000_000, shr=5_969_782_550)]


def _write_wics(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE wics_raw (dt TEXT NOT NULL, sec_cd TEXT NOT NULL, collected_at TEXT NOT NULL, "
                "http_status INTEGER NOT NULL, n_rows INTEGER, body BLOB NOT NULL, PRIMARY KEY (dt, sec_cd, collected_at))")
    con.executemany("INSERT INTO wics_raw VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _snap(tmp_path: Path, rows: list[tuple]) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    p = d / "wiseindex.db"
    _write_wics(p, rows)
    return snapshot.make_snapshot({"wiseindex": p}, tmp_path / "snapshots", snapshot_id="s")


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    root = tmp_path / "stage" / r.table / f"v={r.build_id}"
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{root}/year=*/*.parquet', hive_partitioning=true)")
    return con


ROWS = [
    (DT, "G45", AT, 200, 3, _body(L1_IT)),
    (DT, "G4535", AT, 200, 1, _body(L2_4535)),
    (DT, "G4540", AT, 200, 1, _body(L2_4540)),
    (DT, "G4530", AT, 200, 1, _body(L2_4530)),
    (DT, "G2540", AT, 200, 0, _body([])),                       # 빈 응답 판본 — select_sql 이 거른다
    (DT, "G4530", "2026-09-19T17:00:00", 200, 1, _body([_row("005930", "G4530", "OLD", "G45", "IT")])),   # 옛 판본
    ("20260920", "G45", "2026-09-20T00:00:00", 200, 0, _body([])),   # 휴장일 프로브 — 0행
]


def test_parser_unnests_list_and_counts_cnt_mismatch() -> None:
    blobs = [parsers.RawBlob("G4535", "GetIndexComponets", DT, DT, _body(L2_4535), AT),
             parsers.RawBlob("G45", "GetIndexComponets", DT, DT, _body(L1_IT, cnt=99), AT),      # CNT ≠ len
             parsers.RawBlob("G2540", "GetIndexComponets", DT, DT, _body([]), AT),
             parsers.RawBlob("G9999", "GetIndexComponets", DT, DT, b"not json", AT)]
    res = parsers.parse_wics_components(blobs)
    assert res.columns == parsers.WICS_COLUMNS
    assert res.metrics == {"n_blobs": 4, "n_empty_blobs": 1, "n_rows_emitted": 4, "n_parse_failed": 1, "n_value_mismatch": 1}
    first = res.rows[0]
    assert first["req_sec_cd"] == "G4535" and first["dt"] == DT and first["idx_nm"] == "WICS 전자와 전기제품"
    assert first["sec_cd"] == "G45" and first["mkt_val"] == "7096499" and first["apt_shr_cnt"] == "55011"


def _fixtures(tmp_path: Path) -> Path:
    """G4: unit_scale 컬럼(백만원 → 원)은 골든 픽스처가 스케일을 고정해야 한다 — 서버 fixture 와 같은 모양."""
    fx = tmp_path / "fixtures.json"
    fx.write_text(json.dumps([
        {"key": {"ticker": "005930", "date": "2026-09-18", "req_sec_cd": "G4530"}, "column": "float_mktcap_krw",
         "expect": str(3_500_000_000 * 1_000_000)},
        {"key": {"ticker": "005930", "date": "2026-09-18", "req_sec_cd": "G4530"}, "column": "all_float_mktcap_krw",
         "expect": str(289_313_768 * 1_000_000)},
        {"key": {"ticker": "005930", "date": "2026-09-18", "req_sec_cd": "G4530"}, "column": "idx_nm",
         "expect": "WICS 반도체와반도체장비"},
    ], ensure_ascii=False), encoding="utf-8")
    return fx


def test_build_keeps_l1_and_l2_rows_with_latest_version_only(tmp_path: Path) -> None:
    snap = _snap(tmp_path, ROWS)
    r = build.build_table(rules.RULES["stg_wics_components"], snap, tmp_path / "stage", fixtures_path=_fixtures(tmp_path))
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    g8 = next(g for g in r.gates if g.name == "G8")
    assert g8.status is gates.GateStatus.PASS and g8.metrics["n_rows_emitted"] == 6      # L1 3 + L2 3 (옛 판본·빈 응답 제외)
    con = _read(tmp_path, r)
    assert con.execute("SELECT count(*), count(DISTINCT ticker) FROM t").fetchone() == (6, 3)
    assert con.execute("SELECT count(*) FROM (SELECT ticker, date, req_sec_cd FROM t GROUP BY 1,2,3 HAVING count(*) > 1)").fetchone()[0] == 0
    # 같은 종목이 L1 행과 L2 행으로 공존하고, L2 라벨은 idx_nm 에서 온다
    rows = con.execute("SELECT req_sec_cd, idx_nm, sec_cd, float_mktcap_krw, float_shares_shr FROM t WHERE ticker = '005930' ORDER BY req_sec_cd").fetchall()
    assert [x[0] for x in rows] == ["G45", "G4530"]
    assert rows[1][1] == "WICS 반도체와반도체장비" and rows[1][2] == "G45"
    assert rows[1][3] == 3_500_000_000 * 1_000_000 and rows[1][4] == 5_969_782_550      # 백만원 → 원, 주
    assert con.execute("SELECT count(*) FROM t WHERE idx_nm = 'OLD'").fetchone()[0] == 0   # 옛 판본은 안 쓴다
    assert str(con.execute("SELECT max(date) FROM t").fetchone()[0]) == "2026-09-18"
    assert str(con.execute("SELECT max(available_date) FROM t").fetchone()[0]) == "2026-09-18"
    rec = next(b for b in manifest.load(tmp_path / "stage" / r.table / "MANIFEST.json").builds if b.build_id == r.build_id)
    assert rec.max_available_date == "2026-09-18"             # C6 신선도 축
    con.close()


def test_rule_is_registered_and_freshness_declared() -> None:
    from stage import freshness
    assert "stg_wics_components" in rules.RULES and rules.LEDGER_FILES["wiseindex"] == "wiseindex.db"
    assert freshness.judgement("stg_wics_components") == (14, "")
