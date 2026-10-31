"""S07 서버 예약 실행 스크립트(`scripts/weekly_rebalance_cron.sh`)의 prepare·close 배선.

quant-ledger 달력은 고정 답을 내는 대역, backend python 은 받은 인자를 적는 대역이다. 신호 준비
판정(signal-status)만 진짜 명령줄로 돌려 임시 폴더의 모델 빌드를 읽는다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_workbench.application.weekly_rebalance.facade.rebalance import SignalBasis
from tests.test_adapters_weekly_rebalance import model_db

_CRON = Path(__file__).resolve().parents[1] / "scripts" / "weekly_rebalance_cron.sh"

# 오늘 날짜와 상관없이 "휴장 첫날, 신호일은 금요일 10-23"으로 답하는 달력 대역
_CALENDAR = """\
import datetime as dt


class _Calendar:
    def is_trading_day(self, day):
        return day.weekday() < 5

    def supplement_target(self, today):
        return dt.date(2026, 10, 23)


def load():
    return _Calendar()
"""


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def test_scheduled_prepare_reads_the_supplement_from_the_research_model_root(
    tmp_path: Path,
) -> None:
    # 2026-10-24 사고: 주말 보강판은 quant-ledger 연구 루트(data/model)에만 쓰이고 모델 DB
    # (data/model_db)에는 장 마감 판만 있다. 예약 prepare 가 모델 DB 를 보면 보강판을 못 찾는다.
    ledger = tmp_path / "ledger"
    model_db(ledger / "data", basis=SignalBasis.SUPPLEMENT)
    model_db(ledger / "data" / "model_db", basis=SignalBasis.EVENING)
    _executable(ledger / ".venv" / "bin" / "python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    _executable(ledger / "src" / "daily" / "__init__.py", "")
    _executable(ledger / "src" / "daily" / "calendar.py", _CALENDAR)

    calls = tmp_path / "calls.txt"
    backend = tmp_path / "backend"
    _executable(
        backend / ".venv" / "bin" / "python",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$*" in
  *signal-status*)
    "{sys.executable}" "$@"; rc=$?
    echo "signal-status rc=$rc" >> "{calls}"
    exit $rc ;;
esac
exit 0
""",
    )
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "flock", "#!/bin/sh\nexit 0\n")  # 맥에는 flock 이 없다
    conf = tmp_path / "s07.env"
    conf.write_text(
        f"S07_BACKEND={backend}\n"
        f"S07_WORKDIR={tmp_path / 'work'}\n"
        f"S07_MODEL_DB={ledger / 'data' / 'model_db'}\n"
        f"S07_LEDGER_HOME={ledger}\n"
        "S07_REQUIRE_APPROVAL=1\n"
    )

    result = subprocess.run(
        ["bash", str(_CRON), "prepare"],
        env={
            **os.environ,
            "S07_CONF": str(conf),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    research_root = f"--model-root {ledger / 'data'} "
    assert "signal-status rc=0" in lines
    prepare = [
        line
        for line in lines
        if line.startswith("-m strategy_workbench.bootstrap.weekly_rebalance prepare")
    ]
    assert len(prepare) == 1
    assert research_root in prepare[0]
    assert "--signal-date 2026-10-23 --basis supplement" in prepare[0]


# 오늘이 늘 거래일이라고 답하는 달력 대역(장 마감 단계 배선용)
_TRADING_CALENDAR = """\
import datetime as dt


class _Calendar:
    def is_trading_day(self, day):
        return True


def load():
    return _Calendar()
"""


def _close_rig(tmp_path: Path, *, close_rc: int, report_rc: int, hook: bool) -> tuple[Path, Path]:
    """장 마감 단계를 대역으로 돌린다. backend python 은 인자를 적고 정한 종료 코드로 끝난다."""
    ledger = tmp_path / "ledger"
    _executable(ledger / ".venv" / "bin" / "python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    _executable(ledger / "src" / "daily" / "__init__.py", "")
    _executable(ledger / "src" / "daily" / "calendar.py", _TRADING_CALENDAR)
    calls = tmp_path / "calls.txt"
    backend = tmp_path / "backend"
    _executable(
        backend / ".venv" / "bin" / "python",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$*" in
  *" close "*) exit {close_rc} ;;
  *risk-report*) exit {report_rc} ;;
esac
exit 0
""",
    )
    work = tmp_path / "work"
    (work / "plans").mkdir(parents=True)
    (work / "plans" / "weekly-2026-10-30.json").write_text("{}")
    _executable(tmp_path / "bin" / "flock", "#!/bin/sh\nexit 0\n")
    after = tmp_path / "after_risk_report.sh"
    _executable(after, f'#!/bin/sh\necho "hook $*" >> "{calls}"\n')
    conf = tmp_path / "s07.env"
    conf.write_text(
        f"S07_BACKEND={backend}\nS07_WORKDIR={work}\nS07_LEDGER_HOME={ledger}\n"
        + (f"S07_AFTER_RISK_REPORT={after}\n" if hook else "")
    )
    return conf, calls


def _run_close(tmp_path: Path, conf: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_CRON), "close"],
        env={
            **os.environ,
            "S07_CONF": str(conf),
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


def test_close_is_followed_by_the_risk_report_and_the_server_hook(tmp_path: Path) -> None:
    conf, calls = _close_rig(tmp_path, close_rc=0, report_rc=0, hook=True)
    result = _run_close(tmp_path, conf)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    commands = [line.split()[2] for line in lines if line.startswith("-m ")]
    assert commands == ["close", "risk-report"]
    report = next(line for line in lines if "risk-report" in line)
    assert f"--equity-root {tmp_path / 'ledger' / 'data' / 'equity'} --day {_today()}" in report
    tomorrow = (date.fromisoformat(_today()) + timedelta(days=1)).isoformat()
    assert lines[-1] == f"hook {_today()} {tomorrow}"


def test_a_failed_report_or_close_keeps_the_close_result(tmp_path: Path) -> None:
    conf, calls = _close_rig(tmp_path / "a", close_rc=0, report_rc=1, hook=True)
    result = _run_close(tmp_path / "a", conf)
    assert result.returncode == 0 and "위험 보고를 만들지 못함" in result.stdout
    assert not any(line.startswith("hook") for line in calls.read_text().splitlines())
    # close 가 실패해도 보고는 해 보고(그날 기록이 있으면 만든다), 종료 코드는 close 것이다
    conf, calls = _close_rig(tmp_path / "b", close_rc=1, report_rc=0, hook=False)
    result = _run_close(tmp_path / "b", conf)
    assert result.returncode == 1
    assert [line.split()[2] for line in calls.read_text().splitlines()] == ["close", "risk-report"]
