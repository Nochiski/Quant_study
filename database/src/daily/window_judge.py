"""연속 창 판정 집계 — 그림자·실운영 3거래일 창과 되돌리기 5거래일 창(컷오버 트랙 X-2).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 X-2 · §4(N-42 Q1) · 로드맵 `2026-10-05-roadmap.md` §8 공통 3.
새 기준을 만들지 않는다 — 아래 규칙은 정본 문구를 옮긴 것이다.

하루(거래일 T) 통과 = 넷 다 참.
  ① 그날 장 마감 체인 단계 전부 ok — 런 로그 `data/raw/daily_run.db` 에서 date=T 인 `CHAIN_SOURCES` 런이 모두
     status 'ok'. 같은 단계를 다시 돌려 뒤 런이 ok 여도 앞 런 실패는 실패다. 수집기의 cutoff·late 도 'ok' 가 아니다.
  ② 그날 날짜의 crit 0 — `logs/notify.log`(`scripts/notify.sh` 가 남김)의 crit 줄 가운데 **점수에 영향을 주는 경로의
     crit 만**(사용자 10-10 결정 — 제목 접두어 `COUNTED_CRIT_PREFIXES`). 줄 시각은 UTC 라 KST 날짜로 바꿔 T 와
     맞춘다. 목록 밖 crit(DART·KIS·WISE·키움 저녁 등 수집 단계, 21:20 잠정판 빌드와 그 워치독, 요약 줄인 일일
     리포트, 모르는 새 제목)은 세지 않고 '판정 밖 crit' 으로 보이기만 한다. warn 줄은 보고 대상으로 싣는다.
  ③ 수동 개입 0 — 장부 `data/cutover/manual_interventions.jsonl`(한 줄 = {date, what, by, recorded_at})의
     date=T 항목. 기록은 `record` 하위 명령.
  ④ 다음 날 두 판 대조(PR-7 `daily.board_compare`) 통과 — `data/model_db/compare/<T>.json` 의 verdict pass·rc 0.
     rc 1(미설명·Spearman 하한 미달)·rc 2(입력 오류 — 배포가 끼어 규칙 판본 불일치 등)는 실패이고 사유를 그대로 싣는다.

미판정: 실패 근거가 없는데 체인 런이나 대조 결과가 아직 없는 날. 그 뒤 거래일의 체인 런(대조는 뒤 거래일 대조
결과)이 이미 있으면 '아직'이 아니라 미실행이라 실패다(공통 3 — 못 쟀거나 건너뛰었으면 통과가 아니다).
판정 불가: 대조 결과 파일을 못 읽거나 모양이 다르다 — 그날은 통과로 세지 않고 도구 rc 2.
휴장일·세션 예외일(T-26)은 창에 넣지 않는다 — 실패로도 통과로도 세지 않는다. 거래일은 `daily.calendar`(판정 달력·
세션 예외표)로 센다. 건너뛴 날(주말 포함)의 crit·warn·수동 개입은 직전 거래일(창에 넣는 날)에 귀속한다(T-39 —
주말 06:00 체인이 처리하는 D 는 직전 거래일이고, 모호하면 실패 쪽 P1). 사유에 '귀속: <원래 날짜> → <거래일>' 을
붙인다. 직전 거래일이 판정 범위 앞이면(창 시작이 주말 등) 버리지 않고 '판정 밖 기록'으로 보인다.

3일 창: --start 부터 거래일 순서로 센다. 실패 1건이면 그 다음 거래일부터 다시 센다(공통 3). 마지막 실패 뒤 연속
통과가 3거래일 이상이면 통과. 미판정·판정 불가인 날에서 연속은 멈춘다 — 그날을 건너 이어 세지 않는다.
되돌리기 창: --cutover 부터 5거래일(세션 예외일도 거래일로 센다 — v3 가 최근 5행을 다시 받는 단위가 거래일이다).
경과 = 기준일(--as-of)이 다섯째 날 뒤. 그 사이 날마다 같은 하루 판정과 실패 목록을 낸다.

입력은 전부 읽기 전용이다(런 로그는 mode=ro). 쓰는 것은 `data/cutover/window.json`(세션 시작 보고·아티팩트 갱신이
읽는다, --dry-run 이면 안 씀)과 `record` 의 장부 덧붙이기뿐이다. 알림(notify.sh)은 내지 않는다.
rc: 0 창 통과 · 1 아직·실패 · 2 입력 오류(달력·notify.log·런 로그·장부·대조 결과를 못 읽거나 모양이 다름 —
window.json 에 verdict error 로 남긴다).

사용:
  PYTHONPATH=src python -m daily.window_judge judge --start YYYYMMDD [--cutover YYYYMMDD] [--as-of YYYYMMDD]
                                                    [--home DIR] [--dry-run]
  PYTHONPATH=src python -m daily.window_judge record --date YYYYMMDD --what 무엇을 --by 누가 [--home DIR]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import traceback
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from daily import calendar as cal_mod
from daily import calendar_refresh as cr
from daily.runlog import Run

TOOL = "daily.window_judge"
SCHEMA = 1                      # window.json 모양 판본 — 읽는 쪽(세션 시작 보고·아티팩트)이 본다. 키를 바꾸면 올린다
REQUIRED_DAYS = 3               # 정본 §4 '실운영 3거래일'
ROLLBACK_DAYS = 5               # 정본 §4 '되돌리기 창 5거래일'
# 장 마감 체인 단계 런 로그 source, 체인 순서 — ① 수집기(`daily.postclose.SOURCE`) → ②~⑥ PR-8
# `scripts/postclose_chain.sh close` 가 남기는 단계. PR-8 머지 때 `runlog.POSTCLOSE_STEPS` 를 읽게 바꾼다
# (그때까지 테스트가 두 이름을 대조한다)
CHAIN_SOURCES: tuple[str, ...] = ("kiwoom_postclose", "postclose_stage", "postclose_fi",
                                  "postclose_model", "postclose_excel", "postclose_v3")
# 두 판 대조 결과 모양(PR-7 `daily.board_compare` SCHEMA·TOOL — T-36 반영판 schema 2 부터 `replay` 키) — 판본이
# 바뀌면 판정 불가(rc 2)로 멈춘다. PR-7 머지 뒤에는 테스트가 두 상수를 대조한다
COMPARE_TOOL = "daily.board_compare"
COMPARE_SCHEMA = 2
_COMPARE_RC = {"pass": 0, "fail": 1, "error": 2}

NOTIFY_LOG = Path("logs/notify.log")
RUN_DB = Path("data/raw/daily_run.db")
COMPARE_DIR = Path("data/model_db/compare")
CAL_DIR = Path("data/calendar")
LEDGER = Path("data/cutover/manual_interventions.jsonl")
WINDOW_JSON = Path("data/cutover/window.json")

KST = cr.KST
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"    # notify.sh `date -u +%FT%TZ` · 장부 recorded_at
_NOTIFY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (crit|warn|info) (.*)$")
# 세는 crit 제목(접두어) — 점수에 영향을 주는 경로만(사용자 10-10 결정). 제목은 각 스크립트의
# `scripts/notify.sh crit "<제목>"` 호출부에서 옮겼다. 목록 밖 crit 은 세지 않고 '판정 밖 crit' 으로 보인다 — 모르는
# 새 제목을 사람이 보게 한다. 수집 단계(daily_evening·daily_ledger·daily_master·WICS) 결손이 점수에 닿으면 아래
# 아침 빌드·장 마감 판 실패로 잡힌다. 일일 리포트는 요약 줄이라 개별 원인과 이중으로 세므로 넣지 않는다
COUNTED_CRIT_PREFIXES: tuple[str, ...] = (
    # 장 마감 체인 — scripts/postclose_chain.sh close · refill · morning(PR-8)
    "장 마감 체인 실패", "장 마감 재반영 실패", "장 마감 판 아침 잇기 실패",
    # 연구 아침 빌드 — daily_build.sh(실패·중단·원장 락 날짜 바뀜) · build_morning.sh · build_chain.sh(확정판) ·
    # model_daily.sh(아침 fi·모델·엑셀)
    "daily_build ", "확정 빌드 시작 불가", "확정판 빌드 실패", "모델 단계 실패",
    # v3 반영 — scripts/v3_post.sh
    "v3_post ",
    # 워치독 — watchdog.sh postclose_board · morning_build(확정 빌드 · 확정판 엑셀 발송 장부)
    "watchdog: 16:30 까지 장 마감 판", "watchdog: 10:30 까지 확정", "watchdog: 확정판 엑셀 발송",
)
_YMD_RE = re.compile(r"^\d{8}$")
_WEEKDAY = "월화수목금토일"
SAMPLE_N = 5                    # 형식 밖 notify 줄 표본 수


def counted_crit(title: str) -> bool:
    """이 crit 제목이 '그날 crit 0' 에 드는가 — `COUNTED_CRIT_PREFIXES` 접두어."""
    return title.startswith(COUNTED_CRIT_PREFIXES)


class InputError(RuntimeError):
    """입력을 못 읽었거나 모양이 다르다 — 판정하지 않는다(rc 2). 메시지에 경로를 싣는다."""


def parse_date(s: str) -> dt.date:
    """YYYYMMDD → date. 형식이 틀리거나 없는 날이면 ValueError."""
    if not _YMD_RE.match(s):
        raise ValueError(f"날짜는 YYYYMMDD 여야 한다: {s!r}")
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _key(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _label(d: dt.date) -> str:
    return f"{d:%m-%d}({_WEEKDAY[d.weekday()]})"


# ── 입력 읽기 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Note:
    """notify.log 의 crit·warn 한 줄."""

    level: str
    at_utc: str
    title: str

    def text(self) -> str:
        stamp = dt.datetime.strptime(self.at_utc, TS_FORMAT).replace(tzinfo=dt.UTC)
        return f"{self.level} {stamp.astimezone(KST):%m-%d %H:%M} KST {self.title}"


def read_notify(path: Path) -> tuple[dict[str, list[Note]], list[str]]:
    """crit·warn 줄을 판정 날짜(YYYYMMDD)별로 모은다. info 는 버린다. 형식 밖 줄은 따로 돌려준다.

    판정 날짜 = 줄 시각(UTC)의 KST 날짜. 파일이 없으면 InputError — 'crit 0' 과 '못 읽음'은 다르다(공통 3).
    """
    if not path.is_file():
        raise InputError(f"notify.log 없음: {path} — 그날 crit 0 을 확인할 수 없다")
    by_date: dict[str, list[Note]] = defaultdict(list)
    unparsed: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                m = _NOTIFY_RE.match(line)
                try:
                    stamp = dt.datetime.strptime(m.group(1), TS_FORMAT).replace(tzinfo=dt.UTC) if m else None
                except ValueError:
                    stamp = None
                if m is None or stamp is None:
                    unparsed.append(line[:200])
                    continue
                at, level, rest = m.groups()
                if level == "info":
                    continue
                title = rest.split(" | ", 1)[0]
                by_date[stamp.astimezone(KST).strftime("%Y%m%d")].append(Note(level, at, title))
    except OSError as e:
        raise InputError(f"notify.log 읽기 실패: {path} {type(e).__name__}: {e}") from e
    return by_date, unparsed


def read_chain_runs(path: Path) -> dict[str, list[Run]]:
    """장 마감 체인 단계 런을 date(YYYYMMDD)별로, run_id 순. 파일·`run` 표가 없으면 InputError."""
    if not path.is_file():
        raise InputError(f"런 로그 daily_run.db 없음: {path}")
    marks = ",".join("?" * len(CHAIN_SOURCES))
    try:
        # 절대 경로 file: URI 로 연다 — 경로에 # · ? 가 있어도 mode=ro 가 잘리지 않는다(runlog 와 같은 방식)
        con = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
        try:
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'").fetchone() is None:
                raise InputError(f"런 로그에 run 표 없음: {path}")
            rows = con.execute(
                "SELECT run_id,date,source,started,ended,n_calls,n_rows,status,detail FROM run "
                f"WHERE source IN ({marks}) ORDER BY run_id", CHAIN_SOURCES).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise InputError(f"런 로그 daily_run.db 읽기 실패: {path} {type(e).__name__}: {e}") from e
    out: dict[str, list[Run]] = defaultdict(list)
    for r in rows:
        out[str(r[1])].append(Run(*r))
    return out


def read_ledger(path: Path) -> dict[str, list[dict[str, str]]]:
    """수동 개입 장부를 date 별로. 파일이 없으면 빈 장부(아무도 기록하지 않았다). 깨진 줄이면 InputError —
    어느 날의 개입인지 모르면 그 창을 판정할 수 없다."""
    if not path.exists():
        return {}
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"수동 개입 장부 읽기 실패: {path} {type(e).__name__}: {e}") from e
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise TypeError(f"객체가 아니다: {type(rec).__name__}")
            parse_date(str(rec.get("date", "")))
            for k in ("what", "by"):
                if not isinstance(rec.get(k), str) or not rec[k].strip():
                    raise ValueError(f"{k} 가 비었다")
        except (ValueError, TypeError) as e:
            raise InputError(f"수동 개입 장부 {n}행 형식 오류: {path} {type(e).__name__}: {e} — "
                             f"한 줄 = {{\"date\": YYYYMMDD, \"what\": …, \"by\": …}}") from e
        out[rec["date"]].append(rec)
    return out


def record(path: Path, *, date: str, what: str, by: str, now: dt.datetime | None = None) -> dict[str, str]:
    """장부에 한 줄 덧붙인다(고치거나 지우지 않는다). 날짜·빈 값이 틀리면 ValueError — 아무것도 쓰지 않는다."""
    parse_date(date)
    if not what.strip() or not by.strip():
        raise ValueError(f"what·by 는 비울 수 없다: what={what!r} by={by!r}")
    stamp = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).strftime(TS_FORMAT)
    rec = {"date": date, "what": what.strip(), "by": by.strip(), "recorded_at": stamp}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return rec


def read_compare(path: Path, t: dt.date) -> dict[str, object] | None:
    """`compare/<T>.json` — 없으면 None. 못 읽거나 PR-7 모양(schema·tool·date·verdict↔rc 짝)이 아니거나 재생
    결과면 InputError."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"두 판 대조 결과 읽기 실패: {path} {type(e).__name__}: {e}") from e
    if not isinstance(payload, dict):
        raise InputError(f"두 판 대조 결과가 객체가 아니다: {path}")
    bad: list[str] = []
    if payload.get("schema") != COMPARE_SCHEMA:
        bad.append(f"schema={payload.get('schema')!r}(기대 {COMPARE_SCHEMA})")
    if payload.get("tool") != COMPARE_TOOL:
        bad.append(f"tool={payload.get('tool')!r}")
    if payload.get("date") != t.isoformat():
        bad.append(f"date={payload.get('date')!r}(기대 {t.isoformat()})")
    verdict, rc = payload.get("verdict"), payload.get("rc")
    if verdict not in _COMPARE_RC or type(rc) is not int or rc != _COMPARE_RC[str(verdict)]:
        bad.append(f"verdict={verdict!r}·rc={rc!r} 짝이 아니다")
    elif verdict == "pass" and (type(payload.get("n_unexplained")) is not int
                                or payload.get("n_unexplained") != 0):
        bad.append(f"verdict pass 인데 n_unexplained={payload.get('n_unexplained')!r}")
    elif verdict == "fail" and not _str_list(payload.get("reasons")):
        bad.append(f"verdict fail 인데 reasons={payload.get('reasons')!r}")
    elif verdict == "error" and not (isinstance(payload.get("error"), str) and payload.get("error")):
        bad.append("verdict error 인데 error 사유가 없다")
    if payload.get("replay") not in (None, False):
        # 재생(--replay)은 T 행이 21:05 원장이라 종가 정의를 달리 센다 — 실운영 창의 증거가 아니다
        bad.append(f"재생 대조 결과(replay={payload.get('replay')!r}) — 실운영 판정에 쓰지 않는다")
    if bad:
        raise InputError(f"두 판 대조 결과 모양이 다르다: {path} — {'; '.join(bad)}")
    return payload


