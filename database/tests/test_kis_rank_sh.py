"""scripts/kis_rank.sh(크론 래퍼) · scripts/watchdog.sh kis_rank — KIS 순위 스냅숏의 경보 등급(N-66).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 진짜 스크립트를 두고 돌린다(test_watchdog_sh 와 같은 방식).
`scripts/notify.sh` 는 "등급|제목|본문" 을 notify.txt 에 적는 대역이다 — 운영 notify.log · 원장 · 서버 경로를
건드리지 않는다. 래퍼 쪽 `.venv/bin/python` 은 정해진 rc 와 마지막 줄만 내는 대역이고(수집 로직은
test_daily_kis_rank 몫), 맥에 없는 flock 은 PATH 대역이다. 워치독 쪽은 진짜 `daily.kis_rank` 가 판정한다.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from daily import kis_rank

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SRC = Path(__file__).resolve().parents[1] / "src"
KST = dt.timezone(dt.timedelta(hours=9))
_CALENDAR = """class _Cal:
    def is_trading_day(self, d):
        return True


def load(*_a, **_k):
    return _Cal()
"""


class Run(NamedTuple):
    rc: int
    out: str
    notify: list[str]   # 줄마다 "등급|제목|본문"


def _root(home: Path, python: str) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "src/daily", "data/raw", "bin"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for name in ("kis_rank.sh", "watchdog.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    stubs = {".venv/bin/python": python,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             "bin/flock": '#!/usr/bin/env bash\nexit "${FLOCK_RC:-0}"\n'}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    (root / "src/daily/__init__.py").write_text("", encoding="utf-8")
    (root / "src/daily/calendar.py").write_text(_CALENDAR, encoding="utf-8")
    for name in ("kis_rank.py", "runlog.py"):
        shutil.copy(SRC / "daily" / name, root / "src/daily" / name)
    return root


def _run(home: Path, args: list[str], flock_rc: int = 0) -> Run:
    root = home / "quant-ledger"
    path = f"{root / 'bin'}:{os.environ['PATH']}" if shutil.which("flock") is None or flock_rc else os.environ["PATH"]
    env = dict(os.environ, HOME=str(home), PATH=path, FLOCK_RC=str(flock_rc),
               QL_KIS_RANK_LOCK=str(home / "kis_rank.lock"))
    p = subprocess.run(["bash", *args], cwd=root, env=env, capture_output=True, text=True, timeout=60, check=False)
    note = root / "notify.txt"
    lines = note.read_text(encoding="utf-8").splitlines() if note.exists() else []
    return Run(p.returncode, p.stdout + p.stderr, lines)


def _fake_collector(rc: int) -> str:
    return f'#!/usr/bin/env bash\necho "[kis_rank] 앞 줄"\necho "[kis_rank] 마지막 줄 rc={rc}"\nexit {rc}\n'


# ── 래퍼: rc → 경보 등급 ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("rc", "level"), [
    (0, None),                    # 받음 — 슬롯마다 info 는 남기지 않는다
    (3, None),                    # 격자 밖 · 휴장
    (5, "warn"),                  # 토큰 · 유량 · 호출 실패
    (2, "crit"),                  # 칸 · 행 수 이상
    (1, "crit"),                  # 파이썬 예상 밖 예외 — warn 으로 묻히면 안 된다
])
def test_wrapper_maps_collector_rc_to_alert_level(tmp_path: Path, rc: int, level: str | None) -> None:
    root = _root(tmp_path, _fake_collector(rc))
    r = _run(tmp_path, [str(root / "scripts/kis_rank.sh")])
    assert r.rc == rc
    assert [n.split("|", 1)[0] for n in r.notify] == ([level] if level else []), (r.notify, r.out)
    if level:
        assert f"마지막 줄 rc={rc}" in r.notify[0]



def test_wrapper_crit_body_carries_the_python_exception(tmp_path: Path) -> None:
    """진행 줄을 찍은 뒤 예외로 죽으면 알림 본문은 예외여야 한다 — 파이프의 stdout 버퍼가 traceback 뒤에 비워지면
    마지막 줄이 정상 진행 줄('ok rows=30')이 되어 crit 본문이 정상 수집처럼 보인다."""
    code = "print('[kis_rank] 2026-11-11T11:00 top_interest 0001 ok rows=30'); raise RuntimeError('disk full')"
    root = _root(tmp_path, f'#!/usr/bin/env bash\nexec "{sys.executable}" -c "{code}"\n')
    r = _run(tmp_path, [str(root / "scripts/kis_rank.sh")])
    assert r.rc == 1
    assert [n.split("|", 1)[0] for n in r.notify] == ["crit"], (r.notify, r.out)
    assert "RuntimeError: disk full" in r.notify[0]

def test_wrapper_skips_when_the_previous_run_holds_the_lock(tmp_path: Path) -> None:
    root = _root(tmp_path, _fake_collector(0))
    marker = root / "ran.txt"
    (root / ".venv/bin/python").write_text(f'#!/usr/bin/env bash\ntouch "{marker}"\n', encoding="utf-8")
    r = _run(tmp_path, [str(root / "scripts/kis_rank.sh")], flock_rc=1)
    assert r.rc == 3
    assert not marker.exists()
    assert r.notify == []


# ── 워치독: 하루 요약 → 경보 등급 ─────────────────────────────────────────────

def _seed(root: Path, ok_slots: int) -> None:
    day = dt.datetime.now(KST).date().isoformat()
    con = sqlite3.connect(root / "data/raw/kis_rank.db")
    kis_rank.ensure_schema(con)
    rows = [(f"{day}T{t:%H:%M}", t_.endpoint, t_.scope)
            for t in kis_rank.SLOTS[:ok_slots] for t_ in kis_rank.TARGETS]
    con.executemany("INSERT INTO kis_rank_runs (slot_kst, endpoint, scope, started_utc, n_calls, verdict) "
                    "VALUES (?, ?, ?, '2026-11-11T00:00:00Z', 1, 'ok')", rows)
    con.commit()
    con.close()


@pytest.mark.parametrize(("ok_slots", "level", "title"), [
    (25, "info", "watchdog kis_rank 정상"),
    (22, "warn", "watchdog: KIS 순위 스냅숏 슬롯 부족"),
    (0, "crit", "watchdog: 20:45 까지 KIS 순위 스냅숏 없음/판독 불가"),
    (None, "crit", "watchdog: 20:45 까지 KIS 순위 스냅숏 없음/판독 불가"),     # 원장 파일 없음
])
def test_watchdog_alert_level_follows_the_day_summary(tmp_path: Path, ok_slots: int | None, level: str,
                                                      title: str) -> None:
    root = _root(tmp_path, f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
    if ok_slots is not None:
        _seed(root, ok_slots)
    r = _run(tmp_path, [str(root / "scripts/watchdog.sh"), "kis_rank"])
    assert [n.split("|", 1)[0] for n in r.notify] == [level], (r.notify, r.out)
    assert r.notify[0].split("|")[1].startswith(title)
    assert r.rc == {"info": 0, "warn": 1, "crit": 2}[level]
