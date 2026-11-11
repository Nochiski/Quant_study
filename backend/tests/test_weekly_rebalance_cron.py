"""S07 서버 예약 실행 스크립트(`scripts/weekly_rebalance_cron.sh`)의 prepare·open·close 배선.

quant-ledger 달력은 고정 답을 내는 대역, backend python 은 받은 인자를 적는 대역이다. 신호 준비
판정(signal-status)과 장 상태(session-status)만 진짜 명령줄로 돌려 임시 폴더의 모델 빌드·장운영
기록을 읽는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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


def _close_rig(
    tmp_path: Path,
    *,
    close_rc: int,
    report_rc: int,
    hook: bool,
    recorder_running: bool = False,
    reads: tuple[tuple[str, str | None, int], ...] = (),
) -> tuple[Path, Path]:
    """장 마감·주문 단계를 대역으로 돌린다. backend python 은 인자를 적고 정한 종료 코드로 끝난다.

    `recorder_running`이면 체결 기록기 잠금(.lock-fills)이 잡혀 있는 것으로 답한다.
    `reads`는 장 상태 읽기(session-status)마다 차례로 쓰는 (그때 시각 HHMM, 그 직전 기록기가
    받은 장운영 줄, 읽기 종료 코드)다. 시각은 스크립트의 `date +%H%M`이 답한다. 다 쓰면 마지막
    시각에 머물고, 비우면 진짜 시각이다.
    """
    ledger = tmp_path / "ledger"
    _executable(ledger / ".venv" / "bin" / "python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    _executable(ledger / "src" / "daily" / "__init__.py", "")
    _executable(ledger / "src" / "daily" / "calendar.py", _TRADING_CALENDAR)
    calls = tmp_path / "calls.txt"
    backend = tmp_path / "backend"
    work = tmp_path / "work"
    session_file = work / "records" / f"session-{_today()}.jsonl"
    queue, clock = tmp_path / "reads.tsv", tmp_path / "clock.txt"
    queue.write_text("".join(f"{at}\t{row or '-'}\t{rc}\n" for at, row, rc in reads))
    _executable(
        backend / ".venv" / "bin" / "python",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$*" in
  *session-status*)
    if [ -s "{queue}" ]; then
      IFS="$(printf '\\t')" read -r at row rc < "{queue}"
      tail -n +2 "{queue}" > "{queue}.next" && mv "{queue}.next" "{queue}"
      echo "$at" > "{clock}"
      if [ "$row" != - ]; then
        mkdir -p "{session_file.parent}"
        printf '%s\\n' "$row" >> "{session_file}"
      fi
      [ "$rc" = 0 ] || {{ echo "[unreadable] session record unreadable — line=1"; exit "$rc"; }}
    fi
    # 장 시작을 기다리는 읽기는 0.2초로 줄인다(테스트 시간). 시각은 위 줄이 정한다
    n=$#; prev=""
    for a in "$@"; do
      [ "$prev" = --wait-seconds ] && [ "$a" != 0 ] && a=0.2
      prev="$a"
      set -- "$@" "$a"
    done
    shift "$n"
    exec "{sys.executable}" "$@" ;;
  *" close "*) exit {close_rc} ;;
  *risk-report*) exit {report_rc} ;;
esac
exit 0
""",
    )
    (work / "plans").mkdir(parents=True)
    (work / "plans" / "weekly-2026-10-30.json").write_text("{}")
    held = "  *lock-fills*) exit 1 ;;\n" if recorder_running else ""
    _executable(tmp_path / "bin" / "flock", f'#!/bin/sh\ncase "$*" in\n{held}esac\nexit 0\n')
    real_date = shutil.which("date")
    _executable(
        tmp_path / "bin" / "date",
        f'#!/bin/sh\ncase "$*" in\n  *%H%M*) [ -f "{clock}" ] && exec cat "{clock}" ;;\nesac\n'
        f'exec "{real_date}" "$@"\n',
    )
    # 장 마감 단계: 정규장 마감을 기다리는 동안 1분이 지나면 기록기가 마감 신호를 받는다
    _executable(
        tmp_path / "bin" / "sleep",
        f'#!/bin/sh\necho "sleep $*" >> "{calls}"\n'
        f"[ \"$1\" = 60 ] && printf '%s\\n' '{_session_row('8')}' >> \"{session_file}\"\nexit 0\n",
    )
    after = tmp_path / "after_risk_report.sh"
    _executable(after, f'#!/bin/sh\necho "hook $*" >> "{calls}"\n')
    conf = tmp_path / "s07.env"
    conf.write_text(
        f"S07_BACKEND={backend}\nS07_WORKDIR={work}\nS07_LEDGER_HOME={ledger}\n"
        + (f"S07_AFTER_RISK_REPORT={after}\n" if hook else "")
    )
    return conf, calls


