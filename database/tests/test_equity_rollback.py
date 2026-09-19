"""DEFECT-C03 — equity 전량이 중간에 실패했을 때의 판 되돌리기 (2026-09-19 감사).

`equity_rebuild_all.sh` 는 표를 하나씩 짓고 성공할 때마다 그 표의 `current_build` 를 즉시 바꾼다.
중간에 실패하면 앞선 표는 새 판, 뒤의 표는 어제 판으로 남는데 `inputs.py`·`build_chain.sh` 가
"MANIFEST 포인터가 정본" 을 계약으로 못박아 두었으므로 소비자는 **표마다 다른 날의 판**을 보게
된다. 실측: 09-11 확정 빌드가 14표 커밋 뒤 `credit_daily` 에서 멈췄고 그 혼합 상태가 09-13
03:35 ~ 09-16 15:14 약 3.5일 유지됐다.

되돌리기는 `MANIFEST.current_build` 만 직전 판으로 옮긴다 — `v=` 디렉터리는 지우지 않는다
(keep=10 이라 이전 판이 그대로 있고, 지우면 그 판으로 다시 못 돌아간다).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from equity import rollback
from stage import manifest


def _record(build_id: str) -> manifest.BuildRecord:
    return manifest.BuildRecord(
        build_id=build_id, snapshot_id="", rules_version="e1.15.0",
        built_at_utc="2026-09-19T00:00:00+00:00", n_rows=1, content_hash="1:x")


def _table(root: Path, name: str, build_ids: list[str], keep: int = 10) -> Path:
    t = root / name
    for b in build_ids:
        (t / f"v={b}").mkdir(parents=True)
        manifest.commit(t, _record(b), keep=keep)
    return t


def test_직전_판으로_포인터만_되돌린다(tmp_path: Path) -> None:
    t = _table(tmp_path, "price_daily", ["m_1", "e_2", "m_3"])
    prev = rollback.rollback_table(t)
    assert prev == "e_2"
    assert manifest.load(t / "MANIFEST.json").current_build == "e_2"
    # `v=` 는 전부 남는다 — 지우면 되돌린 판이 곧 GC 대상이 된다
    assert {d.name for d in t.iterdir() if d.is_dir()} == {"v=m_1", "v=e_2", "v=m_3"}


def test_지정한_판으로도_되돌린다(tmp_path: Path) -> None:
    t = _table(tmp_path, "price_daily", ["m_1", "e_2", "m_3"])
    assert rollback.rollback_table(t, to_build_id="m_1") == "m_1"
    assert manifest.load(t / "MANIFEST.json").current_build == "m_1"


def test_판이_하나뿐이면_되돌리지_않는다(tmp_path: Path) -> None:
    t = _table(tmp_path, "price_daily", ["m_1"])
    assert rollback.rollback_table(t) is None
    assert manifest.load(t / "MANIFEST.json").current_build == "m_1"


def test_없는_판으로는_되돌리지_않는다(tmp_path: Path) -> None:
    t = _table(tmp_path, "price_daily", ["m_1", "m_2"])
    with pytest.raises(ValueError, match="m_9"):
        rollback.rollback_table(t, to_build_id="m_9")
    assert manifest.load(t / "MANIFEST.json").current_build == "m_2"


def test_MANIFEST가_없으면_건너뛴다(tmp_path: Path) -> None:
    assert rollback.rollback_table(tmp_path / "없는표") is None


def _summary(path: Path, rows: list[tuple[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{t}\t{rc}\t1\t-\n" for t, rc in rows), encoding="utf-8")


def test_pass_요약의_성공_표만_되돌린다(tmp_path: Path) -> None:
    """실패한 표는 이번 판을 커밋하지 못했으므로 되돌릴 것이 없다 — 건드리면 어제 판을 잃는다."""
    eq = tmp_path / "equity"
    _table(eq, "trading_calendar", ["m_1", "m_2"])
    _table(eq, "price_daily", ["m_1", "m_2"])
    _table(eq, "credit_daily", ["m_1"])          # 이번 판 실패 — 커밋 없음
    _summary(tmp_path / "logs" / "equity" / "rebuild_p1" / "summary.tsv",
             [("trading_calendar", 0), ("price_daily", 0), ("credit_daily", 1)])
    out = rollback.rollback_pass(eq, "p1", log_root=tmp_path / "logs" / "equity")
    assert out == {"trading_calendar": "m_1", "price_daily": "m_1"}
    assert manifest.load(eq / "credit_daily" / "MANIFEST.json").current_build == "m_1"


def test_요약이_없으면_오류다(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rollback.rollback_pass(tmp_path / "equity", "p9", log_root=tmp_path / "logs")


def test_CLI가_pass를_되돌린다(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from equity.__main__ import main

    eq = tmp_path / "equity"
    _table(eq, "price_daily", ["m_1", "m_2"])
    _summary(tmp_path / "logs" / "equity" / "rebuild_p1" / "summary.tsv", [("price_daily", 0)])
    rc = main(["--root", str(eq), "rollback", "--pass", "p1",
               "--log-root", str(tmp_path / "logs" / "equity")])
    assert rc == 0
    assert manifest.load(eq / "price_daily" / "MANIFEST.json").current_build == "m_1"
    assert "price_daily" in capsys.readouterr().out
    # MANIFEST 는 그대로 읽히는 json 이다(손상 없음)
    json.loads((eq / "price_daily" / "MANIFEST.json").read_text(encoding="utf-8"))


def test_before_맵이_있으면_시작_시점_판으로_되돌린다(tmp_path: Path) -> None:
    """아침 확정 빌드가 중간에 실패하면 "직전 판" 은 전날 저녁 잠정판(e_)이다 — 그리로 가면 MANIFEST 를
    직접 읽는 공유 소비자가 확정 자리에서 잠정판을 본다(리뷰 REC-13). 패스 시작 시점 판이 정답이다."""
    eq = tmp_path / "equity"
    _table(eq, "price_daily", ["m_1", "e_2", "m_3"])        # m_3 = 이번 패스가 커밋한 판
    _table(eq, "flow_daily", ["m_1", "e_2", "m_3"])
    before = tmp_path / "before.json"
    before.write_text(json.dumps({"price_daily": "m_1", "flow_daily": "gc_gone"}), encoding="utf-8")
    _summary(tmp_path / "logs" / "equity" / "rebuild_p1" / "summary.tsv",
             [("price_daily", 0), ("flow_daily", 0)])
    out = rollback.rollback_pass(eq, "p1", log_root=tmp_path / "logs" / "equity", before=before)
    assert out == {"price_daily": "m_1", "flow_daily": "e_2"}     # 시작 판 GC 됨 → 직전 판 폴백
    assert manifest.load(eq / "price_daily" / "MANIFEST.json").current_build == "m_1"
