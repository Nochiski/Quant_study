"""S1 — stage 빌더 뼈대 + stg_price_daily. 손계산 KRX·키움 픽스처 (DESIGN v2.2 §2·§3·§4·§9)."""
import json
import sqlite3
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, manifest, rules, snapshot

KRX_COLS = ["BAS_DD", "ISU_CD", "ISU_NM", "MKT_NM", "SECT_TP_NM", "TDD_CLSPRC", "CMPPREVDD_PRC",
            "FLUC_RT", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP",
            "LIST_SHRS", "bas_dd_req", "collected_at"]


def _row(bas_dd: str, isu: str, name: str, mkt: str, close: str, o: str, h: str, lo: str,
         vol: str, shrs: str, collected: str = "2026-08-23T15:23:29", mktcap: str | None = None,
         sect: str = "") -> tuple[str, ...]:
    close_i = int(close) if close.isdigit() else 0   # 비숫자 종가(캐스트 실패 픽스처) 허용
    cap = mktcap if mktcap is not None else str(close_i * int(shrs))
    return (bas_dd, isu, name, mkt, sect, close, "0", "0.00", o, h, lo, vol,
            str(int(vol) * close_i), cap, shrs, bas_dd, collected)


STK_ROWS = [
    _row("20180503", "005930", "삼성전자", "KOSPI", "2650000", "2650000", "2660000", "2640000",
         "100", "10"),
    _row("20180504", "005930", "삼성전자", "KOSPI", "51900", "53000", "53900", "51800",
         "500", "500"),
    # 무거래일: O/H/L '0', 거래량 0 → OHL NULL(ledger_zero), 종가 보존
    _row("20160309", "004200", "고려개발", "KOSPI", "4500", "0", "0", "0", "0", "20"),
    # 정지행 예외: OHL '0' 인데 거래량 > 0 (SPEC DEFECT-001 계열, 회귀 125행의 원형)
    _row("20160310", "004200", "고려개발", "KOSPI", "9000", "0", "0", "0", "7", "8"),
    # 영문 포함 티커 — zfill/int 금지 검증. 전각 이름 → NFKC
    _row("20260820", "0001A0", "ＡＢＣ　우선주", "KOSPI", "1000", "1000", "1000", "1000", "1", "1"),
]
KSQ_ROWS = [
    _row("20180503", "035720", "카카오", "KOSDAQ", "10000", "9900", "10100", "9800", "10", "3",
         sect="관리종목(소속부없음)"),
    # 자정 직전 UTC 수집 → KST 로는 다음 날 (observed_date 하루 이동 검증)
    _row("20180504", "035720", "카카오", "KOSDAQ", "10100", "10000", "10200", "9900", "10", "3",
         collected="2026-08-23T18:41:10"),
]
BAD_ROW = _row("20180507", "035720", "카카오", "KOSDAQ", "abc", "10000", "10200", "9900", "10", "3")


def _write_krx(path: Path, stk: list, ksq: list) -> None:
    con = sqlite3.connect(path)
    for t, rows in (("krx_stk_bydd_trd", stk), ("krx_ksq_bydd_trd", ksq)):
        con.execute(f"CREATE TABLE {t} ({', '.join(c + ' TEXT' for c in KRX_COLS)})")
        con.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(KRX_COLS))})", rows)
    con.commit()
    con.close()