def _run_close(
    tmp_path: Path, conf: Path, phase: str = "close"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_CRON), phase],
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


def _session_row(code: str, remaining: str | None = None, ago_minutes: int = 1) -> str:
    """체결 기록기가 남기는 장운영(0s) 한 줄. 지금보다 `ago_minutes` 앞에 받은 것으로 적는다."""
    at = datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(minutes=ago_minutes)
    values = {"215": code} if remaining is None else {"215": code, "214": remaining}
    entry = {"type": "0s", "name": "장시작시간", "item": "", "values": values}
    return json.dumps({"at": at.isoformat(timespec="seconds"), "kind": "0s", "msg": entry})


def _write_session(tmp_path: Path, *rows: str) -> None:
    records = tmp_path / "work" / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"session-{_today()}.jsonl").write_text("".join(row + "\n" for row in rows))


def _commands(calls: Path) -> list[str]:
    return [line.split()[2] for line in calls.read_text().splitlines() if line.startswith("-m ")]


def test_close_is_followed_by_the_risk_report_and_the_server_hook(tmp_path: Path) -> None:
    conf, calls = _close_rig(tmp_path, close_rc=0, report_rc=0, hook=True)
    result = _run_close(tmp_path, conf)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert _commands(calls) == ["session-status", "close", "risk-report"]
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
    assert _commands(calls) == ["session-status", "close", "risk-report"]


# 오늘이 늘 거래일이고 직전 거래일이 10-30 이라고 답하는 달력 대역(주문 단계 배선용)
_ORDER_DAY_CALENDAR = """\
import datetime as dt


class _Calendar:
    def is_trading_day(self, day):
        return True

    def prev_trading_day(self, day):
        return dt.date(2026, 10, 30)


def load():
    return _Calendar()
"""


def _order_rig(
    tmp_path: Path,
    *,
    reads: tuple[tuple[str, str | None, int], ...],
    recorder_running: bool = True,
) -> tuple[Path, Path]:
    conf, calls = _close_rig(
        tmp_path,
        close_rc=0,
        report_rc=0,
        hook=False,
        recorder_running=recorder_running,
        reads=reads,
    )
    _executable(tmp_path / "ledger" / "src" / "daily" / "calendar.py", _ORDER_DAY_CALENDAR)
    return conf, calls


def _sleeps(calls: Path) -> list[str]:
    return [line for line in calls.read_text().splitlines() if line.startswith("sleep")]


