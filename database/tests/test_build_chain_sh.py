"""scripts/build_chain.sh — 최신판 포인터는 D 가 앞서거나 같을 때만 갱신(배포 묶음 3 T14, N-24 3.8).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역 `.venv/bin/python`·`scripts/*.sh` 를 두고
진짜 스크립트를 돌린다. 대역 python 은 스냅샷·GC heredoc 과 `-m` 모듈(stage.health·equity)만
가로채고, 인계(deliver)·완료 신호(ready) heredoc 은 진짜 python 으로 돌린다 — 판정 대상인 포인터
갱신 코드는 실물이다.
빌드 락은 QL_BUILD_LOCK_HELD=1 로 건너뛴다(운영 락 `/tmp/quant_ledger_build.lock` 을 잡지 않는다).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_chain.sh"
KST = dt.timezone(dt.timedelta(hours=9))

# 대역 python — `$PY - 인자 <<'PY'` heredoc 중 스냅샷은 가짜 id 를 쓰고 GC 는 건너뛴다.
# 나머지 heredoc(인계·완료 신호)은 진짜 python(REAL_PY)으로, `-m` 모듈은 rc 0 으로 끝낸다
# (stage.health 만 RC_STAGE_HEALTH 로 rc 를 바꿀 수 있다).
_PY = """#!/usr/bin/env bash
if [ "$1" = "-" ]; then
  code=$(cat)
  case "$code" in
    *make_snapshot*) echo "$FAKE_SNAP" > "$2"; echo "  스냅샷 $FAKE_SNAP"; exit 0 ;;
    *gc_pinned*) exit 0 ;;
  esac
  shift
  exec "$REAL_PY" - "$@" <<<"$code"
