"""scripts/watchdog.sh morning_build — 확정 빌드가 정상이면 그 D 의 엑셀 발송 장부 줄까지 본다.

배포 묶음 5-1(B-57 · N-31 ① · P1). HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 진짜 watchdog.sh 를
두고 돌린다(test_daily_build_sh 와 같은 방식). 판정 heredoc 은 진짜 파이썬(이 테스트를 돌리는
인터프리터)이 실행하고, `scripts/notify.sh` 는 "등급|제목|본문" 을 notify.txt 에 적는 대역이다 —
운영 notify.log·원장·서버 경로를 건드리지 않는다.
거래일 달력(`daily.calendar`)은 임시 `src/` 의 대역이다: 오늘은 거래일, 직전 거래일은 환경변수
WD_PREV 로 고정한다 — 실행 날짜와 무관하게 확정판 D 가 정해진다(휴장·토요일 규칙은 진짜
달력의 몫이고 여기서는 워치독이 그 D 로 장부를 찾는지만 본다).
픽스처: 건전성 리포트 `logs/health/<D>.json`(mtime = D+1 09:30 KST), 확정판 인계
`data/deliver/latest_morning.json`, 발송 장부 `data/deliver/sent_model_daily.jsonl`.
원천 전환 뒤(컷오버 PR-9 — `config/postclose_chain.env` 의 POSTCLOSE_ENABLED=1·POSTCLOSE_SEND=1, 판정 조각
`scripts/postclose_conf.sh` 는 진짜를 복사)는 장 마감 발송 장부 `data/model_db/deliver/sent_model_daily.jsonl` 도 본다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "watchdog.sh"
D = "20261007"
D_ISO = "2026-10-07"
KST = dt.timezone(dt.timedelta(hours=9))
# crit 제목 — 확정 빌드 실패와 발송 경보를 제목만 보고도 가른다
TITLE_BUILD = "watchdog: 10:30 까지 확정 빌드 보고 없음/실패"
TITLE_NOT_SENT = "watchdog: 10:30 까지 확정판 엑셀 발송 기록 없음"
TITLE_UNKNOWN = "watchdog: 확정판 엑셀 발송 여부 판정 불가"
MORNING_LEDGER = "data/deliver/sent_model_daily.jsonl"
EVENING_LEDGER = "data/model_db/deliver/sent_model_daily.jsonl"
LIVE_CONF = "POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=1\nPOSTCLOSE_V3=in-place\n"
DIR = "<dir>"     # 장부 자리에 디렉터리를 둔다(열기 실패)

_CALENDAR = """import datetime as dt
import os


class _Cal:
    def is_trading_day(self, d):
        return True

    def prev_trading_day(self, d, n=1):
        return dt.date.fromisoformat(os.environ["WD_PREV"])


def load():
    return _Cal()
