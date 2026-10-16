"""scripts/daily_evening.sh — 18:05 저녁 원장 슬롯(키움 21:05 ∥ DART).

WISE 스냅샷은 06:00 `daily_ledger.sh` 로 옮겼다(N-53 — V2-3 되돌림). 이 파일은 저녁 체인이 WISE 를 더는
부르지 않고 인계 파일(`ledger_evening.json`)·런 기록에 WISE 키가 없는지와 휴장 파일 내보내기 스위치(T-48)를
본다. WISE 부분 실패 알림은 `test_daily_ledger_wise_sh.py`.

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역 `.venv/bin/python`·`scripts/notify.sh`·
`scripts/sync_calendar.sh` 를 두고 진짜 스크립트를 돌린다(test_daily_build_sh 와 같은 방식).
v3 휴장 사본 동기화 대역은 부를 때마다 sync.txt 에 한 줄을 적고, 스위치 판정(`scripts/postclose_conf.sh`)은 진짜를 복사한다.
대역 python 은 캘린더·runlog 질의에만 답하고, 인계 파일 쓰기 `-c` 는 진짜 python 에 넘긴다. 스크립트 경로
호출(`src/<파일>.py` — 예전 WISE 수집기)은 calls.txt 에 그 경로를 적는다.
원장 락은 `QL_RAW_LOCK_HELD=1`(부모가 쥔 것으로 물려받음)로 잡지 않고, 키움 대기 하한은
`QL_KW_EVENING_HHMM=0000` 으로 없앤다. 운영 경로(/tmp 락·서버 원장)는 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "daily_evening.sh"
D = "20261006"

# 대역 python — 캘린더(거래일)·runlog(아직 안 함·런 id·종료 기록)만 답하고 나머지 `-c` 는 진짜
# python(REAL_PY)으로 넘긴다. `-m` 은 모듈 이름만 적고 rc 0(키움 kw_daily 만 KW_RC). 스크립트 경로 호출은
# calls.txt 에 경로를 적는다(저녁 체인이 WISE 수집기를 부르면 드러난다).
_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *is_trading_day*) exit 0 ;;
    *runlog.recent*) exit 1 ;;
    *runlog.start*) echo 7; exit 0 ;;
    *runlog.finish*) echo "$4|$5" >> "$QL_HOME/runlog.txt"; exit 0 ;;
  esac
  exec "$REAL_PY" "$@"
fi
if [ "$1" = "-m" ]; then
  echo "$2" >> "$QL_HOME/calls.txt"
  [ "$2" = daily.kw_daily ] && exit "${KW_RC:-0}"
  exit 0
fi
echo "$1" >> "$QL_HOME/calls.txt"
exit 0
"""


class Run(NamedTuple):
    rc: int
    out: str                        # stdout + stderr
    notify: list[str]               # 대역 notify — 줄마다 "등급|제목|본문"
    deliver: dict[str, object] | None   # data/deliver/ledger_evening.json (dry-run 은 없음)
    runlog: list[str]               # evening_chain 종료 기록 "status|detail"
    log: str                        # 체인 로그 logs/daily_evening_<KST 오늘>.log
    calls: list[str]                # 대역 python 이 받은 모듈·스크립트 경로

    def by_level(self, level: str) -> list[str]:
        return [n for n in self.notify if n.startswith(f"{level}|")]


def _run(home: Path, *, dry: bool = False, kw_rc: int = 0, conf: str | None = None) -> Run:
    """`conf` 는 config/calendar_export.env 내용(None 이면 파일 없음 — 꺼짐)."""
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "daily_evening.sh")
    shutil.copy(SCRIPT.parent / "raw_lock.sh", root / "scripts" / "raw_lock.sh")
    shutil.copy(SCRIPT.parent / "postclose_conf.sh", root / "scripts" / "postclose_conf.sh")
    if conf is not None:
        (root / "config").mkdir(exist_ok=True)
        (root / "config" / "calendar_export.env").write_text(conf, encoding="utf-8")
    stubs = {".venv/bin/python": _PY,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             "scripts/sync_calendar.sh": "#!/usr/bin/env bash\necho sync >> sync.txt\nexit 0\n",
             # 장 마감 재반영 훅(PR-8 ⑦) — 이 파일은 갈래·스위치만 보므로 rc 0 대역(훅 자체는 test_postclose_chain_sh)
             "scripts/postclose_chain.sh": "#!/usr/bin/env bash\nexit 0\n"}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"), QL_RAW_LOCK_HELD="1",
               QL_KW_EVENING_HHMM="0000", REAL_PY=sys.executable, KW_RC=str(kw_rc))
    for k in ("QL_EVENING_NOT_BEFORE", "QL_HOME", "PYTHONPATH"):
        env.pop(k, None)
    args = ["--date", D] + (["--dry-run"] if dry else [])
    p = subprocess.run(["bash", str(root / "scripts" / "daily_evening.sh"), *args],
                       env=env, capture_output=True, text=True, timeout=120, check=False)

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    deliver = root / "data" / "deliver" / "ledger_evening.json"
    log = "".join(read(f) for f in sorted((root / "logs").glob("daily_evening_*.log")))
    return Run(p.returncode, p.stdout + p.stderr, read(root / "notify.txt").splitlines(),
               json.loads(deliver.read_text(encoding="utf-8")) if deliver.exists() else None,
               read(root / "runlog.txt").splitlines(), log, read(root / "calls.txt").split())