def _str_list(v: object) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) for x in v)


def _compare_dates(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if _YMD_RE.match(p.stem))


# ── 달력 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Cal:
    cal: cal_mod.Calendar
    sessions: dict[str, str]

    def trading(self, d: dt.date) -> bool:
        try:
            return self.cal.is_trading_day(d)
        except KeyError as e:
            raise InputError(f"판정 달력이 {d.year} 년을 덮지 않는다: {e}") from e

    def skip_reason(self, d: dt.date) -> str | None:
        """창에서 건너뛰는 날의 사유(평일 휴장·세션 예외). 주말은 None — 목록에 싣지 않는다."""
        if not self.trading(d):
            return "휴장" if d.weekday() < 5 else None
        reason = self.sessions.get(_key(d))
        return f"세션 예외({reason})" if reason else None

    def counted(self, d: dt.date) -> bool:
        """3일 창에 넣는 날 = 거래일이고 세션 예외일이 아님."""
        return self.trading(d) and _key(d) not in self.sessions

    def next_counted(self, d: dt.date) -> dt.date:
        cur = d + dt.timedelta(days=1)
        while not self.counted(cur):
            cur += dt.timedelta(days=1)
        return cur

    def first_trading(self, d: dt.date, n: int) -> list[dt.date]:
        """d 부터(d 포함) 거래일 n 개 — 세션 예외일도 거래일로 센다."""
        out, cur = [], d
        while len(out) < n:
            if self.trading(cur):
                out.append(cur)
            cur += dt.timedelta(days=1)
        return out


