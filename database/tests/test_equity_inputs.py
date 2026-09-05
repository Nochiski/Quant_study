"""T0.2~T0.4 — MANIFEST 해석 · `_pinned/` 하드링크 · stage 뷰 생성."""
from __future__ import annotations

import errno
import os
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import inputs
from stage import manifest

COLS = ("k", "val", "available_date", "available_basis")
ROWS: list[dict[str, object]] = [
    {"k": 1, "val": "a", "available_date": date(2020, 1, 3), "available_basis": "measured"},
    {"k": 2, "val": "b", "available_date": date(2020, 1, 6), "available_basis": "measured"},
    {"k": 3, "val": "c", "available_date": date(2021, 1, 4), "available_basis": "measured"},
]
DATED: list[dict[str, object]] = [
    {"date": date(2020, 1, 3), "ticker": "005930"},
    {"date": date(2021, 1, 4), "ticker": "005930"},
]   # date_axis — year(date) 로 2020·2021 두 파티션이 나온다


def test_MANIFEST_partitions만_읽는다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_dated", DATED, "date_axis")
    pb = inputs.resolve(tree.stage_root, "stg_dated")
    assert pb.build_id == tree.build_id
    assert [p.name for p in pb.partition_paths] == ["year=2020", "year=2021"]
    assert pb.has_year_axis is True
    assert pb.meta["table"] == "stg_dated"         # 첫 파티션 _meta.json 을 실었다


def test_구버전_v디렉토리를_무시한다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    stale = tree.table_root / "v=b_stage_0000"     # MANIFEST 에 없는 구버전
    stale.mkdir()
    (stale / "part0.parquet").write_bytes(b"garbage")
    pb = inputs.resolve(tree.stage_root, "stg_sample")
    assert pb.partition_paths == (tree.table_root / f"v={tree.build_id}",)
    assert all("b_stage_0000" not in g for g in pb.globs)


def test_커밋된_빌드_없으면_예외(tmp_path: Path) -> None:
    (tmp_path / "stage" / "stg_none").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="no committed build"):
        inputs.resolve(tmp_path / "stage", "stg_none")


def test_pin은_하드링크다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    equity_root = tmp_path / "equity"
    pb = inputs.pin(tree.stage_root, equity_root, "stg_sample")
    linked = pb.root / "part0.parquet"
    src = tree.table_root / f"v={tree.build_id}" / "part0.parquet"
    assert os.stat(linked).st_nlink == 2
    assert os.stat(linked).st_ino == os.stat(src).st_ino
    pinned_manifest = manifest.load(equity_root / "_pinned" / "stg_sample" / "MANIFEST.json")
    assert pinned_manifest.current_build == tree.build_id
    assert [b.build_id for b in pinned_manifest.builds] == [tree.build_id]


def test_pin_후_원본_v디렉토리_삭제해도_읽힌다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    pb = inputs.pin(tree.stage_root, tmp_path / "equity", "stg_sample")
    for i in range(1, 4):                          # keep=3 GC 가 v=b_stage_0001 을 rmtree 한다
        manifest.commit(tree.table_root, manifest.BuildRecord(
            build_id=f"b_later_{i}", snapshot_id="", rules_version="v_test",
            built_at_utc="2026-09-05T00:00:00+00:00", n_rows=0, content_hash="0:empty"))
    assert not (tree.table_root / f"v={tree.build_id}").exists()
    con = duckdb.connect()
    try:
        n = con.execute(f"SELECT count(*) FROM read_parquet('{pb.globs[0]}')").fetchone()
    finally:
        con.close()
    assert n is not None and n[0] == len(ROWS)


