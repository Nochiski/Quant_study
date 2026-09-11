"""DART 일일 증분 러너 — 플랜 `docs/plans/2026-09-09-daily-incremental.md` Task 1.5 (결정 R4).

`daily_dart.sh` 를 대체한다. 그 스크립트의 결함 4건이 여기서 닫힌다 —

  · B01 열린 분기 창은 구조적으로 영구 `mismatch` 라 `ingest_log.status` 로 "오늘이 끝났는가" 를
    판정할 수 없다 → 완료 판정은 원장 행(`dart_disclosure.rcept_dt=D`)과 **오늘 나간 콜**
    (`dart_call_log`)로만 한다. 이 파일 어디에서도 `ingest_log.status` 를 읽지 않는다.
  · B02 정기보고서 부속 6종이 `11011`(사업) 만 수집됐다 → 당일 공시의 기간 라벨에서 읽은
    **그 (bsns_year, reprt_code)** 로 부른다.
  · B03 stage 2 가 올해 사업연도를 안 쐈다 → 연도를 크론이 아니라 공시가 정한다.
  · B04 stage 5(DS005 조정계수 8종)가 빠져 있었다 → 주요사항보고서 corp 은 DS005 15종 전부.

핵심 구조는 **스윕 → 상세의 연결**이다(`COLLECT_PLAN.md:221`, findings B §8-2):
"당일 `list.json` 공시에 해당 유형이 있는 corp 만 그날 재호출 — 이벤트 없으면 0콜".
백필의 종결 판정은 `ok` 를 축 무관하게 **영구 종결**로 보므로(`backfill_dart.py:674-681`),
재호출하려면 그 유닛의 `ingest_log` 행을 먼저 지워야 한다(`unlock`). `store()` 가 멱등이라
재호출 자체는 안전하고, 백필 코드는 한 줄도 고치지 않는다(플랜 §4 "백필 코드는 동결").

그 "당일" 의 정의는 접수일이 아니라 **처음 본 날**이다(플랜 v2 §3 Task A.2 Step 5). DART 목록에는
접수일이 지난 공시가 뒤늦게 나타난다 — 09-10 스윕에서 08-25 접수 8건·09-03 2건·09-07 1건이
처음 등장했다(검수 09-10 D). `rcept_dt = D` 로만 고르면 그런 공시는 접수일이 D 인 적이 없어
상세 축을 **영영** 못 받는다. 그래서 대상은 (a) `rcept_dt = D` ∪ (b) 이번 런의 스윕이 시작된
뒤에 처음 관측된 `rcept_dt < D` 공시다. 최초 관측 시각은 원장의 `collected_at` 인데,
`backfill_dart.store()` 가 `INSERT OR IGNORE` 라 처음 본 행의 값이 유지된다
(`backfill_dart.py:480-483`). 다만 재스윕은 같은 공시를 다른 `req_page_no` 로 한 번 더 적재하고
(요청 파라미터가 행 해시에 들어간다 — `sweep_disclosure.py:158-165`) 그 행의 `collected_at` 은
오늘이므로, 판정은 행이 아니라 **공시 단위 `MIN(collected_at)`** 로 한다. 행 기준으로 보면
늦은 공시 1건이 끼어들며 밀려난 그 뒤 전 구간이 통째로 "처음 본 공시" 가 되어 예산이 터진다.

하위 도구는 subprocess 로 부른다 — `sweep_disclosure.py`·`backfill_dart.py`·`backfill_docs.py`.
import 하지 않는 이유는 `universe.py` 와 같다: 그쪽은 import 시점에 `.env` 를 요구하는 `api` 를
끌어온다. 그래서 엔드포인트 이름도 여기에 다시 적고(`backfill_dart.py:128-139` 의 STAGES 와
`:141-205` 의 SPEC 축), 어긋나면 테스트가 잡는다(`tests/test_daily_dart.py` 드리프트 대조).
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
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

# 스크립트로 직접 실행될 때(`python src/daily/dart_daily.py`) `daily` 패키지를 찾게 한다 —
# `backfill_dart.py:27`·`sweep_disclosure.py:47` 과 같은 규약. `python -m daily.dart_daily` 로
# 부를 때는 이미 경로에 있어 무해하다.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from daily import calendar as daily_calendar
from daily import runlog

# ── 엔드포인트 (SoT = `backfill_dart.py` STAGES·SPEC. 드리프트는 테스트가 잡는다) ──────────
# stage 2 + stage 3 의 corp_year 축 6종. 정기보고서 하나가 이 7개를 되살린다.
PERIODIC_ENDPOINTS: tuple[str, ...] = ("fin", "dividend", "shares", "capital",
                                       "tesstk", "hyslr", "audit")
# stage 3 의 corp 축 2종. 지분공시(대량보유·임원소유)가 이 둘을 되살린다.
HOLDER_ENDPOINTS: tuple[str, ...] = ("elestock", "majorstock")
# stage 4(7) + stage 5(8) = DS005 15종. corp_range 축이라 1콜에 그 회사 전 이력이 온다.
# report_nm → 엔드포인트 매핑표를 두지 않고 전량 재호출하는 이유는 findings B §8-2:
# 매핑표는 새 유형이 조용히 빠지고, 전량은 일 300~510콜(용량의 0.6%)이라 누락 위험이 없다.
DS005_ENDPOINTS: tuple[str, ...] = (
    "tsstkAqDecsn", "piicDecsn", "cvbdIsDecsn", "ctrcvsBgrq", "dfOcr", "dsRsOcr", "bnkMngtPcbg",
    "fricDecsn", "pifricDecsn", "crDecsn", "cmpMgDecsn", "cmpDvDecsn", "cmpDvmgDecsn",
    "stkExtrDecsn", "tsstkDpDecsn")
# `dart_call_log.endpoint` 는 SPEC 의 `ep`(파일명)이다. 완료 판정이 이 철자로 조인한다.
FIN_CALL_EP = "fnlttSinglAcntAll.json"
DS005_CALL_EPS: tuple[str, ...] = tuple(f"{n}.json" for n in DS005_ENDPOINTS)
# `--only` 의 나열 순서 = 백필의 순회 순서다. 알파벳순 대신 선언순으로 둔다 —
# `fin` 이 먼저 끝나야 나머지를 기다리지 않고 재무를 쓸 수 있다(`backfill_dart.py:120-127`).
_EP_ORDER: dict[str, int] = {ep: i for i, ep in enumerate(
    (*PERIODIC_ENDPOINTS, *HOLDER_ENDPOINTS, *DS005_ENDPOINTS))}

# ── report_nm 어휘 (SoT = `src/equity/rules_s11.py:55-61`) ────────────────────────────
PERIODIC_PREFIXES: tuple[str, ...] = ("사업보고서", "반기보고서", "분기보고서")
EXCLUDED_TOKENS: tuple[str, ...] = ("연장신고", "유동화전문회사", "회계법인", "해외증권거래소")
CORRECTION_PREFIXES: tuple[str, ...] = ("기재정정", "첨부정정")
MAJOR_TOKEN = "주요사항보고서"
# 대량보유(5%)·임원소유 두 보고서. 가운데점 문자가 `ㆍ`/`·` 로 갈리므로 뒤쪽 조각으로 문다.
HOLDER_TOKENS: tuple[str, ...] = ("주식등의대량보유상황보고서", "특정증권등소유상황보고서")

_PREFIX_RE = re.compile(r"^(?:\[([^\]]*)\])+")
_ONE_PREFIX_RE = re.compile(r"\[([^\]]*)\]")
_LABEL_RE = re.compile(r"\((\d{4})\.(\d{2})\)")
# 12월 결산 기준. 분기보고서의 Q1/Q3 를 가르는 데만 쓴다 — 사업·반기는 이름이 먼저 정한다.
_QUARTER_REPRT: dict[str, str] = {"03": "11013", "09": "11014"}

# ── 예산 (findings B §3-1·§7-5) ────────────────────────────────────────────────────
OUR_QUOTA_LIMIT = 40_000       # 우리 키(k2+k3) 합계 상한. 넘으면 중단한다
KAEL_KEY_ID = "kael"           # v3 프로덕션 키. 1콜이라도 나가면 crit
# 완료 판정 기대치 (findings B §7-1 ① — 실측 08-18~09-01 범위)
GATE_MIN_FILINGS = 400
GATE_MIN_LISTED = 290
DEFAULT_MAX_DOCS = 3000        # `backfill_docs.sleep_to_kst_midnight()` 진입 방지
LATE_SAMPLE_MAX = 20           # 늦게 등장한 공시의 rcept_no 표본 상한(로그용). 수는 n_late 가 센다


# ── 결과 값 타입 ──────────────────────────────────────────────────────────────────
class Kind(Enum):
    """당일 공시 1건의 재호출 유형. `CORRECTION` 은 **원 유형을 못 정한 정정본**만이다."""

    PERIODIC = "periodic"
    MAJOR = "major"
    HOLDER = "holder"
    CORRECTION = "correction"
    OTHER = "other"


@dataclass(frozen=True)
class Classified:
    """`classify()` 의 결과. 정정 여부는 유형과 직교한다 — 정정본도 원 유형의 유닛을 되살린다."""

    kind: Kind
    is_correction: bool
    nm_clean: str
    prefixes: tuple[str, ...] = ()
    bsns_year: str | None = None
    reprt_code: str | None = None
    detail: str = ""

    @property
    def resolved(self) -> bool:
        """정기보고서인데 (bsns_year, reprt_code) 를 못 읽었으면 False — 유닛을 만들 수 없다."""
        return self.kind is not Kind.PERIODIC or (self.bsns_year is not None
                                                  and self.reprt_code is not None)


@dataclass(frozen=True, order=True)
class Unit:
    """재호출 1유닛 = `ingest_log` PK 에서 `fs_div` 를 뺀 것.

    corp·corp_range 축은 `bsns_year`·`reprt_code` 가 빈 문자열이다
    (`backfill_dart.py:742` 가 `y or ""`·`rc or ""` 로 적는다).
    """

    endpoint: str
    corp_code: str
    bsns_year: str
    reprt_code: str


@dataclass(frozen=True)
class DailyPlan:
    """D일 공시가 만들어 낸 재호출 계획. 콜은 아직 하나도 나가지 않았다.

    `n_rows`·`n_filings` 는 **접수일이 D 인 공시만** 센다 — 완료 판정의 `filings` 게이트가
    쓰는 값이라 늦게 등장한 공시를 섞으면 하한 400 의 의미가 흐려진다. 늦게 등장한 쪽은
    `n_late` 로 따로 센다(게이트가 아니라 기록용).
    """

    date: str
    n_rows: int                                   # 접수일 D 인 상장사 공시 행수(중복 제거 전)
    n_filings: int                                # 접수일 D 인 DISTINCT rcept_no
    units: tuple[Unit, ...]
    kind_counts: dict[str, int]
    periodic_units: tuple[tuple[str, str, str], ...] = ()   # (corp_code, bsns_year, reprt_code)
    major_corps: tuple[str, ...] = ()
    holder_corps: tuple[str, ...] = ()
    unresolved: tuple[tuple[str, str], ...] = ()  # (rcept_no, report_nm) — 라벨을 못 읽은 정기보고서
    bad_corp_codes: tuple[tuple[str, str], ...] = ()        # (rcept_no, corp_code)
    n_late: int = 0                               # 이번 스윕에서 처음 본 rcept_dt < D 공시 수
    late_rcept_nos: tuple[str, ...] = ()          # 그 표본(최대 LATE_SAMPLE_MAX 건, 로그용)

    @property
    def n_calls_est(self) -> int:
        """`fin` 은 CFS 실패 시 OFS 로 한 번 더 간다(`backfill_dart.py:503` fallback)."""
        return len(self.units) + sum(1 for u in self.units if u.endpoint == "fin")


@dataclass(frozen=True)
class BudgetStatus:
    """`dart_call_log` 파생값. 프로세스 재시작에 안전하다(findings B §1-3)."""

    ours: int
    kael: int
    limit: int
    since_ts: str

    @property
    def over_limit(self) -> bool:
        return self.ours > self.limit

    @property
    def kael_used(self) -> bool:
        return self.kael > 0

    @property
    def ok(self) -> bool:
        return not self.over_limit and not self.kael_used

    def describe(self) -> str:
        return (f"ours={self.ours:,}/{self.limit:,} kael={self.kael:,} "
                f"since={self.since_ts}")


@dataclass(frozen=True)
class Gate:
    """완료 판정 1항. `ingest_log.status` 는 쓰지 않는다(DEFECT-B01)."""

    name: str
    ok: bool
    detail: str
    metrics: dict[str, int] = field(default_factory=dict)


class RunStatus(Enum):
    OK = "ok"
    DRY_RUN = "dry_run"
    GATE_FAILED = "gate_failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    KAEL_KEY_USED = "kael_key_used"
    TOOL_FAILED = "tool_failed"


@dataclass(frozen=True)
class DartDailyResult:
    date: str
    status: RunStatus
    plan: DailyPlan
    budget: BudgetStatus
    gates: tuple[Gate, ...] = ()
    rc_sweep: int | None = None
    rc_backfill: tuple[int, ...] = ()
    rc_docs: int | None = None
    n_unlocked: int = 0
    n_calls: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (RunStatus.OK, RunStatus.DRY_RUN)

    @property
    def exit_code(self) -> int:
        """플랜 §4 러너 공통 규약 — 0 성공 / 2 게이트·예산 실패."""
        return 0 if self.ok else 2


# ── 1. 분류 ──────────────────────────────────────────────────────────────────────
def classify(report_nm: str) -> Classified:
    """`report_nm` 한 건을 재호출 유형으로 가른다.

    접두 `[...]` 는 여러 개 붙을 수 있다(`[기재정정][첨부추가]…`). 접두를 벗긴 이름으로
    유형을 정하고, 접두에 `기재정정`·`첨부정정` 이 있으면 정정본으로 표시한다 —
    정정본도 **원 유형과 같은 유닛**을 되살린다(플랜 R4). `[첨부추가]` 는 원본 라벨이라
    정정이 아니다(`rules_s11.CORRECTION_PREFIXES` 와 같은 규약).

    정기보고서의 `reprt_code` 는 **이름이 먼저 정한다** — 사업 → 11011, 반기 → 11012.
    기간 라벨의 월은 분기보고서의 Q1/Q3 를 가를 때만 쓴다. 라벨 월만으로 정하면
    12월 결산이 아닌 법인(실측 3월 결산 32사)의 `사업보고서 (2026.03)` 가 11013(1분기)로
    둔갑한다. 12월 결산이 아닌 법인의 분기보고서는 라벨 월이 03/09 가 아니어서 판정을
    보류하고(`resolved=False`) 계획에 `unresolved` 로 남긴다 — 조용히 빠뜨리지 않는다.
    """
    nm = report_nm.strip()                        # 우측 공백 패딩 실측 (DART_CENSUS §12)
    head = _PREFIX_RE.match(nm)
    prefixes: tuple[str, ...] = () if head is None else tuple(
        _ONE_PREFIX_RE.findall(head.group(0)))
    nm_clean = nm[head.end():].strip() if head is not None else nm
    is_corr = any(p in CORRECTION_PREFIXES for p in prefixes)

    is_periodic = (any(nm_clean.startswith(p) for p in PERIODIC_PREFIXES)
                   and not any(t in nm_clean for t in EXCLUDED_TOKENS))
    if is_periodic:
        return _classify_periodic(nm_clean, prefixes, is_corr)
    if MAJOR_TOKEN in nm_clean:
        return Classified(Kind.MAJOR, is_corr, nm_clean, prefixes)
    if any(t in nm_clean for t in HOLDER_TOKENS):
        return Classified(Kind.HOLDER, is_corr, nm_clean, prefixes)
    kind = Kind.CORRECTION if is_corr else Kind.OTHER
    detail = "correction of an unhandled report type" if is_corr else ""
    return Classified(kind, is_corr, nm_clean, prefixes, detail=detail)


def _classify_periodic(nm_clean: str, prefixes: tuple[str, ...], is_corr: bool) -> Classified:
    m = _LABEL_RE.search(nm_clean)
    if m is None:
        return Classified(Kind.PERIODIC, is_corr, nm_clean, prefixes,
                          detail=f"no (YYYY.MM) period label in {nm_clean!r}")
    year, month = m.group(1), m.group(2)
    if nm_clean.startswith("사업보고서"):
        return Classified(Kind.PERIODIC, is_corr, nm_clean, prefixes, year, "11011")
    if nm_clean.startswith("반기보고서"):
        return Classified(Kind.PERIODIC, is_corr, nm_clean, prefixes, year, "11012")
    reprt = _QUARTER_REPRT.get(month)
    if reprt is None:
        return Classified(
            Kind.PERIODIC, is_corr, nm_clean, prefixes, year, None,
            detail=f"quarterly label month {month} is neither 03 nor 09 — non-December fiscal "
                   f"year, Q1/Q3 undecidable without dart_company.acc_mt: nm={nm_clean!r}")
    return Classified(Kind.PERIODIC, is_corr, nm_clean, prefixes, year, reprt)


# ── 2. 계획 ──────────────────────────────────────────────────────────────────────
def late_rows(con: sqlite3.Connection, date_yyyymmdd: str,
              since_ts: str) -> list[tuple[str, str, str]]:
    """접수일이 D 이전인데 `since_ts` 이후에 **처음 관측된** 상장사 공시 행.

    두 걸음으로 나눈 이유는 비용이다. 원장은 실측 3.4M 행이고 `collected_at`·`rcept_dt` 에
    인덱스가 없다. 1단계는 후보(보통 한 자리수)를 고르고, 2단계는 그 후보의 접수일에만
    걸어 `MIN(collected_at)` 을 잰다 — 전 원장을 rcept_no 로 묶으면 그룹이 300만 개가 된다.
    한 공시의 모든 행은 같은 `rcept_dt` 를 갖는다(같은 응답 필드라서) — 그래서 접수일로
    좁혀도 그 공시의 옛 행을 빠뜨리지 않는다. `IN` 에 들어가는 접수일 수는 재스윕 창
    (`SWEEP_LOOKBACK_DAYS` = 30일 + 그 창이 걸친 분기)만큼이라 수백 건을 넘지 않는다.
    """
    cand = [(str(r[0]), str(r[1] or ""), str(r[2] or ""), str(r[3] or "")) for r in con.execute(
        "SELECT DISTINCT rcept_no, corp_code, report_nm, rcept_dt FROM dart_disclosure "
        "WHERE collected_at >= ? AND rcept_dt < ? AND stock_code <> '' ORDER BY rcept_no",
        (since_ts, date_yyyymmdd))]
    if not cand:
        return []
    dates = sorted({d for *_x, d in cand})
    marks = ",".join("?" * len(dates))             # IN 절은 자리표시자만 조립한다
    first_seen = {str(r[0]): str(r[1] or "") for r in con.execute(
        f"SELECT rcept_no, MIN(collected_at) FROM dart_disclosure "
        f"WHERE rcept_dt IN ({marks}) GROUP BY rcept_no", dates)}
    # `collected_at` 이 비어 있으면 "" < since_ts 라 옛 공시로 본다 — 안전한 쪽(재호출 안 함)
    return [(rno, corp, nm) for rno, corp, nm, _dt in cand
            if first_seen.get(rno, "") >= since_ts]


def plan(con: sqlite3.Connection, date_yyyymmdd: str, *,
         since_ts: str | None = None) -> DailyPlan:
    """상장사 공시를 분류해 재호출 유닛을 만든다. 콜은 나가지 않는다(CQS — 조회 전용).

    대상은 (a) 접수일이 D 인 공시 ∪ (b) `since_ts`(이번 런의 스윕 시작 시각) 이후에 처음
    관측된 접수일 D 이전 공시다. `since_ts=None`(= `--skip-sweep`)이면 (b) 는 빈 집합이라
    종전과 같다. 분류·유닛 생성 규칙은 (a)·(b) 에 똑같이 걸린다 — 늦게 왔다고 다르게
    다룰 이유가 없고, 다르게 다루면 어느 축이 빠졌는지 나중에 알 수 없다.
    """
    rows = con.execute(
        "SELECT DISTINCT rcept_no, corp_code, report_nm FROM dart_disclosure "
        "WHERE rcept_dt = ? AND stock_code <> '' ORDER BY rcept_no",
        (date_yyyymmdd,)).fetchall()
    n_filings = len({str(r[0]) for r in rows})
    n_rows_on_d = len(rows)                        # 게이트가 쓰는 값 — (a) 기준으로 굳힌다
    late = [] if since_ts is None else late_rows(con, date_yyyymmdd, since_ts)
    late_nos = tuple(sorted({r[0] for r in late}))
    # (a) 는 rcept_dt = D, (b) 는 rcept_dt < D 라 두 집합은 서로소다 — 합쳐도 중복이 없다
    rows = [*rows, *late]

    units: set[Unit] = set()
    periodic: set[tuple[str, str, str]] = set()
    major: set[str] = set()
    holder: set[str] = set()
    unresolved: list[tuple[str, str]] = []
    bad: list[tuple[str, str]] = []
    counts: dict[str, int] = {k.value: 0 for k in Kind}

    for rcept_no, corp_code, report_nm in rows:
        rno, corp, nm = str(rcept_no), str(corp_code or ""), str(report_nm or "")
        c = classify(nm)
        counts[c.kind.value] += 1
        if c.kind in (Kind.OTHER, Kind.CORRECTION):
            continue
        if not (len(corp) == 8 and corp.isdigit()):
            # 8자리 숫자가 아니면 `backfill_dart.py:600` 이 전량 거부한다. 조용히 섞지 않는다.
            bad.append((rno, corp))
            continue
        if c.kind is Kind.PERIODIC:
            if not c.resolved or c.bsns_year is None or c.reprt_code is None:
                unresolved.append((rno, nm))
                continue
            periodic.add((corp, c.bsns_year, c.reprt_code))
            units.update(Unit(ep, corp, c.bsns_year, c.reprt_code) for ep in PERIODIC_ENDPOINTS)
        elif c.kind is Kind.MAJOR:
            major.add(corp)
            units.update(Unit(ep, corp, "", "") for ep in DS005_ENDPOINTS)
        else:
            holder.add(corp)
            units.update(Unit(ep, corp, "", "") for ep in HOLDER_ENDPOINTS)

    return DailyPlan(date=date_yyyymmdd, n_rows=n_rows_on_d, n_filings=n_filings,
                     units=tuple(sorted(units)), kind_counts=counts,
                     periodic_units=tuple(sorted(periodic)), major_corps=tuple(sorted(major)),
                     holder_corps=tuple(sorted(holder)), unresolved=tuple(unresolved),
                     bad_corp_codes=tuple(bad), n_late=len(late_nos),
                     late_rcept_nos=late_nos[:LATE_SAMPLE_MAX])


# ── 3. 종결 해제 ─────────────────────────────────────────────────────────────────
def unlock(con: sqlite3.Connection, units: Iterable[Unit]) -> int:
    """대상 유닛의 `ingest_log` 행을 지운다. 지운 행수를 돌려준다.

    `ok` 는 축 무관 영구 종결이라(`backfill_dart.py:674-681`) 행이 남아 있으면 정정본도
    신규 DS005 사건도 영원히 안 들어온다. `fs_div` 는 **키에서 뺀다** — 백필의 `done` 집합이
    `(name, corp, bsns_year, reprt_code)` 4튜플이라 `fin` 의 CFS/OFS 두 행 중 하나만 남으면
    그 유닛이 되살아나지 않는다.
    """
    n = 0
    for u in units:
        cur = con.execute(
            "DELETE FROM ingest_log WHERE name=? AND corp_code=? AND bsns_year=? AND reprt_code=?",
            (u.endpoint, u.corp_code, u.bsns_year, u.reprt_code))
        n += cur.rowcount
    con.commit()
    return n


# ── 4. 예산 ──────────────────────────────────────────────────────────────────────
def kst_midnight_utc(con: sqlite3.Connection) -> str:
    """오늘 KST 자정을 `dart_call_log.ts` 와 같은 UTC 축으로. `backfill_docs.kst_cut` 과 동일."""
    row = con.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%S','now','+9 hours','start of day','-9 hours')"
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("sqlite strftime returned no row for the KST midnight cut")
    return str(row[0])


def budget(con: sqlite3.Connection, *, since_ts: str,
           limit: int | None = None) -> BudgetStatus:
    """창 안의 키별 콜 수. 우리 키 합계와 `kael` 을 따로 센다(findings B §7-5)."""
    counts = {str(k): int(n) for k, n in con.execute(
        "SELECT key_id, COUNT(*) FROM dart_call_log WHERE ts > ? GROUP BY key_id", (since_ts,))}
    return BudgetStatus(ours=sum(v for k, v in counts.items() if k != KAEL_KEY_ID),
                        kael=counts.get(KAEL_KEY_ID, 0),
                        limit=OUR_QUOTA_LIMIT if limit is None else limit, since_ts=since_ts)


# ── 5. 완료 판정 (findings B §7-1 ① · §7-2 · §7-3) ────────────────────────────────
def check(con: sqlite3.Connection, daily_plan: DailyPlan, *, since_ts: str,
          trading_day: bool = True) -> tuple[Gate, ...]:
    """"하루가 끝났는가" 판정. 근거는 **원장 행과 오늘 나간 콜**뿐이다.

    `ingest_log.status` 는 어느 항에서도 읽지 않는다 — 열린 분기 창은 정상 운영에서도
    영구 `mismatch` 라(DEFECT-B01) 그걸 게이트에 걸면 매일 실패로 보인다. 정기보고서·
    주요사항의 "따라갔는가" 도 `status='ok'` 가 아니라 `dart_call_log` 로 본다: `ok` 는
    영구 종결이라 **어제 이미 ok 였던 유닛**이 오늘 재호출 없이도 통과해 버린다.
    """
    return (_gate_filings(con, daily_plan, trading_day=trading_day),
            _gate_periodic(con, daily_plan, since_ts=since_ts),
            _gate_major(con, daily_plan, since_ts=since_ts),
            _gate_docs(con, daily_plan))


def _gate_filings(con: sqlite3.Connection, daily_plan: DailyPlan, *, trading_day: bool) -> Gate:
    row = con.execute(
        "SELECT COUNT(DISTINCT rcept_no), "
        "COALESCE(SUM(CASE WHEN stock_code <> '' THEN 1 ELSE 0 END), 0) "
        "FROM dart_disclosure WHERE rcept_dt = ?", (daily_plan.date,)).fetchone()
    n_filings, n_listed = (0, 0) if row is None else (int(row[0]), int(row[1]))
    metrics = {"n_filings": n_filings, "n_listed_rows": n_listed}
    if not trading_day:
        return Gate("filings", True, f"non-trading day, n_filings={n_filings} (0 is normal)",
                    metrics)
    ok = n_filings >= GATE_MIN_FILINGS and n_listed >= GATE_MIN_LISTED
    return Gate("filings", ok,
                f"n_filings={n_filings} (>= {GATE_MIN_FILINGS}) "
                f"n_listed_rows={n_listed} (>= {GATE_MIN_LISTED}) date={daily_plan.date}",
                metrics)


def _gate_periodic(con: sqlite3.Connection, daily_plan: DailyPlan, *, since_ts: str) -> Gate:
    called = {(str(c), str(y), str(r)) for c, y, r in con.execute(
        "SELECT DISTINCT corp_code, bsns_year, reprt_code FROM dart_call_log "
        "WHERE endpoint = ? AND ts > ?", (FIN_CALL_EP, since_ts))}
    want = set(daily_plan.periodic_units)
    missing = sorted(want - called)
    metrics = {"n_new_periodic": len(want), "n_fin_recalled": len(want) - len(missing),
               "n_unresolved": len(daily_plan.unresolved)}
    ok = not missing and not daily_plan.unresolved
    return Gate("periodic_followed", ok,
                f"units={len(want)} recalled={len(want) - len(missing)} "
                f"unresolved={len(daily_plan.unresolved)} "
                f"missing={missing[:5]}{'…' if len(missing) > 5 else ''}", metrics)


def _gate_major(con: sqlite3.Connection, daily_plan: DailyPlan, *, since_ts: str) -> Gate:
    marks = ",".join("?" * len(DS005_CALL_EPS))
    called = {str(r[0]) for r in con.execute(
        f"SELECT DISTINCT corp_code FROM dart_call_log "        # IN 절은 자리표시자만 조립한다
        f"WHERE endpoint IN ({marks}) AND ts > ?", (*DS005_CALL_EPS, since_ts))}
    want = set(daily_plan.major_corps)
    missing = sorted(want - called)
    metrics = {"n_corp_with_major": len(want), "n_corp_recalled": len(want) - len(missing)}
    return Gate("major_followed", not missing,
                f"corps={len(want)} recalled={len(want) - len(missing)} "
                f"missing={missing[:5]}{'…' if len(missing) > 5 else ''}", metrics)


_DOC_TARGET_SQL = """
WITH tgt AS (
  SELECT DISTINCT rcept_no FROM dart_disclosure
  WHERE rcept_dt = ? AND stock_code <> ''
    AND (report_nm LIKE '%주식분할결정%' OR report_nm LIKE '%주식병합결정%'
         OR (report_nm NOT LIKE '%연장신고%' AND
             (report_nm LIKE '%사업보고서%' OR report_nm LIKE '%반기보고서%'
              OR report_nm LIKE '%분기보고서%'))))