def _load_cal(directory: Path) -> _Cal:
    try:
        return _Cal(cal_mod.load(directory), cal_mod.load_session_exceptions(directory))
    except cal_mod.CalendarUnavailable as e:
        raise InputError(f"판정 달력 못 읽음: {e}") from e


# ── 하루 판정 ────────────────────────────────────────────────────────────────
@dataclass
class Day:
    date: dt.date
    skip: str | None = None
    fails: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)    # 판정 밖 crit — 세지 않고 보이기만(목록 밖 제목)

    @property
    def key(self) -> str:
        return _key(self.date)

    @property
    def status(self) -> str:
        if self.skip:
            return "skip"
        if self.fails:
            return "fail"
        if self.errors:
            return "error"
        if self.pending:
            return "pending"
        return "pass"

    def reasons(self) -> list[str]:
        st = self.status
        return {"skip": [str(self.skip)], "fail": self.fails, "error": self.errors,
                "pending": self.pending, "pass": []}[st]

    def to_dict(self) -> dict[str, object]:
        return {"date": self.key, "weekday": _WEEKDAY[self.date.weekday()], "status": self.status,
                "skip_reason": self.skip, "fails": self.fails, "pending": self.pending,
                "errors": self.errors, "warns": self.warns, "ignored_crit": self.ignored}


