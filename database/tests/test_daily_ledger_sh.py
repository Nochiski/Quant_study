"""scripts/daily_ledger.sh — 소스별 단계가 서로 막지 않는지(플랜 2026-09-30 T-K3).

임시 루트(`QL_LEDGER_ROOT`)에 대역 `.venv/bin/python`·`scripts/*.sh` 를 두고 진짜 스크립트를 돌린다.
네트워크·원장은 쓰지 않는다. 대역 python 은 `-m <모듈>` 을 calls.txt 에 적고 `RC_<모듈>` 환경변수로 rc 를 낸다.
스크립트 경로 호출(`src/<파일>.py`)은 경로를 적고 `RC_<파일>` 로 rc 를 낸다.
런로그 "D 이미 수집" 검사의 rc 는 `RC_recent`(기본 1 = 미수집)다.
요일은 `QL_WEEKDAY` 로 주입한다(기본 3 = 수요일 — 테스트를 돌리는 요일과 무관하게 고정).
DART 번호표 갱신(A-01)의 런 로그 `-c`(source=dart_universe 판정·성공 기록)만은 진짜 python·진짜
`daily.runlog` 로 넘겨 임시 루트의 `data/raw/daily_run.db` 를 실제로 읽고 쓴다. 마지막 성공은
`univ_age`(KST 달력 며칠 전, 기본 1 — None 이면 기록 없음)로 미리 심는다.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from daily import runlog

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "daily_ledger.sh"

_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *dart_universe*) PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@" ;;
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
    shutil.copy(DB_ROOT / "scripts" / "raw_lock.sh", root / "scripts" / "raw_lock.sh")
    stubs = {
        ".venv/bin/python": _PY,
        "scripts/sync_calendar.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/daily_wise.sh": "#!/usr/bin/env bash\necho daily_wise >> calls.txt\n",
        "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2" >> notify.txt\n',
        "scripts/dart_company_gap.sh": "#!/usr/bin/env bash\necho gap >> calls.txt\n",
    }
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _kst_day(offset: int) -> str:
    """오늘(KST) + offset 일, YYYYMMDD — 스크립트가 런 로그에 적는 KST 달력일과 같은 축."""
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9, days=offset)).strftime("%Y%m%d")


def _run_db(root: Path) -> Path:
    return root / "data" / "raw" / "daily_run.db"


def _seed_universe_ok(root: Path, days_ago: int) -> None:
    """dart_universe 마지막 성공을 KST 달력 `days_ago` 일 전으로 심는다(진짜 runlog)."""
    db = _run_db(root)
    runlog.finish(db, runlog.start(db, date=_kst_day(-days_ago), source="dart_universe"),
                  status="ok")


def _universe_runs(root: Path) -> list[tuple[str, str]]:
    """런 로그의 dart_universe 행 (date, status) — 오래된 것부터."""
    if not _run_db(root).exists():
        return []
    return [(r.date, r.status) for r in reversed(runlog.recent(_run_db(root),
                                                               source="dart_universe"))]


def _env(root: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ, QL_LEDGER_ROOT=str(root), QL_RAW_LOCK_HELD="1",
               REAL_PY=sys.executable, REAL_SRC=str(DB_ROOT / "src"), **extra)
    for k in ("QL_SKIP_KW", "QL_HOME", "PYTHONPATH", "QL_RAW_LOCK_FILE"):
        env.pop(k, None)
    return env


def _run(tmp_path: Path, *args: str, weekday: int = 3, univ_age: int | None = 1,
         **rcs: int) -> tuple[int, list[str], str, str]:
    root = _root(tmp_path)
    if univ_age is not None:
        _seed_universe_ok(root, univ_age)
    env = _env(root, QL_WEEKDAY=str(weekday), **{f"RC_{k}": str(v) for k, v in rcs.items()})
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
    assert calls == ["daily_wise", "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert notify.startswith("info|") and runlog.startswith("ok|")


def test_kis_failure_does_not_stop_dart(tmp_path: Path) -> None:
    """09-25~28·09-30 재현 — 신용잔고 판정 실패(rc 2) 뒤에도 DART·회사정보 공백 메우기가 돈다."""
    rc, calls, notify, runlog = _run(tmp_path, daily_kis_daily=2)
    assert rc == 2
    assert calls == ["daily_wise", "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert notify.startswith("crit|daily_ledger 실패: kis credit(rc=2)")
    assert runlog == "failed|kis credit(rc=2)\n"


def test_dart_failure_skips_only_company_gap(tmp_path: Path) -> None:
    rc, calls, notify, _ = _run(tmp_path, daily_dart_daily=2)
    assert rc == 2
    assert calls == ["daily_wise", "daily.kw_daily", "daily.kis_daily", "daily.dart_daily"]
    assert "dart(rc=2)" in notify


def test_every_failed_step_is_reported(tmp_path: Path) -> None:
    rc, calls, notify, runlog = _run(tmp_path, daily_kw_daily=1, daily_kis_daily=2)
    assert rc == 2
    assert calls == ["daily_wise", "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
    assert "kiwoom fetch(rc=1), kis credit(rc=2)" in notify
    assert runlog == "failed|kiwoom fetch(rc=1), kis credit(rc=2)\n"


# ── A-01 DART 번호표 갱신 — 월요일 + 마지막 성공 7일 초과 재시도(N-27 ⑤ · 배포 묶음 5-2) ─────────
_ALL = ["daily_wise", "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]
_WITH_UNIVERSE = ["daily_wise", "src/dart_universe.py",
                  "daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]


def test_monday_refreshes_dart_universe_before_company_gap(tmp_path: Path) -> None:
    """A-01(10-06) — 월요일엔 마지막 성공이 어제여도 DART 번호표(dart_corp_map)를 갱신한다.

    회사 정보 공백 메우기(gap)보다 앞이어야 신규 corp 가 같은 날 채워진다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=1)
    assert rc == 0
    assert calls == _WITH_UNIVERSE
    assert notify.startswith("info|") and runlog_txt.startswith("ok|")


