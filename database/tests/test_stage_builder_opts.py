"""빌더 옵션 3건 — 5단계 에이전트 리뷰(DART 이벤트·KRX/키움)에서 나온 결함의 재현·수정 테스트.

- 정규화의 태그 제거는 옵트인(`strip_tags`): DART 서술 컬럼의 `<주1>` 각주는 태그가 아니다.
- 내용일 하한 1990 은 상장일(삼성전자 19750611)을, 1956 은 현물출자일(1952~54)을 격리한다 → 1900.
- `coverage_from`(§3 temporality ⓑ) 은 `_meta.json` 에 남아야 한다.
"""
import json
import sqlite3
from pathlib import Path

import duckdb

from stage import build, gates, model, snapshot


def _write(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (k TEXT, note TEXT, lbl TEXT, ld TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?)", [
        ("a", "감자 사유 <주1> 참조", "2026/12(E)<br />(IFRS연결)", "19750611",
         "2026-08-30T10:00:00"),
        ("b", "정상", "x", "18991231", "2026-08-30T10:00:00"),      # 1900 이전 → 격리
    ])
    con.commit()
    con.close()


RULE = model.TableRule(
    name="stg_probe_opts",
    sources=(model.SourceRef("x", "t", "t"),),
    columns=(
        model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
        model.ColumnRule("note", "note", model.KIND_TEXT, normalize_text=True),
        model.ColumnRule("lbl", "lbl", model.KIND_TEXT, normalize_text=True, strip_tags=True),
        model.ColumnRule("ld", "list_date", model.KIND_DATE_YMD8),
    ),
    natural_key=("k",),
    partition_class="whole", partition_expr=None, partition_src=None,
    observed_src="collected_at", write_mode="first_write_wins", fanout=1,
    payload_exclude=("collected_at",), lag_known=False, available=model.AVAILABLE_NONE,
    coverage_from="2026-09-01",
)


def _built(tmp_path: Path) -> build.BuildResult:
    d = tmp_path / "raw"
    d.mkdir()
    _write(d / "x.db")
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    r = build.build_table(RULE, s, tmp_path / "stage", gate_thresholds={"G7": 1.0})
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    return r


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}')")
    return con


def test_normalize_text_keeps_angle_bracket_footnotes_unless_strip_tags(tmp_path: Path) -> None:
    con = _read(tmp_path, _built(tmp_path))
    note, lbl = con.execute("SELECT note, lbl FROM t WHERE k='a'").fetchone()
    assert note == "감자 사유 <주1> 참조"          # 각주 보존
    assert lbl == "2026/12(E)(IFRS연결)"           # strip_tags 옵트인만 태그 제거


def test_content_date_lower_bound_admits_founding_era_dates(tmp_path: Path) -> None:
    assert gates.YEAR_RANGE_CONTENT[0] == 1900        # 현물출자일 1952~54 실재 (stg_capital)
    r = _built(tmp_path)
    con = _read(tmp_path, r)
    got = con.execute("SELECT k, list_date, miss_kind.list_date FROM t ORDER BY k").fetchall()
    assert str(got[0][1]) == "1975-06-11" and got[0][2] is None     # 삼성전자 상장일 보존
    assert got[1][1] is None and got[1][2] == "out_of_range"