fi
if [ "$1" = "-m" ] && [ "$2" = "stage.health" ]; then exit "${RC_STAGE_HEALTH:-0}"; fi
exit 0
"""


def _root(home: Path) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", ".venv/bin", "data/stage", "data/equity"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "build_chain.sh")
    stubs = {
        ".venv/bin/python": _PY,
        "scripts/run_stage_all.sh": ("#!/usr/bin/env bash\nmkdir -p logs/stage_all && "
                                     "echo stage > logs/stage_all/summary.tsv\n"),
        "scripts/equity_rebuild_all.sh": '#!/usr/bin/env bash\nexit "${RC_EQUITY:-0}"\n',
        "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2" >> notify.txt\n',
    }
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _chain(home: Path, d: str, snap: str, basis: str = "morning",
           **rcs: str) -> subprocess.CompletedProcess[str]:
    """build_chain 한 번. 스냅샷 id 로 어느 실행의 판인지 가린다. rcs 는 대역 rc(RC_*)."""
    (home / "tmp").mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"), QL_BUILD_LOCK_HELD="1",
               REAL_PY=sys.executable, FAKE_SNAP=snap, **rcs)
    return subprocess.run(["bash", str(home / "quant-ledger" / "scripts" / "build_chain.sh"),
                           basis, "--date", d],
                          env=env, capture_output=True, text=True, timeout=60, check=False)


def _build(home: Path, d: str, snap: str, basis: str = "morning") -> None:
    """판 D 를 짓고 성공(rc 0)을 확인한다."""
    p = _chain(home, d, snap, basis)
    assert p.returncode == 0, p.stdout + p.stderr


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_old_d_rebuild_keeps_latest_pointer(tmp_path: Path) -> None:
    """G1 — 다음 D 를 지은 뒤 놓친 옛 D 를 손으로 다시 지어도 소비자의 최신판은 다음 D 그대로다.

    옛 D 판은 history/ 에만 남는다(옛 코드는 성공 판이면 날짜와 무관하게 latest 를 덮었다).
    """
    root = _root(tmp_path)
    deliver = root / "data" / "deliver"
    _build(tmp_path, "20261007", snap="snap_new")
    _build(tmp_path, "20261006", snap="snap_old")
    latest = _read(deliver / "latest_morning.json")
    assert (latest["date"], latest["stage_snapshot_id"]) == ("20261007", "snap_new")
    old = _read(deliver / "history" / "20261006_morning.json")
    assert (old["date"], old["stage_snapshot_id"]) == ("20261006", "snap_old")
    assert _read(deliver / "history" / "20261007_morning.json")["stage_snapshot_id"] == "snap_new"
    log = (root / "logs" / "morning" / "build_20261006.log").read_text(encoding="utf-8")
    assert "latest_morning.json 은 더 새 D=20261007 유지" in log


def test_newer_and_same_d_move_latest_pointer(tmp_path: Path) -> None:
    """다음 D 빌드와 같은 D 재빌드는 지금처럼 포인터를 갱신한다."""
    root = _root(tmp_path)
    latest = root / "data" / "deliver" / "latest_morning.json"
    _build(tmp_path, "20261006", snap="snap_a")
    _build(tmp_path, "20261007", snap="snap_b")
    assert (_read(latest)["date"], _read(latest)["stage_snapshot_id"]) == ("20261007", "snap_b")
    _build(tmp_path, "20261007", snap="snap_c")
    assert (_read(latest)["date"], _read(latest)["stage_snapshot_id"]) == ("20261007", "snap_c")


@pytest.mark.parametrize("broken", ["{깨진 JSON", "[]", '{"basis": "morning"}', '{"date": null}'])
def test_unreadable_pointer_is_overwritten(tmp_path: Path, broken: str) -> None:
    """포인터의 D 를 읽을 수 없으면(깨짐·객체 아님·date 없음·null) 지금처럼 새 판으로 덮는다."""
    root = _root(tmp_path)
    latest = root / "data" / "deliver" / "latest_morning.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(broken, encoding="utf-8")
    _build(tmp_path, "20261006", snap="snap_x")
    assert (_read(latest)["date"], _read(latest)["stage_snapshot_id"]) == ("20261006", "snap_x")


# ── 공유 소비자 완료 신호 `_READY.json` (stage·equity 두 루트, 같은 규칙) ─────────────

def _set_current(root: Path, build_id: str) -> None:
    """두 루트에 표 1개짜리 MANIFEST 포인터를 둔다.

    완료 신호의 builds 로 어느 실행이 썼는지 가린다.
    """
    for sub in ("stage", "equity"):
        p = root / "data" / sub / "t1" / "MANIFEST.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"current_build": build_id}), encoding="utf-8")


def _ready(root: Path) -> list[dict]:
    return [_read(root / "data" / sub / "_READY.json") for sub in ("stage", "equity")]


def test_old_d_rebuild_keeps_ready_signals(tmp_path: Path) -> None:
    """옛 D 재빌드는 공유 소비자의 완료 신호(stage·equity `_READY.json`)도 되돌리지 않는다.

    인계 포인터와 같은 규칙(N-24 3.8). 옛 코드는 성공 판이면 날짜와 무관하게 두 신호를 덮었다.
    """
    root = _root(tmp_path)
    _set_current(root, "b_new")
    _build(tmp_path, "20261007", snap="snap_new")
    _set_current(root, "b_old")
    _build(tmp_path, "20261006", snap="snap_old")
    for r in _ready(root):
        assert (r["date"], r["builds"]) == ("20261007", {"t1": "b_new"})
    log = (root / "logs" / "morning" / "build_20261006.log").read_text(encoding="utf-8")
    assert log.count("완료 신호 유지") == 2


def test_same_d_rebuild_updates_ready_signals(tmp_path: Path) -> None:
    """같은 D 재빌드는 지금처럼 두 완료 신호를 새 판으로 바꾼다."""
    root = _root(tmp_path)
    _set_current(root, "b_1")
    _build(tmp_path, "20261007", snap="snap_1")
    _set_current(root, "b_2")
    _build(tmp_path, "20261007", snap="snap_2")
    for r in _ready(root):
        assert (r["date"], r["builds"]) == ("20261007", {"t1": "b_2"})


@pytest.mark.parametrize("broken", ["{깨진 JSON", '{"basis": "evening", "builds": {}}'])
def test_unreadable_ready_signal_is_rewritten(tmp_path: Path, broken: str) -> None:
    """완료 신호의 D 를 읽을 수 없으면(깨짐·date 없는 옛 형식) 지금처럼 새 판으로 쓴다."""
    root = _root(tmp_path)
    for sub in ("stage", "equity"):
        (root / "data" / sub / "_READY.json").write_text(broken, encoding="utf-8")
    _build(tmp_path, "20261006", snap="snap_x")
    for r in _ready(root):
        assert (r["date"], r["basis"]) == ("20261006", "morning")


# ── 판정 밖 경로: 미래 D 거부 · 실패 판 ─────────────────────────────────────────

def test_future_d_is_refused(tmp_path: Path) -> None:
    """M-4 — 아직 오지 않은 D(오타)로는 판을 짓지 않는다(rc 2, 인자 오류).

    지으면 'D 가 앞설 때만 갱신' 규칙 때문에 최신판 포인터·완료 신호가 그 날짜에 묶인다.
    """
    root = _root(tmp_path)
    # +2일 — 테스트와 스크립트가 '오늘'을 따로 재므로 +1일이면 KST 자정 직전에 깜빡일 수 있다
    future = (dt.datetime.now(KST) + dt.timedelta(days=2)).strftime("%Y%m%d")
    p = _chain(tmp_path, future, snap="snap_future")
    assert p.returncode == 2, p.stdout + p.stderr
    assert f"D={future}" in p.stderr and "오늘" in p.stderr
    assert not (root / "data" / "deliver").exists()
    assert not (root / "data" / "stage" / "_READY.json").exists()


def test_today_d_builds(tmp_path: Path) -> None:
    """저녁 잠정판은 D = 오늘(KST)이다 — 미래 D 거부에 걸리지 않는다."""
    root = _root(tmp_path)
    today = dt.datetime.now(KST).strftime("%Y%m%d")
    _build(tmp_path, today, snap="snap_today", basis="evening")
    assert _read(root / "data" / "deliver" / "latest_evening.json")["date"] == today
    assert [r["date"] for r in _ready(root)] == [today, today]


@pytest.mark.parametrize("fail", [{"RC_STAGE_HEALTH": "2"}, {"RC_EQUITY": "1"}])
def test_failed_build_keeps_latest_and_ready(tmp_path: Path, fail: dict[str, str]) -> None:
    """M-7 — 실패 판(stage 건전성·equity)은 D 가 앞서도 latest·완료 신호를 건드리지 않는다.

    기존 동작의 회귀 테스트다. 실패 판은 history 에만 남고 rc 2(crit)다.
    """
    root = _root(tmp_path)
    _set_current(root, "b_ok")
    _build(tmp_path, "20261006", snap="snap_ok")
    _set_current(root, "b_fail")
    p = _chain(tmp_path, "20261007", snap="snap_fail", **fail)
    assert p.returncode == 2, p.stdout + p.stderr
    latest = _read(root / "data" / "deliver" / "latest_morning.json")
    assert (latest["date"], latest["stage_snapshot_id"]) == ("20261006", "snap_ok")
    for r in _ready(root):
        assert (r["date"], r["builds"]) == ("20261006", {"t1": "b_ok"})
    failed = _read(root / "data" / "deliver" / "history" / "20261007_morning.json")
    assert failed["health"] != {"stage": "ok", "equity": "ok"}
