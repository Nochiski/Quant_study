"""scripts/sync_calendar.sh — v3 휴장 사본은 병행 대조용 `data/calendar/v3/` 에만 쌓는다(K1-9, N-31 ②).

판정 디렉터리의 연도 파일(`data/calendar/kis_holidays_<year>.json`)은 KIS 직접 갱신
(`daily.calendar_refresh`)이 쓴다. 동기화가 그 파일을 덮으면 06:00 에 받은 임시공휴일이 18:05 에 지워진다.
임시 `QL_HOME` 에 진짜 python(`.venv/bin/python` → 이 인터프리터)과 대역 `notify.sh` 를 두고 진짜 스크립트를 돌린다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "sync_calendar.sh"


def _v3_holidays(year: int) -> list[str]:
    """v3 캐시 형식의 휴장 목록 — 그해 토·일 전부 + 평일 휴장 몇 개(검증 ≥100건·주말 ≥90 통과)."""
    d = dt.date(year, 1, 1)
    out: list[str] = []
    while d.year == year:
        if d.weekday() >= 5 or d.strftime("%m%d") in ("0101", "1009", "1225", "1231"):
            out.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    return out


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ql"
    (home / "scripts").mkdir(parents=True)
    (home / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(SCRIPT, home / "scripts" / "sync_calendar.sh")
    (home / ".venv" / "bin" / "python").symlink_to(sys.executable)
    notify = home / "scripts" / "notify.sh"
    notify.write_text('#!/usr/bin/env bash\necho "$1|$2" >> notify.txt\n', encoding="utf-8")
    notify.chmod(0o755)
    return home


def _run(home: Path, src: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, QL_HOME=str(home))
    return subprocess.run(["bash", str(home / "scripts" / "sync_calendar.sh"), str(src)], env=env,
                          capture_output=True, text=True, timeout=60, check=False)


def test_v3_copy_goes_to_v3_dir_and_leaves_the_judging_file(tmp_path: Path) -> None:
    home = _home(tmp_path)
    judging = home / "data" / "calendar" / "kis_holidays_2026.json"
    judging.parent.mkdir(parents=True)
    # 직접 갱신이 06:00 에 반영한 임시공휴일(10-20)이 든 판정 파일
    mine = {"year": 2026, "holidays": [*_v3_holidays(2026), "20261020"], "source": "kis_direct"}
    judging.write_text(json.dumps(mine), encoding="utf-8")
    before = judging.read_bytes()
    src = tmp_path / ".kis_holidays.json"
    src.write_text(json.dumps({"year": 2026, "holidays": _v3_holidays(2026),
                               "fetched_at": "2026-10-01T00:00:00"}), encoding="utf-8")

    p = _run(home, src)

    assert p.returncode == 0, p.stdout + p.stderr
    assert judging.read_bytes() == before                    # 옛 코드는 여기서 v3 사본으로 덮었다
    copied = home / "data" / "calendar" / "v3" / "kis_holidays_2026.json"
    assert json.loads(copied.read_text(encoding="utf-8"))["holidays"] == _v3_holidays(2026)
    assert (copied.stat().st_mode & 0o777) == 0o600
    assert not (home / "notify.txt").exists()


def test_invalid_v3_copy_keeps_the_previous_v3_file(tmp_path: Path) -> None:
    home = _home(tmp_path)
    v3 = home / "data" / "calendar" / "v3"
    v3.mkdir(parents=True)
    prev = v3 / "kis_holidays_2026.json"
    prev.write_text(json.dumps({"year": 2026, "holidays": _v3_holidays(2026)}), encoding="utf-8")
    before = prev.read_bytes()
    src = tmp_path / ".kis_holidays.json"
    src.write_text(json.dumps({"year": 2026, "holidays": ["20260101"]}), encoding="utf-8")  # 1건

    p = _run(home, src)

    assert p.returncode == 1
    assert prev.read_bytes() == before
    assert (home / "notify.txt").read_text(encoding="utf-8").startswith("warn|캘린더 검증 실패")