def test_success_is_recorded_in_runlog_with_kst_date(tmp_path: Path) -> None:
    """성공하면 런 로그 source=dart_universe 에 오늘(KST 달력일) ok 1행이 더해진다.

    다음 실행의 재시도 판정이 이 행을 읽는다.
    """
    _run(tmp_path, weekday=1)
    assert _universe_runs(tmp_path / "ql") == [(_kst_day(-1), "ok"), (_kst_day(0), "ok")]


@pytest.mark.parametrize("weekday", [2, 3, 4, 5, 6, 7])
def test_dart_universe_not_called_on_other_days(tmp_path: Path, weekday: int) -> None:
    """회귀 가드 — 마지막 성공이 7일 이내면 월요일이 아닌 날엔 부르지 않는다."""
    rc, calls, _, _ = _run(tmp_path, weekday=weekday)
    assert rc == 0
    assert calls == _ALL


@pytest.mark.parametrize("days_ago", [3, 7])
def test_recent_success_is_not_retried_on_tuesday(tmp_path: Path, days_ago: int) -> None:
    """회귀 가드 — 3일 전·딱 7일 전 성공은 '7일 넘음'이 아니다(화요일 안 돎)."""
    rc, calls, notify, _ = _run(tmp_path, weekday=2, univ_age=days_ago)
    assert rc == 0
    assert calls == _ALL
    assert "warn|" not in notify


