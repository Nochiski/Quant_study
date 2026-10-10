"""scripts/daily_ledger.sh — 소스별 단계가 서로 막지 않는지(플랜 2026-09-30 T-K3).

임시 루트(`QL_LEDGER_ROOT`)에 대역 `.venv/bin/python`·`scripts/*.sh` 를 두고 진짜 스크립트를 돌린다.
네트워크·원장은 쓰지 않는다. 대역 python 은 `-m <모듈>` 을 calls.txt 에 적고 `RC_<모듈>` 환경변수로 rc 를 낸다.
스크립트 경로 호출(`src/<파일>.py`)은 경로를 적고 `RC_<파일>` 로 rc 를 낸다.
런로그 "D 이미 수집" 검사의 rc 는 `RC_recent`(기본 1 = 미수집)다.
요일은 `QL_WEEKDAY` 로 주입한다(기본 3 = 수요일 — 테스트를 돌리는 요일과 무관하게 고정).
휴장 달력 직접 갱신(`-m daily.calendar_refresh`)은 calls.txt 맨 앞에 적히고, 받은 인자는 cal_args.txt 에 남는다.
D 산출(`-c` 의 `prev_trading_day`)은 진짜 python·진짜 `daily.calendar` 로 넘긴다(`--date` 없는 실행만 닿는다).
DART 번호표 갱신(A-01)의 런 로그 `-c`(source=dart_universe 판정·성공 기록)만은 진짜 python·진짜
`daily.runlog` 로 넘겨 임시 루트의 `data/raw/daily_run.db` 를 실제로 읽고 쓴다. 마지막 성공은
`univ_age`(KST 달력 며칠 전, 기본 1 — None 이면 기록 없음)로 미리 심는다.
저녁 키움 보강 판정(`-m daily.kw_daily --cover`, T-13)은 calls.txt 에 `kw_cover` 로 적히고 rc 는 `RC_kw_cover`,
받은 인자는 kw_cover_args.txt 에 남는다. `REAL_KW` 가 있으면 `-m daily.kw_daily` 는 진짜 python·진짜 kw_daily 로
가고 `api` 만 `FAKE_API` 디렉터리의 가짜로 바뀐다(GH1-d 리허설 — 실제 키움 콜 없음).
notify 대역은 본문(셋째 인자)을 notify_body.txt 에 따로 남긴다.
v3 휴장 사본 동기화 대역(`scripts/sync_calendar.sh`)은 부를 때마다 sync.txt 에 한 줄을 적는다. 스위치 판정
(`scripts/postclose_conf.sh`)은 진짜를 복사한다. 휴장 파일 내보내기(`-m daily.calendar_export`, T-48)는 calls.txt 에
적히고 받은 인자는 export_args.txt 에 남으며 rc 는 `RC_daily_calendar_export` 다. `REAL_EXPORT` 가 있으면 진짜
python·진짜 모듈로 가서 `QL_V3_HOLIDAY_FILE`(테스트가 늘 임시 경로로 준다)에 실제로 쓴다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from daily import ledger_health as lh
from daily import runlog

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "daily_ledger.sh"

_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *dart_universe*|*prev_trading_day*) PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@" ;;
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
name="$2"
case " ${*:3} " in *" --cover "*) name=kw_cover; echo "${*:3}" >> "$QL_HOME/kw_cover_args.txt" ;; esac
echo "$name" >> "$QL_HOME/calls.txt"
if [ "$2" = "daily.kw_daily" ] && [ -n "${REAL_KW:-}" ]; then
  PYTHONPATH="$FAKE_API:$REAL_SRC" exec "$REAL_PY" "$@"
fi
if [ "$2" = "daily.calendar_refresh" ]; then
  echo "${*:3}" >> "$QL_HOME/cal_args.txt"
  [ -z "${CAL_NO_SUMMARY:-}" ] && echo "휴장 달력 20260929 갱신: rc=${RC_daily_calendar_refresh:-0} (대역)"
fi
if [ "$2" = "daily.calendar_export" ]; then
  echo "${*:3}" >> "$QL_HOME/export_args.txt"
  [ -n "${REAL_EXPORT:-}" ] && PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@"
  echo "휴장 달력 내보내기 대역: rc=${RC_daily_calendar_export:-0}"
fi
[ "$name" = kw_cover ] && echo "[kw_daily] cover 판정 D=대역 rc=${RC_kw_cover:-0}"
var="RC_${name//./_}"
exit "${!var:-0}"
"""