SELECT COUNT(*),
       COALESCE(SUM(CASE WHEN s.zip_ok = 1 THEN 1 ELSE 0 END), 0),
       COALESCE(SUM(CASE WHEN s.rcept_no IS NULL THEN 1 ELSE 0 END), 0),
       COALESCE(SUM(CASE WHEN s.zip_ok = 0 AND COALESCE(s.http_status,'') <> '014'
                         THEN 1 ELSE 0 END), 0)
FROM tgt t LEFT JOIN doc_store s USING (rcept_no)
"""


def _gate_docs(con: sqlite3.Connection, daily_plan: DailyPlan) -> Gate:
    row = con.execute(_DOC_TARGET_SQL, (daily_plan.date,)).fetchone()
    n_target, n_ok, n_never, n_bad = (0, 0, 0, 0) if row is None else tuple(int(x) for x in row)
    metrics = {"n_target": n_target, "n_ok": n_ok, "n_never_tried": n_never,
               "n_failed_other": n_bad}
    # `http_status='014'`(DART 파일없음, 전부 정정공시)는 허용 실패다 — findings B §2-2.
    return Gate("documents", n_never == 0 and n_bad == 0,
                f"n_target={n_target} n_ok={n_ok} n_never_tried={n_never} "
                f"n_failed_other={n_bad} (014 는 허용)", metrics)


# ── 6. 하위 도구 실행 ────────────────────────────────────────────────────────────
def _exec(cmd: Sequence[str], *, cwd: str) -> int:
    """하위 도구 1회 실행. 표준출력은 상속한다(크론 로그에 그대로 남는다).

    테스트는 이 함수를 갈아끼워 실제 콜 없이 인자만 기록한다.
    """
    print("  $ " + " ".join(cmd), flush=True)
    return subprocess.run(list(cmd), cwd=cwd, check=False).returncode


def to_date(date_yyyymmdd: str) -> dt.date:
    """'YYYYMMDD' → date. 시각축이 없는 값이라 naive datetime 을 거치지 않는다."""
    if not re.fullmatch(r"\d{8}", date_yyyymmdd):
        raise ValueError(f"date must be 8 digits YYYYMMDD: got {date_yyyymmdd!r}")
    return dt.date(int(date_yyyymmdd[:4]), int(date_yyyymmdd[4:6]), int(date_yyyymmdd[6:]))


# 직전 분기를 함께 쓸어담는 기간(일). 접수일이 지난 공시가 뒤늦게 목록에 나타난다 — 09-10 스윕에서
# 08-25 접수 8건이 처음 나타났다(검수 D H3, 16일 지연). 분기 첫 30일은 직전 분기 창도 page 1 부터
# 다시 받는다(`sweep_disclosure --lookback-days`). 비용은 그 기간에 하루 ≈600콜.
SWEEP_LOOKBACK_DAYS = 30


def sweep_from_for(date_yyyymmdd: str, lookback_days: int = SWEEP_LOOKBACK_DAYS) -> str:
    """D − lookback 이 속한 표준 분기 창부터. 좁히면 창 id 가 달라져 원장에 중복 적재된다
    (`sweep_disclosure.py:82-90` 주석)."""
    d = to_date(date_yyyymmdd) - dt.timedelta(days=lookback_days)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _ep_rank(endpoint: str) -> tuple[int, str]:
    return (_EP_ORDER.get(endpoint, len(_EP_ORDER)), endpoint)


def group_units(units: Iterable[Unit]) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """`(bsns_year, reprt_code, endpoints, corps)` 로 묶는다 — subprocess 횟수를 최소화한다.

    `backfill_dart.py` 는 corps × endpoints 를 교차로 돈다. 그래서 corp 집합이 **정확히 같은**
    엔드포인트끼리만 한 호출로 묶을 수 있다. 정기보고서 7종·DS005 15종·지분 2종은 각각
    같은 corp 집합에서 나오므로 실제로는 (연도, 보고서종류) 그룹당 1회로 접힌다.

    순서는 **corp_year 축(정기보고서) 먼저**다. 반기·사업보고서 마감일에는 정기보고서가
    ~18,200콜로 그날 예산의 대부분을 차지하는데(findings B §3-5), 값싼 DS005 를 앞에 두면
    한도에 걸렸을 때 정작 재무가 통째로 밀린다.
    """
    by_axis: dict[tuple[str, str], dict[str, set[str]]] = {}
    for u in units:
        by_axis.setdefault((u.bsns_year, u.reprt_code), {}).setdefault(u.endpoint, set()) \
            .add(u.corp_code)
    out: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for (year, reprt), by_ep in sorted(by_axis.items(), key=lambda kv: (not kv[0][0], kv[0])):
        merged: dict[tuple[str, ...], list[str]] = {}
        for ep in sorted(by_ep, key=_ep_rank):
            merged.setdefault(tuple(sorted(by_ep[ep])), []).append(ep)
        for corps_key, eps in sorted(merged.items()):
            out.append((year, reprt, tuple(eps), corps_key))
    return out


def _backfill_cmd(home: str, corps_path: str, endpoints: Sequence[str],
                  bsns_year: str, reprt_code: str) -> list[str]:
    cmd = [sys.executable, os.path.join(home, "src", "backfill_dart.py"),
           "--corps", corps_path, "--only", ",".join(endpoints),
           "--quota-window", "midnight"]
    if bsns_year:                      # corp·corp_range 축은 연도축이 없다 — 인자를 주지 않는다
        cmd += ["--years", bsns_year, "--reprt", reprt_code]
    return cmd


def _write_corps(tmpdir: str, tag: str, corps: Sequence[str]) -> str:
    """`--corps` 목록 파일. 탭이 없으면 `backfill_dart.py:585-595` 가 상장구간 게이팅을 끈다."""
    path = os.path.join(tmpdir, f"corps_{tag}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(corps) + "\n")
    return path


# ── 7. 러너 ──────────────────────────────────────────────────────────────────────
_REQUIRED_TABLES = ("dart_disclosure", "ingest_log", "dart_call_log", "doc_store")


def _open_dart(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    have = {str(r[0]) for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in _REQUIRED_TABLES if t not in have]
    if missing:
        con.close()
        raise RuntimeError(f"dart.db is missing tables {missing} — db={db_path} "
                           f"present={sorted(have)[:10]}; run the backfill tools once first")
    return con


def default_home() -> str:
    return os.environ.get("QL_HOME") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(date_yyyymmdd: str, *, home: str, skip_sweep: bool = False,
        sweep_from: str | None = None, max_docs: int = DEFAULT_MAX_DOCS,
        dry_run: bool = False, limit: int = 5, trading_day: bool = True) -> DartDailyResult:
    """D일 증분 1회. 순서 = 스윕 → 계획 → 종결 해제 → 상세 재호출 → 문서 ZIP → 판정.

    `dry_run` 은 플랜 §4 공통 정의를 따른다 — 콜은 `limit` 만큼 실제로 하되 원장·`daily_run.db`
    에 쓰지 않는다. 여기서 "쓰지 않는다" 는 `unlock`(= `ingest_log` 삭제)과 runlog 기록을
    건너뛰는 것이다. 종결 해제가 없으므로 백필은 이미 `ok` 인 유닛을 그냥 건너뛴다.
    """
    db = os.path.join(home, "data", "raw", "dart.db")
    con = _open_dart(db)
    try:
        started_ts = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
        window_ts = kst_midnight_utc(con)
        pre = budget(con, since_ts=window_ts)
        if not pre.ok:
            status = (RunStatus.KAEL_KEY_USED if pre.kael_used else RunStatus.BUDGET_EXCEEDED)
            reason = ("v3 production key 'kael' was used — stop and investigate"
                      if pre.kael_used else f"our keys exceeded {pre.limit:,} calls today")
            return DartDailyResult(date_yyyymmdd, status,
                                   DailyPlan(date_yyyymmdd, 0, 0, (), {}), pre,
                                   detail=f"{reason}: {pre.describe()}")

        run_id: int | None = None
        if not dry_run:
            run_id = runlog.start(os.path.join(home, "data", "raw", "daily_run.db"),
                                  date=date_yyyymmdd, source="dart")
        rc_sweep: int | None = None
        rc_backfill: list[int] = []
        rc_docs: int | None = None
        n_unlocked = 0
        sweep_started_ts: str | None = None
        try:
            if not skip_sweep:
                cmd = [sys.executable, os.path.join(home, "src", "sweep_disclosure.py"),
                       "--from", sweep_from or sweep_from_for(date_yyyymmdd),
                       "--lookback-days", str(SWEEP_LOOKBACK_DAYS),
                       "--quota-window", "midnight"]
                if dry_run:
                    cmd += ["--max-calls", str(limit)]
                # 스윕이 적재할 행의 `collected_at` 은 이 시각 이후다. 기동 **직전**에 잡는다 —
                # 뒤에 잡으면 스윕 도중 들어온 늦은 공시가 창 밖으로 떨어진다.
                sweep_started_ts = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
                rc_sweep = _exec(cmd, cwd=home)

            daily_plan = plan(con, date_yyyymmdd, since_ts=sweep_started_ts)
            groups = group_units(daily_plan.units)
            if not dry_run:
                n_unlocked = unlock(con, daily_plan.units)

            with tempfile.TemporaryDirectory(prefix="dart_daily_") as tmpdir:
                for i, (year, reprt, eps, corps) in enumerate(groups):
                    use = corps[:limit] if dry_run else corps
                    if not use:
                        continue
                    path = _write_corps(tmpdir, f"{i:03d}", use)
                    rc_backfill.append(
                        _exec(_backfill_cmd(home, path, eps, year, reprt), cwd=home))

            docs_cap = min(max_docs, limit) if dry_run else max_docs
            rc_docs = _exec([sys.executable, os.path.join(home, "src", "backfill_docs.py"),
                             "--max-calls", str(docs_cap)], cwd=home)

            post = budget(con, since_ts=window_ts)
            n_calls = int(con.execute("SELECT COUNT(*) FROM dart_call_log WHERE ts > ?",
                                      (started_ts,)).fetchone()[0])
            gates = check(con, daily_plan, since_ts=started_ts, trading_day=trading_day)
            bad_rc = [rc for rc in (rc_sweep, rc_docs, *rc_backfill) if rc not in (None, 0)]
            status, detail = _verdict(post, gates, bad_rc, dry_run=dry_run)
            result = DartDailyResult(date_yyyymmdd, status, daily_plan, post, gates,
                                     rc_sweep, tuple(rc_backfill), rc_docs, n_unlocked,
                                     n_calls, detail)
        except BaseException as e:
            if run_id is not None:
                runlog.finish(os.path.join(home, "data", "raw", "daily_run.db"), run_id,
                              status="error", detail=f"{type(e).__name__}: {e}")
            raise
        if run_id is not None:
            runlog.finish(os.path.join(home, "data", "raw", "daily_run.db"), run_id,
                          status=result.status.value, n_calls=result.n_calls,
                          n_rows=result.plan.n_filings, detail=result.detail)
        return result
    finally:
        con.close()


def _verdict(post: BudgetStatus, gates: Sequence[Gate], bad_rc: Sequence[int], *,
             dry_run: bool) -> tuple[RunStatus, str]:
    if post.kael_used:
        return RunStatus.KAEL_KEY_USED, f"v3 production key used during the run: {post.describe()}"
    if post.over_limit:
        return RunStatus.BUDGET_EXCEEDED, f"our key budget exceeded: {post.describe()}"
    if bad_rc:
        return RunStatus.TOOL_FAILED, f"sub-tool returned non-zero: rc={list(bad_rc)}"
    if dry_run:
        failed = [g.name for g in gates if not g.ok]
        return RunStatus.DRY_RUN, f"dry-run — gates not enforced (would fail: {failed})"
    failed = [g.name for g in gates if not g.ok]
    if failed:
        return RunStatus.GATE_FAILED, "; ".join(f"{g.name}: {g.detail}" for g in gates if not g.ok)
    return RunStatus.OK, f"all gates passed; {post.describe()}"


# ── 8. CLI ───────────────────────────────────────────────────────────────────────
def _parse_date(s: str) -> str:
    t = s.strip().replace("-", "")
    if not re.fullmatch(r"\d{8}", t):
        raise ValueError(f"--date must be YYYYMMDD or YYYY-MM-DD: got {s!r}")
    to_date(t)                                    # 존재하지 않는 날짜(0230 등)를 여기서 거른다
    return t


def report(result: DartDailyResult) -> str:
    p = result.plan
    lines = [f"── DART 일일 증분 {result.date} · {result.status.value} (rc {result.exit_code})",
             (f"  공시 {p.n_filings:,}건(상장사 행 {p.n_rows:,}) · late={p.n_late:,} · "
              f"분류 {p.kind_counts}"),
             (f"  유닛 {len(p.units):,} · 콜 추정 {p.n_calls_est:,} · "
              f"해제 {result.n_unlocked:,} · 실제 콜 {result.n_calls:,}"),
             f"  예산 {result.budget.describe()}"]
    if p.n_late:
        # 늦게 등장한 공시 = 접수일이 D 이전인데 이번 스윕에서 처음 본 것. 게이트는 아니다
        lines.append(f"  ↻ 늦게 등장 {p.n_late:,}건 (표본 {len(p.late_rcept_nos)}): "
                     f"{list(p.late_rcept_nos)}")
    if p.unresolved:
        lines.append(f"  ⚠ 기간 라벨 미해석 {len(p.unresolved)}건: {p.unresolved[:3]}")
    if p.bad_corp_codes:
        lines.append(f"  ⚠ corp_code 형식 오류 {len(p.bad_corp_codes)}건: {p.bad_corp_codes[:3]}")
    lines += [f"  {'○' if g.ok else '✖'} {g.name}: {g.detail}" for g in result.gates]
    if result.detail:
        lines.append(f"  → {result.detail}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DART 일일 증분 (플랜 Task 1.5)")
    ap.add_argument("--date", default="", help="대상 접수일 YYYYMMDD (기본: 직전 거래일)")
    ap.add_argument("--skip-sweep", action="store_true",
                    help="공시 스윕 생략 — 갭 메우기에서 날짜를 연속으로 돌 때")
    ap.add_argument("--sweep-from", default="", help="스윕 시작 창 YYYYQn (기본: D 가 속한 분기)")
    ap.add_argument("--max-docs", type=int, default=DEFAULT_MAX_DOCS,
                    help=f"문서 ZIP 콜 상한 (기본 {DEFAULT_MAX_DOCS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="콜은 --limit 만큼 하되 ingest_log·daily_run.db 에 쓰지 않는다")
    ap.add_argument("--limit", type=int, default=5, help="dry-run 의 그룹당 corp 수 (기본 5)")
    ap.add_argument("--json", action="store_true", help="게이트 지표를 JSON 으로도 출력")
    a = ap.parse_args(argv)

    home = default_home()
    calendar = daily_calendar.load()
    if a.date:
        date_yyyymmdd = _parse_date(a.date)
    else:
        today_kst = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9)).date()
        date_yyyymmdd = calendar.prev_trading_day(today_kst).strftime("%Y%m%d")
    if calendar.detail:
        print(f"  ⚠ {calendar.detail}", flush=True)
    trading_day = calendar.is_trading_day(to_date(date_yyyymmdd))

    result = run(date_yyyymmdd, home=home, skip_sweep=a.skip_sweep,
                 sweep_from=a.sweep_from or None, max_docs=a.max_docs,
                 dry_run=a.dry_run, limit=a.limit, trading_day=trading_day)
    print(report(result))
    if a.json:
        payload: dict[str, object] = {g.name: {"ok": g.ok, **g.metrics} for g in result.gates}
        # `plan` 은 게이트가 아니다 — 늦게 등장한 공시는 세어서 남기기만 한다(플랜 v2 A.2 Step 5)
        payload["plan"] = {"n_filings": result.plan.n_filings, "n_late": result.plan.n_late,
                           "late_rcept_no": list(result.plan.late_rcept_nos)}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
