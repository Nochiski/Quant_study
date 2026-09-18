"""ledger_sync.verify — manifest · files · hash 세 층위."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from ledger_sync.layout import MANIFEST_NAME, Partition, snapshot_id
from ledger_sync.plan import make_plan
from ledger_sync.pull import execute_plan
from ledger_sync.state import load_state, save_state
from ledger_sync.verify import (
    Level,
    VerifyReport,
    compute_partition_hash,
    verify_files,
    verify_hash,
    verify_manifest,
)
from ledger_sync_fake import FakeRemote

REMOTE = "/equity"


def _server_hash(con: duckdb.DuckDBPyConnection, glob: str) -> str:
    """`stage/build.py:_content_hash` 원문 — tmp 경로(`v=` 없음)에서 hive 로 뜬 값."""
    n, h = con.execute(f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) "
                       f"FROM read_parquet('{glob}', hive_partitioning=true) t").fetchone()
    return f"{int(str(n))}:{'0' if h is None else format(int(str(h)), 'x')}"


def _parquet(con: duckdb.DuckDBPyConnection, path: Path, rows: str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM (VALUES {rows}) v(ticker, close)) "
                f"TO '{path}' (FORMAT PARQUET)")
    return path.read_bytes()


@pytest.fixture
def synced(tmp_path: Path) -> tuple[FakeRemote, Path, dict[str, str]]:
    """서버가 만든 것과 같은 방식(tmp 경로 + hive)으로 해시를 뜬 표 두 개를 올리고 로컬에 받는다."""
    con = duckdb.connect()
    tmp = tmp_path / "server_tmp"
    whole = _parquet(con, tmp / "security" / "part0.parquet", "('005930', 1), ('000660', 2)")
    y10 = _parquet(con, tmp / "price" / "year=2010" / "part0.parquet", "('005930', 10)")
    y11 = _parquet(con, tmp / "price" / "year=2011" / "part0.parquet",
                   "('005930', 11), ('000660', 12)")
    hashes = {
        "": _server_hash(con, str(tmp / "security" / "*.parquet")),
        "year=2010": _server_hash(con, str(tmp / "price" / "year=2010" / "*.parquet")),
        "year=2011": _server_hash(con, str(tmp / "price" / "year=2011" / "*.parquet")),
    }
    con.close()
    remote = FakeRemote()
    remote.put_table(REMOTE, "security", "b1", {"": {"part0.parquet": whole, "_meta.json": b"{}"}},
                     {"": hashes[""]})
    remote.put_table(REMOTE, "price_daily", "b1",
                     {"year=2010": {"part0.parquet": y10}, "year=2011": {"part0.parquet": y11}},
                     {"year=2010": hashes["year=2010"], "year=2011": hashes["year=2011"]})
    root = tmp_path / "local"
    state = load_state(root, "equity")
    execute_plan(remote, make_plan(remote, REMOTE, root, state, layer="equity"), root, state,
                 check_space=False)
    return remote, root, hashes


def test_clean_copy_passes_all_three_levels(synced) -> None:
    remote, root, _ = synced
    state = load_state(root, "equity")
    report = VerifyReport()
    verify_manifest(remote, REMOTE, root, state, report)
    verify_files(remote, REMOTE, root, state, report)
    verify_hash(root, state, report)
    assert report.ok, report.findings
    assert report.checked[Level.HASH.value] == 3


def test_hash_reproduces_server_rule_despite_v_dir_in_local_path(synced) -> None:
    _, root, hashes = synced
    con = duckdb.connect()
    assert compute_partition_hash(con, root / "price_daily" / "v=b1" / "year=2010",
                                  Partition("v=b1/year=2010", 1, "")) == hashes["year=2010"]
    assert compute_partition_hash(con, root / "security" / "v=b1",
                                  Partition("v=b1", 2, "")) == hashes[""]
    assert compute_partition_hash(con, root / "nowhere", Partition("v=b1", 0, "")) == "0:empty"


def test_corrupted_parquet_is_a_hash_finding(synced) -> None:
    _, root, _ = synced
    target = root / "price_daily" / "v=b1" / "year=2011" / "part0.parquet"
    good = target.read_bytes()
    con = duckdb.connect()
    con.execute(f"COPY (SELECT '005930' AS ticker, 99 AS close) TO '{target}' (FORMAT PARQUET)")
    con.close()
    state = load_state(root, "equity")
    state.tables["price_daily"].files["v=b1/year=2011/part0.parquet"] = \
        type(state.tables["price_daily"].files["v=b1/year=2011/part0.parquet"])(
            target.stat().st_size, "downloaded")
    report = VerifyReport()
    verify_hash(root, state, report, tables=["price_daily"])
    assert [f.level for f in report.findings] == [Level.HASH]
    assert "year=2011" in report.findings[0].detail
    target.write_bytes(good)


def test_manifest_level_sees_remote_advance_and_stale_catalog(synced) -> None:
    remote, root, _ = synced
    remote.put_table(REMOTE, "security", "b2", {"": {"part0.parquet": b"new"}}, {"": "1:1"})
    state = load_state(root, "equity")
    (root / "_catalog_meta.json").write_text(json.dumps({"snapshot_id": "stale"}), encoding="utf-8")
    report = VerifyReport()
    verify_manifest(remote, REMOTE, root, state, report)
    details = {f.table: f.detail for f in report.findings}
    assert "current_build differs" in details["security"]
    assert "catalog snapshot_id" in details["_catalog_meta"]
    fresh = snapshot_id({t: ts.build_id for t, ts in state.tables.items()})
    (root / "_catalog_meta.json").write_text(json.dumps({"snapshot_id": fresh}), encoding="utf-8")
    report = VerifyReport()
    verify_manifest(remote, REMOTE, root, state, report, tables=["price_daily"])
    assert report.ok


def test_files_level_compares_names_and_sizes_but_skips_reused_parquet(synced) -> None:
    remote, root, _ = synced
    state = load_state(root, "equity")
    # 원격 파일이 조용히 바뀌었다(크기 다름) → downloaded 파일은 지적, reused 로 표시하면 건너뛴다
    remote.files[f"{REMOTE}/security/v=b1/part0.parquet"] += b"tail"
    report = VerifyReport()
    verify_files(remote, REMOTE, root, state, report, tables=["security"])
    assert any("size differs from remote" in f.detail for f in report.findings)
    rec = state.tables["security"].files["v=b1/part0.parquet"]
    state.tables["security"].files["v=b1/part0.parquet"] = type(rec)(rec.size, "reused")
    save_state(root, state)
    report = VerifyReport()
    verify_files(remote, REMOTE, root, state, report, tables=["security"])
    assert report.ok
    # 파일 집합이 다르면(원격에 새 파일) 지적
    remote.files[f"{REMOTE}/security/v=b1/extra.parquet"] = b"x"
    report = VerifyReport()
    verify_files(remote, REMOTE, root, state, report, tables=["security"])
    assert any("file set differs" in f.detail and "extra.parquet" in f.detail
               for f in report.findings)


def test_files_level_flags_manifest_byte_drift_and_missing_disk_file(synced) -> None:
    remote, root, _ = synced
    state = load_state(root, "equity")
    (root / "security" / MANIFEST_NAME).write_bytes(
        remote.files[f"{REMOTE}/security/MANIFEST.json"] + b"\n")
    (root / "security" / "v=b1" / "_meta.json").unlink()
    report = VerifyReport()
    verify_files(remote, REMOTE, root, state, report, tables=["security"])
    details = [f.detail for f in report.findings]
    assert any("MANIFEST bytes differ" in d for d in details)
    assert any("file missing on disk" in d for d in details)