_CAL = "daily.calendar_refresh"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "ql"
    (root / "scripts").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "daily_ledger.sh")
    shutil.copy(DB_ROOT / "scripts" / "raw_lock.sh", root / "scripts" / "raw_lock.sh")
    shutil.copy(DB_ROOT / "scripts" / "postclose_conf.sh", root / "scripts" / "postclose_conf.sh")
    stubs = {
        ".venv/bin/python": _PY,
        "scripts/sync_calendar.sh": "#!/usr/bin/env bash\necho sync >> sync.txt\nexit 0\n",
        "scripts/daily_wise.sh": "#!/usr/bin/env bash\necho daily_wise >> calls.txt\n",
        "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2" >> notify.txt\necho "$3" >> notify_body.txt\n',
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
    assert calls == [_CAL, "daily_wise", "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily",
                     "gap"]
    assert notify.startswith("info|") and runlog.startswith("ok|")


def test_kis_failure_does_not_stop_dart(tmp_path: Path) -> None:
    """09-25~28·09-30 재현 — 신용잔고 판정 실패(rc 2) 뒤에도 DART·회사정보 공백 메우기가 돈다."""
    rc, calls, notify, runlog = _run(tmp_path, daily_kis_daily=2)
    assert rc == 2
    assert calls == [_CAL, "daily_wise", "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily",
                     "gap"]
    assert notify.startswith("crit|daily_ledger 실패: kis credit(rc=2)")
    assert runlog == "failed|kis credit(rc=2)\n"


def test_dart_failure_skips_only_company_gap(tmp_path: Path) -> None:
    rc, calls, notify, _ = _run(tmp_path, daily_dart_daily=2)
    assert rc == 2
    assert calls == [_CAL, "daily_wise", "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily"]
    assert "dart(rc=2)" in notify


def test_every_failed_step_is_reported(tmp_path: Path) -> None:
    rc, calls, notify, runlog = _run(tmp_path, daily_kw_daily=1, daily_kis_daily=2)
    assert rc == 2
    assert calls == [_CAL, "daily_wise", "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily",
                     "gap"]
    assert "kiwoom fetch(rc=1), kis credit(rc=2)" in notify
    assert runlog == "failed|kiwoom fetch(rc=1), kis credit(rc=2)\n"


# ── A-01 DART 번호표 갱신 — 월요일 + 마지막 성공 7일 초과 재시도(N-27 ⑤ · 배포 묶음 5-2) ─────────
_ALL = [_CAL, "daily_wise", "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily", "gap"]
_WITH_UNIVERSE = [_CAL, "daily_wise", "src/dart_universe.py",
                  "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily", "gap"]


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
    assert calls == [_CAL, "daily.kw_daily", "daily.kis_daily", "kw_cover", "daily.dart_daily", "gap"]


def test_monday_refresh_runs_even_when_d_already_collected(tmp_path: Path) -> None:
    """평소 월요일 06:00 은 D(금요일)를 토요일에 이미 받아 건너뛰는 날이다 — 그날도 돈다."""
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=1, recent=0)
    assert rc == 0
    assert calls == [_CAL, "daily_wise", "src/dart_universe.py"]
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
    assert calls[:3] == [_CAL, "daily_wise", "src/dart_universe.py"]


def test_skip_day_monday_failure_is_warn_without_runlog(tmp_path: Path) -> None:
    """가장 흔한 실패 모양 — 평소 월요일(건너뜀 날)에 dart_universe 만 실패한다.

    warn 1줄 + 건너뜀 info, rc 0 이다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, weekday=1, recent=0, dart_universe=1)
    assert rc == 0
    assert calls == [_CAL, "daily_wise", "src/dart_universe.py"]
    assert notify.splitlines() == ["warn|daily_ledger dart universe 실패(rc=1)",
                                   "info|daily_ledger 건너뜀(D=20260929 이미 수집 완료)"]
    assert runlog_txt == ""


# ── 휴장 달력 직접 갱신(K1-9 ①③④⑥ · N-31 ②) — 체인 rc·런 로그와 분리된 알림 ─────────────────
def _cal_args(tmp_path: Path) -> str:
    f = tmp_path / "ql" / "cal_args.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def test_calendar_refresh_runs_first_without_check_flag(tmp_path: Path) -> None:
    rc, calls, notify, _ = _run(tmp_path)
    assert rc == 0 and calls[0] == _CAL
    assert _cal_args(tmp_path).strip() == ""
    assert "휴장 달력" not in notify


def test_dry_run_refreshes_calendar_in_check_mode(tmp_path: Path) -> None:
    """dry-run 은 원장·달력 파일을 쓰지 않는다 — 달력 갱신도 `--check`(읽기 전용)로 부른다."""
    rc, calls, _, _ = _run(tmp_path, "--dry-run")
    assert rc == 0 and calls[0] == _CAL
    assert _cal_args(tmp_path).split() == ["--check"]


def test_calendar_crit_is_notified_but_the_chain_and_runlog_are_unchanged(tmp_path: Path) -> None:
    """달력 갱신 crit(rc 2)는 crit 알림 한 줄 — 직전 판정 달력으로 수집은 계속한다.

    체인 rc·FAILED·ledger_chain 런 로그에 넣으면 그날 D 가 '실패'로 남아 다음 06:00 이 전 소스를 다시 받는다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, daily_calendar_refresh=2)
    assert rc == 0
    assert calls == _ALL
    lines = notify.splitlines()
    assert lines[0].startswith("crit|휴장 달력 갱신 crit(rc=2)")
    assert lines[-1].startswith("info|daily_ledger 완료")
    assert runlog_txt == "ok|\n"