def test_open_waits_for_the_opening_signal_and_sends_at_once(tmp_path: Path) -> None:
    # 2027-01-04(월) 새해 첫 거래일은 10:00 개장인 주문일이다. open 은 장 시작 전에 떠서
    # 기다리다 장시작 신호를 받는 순간 낸다 — 동시호가에 내지 않는다
    # (2026-11-11 사용자 결정 "장 시작과 동시에").
    conf, calls = _order_rig(
        tmp_path, reads=(("0958", None, 0), ("1000", _session_row("3", ago_minutes=0), 0))
    )
    _write_session(tmp_path, _session_row("0", "005500"))
    result = _run_close(tmp_path, conf, "open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "장 시작을 기다린다(장운영 before_open)" in result.stdout
    assert "장운영 before_open → open" in result.stdout
    assert _commands(calls) == ["session-status", "session-status", "execute"]
    lines = calls.read_text().splitlines()
    waited = [line for line in lines if "session-status" in line][1]
    assert "--wait-seconds 60" in waited  # 한 프로세스 안에서 다시 읽으며 기다린다
    execute = next(line for line in lines if " execute " in line)
    assert "--plan weekly-2026-10-30 --phase open --send" in execute
    assert _sleeps(calls) == []


@pytest.mark.parametrize(
    ("recorder_running", "reads", "reason", "sleeps"),
    [
        # 기록기가 돌지 않으면 신호를 쓰기 전처럼 09:05 에 낸다 — 그 주 주문을 잃지 않는다
        (False, (("0857", None, 0), ("0905", None, 0)), "체결 기록기가 돌지 않아", []),
        # 기록기는 도는데 신호가 한 줄도 없으면 신호 고장으로 보고 10:15 에 낸다
        (True, (("0957", None, 0), ("1015", None, 0)), "신호 고장으로 봄", []),
        # 장 상태를 읽지 못하면 30초마다 다시 읽고(로그를 채우지 않게) 09:05 에 낸다
        (True, (("0857", None, 1), ("0905", None, 1)), "장 상태를 읽지 못함(rc=1", ["sleep 30"]),
    ],
)
def test_open_falls_back_to_a_fixed_time_without_session_signals(
    tmp_path: Path,
    recorder_running: bool,
    reads: tuple[tuple[str, str | None, int], ...],
    reason: str,
    sleeps: list[str],
) -> None:
    conf, calls = _order_rig(tmp_path, reads=reads, recorder_running=recorder_running)
    result = _run_close(tmp_path, conf, "open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "장 시작을 기다린다" in result.stdout
    assert reason in result.stdout
    assert _commands(calls) == ["session-status", "session-status", "execute"]
    assert _sleeps(calls) == sleeps


def test_open_gives_up_the_week_when_the_market_is_not_open_by_the_last_round(
    tmp_path: Path,
) -> None:
    # 개장이 11:30 뒤로 밀리면(장애 공지 등) 그 주는 내지 않는다 — 마지막 chase 회차 뒤라
    # 쫓을 수 없다
    conf, calls = _order_rig(tmp_path, reads=(("1129", None, 0), ("1130", None, 0)))
    _write_session(tmp_path, _session_row("0", "030000"))
    result = _run_close(tmp_path, conf, "open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "11:30 까지 장이 열리지 않음(장운영 before_open)" in result.stdout
    assert _commands(calls) == ["session-status", "session-status"]


@pytest.mark.parametrize(
    ("rows", "recorder_running", "read", "sent", "reason"),
    [
        (
            (_session_row("0", "005500"),),
            True,
            ("0915", None, 0),
            False,
            "장 시작 전(장운영 before_open)",
        ),
        ((_session_row("3", ago_minutes=15),), True, ("0915", None, 0), True, None),
        ((_session_row("8"),), True, ("1530", None, 0), False, "정규장이 끝남"),
        ((), True, ("0955", None, 0), False, "장 시작 전(장운영 unknown)"),
        ((), True, ("1015", None, 0), True, "신호 고장으로 봄"),
        ((), False, ("0915", None, 0), True, "체결 기록기가 돌지 않아"),
        ((), True, ("0915", None, 1), True, "장 상태를 읽지 못함(rc=1"),
    ],
    ids=[
        "before-open-skips",
        "open-sends",
        "closed-skips",
        "silent-recorder-waits",
        "silent-recorder-gives-up-at-10-15",
        "no-recorder-keeps-schedule",
        "unreadable-keeps-schedule",
    ],
)
def test_chase_round_sends_only_once_the_market_may_be_open(
    tmp_path: Path,
    rows: tuple[str, ...],
    recorder_running: bool,
    read: tuple[str, str | None, int],
    sent: bool,
    reason: str | None,
) -> None:
    # chase 는 기다리지 않는다 — 판정이 아직이면 그 회차를 건너뛰고 다음 회차가 다시 본다
    conf, calls = _order_rig(tmp_path, reads=(read,), recorder_running=recorder_running)
    if rows:
        _write_session(tmp_path, *rows)
    result = _run_close(tmp_path, conf, "chase")
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ["session-status", "execute"] if sent else ["session-status"]
    assert _commands(calls) == expected
    if reason is not None:
        assert reason in result.stdout
    assert "장 시작을 기다린다" not in result.stdout


def test_close_waits_for_the_regular_close_on_a_late_closing_day(tmp_path: Path) -> None:
    # 수능일(마감 16:30): 15:40 close 는 장중이다. 정규장 마감 신호를 받을 때까지 기다렸다 잰다
    conf, calls = _close_rig(tmp_path, close_rc=0, report_rc=0, hook=False, recorder_running=True)
    _write_session(tmp_path, _session_row("3", ago_minutes=60))
    result = _run_close(tmp_path, conf)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert [line for line in lines if line.startswith("sleep")] == ["sleep 60"]
    assert _commands(calls) == ["session-status", "session-status", "close", "risk-report"]


def test_close_goes_ahead_when_the_recorder_ended_without_a_close_signal(tmp_path: Path) -> None:
    # 마감 신호를 놓친 채 기록기가 끝났으면(잠금이 비었다) 더 기다리지 않고 잰다
    conf, calls = _close_rig(tmp_path, close_rc=0, report_rc=0, hook=False)
    _write_session(tmp_path, _session_row("3", ago_minutes=60))
    result = _run_close(tmp_path, conf)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "정규장 마감 신호 없이 체결 기록기가 끝남" in result.stdout
    assert _commands(calls) == ["session-status", "close", "risk-report"]


def test_close_measures_at_the_fixed_time_when_the_state_is_unreadable(tmp_path: Path) -> None:
    # 장운영 기록을 읽지 못하면 기다리지 않고 지금처럼 15:40 에 잰다
    conf, calls = _close_rig(
        tmp_path, close_rc=0, report_rc=0, hook=False, reads=(("1540", None, 1),)
    )
    result = _run_close(tmp_path, conf)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "장 상태를 읽지 못함(rc=1 [unreadable]" in result.stdout
    assert _commands(calls) == ["session-status", "close", "risk-report"]
