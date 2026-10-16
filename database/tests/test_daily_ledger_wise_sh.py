"""scripts/daily_ledger.sh — 06:00 WISE 스냅샷 갈래의 부분 실패 알림(N-53 · 결정 N-27 ③ · N-30 ③).

WISE 스냅샷은 18:05 저녁 체인에서 06:00 체인으로 옮겼다(N-53 — 그날 장 마감 판이 그날 수집분을 읽게).
수집기(`src/backfill_wise.py`)는 일부 콜이 실패해도 rc 0 으로 끝난다. 체인은 이번 런의 런 로그(`ws_run_log`)를
세어 실패가 1콜이라도 있으면 warn 1건을 남긴다 — 08:10 확정 체인 전에 같은 날 다시 받으면 그날 장 마감 판에도
실린다. 수집기 rc≠0 은 다른 단계처럼 FAILED(crit)다. (저녁 체인 시절 `test_daily_evening_sh.py` 의 같은 테스트를
옮겼다.)

임시 루트(`QL_LEDGER_ROOT`)에 대역 `.venv/bin/python`·`scripts/*.sh` 를 두고 진짜 스크립트를 돌린다. 대역 python 은
런 로그(runlog — 아직 안 함·런 id·종료 기록)와 DART 번호표 판정(7일 이내)에만 답하고, `-m` 은 모듈 이름을 적고
rc 0, 나머지 `-c`(WISE 실패 콜 집계)는 진짜 python 에 넘긴다. WISE 갈래는 **진짜 `backfill_wise.main()`** 을 돌린다
— 네트워크(`fetch`·`encparam`)와 유니버스만 대역이라 런 로그의 n_req·n_ok·n_bad·bad_summary 는 실물 수집기가
쓴다. 원장 락은 `QL_RAW_LOCK_HELD=1`(부모가 쥔 것으로 물려받음)로 잡지 않는다. 운영 경로는 건드리지 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import backfill_wise as bw  # conftest 가 src/ 를 올려 준다 — DDL 만 쓴다
import pytest

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "daily_ledger.sh"
D = "20261006"
BAD = "082640"      # 09-02 실례 — 08-31 상장폐지 직후 유니버스에 남아 invalid_stock 1콜(동양생명)

_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *dart_universe*) echo "마지막 성공 (대역, 1일 전)"; exit 0 ;;
    *runlog.recent*) exit 1 ;;
    *runlog.start*) echo 7; exit 0 ;;
    *runlog.finish*) echo "$4|$5" >> "$QL_HOME/runlog.txt"; exit 0 ;;
  esac
  exec "$REAL_PY" "$@"
fi
if [ "$1" = "-m" ]; then
  echo "$2" >> "$QL_HOME/calls.txt"
  [ "$2" = daily.kis_daily ] && exit "${KIS_RC:-0}"
  [ "$2" = daily.calendar_refresh ] && echo "휴장 달력 대역: rc=0"
  exit 0
fi
if [ "$1" = "src/backfill_wise.py" ]; then shift; exec "$REAL_PY" "$QL_HOME/fake_wise.py" "$@"; fi
exit 0
"""
# 대역 수집기 — 진짜 backfill_wise.main() 에 네트워크·유니버스만 바꿔 넣는다.
#   WISE_FAIL=none           전부 ok
#   WISE_FAIL=invalid_stock  BAD 종목 c1010001 이 '올바른 종목이 아닙니다'(본문 검증 실패 n_bad 1)
#   WISE_FAIL=http           BAD 종목 cF5002 한 콜이 http503(전송 실패 — n_bad 0, n_ok = n_req − 1)
#   WISE_FAIL=crash          수집 전에 rc 1 (런 로그 없음)
#   WISE_FAIL=nolog          rc 0 인데 런 로그 없음(실제 수집기는 --limit 없는 rc 0 런이면 반드시
#                            1행 쓴다)
#   WISE_FAIL=badjson        정상 런 뒤 그 런 로그의 bad_summary 가 깨짐(읽기 쪽 파싱 실패)
_FAKE_WISE = """import json, os, sys
sys.path.insert(0, os.environ["REAL_SRC"])
import backfill_wise as bw

MODE = os.environ.get("WISE_FAIL", "none")
BAD = os.environ["WISE_BAD"]
if MODE == "crash":
    sys.exit(1)
if MODE == "nolog":
    sys.exit(0)


def fetch(cmp_cd, ep, pkey, url):
    if MODE == "http" and cmp_cd == BAD and ep == "cF5002" and pkey == bw.kst_today()[:4] + "12":
        return cmp_cd, "http503", b"", 0, 1
    if ep == "c1010001":
        invalid = MODE == "invalid_stock" and cmp_cd == BAD
        body = ("올바른 종목이 아닙니다" if invalid else "투자의견 " * 1000).encode()
    elif ep == "cF5001":
        body = json.dumps({"chart1": json.dumps({"select_item": [1]}),
                           "chart2": json.dumps({"select_item": [None]})}).encode()
    else:
        body = b'{"JsonData": []}'
    return cmp_cd, "ok", body, len(body), 1


bw.universe = lambda con: ["005930", BAD]
bw.fetch = fetch
bw.encparam = lambda refresh=False: "tok"
sys.argv = ["backfill_wise.py", *sys.argv[1:]]
bw.main()
if MODE == "badjson":
    import sqlite3
    con = sqlite3.connect(bw.DB)
    con.execute("UPDATE ws_run_log SET bad_summary = '{broken'")
    con.commit()
    con.close()
"""


