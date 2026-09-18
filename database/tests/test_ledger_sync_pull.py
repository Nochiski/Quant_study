"""ledger_sync.plan / pull — 계획·수신·재사용·원자 교체·재개·격리·드리프트·GC."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from ledger_sync.layout import INCOMING_DIR, MANIFEST_NAME
from ledger_sync.plan import TableAction, make_plan
from ledger_sync.pull import (
    KEEP_DEFAULT,
    InsufficientDiskSpace,
    TableOutcome,
    execute_plan,
    gc_table,
)
from ledger_sync.state import ORIGIN_DOWNLOADED, ORIGIN_REUSED, load_state
from ledger_sync_fake import FakeRemote

REMOTE = "/equity"


@pytest.fixture
def remote() -> FakeRemote:
    fake = FakeRemote()
    fake.put_table(REMOTE, "security", "b1", {"": {"part0.parquet": b"S1" * 10,
                                                   "_meta.json": b"{}"}}, {"": "5:aa"})
    fake.put_table(REMOTE, "price_daily", "b1",
                   {"year=2010": {"part0.parquet": b"P10", "_meta.json": b"{}"},
                    "year=2011": {"part0.parquet": b"P11", "_meta.json": b"{}"}},
                   {"year=2010": "1:10", "year=2011": "1:11"})
    fake.put_meta(REMOTE, "baseline.json", b"{}")
    fake.put_meta(REMOTE, "_catalog_meta.json", b'{"snapshot_id": "x"}')
    fake.files[f"{REMOTE}/fixtures/readme.txt"] = b"not a table"
    fake.files[f"{REMOTE}/_pinned/stg_x/MANIFEST.json"] = b"{}"
    return fake


def _sync(remote: FakeRemote, root: Path, keep: int = KEEP_DEFAULT):
    state = load_state(root, "equity")
    plan = make_plan(remote, REMOTE, root, state, layer="equity")
    report = execute_plan(remote, plan, root, state, keep=keep, check_space=False)
    return plan, report


def test_first_pull_downloads_only_current_builds_and_meta(remote: FakeRemote,
                                                           tmp_path: Path) -> None:
    plan, report = _sync(remote, tmp_path)
    assert {t.table for t in plan.tables} == {"security", "price_daily"}  # fixtures·_pinned 제외
    assert all(t.action is TableAction.NEW_BUILD for t in plan.tables)
    assert report.ok and {r.outcome for r in report.results} == {TableOutcome.DONE}
    y2010 = tmp_path / "price_daily" / "v=b1" / "year=2010" / "part0.parquet"
    assert y2010.read_bytes() == b"P10"
    remote_manifest = remote.files[f"{REMOTE}/security/MANIFEST.json"]
    assert (tmp_path / "security" / MANIFEST_NAME).read_bytes() == remote_manifest
    assert (tmp_path / "baseline.json").exists() and (tmp_path / "_catalog_meta.json").exists()
    assert not (tmp_path / "price_daily" / INCOMING_DIR).exists()
    state = load_state(tmp_path, "equity")
    assert state.tables["price_daily"].build_id == "b1"
    files = state.tables["price_daily"].files
    assert files["v=b1/year=2010/part0.parquet"].origin == ORIGIN_DOWNLOADED


def test_second_pull_is_a_no_op(remote: FakeRemote, tmp_path: Path) -> None:
    _sync(remote, tmp_path)
    remote.calls.clear()
    plan, report = _sync(remote, tmp_path)
    assert all(t.action is TableAction.UP_TO_DATE for t in plan.tables)
    assert {r.outcome for r in report.results} == {TableOutcome.UNCHANGED}
    assert not [c for c in remote.calls if c[0] == "download"]


def test_new_build_reuses_partitions_with_same_content_hash(remote: FakeRemote,
                                                            tmp_path: Path) -> None:
    _sync(remote, tmp_path)
    # 새 빌드: 2010 은 내용이 같고(해시 동일) 2011 만 바뀌었다
    remote.put_table(REMOTE, "price_daily", "b2",
                     {"year=2010": {"part0.parquet": b"P10-rewritten", "_meta.json": b"{2}"},
                      "year=2011": {"part0.parquet": b"P11-new", "_meta.json": b"{2}"}},
                     {"year=2010": "1:10", "year=2011": "1:11b"})
    plan, report = _sync(remote, tmp_path)
    pd = next(t for t in plan.tables if t.table == "price_daily")
    assert pd.action is TableAction.NEW_BUILD
    assert [r.partition_path for r in pd.reuses] == ["v=b2/year=2010"]
    assert sorted(t.rel_path for t in pd.downloads) == [
        "v=b2/year=2010/_meta.json", "v=b2/year=2011/_meta.json", "v=b2/year=2011/part0.parquet"]
    assert report.ok
    # 재사용한 parquet 는 로컬 구판본 바이트, _meta.json 은 새로 받은 것
    y2010 = tmp_path / "price_daily" / "v=b2" / "year=2010"
    assert (y2010 / "part0.parquet").read_bytes() == b"P10"
    assert (y2010 / "_meta.json").read_bytes() == b"{2}"
    files = load_state(tmp_path, "equity").tables["price_daily"].files
    assert files["v=b2/year=2010/part0.parquet"].origin == ORIGIN_REUSED
    assert files["v=b2/year=2011/part0.parquet"].origin == ORIGIN_DOWNLOADED
    # keep=2: b1 은 남고 그 이전은 없다
    assert (tmp_path / "price_daily" / "v=b1").is_dir()


def test_no_reuse_downloads_everything(remote: FakeRemote, tmp_path: Path) -> None:
    _sync(remote, tmp_path)
    remote.put_table(REMOTE, "price_daily", "b2",
                     {"year=2010": {"part0.parquet": b"P10-rewritten", "_meta.json": b"{2}"}},
                     {"year=2010": "1:10"})
    state = load_state(tmp_path, "equity")
    plan = make_plan(remote, REMOTE, tmp_path, state, layer="equity", reuse=False)
    pd = next(t for t in plan.tables if t.table == "price_daily")
    assert not pd.reuses and len(pd.downloads) == 2


def test_transfer_failure_isolates_the_table_and_resumes_next_time(remote: FakeRemote,
                                                                   tmp_path: Path) -> None:
    remote.fail_once.add(f"{REMOTE}/price_daily/v=b1/year=2011/part0.parquet")
    plan, report = _sync(remote, tmp_path)
    failed = next(r for r in report.results if r.table == "price_daily")
    assert failed.outcome is TableOutcome.FAILED and "injected failure" in (failed.detail or "")
    assert next(r for r in report.results if r.table == "security").outcome is TableOutcome.DONE
    assert not (tmp_path / "price_daily" / MANIFEST_NAME).exists()          # 반쪽 빌드 노출 없음
    incoming = tmp_path / "price_daily" / INCOMING_DIR / "v=b1"
    assert (incoming / "year=2010" / "part0.parquet").exists()
    assert "price_daily" not in load_state(tmp_path, "equity").tables
    remote.calls.clear()
    _, report2 = _sync(remote, tmp_path)
    assert report2.ok
    downloaded = [c[1] for c in remote.calls if c[0] == "download"]
    # 2010 은 _incoming 에 같은 크기로 남아 있어 재개로 건너뛴다
    assert downloaded == [f"{REMOTE}/price_daily/v=b1/year=2011/part0.parquet"]


def test_size_mismatch_is_a_failure_not_a_silent_commit(remote: FakeRemote, tmp_path: Path) -> None:
    class Truncating(FakeRemote):
        def download(self, path: str, local: Path, progress=None) -> int:  # noqa: ANN001  # reason: 테스트 더블
            n = super().download(path, local, progress)
            if path.endswith("year=2011/part0.parquet"):
                local.write_bytes(b"P")
                return 1
            return n

    truncating = Truncating(files=remote.files)
    _, report = _sync(truncating, tmp_path)
    failed = next(r for r in report.results if r.table == "price_daily")
    assert failed.outcome is TableOutcome.FAILED and "size mismatch" in (failed.detail or "")


def test_drift_is_reported_when_server_commits_during_pull(remote: FakeRemote,
                                                           tmp_path: Path) -> None:
    class Drifting(FakeRemote):
        def download(self, path: str, local: Path, progress=None) -> int:  # noqa: ANN001  # reason: 테스트 더블
            n = super().download(path, local, progress)
            if path.endswith("security/v=b1/part0.parquet"):
                self.put_table(REMOTE, "security", "b2", {"": {"part0.parquet": b"S2"}},
                               {"": "5:bb"})
            return n

    drifting = Drifting(files=dict(remote.files))
    _, report = _sync(drifting, tmp_path)
    assert report.drifted == ["security"] and not report.ok
    assert load_state(tmp_path, "equity").tables["security"].build_id == "b1"


def test_manifest_refresh_when_only_builds_list_changed(remote: FakeRemote, tmp_path: Path) -> None:
    _sync(remote, tmp_path)
    manifest_path = f"{REMOTE}/security/MANIFEST.json"
    doc = json.loads(remote.files[manifest_path])
    doc["keep"] = 2   # current_build 는 그대로, 원문만 바뀜
    remote.files[manifest_path] = json.dumps(doc).encode()
    plan, report = _sync(remote, tmp_path)
    sec = next(t for t in plan.tables if t.table == "security")
    assert sec.action is TableAction.UP_TO_DATE and sec.manifest_refresh
    assert (tmp_path / "security" / MANIFEST_NAME).read_bytes() == remote.files[manifest_path]


def test_gc_keeps_current_plus_keep_minus_one(remote: FakeRemote, tmp_path: Path) -> None:
    for build in ("b1", "b2", "b3", "b4"):
        remote.put_table(REMOTE, "security", build, {"": {"part0.parquet": build.encode()}},
                         {"": f"1:{build}"})
        _sync(remote, tmp_path, keep=2)
    names = sorted(p.name for p in (tmp_path / "security").iterdir() if p.name.startswith("v="))
    assert names == ["v=b3", "v=b4"]
    state = load_state(tmp_path, "equity")
    (tmp_path / "security" / INCOMING_DIR / "v=b9").mkdir(parents=True)
    removed = gc_table(tmp_path, "security", state, keep=1)
    assert sorted(removed) == ["security/_incoming/v=b9", "security/v=b3"]
    assert (tmp_path / "security" / "v=b4").is_dir()


def test_unreadable_remote_manifest_is_an_error_plan(remote: FakeRemote, tmp_path: Path) -> None:
    remote.files[f"{REMOTE}/security/MANIFEST.json"] = b"{broken"
    plan, report = _sync(remote, tmp_path)
    sec = next(t for t in plan.tables if t.table == "security")
    assert sec.action is TableAction.ERROR and "not JSON" in (sec.detail or "")
    assert next(r for r in report.results if r.table == "security").outcome is TableOutcome.ERROR
    assert next(r for r in report.results if r.table == "price_daily").outcome is TableOutcome.DONE


def test_free_space_gate_runs_before_any_transfer(remote: FakeRemote, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(100, 100, 0))
    state = load_state(tmp_path, "equity")
    plan = make_plan(remote, REMOTE, tmp_path, state, layer="equity")
    with pytest.raises(InsufficientDiskSpace):
        execute_plan(remote, plan, tmp_path, state)
    assert not [c for c in remote.calls if c[0] == "download"]
