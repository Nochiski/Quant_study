"""scripts/daily_ledger.sh — 소스별 단계가 서로 막지 않는지(플랜 2026-09-30 T-K3).

임시 루트(`QL_LEDGER_ROOT`)에 대역 `.venv/bin/python`·`scripts/*.sh` 를 두고 진짜 스크립트를 돌린다.
네트워크·원장은 쓰지 않는다. 대역 python 은 `-m <모듈>` 을 calls.txt 에 적고 `RC_<모듈>` 환경변수로 rc 를 낸다.
스크립트 경로 호출(`src/<파일>.py`)은 경로를 적고 `RC_<파일>` 로 rc 를 낸다.
런로그 "D 이미 수집" 검사의 rc 는 `RC_recent`(기본 1 = 미수집)다.
요일은 `QL_WEEKDAY` 로 주입한다(기본 3 = 수요일 — 테스트를 돌리는 요일과 무관하게 고정).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_ledger.sh"

_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *runlog.start*) echo 7 ;;
    *runlog.recent*) exit "${RC_recent:-1}" ;;
    *runlog.finish*) echo "$4|$5" >> "$QL_HOME/runlog.txt" ;;
  esac
  exit 0
fi
if [ "$1" != "-m" ]; then
  echo "$1" >> "$QL_HOME/calls.txt"
  var="RC_$(basename "$1" .py)"
  exit "${!var:-0}"
fi
echo "$2" >> "$QL_HOME/calls.txt"
var="RC_${2//./_}"
exit "${!var:-0}"
"""


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "ql"
    (root / "scripts").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "daily_ledger.sh")
    stubs = {
        ".venv/bin/python": _PY,
        "scripts/sync_calendar.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/daily_wise.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2" >> notify.txt\n',
        "scripts/dart_company_gap.sh": "#!/usr/bin/env bash\necho gap >> calls.txt\n",
    }
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _run(tmp_path: Path, *args: str, weekday: int = 3,
         **rcs: int) -> tuple[int, list[str], str, str]:
    root = _root(tmp_path)
    env = dict(os.environ, QL_LEDGER_ROOT=str(root), QL_RAW_LOCK_HELD="1",
               QL_WEEKDAY=str(weekday), **{f"RC_{k}": str(v) for k, v in rcs.items()})
    env.pop("QL_SKIP_KW", None)
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", "20260929",
                        *args],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    def read(name: str) -> str:
        f = root / name
        return f.read_text(encoding="utf-8") if f.exists() else ""
    return p.returncode, read("calls.txt").split(), read("notify.txt"), read("runlog.txt")


def test_all_steps_ok(tmp_path: Path) -> None:
    rc, calls, notify, runlog = _run(tmp_path)
    assert rc == 0
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert notify.startswith("info|") and runlog.startswith("ok|")


def test_kis_failure_does_not_stop_dart(tmp_path: Path) -> None:
    """09-25~28·09-30 재현 — 신용잔고 판정 실패(rc 2) 뒤에도 DART·회사정보 공백 메우기가 돈다."""
    rc, calls, notify, runlog = _run(tmp_path, daily_kis_daily=2)
    assert rc == 2
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert notify.startswith("crit|daily_ledger 실패: kis credit(rc=2)")
    assert runlog == "failed|kis credit(rc=2)\n"


def test_dart_failure_skips_only_company_gap(tmp_path: Path) -> None:
    rc, calls, notify, _ = _run(tmp_path, daily_dart_daily=2)
    assert rc == 2
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily"]
    assert "dart(rc=2)" in notify


def test_every_failed_step_is_reported(tmp_path: Path) -> None:
    rc, calls, notify, runlog = _run(tmp_path, daily_kw_daily=1, daily_kis_daily=2)
    assert rc == 2
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert "kiwoom fetch(rc=1), kis credit(rc=2)" in notify
    assert runlog == "failed|kiwoom fetch(rc=1), kis credit(rc=2)\n"


def test_monday_refreshes_dart_universe_before_company_gap(tmp_path: Path) -> None:
    """A-01(10-06) — 월요일엔 DART 번호표(dart_corp_map)를 갱신한다.

    회사 정보 공백 메우기(gap)보다 앞이어야 신규 corp 가 같은 날 채워진다.
    """
    rc, calls, notify, runlog = _run(tmp_path, weekday=1)
    assert rc == 0
    assert calls == ["src/dart_universe.py",
                     "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert notify.startswith("info|") and runlog.startswith("ok|")


@pytest.mark.parametrize("weekday", [2, 3, 4, 5, 6, 7])
def test_dart_universe_not_called_on_other_days(tmp_path: Path, weekday: int) -> None:
    rc, calls, _, _ = _run(tmp_path, weekday=weekday)
    assert rc == 0
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]


def test_dry_run_skips_dart_universe_on_monday(tmp_path: Path) -> None:
    """dart_universe 는 dry-run 이 없고 원장(dart.db·corps.txt)을 바로 쓴다."""
    rc, calls, _, _ = _run(tmp_path, "--dry-run", weekday=1)
    assert rc == 0
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]


def test_monday_refresh_runs_even_when_d_already_collected(tmp_path: Path) -> None:
    """평소 월요일 06:00 은 D(금요일)를 토요일에 이미 받아 건너뛰는 날이다 — 그날도 돈다."""
    rc, calls, notify, runlog = _run(tmp_path, weekday=1, recent=0)
    assert rc == 0
    assert calls == ["src/dart_universe.py"]
    assert notify.startswith("info|daily_ledger 건너뜀") and runlog == ""


def test_dart_universe_failure_is_reported_but_chain_continues(tmp_path: Path) -> None:
    """daily_wise 와 같은 처리 — 뒤 단계는 돌고, FAILED 에 남아 crit·런로그 failed 가 된다."""
    rc, calls, notify, runlog = _run(tmp_path, weekday=1, dart_universe=1)
    assert rc == 2
    assert calls == ["src/dart_universe.py",
                     "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert notify.startswith("crit|daily_ledger 실패: dart universe(월)(rc=1)")
    assert runlog == "failed|dart universe(월)(rc=1)\n"