class Run(NamedTuple):
    rc: int
    out: str                        # stdout + stderr
    notify: list[str]               # 대역 notify — 줄마다 "등급|제목|본문"
    runlog: list[str]               # ledger_chain 종료 기록 "status|detail"
    log: str                        # 체인 로그 logs/daily_ledger_<KST 오늘>.log
    day: str                        # 스냅샷 날짜(KST 오늘)

    def by_level(self, level: str) -> list[str]:
        return [n for n in self.notify if n.startswith(f"{level}|")]


def _root(base: Path) -> Path:
    root = base / "ql"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for name in ("daily_ledger.sh", "raw_lock.sh", "postclose_conf.sh"):
        shutil.copy(SCRIPT.parent / name, root / "scripts" / name)
    stubs = {".venv/bin/python": _PY,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             "scripts/sync_calendar.sh": "#!/usr/bin/env bash\nexit 0\n",
             "scripts/daily_wise.sh": "#!/usr/bin/env bash\nexit 0\n",
             "scripts/dart_company_gap.sh": "#!/usr/bin/env bash\nexit 0\n"}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    (root / "fake_wise.py").write_text(_FAKE_WISE, encoding="utf-8")
    return root


def _run(base: Path, wise_fail: str, *, kis_rc: int = 0) -> Run:
    root = _root(base)
    env = dict(os.environ, QL_LEDGER_ROOT=str(root), QL_RAW_LOCK_HELD="1", QL_WEEKDAY="3",
               REAL_PY=sys.executable, REAL_SRC=str(DB_ROOT / "src"),
               WISE_FAIL=wise_fail, WISE_BAD=BAD, KIS_RC=str(kis_rc))
    for k in ("QL_SKIP_KW", "QL_HOME", "PYTHONPATH", "QL_RAW_LOCK_FILE"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(root / "scripts" / "daily_ledger.sh"), "--date", D],
                       env=env, capture_output=True, text=True, timeout=120, check=False)

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    log = "".join(read(f) for f in sorted((root / "logs").glob("daily_ledger_*.log")))
    day = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
    return Run(p.returncode, p.stdout + p.stderr, read(root / "notify.txt").splitlines(),
               read(root / "runlog.txt").splitlines(), log, day)


def _run_log(base: Path) -> list[tuple[int, int, int, str]]:
    """대역 수집기가 남긴 실물 런 로그 — (n_req, n_ok, n_bad, bad_summary)."""
    con = sqlite3.connect(base / "ql" / "data" / "raw" / "wisereport.db")
    try:
        return con.execute("SELECT n_req, n_ok, n_bad, bad_summary FROM ws_run_log").fetchall()
    finally:
        con.close()


def _seed_prior_run(base: Path, minutes_ago: int = 30) -> None:
    """같은 날 앞 런 — `minutes_ago` 분 전 run_at 으로 실패 런 로그 1행(n_bad 5, n_req − n_ok 10).
    스키마는 실물 수집기의 DDL 이다."""
    db = base / "ql" / "data" / "raw" / "wisereport.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    earlier = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes_ago)
    con = sqlite3.connect(db)
    try:
        con.executescript(bw.DDL)
        con.execute("INSERT INTO ws_run_log VALUES (?,?,?,?,?,?,?)",
                    (earlier.strftime("%Y-%m-%dT%H:%M:%S"), "full", 2, 30, 20, 5,
                     json.dumps({"invalid_stock": 5})))
        con.commit()
    finally:
        con.close()


def test_partial_failure_warns_once_and_keeps_chain_ok(tmp_path: Path) -> None:
    """WISE 갈래가 n_bad=1(invalid_stock) 런을 남기면 warn 1건, 체인은 rc 0·런 기록 ok 그대로."""
    r = _run(tmp_path, "invalid_stock")
    assert r.rc == 0, r.out + r.log
    assert [row[2:] for row in _run_log(tmp_path)] == [(1, '{"invalid_stock": 1}')]
    warns = r.by_level("warn")
    assert len(warns) == 1, r.notify
    _level, title, body = warns[0].split("|", 2)
    assert title == "WISE 수집 일부 실패 1콜"
    assert body.startswith(f"스냅샷={r.day} | 종류 ")
    assert '"invalid_stock": 1' in body
    assert "README 'WISE 같은 날 재실행'(08:10 전)" in body
    assert f"logs/wise_{r.day}.log" in body
    assert r.by_level("crit") == []
    assert [n.split("|")[1] for n in r.by_level("info")] == ["daily_ledger 완료"]
    assert [ln.split("|")[0] for ln in r.runlog] == ["ok"]


