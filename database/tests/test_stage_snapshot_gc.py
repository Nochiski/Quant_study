"""스냅샷 GC — 보호 세트 + mtime 최신 keep 만 남긴다 (플랜 v2 Task B.1 / v1 Task 4.2).

저녁 잠정판·아침 확정판으로 하루 2판을 지으므로 keep 기본값이 6(≈3일치)이다. 보호 세트는
현재 stage MANIFEST 66개가 가리키는 `snapshot_id` — 그 판을 지우면 "같은 스냅샷으로 재빌드해
content_hash 를 대조" 하는 재현성 검증이 불가능해진다.
"""
import os
from pathlib import Path

from stage import snapshot

_PAYLOAD = b"x" * 1000          # 판마다 같은 크기 — 확보 바이트를 결정적으로 단언하려고


def _snap(root: Path, name: str, age_s: int) -> Path:
    """`snap_root/<name>/` 한 세트. mtime 이 오래될수록 age_s 가 크다."""
    d = root / name
    d.mkdir(parents=True)
    (d / "krx.db").write_bytes(_PAYLOAD)
    (d / "snapshot.json").write_text("{}", encoding="utf-8")
    ts = 1_700_000_000 - age_s
    os.utime(d, (ts, ts))       # 파일을 다 쓴 뒤에 찍는다 — 쓰기가 디렉토리 mtime 을 갱신한다
    return d


def test_gc_keeps_protected_and_newest_keep_sets_and_deletes_the_rest(tmp_path: Path) -> None:
    """8세트 = 보호 1(최古) + 최신 6 + 삭제 1. 보호는 keep 과 별개로 더해진다."""
    root = tmp_path / "snapshots"
    for i in range(8):
        _snap(root, f"snap_{i}", age_s=i * 3600)        # snap_0 이 최신, snap_7 이 최古
    r = snapshot.gc(root, keep=6, protect={"snap_7"})
    assert r.deleted == ("snap_6",)
    assert set(r.kept) == {f"snap_{i}" for i in range(6)} | {"snap_7"}
    assert r.freed_bytes == len(_PAYLOAD) + 2           # krx.db + snapshot.json
    assert not (root / "snap_6").exists()
    assert (root / "snap_7").exists() and (root / "snap_0").exists()


def test_gc_leaves_directories_that_are_not_snapshot_sets(tmp_path: Path) -> None:
    """`data/snapshots` 아래에 사람이 둔 디렉토리·파일은 GC 대상이 아니다."""
    root = tmp_path / "snapshots"
    _snap(root, "snap_old", age_s=7200)
    _snap(root, "snap_new", age_s=0)
    (root / "keepme").mkdir()
    (root / "note.txt").write_text("메모", encoding="utf-8")
    r = snapshot.gc(root, keep=1, protect=())
    assert r.deleted == ("snap_old",)
    assert (root / "keepme").is_dir() and (root / "note.txt").exists()


def test_gc_on_a_missing_root_is_a_no_op(tmp_path: Path) -> None:
    r = snapshot.gc(tmp_path / "none", keep=6, protect=())
    assert (r.deleted, r.kept, r.freed_bytes) == ((), (), 0)


def test_current_snapshot_ids_collects_every_manifest_pointer(tmp_path: Path,
                                                              make_stage_tree) -> None:
    """보호 세트의 출처 — 표마다 `current_build` 가 가리키는 판의 snapshot_id."""
    from stage import manifest
    t1 = make_stage_tree(tmp_path, "stg_a", [{"k": "1"}],
                         build_id="e_20260911T091500_000000Z")
    t2 = make_stage_tree(tmp_path, "stg_b", [{"k": "2"}],
                         build_id="m_20260910T231000_000000Z")
    manifest.commit(t2.table_root, manifest.BuildRecord(
        build_id="e_20260911T091600_000000Z", snapshot_id="snap_evening",
        rules_version="2.2.3", built_at_utc="2026-09-11T09:30:00+00:00", n_rows=1,
        content_hash="1:a"))
    got = snapshot.current_snapshot_ids(t1.stage_root)
    assert got == {"snap_test", "snap_evening"}     # stg_a 는 픽스처 기본값 snap_test