"""


class Run(NamedTuple):
    rc: int
    out: str
    notify: list[str]   # 줄마다 "등급|제목|본문"


def _write(path: Path, body: str | bytes | None) -> None:
    """장부 픽스처 — None 이면 파일 없음, DIR 이면 디렉터리."""
    if body == DIR:
        path.mkdir()
    elif isinstance(body, bytes):
        path.write_bytes(body)
    elif body is not None:
        path.write_text(body, encoding="utf-8")


def _root(home: Path, *, ledger: str | bytes | None, morning_ok: bool = True,
          conf: str | None = None, evening: str | bytes | None = None) -> Path:
    """ledger = 아침 발송 장부, evening = 장 마감 발송 장부, conf = config/postclose_chain.env(None 이면 파일 없음)."""
    root = home / "quant-ledger"
    for sub in ("scripts", "logs/health", ".venv/bin", "src/daily", "data/deliver", "config",
                "data/model_db/deliver"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "watchdog.sh")
    shutil.copy(SCRIPT.parent / "postclose_conf.sh", root / "scripts" / "postclose_conf.sh")
    if conf is not None:
        (root / "config/postclose_chain.env").write_text(conf, encoding="utf-8")
    stubs = {".venv/bin/python": f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n',
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n'}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    (root / "src/daily/__init__.py").write_text("", encoding="utf-8")
    (root / "src/daily/calendar.py").write_text(_CALENDAR, encoding="utf-8")
    health = root / "logs/health" / f"{D}.json"
    health.write_text(json.dumps({"ok": True, "checks": []}), encoding="utf-8")
    built = dt.datetime(2026, 10, 8, 9, 30, tzinfo=KST).timestamp()   # D+1 08:00 KST 이후
    os.utime(health, (built, built))
    lat = {"date": D, "generated_at": "2026-10-08T00:30:00Z",
           "health": {"stage": "ok", "equity": "ok" if morning_ok else "fail"},
           "stage_builds": {"s": {}}, "equity_builds": {"e": {}}}
    (root / "data/deliver/latest_morning.json").write_text(json.dumps(lat), encoding="utf-8")
    _write(root / MORNING_LEDGER, ledger)
    _write(root / EVENING_LEDGER, evening)
    return root


def _line(date: str, basis: str = "morning", correction: int = 0) -> str:
    return json.dumps({"date": date, "basis": basis, "build_id": "m_x", "sha256": "0" * 64,
                       "sent_utc": "2026-10-07T23:31:00Z", "correction": correction}) + "\n"


def _run(home: Path) -> Run:
    root = home / "quant-ledger"
    env = dict(os.environ, HOME=str(home), WD_PREV=D_ISO)
    p = subprocess.run(["bash", str(root / "scripts" / "watchdog.sh"), "morning_build"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    note = root / "notify.txt"
    lines = note.read_text(encoding="utf-8").splitlines() if note.exists() else []
    return Run(p.returncode, p.stdout + p.stderr, lines)


@pytest.mark.parametrize("ledger", [
    None,                                               # 장부 파일 없음(첫 자동 발송 전)
    _line("2026-10-06"),                                # 전날 D 만 있음
    _line(D_ISO, basis="evening"),                      # 같은 D 라도 잠정판(evening) 발송은 아니다
    "",                                                 # 빈 파일
    "\n\n",                                             # 빈 줄만
], ids=["no_ledger", "other_day", "evening_only", "empty_file", "blank_lines"])
def test_missing_send_record_is_crit(tmp_path: Path, ledger: str | None) -> None:
    """G1 — 확정판은 정상인데 장부에 그 D(basis=morning) 줄이 없으면 crit 1건.

    옛 코드는 확정 빌드 보고만 보고 '정상' info 를 남겼다 — 모델 단계·발송이 실패해도
    엑셀이 오지 않은 것으로만 드러났다(B-57).
    """
    _root(tmp_path, ledger=ledger)
    r = _run(tmp_path)
    assert r.rc == 2, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert (level, title) == ("crit", TITLE_NOT_SENT)
    assert f"확정판 D={D} 엑셀 발송 기록 없음" in body
    assert f"scripts/model_daily.sh --date {D} 로 손 발송(사용자 승인 뒤)" in body
    # 발송 뒤 장부 기록만 실패했을 수 있다 — 손 발송 전에 notify.log 를 보라는 안내(B-58)
    assert "이미 보냈을 수 있다(B-58)" in body
    assert ("파일 없음" in body) == (ledger is None)


@pytest.mark.parametrize("correction", [0, 1])
def test_send_record_present_is_ok(tmp_path: Path, correction: int) -> None:
    """회귀 가드 — 장부에 그 D 의 morning 줄이 있으면 지금처럼 정상 info 1건, 정정 번호를 싣는다.

    정정 발송(--resend)이 있으면 마지막 줄의 correction 을 보인다.
    """
    ledger = _line("2026-10-06") + _line(D_ISO) + (_line(D_ISO, correction=1) if correction else "")
    _root(tmp_path, ledger=ledger)
    r = _run(tmp_path)
    assert r.rc == 0, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert level == "info" and title.startswith("watchdog morning_build 정상"), r.notify
    assert f"D={D} 원장 건전성 OK" in body
    assert f"발송 기록 있음(정정 {correction})" in body


def test_broken_build_reports_only_the_build_crit(tmp_path: Path) -> None:
    """회귀 가드 — 확정 빌드가 비정상이면 그 crit 하나만, 발송 검사는 겹쳐 내지 않는다."""
    _root(tmp_path, ledger=None, morning_ok=False)
    r = _run(tmp_path)
    assert r.rc == 2, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert (level, title) == ("crit", TITLE_BUILD)
    assert "확정판 건전성 실패 equity" in body
    assert "발송" not in body


@pytest.mark.parametrize("ledger", [
    _line(D_ISO) + "{not json\n",                       # 줄이 JSON 이 아님
    _line(D_ISO) + "[1, 2]\n",                          # JSON 이지만 객체가 아님
    b"\xff\xfe" + _line(D_ISO).encode(),                # UTF-8 로 읽을 수 없음
], ids=["not_json", "not_object", "not_utf8"])
def test_unreadable_ledger_is_crit(tmp_path: Path, ledger: str | bytes) -> None:
    """P1 — 장부를 못 읽으면 보냈는지 모르므로 crit(D 줄이 섞여 있어도 정상으로 넘기지 않는다)."""
    _root(tmp_path, ledger=ledger)
    r = _run(tmp_path)
    assert r.rc == 2, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert (level, title) == ("crit", TITLE_UNKNOWN)
    assert "sent_model_daily.jsonl" in body and "발송 여부 판정 불가" in body


# ── 원천 전환 뒤(컷오버 PR-9 · T-7) — D 의 발송 기록 = 장 마감 장부 evening 줄 또는 아침 장부 morning 줄 ──────────
@pytest.mark.parametrize("conf", [
    None,
    "POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=0\n",               # 그림자 · 되돌리기 4-1
    "POSTCLOSE_ENABLED=0\nPOSTCLOSE_SEND=1\n",
    "POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=yes\n",
], ids=["no_file", "shadow", "disabled", "send_yes"])
def test_before_cutover_evening_ledger_does_not_count(tmp_path: Path, conf: str | None) -> None:
    """회귀 — 전환 전은 지금 그대로 아침 장부만 본다. 장 마감 장부에 그 D 줄이 있어도 '발송 기록 없음' crit 이고
    문구도 종전 그대로(장 마감 장부 경로를 싣지 않는다)."""
    _root(tmp_path, ledger=None, conf=conf, evening=_line(D_ISO, basis="evening"))
    r = _run(tmp_path)
    assert r.rc == 2, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert (level, title) == ("crit", TITLE_NOT_SENT)
    assert EVENING_LEDGER not in body


@pytest.mark.parametrize(("ledger", "evening", "seen"), [
    (None, _line(D_ISO, basis="evening"), "장 마감 판"),          # 정상 — 장 마감 체인 ⑤ 가 보냈다
    (_line(D_ISO), None, "아침 대체 발송"),                       # 장 마감 판 실패 → 다음 날 아침판 대체 발송
    (_line(D_ISO), _line(D_ISO, basis="evening"), "장 마감 판"),  # 둘 다(손 재발송 등)
], ids=["evening_only", "morning_only", "both"])
def test_after_cutover_either_ledger_counts_as_sent(tmp_path: Path, ledger: str | None,
                                                     evening: str | None, seen: str) -> None:
    """전환 뒤 아침판은 짓기만이라 아침 장부엔 보통 줄이 없다 — 장 마감 장부 basis=evening 줄 또는 아침 장부
    basis=morning 줄이면 정상 info 1건(어느 쪽인지 싣는다)."""
    _root(tmp_path, ledger=ledger, conf=LIVE_CONF, evening=evening)
    r = _run(tmp_path)
    assert r.rc == 0, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert level == "info" and title.startswith("watchdog morning_build 정상"), r.notify
    assert "발송 기록 있음(" in body and seen in body


@pytest.mark.parametrize(("ledger", "evening"), [
    (None, None),                                                    # 두 장부 다 없음
    (_line("2026-10-06"), _line("2026-10-06", basis="evening")),     # 전날 D 만
    (_line(D_ISO, basis="evening"), _line(D_ISO)),                   # 장부와 basis 가 엇갈린 줄은 세지 않는다
], ids=["no_files", "other_day", "crossed_basis"])
def test_after_cutover_no_record_in_either_ledger_is_crit(tmp_path: Path, ledger: str | None,
                                                          evening: str | None) -> None:
    """전환 뒤 두 장부 어디에도 그 D 줄이 없으면 지금처럼 '발송 기록 없음' crit — 본문에 두 장부 경로."""
    _root(tmp_path, ledger=ledger, conf=LIVE_CONF, evening=evening)
    r = _run(tmp_path)
    assert r.rc == 2, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert (level, title) == ("crit", TITLE_NOT_SENT)
    assert f"확정판 D={D} 엑셀 발송 기록 없음" in body
    assert MORNING_LEDGER in body and EVENING_LEDGER in body
    assert f"scripts/model_daily.sh --date {D}" in body


@pytest.mark.parametrize(("ledger", "evening", "bad"), [
    (_line(D_ISO), "{not json\n", EVENING_LEDGER),                   # 장 마감 장부 손상 — 아침 줄이 있어도
    (_line(D_ISO), DIR, EVENING_LEDGER),                             # 장 마감 장부 자리가 디렉터리
    ("{not json\n", _line(D_ISO, basis="evening"), MORNING_LEDGER),  # 아침 장부 손상 — 장 마감 줄이 있어도
], ids=["evening_not_json", "evening_dir", "morning_not_json"])
def test_after_cutover_unreadable_either_ledger_is_crit(tmp_path: Path, ledger: str,
                                                        evening: str, bad: str) -> None:
    """P1 — 전환 뒤 어느 장부든 못 읽으면 판정 불가 crit(다른 장부에 그 D 줄이 있어도 정상으로 넘기지 않는다 —
    손상된 장부는 다음 발송·대체 발송을 막는다)."""
    _root(tmp_path, ledger=ledger, conf=LIVE_CONF, evening=evening)
    r = _run(tmp_path)
    assert r.rc == 2, r.out
    assert len(r.notify) == 1, r.notify
    level, title, body = r.notify[0].split("|", 2)
    assert (level, title) == ("crit", TITLE_UNKNOWN)
    assert bad in body and "발송 여부 판정 불가" in body