def _write_kiwoom(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ka10060_investor_flows (ticker TEXT, dt TEXT, cur_prc TEXT, "
                "acc_trde_prica TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO ka10060_investor_flows VALUES (?,?,?,?,?)", [
        ("005930", "20180503", "-2650000", "100", "x"),   # 부호 접두 = 방향 표시자 → abs 일치
        ("005930", "20180504", "+51900", "500", "x"),
        ("035720", "20180503", "10000", "11", "x"),       # 거래량 1 차이 → 거래량 불일치 1건
    ])
    con.commit()
    con.close()


@pytest.fixture
def raw(tmp_path: Path) -> dict[str, Path]:
    d = tmp_path / "raw"
    d.mkdir()
    _write_krx(d / "krx.db", STK_ROWS, KSQ_ROWS)
    _write_kiwoom(d / "kiwoom.db")
    return {"krx": d / "krx.db", "kiwoom": d / "kiwoom.db"}


@pytest.fixture
def snap(raw: dict[str, Path], tmp_path: Path) -> snapshot.Snapshot:
    return snapshot.make_snapshot(raw, tmp_path / "snapshots", snapshot_id="snap_test")


def _built(snap: snapshot.Snapshot, tmp_path: Path, **kw: object) -> build.BuildResult:
    return build.build_table(rules.RULES["stg_price_daily"], snap, tmp_path / "stage", **kw)


def _read(stage_root: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(stage_root / "stg_price_daily" / f"v={r.build_id}" / "year=*" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


# ── rules ──────────────────────────────────────────────────────────────────────
def test_rules_price_daily_maps_every_source_column_and_excludes_request_axis() -> None:
    rule = rules.RULES["stg_price_daily"]
    assert [s.table for s in rule.sources] == ["krx_stk_bydd_trd", "krx_ksq_bydd_trd"]
    mapped = sorted(c.src for c in rule.columns)
    assert mapped == sorted(c for c in KRX_COLS if c not in ("bas_dd_req", "collected_at"))
    assert rule.natural_key == ("ticker", "date")
    assert rule.partition_expr == "substr(BAS_DD, 1, 4)"
    assert rule.observed_src == "collected_at"
    assert rule.payload_exclude == ("bas_dd_req", "collected_at")
    assert rule.column("ticker").expected_len == 6
    assert rule.column("close_krw").precision == 9 and rule.column("close_krw").scale == 0


# ── snapshot ───────────────────────────────────────────────────────────────────
def test_snapshot_copies_ledgers_by_vacuum_into(raw: dict[str, Path], tmp_path: Path) -> None:
    s = snapshot.make_snapshot(raw, tmp_path / "snapshots", snapshot_id="snap_x")
    assert s.snapshot_id == "snap_x"
    assert set(s.files) == {"krx", "kiwoom"}
    assert s.files["krx"].bytes > 0
    con = sqlite3.connect(s.files["krx"].path)
    assert con.execute("SELECT count(*) FROM krx_stk_bydd_trd").fetchone()[0] == len(STK_ROWS)
    con.close()


# ── build ──────────────────────────────────────────────────────────────────────
def test_build_unions_sources_with_src_tag(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    r = _built(snap, tmp_path)
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    con = _read(tmp_path / "stage", r)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 7
    assert con.execute("SELECT _src, count(*) FROM t GROUP BY 1 ORDER BY 1").fetchall() == [
        ("ksq", 2), ("stk", 5)]
    assert con.execute("SELECT count(*) FROM t WHERE market <> upper(_src) AND NOT "
                       "(market='KOSPI' AND _src='stk') AND NOT (market='KOSDAQ' AND _src='ksq')"
                       ).fetchone()[0] == 0


def test_build_converts_collected_at_from_utc_to_kst_date(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _built(snap, tmp_path)
    con = _read(tmp_path / "stage", r)
    got = con.execute("SELECT date, observed_date FROM t WHERE ticker='035720' ORDER BY date"
                      ).fetchall()
    assert [str(d) for _, d in got] == ["2026-08-24", "2026-08-24"]  # 15:23Z·18:41Z 모두 KST 08-24
    assert str(got[0][0]) == "2018-05-03"


def test_build_nulls_zero_ohl_independently_and_keeps_close(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _built(snap, tmp_path)
    con = _read(tmp_path / "stage", r)
    row = con.execute("SELECT close_krw, open_krw, high_krw, low_krw, volume_shr,"
                      " miss_kind.open_krw, miss_kind.close_krw FROM t"
                      " WHERE ticker='004200' AND date=DATE '2016-03-10'").fetchone()
    assert row == (9000, None, None, None, 7, "ledger_zero", None)
    assert con.execute("SELECT count(*) FROM t WHERE open_krw IS NULL AND volume_shr > 0"
                       ).fetchone()[0] == 1


def test_build_flags_cast_failure_as_partial_row(raw: dict[str, Path], tmp_path: Path) -> None:
    _write_krx(raw["krx"].with_name("krx2.db"), STK_ROWS, KSQ_ROWS + [BAD_ROW])
    s = snapshot.make_snapshot({"krx": raw["krx"].with_name("krx2.db"), "kiwoom": raw["kiwoom"]},
                               tmp_path / "snapshots", snapshot_id="snap_bad")
    r = _built(s, tmp_path, gate_thresholds={"G2": 1.0})
    con = _read(tmp_path / "stage", r)
    bad = con.execute("SELECT close_krw, _src_flag, _cast_fail_cols, miss_kind.close_krw FROM t "
                      "WHERE ticker='035720' AND date=DATE '2018-05-07'").fetchone()
    assert bad == (None, "partial", ["close_krw"], "cast_failed")
    assert con.execute("SELECT count(*) FROM t WHERE _src_flag='ok'").fetchone()[0] == 7


def test_build_normalizes_text_columns_with_nfkc(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    r = _built(snap, tmp_path)
    con = _read(tmp_path / "stage", r)
    assert con.execute("SELECT name FROM t WHERE ticker='0001A0'").fetchone()[0] == "ABC 우선주"
    assert con.execute("SELECT sect_tp, sect_available FROM t WHERE ticker='035720' "
                       "AND date=DATE '2018-05-03'").fetchone() == ("관리종목(소속부없음)", True)
    stk = con.execute("SELECT sect_available FROM t WHERE ticker='005930' LIMIT 1").fetchone()
    assert stk[0] is False


def test_build_partitions_by_year_and_commits_manifest(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _built(snap, tmp_path)
    root = tmp_path / "stage" / "stg_price_daily"
    years = sorted(p.name for p in (root / f"v={r.build_id}").glob("year=*"))
    assert years == ["year=2016", "year=2018", "year=2026"]
    m = manifest.load(root / "MANIFEST.json")
    assert m.current_build == r.build_id
    assert m.builds[-1].snapshot_id == "snap_test"
    assert {g.name: g.status.value for g in r.gates}["G5"] == "skip"
    assert (root / f"v={r.build_id}" / "year=2018" / "_meta.json").exists()


def test_rebuild_from_same_snapshot_is_reproducible(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    a = _built(snap, tmp_path, build_id="b1")
    b = _built(snap, tmp_path, build_id="b2")
    assert a.content_hash == b.content_hash
    assert a.n_rows == b.n_rows == 7


def test_manifest_gc_keeps_only_three_builds(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    for i in range(4):
        _built(snap, tmp_path, build_id=f"b{i}")
    root = tmp_path / "stage" / "stg_price_daily"
    assert sorted(p.name for p in root.glob("v=*")) == ["v=b1", "v=b2", "v=b3"]
    assert manifest.load(root / "MANIFEST.json").current_build == "b3"


# ── gates ──────────────────────────────────────────────────────────────────────
def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


def test_gate_g1_row_equation_holds(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    r = _built(snap, tmp_path)
    g = _gate(r, "G1")
    assert g.status is gates.GateStatus.PASS
    assert g.metrics == {"n_src": 7, "fanout": 1, "n_dedup": 0, "n_reject": 0, "n_stage": 7}


def test_gate_g3_detects_mktcap_identity_violation(raw: dict[str, Path], tmp_path: Path) -> None:
    broken = STK_ROWS + [_row("20200102", "000020", "동화약품", "KOSPI", "100", "100", "100", "100",
                              "1", "10", mktcap="999")]
    _write_krx(raw["krx"].with_name("krx3.db"), broken, KSQ_ROWS)
    s = snapshot.make_snapshot({"krx": raw["krx"].with_name("krx3.db"), "kiwoom": raw["kiwoom"]},
                               tmp_path / "snapshots", snapshot_id="snap_g3")
    r = _built(s, tmp_path)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "G3").status is gates.GateStatus.FAIL
    assert _gate(r, "G3").metrics["mktcap_violations"] == 1
    assert not (tmp_path / "stage" / "stg_price_daily" / f"v={r.build_id}").exists()
    assert (tmp_path / "stage" / "_failed" / f"{r.build_id}.json").exists()


def test_gate_g7_isolates_out_of_range_date_key_as_reject(
    raw: dict[str, Path], tmp_path: Path
) -> None:
    rows = STK_ROWS + [_row("29230102", "000020", "동화약품", "KOSPI", "100", "100", "100", "100",
                            "1", "10")]
    _write_krx(raw["krx"].with_name("krx4.db"), rows, KSQ_ROWS)
    s = snapshot.make_snapshot({"krx": raw["krx"].with_name("krx4.db"), "kiwoom": raw["kiwoom"]},
                               tmp_path / "snapshots", snapshot_id="snap_g7")
    r = _built(s, tmp_path, gate_thresholds={"G7": 0.2})  # 8행 픽스처 — 기본 0.1% 는 운영 임계
    assert r.ok
    assert _gate(r, "G7").metrics["n_out_of_range"] == 1
    assert _gate(r, "G1").metrics["n_reject"] == 1
    assert r.n_rows == 7
    rej_dir = tmp_path / "stage" / "stg_price_daily" / f"v={r.build_id}" / "_reject"
    rej = list(rej_dir.glob("*.parquet"))
    assert len(rej) == 1


def test_gate_g9_cross_source_match_against_kiwoom(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    r = _built(snap, tmp_path)
    g = _gate(r, "G9")
    assert g.status is gates.GateStatus.PASS
    assert g.metrics["close_joined"] == 3
    assert g.metrics["close_match_ratio"] == 1.0
    assert g.metrics["volume_match_ratio"] == pytest.approx(2 / 3)


def test_gate_g4_fixture_mismatch_fails_build(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    fx = tmp_path / "fixtures.json"
    fx.write_text(json.dumps([
        {"key": {"ticker": "005930", "date": "2018-05-03"}, "column": "close_krw",
         "expect": "2650000"},
        {"key": {"ticker": "005930", "date": "2018-05-04"}, "column": "close_krw", "expect": "1"},
    ]), encoding="utf-8")
    r = _built(snap, tmp_path, fixtures_path=fx)
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "G4")
    assert g.status is gates.GateStatus.FAIL
    assert g.metrics == {"n_fixtures": 2, "n_mismatch": 1}