def test_calendar_warn_is_notified_as_warn(tmp_path: Path) -> None:
    rc, _, notify, _ = _run(tmp_path, daily_calendar_refresh=1)
    assert rc == 0
    assert notify.splitlines()[0].startswith("warn|휴장 달력 경고")


def test_calendar_rc1_without_summary_is_crit(tmp_path: Path) -> None:
    """파이썬이 요약 없이 rc 1 로 죽으면(import 실패·traceback) warn 이 아니라 crit 이다."""
    root = _root(tmp_path)
    _seed_universe_ok(root, 1)
    env = _env(root, QL_WEEKDAY="3", RC_daily_calendar_refresh="1", CAL_NO_SUMMARY="1")
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", "20260929"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stderr
    notify = (root / "notify.txt").read_text(encoding="utf-8")
    assert notify.splitlines()[0].startswith("crit|휴장 달력 갱신 crit(rc=1)")


def test_unreadable_calendar_stops_the_chain(tmp_path: Path) -> None:
    """K1-9 ⑦ — 달력을 못 읽으면 D 를 구하지 않고 중단한다(crit·rc 2, 수집 단계 0).

    옛 코드는 `calendar.load()` 가 weekend_only 로 폴백해 D 를 '영업일 가정'으로 구하고 체인을 끝까지 돌렸다.
    판정 디렉터리(`data/calendar/`)가 비어 있는 임시 루트에서 `--date` 없이 돌린다.
    """
    root = _root(tmp_path)
    _seed_universe_ok(root, 1)
    env = _env(root, QL_WEEKDAY="3")
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh")],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 2, p.stdout + p.stderr
    calls = (root / "calls.txt").read_text(encoding="utf-8").split()
    assert calls == [_CAL]                                   # 달력 갱신 뒤 D 산출에서 멈춘다
    notify = (root / "notify.txt").read_text(encoding="utf-8")
    assert "crit|daily_ledger 중단 — 대상 거래일 산출 실패" in notify


# ── 휴장 파일 내보내기(T-48 · QL-Q2) — 스위치 config/calendar_export.env, 꺼짐이면 지금 동작 그대로 ─────────
_EXPORT = "daily.calendar_export"
_ON = "CALENDAR_EXPORT_V3=1\n"
_SHIPPED_CONF = DB_ROOT / "config" / "calendar_export.env"
_V3_BEFORE = '{"year": 2026, "holidays": ["20260101"], "note": "v3 가 쓴 옛 판(대역)"}'
_KST_TS = r"\d{2}-\d{2} \d{2}:\d{2}:\d{2} KST"


class Exported(NamedTuple):
    p: subprocess.CompletedProcess[str]
    root: Path
    target: Path                    # QL_V3_HOLIDAY_FILE — v3 data/.kis_holidays.json 자리(임시)

    def read(self, name: str) -> str:
        f = self.root / name
        return f.read_text(encoding="utf-8") if f.exists() else ""

    @property
    def calls(self) -> list[str]:
        return self.read("calls.txt").split()

    @property
    def log(self) -> str:
        return "".join(f.read_text(encoding="utf-8") for f in (self.root / "logs").glob("daily_ledger_*.log"))


def _closed(year: int, weekdays: tuple[str, ...] = ()) -> list[str]:
    """그해 휴장 = 토·일 전부 + 주어진 평일 — 판정 연도 파일의 `holidays` 모양."""
    days = (dt.date(year, 1, 1) + dt.timedelta(days=i) for i in range(366))
    return [d.strftime("%Y%m%d") for d in days
            if d.year == year and (d.weekday() >= 5 or d.strftime("%Y%m%d") in weekdays)]