def _chain_reasons(runs: list[Run], later: bool) -> tuple[list[str], list[str]]:
    """(실패, 미판정). `later` = 그 뒤 거래일의 체인 런이 이미 있다 — 그러면 빈 단계·running 은 미실행이다."""
    fails = [f"장 마감 체인 {r.source} {r.status}(run_id {r.run_id})"
             for r in runs if r.status not in ("ok", "running")]
    if fails:
        return fails, []        # 앞 단계 실패면 뒤 단계가 없는 것은 당연하다 — 따로 적지 않는다
    pending: list[str] = []
    seen = {r.source for r in runs}
    missing = [s for s in CHAIN_SOURCES if s not in seen]
    running = [f"{r.source}(run_id {r.run_id})" for r in runs if r.status == "running"]
    tail = " — 뒤 거래일 체인 런이 있어 미실행으로 본다" if later else ""
    if running:
        (fails if later else pending).append(
            ("장 마감 체인 running 으로 남음 " if later else "장 마감 체인 실행 중 ") + ", ".join(running) + tail)
    if missing:
        what = ("장 마감 체인 런 없음" if len(missing) == len(CHAIN_SOURCES)
                else f"장 마감 체인 단계 런 없음 {', '.join(missing)}")
        (fails if later else pending).append(what + tail)
    return fails, pending