def test_다른_파일시스템이면_OSError(tmp_path: Path, make_stage_tree, monkeypatch) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)

    def _exdev(src: object, dst: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", _exdev)
    with pytest.raises(OSError) as e:
        inputs.pin(tree.stage_root, tmp_path / "equity", "stg_sample")
    assert e.value.errno == errno.EXDEV


def test_같은_build_재pin은_noop(tmp_path: Path, make_stage_tree, monkeypatch) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    equity_root = tmp_path / "equity"
    first = inputs.pin(tree.stage_root, equity_root, "stg_sample")
    calls: list[object] = []
    real_link = os.link
    monkeypatch.setattr(os, "link", lambda s, d: calls.append(d) or real_link(s, d))
    second = inputs.pin(tree.stage_root, equity_root, "stg_sample")
    assert calls == []                             # 재링크하지 않는다
    assert second == first


def test_선언_컬럼만_투영한다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    pb = inputs.pin(tree.stage_root, tmp_path / "equity", "stg_sample")
    con = duckdb.connect()
    try:
        inputs.create_views(con, {"stg_sample": pb}, {"stg_sample": ["k", "val"]})
        cols = [r[0] for r in con.execute('DESCRIBE "stg_sample"').fetchall()]
        assert cols == ["k", "val"]
    finally:
        con.close()


def test_year_하이브_컬럼이_붙는다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_dated", DATED, "date_axis")
    pb = inputs.pin(tree.stage_root, tmp_path / "equity", "stg_dated")
    con = duckdb.connect()
    try:
        inputs.create_views(con, {"stg_dated": pb}, {"stg_dated": ["date"]})
        cols = [r[0] for r in con.execute('DESCRIBE "stg_dated"').fetchall()]
        assert cols == ["date", "year"]            # 선언 밖이지만 파티션 축은 노출한다
        years = con.execute('SELECT year FROM "stg_dated" ORDER BY 1').fetchall()
        assert [r[0] for r in years] == [2020, 2021]
    finally:
        con.close()


def test_원장_실명_선언은_missing으로_잡힌다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    pb = inputs.pin(tree.stage_root, tmp_path / "equity", "stg_sample")
    con = duckdb.connect()
    try:
        # `stck_prpr` 은 원장 실명 — stage 컬럼명이 아니다. 뷰 생성이 죽는 대신 EG0 가 잡는다.
        inputs.create_views(con, {"stg_sample": pb}, {"stg_sample": ["k", "stck_prpr"]})
        assert inputs.declared_columns_missing(con, "stg_sample", ["k", "stck_prpr"]) \
            == ["stck_prpr"]
        assert inputs.declared_columns_missing(con, "stg_sample", ["k"]) == []
    finally:
        con.close()


def test_load_pinned는_고정한_빌드를_다시_연다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    equity_root = tmp_path / "equity"
    pinned = inputs.pin(tree.stage_root, equity_root, "stg_sample")
    assert inputs.load_pinned(equity_root, "stg_sample", tree.build_id) == pinned
    with pytest.raises(FileNotFoundError, match="pinned build not found"):
        inputs.load_pinned(equity_root, "stg_sample", "b_nope")


def test_pinned_하이브_v축은_stage_뷰에_안_뜬다(tmp_path: Path, make_stage_tree) -> None:
    """`_pinned/<t>/v=<build>/` 경로의 `v` 는 build_id 축이지 stage 컬럼이 아니다.

    노출하면 같은 이름의 stage 컬럼이 build_id 문자열로 조용히 덮이고 픽스처가 통과해 버린다.
    """
    rows = [{"k": 1, "v": "a"}, {"k": 2, "v": "b"}]
    tree = make_stage_tree(tmp_path, "stg_collide", rows)
    pb = inputs.pin(tree.stage_root, tmp_path / "equity", "stg_collide")
    con = duckdb.connect()
    try:
        inputs.create_views(con, {"stg_collide": pb}, {})
        cols = [r[0] for r in con.execute('DESCRIBE "stg_collide"').fetchall()]
        assert cols == ["k"]                        # 파일 컬럼 `v` 는 하이브 축과 이름이 겹친다
        assert inputs.declared_columns_missing(con, "stg_collide", ["v"]) == ["v"]
    finally:
        con.close()