def _run_export(tmp_path: Path, *args: str, conf: str | None, real: bool = False,
                years: tuple[int, ...] = (), date: bool = True, home: Path | None = None,
                **rcs: int) -> Exported:
    """`conf` 를 config/calendar_export.env 로 두고(None 이면 파일 없음) 체인을 돌린다. 대상은 늘 임시 경로다.

    `home` 을 주면 HOME 을 그 임시 폴더로 바꾸고 QL_V3_HOLIDAY_FILE 을 넣지 않는다 — 운영 기본 대상 경로를 본다.
    """
    root = _root(tmp_path)
    _seed_universe_ok(root, 1)
    if conf is not None:
        (root / "config").mkdir()
        (root / "config" / "calendar_export.env").write_text(conf, encoding="utf-8")
    for y in years:
        cal = root / "data" / "calendar"
        cal.mkdir(parents=True, exist_ok=True)
        (cal / f"kis_holidays_{y}.json").write_text(
            json.dumps({"year": y, "holidays": _closed(y, (f"{y}0101",))}), encoding="utf-8")
    target = tmp_path / "v3" / "data" / ".kis_holidays.json"
    target.parent.mkdir(parents=True)
    target.write_text(_V3_BEFORE, encoding="utf-8")
    env = _env(root, QL_WEEKDAY="3", QL_V3_HOLIDAY_FILE=str(target),
               **{f"RC_{k}": str(v) for k, v in rcs.items()})
    if home is not None:
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
        env.pop("QL_V3_HOLIDAY_FILE")
    if real:
        env["REAL_EXPORT"] = "1"
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"),
                        *(["--date", "20260929"] if date else []), *args],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    return Exported(p, root, target)


@pytest.mark.parametrize("conf", [None, "shipped", "CALENDAR_EXPORT_V3=yes\n", "CALENDAR_EXPORT_V3=\n",
                                  "CALENDAR_EXPORT_V3=0\n"])
def test_export_switch_off_is_todays_chain(tmp_path: Path, conf: str | None) -> None:
    """꺼짐(파일 없음·저장소 값·다른 값·빈 값) — 내보내기를 부르지 않고 v3 사본 동기화를 부른다. 로그 줄도 늘지 않는다."""
    text = _SHIPPED_CONF.read_text(encoding="utf-8") if conf == "shipped" else conf
    r = _run_export(tmp_path, conf=text)
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    assert r.calls == _ALL
    assert r.read("sync.txt") == "sync\n"
    assert r.read("export_args.txt") == ""
    assert "내보내기" not in r.log and "동기화 건너뜀" not in r.log
    assert r.target.read_text(encoding="utf-8") == _V3_BEFORE
    assert "crit|" not in r.read("notify.txt")


def test_export_switch_off_log_is_the_same_with_or_without_the_file(tmp_path: Path) -> None:
    """꺼짐이면 설정 파일 유무와 무관하게 체인 로그가 시각만 빼고 같다(스위치가 출력에 흔적을 남기지 않는다)."""
    logs = [re.sub(_KST_TS, "T", _run_export(tmp_path / str(i), conf=c).log)
            for i, c in enumerate((None, _SHIPPED_CONF.read_text(encoding="utf-8"), "CALENDAR_EXPORT_V3=yes\n"))]
    assert logs[0] and logs[0] == logs[1] == logs[2]


def test_export_switch_on_runs_after_calendar_refresh_and_skips_sync(tmp_path: Path) -> None:
    """켜짐 — v3 사본 동기화는 건너뛰고(자기 사본 대조) 휴장 달력 갱신 바로 뒤·D 산출 앞에 대상 경로로 내보낸다."""
    r = _run_export(tmp_path, conf=_ON)
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    assert r.calls == [_CAL, _EXPORT, *_ALL[1:]]
    assert r.read("export_args.txt").split() == ["--target", str(r.target)]
    assert r.read("sync.txt") == ""
    log = r.log
    assert "v3 휴장 사본 동기화 건너뜀 — CALENDAR_EXPORT_V3=1" in log
    assert (log.index("──── 휴장 달력 갱신 종료") < log.index("──── 휴장 파일 내보내기 시작")
            < log.index("──── 휴장 파일 내보내기 종료 rc=0") < log.index("  대상 거래일 D="))
    notify = r.read("notify.txt")
    assert "crit|" not in notify and notify.splitlines()[-1].startswith("info|daily_ledger 완료")
    assert r.read("runlog.txt") == "ok|\n"


def test_export_switch_on_writes_the_v3_file_with_the_real_module(tmp_path: Path) -> None:
    """진짜 `daily.calendar_export` — 판정 달력(QL_HOME/data/calendar)의 올해 판을 v3 형식으로 대상에 쓴다."""
    this_year = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9)).year
    r = _run_export(tmp_path, conf=_ON, real=True, years=(this_year, this_year + 1))
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    out = json.loads(r.target.read_text(encoding="utf-8"))
    assert set(out) == {"year", "fetched_at", "last_reviewed_at", "holidays", "review_history"}
    assert out["year"] in (this_year, this_year + 1)                 # 연말 자정 경계에서 돌아도 깨지지 않게
    assert out["holidays"] == _closed(out["year"], (f"{out['year']}0101",))
    assert f"rc=0 year={out['year']}" in r.log and f"target={r.target}" in r.log
    assert "crit|" not in r.read("notify.txt")