def _add_records(day: Day, notes: list[Note], ledger: list[dict[str, str]], tail: str = "") -> None:
    """세는 crit·수동 개입은 실패, 목록 밖 crit 은 판정 밖, warn 은 경고로 싣는다. `tail` = 건너뛴 날에서 귀속한
    표시(T-39)."""
    for n in notes:
        if n.level != "crit":
            day.warns.append(n.text() + tail)
        else:
            (day.fails if counted_crit(n.title) else day.ignored).append(n.text() + tail)
    for m in ledger:
        day.fails.append(f"수동 개입: {m['what']} ({m['by']})" + tail)


def _judge_day(d: dt.date, *, runs: dict[str, list[Run]], last_run: str, notes: dict[str, list[Note]],
               ledger: dict[str, list[dict[str, str]]], compare_dir: Path, last_compare: str) -> Day:
    key = _key(d)
    day = Day(d)
    day.fails, day.pending = _chain_reasons(runs.get(key, []), later=last_run > key)
    chain_failed = bool(day.fails)
    _add_records(day, notes.get(key, []), ledger.get(key, []))
    path = compare_dir / f"{key}.json"
    try:
        cmp = read_compare(path, d)
    except InputError as e:
        day.errors.append(str(e))
        return day
    if cmp is None:
        if chain_failed:
            pass                # 모델 판이 없으면 대조는 돌지 않는다(PR-8) — 체인 실패로 이미 실패
        elif last_compare > key:
            day.fails.append(f"두 판 대조 결과 없음({path.name}) — 뒤 거래일 대조가 있어 미실행으로 본다")
        else:
            day.pending.append(f"두 판 대조 결과 아직 없음({COMPARE_DIR}/{path.name})")
    elif cmp["verdict"] == "fail":
        reasons = cmp["reasons"]
        text = "; ".join(str(x) for x in reasons) if isinstance(reasons, list) else str(reasons)
        day.fails.append(f"두 판 대조 불일치(rc 1): {text}")
    elif cmp["verdict"] == "error":
        day.fails.append(f"두 판 대조 입력 오류(rc 2): {cmp['error']}")
    return day


