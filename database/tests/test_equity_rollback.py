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
    """아침 확정 빌드가 중간에 실패하면 "직전 판" 은 전날 저녁 잠정판(e_)이다 — 그리로 가면
    MANIFEST 를 직접 읽는 공유 소비자가 확정 자리에서 잠정판을 본다(리뷰 REC-13).
    패스 시작 시점 판이 정답이다."""
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


TABLES3 = ["price_daily", "corp_event", "adj_factor"]


def _latest_morning(path: Path, builds: dict[str, str]) -> Path:
    """build_chain deliver_step 이 아침 판 성공 때만 쓰는 인계 포인터와 같은 모양."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": "20261006", "basis": "morning", "equity_builds": builds}),
                    encoding="utf-8")
    return path


def _commit(t: Path, build_id: str) -> None:
    (t / f"v={build_id}").mkdir()
    manifest.commit(t, _record(build_id), keep=10)


def _currents(eq: Path, tables: list[str]) -> dict[str, str | None]:
    return {t: manifest.load(eq / t / "MANIFEST.json").current_build for t in tables}


def _failed_morning_pass(tmp_path: Path, eq: Path, latest: Path, name: str,
                         commit: str) -> dict[str, str]:
    """아침 패스: before 기록 → 첫 표만 `commit` 커밋 → 둘째 표 실패(셋째 미도달) → CLI 롤백."""
    from equity.__main__ import main

    before = tmp_path / f"before_{name}.json"
    before.write_text(json.dumps(rollback.pass_start_targets(eq, TABLES3, "morning",
                                                             latest_morning=latest)),
                      encoding="utf-8")
    _commit(eq / TABLES3[0], commit)
    _summary(tmp_path / "logs" / "equity" / f"rebuild_{name}" / "summary.tsv",
             [(TABLES3[0], 0), (TABLES3[1], 1)])
    rc = main(["--root", str(eq), "rollback", "--pass", name, "--before", str(before),
               "--log-root", str(tmp_path / "logs" / "equity"), "--basis", "morning"])
    assert rc == 0
    return json.loads(before.read_text(encoding="utf-8"))


def test_아침_패스의_되돌릴_판은_latest_morning_의_equity_builds다(tmp_path: Path) -> None:
    """C-01(N-25 Q4): '직전 확정판' = 마지막으로 **완료된** 아침 판(build_chain 이 stage·equity 가
    다 ok 일 때만 쓰는 latest_morning.json). 그 판이 builds[] 에 없거나(GC) 표가 없으면 시작
    current(옛 동작)."""
    eq = tmp_path / "equity"
    _table(eq, "price_daily", ["m_1", "e_2"])
    _table(eq, "corp_event", ["m_1", "e_2"])
    _table(eq, "adj_factor", ["e_2"])                          # 확정판 m_1 이 GC 됨
    _table(eq, "fin_std", ["m_1", "e_2"])                      # latest 에 없는 표
    latest = _latest_morning(tmp_path / "deliver" / "latest_morning.json",
                             {"price_daily": "m_1", "corp_event": "m_1", "adj_factor": "m_1"})
    got = rollback.pass_start_targets(eq, [*TABLES3, "fin_std"], "morning", latest_morning=latest)
    assert got == {"price_daily": "m_1", "corp_event": "m_1", "adj_factor": "e_2",
                   "fin_std": "e_2"}


def test_latest_morning_이_없거나_못_읽으면_시작_current다(tmp_path: Path) -> None:
    eq = tmp_path / "equity"
    _table(eq, "price_daily", ["m_1", "e_2"])
    missing = tmp_path / "없음" / "latest_morning.json"
    assert rollback.pass_start_targets(eq, ["price_daily"], "morning",
                                       latest_morning=missing) == {"price_daily": "e_2"}
    broken = tmp_path / "latest_morning.json"
    broken.write_text("{깨진", encoding="utf-8")
    assert rollback.pass_start_targets(eq, ["price_daily"], "morning",
                                       latest_morning=broken) == {"price_daily": "e_2"}


def test_저녁_패스와_수동_패스의_되돌릴_판은_시작_current_그대로다(tmp_path: Path) -> None:
    eq = tmp_path / "equity"
    _table(eq, "price_daily", ["m_1", "e_2"])
    latest = _latest_morning(tmp_path / "latest_morning.json", {"price_daily": "m_1"})
    for basis in ("evening", "manual"):
        assert rollback.pass_start_targets(eq, ["price_daily"], basis,
                                           latest_morning=latest) == {"price_daily": "e_2"}


def test_아침_패스가_둘째_표에서_실패하면_전_표가_latest_morning_판으로_간다(
        tmp_path: Path) -> None:
    """검토자 재현(09-11 형): 3표 [m_1, e_2], 첫 표 m_3 커밋 → 둘째 표 실패 → 셋째 미도달.
    rc 0 표만 옮기면 앞 = m_1 · 뒤 = e_2 로 섞인다(DEFECT-C03 재발 + 확정 자리에 잠정판).
    아침 롤백은 before 의 **모든 표**를 목표 판으로 — 포인터만이라 판을 잃지 않는다."""
    eq = tmp_path / "equity"
    for t in TABLES3:
        _table(eq, t, ["m_1", "e_2"])
    latest = _latest_morning(tmp_path / "latest_morning.json", dict.fromkeys(TABLES3, "m_1"))
    _failed_morning_pass(tmp_path, eq, latest, "m_d1", "m_3")
    assert _currents(eq, TABLES3) == dict.fromkeys(TABLES3, "m_1")
    assert {d.name for d in (eq / "price_daily").iterdir() if d.is_dir()} == {
        "v=m_1", "v=e_2", "v=m_3"}                             # 실패 판도 지우지 않는다


def test_실패한_아침_패스가_남긴_m은_다음_날에도_목표가_아니다(tmp_path: Path) -> None:
    """다음 날 저녁 e_4 커밋 뒤 아침이 또 실패해도 목표는 latest_morning(m_1) — 실패 패스가 남긴
    m_3 을 고르면 m_3/m_1 이 다시 섞인다."""
    eq = tmp_path / "equity"
    for t in TABLES3:
        _table(eq, t, ["m_1", "e_2"])
    latest = _latest_morning(tmp_path / "latest_morning.json", dict.fromkeys(TABLES3, "m_1"))
    _failed_morning_pass(tmp_path, eq, latest, "m_d1", "m_3")
    for t in TABLES3:
        _commit(eq / t, "e_4")                                 # 다음 날 저녁 잠정판
    before = _failed_morning_pass(tmp_path, eq, latest, "m_d2", "m_5")
    assert before == dict.fromkeys(TABLES3, "m_1")
    assert _currents(eq, TABLES3) == dict.fromkeys(TABLES3, "m_1")


def test_아침_롤백은_커밋_안_한_표의_목표가_사라졌으면_건드리지_않는다(tmp_path: Path) -> None:
    """패스 도중 GC 로 목표 판이 사라진 경우: 커밋한 표는 직전 판 폴백(옛 동작), 커밋 안 한 표는
    그대로 — 한 칸 되돌리면 멀쩡한 판을 잃는다."""
    eq = tmp_path / "equity"
    for t in TABLES3:
        _table(eq, t, ["m_1", "e_2"])
    before = tmp_path / "before.json"
    before.write_text(json.dumps(dict.fromkeys(TABLES3, "gc_gone")), encoding="utf-8")
    _commit(eq / TABLES3[0], "m_3")
    _summary(tmp_path / "logs" / "equity" / "rebuild_p" / "summary.tsv",
             [(TABLES3[0], 0), (TABLES3[1], 1)])
    out = rollback.rollback_pass(eq, "p", log_root=tmp_path / "logs" / "equity", before=before,
                                 basis="morning")
    assert out == {TABLES3[0]: "e_2"}
    assert _currents(eq, TABLES3) == dict.fromkeys(TABLES3, "e_2")


def test_저녁_패스_실패_롤백은_지금처럼_rc0_표만이다(tmp_path: Path) -> None:
    """회귀 가드: 저녁·수동 패스는 before 에 다른 판이 적혀 있어도 rc 0 표만 옮긴다."""
    eq = tmp_path / "equity"
    for t in TABLES3:
        _table(eq, t, ["m_1", "e_2"])
    before = tmp_path / "before.json"
    before.write_text(json.dumps({TABLES3[0]: "e_2", TABLES3[1]: "m_1", TABLES3[2]: "m_1"}),
                      encoding="utf-8")
    _commit(eq / TABLES3[0], "e_3")
    _summary(tmp_path / "logs" / "equity" / "rebuild_p" / "summary.tsv",
             [(TABLES3[0], 0), (TABLES3[1], 1)])
    for basis in ("evening", "manual"):
        out = rollback.rollback_pass(eq, "p", log_root=tmp_path / "logs" / "equity",
                                     before=before, basis=basis)
        assert _currents(eq, TABLES3) == dict.fromkeys(TABLES3, "e_2")
        assert out == ({TABLES3[0]: "e_2"} if basis == "evening" else {})