def test_coverage_from_is_written_to_meta(tmp_path: Path) -> None:
    r = _built(tmp_path)
    assert r.out_dir is not None
    meta = json.loads((r.out_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["coverage_from"] == "2026-09-01"


ZERO_RULE = model.TableRule(
    name="stg_probe_zero",
    sources=(model.SourceRef("x", "z", "z"),),
    columns=(
        model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
        model.ColumnRule("p", "price_krw", model.KIND_NUMERIC, 12, 2, zero_is_missing=True),
        model.ColumnRule("d", "abol_date", model.KIND_DATE_YMD8, zero_is_missing=True),
    ),
    natural_key=("k",),
    partition_class="whole", partition_expr=None, partition_src=None,
    observed_src="collected_at", write_mode="append_only", fanout=1,
    payload_exclude=("collected_at",), lag_known=False, available=model.AVAILABLE_NONE,
)


def test_zero_marker_matches_decimal_and_all_zero_date_literals(tmp_path: Path) -> None:
    """KIS 결측 '0' 은 '0.00'(loan stck_prpr 281행)·'00000000'(stock_info 날짜 168셀)로도 온다."""
    d = tmp_path / "raw"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    con.execute("CREATE TABLE z (k TEXT, p TEXT, d TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO z VALUES (?,?,?,?)", [
        ("a", "0", "00000000", "2026-08-30T10:00:00"),
        ("b", "0.00", "20200101", "2026-08-30T10:00:00"),
        ("c", "0.50", "-", "2026-08-30T10:00:00"),
    ])
    con.commit()
    con.close()
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    r = build.build_table(ZERO_RULE, s, tmp_path / "stage")
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    con2 = _read(tmp_path, r)
    got = con2.execute("SELECT k, price_krw, miss_kind.price_krw, abol_date, miss_kind.abol_date "
                       "FROM t ORDER BY k").fetchall()
    assert got[0] == ("a", None, "ledger_zero", None, "ledger_zero")
    assert (got[1][1], got[1][2], str(got[1][3])) == (None, "ledger_zero", "2020-01-01")
    assert (str(got[2][1]), got[2][2], got[2][4]) == ("0.50", None, "ledger_dash")


def _log_rule(versioned: bool) -> model.TableRule:
    return model.TableRule(
        name="stg_probe_log",
        sources=(model.SourceRef("x", "lg", "lg"),),
        columns=(model.ColumnRule("ep", "endpoint", model.KIND_TEXT, key=True),
                 model.ColumnRule("st", "status", model.KIND_TEXT),
                 model.ColumnRule("d", "req_date", model.KIND_DATE_YMD8, key=True)),
        natural_key=("endpoint", "req_date"),
        partition_class="whole", partition_expr=None, partition_src=None,
        observed_src="ts", write_mode="append_only", fanout=1, payload_exclude=("ts",),
        lag_known=False, available=model.AVAILABLE_NONE, versioned=versioned,
    )


def _log_snap(tmp_path: Path) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    con.execute("CREATE TABLE lg (ep TEXT, st TEXT, d TEXT, ts TEXT)")
    con.executemany("INSERT INTO lg VALUES (?,?,?,?)", [
        ("list", "ok", "19990403", "2026-08-30T10:00:00"),      # 1999 관측일 — DART 최초 공시 연도
        ("list", "empty", "19990403", "2026-08-30T10:00:05"),   # 같은 키·같은 관측일, 다른 payload
    ])
    con.commit()
    con.close()
    return snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")


def test_unversioned_log_table_skips_g6_and_keeps_both_rows(tmp_path: Path) -> None:
    """콜/유닛 로그는 판본 개념이 없다 — 같은 키가 하루에 여러 번 정상 (DART 리뷰 D2)."""
    r = build.build_table(_log_rule(versioned=False), _log_snap(tmp_path), tmp_path / "stage")
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    assert r.n_rows == 2 and r.n_reject == 0
    g6 = next(g for g in r.gates if g.name == "G6")
    assert g6.status is gates.GateStatus.SKIP and g6.detail == "unversioned"


def test_versioned_append_only_table_folds_same_day_duplicates_to_the_last_observation(
        tmp_path: Path) -> None:
    """2026-09-11 계약 변경: 판본 축이 observed_date(날짜)라 같은 키의 같은 날 다른 페이로드는 표현할 수
    없다 — 예전엔 G6 이 표를 폐기했지만(첫 저녁 슬롯에서 DART 아침·저녁 이중 관측으로 실제 발생),
    이제는 그날의 마지막 관측을 판으로 접고 n_dedup_same_day 로 센다. 콜 로그(versioned=False)는 그대로."""
    r = build.build_table(_log_rule(versioned=True), _log_snap(tmp_path), tmp_path / "stage")
    assert r.status is build.BuildStatus.OK, [(g.name, g.detail) for g in r.gates]
    assert next(g for g in r.gates if g.name == "G6").status is gates.GateStatus.PASS
    g1 = next(g for g in r.gates if g.name == "G1")
    assert g1.metrics["n_dedup_same_day"] >= 1
    assert g1.metrics["n_dedup"] >= g1.metrics["n_dedup_same_day"]


def test_observed_year_floor_admits_1999_dart_receipts() -> None:
    assert gates.YEAR_RANGE_OBSERVED[0] == 1999          # 19990403000009 실재 (DART 리뷰 D4)


def test_build_with_every_row_rejected_yields_an_empty_build_not_a_crash(tmp_path: Path) -> None:
    """전 행 reject 시 PARTITION_BY COPY 가 파일을 안 써 read_parquet 이 죽었다(1차 풀 빌드)."""
    d = tmp_path / "raw"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    con.execute("CREATE TABLE z (k TEXT, p TEXT, d TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO z VALUES (?,?,?,?)", [
        (None, "1", "20200101", "2026-08-30T10:00:00"),
        ("", "2", "20200102", "2026-08-30T10:00:00")])
    con.commit()
    con.close()
    rule = model.TableRule(
        name="stg_probe_empty", sources=(model.SourceRef("x", "z", "z"),),
        columns=(model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
                 model.ColumnRule("d", "date", model.KIND_DATE_YMD8, key=True),
                 model.ColumnRule("p", "p", model.KIND_NUMERIC, 5, 0)),
        natural_key=("k", "date"), partition_class="date_axis", partition_expr="substr(d, 1, 4)",
        partition_src="d", observed_src="collected_at", write_mode="append_only", fanout=1,
        payload_exclude=("collected_at",), lag_known=False, available=model.AVAILABLE_NONE)
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    r = build.build_table(rule, s, tmp_path / "stage")
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    assert (r.n_src, r.n_rows, r.n_reject, r.content_hash) == (2, 0, 2, "0:empty")


def test_manifest_round_trips_equity_inputs_and_reads_old_records(tmp_path: Path) -> None:
    """equity 층은 BuildRecord.inputs(입력 stage 테이블 → 고정 build_id)를 쓴다. stage 빌드는 빈 dict."""
    from stage import manifest
    root = tmp_path / "eq_table"
    root.mkdir()
    rec = manifest.BuildRecord("b1", "s1", "2.2.3", "2026-09-05T00:00:00Z", 3, "3:abc",
                               inputs={"stg_capital": "b_20260905T105922_786120Z"})
    manifest.commit(root, rec)
    m = manifest.load(root / "MANIFEST.json")
    assert m.builds[-1].inputs == {"stg_capital": "b_20260905T105922_786120Z"}
    old = {"table": "eq_table", "current_build": "b0", "keep": 3, "builds": [
        {"build_id": "b0", "snapshot_id": "s0", "rules_version": "2.2.3",
         "built_at_utc": "2026-09-01T00:00:00Z", "n_rows": 1, "content_hash": "1:0"}]}
    (root / "MANIFEST.json").write_text(json.dumps(old), encoding="utf-8")
    assert manifest.load(root / "MANIFEST.json").builds[0].inputs == {}   # 구 레코드 호환