def test_export_runs_even_when_calendar_refresh_is_crit(tmp_path: Path) -> None:
    """달력 갱신 rc 와 무관하게 부른다 — 판정 연도 파일을 못 읽으면 내보내기 자신이 rc 2·대상 무변경으로 멈춘다."""
    r = _run_export(tmp_path, conf=_ON, daily_calendar_refresh=2)
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    assert r.calls[:2] == [_CAL, _EXPORT]
    assert r.read("notify.txt").splitlines()[0].startswith("crit|휴장 달력 갱신 crit(rc=2)")


def test_export_failure_is_one_crit_and_the_chain_is_unchanged(tmp_path: Path) -> None:
    """내보내기 rc 2 — crit 한 줄('휴장 파일 내보내기 실패', 본문은 출력 마지막 줄 + 로그 경로). 수집은 계속하고
    체인 rc·FAILED·ledger_chain 런 로그·완료 info 는 그대로다(점수 경로 밖 — 창 판정 제외 목록)."""
    r = _run_export(tmp_path, conf=_ON, daily_calendar_export=2)
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    assert r.calls == [_CAL, _EXPORT, *_ALL[1:]]
    lines = r.read("notify.txt").splitlines()
    assert [ln for ln in lines if ln.startswith("crit|")] == ["crit|휴장 파일 내보내기 실패(rc=2)"]
    assert lines[-1].startswith("info|daily_ledger 완료")
    body = r.read("notify_body.txt").splitlines()[0]
    assert body.startswith("휴장 달력 내보내기 대역: rc=2 | 로그 logs/daily_ledger_")
    assert r.read("runlog.txt") == "ok|\n"
    assert "──── 휴장 파일 내보내기 종료 rc=2" in r.log


def test_export_failure_with_the_real_module_leaves_the_target_and_d_stops_the_chain(tmp_path: Path) -> None:
    """판정 달력이 없는 임시 루트(`--date` 없음) — 진짜 내보내기는 rc 2·대상 무변경(crit), 이어 D 산출이 체인을 멈춘다."""
    r = _run_export(tmp_path, conf=_ON, real=True, date=False)
    assert r.p.returncode == 2, r.p.stdout + r.p.stderr
    assert r.calls == [_CAL, _EXPORT]
    assert r.target.read_text(encoding="utf-8") == _V3_BEFORE
    crits = [ln for ln in r.read("notify.txt").splitlines() if ln.startswith("crit|")]
    assert crits == ["crit|휴장 파일 내보내기 실패(rc=2)", "crit|daily_ledger 중단 — 대상 거래일 산출 실패"]
    assert "calendar_unavailable" in r.read("notify_body.txt").splitlines()[0]


def test_export_dry_run_only_prints_the_plan(tmp_path: Path) -> None:
    """dry-run — 내보내기를 부르지 않고(대상 무변경) 계획 한 줄만. v3 사본 동기화도 건너뛴다."""
    this_year = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9)).year
    r = _run_export(tmp_path, "--dry-run", conf=_ON, real=True, years=(this_year, this_year + 1))
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    assert _EXPORT not in r.calls and r.read("export_args.txt") == ""
    assert r.target.read_text(encoding="utf-8") == _V3_BEFORE
    assert r.read("sync.txt") == ""
    plan = [ln for ln in r.log.splitlines() if "휴장 파일 내보내기" in ln]
    assert len(plan) == 1 and "dry-run" in plan[0] and f"--target {r.target}" in plan[0]


@pytest.mark.parametrize("dry", [True, False])
def test_export_default_target_is_the_v3_holiday_file(tmp_path: Path, dry: bool) -> None:
    """운영 기본 대상(QL_V3_HOLIDAY_FILE 없음) = $HOME/kael-system-v3/data/.kis_holidays.json — sync_calendar.sh 의
    원본과 같은 파일. HOME 은 임시 폴더이고 내보내기는 대역이라 아무 파일도 쓰지 않는다. dry-run 은 계획 줄, 실제
    실행은 내보내기가 받은 `--target` 인자를 본다."""
    home = tmp_path / "home"
    r = _run_export(tmp_path, *(["--dry-run"] if dry else []), conf=_ON, home=home)
    assert r.p.returncode == 0, r.p.stdout + r.p.stderr
    default = f"{home}/kael-system-v3/data/.kis_holidays.json"
    if dry:
        plan = [ln for ln in r.log.splitlines() if "휴장 파일 내보내기 계획" in ln]
        assert len(plan) == 1 and plan[0].endswith(f"--target {default}"), plan
        assert r.read("export_args.txt") == ""
    else:
        assert r.read("export_args.txt").split() == ["--target", default]
    assert not (home / "kael-system-v3").exists()