# ── 창 판정 ──────────────────────────────────────────────────────────────────
@dataclass
class Result:
    start: dt.date
    as_of: dt.date
    cutover: dt.date | None
    days: list[Day]                         # 3일 창(--start ~ --as-of), 건너뛴 평일 포함
    by_date: dict[dt.date, Day]             # 판정한 모든 날(되돌리기 창 포함)
    rollback_dates: list[dt.date]
    off_window: list[str]                   # 판정 범위 앞 거래일에 귀속될 건너뛴 날 기록(버리지 않고 표시)
    unparsed: list[str]
    restart_from: str | None
    generated_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.UTC).strftime(TS_FORMAT))

    @property
    def counted(self) -> list[Day]:
        return [d for d in self.days if d.status != "skip"]

    @property
    def last_fail(self) -> str | None:
        fails = [d.key for d in self.counted if d.status == "fail"]
        return fails[-1] if fails else None

    @property
    def streak(self) -> list[str]:
        """마지막 실패 뒤 연속 통과 거래일 — 미판정·판정 불가에서 멈춘다."""
        out: list[str] = []
        last_fail = self.last_fail
        for d in self.counted:
            if last_fail is not None and d.key <= last_fail:
                continue
            if d.status != "pass":
                break
            out.append(d.key)
        return out

    @property
    def has_error(self) -> bool:
        return any(d.errors for d in self.by_date.values())

    @property
    def rc(self) -> int:
        if self.has_error:
            return 2
        return 0 if len(self.streak) >= REQUIRED_DAYS else 1

    @property
    def verdict(self) -> str:
        return {0: "pass", 1: "not_yet", 2: "error"}[self.rc]

    def rollback_rows(self) -> list[tuple[str, str, list[str]]]:
        """되돌리기 창 날마다 (날짜, 상태, 사유). 기준일 뒤의 날은 upcoming."""
        rows: list[tuple[str, str, list[str]]] = []
        for d in self.rollback_dates:
            day = self.by_date.get(d)
            if d > self.as_of or day is None:
                rows.append((_key(d), "upcoming", []))
            else:
                rows.append((day.key, day.status, day.reasons()))
        return rows

    def rollback(self) -> dict[str, object] | None:
        if self.cutover is None:
            return None
        rows = self.rollback_rows()
        last = self.rollback_dates[-1]
        return {"cutover": _key(self.cutover), "n_days": ROLLBACK_DAYS,
                "days": [{"date": k, "status": st, "reasons": why} for k, st, why in rows],
                "last_day": _key(last), "n_reached": sum(d <= self.as_of for d in self.rollback_dates),
                "elapsed": self.as_of > last,
                "failures": [{"date": k, "reasons": why} for k, st, why in rows if st == "fail"],
                "pending": [k for k, st, _ in rows if st in ("pending", "error")]}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA, "tool": TOOL, "generated_at": self.generated_at,
            "start": _key(self.start), "as_of": _key(self.as_of),
            "cutover": _key(self.cutover) if self.cutover else None,
            "required_days": REQUIRED_DAYS, "verdict": self.verdict, "rc": self.rc,
            "streak": len(self.streak), "streak_days": self.streak,
            "last_fail": self.last_fail, "restart_from": self.restart_from,
            "days": [d.to_dict() for d in self.days],
            "rollback": self.rollback(),
            "off_window": self.off_window,
            "notify_unparsed": {"count": len(self.unparsed), "samples": self.unparsed[:SAMPLE_N]},
            "inputs": {"notify_log": str(NOTIFY_LOG), "run_db": str(RUN_DB), "compare_dir": str(COMPARE_DIR),
                       "ledger": str(LEDGER), "calendar_dir": str(CAL_DIR)},
        }


