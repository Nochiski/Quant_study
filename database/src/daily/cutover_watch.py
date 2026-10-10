"""컷오버 감시 — 컷오버 뒤 v3 의 무거운 퀀트 수집이 0 이고 v3 quant.db 점수 쓰기가 compat 한 곳뿐인지 하루 단위로 본다
(컷오버 트랙 QL-L).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 QL-L · T-16 · T-24 · T-27 · T-31, DECISIONS N-42 Q2,
`docs/COMPAT_LAYER.md` §8-1(V3-A·B·D). 아래 근거 줄 번호는 v3 로컬 사본(25dd56b, 08-30) 기준이다.

그날 D = KST 달력 날짜(자정~자정, 휴장·주말도 본다). v3 의 시각은 전부 UTC 다 — `pipeline_runs.started_at` 은
job_runner 가 `datetime.now(timezone.utc)`(`scripts/job_runner.py:86`), DailyPipeline·증권사 리포트가 sqlite
`datetime('now')`(`backend/pipeline/daily_pipeline.py:282` · `backend/research/brokers/_store.py:31`)로 쓰고, 로그
asctime 은 서버 TZ(Etc/UTC — v3 `docs/system-guide/survey/08-ops.md` §1-1)다. 그래서 창은 UTC [D−1 15:00, D 15:00)
이고 07:00 KST 브리핑(전날 22:00 UTC)도 D 에 든다.

① v3 무거운 수집 0 (N-42 Q2 — 끄는 것은 키움 종목별·KIS·네이버 WiseReport 크롤링)
   근거 둘을 같은 분류표(`JOBS`)로 가른다. 허용 목록 밖 job 은 위반이다 — 분류표에 없는 새 job 도(fail-closed).
   · `pipeline_runs` 의 그날 행 job 이름. v3 에서 이 표에 쓰는 곳은 셋뿐이다 — job_runner(잡 이름·`chain:<체인>`,
     `job_runner.py:121·150`), DailyPipeline 하위 단계(`daily_pipeline.py:121·165`), 증권사 리포트
     (`research_broker_<소스>`, `_store.py:26-35`). 뉴스·DART·이벤트 크론은 이 표에 쓰지 않는다.
   · v3 체인 로그(`~/logs/kael-v3/pipeline.log`)의 그날 줄. job_runner 줄 `<시각> <등급> job=<이름> …`
     (`job_runner.py:15-19·93`)에서 job 이름을, 모든 줄에서 표지를 본다 — 허용 TR(T-27 insight 시장 단위 5종) 밖
     키움 TR id · KIS(도메인·속도 제한 경고 `backend/clients/kis/client.py:98`) · WiseReport. 키움 클라이언트는 실패만
     로그에 남겨(`backend/clients/kiwoom/client.py:108·192·234`) 표지는 보조 근거다. 시각 없는 줄(자식 프로세스
     경고 — `backend.pipeline` 은 로깅 설정이 없어 WARNING 만 시각 없이 나온다)은 바로 앞 시각 줄의 날짜·job 을 따른다.
     연구 리포트 로그(research.log)는 넣지 않는다 — 리포트 수집의 KIS 휴장 1콜(`backend/research/pipeline.py:20-37`)은
     유지 대상이라 표지가 위반으로 잡힌다.
② 점수 쓰기 한 곳 (T-16)
   · v3 스코어링 job(`scoring`·`scoring_v2`)이 그날 돌았으면(`pipeline_runs`·로그) 위반.
   · `score_history`·`_v2` 의 score_date = D 행이 있으면 `_compat_meta` 에 그날 ok 기록 중 그 표를 쓴 기록이 있어야
     하고(마지막 것 — 판 id = `model_builds` 의 그 표 spec), 행 수가 그 기록의 넣은 행 수와 같아야 한다. compat 은 날짜
     단위 교체라 뒤에 다른 쓰기가 끼면 수가 갈린다(v3 스코어링은 1,329·2,526 종목, compat 593·625 — T-17). 같은 수로
     값만 바꾼 쓰기는 이 검사로 못 보고 위 job 검사가 맡는다. 점수 행이 0 이면 위반이 아니다(판 실패일은 다음 날 아침
     재반영이 채운다 — T-38, 그 실패는 장 마감 체인 crit 이 잡는다).
③ v3 크론 (V3-A·B·D) — `crontab -l` 출력(파일, `-` 이면 표준입력). 주석(#)·환경 대입 줄은 꺼진 줄로 본다.
   · `--chain daily_all` 줄이 있으면 위반(V3-B) · `--chain daily_insight` 줄이 없으면 위반(V3-A)
   · v3 휴장 쓰기 `refresh_year_holidays`·`monthly_holiday_review` 줄이 있으면 위반(V3-D, T-24)
   · `daily_post` 는 크론에 두지 않는다 — `scripts/v3_post.sh` 가 `--v3-post-cmd` 로 부른다(COMPAT_LAYER §8-1 V3-A).
     크론 줄 수와 그날 `chain:daily_post` 실행 수는 보이기만 한다.

모드: 기본(감시)은 위반이면 rc 1 + `scripts/notify.sh crit` 한 줄(제목 `TITLE_VIOLATION`). `--baseline`(컷오버 전 그림자
기간 — v3 가 아직 전부 돈다)은 같은 분석을 기록만 하고 rc 0, 알림 없음 — 허용 목록이 실제 v3 job 과 맞는지 실측하는
용도다. 입력 오류(파일·표 없음, 빈 crontab, 모양이 다름)는 rc 2 이고 감시 모드면 crit `TITLE_ERROR`(못 쟀으면 통과가
아니다 — 로드맵 §8 공통 3). 두 제목은 X-2 `window_judge.COUNTED_CRIT_PREFIXES` 에 든다(v3 반영 경로 — T-39).

입력은 전부 읽기 전용이다(v3 quant.db 는 `mode=ro`). 쓰는 것은 `data/cutover/watch_<D>.json` 과 감시 모드의 notify.log
한 줄뿐이다(`--dry-run` 이면 둘 다 안 함).

사용:
  crontab -l | PYTHONPATH=src python -m daily.cutover_watch --v3-db <v3 quant.db> --v3-log <pipeline.log> --crontab -
      [--date YYYYMMDD(기본 오늘 KST)] [--baseline] [--home DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import traceback
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from compat.mappings import BY_TABLE
from compat.v3_post import SCORE_TABLES, _meta_rows, _ro

from daily import calendar_refresh as cr
from daily.window_judge import parse_date

TOOL = "daily.cutover_watch"
SCHEMA = 1                      # watch_<D>.json 모양 판본 — 키를 바꾸면 올린다
WATCH_DIR = Path("data/cutover")
NOTIFY = Path("scripts/notify.sh")
# crit 제목 — X-2 `window_judge.COUNTED_CRIT_PREFIXES` 의 '컷오버 감시 ' 접두로 센다(테스트가 대조한다)
TITLE_VIOLATION = "컷오버 감시 위반"
TITLE_ERROR = "컷오버 감시 판정 불가"
KST = cr.KST
SAMPLE_N = 8                    # 화면에 펼칠 항목 수(나머지는 '외 n')

ALLOWED, COLLECT, SCORING, UNKNOWN = "allowed", "collect", "scoring", "unknown"
# 화면·JSON 순서 — 분류 안 된 job 을 맨 앞에(기준선 실측의 목적이 허용 목록 확인이다)
_CLASS_ORDER = {UNKNOWN: 0, COLLECT: 1, SCORING: 2, ALLOWED: 3}

# job 분류표 — (분류, 근거). 허용 = 남기는 것(N-42 Q2 · 정본 §0 범위 밖 · T-27 · T-31), 금지 = 무거운 수집,
# 스코어링 = 점수 쓰기(②). 여기 없는 job 은 허용 목록 밖(위반) — 근거와 함께 넣어야 통과한다.
JOBS: dict[str, tuple[str, str]] = {
    "holiday_gate": (ALLOWED, "휴장 캐시 파일만 읽는다, API 없음(scripts/check_today_business.py:13-40) — "
                              "daily_post·daily_insight 첫 단계(T-31)"),
    "export_scores": (ALLOWED, "점수 엑셀·텔레그램(scripts/export_and_send.py) — daily_post, 되돌리기 창 동안 유지(T-20)"),
    "insight_pipeline": (ALLOWED, "시장 단위 키움 TR 5종 + ECOS(backend/insight/collector.py:172-188) — T-27"),
    "wiki_ingest": (ALLOWED, "kael-wiki 위키 갱신 — daily_insight(T-31)"),
    "wiki_lint": (ALLOWED, "kael-wiki 주간 점검 — daily_insight(T-31)"),
    "briefing_morning": (ALLOWED, "07:00 브리핑 — v3 소비자(정본 §0), 시장 단위 시황"),
    "briefing_midday": (ALLOWED, "12:15 브리핑 — v3 소비자(정본 §0)"),
    "briefing_close": (ALLOWED, "15:35 브리핑 — v3 소비자(정본 §0)"),
    "news_ingest": (ALLOWED, "뉴스 수집 — 유지(N-42 Q2)"),
    "trade_ingest": (ALLOWED, "관세청 수출입 — 키움·KIS·WiseReport 아님(N-42 Q2 끄는 목록 밖)"),
    "trade_backfill": (ALLOWED, "관세청 수출입 백필 — 키움·KIS·WiseReport 아님(N-42 Q2 끄는 목록 밖)"),
    "chain:daily_post": (ALLOWED, "v3_post.sh 가 부르는 holiday_gate·export_scores(T-31, COMPAT_LAYER §8-1 V3-A)"),
    "chain:daily_insight": (ALLOWED, "20:05 holiday_gate·insight·wiki(T-31, V3-A)"),
    "chain:briefing_morning": (ALLOWED, "브리핑 체인(job_runner.py:55-58)"),
    "chain:briefing_midday": (ALLOWED, "브리핑 체인(job_runner.py:59-62)"),
    "chain:briefing_close": (ALLOWED, "브리핑 체인(job_runner.py:63-66)"),
    "chain:daily_all": (COLLECT, "옛 20:05 체인 — 수집·스코어링 포함(job_runner.py:43-54), 컷오버 날 크론 제거(V3-B)"),
    "daily_pipeline": (COLLECT, "KIS·키움·WiseReport 전체 수집(backend/pipeline/daily_pipeline.py:100-118)"),
    "holiday_check": (COLLECT, "KIS 휴장 조회 3회(daily_pipeline.py:120-162)"),
    "stock_master": (COLLECT, "KIS 종목 마스터 + 키움 ka10099(daily_pipeline.py:179-253)"),
    "daily_prices": (COLLECT, "키움 종목별 ka10081·ka10001(backend/pipeline/collectors.py:56·63)"),
    "investor_flows": (COLLECT, "키움 종목별 ka10059(collectors.py:104)"),
    "consensus": (COLLECT, "네이버 WiseReport 종목별(backend/pipeline/collect_wisereport.py)"),
    "adj_prices": (COLLECT, "키움 종목별 ka10081 수정주가 소급(scripts/backfill.py:95-98 → "
                            "_backfill_mode_helpers.py:216)"),
    "calendar_refresh": (COLLECT, "KIS 휴장 호출로 .kis_holidays.json 을 덮는다(scripts/refresh_market_calendar.py) — "
                                  "T-31 ①·T-24"),
    "scoring": (SCORING, "v3 스코어링 → score_history(backend.scoring.engine)"),
    "scoring_v2": (SCORING, "v3 v2 스코어링 → score_history_v2(backend.scoring.v2_engine)"),
}
# 접두 규칙 — 소스마다 이름이 붙는 job
JOB_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("research_broker_", ALLOWED, "증권사 리포트 직접 수집 — 유지(backend/research/brokers/_store.py:26-35)"),
)
# T-27 — insight 의 시장 단위 키움 TR(backend/clients/kiwoom/market_client.py:37·49·65·99·123). QL-L 감시의 예외
ALLOWED_TR = frozenset({"ka20006", "ka20001", "ka10051", "ka90010", "ka20003"})

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
_JOB_RE = re.compile(r"\bjob=(\S+)")
_TR_RE = re.compile(r"\bk[at]\d{5}\b")
_KIS_RE = re.compile(r"koreainvestment\.com|KIS rate limit", re.IGNORECASE)
_WISE_RE = re.compile(r"wisereport", re.IGNORECASE)
_MARKER_LABEL = {"kis": "KIS 호출", "wisereport": "네이버 WiseReport"}

# 크론 규칙 — (이름, 패턴, 종류, 근거). forbid = 켜진 줄이 있으면 위반, require = 없으면 위반, info = 보이기만
_CRON_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
CRON_RULES: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    ("daily_all", re.compile(r"--chain[= ]+daily_all\b"), "forbid", "V3-B — daily_all 줄을 지운다"),
    ("daily_insight", re.compile(r"--chain[= ]+daily_insight\b"), "require", "V3-A — 20:05 자리 daily_insight 체인"),
    ("refresh_year_holidays", re.compile(r"refresh_year_holidays"), "forbid", "V3-D — v3 휴장 쓰기(KIS) 끄기, T-24"),
    ("monthly_holiday_review", re.compile(r"monthly_holiday_review"), "forbid",
     "V3-D — v3 휴장 쓰기(KIS) 끄기, T-24"),
    ("daily_post", re.compile(r"\bdaily_post\b"), "info",
     "v3_post.sh 가 --v3-post-cmd 로 부른다 — 크론 줄을 요구하지 않는다(COMPAT_LAYER §8-1 V3-A)"),
)

_WEEKDAY = "월화수목금토일"
_VERDICT_KO = {"ok": "정상", "violation": "위반", "baseline": "기준선 기록", "error": "판정 불가"}
_TS_OUT = "%Y-%m-%dT%H:%M:%SZ"


class InputError(RuntimeError):
    """입력을 못 읽었거나 모양이 다르다 — 판정하지 않는다(rc 2). 메시지에 경로를 싣는다."""


def classify_job(job: str) -> tuple[str, str]:
    """job 이름 → (분류, 근거). 분류표·접두 규칙 어디에도 없으면 허용 목록 밖(UNKNOWN)."""
    if job in JOBS:
        return JOBS[job]
    for prefix, cls, why in JOB_PREFIXES:
        if job.startswith(prefix):
            return cls, why
    return UNKNOWN, "분류표에 없다 — 새 job 이면 근거와 함께 JOBS 에 넣는다"


def day_window(d: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """KST 하루 D → UTC [시작, 끝)."""
    lo = dt.datetime.combine(d, dt.time(), KST).astimezone(dt.UTC)
    return lo, lo + dt.timedelta(days=1)


def _utc(date_s: str, time_s: str) -> dt.datetime:
    return dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC)


# ── 입력 읽기 ────────────────────────────────────────────────────────────────
@dataclass
class JobSeen:
    """그날 본 job 하나 — 근거 두 곳의 횟수."""

    runs: int = 0                                          # pipeline_runs 행 수
    log_lines: int = 0                                     # 로그의 `job=<이름>` 줄 수
    statuses: Counter[str] = field(default_factory=Counter)  # pipeline_runs status 분포


@dataclass
class Marker:
    """로그 표지 묶음 — (표지, 그 줄의 job) 하나."""

    token: str                  # 키움 TR id · 'kis' · 'wisereport'
    job: str | None             # 바로 앞 job_runner 줄의 job(없으면 None)
    allowed: bool
    lines: int = 0
    sample: str = ""


@dataclass(frozen=True)
class LogInfo:
    path: str
    lines_in_day: int
    last_at_utc: str | None     # 파일의 마지막 시각 줄(창과 무관) — 로그가 멈췄는지 보이기만


def read_runs(con: sqlite3.Connection, lo: dt.datetime, hi: dt.datetime) -> list[tuple[str, dt.datetime, str]]:
    """`pipeline_runs` 의 창 안 행 → (job, 시작 UTC, status). 시각 형식이 다르면 InputError."""
    days = sorted({(lo + dt.timedelta(hours=h)).strftime("%Y-%m-%d") for h in (0, 24)})
    try:
        rows = con.execute("SELECT job_name, started_at, status FROM pipeline_runs "
                           f"WHERE substr(started_at, 1, 10) IN ({', '.join('?' * len(days))}) ORDER BY run_id",
                           days).fetchall()
    except sqlite3.Error as e:
        raise InputError(f"v3 quant.db pipeline_runs 를 못 읽음: {e}") from e
    out: list[tuple[str, dt.datetime, str]] = []
    for job, started, status in rows:
        m = _TS_RE.match(started or "")
        if not m:
            raise InputError(f"v3 quant.db pipeline_runs started_at 형식 밖: {job} {started!r}")
        at = _utc(m.group(1), m.group(2))
        if lo <= at < hi:
            out.append((job, at, status or ""))
    return out


def read_log(path: Path, lo: dt.datetime, hi: dt.datetime, jobs: dict[str, JobSeen],
             markers: dict[tuple[str, str | None], Marker]) -> LogInfo:
    """v3 체인 로그의 창 안 줄에서 job 이름·표지를 모은다. 시각 없는 줄은 앞 시각 줄의 날짜·job 을 따르고, 첫 시각
    줄 앞의 줄은 날짜를 몰라 버린다."""
    if not path.is_file():
        raise InputError(f"v3 로그 없음: {path}")
    at: dt.datetime | None = None
    job: str | None = None
    n_in = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = _TS_RE.match(line)
            if m:
                at = _utc(m.group(1), m.group(2))
            jm = _JOB_RE.search(line)
            if jm:
                job = jm.group(1)
            if at is None or not (lo <= at < hi):
                continue
            n_in += 1
            if jm:
                jobs.setdefault(jm.group(1), JobSeen()).log_lines += 1
            hits = [(t, t in ALLOWED_TR) for t in dict.fromkeys(_TR_RE.findall(line))]
            if _KIS_RE.search(line):
                hits.append(("kis", False))
            if _WISE_RE.search(line):
                hits.append(("wisereport", False))
            for token, allowed in hits:
                mk = markers.setdefault((token, job), Marker(token, job, allowed))
                mk.lines += 1
                mk.sample = mk.sample or line.strip()[:200]
    return LogInfo(str(path), n_in, at.strftime(_TS_OUT) if at else None)


def _require_tables(con: sqlite3.Connection, path: Path) -> None:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in ("pipeline_runs", *SCORE_TABLES) if t not in have]
    if missing:
        raise InputError(f"v3 quant.db 에 표 없음 {missing}: {path}")


def read_scores(con: sqlite3.Connection, d_iso: str) -> tuple[dict[str, dict[str, object]], list[str]]:
    """점수 두 표의 그날 행 수와 그 표를 쓴 그날 마지막 compat ok 기록 → (표별 요약, 위반)."""
    try:
        meta = _meta_rows(con)
    except sqlite3.Error as e:
        raise InputError(f"v3 quant.db _compat_meta 를 못 읽음: {e}") from e
    tables: dict[str, dict[str, object]] = {}
    fails: list[str] = []
    for t in SCORE_TABLES:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE score_date = ?', (d_iso,)).fetchone()[0]
        spec = BY_TABLE[t].sources[0]
        recs = []
        for r in meta:
            if r.get("date") != d_iso or r.get("status") != "ok":
                continue
            try:
                written = json.loads(r["tables"])
                builds = json.loads(r.get("model_builds") or "{}")
            except (TypeError, ValueError) as e:
                raise InputError(f"_compat_meta {r.get('exported_at')} 의 tables·model_builds 가 JSON 이 아니다: "
                                 f"{e}") from e
            if t in written:
                recs.append((str(r["exported_at"]), str(r.get("basis")), written[t], builds))
        rec: dict[str, object] | None = None
        if recs:
            exported_at, basis, res, builds = max(recs, key=lambda x: x[0])
            if not isinstance(res, dict) or not isinstance(res.get("n_rows"), int):
                raise InputError(f"_compat_meta {exported_at} 의 {t} 결과에 n_rows 가 없다: {res!r}")
            rec = {"exported_at": exported_at, "basis": basis, "n_rows": res["n_rows"], "spec": spec,
                   "build_id": builds.get(spec) if isinstance(builds, dict) else None}
            if n != res["n_rows"]:
                fails.append(f"{t} {d_iso} 행 {n} ≠ compat 기록 {exported_at}({basis}) 넣은 행 {res['n_rows']} — "
                             "compat 반영 뒤 다른 쓰기")
            if not rec["build_id"]:
                fails.append(f"{t} compat 기록 {exported_at} 에 판 id({spec}) 없음 — model_builds")
        elif n:
            fails.append(f"{t} {d_iso} 행 {n} 이 있는데 그날 compat 반영 기록(_compat_meta ok·{t} 포함)이 없다 — "
                         "compat 밖 쓰기")
        tables[t] = {"rows": n, "record": rec}
    return tables, fails


def read_crontab(source: str) -> list[str]:
    """`crontab -l` 출력 → 켜진 줄. `-` 이면 표준입력. 켜진 줄이 0 이면 InputError(`crontab -l` 실패를 '위반 0' 으로
    읽지 않는다 — 사용자 crontab 이 없을 때도 빈 출력이다)."""
    if source == "-":
        text = sys.stdin.read()
    else:
        path = Path(source)
        if not path.is_file():
            raise InputError(f"crontab 출력 파일 없음: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
    active = [ln.strip() for ln in text.splitlines()
              if ln.strip() and not ln.lstrip().startswith("#") and not _CRON_ENV_RE.match(ln.strip())]
    if not active:
        raise InputError(f"crontab 켜진 줄 0 — `crontab -l` 실패로 본다: {source}")
    return active


# ── 판정 ───────────────────────────────────────────────────────────────────
@dataclass
class Result:
    date: dt.date
    baseline: bool
    window: tuple[dt.datetime, dt.datetime]
    inputs: dict[str, object]
    jobs: dict[str, JobSeen]
    markers: list[Marker]
    logs: list[LogInfo]
    scores: dict[str, dict[str, object]]
    cron_lines: dict[str, list[str]]
    daily_post_runs: int
    v_collect: list[str]
    v_scores: list[str]
    v_cron: list[str]
    notified: bool | None = None

    @property
    def violations(self) -> list[str]:
        return self.v_collect + self.v_scores + self.v_cron

    @property
    def verdict(self) -> str:
        if self.baseline:
            return "baseline"
        return "violation" if self.violations else "ok"

    @property
    def rc(self) -> int:
        return 1 if self.verdict == "violation" else 0

    def title(self) -> str:
        return (f"{TITLE_VIOLATION} D={self.date:%Y%m%d}: ① 수집 {len(self.v_collect)} · ② 점수 {len(self.v_scores)} · "
                f"③ 크론 {len(self.v_cron)}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA, "tool": TOOL, "generated_at": dt.datetime.now(dt.UTC).strftime(_TS_OUT),
            "date": f"{self.date:%Y%m%d}", "mode": "baseline" if self.baseline else "watch",
            "verdict": self.verdict, "rc": self.rc,
            "window_utc": [x.strftime(_TS_OUT) for x in self.window],
            "inputs": self.inputs,
            "collect": {
                "jobs": [{"job": j, "class": classify_job(j)[0], "why": classify_job(j)[1], "runs": s.runs,
                          "log_lines": s.log_lines, "statuses": dict(s.statuses)}
                         for j, s in _sorted_jobs(self.jobs)],
                "markers": [{"token": m.token, "job": m.job, "allowed": m.allowed, "lines": m.lines,
                             "sample": m.sample} for m in self.markers],
                "logs": [{"path": x.path, "lines_in_day": x.lines_in_day, "last_at_utc": x.last_at_utc}
                         for x in self.logs],
                "violations": self.v_collect,
            },
            "scores": {"tables": self.scores, "violations": self.v_scores},
            "cron": {"lines": self.cron_lines, "daily_post_runs": self.daily_post_runs, "violations": self.v_cron},
            "violations": self.violations,
            "notified": self.notified,
        }


def _sorted_jobs(jobs: dict[str, JobSeen]) -> list[tuple[str, JobSeen]]:
    return sorted(jobs.items(), key=lambda kv: (_CLASS_ORDER[classify_job(kv[0])[0]], kv[0]))


def _evidence(s: JobSeen) -> str:
    return f"[pipeline_runs {s.runs}회·로그 {s.log_lines}줄]"


def watch(d: dt.date, *, v3_db: Path, v3_logs: Sequence[Path], crontab: str, baseline: bool = False) -> Result:
    """세 검사를 돌린다. 입력을 못 읽으면 InputError."""
    lo, hi = day_window(d)
    if not v3_db.is_file():
        raise InputError(f"v3 quant.db 없음: {v3_db}")
    cron = read_crontab(crontab)
    jobs: dict[str, JobSeen] = {}
    markers: dict[tuple[str, str | None], Marker] = {}
    logs = [read_log(p, lo, hi, jobs, markers) for p in v3_logs]
    try:
        con = _ro(v3_db)
    except sqlite3.Error as e:
        raise InputError(f"v3 quant.db 를 못 엶: {v3_db} — {e}") from e
    try:
        try:
            _require_tables(con, v3_db)
        except sqlite3.Error as e:
            raise InputError(f"v3 quant.db 를 못 읽음: {v3_db} — {e}") from e
        for job, _at, status in read_runs(con, lo, hi):
            seen = jobs.setdefault(job, JobSeen())
            seen.runs += 1
            seen.statuses[status] += 1
        scores, v_scores = read_scores(con, f"{d:%Y-%m-%d}")
    finally:
        con.close()

    v_collect: list[str] = []
    v_scoring: list[str] = []
    for job, seen in _sorted_jobs(jobs):
        cls, why = classify_job(job)
        if cls == COLLECT:
            v_collect.append(f"금지 수집 job {job} — {why} {_evidence(seen)}")
        elif cls == UNKNOWN:
            v_collect.append(f"허용 목록 밖 job {job} — {why} {_evidence(seen)}")
        elif cls == SCORING:
            v_scoring.append(f"v3 스코어링 job {job} 실행 — {why} {_evidence(seen)}")
    marks = sorted(markers.values(), key=lambda m: (m.allowed, m.token, m.job or ""))
    for m in marks:
        if not m.allowed:
            label = _MARKER_LABEL.get(m.token, f"키움 TR {m.token}(허용 TR 밖)")
            v_collect.append(f"로그 표지 {label} {m.lines}줄(job {m.job or '?'}) — 예: {m.sample}")

    cron_lines = {name: [ln for ln in cron if pat.search(ln)] for name, pat, _kind, _why in CRON_RULES}
    v_cron: list[str] = []
    for name, _pat, kind, why in CRON_RULES:
        got = cron_lines[name]
        if kind == "forbid" and got:
            v_cron += [f"{name} 크론 줄 남음({why}): {ln[:160]}" for ln in got]
        elif kind == "require" and not got:
            v_cron.append(f"{name} 크론 줄 없음({why})")

    inputs: dict[str, object] = {"v3_db": str(v3_db), "v3_logs": [str(p) for p in v3_logs], "crontab": crontab}
    post = jobs.get("chain:daily_post")
    return Result(d, baseline, (lo, hi), inputs, jobs, marks, logs, scores, cron_lines,
                  post.runs if post else 0, v_collect, v_scoring + v_scores, v_cron)


# ── 출력 ───────────────────────────────────────────────────────────────────
def _cap(items: Sequence[str], indent: str = "   ") -> list[str]:
    out = [f"{indent}✗ {x}" for x in items[:SAMPLE_N]]
    if len(items) > SAMPLE_N:
        out.append(f"{indent}… 외 {len(items) - SAMPLE_N}건(JSON)")
    return out


def render(res: Result, out: Path | None) -> str:
    d = res.date
    lo, hi = res.window
    mode = "기준선(위반으로 보지 않음)" if res.baseline else "감시"
    lines = [f"컷오버 감시 {d:%Y-%m-%d}({_WEEKDAY[d.weekday()]}) KST — 모드 {mode} · 창 UTC "
             f"{lo:%m-%d %H:%M} ~ {hi:%m-%d %H:%M}"]
    allowed = [j for j, _s in _sorted_jobs(res.jobs) if classify_job(j)[0] == ALLOWED]
    logs = " · ".join(f"{Path(x.path).name} {x.lines_in_day}줄(마지막 {x.last_at_utc or '없음'})" for x in res.logs)
    lines.append(f"① v3 무거운 수집 — 위반 {len(res.v_collect)} · job {len(res.jobs)}종(허용 {len(allowed)}) · {logs}")
    lines += _cap(res.v_collect)
    unknown = [j for j in res.jobs if classify_job(j)[0] == UNKNOWN]
    lines.append("   분류 안 된 job: " + (" · ".join(sorted(unknown)) if unknown else "없음"))
    if allowed:
        lines.append("   허용: " + " · ".join(allowed[:SAMPLE_N * 2])
                     + (f" 외 {len(allowed) - SAMPLE_N * 2}" if len(allowed) > SAMPLE_N * 2 else ""))
    ok_marks = [f"{m.token} {m.lines}줄({m.job or '?'})" for m in res.markers if m.allowed]
    if ok_marks:
        lines.append("   허용 TR 표지(T-27): " + " · ".join(ok_marks))
    lines.append(f"② 점수 쓰기 한 곳 — 위반 {len(res.v_scores)}")
    for t, info in res.scores.items():
        rec = info["record"]
        if isinstance(rec, dict):
            lines.append(f"   {t} {info['rows']}행 · compat {rec['exported_at']}({rec['basis']}) {rec['n_rows']}행 · "
                         f"판 {rec['spec']}={rec['build_id'] or '없음'}")
        else:
            lines.append(f"   {t} {info['rows']}행 · 그날 compat 기록 없음")
    lines += _cap(res.v_scores)
    counts = " · ".join(f"{name} {len(res.cron_lines[name])}줄" for name, *_ in CRON_RULES)
    lines.append(f"③ v3 크론 — 위반 {len(res.v_cron)} · {counts} · 그날 chain:daily_post {res.daily_post_runs}회"
                 "(daily_post 는 v3_post.sh 가 부른다)")
    lines += _cap(res.v_cron)
    tail = f" — 감시 모드였다면 위반 {len(res.violations)}건" if res.baseline else ""
    lines.append(f"판정: {_VERDICT_KO[res.verdict]}(rc {res.rc}){tail}")
    lines.append(f"→ {out}" if out else "→ (dry-run — 결과 파일·알림 없음)")
    return "\n".join(lines)


def _notify(home: Path, title: str, body: str) -> bool:
    """`scripts/notify.sh crit`(기록만 — 텔레그램 금지 10-01). 실패하면 False."""
    notify = home / NOTIFY
    try:
        proc = subprocess.run([str(notify), "crit", title, body], check=False, capture_output=True, text=True)
    except OSError as e:
        print(f"{TOOL} notify 실행 실패 — {notify} ({type(e).__name__}: {e})", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"{TOOL} notify 실패 rc={proc.returncode} {proc.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return True


def _error_payload(d: str, baseline: bool, inputs: dict[str, object], message: str) -> dict[str, object]:
    return {"schema": SCHEMA, "tool": TOOL, "generated_at": dt.datetime.now(dt.UTC).strftime(_TS_OUT),
            "date": d, "mode": "baseline" if baseline else "watch", "verdict": "error", "rc": 2,
            "inputs": inputs, "error": message}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=TOOL, description="컷오버 감시(QL-L) — v3 수집 0 · 점수 쓰기 한 곳 · v3 크론")
    ap.add_argument("--date", default=None, help="KST 날짜 YYYYMMDD(기본 오늘 KST)")
    ap.add_argument("--v3-db", required=True, help="v3 quant.db(읽기 전용으로 연다)")
    ap.add_argument("--v3-log", required=True, action="append",
                    help="v3 체인 로그(pipeline.log). 여러 번 줄 수 있다 — research.log 는 넣지 않는다")
    ap.add_argument("--crontab", required=True, help="`crontab -l` 출력 파일, - 이면 표준입력")
    ap.add_argument("--baseline", action="store_true",
                    help="컷오버 전 기준선 — 기록만 하고 위반으로 보지 않는다(rc 0, 알림 없음)")
    ap.add_argument("--dry-run", action="store_true", help="결과 파일·알림 없이 요약만")
    ap.add_argument("--home", default=None, help="quant-ledger 루트(기본 $QL_HOME)")
    a = ap.parse_args(argv)
    home = Path(a.home or os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    d_s = a.date or dt.datetime.now(KST).strftime("%Y%m%d")
    inputs: dict[str, object] = {"v3_db": a.v3_db, "v3_logs": a.v3_log, "crontab": a.crontab}
    alert = not (a.baseline or a.dry_run)
    out: Path | None = None
    try:
        d = parse_date(d_s)
        out = None if a.dry_run else home / WATCH_DIR / f"watch_{d_s}.json"
        res = watch(d, v3_db=Path(a.v3_db), v3_logs=[Path(p) for p in a.v3_log], crontab=a.crontab,
                    baseline=a.baseline)
    except (InputError, ValueError) as e:
        msg = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001  # reason: 예상 밖 예외도 '판정 못 함'(rc 2) — 통과(rc 0)로 읽히지 않게
        traceback.print_exc()
        msg = f"예상 밖 예외 {type(e).__name__}: {e}"
    else:
        if alert and res.rc == 1:
            res.notified = _notify(home, res.title(), " · ".join(res.violations))
        print(render(res, out))
        if out is not None:
            try:
                cr._atomic_write_json(out, res.to_dict())
            except OSError as e:
                print(f"{TOOL} 결과 파일 쓰기 실패: {out} {type(e).__name__}: {e}", file=sys.stderr)
                return 2
        return res.rc
    print(f"{TOOL} 입력 오류(rc 2): {msg}", file=sys.stderr)
    if alert:
        _notify(home, f"{TITLE_ERROR} D={d_s}", msg)
    if out is not None:
        try:
            cr._atomic_write_json(out, _error_payload(d_s, a.baseline, inputs, msg))
        except OSError as e:
            print(f"{TOOL} 결과 파일(판정 불가) 쓰기 실패: {out} {type(e).__name__}: {e}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
