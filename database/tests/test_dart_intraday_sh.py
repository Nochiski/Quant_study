"""scripts/dart_intraday.sh — 낮 분할 수집(거래일 12:30 · 14:30 · 16:15 · 17:15).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역 `.venv/bin/python` · `scripts/notify.sh` 를 두고 진짜 스크립트를
돌린다(test_daily_evening_sh 와 같은 방식). 대역 python 은 캘린더 질의에 `TD_RC` 로 답하고 `-m` 호출은 인자를
calls.txt 에 적은 뒤 `DART_RC` 로 끝난다. 원장 락은 PATH 앞의 대역 `flock`(rc = `FLOCK_RC`)으로 흉내 낸다 —
운영 경로(/tmp 락 · 서버 원장)는 건드리지 않는다.
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
if [ "$1" = "-c" ]; then exit "${TD_RC:-0}"; fi
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


def _run(home: Path, *, dart_rc: int = 0, td_rc: int = 0, flock_rc: int = 0) -> Run:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data", "bin"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "dart_intraday.sh")
    shutil.copy(SCRIPT.parent / "raw_lock.sh", root / "scripts" / "raw_lock.sh")
    stubs = {".venv/bin/python": _PY,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             # 저녁 판 훅을 부르면 드러나게 흔적을 남긴다
             "scripts/postclose_chain.sh": "#!/usr/bin/env bash\necho hook >> hook.txt\n",
             "bin/flock": '#!/usr/bin/env bash\nexit "${FLOCK_RC:-0}"\n'}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    env = dict(os.environ, HOME=str(home), PATH=f"{root / 'bin'}:{os.environ['PATH']}",
               QL_RAW_LOCK_FILE=str(home / "raw.lock"), DART_RC=str(dart_rc), TD_RC=str(td_rc),
               FLOCK_RC=str(flock_rc))
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


def test_skips_without_waiting_when_the_ledger_lock_is_held(tmp_path: Path) -> None:
    """다른 원장 작업이 락을 쥐고 있으면 기다리지 않고 건너뛴다 — 낮 런이 줄을 서면 18:05 저녁 체인 앞에 끼어든다."""
    r = _run(tmp_path, flock_rc=1)
    assert r.rc == 0, r.out + r.log
    assert r.calls == [] and r.notify == []
    assert "건너뜀" in r.log


def test_holiday_is_skipped_and_calendar_error_is_crit(tmp_path: Path) -> None:
    holiday = _run(tmp_path / "a", td_rc=1)
    assert (holiday.rc, holiday.calls, holiday.notify) == (0, [], [])
    broken = _run(tmp_path / "b", td_rc=2)
    assert broken.rc == 2 and broken.calls == []
    assert [n.split("|")[0] for n in broken.notify] == ["crit"]