def judge(home: Path, start: dt.date, as_of: dt.date, *, cutover: dt.date | None = None) -> Result:
    """`home`(QL_HOME) 아래 입력을 읽어 창을 판정한다. 아무것도 쓰지 않는다. 입력을 못 읽으면 InputError."""
    cal = _load_cal(home / CAL_DIR)
    notes, unparsed = read_notify(home / NOTIFY_LOG)
    runs = read_chain_runs(home / RUN_DB)
    ledger = read_ledger(home / LEDGER)
    compare_dir = home / COMPARE_DIR
    last_run = max(runs, default="")
    last_compare = max(_compare_dates(compare_dir), default="")
    rollback_dates = cal.first_trading(cutover, ROLLBACK_DAYS) if cutover else []
    by_date: dict[dt.date, Day] = {}
    off_window: list[str] = []
    cur = min([start, *rollback_dates[:1]])
    prev: dt.date | None = None             # 직전 거래일(창에 넣는 날) — 건너뛴 날 기록의 귀속처(T-39)
    while cur <= as_of:
        key = _key(cur)
        if cal.counted(cur):
            by_date[cur] = _judge_day(cur, runs=runs, last_run=last_run, notes=notes, ledger=ledger,
                                      compare_dir=compare_dir, last_compare=last_compare)
            prev = cur
        else:
            reason = cal.skip_reason(cur)
            if reason:
                by_date[cur] = Day(cur, skip=reason)
            origin = f"{_label(cur)} {reason or '주말'}"
            if prev is not None:
                _add_records(by_date[prev], notes.get(key, []), ledger.get(key, []),
                             tail=f" (귀속: {origin} → {_label(prev)})")
            else:
                off_window += [f"{origin} · {n.text()}" for n in notes.get(key, [])]
                off_window += [f"{origin} · 수동 개입: {m['what']} ({m['by']})"
                               for m in ledger.get(key, [])]
        cur += dt.timedelta(days=1)
    days = [by_date[d] for d in sorted(by_date) if d >= start]
    fails = [d.date for d in days if d.status == "fail"]
    restart = _key(cal.next_counted(fails[-1])) if fails else None
    return Result(start, as_of, cutover, days, by_date, rollback_dates, off_window, unparsed, restart)


# ── 출력 ─────────────────────────────────────────────────────────────────────
_STATUS_KO = {"pass": "통과", "fail": "실패", "pending": "미판정", "error": "판정 불가", "skip": "건너뜀",
              "upcoming": "아직 안 옴"}
_VERDICT_KO = {"pass": "통과", "not_yet": "아직", "error": "입력 오류"}


def render(res: Result, out: Path | None) -> str:
    lines = [f"연속 창 판정 — 창 시작 {_label(res.start)} · 기준일 {_label(res.as_of)} · "
             f"필요 {REQUIRED_DAYS}거래일 연속"]
    for d in res.days:
        why = " · ".join(d.reasons())
        lines.append(f"  {_label(d.date)} {_STATUS_KO[d.status]}" + (f" — {why}" if why else ""))
        for x in d.ignored:
            lines.append(f"      판정 밖 {x}")          # x = "crit MM-DD HH:MM KST 제목"
        for w in d.warns:
            lines.append(f"      경고 {w}")
    tail = (f" · 마지막 실패 {res.last_fail} → {res.restart_from} 부터 다시 셈" if res.last_fail else "")
    lines.append(f"판정: {_VERDICT_KO[res.verdict]}(rc {res.rc}) · 현재 연속 {len(res.streak)}거래일"
                 + (f"({', '.join(res.streak)})" if res.streak else "") + tail)
    if res.cutover is not None:
        rows = res.rollback_rows()
        last = res.rollback_dates[-1]
        n_reached = sum(d <= res.as_of for d in res.rollback_dates)
        fails = [f"{k}: {' · '.join(why)}" for k, st, why in rows if st == "fail"]
        waits = [k for k, st, _ in rows if st in ("pending", "error")]
        lines.append(f"되돌리기 창: 컷오버 {_label(res.cutover)} · {rows[0][0]}~{_key(last)}"
                     f"({ROLLBACK_DAYS}거래일) · 진행 {n_reached}/{ROLLBACK_DAYS} · "
                     f"{'경과' if res.as_of > last else '경과 전'} · 실패 {len(fails)}"
                     + (f" · 미판정 {', '.join(waits)}" if waits else ""))
        lines += [f"  실패 {f}" for f in fails]
        lines += [f"  {k} 건너뜀 — {' · '.join(why)}" for k, st, why in rows if st == "skip"]
    if res.off_window:
        lines.append("판정 밖 기록(직전 거래일이 판정 범위 앞 — 창 시작이 주말·휴장):")
        lines += [f"  {x}" for x in res.off_window]
    if res.unparsed:
        lines.append(f"notify.log 형식 밖 줄 {len(res.unparsed)} — 날짜·등급을 몰라 판정에 넣지 못함:")
        lines += [f"  {x}" for x in res.unparsed[:SAMPLE_N]]
    lines.append(f"→ {out}" if out else "→ (dry-run — window.json 쓰지 않음)")
    return "\n".join(lines)