def test_stale_success_is_retried_on_tuesday(tmp_path: Path) -> None:
    """G1 — 마지막 성공 8일 전 · 화요일이면 돈다.

    옛 코드는 월요일에만 돌아 실패한 주는 2주 공백이었다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=2, univ_age=8)
    assert rc == 0
    assert calls == _WITH_UNIVERSE
    assert notify.startswith("info|daily_ledger 완료") and runlog_txt.startswith("ok|")
    assert _universe_runs(tmp_path / "ql") == [(_kst_day(-8), "ok"), (_kst_day(0), "ok")]


def test_no_success_record_runs_on_any_day(tmp_path: Path) -> None:
    """기록이 없으면(첫 배포 · 런 로그 초기화) 요일과 무관하게 돈다."""
    rc, calls, _, _ = _run(tmp_path, weekday=4, univ_age=None)
    assert rc == 0
    assert calls == _WITH_UNIVERSE
    assert _universe_runs(tmp_path / "ql") == [(_kst_day(0), "ok")]


def test_unreadable_runlog_runs_universe(tmp_path: Path) -> None:
    """런 로그를 못 읽으면 돈다(P1 — 놓치는 쪽보다 DART 1콜). 성공 기록은 못 남겨도 체인은 계속."""
    root = _root(tmp_path)
    _run_db(root).parent.mkdir(parents=True)
    _run_db(root).write_bytes(b"not a sqlite database" * 100)
    env = _env(root, QL_WEEKDAY="3")
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", "20260929"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stderr
    assert (root / "calls.txt").read_text(encoding="utf-8").split() == _WITH_UNIVERSE
    log = "".join(f.read_text(encoding="utf-8") for f in (root / "logs").glob("daily_ledger_*.log"))
    assert "dart_universe 성공 기록 실패" in log


@pytest.mark.parametrize("univ_age", [1, None])
def test_dry_run_skips_dart_universe(tmp_path: Path, univ_age: int | None) -> None:
    """dart_universe 는 dry-run 이 없고 원장(dart.db·corps.txt)을 바로 쓴다.

    월요일이거나 성공 기록이 없어도 dry-run 에선 건너뛴다.
    """
    rc, calls, _, _ = _run(tmp_path, "--dry-run", weekday=1, univ_age=univ_age)
    assert rc == 0
    assert calls == ["daily.kw_daily", "daily.kis_daily", "daily.dart_daily", "gap"]


def test_monday_refresh_runs_even_when_d_already_collected(tmp_path: Path) -> None:
    """평소 월요일 06:00 은 D(금요일)를 토요일에 이미 받아 건너뛰는 날이다 — 그날도 돈다."""
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=1, recent=0)
    assert rc == 0
    assert calls == ["daily_wise", "src/dart_universe.py"]
    assert notify.startswith("info|daily_ledger 건너뜀") and runlog_txt == ""


def test_dart_universe_failure_is_warn_and_chain_rc_unchanged(tmp_path: Path) -> None:
    """실패하면 warn 1줄 — 뒤 단계는 돌고 체인 rc·완료 info·ledger_chain 런 로그는 그대로.

    FAILED 에 넣지 않는다. 성공 기록이 없으니 마지막 성공이 7일을 넘는 동안 다음 06:00 이 다시
    부른다. 옛 코드는 crit + rc 2 였다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=2, univ_age=8, dart_universe=1)
    assert rc == 0
    assert calls == _WITH_UNIVERSE
    lines = notify.splitlines()
    warns = [ln for ln in lines if ln.startswith("warn|")]
    assert warns == ["warn|daily_ledger dart universe 실패(rc=1)"]
    assert lines[-1].startswith("info|daily_ledger 완료") and "crit|" not in notify
    assert runlog_txt == "ok|\n"
    assert _universe_runs(tmp_path / "ql") == [(_kst_day(-8), "ok")]   # 실패는 성공으로 적지 않는다


_DATE = """#!/usr/bin/env bash
# 요일(+%u) 질의만 가로챈다 — TZ=Asia/Seoul 로 물으면 월(1), 그 밖(UTC·-u)은 일(7). 나머지는 진짜 date.
if [ "${!#}" = "+%u" ]; then
  if [ "${TZ:-}" = "Asia/Seoul" ] && [ "$#" -eq 1 ]; then echo 1; else echo 7; fi
  exit 0
fi
PATH="${PATH#*:}" exec date "$@"
"""


def test_weekday_is_judged_in_kst(tmp_path: Path) -> None:
    """크론은 UTC 일 21:00 = KST 월 06:00 — 요일을 KST 로 봐야 돈다(QL_WEEKDAY 주입 없이).

    마지막 성공은 어제로 심어 '7일 초과' 규칙이 아니라 요일 규칙으로만 돌게 한다.
    """
    root = _root(tmp_path)
    _seed_universe_ok(root, 1)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "date").write_text(_DATE, encoding="utf-8")
    (fake / "date").chmod(0o755)
    env = _env(root, TZ="UTC", PATH=f"{fake}:{os.environ['PATH']}")
    env.pop("QL_WEEKDAY", None)
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", "20260929"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stderr
    calls = (root / "calls.txt").read_text(encoding="utf-8").split()
    assert calls[:2] == ["daily_wise", "src/dart_universe.py"]


def test_skip_day_monday_failure_is_warn_without_runlog(tmp_path: Path) -> None:
    """가장 흔한 실패 모양 — 평소 월요일(건너뜀 날)에 dart_universe 만 실패한다.

    warn 1줄 + 건너뜀 info, rc 0 이다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=1, recent=0, dart_universe=1)
    assert rc == 0
    assert calls == ["daily_wise", "src/dart_universe.py"]
    assert notify.splitlines() == ["warn|daily_ledger dart universe 실패(rc=1)",
                                   "info|daily_ledger 건너뜀(D=20260929 이미 수집 완료)"]
    assert runlog_txt == ""