def _switch_on(tmp_path: Path, text: str | None) -> bool:
    """`scripts/postclose_conf.sh` `calendar_export_v3_on` 판정 — rc 0 켜짐 · 1 꺼짐."""
    if text is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "calendar_export.env").write_bytes(text.encode("utf-8"))
    p = subprocess.run(["bash", "-c", 'set -u; cd "$1" && . "$2" && calendar_export_v3_on', "_", str(tmp_path),
                        str(DB_ROOT / "scripts" / "postclose_conf.sh")],
                       capture_output=True, text=True, timeout=30, check=False)
    assert p.returncode in (0, 1), p.stderr
    return p.returncode == 0


@pytest.mark.parametrize(("text", "on"), [
    (None, False), ("", False), ("CALENDAR_EXPORT_V3=1\n", True), ("CALENDAR_EXPORT_V3='1'\n", True),
    ('CALENDAR_EXPORT_V3="1"\n', True), ("CALENDAR_EXPORT_V3=0\n", False), ("CALENDAR_EXPORT_V3=yes\n", False),
    ("CALENDAR_EXPORT_V3=\n", False), ("# CALENDAR_EXPORT_V3=1\n", False),
    ("CALENDAR_EXPORT_V3=1\nCALENDAR_EXPORT_V3=0\n", False), ("CALENDAR_EXPORT_V3=0\nCALENDAR_EXPORT_V3=1\n", True),
    ("CALENDAR_EXPORT_V3X=1\n", False), ("SILENT_LOSS_BLOCK=1\n", False)])
def test_calendar_export_switch_requires_exactly_one(tmp_path: Path, text: str | None, on: bool) -> None:
    """켜는 쪽만 정확한 값(P1) — 판정은 daily_ledger.sh·daily_evening.sh 가 같이 쓰는 한 곳이다."""
    assert _switch_on(tmp_path, text) is on


def test_shipped_calendar_export_conf_is_off(tmp_path: Path) -> None:
    """저장소 값은 꺼짐 — 머지·배포만으로는 v3 휴장 파일을 쓰지 않는다(컷오버 날 V3-D 와 같은 배포로 켠다, T-48)."""
    text = _SHIPPED_CONF.read_text(encoding="utf-8")
    assert "CALENDAR_EXPORT_V3=0" in text.splitlines()
    assert _switch_on(tmp_path, text) is False


# ── T-13(H1-5) 저녁 키움 보강 — KIS 뒤·DART 앞, 소스 단계 규약(실패 = FAILED → crit · rc 2) ─────────────
def _read(tmp_path: Path, name: str) -> str:
    f = tmp_path / "ql" / name
    return f.read_text(encoding="utf-8") if f.exists() else ""


def test_evening_cover_runs_after_kis_and_before_dart(tmp_path: Path) -> None:
    """KIS 는 07:00(v3 토큰 재발급) 전에 끝나야 한다(실측 06:13→06:41, 여유 19분) — 보강(최대 ≈14분)은 그 뒤.

    판정 대상은 저녁 체인이 받는 두 TR 이다.
    """
    rc, calls, _, _ = _run(tmp_path)
    assert rc == 0
    assert calls.index("daily.kis_daily") < calls.index("kw_cover") < calls.index("daily.dart_daily")
    assert _read(tmp_path, "kw_cover_args.txt").split() == ["--cover", "--date", "20260929",
                                                            "--tr", "ka10060,ka10014"]


def test_evening_cover_still_short_is_crit_and_fails_the_chain(tmp_path: Path) -> None:
    """보강 뒤에도 미달(rc 2)은 다른 소스 단계와 같다 — DART 는 계속 돌고, 끝에 crit · rc 2 · 런 로그 failed.

    실패로 남은 D 는 같은 D 를 보는 다음 06:00(주말·연휴)이 처음부터 다시 판정한다.
    """
    rc, calls, notify, runlog_txt = _run(tmp_path, kw_cover=2)
    assert rc == 2
    assert calls == _ALL
    assert notify.startswith("crit|daily_ledger 실패: kiwoom evening cover(rc=2)")
    assert runlog_txt == "failed|kiwoom evening cover(rc=2)\n"


def test_evening_cover_judgement_leads_the_summary(tmp_path: Path) -> None:
    """판정 줄은 요약 맨 앞이다 — 뒤 단계(KIS·DART) 출력이 tail 창에서 밀어내지 않게."""
    rc, _, _, _ = _run(tmp_path)
    assert rc == 0
    assert _read(tmp_path, "notify_body.txt").startswith("[kw_daily] cover 판정 D=대역")