def _deliver_writes(log: str) -> list[dict[str, object]]:
    """체인 로그에 남은 인계 파일 쓰기 기록(`deliver_json` 출력) — 순서대로. 첫 번째가 dart_rc null
    단계다."""
    mark = "  deliver/ledger_evening.json 기록 "
    return [json.loads(ln[len(mark):]) for ln in log.splitlines() if ln.startswith(mark)]


def test_evening_chain_runs_kiwoom_and_dart_only(tmp_path: Path) -> None:
    """N-53 — 저녁 체인은 키움·DART 두 갈래다. WISE 수집기(`src/backfill_wise.py`)를 부르지 않고, 인계
    파일(두 번 다)·런 기록 detail 에 WISE 키가 없다. 옛 체인은 WISE 를 18:05 에 받아 그날 장 마감 판(15:41)이
    쓸 수 없었다."""
    r = _run(tmp_path)
    assert r.rc == 0, r.out + r.log
    assert "src/backfill_wise.py" not in r.calls
    assert sorted(set(r.calls)) == ["daily.dart_daily", "daily.kw_daily"]
    first, final = _deliver_writes(r.log)
    assert first["dart_rc"] is None and final["dart_rc"] == 0
    for w in (first, final):
        assert not [k for k in w if "wise" in k], w
        assert w["kiwoom_rc"] == 0
    assert [ln.split("|")[0] for ln in r.runlog] == ["ok"]
    assert "wise" not in r.runlog[0]
    assert [n.split("|")[1] for n in r.notify] == ["daily_evening 완료"]
    assert "WISE" not in r.notify[0]


def test_kiwoom_failure_is_crit_without_wise(tmp_path: Path) -> None:
    """갈래 실패는 종전대로 crit(FAILED) — 실패 갈래 목록·인계 파일에 WISE 가 없다."""
    r = _run(tmp_path, kw_rc=1)
    assert r.rc == 2, r.out + r.log
    assert [n.split("|")[1] for n in r.by_level("crit")] == ["daily_evening 실패: 키움(rc=1)"]
    assert r.deliver is not None and r.deliver["kiwoom_rc"] == 1
    assert not [k for k in r.deliver if "wise" in k]


# ── 휴장 파일 내보내기 스위치(T-48 · QL-Q2) — 켜져 있으면 v3 사본 동기화(병행 대조)를 건너뛴다 ─────────
def _synced(home: Path) -> str:
    f = home / "quant-ledger" / "sync.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


@pytest.mark.parametrize("conf", [None, "CALENDAR_EXPORT_V3=yes\n", "CALENDAR_EXPORT_V3=0\n"])
def test_export_switch_off_keeps_the_v3_calendar_sync(tmp_path: Path, conf: str | None) -> None:
    """꺼짐(파일 없음·다른 값) — 지금처럼 v3 사본 동기화를 부르고 건너뜀 줄이 없다."""
    r = _run(tmp_path, dry=True, conf=conf)
    assert r.rc == 0, r.out + r.log
    assert _synced(tmp_path) == "sync\n"
    assert "동기화 건너뜀" not in r.log


def test_export_switch_on_skips_the_v3_calendar_sync(tmp_path: Path) -> None:
    """켜짐 — v3 휴장 파일이 06:00 내보내기 산출이라 v3 사본 대조는 자기 사본 대조다(T-48). 동기화를 부르지 않고
    이유 한 줄을 남긴다. 저녁 슬롯은 내보내기를 부르지 않는다(06:00 체인 몫)."""
    r = _run(tmp_path, dry=True, conf="CALENDAR_EXPORT_V3=1\n")
    assert r.rc == 0, r.out + r.log
    assert _synced(tmp_path) == ""
    assert "  v3 휴장 사본 동기화 건너뜀 — CALENDAR_EXPORT_V3=1" in r.log
    calls = tmp_path / "quant-ledger" / "calls.txt"
    assert "daily.calendar_export" not in (calls.read_text(encoding="utf-8") if calls.exists() else "")
