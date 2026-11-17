"""scripts/dart_intraday.sh — 낮 분할 수집(거래일 12:30 · 14:30 · 16:15 · 17:15).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역 `.venv/bin/python` · `scripts/notify.sh` 를 두고 진짜 스크립트를
돌린다(test_daily_evening_sh 와 같은 방식). 대역 python 은 캘린더 질의에 `TD_RC` 로 답하고 `-m` 호출은 인자를
calls.txt 에 적은 뒤 `DART_RC` 로 끝난다. '기다리는 사이 저녁 수집이 돌았나' 질의에는 `EVE_RC` 로 답한다.
원장 락은 PATH 앞의 대역 `flock` 으로 흉내 낸다(원장 락 비대기 `-n 9` 는 `FLOCK_N9_RC`, 대기자 락 `-n 8` 은
`FLOCK_N8_RC`, 나머지는 0) — 운영 경로(/tmp 락 · 서버 원장)는 건드리지 않는다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "dart_intraday.sh"
D = "20261113"

_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in *first_started_utc*) exit "${EVE_RC:-0}" ;; esac
  exit "${TD_RC:-0}"
fi
shift
echo "$*" >> "$QL_HOME/calls.txt"
exit "${DART_RC:-0}"
"""


class Run(NamedTuple):
    rc: int
    out: str
    notify: list[str]               # 대역 notify — 줄마다 "등급|제목|본문"
    calls: list[str]                # 대역 python 이 받은 `-m` 뒤 인자 한 줄씩
    log: str
    made: list[str]                 # data/ 아래 생긴 파일(인계 파일을 쓰지 않는지 본다)


_FLOCK = """#!/usr/bin/env bash
case "$*" in
  "-n 9") exit "${FLOCK_N9_RC:-0}" ;;
  "-n 8") exit "${FLOCK_N8_RC:-0}" ;;
esac
exit 0
"""


def _run(home: Path, *, dart_rc: int = 0, td_rc: int = 0, held: bool = False,
         waiter: bool = False, evening_ran: bool = False) -> Run:
    """held = 다른 원장 작업이 락을 쥠 · waiter = 같은 스크립트가 이미 기다림 · evening_ran = 그 D 의 저녁 수집 런이 있음."""
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data", "bin"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "dart_intraday.sh")
    shutil.copy(SCRIPT.parent / "raw_lock.sh", root / "scripts" / "raw_lock.sh")
    stubs = {".venv/bin/python": _PY,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             # 저녁 판 훅을 부르면 드러나게 흔적을 남긴다
             "scripts/postclose_chain.sh": "#!/usr/bin/env bash\necho hook >> hook.txt\n",
             "bin/flock": _FLOCK}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    env = dict(os.environ, HOME=str(home), PATH=f"{root / 'bin'}:{os.environ['PATH']}",
               QL_RAW_LOCK_FILE=str(home / "raw.lock"), DART_RC=str(dart_rc), TD_RC=str(td_rc),
               FLOCK_N9_RC=str(int(held)), FLOCK_N8_RC=str(int(waiter)), EVE_RC=str(int(evening_ran)))
    for k in ("QL_RAW_LOCK_HELD", "QL_HOME", "PYTHONPATH"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(root / "scripts" / "dart_intraday.sh"), "--date", D],
                       env=env, capture_output=True, text=True, timeout=60, check=False)

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return Run(p.returncode, p.stdout + p.stderr, read(root / "notify.txt").splitlines(),
               read(root / "calls.txt").splitlines(), read(root / "logs" / f"dart_intraday_{D}.log"),
               sorted(str(f.relative_to(root)) for f in (root / "data").rglob("*") if f.is_file())
               + (["hook.txt"] if (root / "hook.txt").exists() else []))


def test_trading_day_runs_the_collector_in_intraday_mode_only(tmp_path: Path) -> None:
    """낮 런은 수집만 한다 — `--intraday` 로 수집기를 한 번 부르고, 저녁 인계 파일 · 저녁 판 훅은 건드리지 않는다
    (18:05 저녁 체인 몫). 성공하면 알림도 없다."""
    r = _run(tmp_path)
    assert r.rc == 0, r.out + r.log
    assert r.calls == [f"daily.dart_daily --date {D} --intraday"]
    assert r.made == [] and r.notify == []


def test_collector_failure_is_a_warn_not_a_crit(tmp_path: Path) -> None:
    """낮 런 실패는 18:05 런이 받지 않은 유닛을 다시 부르므로 warn 한 줄이다."""
    r = _run(tmp_path, dart_rc=2)
    assert r.rc == 2
    assert [n.split("|")[0] for n in r.notify] == ["warn"]
    assert f"D={D}" in r.notify[0]


def test_waits_for_the_ledger_lock_then_collects(tmp_path: Path) -> None:
    """다른 원장 작업(앞 회차 낮 수집 · 배포)이 락을 쥐고 있으면 끝날 때까지 기다렸다 이어서 수집한다 — 회차를
    건너뛰지 않는다(P9). 옛 코드는 `flock -n` 실패에 그 회차를 통째로 건너뛰었다."""
    r = _run(tmp_path, held=True)
    assert r.rc == 0, r.out + r.log
    assert r.calls == [f"daily.dart_daily --date {D} --intraday"]
    assert [n.split("|")[:2] for n in r.notify] == [["info", "dart_intraday 원장 락 대기"]]
    assert "원장 락 대기" in r.log


def test_second_waiter_leaves_the_queue_to_the_first(tmp_path: Path) -> None:
    """같은 낮 수집이 이미 기다리는 중이면 줄을 또 서지 않는다(대기자 하나, N-27 ⑥) — 먼저 기다린 회차가 더 늦게
    시작해 같은 공시를 다 받는다. 수집은 부르지 않고 rc 3."""
    r = _run(tmp_path, held=True, waiter=True)
    assert r.rc == 3, r.out + r.log
    assert r.calls == []
    assert [n.split("|")[0] for n in r.notify] == ["info"]


def test_waited_past_the_evening_run_ends_without_collecting(tmp_path: Path) -> None:
    """기다리는 사이 그 D 의 18:05 저녁 수집이 먼저 돌았으면 수집 없이 끝낸다 — 저녁 런이 그날 접수분을 다 받았고,
    밤에 낮 수집이 다시 돌 이유가 없다."""
    r = _run(tmp_path, held=True, evening_ran=True)
    assert r.rc == 0, r.out + r.log
    assert r.calls == []
    assert "저녁 수집" in r.log


def test_free_lock_does_not_ask_about_the_evening_run(tmp_path: Path) -> None:
    """기다리지 않았으면 저녁 런 유무를 묻지 않고 수집한다 — 저녁 런 뒤 손으로 다시 돌리는 것을 막지 않는다."""
    r = _run(tmp_path, evening_ran=True)
    assert r.rc == 0, r.out + r.log
    assert r.calls == [f"daily.dart_daily --date {D} --intraday"] and r.notify == []


def test_holiday_is_skipped_and_calendar_error_is_crit(tmp_path: Path) -> None:
    holiday = _run(tmp_path / "a", td_rc=1)
    assert (holiday.rc, holiday.calls, holiday.notify) == (0, [], [])
    broken = _run(tmp_path / "b", td_rc=2)
    assert broken.rc == 2 and broken.calls == []
    assert [n.split("|")[0] for n in broken.notify] == ["crit"]