def test_skip_kw_also_skips_evening_cover(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed_universe_ok(root, 1)
    env = _env(root, QL_WEEKDAY="3")
    env["QL_SKIP_KW"] = "1"
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", "20260929"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stderr
    calls = (root / "calls.txt").read_text(encoding="utf-8").split()
    assert calls == [_CAL, "daily_wise", "daily.kis_daily", "daily.dart_daily", "gap"]


def test_dry_run_is_passed_to_evening_cover(tmp_path: Path) -> None:
    """dry-run 은 판정만 한다(콜·원장·런 로그 없음 — kw_daily 쪽 테스트가 본다)."""
    rc, calls, _, _ = _run(tmp_path, "--dry-run")
    assert rc == 0 and "kw_cover" in calls
    assert "--dry-run" in _read(tmp_path, "kw_cover_args.txt").split()


# ── GH1-d 리허설 — 테스트 원장 사본에서 D 행을 지우고 격리 QL_HOME 으로 06:00 체인을 돌린다 ─────────────
# 진짜 daily_ledger.sh · 진짜 kw_daily · 가짜 `api.kiwoom`(실제 키움 콜 없음). KIS·DART 는 대역.
# 판정 달력은 주말만 휴장인 2026 연도 파일, D = 20260929(화).
_FAKE_API = '''"""GH1-d 리허설 전용 가짜 `api` — `kiwoom()` 만 둔다. 실제 키움 콜은 없다.

콜은 `$QL_HOME/kw_calls.txt` 에 'api_id ticker' 로 적고, `FAKE_KW_DATES`(쉼표) 날짜 행만 돌려준다.
"""
import os

_KEYS = {"ka10014": "shrts_trnsn", "ka20068": "slb_rmnd", "ka10060": "invsr_trde"}


def _row(api_id, d):
    if api_id == "ka10014":
        return {"dt": d, "close_pric": "1", "shrts_qty": "1", "ovr_shrts_qty": "1"}
    if api_id == "ka20068":
        return {"dt": d, "rmnd": "5"}
    return {"dt": d, "ind_invsr": "1", "frgnr_invsr": "2"}


def kiwoom(api_id, url, body, cont=None, next_key=None):
    with open(os.path.join(os.environ["QL_HOME"], "kw_calls.txt"), "a", encoding="utf-8") as f:
        f.write(f"{api_id} {body['stk_cd']}\\n")
    dates = [d for d in os.environ["FAKE_KW_DATES"].split(",") if d]
    return {"return_code": 0, "return_msg": "정상", _KEYS[api_id]: [_row(api_id, d) for d in dates]}, {}
'''
_RD = "20260929"
_RD_PREV = "20260928"
_R_HIST = ("20260922", "20260923", "20260924", "20260925", _RD_PREV)
_R_TICKERS = tuple(f"{i:06d}" for i in range(10))


def _full_ledger(path: Path) -> None:
    """D 까지 저녁 직행을 다 받은 원장(마스터 + ka10060 + ka10014) — 리허설의 '정본'."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, code TEXT, upSizeName TEXT, "
                "marketName TEXT DEFAULT '거래소', mrkt_tp TEXT DEFAULT '0')")
    con.executemany("INSERT INTO ka10099_stock_master (snap_date, code, upSizeName) VALUES (?,?,?)",
                    [("20260930", t, "대형주") for t in _R_TICKERS])
    con.execute('CREATE TABLE ka10060_investor_flows ("ticker" TEXT NOT NULL, "dt" TEXT, "ind_invsr" TEXT, '
                '"frgnr_invsr" TEXT, "src_api" TEXT, "collected_at" TEXT, PRIMARY KEY ("ticker", "dt"))')
    con.execute('CREATE TABLE ka10014_short_selling ("ticker" TEXT NOT NULL, "dt" TEXT, "close_pric" TEXT, '
                '"shrts_qty" TEXT, "ovr_shrts_qty" TEXT, "src_api" TEXT, "collected_at" TEXT, '
                'PRIMARY KEY ("ticker", "dt"))')
    days = (*_R_HIST, _RD)
    con.executemany("INSERT INTO ka10060_investor_flows VALUES (?,?,?,?,?,?)",
                    [(t, d, "1", "2", "ka10060", "old") for d in days for t in _R_TICKERS])
    con.executemany("INSERT INTO ka10014_short_selling VALUES (?,?,?,?,?,?,?)",
                    [(t, d, "1", "1", "1", "ka10014", "old") for d in days for t in _R_TICKERS])
    con.commit()
    con.close()


def _d_rows(root: Path, table: str) -> int:
    con = sqlite3.connect(root / "data" / "raw" / "kiwoom.db")
    try:
        return int(con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE dt=?', (_RD,)).fetchone()[0])
    finally:
        con.close()


def _ka10060_rows_status(root: Path) -> lh.Status:
    """08:10 확정 체인의 필수 검사 `kiwoom.ka10060.rows` 를 이 원장에 그대로 돌린 결과."""
    con = sqlite3.connect(root / "data" / "raw" / "kiwoom.db")
    try:
        checks = {c.name: c for c in lh.check_kiwoom(con, None, _RD, _RD_PREV, len(_R_TICKERS))}
    finally:
        con.close()
    return checks["kiwoom.ka10060.rows"].status


def _rehearse(tmp_path: Path, *, source_dates: str) -> tuple[Path, subprocess.CompletedProcess[str]]:
    root = _root(tmp_path)
    _seed_universe_ok(root, 1)
    cal = root / "data" / "calendar"
    cal.mkdir(parents=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    (cal / "kis_holidays_2026.json").write_text(
        json.dumps({"year": 2026, "holidays": [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5]}),
        encoding="utf-8")
    full = tmp_path / "kiwoom_full.db"
    _full_ledger(full)
    copy = root / "data" / "raw" / "kiwoom.db"
    shutil.copy(full, copy)                                   # 테스트 원장 사본에서 D 행을 지운다
    con = sqlite3.connect(copy)
    for table in ("ka10060_investor_flows", "ka10014_short_selling"):
        con.execute(f'DELETE FROM "{table}" WHERE dt=?', (_RD,))
    con.commit()
    con.close()
    assert _ka10060_rows_status(root) is lh.Status.FAIL        # 이대로면 08:10 확정판이 막힌다
    fake = tmp_path / "fakeapi"
    fake.mkdir()
    (fake / "api.py").write_text(_FAKE_API, encoding="utf-8")
    env = _env(root, QL_WEEKDAY="3", REAL_KW="1", FAKE_API=str(fake), FAKE_KW_DATES=source_dates,
               QL_KW_NOT_BEFORE="00:00")                       # 06:00 하한은 운영 시각 — 테스트는 아무 때나 돈다
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", _RD],
                       env=env, capture_output=True, text=True, timeout=120, check=False)
    return root, p


def _chain_log(root: Path) -> str:
    return "".join(f.read_text(encoding="utf-8") for f in (root / "logs").glob("daily_ledger_*.log"))


def test_rehearsal_missing_evening_rows_are_backfilled_by_the_0600_chain(tmp_path: Path) -> None:
    """GH1-d — 전날 저녁 키움 누락(사본에서 D 행 삭제) → 06:00 체인이 다시 받아 08:10 필수 검사가 통과한다."""
    root, p = _rehearse(tmp_path, source_dates=f"{_RD},{_RD_PREV}")
    assert p.returncode == 0, p.stdout + p.stderr
    assert _d_rows(root, "ka10060_investor_flows") == len(_R_TICKERS)
    assert _d_rows(root, "ka10014_short_selling") == len(_R_TICKERS)
    assert _ka10060_rows_status(root) is lh.Status.PASS
    kw_calls = (root / "kw_calls.txt").read_text(encoding="utf-8").split("\n")
    assert sorted({ln.split()[0] for ln in kw_calls if ln}) == ["ka10014", "ka10060", "ka20068"]
    log = _chain_log(root)
    assert "보강 뒤 충족" in log and "──── kiwoom evening cover 종료 rc=0" in log
    notify = (root / "notify.txt").read_text(encoding="utf-8")
    assert notify.startswith("info|daily_ledger 완료")
    assert (root / "notify_body.txt").read_text(encoding="utf-8").startswith("[kw_daily] cover 판정")
    assert [(r.status, "보강 뒤 충족" in (r.detail or ""))
            for r in runlog.recent(_run_db(root), source="kiwoom_cover")] == [("ok", True)]


def test_rehearsal_source_also_empty_is_crit(tmp_path: Path) -> None:
    """GH1-d 음성 — 원천도 D 행을 주지 않으면 보강 뒤에도 미달 → crit · rc 2(08:10 은 원장 필수 검사에서 막힌다)."""
    root, p = _rehearse(tmp_path, source_dates=_RD_PREV)
    assert p.returncode == 2, p.stdout + p.stderr
    assert _d_rows(root, "ka10060_investor_flows") == 0
    assert _ka10060_rows_status(root) is lh.Status.FAIL
    notify = (root / "notify.txt").read_text(encoding="utf-8")
    assert notify.startswith("crit|daily_ledger 실패: kiwoom evening cover(rc=2)")
    assert "보강 뒤에도 미달 ka10060,ka10014" in (root / "notify_body.txt").read_text(encoding="utf-8")
    assert [r.status for r in runlog.recent(_run_db(root), source="kiwoom_cover")] == ["coverage_failed"]