def _error_payload(start: str, as_of: str, cutover: str | None, message: str) -> dict[str, object]:
    return {"schema": SCHEMA, "tool": TOOL, "generated_at": dt.datetime.now(dt.UTC).strftime(TS_FORMAT),
            "start": start, "as_of": as_of, "cutover": cutover, "verdict": "error", "rc": 2,
            "error": message}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=TOOL, description="연속 창 판정 집계(컷오버 X-2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge", help="창 판정 — 요약 출력 + data/cutover/window.json")
    j.add_argument("--start", required=True, help="3일 창 시작일 YYYYMMDD(그림자 첫날 · 컷오버일)")
    j.add_argument("--cutover", default=None, help="되돌리기 창 시작 = 컷오버일 YYYYMMDD")
    j.add_argument("--as-of", default=None, help="기준일 YYYYMMDD(기본 오늘 KST)")
    j.add_argument("--dry-run", action="store_true", help="window.json 을 쓰지 않는다(요약만)")
    r = sub.add_parser("record", help="수동 개입 장부에 한 줄 덧붙이기")
    r.add_argument("--date", required=True, help="개입한 날(KST) YYYYMMDD")
    r.add_argument("--what", required=True, help="무엇을 했나")
    r.add_argument("--by", required=True, help="누가(사람·컨트롤러)")
    for p in (j, r):
        p.add_argument("--home", default=None, help="quant-ledger 루트(기본 $QL_HOME)")
    a = ap.parse_args(argv)
    home = Path(a.home or os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])

    if a.cmd == "record":
        try:
            rec = record(home / LEDGER, date=a.date, what=a.what, by=a.by)
        except ValueError as e:
            print(f"{TOOL} record 거부(rc 2): {e} — 장부 무변경 {home / LEDGER}", file=sys.stderr)
            return 2
        print(f"수동 개입 기록 {rec['date']} — {rec['what']} ({rec['by']}) → {home / LEDGER}")
        return 0

    as_of_s = a.as_of or dt.datetime.now(KST).strftime("%Y%m%d")
    out = None if a.dry_run else home / WINDOW_JSON
    try:
        start, as_of = parse_date(a.start), parse_date(as_of_s)
        cutover = parse_date(a.cutover) if a.cutover else None
        res = judge(home, start, as_of, cutover=cutover)
    except (InputError, ValueError) as e:
        msg = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001  # reason: 예상 밖 예외도 '판정 못 함'(rc 2) — rc 1(아직)로 읽히지 않게
        traceback.print_exc()
        msg = f"예상 밖 예외 {type(e).__name__}: {e}"
    else:
        print(render(res, out))
        if out is None:
            return res.rc
        try:
            cr._atomic_write_json(out, res.to_dict())
        except OSError as e:
            # 쓰지 못하면 읽는 쪽이 옛 window.json 을 오늘 판정으로 본다 — rc 2 로 알린다
            print(f"{TOOL} window.json 쓰기 실패(rc 2): {out} {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        return res.rc
    print(f"{TOOL} 입력 오류(rc 2): {msg}", file=sys.stderr)
    if out is not None:
        cr._atomic_write_json(out, _error_payload(a.start, as_of_s, a.cutover, msg))
    return 2


if __name__ == "__main__":
    sys.exit(main())
