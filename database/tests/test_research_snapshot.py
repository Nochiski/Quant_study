"""합성 입력으로 연구 snapshot의 갱신 격리·실패·공개 경계를 검증한다."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest
from equity import research_snapshot as snapshots
from ledger_sync.hashing import compute_partition_hash
from ledger_sync.layout import Partition, parse_manifest
from stage import manifest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for relative in snapshots.CONTRACT_FILES + snapshots.ENVIRONMENT_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic contract\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    return root


def _build(root: Path, build_id: str, value: int) -> None:
    table = root / "observations"
    directory = table / f"v={build_id}"
    directory.mkdir(parents=True)
    with duckdb.connect() as connection:
        connection.execute(f"COPY (SELECT {value} AS value) TO '{directory}/part0.parquet' "
                           "(FORMAT PARQUET)")
        partition = Partition(f"v={build_id}", 1, "")
        content_hash = compute_partition_hash(connection, directory, partition)
    record = manifest.BuildRecord(build_id, "fixture", "synthetic-v1", "2024-01-02T00:00:00Z",
                                  1, content_hash, partitions=[{
                                      "path": partition.path, "n_rows": 1,
                                      "content_hash": content_hash}])
    manifest.commit(table, record, keep=1)


def _value(root: Path) -> int:
    view = parse_manifest("observations", (root / "observations/MANIFEST.json").read_bytes())
    build = view.current
    assert build is not None
    with duckdb.connect() as connection:
        row = connection.execute(
            f"SELECT value FROM read_parquet('{root}/observations/{build.partitions[0].path}/*.parquet')"
        ).fetchone()
        assert row is not None
        return int(row[0])


def test_update_and_gc_preserve_snapshot_input(tmp_path: Path, repo: Path) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    result = snapshots.create_snapshot(source, frozen, repo_root=repo)
    assert result.ok, result.detail
    _build(source, "b2", 29)
    assert _value(source) == 29
    assert _value(frozen) == 11
    checked = snapshots.verify_snapshot(frozen, repo_root=repo)
    assert checked.ok, checked.detail
    assert checked.ledger_snapshot_id == result.ledger_snapshot_id
    assert checked.contract_sources_sha256 == result.contract_sources_sha256


@pytest.mark.parametrize("failure", ["missing", "corrupt", "extra", "manifest", "pending"])
def test_verify_rejects_changed_input(tmp_path: Path, repo: Path, failure: str) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    parquet = frozen / "observations/v=b1/part0.parquet"
    if failure == "missing":
        parquet.unlink()
    elif failure == "corrupt":
        parquet.write_bytes(b"damaged")
    elif failure == "extra":
        shutil.copyfile(parquet, parquet.with_name("extra.parquet"))
    elif failure == "pending":
        (frozen / snapshots.PENDING_NAME).touch()
    else:
        (frozen / "observations/MANIFEST.json").write_text("{}", encoding="utf-8")
    result = snapshots.verify_snapshot(frozen)
    assert not result.ok
    assert result.detail is not None


@pytest.mark.parametrize("failure", ["hash", "missing", "symlink", "unsafe", "duplicate"])
def test_create_rejects_invalid_partition(tmp_path: Path, repo: Path, failure: str) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    path = source / "observations/MANIFEST.json"
    document = json.loads(path.read_bytes())
    part = document["builds"][0]["partitions"][0]
    if failure == "hash":
        part["content_hash"] = "1:wrong"
    elif failure == "missing":
        shutil.rmtree(source / "observations/v=b1")
    elif failure == "symlink":
        parquet = source / "observations/v=b1/part0.parquet"
        outside = tmp_path / "outside.parquet"
        parquet.rename(outside)
        parquet.symlink_to(outside)
    elif failure == "duplicate":
        document["builds"][0]["partitions"].append(dict(part))
    else:
        part["path"] = "v=b1/../../outside"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = snapshots.create_snapshot(source, frozen, repo_root=repo)
    assert not result.ok
    assert snapshots.verify_snapshot(frozen).ok is False


def test_existing_destination_is_preserved(tmp_path: Path, repo: Path) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    result = snapshots.create_snapshot(source, frozen, repo_root=repo)
    assert not result.ok
    assert _value(frozen) == 11
    assert snapshots.verify_snapshot(frozen).ok


def test_copy_failure_never_publishes_partial_root(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)

    def fail_copy(*args: object) -> None:
        assert not snapshots.verify_snapshot(frozen).ok
        raise OSError("synthetic copy interruption")

    monkeypatch.setattr(snapshots.shutil, "copyfile", fail_copy)
    assert not snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    assert not snapshots.verify_snapshot(frozen).ok


def test_source_manifest_race_refuses_publication(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    original = snapshots._provenance

    def update_after_copy(repo_root: Path, root: Path) -> dict[str, object]:
        _build(source, "b2", 29)
        return original(repo_root, root)

    monkeypatch.setattr(snapshots, "_provenance", update_after_copy)
    result = snapshots.create_snapshot(source, frozen, repo_root=repo)
    assert not result.ok
    assert "changed during copy" in str(result.detail)
    assert _value(source) == 29
    assert not snapshots.verify_snapshot(frozen).ok


def test_current_code_mismatch_is_explicit(tmp_path: Path, repo: Path) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    (repo / snapshots.CONTRACT_FILES[-1]).write_text("changed", encoding="utf-8")
    assert snapshots.verify_snapshot(frozen).ok
    assert not snapshots.verify_snapshot(frozen, repo_root=repo).ok


def test_cli_open_outputs_only_verified_root(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.main(["create", "--source-root", str(source), "--root", str(frozen),
                           "--repo-root", str(repo)]) == 0
    capsys.readouterr()
    assert snapshots.main(["open", "--root", str(frozen)]) == 0
    assert capsys.readouterr().out.strip() == str(frozen.resolve())
    (frozen / snapshots.PENDING_NAME).touch()
    assert snapshots.main(["open", "--root", str(frozen)]) == 1
    assert capsys.readouterr().out == ""


def test_source_in_place_edit_does_not_change_snapshot(tmp_path: Path, repo: Path) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    (source / "observations/v=b1/part0.parquet").write_bytes(b"source was overwritten")
    assert _value(frozen) == 11
    assert snapshots.verify_snapshot(frozen).ok


def test_missing_completion_descriptor_is_not_a_snapshot(tmp_path: Path) -> None:
    result = snapshots.verify_snapshot(tmp_path)
    assert not result.ok


@pytest.mark.parametrize("raw", ["[]", "null", "{}", '{"provenance": []}'])
def test_malformed_completion_descriptor_is_a_validation_failure(tmp_path: Path, raw: str) -> None:
    (tmp_path / snapshots.SNAPSHOT_NAME).write_text(raw, encoding="utf-8")
    assert not snapshots.verify_snapshot(tmp_path).ok


def test_new_code_file_requires_matching_checkout(tmp_path: Path, repo: Path) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    (repo / "database/src/new_module.py").write_text("x = 1", encoding="utf-8")
    assert not snapshots.verify_snapshot(frozen, repo_root=repo).ok


def test_nested_descriptor_name_is_not_hidden_from_inventory(tmp_path: Path, repo: Path) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)
    assert snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    (frozen / "observations" / snapshots.SNAPSHOT_NAME).write_text("{}", encoding="utf-8")
    assert not snapshots.verify_snapshot(frozen).ok


def test_final_verification_failure_keeps_root_unpublished(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, frozen = tmp_path / "source", tmp_path / "frozen"
    _build(source, "b1", 11)

    def fail_final(root: Path, *, repo_root: Path | None) -> snapshots.SnapshotResult:
        assert (root / snapshots.PENDING_NAME).is_file()
        return snapshots.SnapshotResult(root, "synthetic final verification failure")

    monkeypatch.setattr(snapshots, "_verify_contents", fail_final)
    assert not snapshots.create_snapshot(source, frozen, repo_root=repo).ok
    assert not frozen.is_dir()


def test_result_cannot_claim_ready_without_verified_identities(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires both identities"):
        snapshots.SnapshotResult(tmp_path)
    invalid = snapshots.SnapshotResult(tmp_path, "synthetic validation failure")
    assert invalid.status is snapshots.SnapshotStatus.INVALID
    assert not invalid.ok
    with pytest.raises(ValueError, match="requires detail and no identities"):
        snapshots.SnapshotResult(tmp_path, "failed", ledger_snapshot_id="unverified")