def test_transport_failure_is_counted_too(tmp_path: Path) -> None:
    """전송 실패(http·exc·notjson)는 n_bad 에 안 들어가지만 08:10 wise.run(n_ok = n_req)은
    FAIL 시킨다 — 같은 경보를 낸다(P1). 런 로그의 n_req − n_ok 로 센다."""
    r = _run(tmp_path, "http")
    assert r.rc == 0, r.out + r.log
    (n_req, n_ok, n_bad, _), = _run_log(tmp_path)
    assert (n_req - n_ok, n_bad) == (1, 0)
    warns = r.by_level("warn")
    assert [n.split("|")[1] for n in warns] == ["WISE 수집 일부 실패 1콜"]
    assert '"전송 실패(http·exc·notjson)": 1' in warns[0]


def test_clean_run_has_no_warn(tmp_path: Path) -> None:
    """n_bad=0 · 전송 실패 0 → warn 없음. 체인 로그에 '실패 콜 0' 이 남는다."""
    r = _run(tmp_path, "none")
    assert r.rc == 0, r.out + r.log
    assert [n.split("|")[1] for n in r.notify] == ["daily_ledger 완료"]
    assert f"  WISE 스냅샷 {r.day} 실패 콜 0 {{}}" in r.log


def test_wise_crash_is_crit_and_failure_count_unknown(tmp_path: Path) -> None:
    """수집기가 rc≠0 이면 다른 단계처럼 crit(FAILED)·런 기록 failed. 런 로그가 없으니 실패 콜 수는 모름이고
    warn 은 없다."""
    r = _run(tmp_path, "crash")
    assert r.rc == 2, r.out + r.log
    assert [n.split("|")[1] for n in r.by_level("crit")] == ["daily_ledger 실패: wise(rc=1)"]
    assert r.by_level("warn") == []
    assert r.runlog == ["failed|wise(rc=1)"]


@pytest.mark.parametrize("mode", ["nolog", "badjson"])
def test_rc0_but_unreadable_run_log_warns_unknown(tmp_path: Path, mode: str) -> None:
    """수집기 rc 0 인데 이번 런의 런 로그가 0행이거나 읽다 깨지면 실패 수를 모른다 — warn 1건(P1).
    `--limit` 없는 rc 0 런은 대상 0 이어도 런 로그를 1행 쓰므로 0행은 실제 이상이다. 체인 rc·info 는 그대로."""
    r = _run(tmp_path, mode)
    assert r.rc == 0, r.out + r.log
    warns = r.by_level("warn")
    assert [n.split("|")[1] for n in warns] == ["WISE 실패 콜 수 확인 불가"], r.notify
    body = warns[0].split("|", 2)[2]
    assert body.startswith(f"스냅샷={r.day} | 수집기 rc 0, 런 로그(run_at>=")
    assert "못 읽음 | 로그 logs/daily_ledger_" in body
    assert r.by_level("crit") == []
    assert [n.split("|")[1] for n in r.by_level("info")] == ["daily_ledger 완료"]


def test_prior_same_day_run_is_not_counted(tmp_path: Path) -> None:
    """같은 날 앞 런(30분 전, 실패 10콜)은 세지 않는다 — 이번 런이 깨끗하면 warn 0. 회복 여부는 08:10
    건전성 몫이다. 하한(run_at ≥ 갈래 시작)을 빼면 이 테스트가 FAIL 한다."""
    _seed_prior_run(tmp_path)
    r = _run(tmp_path, "none")
    assert r.rc == 0, r.out + r.log
    assert len(_run_log(tmp_path)) == 2                  # 앞 런 + 이번 런
    assert r.by_level("warn") == [], r.notify


def test_other_step_crit_and_wise_warn_both_go_out(tmp_path: Path) -> None:
    """KIS 단계 실패(crit)와 WISE 1콜 실패가 겹치면 crit 1·warn 1 둘 다 — WISE 재실행은 따로 해야 한다."""
    r = _run(tmp_path, "invalid_stock", kis_rc=2)
    assert r.rc == 2, r.out + r.log
    assert [n.split("|")[1] for n in r.by_level("crit")] == ["daily_ledger 실패: kis credit(rc=2)"]
    assert [n.split("|")[1] for n in r.by_level("warn")] == ["WISE 수집 일부 실패 1콜"]
