"""ledger_sync.layout — MANIFEST 해석·테이블 판별·파티션 꼬리."""
from __future__ import annotations

import json

from ledger_sync.layout import (
    ManifestStatus,
    Partition,
    build_dir_names,
    is_safe_segment,
    is_table_name,
    parse_manifest,
    snapshot_id,
)


def _manifest(current: str, builds: list[dict[str, object]]) -> bytes:
    document = {"table": "t", "current_build": current, "keep": 3, "builds": builds}
    return json.dumps(document).encode()


def _build(build_id: str, paths: list[str]) -> dict[str, object]:
    return {"build_id": build_id, "built_at_utc": "2026-09-18T13:30:50+00:00",
            "content_hash": "9:abc",
            "partitions": [{"path": p, "n_rows": 3, "content_hash": f"3:{i}"}
                           for i, p in enumerate(paths)]}


def test_current_build_partitions_are_resolved() -> None:
    view = parse_manifest("t", _manifest("b2", [_build("b1", ["v=b1"]),
                                               _build("b2", ["v=b2/year=2010", "v=b2/year=2011"])]))
    assert view.ok and view.current is not None
    assert view.current.build_id == "b2"
    assert [p.tail for p in view.current.partitions] == ["year=2010", "year=2011"]
    assert view.builds["b1"].partitions[0].tail == ""
    assert view.raw.startswith(b"{")


def test_invalid_documents_are_status_values_not_exceptions() -> None:
    assert parse_manifest("t", b"not json").status is ManifestStatus.INVALID_JSON
    assert parse_manifest("t", b"[1]").status is ManifestStatus.NOT_OBJECT
    assert parse_manifest("t", b'{"builds": []}').status is ManifestStatus.NO_CURRENT_BUILD
    missing = parse_manifest("t", _manifest("b9", [_build("b1", ["v=b1"])]))
    assert missing.status is ManifestStatus.CURRENT_NOT_IN_BUILDS
    assert "b9" in (missing.detail or "")
    bad = parse_manifest("t", _manifest("b1", [_build("b1", ["year=2010"])]))
    assert bad.status is ManifestStatus.BAD_PARTITION
    empty = parse_manifest("t", _manifest("b1", [_build("b1", [])]))
    assert empty.status is ManifestStatus.BAD_PARTITION
    assert empty.current is None


def test_hive_columns_come_from_partition_tail() -> None:
    assert Partition("v=b/year=2010", 1, "h").hive_columns() == (("year", "2010"),)
    assert Partition("v=b", 1, "h").hive_columns() == ()
    assert Partition("v=b/year=2010/k=v", 1, "h").hive_columns() == (("year", "2010"), ("k", "v"))


def test_table_name_rule_excludes_internal_dirs() -> None:
    assert is_table_name("price_daily")
    assert not is_table_name("_pinned")
    assert not is_table_name("_sync")
    assert not is_table_name(".hidden")


def test_snapshot_id_matches_workbench_rule() -> None:
    # workbench `_source.snapshot_id` 와 같은 규칙: 정렬한 `table=build` 줄의 sha256 앞 16자리
    import hashlib

    builds = {"b": "2", "a": "1"}
    expected = hashlib.sha256(b"a=1\nb=2").hexdigest()[:16]
    assert snapshot_id(builds) == expected


def test_build_dir_names_are_sorted_v_dirs() -> None:
    assert build_dir_names(["v=b2", "_incoming", "v=b1", "MANIFEST.json"]) == ["v=b1", "v=b2"]


def test_remote_names_that_could_escape_the_local_root_are_rejected() -> None:
    # 원격 MANIFEST 의 build_id·파티션 경로는 그대로 로컬 경로 조각이 된다 — 문법 밖이면 해석 실패
    evil = "x/../../evil"
    traversal = parse_manifest("t", _manifest(evil, [_build(evil, [f"v={evil}"])]))
    assert traversal.status is ManifestStatus.UNSAFE_NAME
    bad_tail = parse_manifest("t", _manifest("b1", [_build("b1", ["v=b1/../../oops"])]))
    assert bad_tail.status is ManifestStatus.BAD_PARTITION
    other_build = parse_manifest("t", _manifest("b1", [_build("b1", ["v=b2/year=2010"])]))
    assert other_build.status is ManifestStatus.BAD_PARTITION
    assert parse_manifest("t", _manifest("b1", [_build("b1", ["v=b1/year=2010"])])).ok
    for name in ("part0.parquet", "_meta.json", "e_20260918T133020_791078Z", "year=2010"):
        assert is_safe_segment(name)
    for name in ("..", ".", "a/b", "a\\b", "C:", " x", "", "-x", ".hidden"):
        assert not is_safe_segment(name)


def test_hive_columns_are_url_decoded_and_default_partition_is_null() -> None:
    assert Partition("v=b/k=a%20b", 1, "h").hive_columns() == (("k", "a b"),)
    assert Partition("v=b/k=__HIVE_DEFAULT_PARTITION__", 1, "h").hive_columns() == (("k", None),)


def test_broken_old_builds_do_not_block_the_current_build() -> None:
    old = {"build_id": "b0", "partitions": [{"path": "year=2010", "n_rows": None}]}
    view = parse_manifest("t", _manifest("b2", [old, _build("b2", ["v=b2"])]))
    assert view.ok and "b0" not in view.builds
    bad_rows = {"build_id": "b2", "partitions": [{"path": "v=b2", "n_rows": None}]}
    assert parse_manifest("t", _manifest("b2", [bad_rows])).status is ManifestStatus.BAD_PARTITION


def test_snapshot_id_matches_the_equity_catalog_source_of_truth() -> None:
    from equity.catalog import snapshot_id as sot

    builds = {"price_daily": "e_1", "security": "m_2", "adj_factor": "b_3"}
    assert snapshot_id(builds) == sot(builds)
