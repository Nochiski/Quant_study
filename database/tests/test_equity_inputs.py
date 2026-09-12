"""T0.2~T0.4 — MANIFEST 해석 · `_pinned/` 하드링크 · stage 뷰 생성."""
from __future__ import annotations

import errno
import json
import os
from dataclasses import asdict
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


# ── `_pinned/` GC (플랜 v1 §8 Task 5.2 · v2 §4 B.3) ──────────────────────────

def _pin_versions(equity_root: Path, table: str) -> list[str]:
    d = equity_root / inputs.PINNED_DIR / table
    return sorted(p.name[2:] for p in d.iterdir() if p.is_dir() and p.name.startswith("v="))


def _fake_pin(equity_root: Path, table: str, build_id: str) -> None:
    """`_pinned/<table>/v=<build>/` 골격만 만든다 — GC 는 파일 내용을 안 본다."""
    vdir = equity_root / inputs.PINNED_DIR / table / f"v={build_id}"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "part0.parquet").write_bytes(b"x" * 10)
    path = equity_root / inputs.PINNED_DIR / table / "MANIFEST.json"
    m = manifest.load(path)
    rec = manifest.BuildRecord(build_id=build_id, snapshot_id="", rules_version="v_test",
                               built_at_utc="2026-09-11T00:00:00+00:00", n_rows=1,
                               content_hash="1:0",
                               partitions=[{"path": f"v={build_id}", "n_rows": 1}])
    kept = [b for b in m.builds if b.build_id != build_id] + [rec]
    path.write_text(json.dumps({"table": table, "current_build": build_id, "keep": len(kept),
                                "builds": [asdict(b) for b in kept]}, ensure_ascii=False),
                    encoding="utf-8")


def _fake_equity_build(equity_root: Path, table: str, build_id: str,
                       pinned_inputs: dict[str, str]) -> None:
    """equity 산출 표의 MANIFEST 를 손으로 쓴다 — `inputs` 가 GC 의 참조 축이다."""
    path = equity_root / table / "MANIFEST.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = manifest.BuildRecord(build_id=build_id, snapshot_id="", rules_version="v_test",
                               built_at_utc="2026-09-11T00:00:00+00:00", n_rows=1,
                               content_hash="1:0", partitions=[], inputs=dict(pinned_inputs))
    path.write_text(json.dumps({"table": table, "current_build": build_id, "keep": 3,
                                "builds": [asdict(rec)]}, ensure_ascii=False), encoding="utf-8")


def test_gc_pinned는_참조_보호_최신keep만_남긴다(tmp_path: Path) -> None:
    eq = tmp_path / "equity"
    for bid in ("b_20260901T000000_000000Z", "e_20260902T000000_000000Z",
                "m_20260903T000000_000000Z", "e_20260904T000000_000000Z",
                "m_20260905T000000_000000Z"):
        _fake_pin(eq, "stg_price_daily", bid)
    # 현행 equity 빌드가 가장 **오래된** 판을 가리킨다 — 최신 keep 축과 겹치지 않아야 축이 갈린다
    _fake_equity_build(eq, "price_daily", "m_20260905T000000_000000Z",
                       {"stg_price_daily": "b_20260901T000000_000000Z"})
    r = inputs.gc_pinned(eq, keep=1, protect=["e_20260902T000000_000000Z"])
    assert _pin_versions(eq, "stg_price_daily") == ["b_20260901T000000_000000Z",
                                                    "e_20260902T000000_000000Z",
                                                    "m_20260905T000000_000000Z"]
    assert [p.build_id for p in r.removed] == ["m_20260903T000000_000000Z",
                                               "e_20260904T000000_000000Z"]
    assert [p.build_id for p in r.kept_referenced] == ["b_20260901T000000_000000Z"]
    assert [p.build_id for p in r.kept_protected] == ["e_20260902T000000_000000Z"]
    assert [p.build_id for p in r.kept_recent] == ["m_20260905T000000_000000Z"]
    assert r.n_removed == 2 and r.freed_bytes == 20 and r.errors == ()
    assert r.scanned_tables == ("stg_price_daily",)


def test_gc_pinned는_MANIFEST에서도_지운_판을_뺀다(tmp_path: Path) -> None:
    eq = tmp_path / "equity"
    for bid in ("b_20260901T000000_000000Z", "b_20260902T000000_000000Z"):
        _fake_pin(eq, "stg_index_daily", bid)
    inputs.gc_pinned(eq, keep=1)
    m = manifest.load(eq / inputs.PINNED_DIR / "stg_index_daily" / "MANIFEST.json")
    assert [b.build_id for b in m.builds] == ["b_20260902T000000_000000Z"]
    assert m.current_build == "b_20260902T000000_000000Z"
    with pytest.raises(FileNotFoundError, match="pinned build not found"):
        inputs.load_pinned(eq, "stg_index_daily", "b_20260901T000000_000000Z")


def test_gc_pinned는_옛_current_build도_옮긴다(tmp_path: Path) -> None:
    """`_pinned/` 의 current_build 는 '마지막으로 고정한 판' 이지 '참조되는 판' 이 아니다."""
    eq = tmp_path / "equity"
    _fake_pin(eq, "stg_index_daily", "b_20260901T000000_000000Z")
    _fake_pin(eq, "stg_index_daily", "b_20260902T000000_000000Z")   # 이게 current 가 된다
    _fake_equity_build(eq, "trading_calendar", "b_cal",
                       {"stg_index_daily": "b_20260901T000000_000000Z"})
    r = inputs.gc_pinned(eq, keep=0)
    assert [p.build_id for p in r.removed] == ["b_20260902T000000_000000Z"]
    m = manifest.load(eq / inputs.PINNED_DIR / "stg_index_daily" / "MANIFEST.json")
    assert m.current_build == "b_20260901T000000_000000Z"


def test_gc_pinned는_실물_pin_왕복_뒤에도_참조판을_지키지_않으면_안_된다(
        tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    pinned = inputs.pin(tree.stage_root, eq, "stg_sample")
    _fake_equity_build(eq, "sample_table", "b_eq_1", {"stg_sample": pinned.build_id})
    r = inputs.gc_pinned(eq, keep=0)
    assert r.removed == () and [p.build_id for p in r.kept_referenced] == [pinned.build_id]
    assert inputs.load_pinned(eq, "stg_sample", pinned.build_id) == pinned


def test_gc_pinned는_pinned가_없으면_빈_판정(tmp_path: Path) -> None:
    r = inputs.gc_pinned(tmp_path / "equity")
    assert r.n_removed == 0 and r.scanned_tables == () and r.freed_bytes == 0


def test_gc_pinned는_음수_keep을_거절한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="keep must be >= 0"):
        inputs.gc_pinned(tmp_path / "equity", keep=-1)
