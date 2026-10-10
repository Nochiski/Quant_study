"""두 판 대조 — 장 마감 판 T 대 다음 날 연구 판 T(컷오버 트랙 PR-7 · 판정 기준 T-36).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 PR-7 · T-36 · P5('장 마감 판 60거래일 재생 대 연구 판 —
차이가 등록 범주에 다 들어감, 미설명 0') · §4(과거 재생 + 실운영 3거래일).

입력(읽기만 한다)
  장 마감 판 E    <evening-root>/{factor_inputs,model}/_runs/<T>_evening.json (운영 `data/model_db`, T-3)
                  + 장 마감 stage T 행 판(fi 판 기록 `postclose_stage_root`·`postclose_builds`, PR-5·T-29)
  연구 판 T  R    <research-root>/{factor_inputs,model}/_runs/<T>_morning.json(다음 날 08:10 확정판)
                  + 그 판의 equity `adj_factor`(fi 판 기록 `equity_root`·`equity_builds`) — (D', T] 공개 사건
  연구 판 D' P    <research-root>/factor_inputs/_runs/<D'>_morning.json — 3자 대조의 기준
  판정 달력       `daily.calendar`(기본 <research-root>/calendar = 운영 data/calendar) — D' = T 직전 거래일

판정(범주는 증거가 있을 때만 — T-36). fi 8표는 해시가 같으면 끝이고, 다르면 grain 으로 맞대어 다른 칸마다
`CATEGORIES` 의 범주 하나 또는 미설명(`unexplained`)으로 나눈다. 새 범주는 만들지 않는다.
  · 이월·정보 시점·filing_late: 3자 대조 — E = P(장 마감 판이 D' 를 그대로 실었다)이고 R ≠ P(T 에 바뀌었다).
    정보 표는 연구 판 수집·공개일이 장 마감 판보다 늦어야 하고, 장 마감 판에 D' 뒤 정보가 있으면 미설명.
    연초 첫 거래일(D' 와 T 의 해가 다름)의 연도 창 차이만 3자 대조 없이 인정한다.
  · T 행: 종가 차이는 실운영 미설명(N-35 ①), `--replay`(T 행 = 21:05 원장)에서만 종가 정의. 거래량은
    장 마감 값 ≤ 연구 값이면 거래량 정의. 수급은 주체별 판 통계(중앙 상대 차이·부호 반전)가 상한 안이면
    수급 정의. T 가격 없음은 장 마감 stage 증거(행 없음·price_valid 참 아님)가 있어야 16:00 컷오프, 수집
    대상(수집기 ① `daily.postclose.fi_candidates` · ② V3_STOCK_FILTER) 밖이면 '수집 대상 밖'. T-6 보류는
    연구 판에 당일 기업행위 흔적이 있어야 한다. 수정주가 계수·표식은 (D', T] 에 공개된 기업행위가 있어야
    정보 시점(T 전 행은 적용일부터 T−1 까지 끝 구간 모양).
  · T 전 가격·수급 행은 두 판이 같아야 한다 — 다르면 미설명.
  · 표 하나의 차이 행이 `DIFF_ROW_MAX` 를 넘으면 행 분류를 하지 않고 SQL 로 열별로 센 뒤 전부 미설명이다.
모델 층: spec 마다 종합점수 Spearman · 엑셀 후보(`deliver.view.load_day`) 겹침 · 점수 열 |Δ| 상위 종목과 그
종목의 fi 범주(그 spec 엔진이 읽는 표만). 한 판에만 있는 점수 행은 적격성 범주(종목 집합·eligible·시총·
T 가격)로만, 한쪽만 종합점수가 빈 행은 자기 fi 차이로만 설명한다. fi 8표가 같은데 점수가 다르거나 spec 이
한 판에만 있으면 미설명이다.

rc: 0 = 미설명 0 이고 모든 spec Spearman ≥ 하한 / 1 = 미설명 있음 또는 하한 미달(셀 수 없음 포함) /
2 = 입력 오류(판 없음·실패 판·date·basis·asof·달력·규칙 판본·스키마·증거 원천 없음·보고서 쓰기 실패·
예상 밖 예외). 산출: 표준 출력 한 화면 요약 + `<out-root>/compare/<T>.json`(기본 out-root =
--evening-root). X-2 연속 창 집계·실운영 3거래일 판정이 읽는다 — rc 2 도 `verdict: error` 로 남긴다.

사용: PYTHONPATH=src python -m daily.board_compare --date YYYYMMDD --evening-root data/model_db \\
          --research-root data [--calendar-dir DIR] [--replay] [--out-root DIR] \\
          [--spearman-min 0.975]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
from compat.mappings import V3_STOCK_FILTER
from deliver.reader import DeliverError, ModelRun, load_run
from deliver.view import DayView, composite_of, load_day
from equity import inputs as eq_inputs
from factor_inputs import queries as fiq
from factor_inputs.build import DUCKDB_MEMORY_LIMIT, DUCKDB_THREADS, _content_hash, _write_json
from model import gates as mgates
from model.build import ModelBuildError, _resolve_fi
from model.contracts import FI_TABLES
from stage import manifest

from daily import calendar as daily_calendar
from daily import kw_daily

log = logging.getLogger(__name__)

TOOL = "daily.board_compare"
SCHEMA = 2                     # compare/<T>.json 모양 판본 — X-2 가 읽는다. 키를 바꾸면 올린다
# 점수 Spearman 하한(T-36) — P5 장 마감 판 대 연구 판 행의 통과 기준은 미설명 0 이고, 이 하한은 임시다
# (정본 P5 v3 소비자 행의 0.975~0.995 아래 끝). P5 재생을 `--spearman-min 0`(기록형)으로 돌려 잰 분포로
# spec 별로 등록하면 이 상수와 이 주석을 함께 고친다.
SPEARMAN_MIN = 0.975
# 수급 정의 상한(T-36, 임시 굵은 상한) — 주체별 T 행 판 통계가 넘으면 그 주체 차이 전부 미설명.
# 근거 N-35 ②: 15:40 정규장 대 21:05 하루 전체, 중앙 |차이|/|연구값| 외국인 0.8~2.6%·개인 1.1~2.9%·
# 기관 0~0.1%, 부호 반전 0~2/100. 단위·부호·주체 뒤바뀜(×1e6·열 바뀜)은 100% 수준이라 잡힌다.
# P5 재생 분포로 조인다(조이면 이 주석과 함께 고친다).
FLOW_REL_MAX = 0.10
FLOW_FLIP_MAX = 0.10
FLOW_STAT_MIN_N = 20           # 이보다 적게 관측된 주체는 판정하지 않는다(중앙값이 몇 칸의 잡음)
# 표 하나의 차이 행 상한 — 정상인 날은 T 행(≈ 유니버스 2,800)·신규 상장 종목 행·정보 표 판 교체(수만)뿐이다.
# 넘으면 행 분류 없이 SQL 로 열별로 세고 표본만 올린다(전부 미설명 — 그런 날은 어차피 실패다)
DIFF_ROW_MAX = 100_000
LIST_N = 20                    # 요약의 미설명 목록·종합점수 |Δ| 상위 종목 수
FACTOR_TOP_N = 5               # 점수 열마다 |Δ| 상위 종목 수(JSON)
SAMPLE_N = 20                  # 범주마다 JSON 에 싣는 표본 기록 수
UNEXPLAINED_MAX = 1000         # JSON 에 싣는 미설명 기록 상한(넘으면 개수만)
SCORE_TOL = 1e-9               # 점수 |Δ| 를 '다르다' 고 볼 절대 허용치(model.compare 와 같은 값)
CROSS_SECTION = "교차 단면 — 자기 fi 입력 차이 없음(다른 종목 차이가 표준화·백분위로 번짐)"
# 장 마감 판 T 행 원천 표(PR-2·T-29) — fi 정본(PR-5)
T_SOURCE_TABLE = fiq.T_SOURCE_TABLE

# ── 등록 범주(한 곳) ──────────────────────────────────────────────────────────
CLOSE_DEF = "close_definition"
VOLUME_DEF = "volume_definition"
FLOW_DEF = "flow_definition"
CARRY = "carry"
INFO = "info_timing"
T6 = "corp_action_pending"
CUTOFF = "postclose_cutoff"
NOT_TARGETED = "not_targeted"
CREDIT_T = "credit_available_t"
FY = "fy_window"
FILING = "filing_late"
UNEXPLAINED = "unexplained"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    basis: str              # 정본 근거(결정 번호·문서 절)
    definition: str


CATEGORIES: dict[str, Category] = {c.key: c for c in (
    Category(CLOSE_DEF, "종가 정의", "T-36·N-35 ①·T-33",
             "재생(`--replay`)에서만 — T 행이 21:05 원장(애프터마켓 마지막 체결가)이라 KRX 공식 종가와 "
             "다르다. 실운영 T 종가 차이는 미설명이다(15:35 회차부터 공식 종가와 전 종목 일치). 시총·"
             "수정종가·수정주가 표식의 T 행 차이도 T 종가가 다를 때 여기 든다"),
    Category(VOLUME_DEF, "거래량 정의", "T-36·PR-5(T 행 volume = 수집 시점 누적)",
             "T 행 거래량 — 장 마감 판은 15:41 수집 시점 누적, 연구 판은 KRX 일 거래량(시간외·애프터마켓 "
             "포함)이라 장 마감 값 ≤ 연구 값일 때만. 엔진은 거래량을 읽지 않는다"),
    Category(FLOW_DEF, "수급 정의", "N-35 ②④·T-36",
             "T 행 수급 — 15:40 KRX 정규장 확정 수급 대 21:05 하루 전체 수급. 주체별 판 통계(중앙 "
             "|차이|/|연구값|·부호 반전 비율)가 상한 안이고 양쪽 값이 다 있을 때만"),
    Category(CARRY, "이월", "T-2·T-36·FACTOR_INPUTS §2-1·§7",
             "3자 대조 — 장 마감 판 값 = 연구 판 D' 값(D' universe_daily 이월·고정 판 마스터)이고 연구 판 "
             "T 값이 D' 와 다르다. T 신규 상장·폐지·정지 변경과 D' 주식수 시총이 여기 든다"),
    Category(INFO, "정보 시점", "T-2·T-5·T-36·FACTOR_INPUTS §2-1·PR-5 리뷰 MINOR-1",
             "3자 대조 — 장 마감 판 = 연구 판 D' 이고 연구 판 T 가 다르며, 정보 표는 연구 판 수집·공개일이 "
             "장 마감 판보다 늦다(WISE·DART·WICS·추정기관 수). 수정주가 계수·표식은 (D', T] 에 공개된 "
             "기업행위가 있을 때(T 전 행은 적용일부터 T−1 까지 끝 구간 모양)"),
    Category(T6, "T-6 당일 기업행위", "T-6·T-36",
             "장 마감 판 corp_action_pending 보류 — 연구 판 T 행 계수 ≠ D' 행 계수, 또는 연구 판 T 수익률이 "
             "가격제한폭(`queries.PRICE_LIMIT_*`) 밖일 때만"),
    Category(CUTOFF, "16:00 컷오프", "PR-1·N-35 ①③·N-42 Q3·T-36",
             "수집 대상인데 장 마감 stage 에 그 종목 T 행이 없거나 price_valid 가 참이 아니라(16:00 뒤 응답·"
             "NULL) T 가격(행이 없으면 수급도)이 없다 — no_price·시총 NULL. price_valid 참인데 종가·거래량이 "
             "가격 술어(`kw_daily.ka10060_postclose_price_usable_sql`)를 못 넘는 행은 여기 들지 않는다"),
    Category(NOT_TARGETED, "수집 대상 밖", "PR-1(수집 대상 ①직전 판 후보 ②V3_STOCK_FILTER)·T-36",
             "장 마감 수집 대상 밖(직전 판 모델 후보도 v3 유니버스 종목도 아님)이라 T 가격·수급이 없다"),
    Category(CREDIT_T, "신용 available_date ≤ T", "PR-4 리뷰·FACTOR_INPUTS §2-1(세션 축)",
             "신용잔고는 세션 축이라 available_date ≤ T(T 아침 실입수분)까지 싣는다 — available_date = T 인 "
             "행이 연구 판에만 있거나 값이 다를 때"),
    Category(FY, "연도 창 = T 의 연도", "PR-4 리뷰·T-36·FACTOR_INPUTS §2-1·§5",
             "연초 첫 거래일(D' 와 T 의 해가 다름) — 당해 12월기·연간 컨센서스 연도 창은 T 의 연도인데 "
             "장 마감 판의 WISE 자료는 D' 판이다. 3자 대조 없이 인정하는 유일한 예외"),
    Category(FILING, "filing_late 경계", "PR-4 리뷰·T-36·FACTOR_INPUTS §2-1·§4",
             "3자 대조 — 장 마감 판 = 연구 판 D'(또는 기한 ∈ (D', T] 라 장 마감 판 false·D' 판 NULL)이고 "
             "연구 판 T 가 다르다(T 공개 공시)"),
)}

# ── 제외 사유 어휘(fi `queries.EXCLUDE_REASONS` 와 같다 — 테스트가 대조) ─────────
NO_PRICE = "no_price"
CARRY_REASONS = ("sec_type", "market")
ESTIMATE_REASONS = ("estimates_lapsed", "estimates_none")
# T-6 제외 사유 — fi 어휘 `queries.EXCLUDE_REASONS` 의 장 마감 판 사유(PR-5, 테스트가 대조)
T6_REASON = "corp_action_pending"
# 장 마감 stage T 행의 상태 — 가격 술어는 fi T 가격 행·compat T 행과 같은 정본
# (`daily.kw_daily.ka10060_postclose_price_usable_sql`: price_valid 참 · 종가 > 0 · 거래량 있음, P4)
STAGE_USABLE = "usable"            # 가격을 쓸 수 있다 — 장 마감 판에 T 가격이 있어야 한다
STAGE_INVALID = "invalid"          # price_valid 가 참이 아니다(16:00 뒤 응답·NULL) — 16:00 컷오프
STAGE_UNUSABLE = "unusable"        # price_valid 참인데 종가·거래량이 술어를 못 넘는다 — 등록 범주 밖

# ── 열 묶음 ──────────────────────────────────────────────────────────────────
UNI_CARRY = frozenset({"name", "listed_date", "market", "sec_type", "shares", "adv20", "is_admin",
                       "is_halted"})
UNI_INFO = frozenset({"sector_l1", "sector_l1_name", "sector_l2", "sector_l2_name", "n_analysts",
                      "audit_adverse"})
UNI_WISE = ("has_estimates", "coverage_state", "coverage_age_days")    # 신선도 — 당해 12월기(fy)
UNI_ELIG = frozenset({"eligible", "exclude_reason", "market_cap"})     # 점수 대상 판정 재료
# 판 전체에서 대조하지 않는 열 — 판 기준 표식(장 마감 판 시총 기준, FG2·FG3 이 판마다 검사)
NOT_COMPARED: Mapping[str, tuple[str, ...]] = {"fi_universe": ("mktcap_basis",)}
# T 행에서 대조하지 않는 열 — 장 마감 원천(ka10060)에 시·고·저가·거래대금이 없어 NULL 이고(PR-5) 엔진은
# 종가만 읽는다. price_source 는 T-30 어휘('postclose' 대 'krx')라 구조상 다르다. T 전 행은 대조한다
T_ROW_NOT_COMPARED: Mapping[str, tuple[str, ...]] = {
    "fi_prices": ("open", "high", "low", "amount", "price_source")}
# 정보 표의 수집·공개일 열 — 장 마감 판은 ≤ D'(asof)
INFO_DATE_COL: Mapping[str, str] = {"fi_consensus": "fetched_date",
                                    "fi_consensus_annual": "fetched_date",
                                    "fi_fin_summary": "available_date"}
# 수정주가 미해결 사건 표식(adj_jump_ok 는 H1-4 · fi1.6.0)과 계수
ADJ_FLAG_COLS = frozenset({"adj_ok", "adj_jump_ok"})
ADJ_EVENT_COLS = ADJ_FLAG_COLS | {"adj_factor"}
DATED_TABLES = frozenset({"fi_prices", "fi_adj_prices", "fi_flows", "fi_credit"})


class CompareInputError(RuntimeError):
    """대조를 시작할 수 없는 입력(rc 2)."""


# ── 값 ───────────────────────────────────────────────────────────────────────
def _jsonable(v: object) -> object:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _same(a: object, b: object) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float | Decimal):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _lit(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(v: object) -> str | None:
    """'YYYY-MM-DD' · 'YYYYMMDD' · date → 'YYYY-MM-DD'."""
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


# ── 기록 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Finding:
    """차이 하나를 범주 하나로 분류한 기록(행 하나가 범주별로 여러 기록이 될 수 있다)."""

    table: str                       # fi 표 · 'model:<spec_id>' · 'boards'
    ticker: str
    key: dict[str, str]              # grain 열(종목 제외) → 값(ISO 문자열)
    kind: str                        # value | evening_only | research_only | bulk
    category: str
    columns: tuple[str, ...]         # 값이 다른 열(한 판에만 있는 행은 비어 있다)
    evening: dict[str, object]
    research: dict[str, object]
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"table": self.table, "ticker": self.ticker, "key": self.key, "kind": self.kind,
                "category": self.category, "columns": list(self.columns),
                "evening": self.evening, "research": self.research, "note": self.note}


@dataclass
class Tally:
    """범주별 건수·종목·열·표본 — 기록을 다 들고 있지 않는다(차이가 많은 날 메모리)."""

    count: Counter[str] = field(default_factory=Counter)
    by_table: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    by_column: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    tickers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    samples: dict[str, list[Finding]] = field(default_factory=lambda: defaultdict(list))
    unexplained: list[Finding] = field(default_factory=list)
    # 종목 → 표 → 범주(모델 추적 — spec 엔진이 읽는 표만 고른다)
    ticker_tables: dict[str, dict[str, set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    # 종목 → 점수 대상 판정에 닿는 범주(종목 집합·eligible·시총·T 가격)
    eligibility: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, f: Finding, n: int = 1, columns: Mapping[str, int] | None = None,
            tickers: Iterable[str] = ()) -> None:
        cat = f.category
        self.count[cat] += n
        self.by_table[cat][f.table] += n
        for col, k in (columns or {c: n for c in f.columns}).items():
            self.by_column[cat][f"{f.table}.{col}"] += k
        for tk in (*tickers, *([f.ticker] if f.ticker else [])):
            self.tickers[cat].add(tk)
            if f.table in FI_TABLES:
                self.ticker_tables[tk][f.table].add(cat)
        if f.ticker and _affects_eligibility(f):
            self.eligibility[f.ticker].add(cat)
        if len(self.samples[cat]) < SAMPLE_N:
            self.samples[cat].append(f)
        if cat == UNEXPLAINED and len(self.unexplained) < UNEXPLAINED_MAX:
            self.unexplained.append(f)

    @property
    def n_findings(self) -> int:
        return sum(self.count.values())

    def ticker_categories(self, engine: str | None = None) -> dict[str, list[str]]:
        """종목 → fi 범주. `engine` 을 주면 그 엔진이 읽는 표(계약 `readers`)의 차이만."""
        out: dict[str, list[str]] = {}
        for tk, tables in sorted(self.ticker_tables.items()):
            got = {c for t, cs in tables.items()
                   if engine is None or engine in FI_TABLES[t].readers for c in cs}
            if got:
                out[tk] = sorted(got)
        return out


def _affects_eligibility(f: Finding) -> bool:
    """점수 대상 판정에 닿는 차이 — 유니버스의 종목 집합·eligible·시총과 T 가격 행."""
    if f.table == "fi_universe":
        return f.kind != "value" or bool(set(f.columns) & UNI_ELIG)
    return f.table == "fi_prices" and f.key.get("is_t") == "1"


@dataclass(frozen=True)
class _Pair:
    """grain 으로 맞댄 행 — 장 마감(e)·연구 T(r)·연구 D'(d)."""

    key: dict[str, object]
    in_e: bool
    in_r: bool
    in_d: bool
    e: dict[str, object]
    r: dict[str, object]
    d: dict[str, object]
    diff: tuple[str, ...]            # e·r 둘 다 있을 때 값이 다른 대조 열

    @property
    def ticker(self) -> str:
        return str(self.key["ticker"])

    @property
    def day(self) -> date | None:
        v = self.key.get("date")
        return v if isinstance(v, date) else None

    def three_way(self, col: str) -> bool:
        """E = P(D' 그대로 실었다)이고 R ≠ P(T 에 바뀌었다)."""
        return (self.in_d and _same(self.e.get(col), self.d.get(col))
                and not _same(self.r.get(col), self.d.get(col)))


@dataclass(frozen=True)
class Evidence:
    """판 밖에서 읽는 증거."""

    stage: Mapping[str, str]                       # 장 마감 stage T 행 종목 → STAGE_* 상태
    new_events: Mapping[str, tuple[date, ...]]     # (D', T] 에 공개된 기업행위 → 적용일
    targeted: frozenset[str]                       # 장 마감 수집 대상(`read_targets`)


@dataclass
class _Ctx:
    t: date
    dprime: date
    replay: bool
    ev: Evidence
    uni_e: dict[str, dict[str, object]]
    uni_r: dict[str, dict[str, object]]
    uni_d: dict[str, dict[str, object]]
    tclose_e: dict[str, object]
    tclose_r: dict[str, object]
    rt_factor_t: dict[str, object]          # 연구 판 T 행 adj_factor
    rt_factor_dp: dict[str, object]         # 연구 판 D' 행 adj_factor
    rt_close_dp: dict[str, object]          # 연구 판 D' 행 종가
    flow_over: frozenset[str] = frozenset()
    max_e: dict[str, dict[str, object]] = field(default_factory=dict)
    max_r: dict[str, dict[str, object]] = field(default_factory=dict)
    member: dict[str, tuple[str, str]] = field(default_factory=dict)
    adj_tail: dict[tuple[str, str], bool] = field(default_factory=dict)

    @property
    def boundary(self) -> bool:
        """연초 첫 거래일 — D' 와 T 의 해가 다르다."""
        return self.t.year != self.dprime.year

    def reason_e(self, tk: str) -> object:
        return self.uni_e.get(tk, {}).get("exclude_reason")

    def cov_differs(self, tk: str) -> bool:
        e, r = self.uni_e.get(tk, {}), self.uni_r.get(tk, {})
        return any(not _same(e.get(c), r.get(c)) for c in UNI_WISE)

    def uni3(self, tk: str, col: str) -> bool:
        """유니버스 열 3자 대조(E = P, R ≠ P)."""
        e, r, d = self.uni_e.get(tk), self.uni_r.get(tk), self.uni_d.get(tk)
        return (e is not None and r is not None and d is not None
                and _same(e.get(col), d.get(col)) and not _same(r.get(col), d.get(col)))

    def close_differs(self, tk: str) -> bool:
        return not _same(self.tclose_e.get(tk), self.tclose_r.get(tk))

    def close_verdict(self) -> tuple[str, str]:
        if self.replay:
            return CLOSE_DEF, ""
        return UNEXPLAINED, "실운영 T 종가 차이(N-35 ① 전 종목 일치 — T-36)"

    def no_t_price(self, tk: str) -> tuple[str, str]:
        """장 마감 판에 T 가격이 없다 — 장 마감 stage 증거로만 설명한다."""
        state = self.ev.stage.get(tk)
        if state == STAGE_USABLE:
            return UNEXPLAINED, "장 마감 stage 에 쓸 수 있는 가격이 있는데 T 가격이 없다"
        if state == STAGE_UNUSABLE:
            return UNEXPLAINED, "price_valid 참인데 종가·거래량이 가격 술어를 못 넘는다 — 16:00 컷오프 아님"
        if state == STAGE_INVALID:
            return CUTOFF, "price_valid 참 아님"
        return (CUTOFF, "stage 행 없음") if tk in self.ev.targeted else (NOT_TARGETED, "")

    def halted_at_t(self, tk: str) -> bool:
        return self.uni3(tk, "is_halted") and self.uni_r.get(tk, {}).get("is_halted") is True

    def t6_evidence(self, tk: str) -> bool:
        """연구 판에 당일 기업행위 흔적 — T 행 계수 ≠ D' 행 계수 또는 T 수익률이 제한폭 밖."""
        ft, fd = _num(self.rt_factor_t.get(tk)), _num(self.rt_factor_dp.get(tk))
        if ft is not None and fd is not None and abs(ft - fd) > SCORE_TOL * max(1.0, abs(fd)):
            return True
        ct, cd = _num(self.tclose_r.get(tk)), _num(self.rt_close_dp.get(tk))
        if ct is None or cd is None or cd <= 0:
            return False
        before = self.t.isoformat() < fiq.PRICE_LIMIT_CHANGE_DATE
        limit = fiq.PRICE_LIMIT_BEFORE if before else fiq.PRICE_LIMIT_AFTER
        return abs(ct / cd - 1) > limit + fiq.PRICE_LIMIT_EPS


_Verdict = tuple[str, tuple[str, ...], str]          # (범주, 열, 메모)


def _group(items: Iterable[tuple[str, str, str]]) -> list[_Verdict]:
    """(열, 범주, 메모) → 범주·메모별로 열을 모은다(처음 나온 순서)."""
    out: dict[tuple[str, str], list[str]] = {}
    for col, cat, note in items:
        out.setdefault((cat, note), []).append(col)
    return [(cat, tuple(cols), note) for (cat, note), cols in out.items()]


NO_3WAY = "3자 대조 증거 없음(장 마감 ≠ 연구 D' 또는 연구 T = 연구 D')"


# ── 표별 규칙 ────────────────────────────────────────────────────────────────
def _rule_universe(p: _Pair, c: _Ctx) -> list[_Verdict]:
    tk = p.ticker
    items: list[tuple[str, str, str]] = []
    for col in p.diff:
        if col in UNI_CARRY:
            items.append((col, CARRY, "") if c.uni3(tk, col) else (col, UNEXPLAINED, NO_3WAY))
        elif col in UNI_INFO:
            items.append((col, INFO, "") if c.uni3(tk, col) else (col, UNEXPLAINED, NO_3WAY))
        elif col in UNI_WISE:
            items.append((col, *_wise(c, tk, col)))
        elif col == "filing_late":
            d = c.uni_d.get(tk)
            session_axis = d is not None and p.e.get(col) is False and d.get(col) is None
            ok = d is not None and not _same(p.r.get(col), d.get(col)) and (
                _same(p.e.get(col), d.get(col)) or session_axis)
            items.append((col, FILING, "") if ok else (col, UNEXPLAINED, NO_3WAY))
        elif col == "market_cap":
            items.append((col, *_mktcap(p, c)))
        elif col in ("eligible", "exclude_reason"):
            items.append((col, *_eligibility(p, c)))
        else:
            items.append((col, UNEXPLAINED, "등록 범주가 없는 열"))
    return _group(items)


def _wise(c: _Ctx, tk: str, col: str) -> tuple[str, str]:
    if c.boundary:
        return FY, ""
    return (INFO, "") if c.uni3(tk, col) else (UNEXPLAINED, NO_3WAY)


def _mktcap(p: _Pair, c: _Ctx) -> tuple[str, str]:
    tk = p.ticker
    if tk not in c.tclose_e and tk in c.tclose_r:
        return c.no_t_price(tk)
    if c.uni3(tk, "shares"):
        return CARRY, "D' 주식수 × T 종가"
    if c.close_differs(tk):
        return c.close_verdict()
    return UNEXPLAINED, "주식수·T 종가 증거 없이 시총이 다르다"


def _eligibility(p: _Pair, c: _Ctx) -> tuple[str, str]:
    tk = p.ticker
    er, rr = p.e.get("exclude_reason"), p.r.get("exclude_reason")
    if er == T6_REASON:
        return ((T6, "") if c.t6_evidence(tk) else
                (UNEXPLAINED, "T-6 보류인데 연구 판에 당일 기업행위 흔적(계수 변경·제한폭 밖 수익률)이 없다"))
    if rr == T6_REASON:
        return UNEXPLAINED, "연구 판에 T-6 사유가 있다"
    if er == NO_PRICE and tk not in c.tclose_e and tk in c.tclose_r:
        return c.no_t_price(tk)
    reasons = {er, rr}
    if reasons & set(CARRY_REASONS):
        ok = c.uni3(tk, "sec_type") or c.uni3(tk, "market")
        return (CARRY, "") if ok else (UNEXPLAINED, NO_3WAY)
    if reasons & set(ESTIMATE_REASONS):
        return _wise(c, tk, "coverage_state")
    if rr == NO_PRICE and tk not in c.tclose_r and c.halted_at_t(tk):
        return CARRY, "T 정지"
    return UNEXPLAINED, "제외 사유 차이를 설명할 증거가 없다"


def _t_missing(p: _Pair, c: _Ctx, *, flows: bool = False) -> list[_Verdict] | None:
    """T 행이 한 판에만 있을 때. 둘 다 있으면 None."""
    tk = p.ticker
    if not p.in_e:
        if flows:
            # 수급은 price_valid 와 무관하다 — stage 행이 있으면 T 수급도 있어야 한다
            if tk in c.ev.stage:
                return [(UNEXPLAINED, (), "장 마감 stage 에 행이 있는데 T 수급이 없다")]
            return [(CUTOFF if tk in c.ev.targeted else NOT_TARGETED, (), "")]
        if tk in c.tclose_e:
            return [(UNEXPLAINED, (), "T 가격은 있는데 이 표의 T 행이 없다")]
        cat, note = c.no_t_price(tk)
        return [(cat, (), note)]
    if not p.in_r:
        return [(CARRY, (), "T 정지") if c.halted_at_t(tk)
                else (UNEXPLAINED, (), "연구 판에 T 행이 없다")]
    return None


def _other_day(p: _Pair, c: _Ctx) -> list[_Verdict]:
    d = p.day
    note = "T 전 행이 다르다" if d is not None and d < c.t else "T 뒤 행"
    return [(UNEXPLAINED, p.diff, note)]


def _rule_prices(p: _Pair, c: _Ctx) -> list[_Verdict]:
    if p.day != c.t:
        return _other_day(p, c)
    missing = _t_missing(p, c)
    if missing is not None:
        return missing
    items: list[tuple[str, str, str]] = []
    for col in p.diff:
        if col in T_ROW_NOT_COMPARED["fi_prices"]:
            continue
        if col == "close":
            items.append((col, *c.close_verdict()))
        elif col == "volume":
            ve, vr = _num(p.e.get(col)), _num(p.r.get(col))
            ok = ve is not None and vr is not None and ve <= vr
            items.append((col, VOLUME_DEF, "") if ok
                         else (col, UNEXPLAINED, "장 마감 거래량이 없거나 연구 판보다 크다"))
        else:
            items.append((col, UNEXPLAINED, "등록 범주가 없는 열"))
    return _group(items)


def _rule_adj(p: _Pair, c: _Ctx) -> list[_Verdict]:
    d, tk = p.day, p.ticker
    events = bool(c.ev.new_events.get(tk))
    if d is not None and d < c.t:
        if not (p.in_e and p.in_r):
            return _other_day(p, c)
        items: list[tuple[str, str, str]] = []
        for col in p.diff:
            base = "adj_factor" if col == "adj_close" else col
            ok = (col in ADJ_EVENT_COLS or (col == "adj_close" and "adj_factor" in p.diff)) and (
                events and c.adj_tail.get((tk, base), False) and p.three_way(col))
            items.append((col, INFO, "(D', T] 공개 기업행위 — 적용일부터 T−1 끝 구간") if ok
                         else (col, UNEXPLAINED, "T 전 행이 다르다(공개 사건·끝 구간·3자 대조 증거 없음)"))
        return _group(items)
    if d != c.t:
        return _other_day(p, c)
    missing = _t_missing(p, c)
    if missing is not None:
        return missing
    if c.reason_e(tk) == T6_REASON:
        return [(T6, p.diff, "") if c.t6_evidence(tk) else
                (UNEXPLAINED, p.diff, "T-6 보류인데 연구 판에 당일 기업행위 흔적이 없다")]
    items = []
    for col in p.diff:
        if events and col in ADJ_EVENT_COLS | {"adj_close"}:
            items.append((col, INFO, "(D', T] 공개 기업행위"))
        elif col in ADJ_FLAG_COLS or (col == "adj_close" and "adj_factor" not in p.diff):
            items.append((col, *c.close_verdict()) if c.close_differs(tk)
                         else (col, UNEXPLAINED, "공개 사건·T 종가 차이 없이 T 행이 다르다"))
        else:
            items.append((col, UNEXPLAINED, "공개 사건 없이 T 행 계수가 다르다 — T-6 이 못 잡은 당일 "
                                            "기업행위?"))
    return _group(items)


def _rule_flows(p: _Pair, c: _Ctx) -> list[_Verdict]:
    if p.day != c.t:
        return _other_day(p, c)
    missing = _t_missing(p, c, flows=True)
    if missing is not None:
        return missing
    items: list[tuple[str, str, str]] = []
    for col in p.diff:
        if p.e.get(col) is None or p.r.get(col) is None:
            items.append((col, UNEXPLAINED, "한쪽만 수급 값이 있다"))
        elif col in c.flow_over:
            items.append((col, UNEXPLAINED, "주체 판 통계가 수급 정의 상한을 넘는다(T-36)"))
        else:
            items.append((col, FLOW_DEF, ""))
    return _group(items)


def _rule_credit(p: _Pair, c: _Ctx) -> list[_Verdict]:
    arrived = c.t in (p.e.get("available_date"), p.r.get("available_date"))
    if arrived and p.in_r:
        return [(CREDIT_T, p.diff, "")]
    return [(UNEXPLAINED, p.diff, "available_date = T 인 행이 연구 판에서 사라졌다" if arrived
             else "available_date < T 인 행이 다르다")]


def _rule_info(table: str) -> Callable[[_Pair, _Ctx], list[_Verdict]]:
    col = INFO_DATE_COL[table]

    def rule(p: _Pair, c: _Ctx) -> list[_Verdict]:
        tk = p.ticker
        got = p.e.get(col)
        if p.in_e and isinstance(got, date) and got > c.dprime:
            return [(UNEXPLAINED, p.diff, f"장 마감 판 {col} {got.isoformat()} > D' — D' 자르기 위반")]
        if c.boundary and (table == "fi_consensus_annual" or c.cov_differs(tk)):
            return [(FY, p.diff, "")]
        if not _info_evidence(table, p, c):
            return [(UNEXPLAINED, p.diff, f"{NO_3WAY} 또는 연구 판 {col} 가 늦지 않다")]
        return [(INFO, p.diff, "")]
    return rule


def _after(a: object, b: date) -> bool:
    return isinstance(a, date) and a > b


def _info_evidence(table: str, p: _Pair, c: _Ctx) -> bool:
    """3자 대조 + 방향(MINOR-1) — 장 마감 판 행 = 연구 판 D' 행이고, 연구 판 T 쪽이 D' 뒤에 온
    정보다(수집·공개일 > D' ≥ 장 마감 판 날짜). 신선도가 3자 대조로 바뀐 종목(WISE 판 교체·소멸)도
    정보 시점이다(연간 컨센서스는 신선도와 무관한 최신 판이라 빼고)."""
    col, tk = INFO_DATE_COL[table], p.ticker
    cov = table != "fi_consensus_annual" and c.uni3(tk, "coverage_state")
    same_as_d = p.in_d and all(_same(p.e.get(x), p.d.get(x)) for x in p.e)
    if p.in_e and p.in_r:                         # 값이 다르다
        return same_as_d and (_after(p.r.get(col), c.dprime) or cov)
    if p.in_r:                                    # 연구 판에만 — 새 판이 더한 행
        return not p.in_d and (_after(p.r.get(col), c.dprime) or cov)
    newer = _after(c.max_r[table].get(tk), c.dprime)
    return same_as_d and (newer or cov)           # 장 마감 판에만 — 새 판이 뺀 행


def _rule_unknown(p: _Pair, c: _Ctx) -> list[_Verdict]:
    return [(UNEXPLAINED, p.diff, "대조 규칙이 없는 표")]


_RULES: dict[str, Callable[[_Pair, _Ctx], list[_Verdict]]] = {
    "fi_universe": _rule_universe, "fi_prices": _rule_prices, "fi_adj_prices": _rule_adj,
    "fi_flows": _rule_flows, "fi_credit": _rule_credit,
    **{t: _rule_info(t) for t in INFO_DATE_COL}}


def classify(table: str, p: _Pair, c: _Ctx) -> list[_Verdict]:
    """행 → (범주, 열, 메모) 목록. 한 판에만 있는 종목은 표와 상관없이 그 종목 집합 판정을 따른다."""
    if p.ticker in c.member:
        cat, note = c.member[p.ticker]
        return [(cat, p.diff, note)]
    return _RULES.get(table, _rule_unknown)(p, c)


def _membership(c: _Ctx) -> dict[str, tuple[str, str]]:
    """한 판 유니버스에만 있는 종목 — 장 마감 판 종목 집합 = 연구 판 D' 종목 집합이어야 한다(이월)."""
    out: dict[str, tuple[str, str]] = {}
    for tk in set(c.uni_r) - set(c.uni_e):
        out[tk] = ((CARRY, "T 에 들어온 종목") if tk not in c.uni_d
                   else (UNEXPLAINED, "D' 종목을 장 마감 판이 빠뜨렸다"))
    for tk in set(c.uni_e) - set(c.uni_r):
        out[tk] = ((CARRY, "T 에 빠진 종목") if tk in c.uni_d
                   else (UNEXPLAINED, "D' 에 없던 종목이 장 마감 판에 있다"))
    return out


# ── fi 층 ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FiBoard:
    root: Path               # factor_inputs 루트
    build_id: str


@dataclass
class FiResult:
    tally: Tally
    tables: dict[str, dict[str, object]]
    flow_stats: dict[str, dict[str, object]]
    targeted: int


def _part(b: FiBoard, table: str) -> str:
    return (Path(b.root) / table / f"v={b.build_id}" / "*.parquet").as_posix()


def _read(path: str) -> str:
    return f"read_parquet({_lit(path)}, hive_partitioning=false)"


def _connect() -> duckdb.DuckDBPyConnection:
    """fi 빌드와 같은 스레드·메모리 한도."""
    con = duckdb.connect()
    con.execute(f"SET threads = {DUCKDB_THREADS}")
    con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    con.execute("SET enable_progress_bar = false")
    return con


def _records(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, object]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _describe(con: duckdb.DuckDBPyConnection, path: str) -> list[tuple[str, str]]:
    return [(str(r[0]), str(r[1])) for r in con.execute(
        f"DESCRIBE SELECT * FROM {_read(path)}").fetchall()]


def _joined(table: str, pe: str, pr: str, pd: str, cols: Sequence[str]) -> tuple[str, str]:
    """(맞댄 행 FROM 절, 차이 조건). 연구 판 D' 는 grain 으로(유니버스는 종목으로) 붙인다."""
    grain = FI_TABLES[table].grain
    on = " AND ".join(f'e."{g}" = r."{g}"' for g in grain)
    d_on = " AND ".join(f'd."{g}" = coalesce(e."{g}", r."{g}")'
                        for g in (("ticker",) if table == "fi_universe" else grain))
    src = (f"{_read(pe)} e FULL OUTER JOIN {_read(pr)} r ON {on} "
           f"LEFT JOIN {_read(pd)} d ON {d_on}")
    g0 = grain[0]
    neq = " OR ".join(f'e."{x}" IS DISTINCT FROM r."{x}"' for x in cols) or "false"
    return src, f'e."{g0}" IS NULL OR r."{g0}" IS NULL OR {neq}'


def _pairs(con: duckdb.DuckDBPyConnection, table: str, src: str, cond: str,
           cols: Sequence[str]) -> list[_Pair]:
    grain = FI_TABLES[table].grain
    g0 = grain[0]
    keys = ", ".join(f'coalesce(e."{g}", r."{g}") AS "k__{g}"' for g in grain)
    sel = ", ".join(f'e."{x}" AS "e__{x}", r."{x}" AS "r__{x}", d."{x}" AS "d__{x}"' for x in cols)
    sql = (f'SELECT {keys}, e."{g0}" IS NOT NULL AS in_e, r."{g0}" IS NOT NULL AS in_r, '
           f'd."{g0}" IS NOT NULL AS in_d' + (f", {sel}" if sel else "")
           + f" FROM {src} WHERE {cond} ORDER BY " + ", ".join(f'"k__{g}"' for g in grain))
    out: list[_Pair] = []
    for row in _records(con, sql):
        in_e, in_r, in_d = bool(row["in_e"]), bool(row["in_r"]), bool(row["in_d"])
        e = {x: row[f"e__{x}"] for x in cols} if in_e else {}
        r = {x: row[f"r__{x}"] for x in cols} if in_r else {}
        d = {x: row[f"d__{x}"] for x in cols} if in_d else {}
        diff = tuple(x for x in cols if not _same(e[x], r[x])) if in_e and in_r else ()
        out.append(_Pair({g: row[f"k__{g}"] for g in grain}, in_e, in_r, in_d, e, r, d, diff))
    return out


def _finding(table: str, p: _Pair, cat: str, cols: tuple[str, ...], note: str,
             t: date) -> Finding:
    kind = "value" if p.in_e and p.in_r else ("evening_only" if p.in_e else "research_only")
    key = {g: str(_jsonable(v)) for g, v in p.key.items() if g != "ticker"}
    if p.day == t:
        key["is_t"] = "1"
    return Finding(table, p.ticker, key, kind, cat, cols,
                   {x: _jsonable(p.e.get(x)) for x in cols},
                   {x: _jsonable(p.r.get(x)) for x in cols}, note)


def _bulk(con: duckdb.DuckDBPyConnection, table: str, src: str, cond: str,
          cols: Sequence[str], n_rows: int, tally: Tally) -> None:
    """차이 행이 상한을 넘은 표 — SQL 로 열별로 세고 표본만 올린다(전부 미설명)."""
    counts = ", ".join(f'count(*) FILTER (WHERE e."{x}" IS DISTINCT FROM r."{x}" '
                       f'AND e."ticker" IS NOT NULL AND r."ticker" IS NOT NULL) AS "{x}"'
                       for x in cols)
    row = _records(con, f"SELECT {counts} FROM {src} WHERE {cond}")[0] if cols else {}
    by_col = {x: int(str(n)) for x, n in row.items() if n}
    tickers = [str(t) for (t,) in con.execute(
        f'SELECT DISTINCT coalesce(e."ticker", r."ticker") FROM {src} WHERE {cond}').fetchall()]
    note = f"차이 행 {n_rows:,} > 상한 {DIFF_ROW_MAX:,} — 행 분류 없이 전부 미설명(열별 건수는 SQL)"
    tally.add(Finding(table, "", {}, "bulk", UNEXPLAINED, tuple(by_col), {}, {}, note),
              n=n_rows, columns=by_col, tickers=tickers)


def _adj_tails(con: duckdb.DuckDBPyConnection, pe: str, pr: str, pairs: Sequence[_Pair],
               t: date) -> dict[tuple[str, str], bool]:
    """T 전 수정주가 행의 열별 차이 날짜가 '처음 다른 날부터 T−1 까지 두 판 공통 행 전부'인가."""
    diff_days: dict[tuple[str, str], set[date]] = defaultdict(set)
    for p in pairs:
        if p.in_e and p.in_r and p.day is not None and p.day < t:
            for col in p.diff:
                diff_days[(p.ticker, col)].add(p.day)
    tickers = sorted({tk for tk, _ in diff_days})
    if not tickers:
        return {}
    lit = ", ".join(f"'{tk}'" for tk in tickers)
    common: dict[str, list[date]] = defaultdict(list)
    for tk, d in con.execute(
            f"SELECT e.ticker, e.date FROM {_read(pe)} e JOIN {_read(pr)} r "
            f"ON e.ticker = r.ticker AND e.date = r.date WHERE e.ticker IN ({lit}) "
            f"AND e.date < DATE '{t.isoformat()}' ORDER BY 1, 2").fetchall():
        common[str(tk)].append(d)
    return {(tk, col): days == {d for d in common[tk] if d >= min(days)}
            for (tk, col), days in diff_days.items()}


def _flow_stats(con: duckdb.DuckDBPyConnection, pe: str, pr: str,
                t: date) -> dict[str, dict[str, object]]:
    """주체별 T 행 판 통계 — 중앙 |차이|/|연구값|(연구값 0 제외)·부호 반전 비율(둘 다 0 아님)."""
    subjects = [c.name for c in FI_TABLES["fi_flows"].columns if c.name not in ("ticker", "date")]
    parts = ", ".join(
        f'median(abs(e."{s}" - r."{s}") / abs(r."{s}")) FILTER (WHERE r."{s}" <> 0 '
        f'AND e."{s}" IS NOT NULL) AS "m__{s}", '
        f'count(*) FILTER (WHERE r."{s}" <> 0 AND e."{s}" IS NOT NULL) AS "n__{s}", '
        f'count(*) FILTER (WHERE e."{s}" * r."{s}" < 0) AS "f__{s}", '
        f'count(*) FILTER (WHERE e."{s}" <> 0 AND r."{s}" <> 0) AS "b__{s}"' for s in subjects)
    row = _records(con, f"SELECT {parts} FROM {_read(pe)} e JOIN {_read(pr)} r "
                        f"ON e.ticker = r.ticker AND e.date = r.date "
                        f"WHERE e.date = DATE '{t.isoformat()}'")[0]
    out: dict[str, dict[str, object]] = {}
    for s in subjects:
        n, both = int(str(row[f"n__{s}"])), int(str(row[f"b__{s}"]))
        med = _num(row[f"m__{s}"])
        flip = int(str(row[f"f__{s}"])) / both if both else None
        judged = n >= FLOW_STAT_MIN_N
        over = judged and ((med is not None and med > FLOW_REL_MAX)
                           or (flip is not None and flip > FLOW_FLIP_MAX))
        out[s] = {"n": n, "median_rel": med, "n_both_nonzero": both, "flip_ratio": flip,
                  "judged": judged, "over": over}
    return out


def compare_fi(e: FiBoard, r: FiBoard, d: FiBoard, t: date, dprime: date, ev: Evidence, *,
               replay: bool = False) -> FiResult:
    """fi 8표 대조 — 장 마감 판 e · 연구 판 T r · 연구 판 D' d. 스키마가 다르면 `CompareInputError`."""
    con = _connect()
    try:
        paths = {n: (_part(e, n), _part(r, n), _part(d, n)) for n in FI_TABLES}
        cols: dict[str, list[str]] = {}
        for name, (pe, pr, pd) in paths.items():
            se, sr, sd = _describe(con, pe), _describe(con, pr), _describe(con, pd)
            if not se == sr == sd:
                raise CompareInputError(f"스키마가 다르다 — table={name} 장 마감 판 {se} · 연구 판 T "
                                        f"{sr} · 연구 판 D' {sd}")
            skip = {*FI_TABLES[name].grain, *NOT_COMPARED.get(name, ())}
            cols[name] = [x for x, _ in se if x not in skip]

        def by_ticker(path: str) -> dict[str, dict[str, object]]:
            return {str(x["ticker"]): x for x in _records(con, f"SELECT * FROM {_read(path)}")}

        def on_day(path: str, col: str, day: date) -> dict[str, object]:
            return {str(tk): v for tk, v in con.execute(
                f'SELECT ticker, "{col}" FROM {_read(path)} WHERE date = DATE '
                f"'{day.isoformat()}' AND \"{col}\" IS NOT NULL").fetchall()}

        pu, pp, pa = paths["fi_universe"], paths["fi_prices"], paths["fi_adj_prices"]
        ctx = _Ctx(t, dprime, replay, ev, by_ticker(pu[0]), by_ticker(pu[1]), by_ticker(pu[2]),
                   on_day(pp[0], "close", t), on_day(pp[1], "close", t),
                   on_day(pa[1], "adj_factor", t), on_day(pa[1], "adj_factor", dprime),
                   on_day(pp[1], "close", dprime))
        ctx.member = _membership(ctx)
        for name, col in INFO_DATE_COL.items():
            for side, store in ((0, ctx.max_e), (1, ctx.max_r)):
                store[name] = {str(tk): v for tk, v in con.execute(
                    f'SELECT ticker, max("{col}") FROM {_read(paths[name][side])} '
                    "GROUP BY ticker").fetchall()}
        flow_stats = _flow_stats(con, paths["fi_flows"][0], paths["fi_flows"][1], t)
        ctx.flow_over = frozenset(s for s, v in flow_stats.items() if v["over"])

        tally = Tally()
        tables: dict[str, dict[str, object]] = {}
        for name, (pe, pr, pd) in paths.items():
            he, hr = _content_hash(con, Path(pe)), _content_hash(con, Path(pr))
            info: dict[str, object] = {"hash_equal": he == hr, "hash_evening": he,
                                       "hash_research": hr, "n_rows_diff": 0, "bulk": False}
            tables[name] = info
            if he == hr:
                continue
            src, cond = _joined(name, pe, pr, pd, cols[name])
            n_sql = f"SELECT count(*) AS n FROM {src} WHERE {cond}"
            n_diff = int(str(_records(con, n_sql)[0]["n"]))
            if n_diff > DIFF_ROW_MAX:
                info.update(n_rows_diff=n_diff, bulk=True)
                _bulk(con, name, src, cond, cols[name], n_diff, tally)
                continue
            pairs = _pairs(con, name, src, cond, cols[name])
            if name == "fi_adj_prices":
                ctx.adj_tail = _adj_tails(con, pe, pr, pairs, t)
            n_rows = 0
            for p in pairs:
                verdicts = [v for v in classify(name, p, ctx) if v[1] or not (p.in_e and p.in_r)]
                n_rows += bool(verdicts)
                for cat, cs, note in verdicts:
                    tally.add(_finding(name, p, cat, cs, note, t))
            info["n_rows_diff"] = n_rows
    finally:
        con.close()
    return FiResult(tally, tables, flow_stats, len(ev.targeted))


# ── 판 ───────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Side:
    """판 하나(fi + model)."""

    label: str                        # 장 마감 판 | 연구 판
    basis: str
    root: Path
    fi_run: dict[str, object]
    model_run: ModelRun

    @property
    def fi_root(self) -> Path:
        return self.root / "factor_inputs"

    @property
    def model_root(self) -> Path:
        return self.root / "model"

    @property
    def fi(self) -> FiBoard:
        return FiBoard(self.fi_root, str(self.fi_run["build_id"]))

    def to_dict(self) -> dict[str, object]:
        ex = self.model_run.meta.get("excluded_specs")
        return {"root": str(self.root), "basis": self.basis, "fi_build_id": self.fi.build_id,
                "model_build_id": self.model_run.build_id,
                "fi_rules_version": self.fi_run.get("rules_version"),
                "asof": self.fi_run.get("asof"), "primary_spec": self.model_run.primary_spec,
                "excluded_specs": sorted(ex) if isinstance(ex, dict) else []}


def load_fi_run(fi_root: Path, basis: str, d: date, label: str) -> dict[str, object]:
    """`<fi_root>/_runs/<D>_<basis>.json`(성공 판·date·basis) → fi 8표(`_meta.json` date·basis,
    `model.build._resolve_fi`)."""
    path = Path(fi_root) / "_runs" / f"{d:%Y%m%d}_{basis}.json"
    if not path.exists():
        raise CompareInputError(f"{label} factor_inputs 판 기록이 없다: {path}")
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CompareInputError(f"{label} factor_inputs 판 기록을 읽지 못했다: {path} — {e}") from e
    if not isinstance(run, dict):
        raise CompareInputError(f"{label} factor_inputs 판 기록이 객체가 아니다: {path}")
    if run.get("status") != "ok":
        raise CompareInputError(f"{label} factor_inputs 판이 성공 판이 아니다"
                                f"(status={run.get('status')!r}): {path}")
    if run.get("basis") != basis:
        raise CompareInputError(f"{label} factor_inputs 판 basis={run.get('basis')!r} — 기대 "
                                f"{basis}: {path}")
    if run.get("date") != d.isoformat():
        raise CompareInputError(f"{label} factor_inputs 판 date={run.get('date')!r} — 기대 "
                                f"{d.isoformat()}: {path}")
    try:
        _resolve_fi(Path(fi_root), str(run.get("build_id")), d.isoformat(), basis)
    except ModelBuildError as e:
        raise CompareInputError(f"{label} {e}") from e
    return run


def load_side(root: Path, basis: str, t: date) -> Side:
    """fi 판 기록(`load_fi_run`) → `root/model/_runs/<T>_<basis>.json`(`deliver.reader.load_run`) →
    모델 판이 그 fi 판을 가리키는지."""
    label = "장 마감 판" if basis == "evening" else "연구 판"
    root = Path(root)
    run = load_fi_run(root / "factor_inputs", basis, t, label)
    bid = str(run.get("build_id"))
    try:
        mrun = load_run(root / "model", t, basis)
    except DeliverError as e:
        raise CompareInputError(f"{label} {e}") from e
    if mrun.fi_build_id != bid:
        raise CompareInputError(f"{label} model 판 {mrun.build_id} 의 fi_build_id="
                                f"{mrun.fi_build_id} ≠ factor_inputs 판 {bid} — 다른 fi 판으로 "
                                "지은 점수다")
    return Side(label, basis, root, run, mrun)


def _dprime(ev: Side, rt: Side, t: date, calendar_dir: Path) -> date:
    """장 마감 판 asof = D' = `daily.calendar` 의 T 직전 거래일 = 고정한 인계 이력 날짜.
    연구 판 T 의 asof 는 T(PR-4 이전 판은 키가 없다)."""
    raw = ev.fi_run.get("asof")
    try:
        dprime = date.fromisoformat(str(raw))
    except ValueError as e:
        raise CompareInputError(f"장 마감 판 asof={raw!r} 를 날짜로 읽지 못했다") from e
    try:
        want = daily_calendar.load(calendar_dir).prev_trading_day(t)
    except (daily_calendar.CalendarUnavailable, KeyError) as e:
        raise CompareInputError(f"T={t.isoformat()} 직전 거래일 판정 불가 — daily.calendar "
                                f"(calendar_dir={calendar_dir}): {e}") from e
    if dprime != want:
        raise CompareInputError(f"장 마감 판 asof={dprime.isoformat()} ≠ T={t.isoformat()} 의 직전 "
                                f"거래일 {want.isoformat()}(daily.calendar)")
    pinned = _iso(ev.fi_run.get("builds_from_date"))
    if pinned != dprime.isoformat():
        raise CompareInputError(f"장 마감 판 builds_from_date={ev.fi_run.get('builds_from_date')!r} "
                                f"≠ asof {dprime.isoformat()} — D' 확정판을 고정하지 않았다")
    got = rt.fi_run.get("asof")
    if got is not None and got != t.isoformat():
        raise CompareInputError(f"연구 판 asof={got!r} ≠ T={t.isoformat()}")
    return dprime


def _model_rules(side: Side, spec_id: str) -> str:
    m = manifest.load(side.model_root / spec_id / "MANIFEST.json")
    rec = next((b for b in m.builds if b.build_id == side.model_run.build_id), None)
    if rec is None:
        raise CompareInputError(f"{side.label} model {spec_id} MANIFEST 에 판 "
                                f"{side.model_run.build_id} 기록이 없다(rules_version 확인 불가)")
    return rec.rules_version


def _check_versions(ev: Side, rt: Side, rd: Mapping[str, object]) -> None:
    """세 판이 같은 규칙 판본이어야 차이를 데이터 범주로 설명할 수 있다."""
    got = {"장 마감 판": ev.fi_run.get("rules_version"), "연구 판 T": rt.fi_run.get("rules_version"),
           "연구 판 D'": rd.get("rules_version")}
    if len(set(got.values())) != 1:
        raise CompareInputError(f"factor_inputs rules_version 이 다르다 — {got}")
    for sid in sorted(set(ev.model_run.specs) & set(rt.model_run.specs)):
        me, mr = _model_rules(ev, sid), _model_rules(rt, sid)
        if me != mr:
            raise CompareInputError(f"model {sid} rules_version 이 다르다 — 장 마감 판 {me} · "
                                    f"연구 판 {mr}")


def read_stage(ev: Side, t: date) -> dict[str, str]:
    """장 마감 stage T 행 → 종목 → STAGE_* 상태. 루트·판은 장 마감 fi 판 기록이 읽은 그대로다 —
    `postclose_stage_root`·`postclose_builds[T_SOURCE_TABLE]`(PR-5, T-29)."""
    builds, root = ev.fi_run.get("postclose_builds"), ev.fi_run.get("postclose_stage_root")
    bid = builds.get(T_SOURCE_TABLE) if isinstance(builds, dict) else None
    if not bid or not root:
        raise CompareInputError(f"장 마감 판 기록에 postclose_stage_root·postclose_builds"
                                f"[{T_SOURCE_TABLE}] 가 없다 — T 행 원천을 확인할 수 없다(PR-5 이전 판?)")
    try:
        pb = eq_inputs.resolve(Path(str(root)), T_SOURCE_TABLE, str(bid))
    except FileNotFoundError as e:
        raise CompareInputError(f"장 마감 stage 판이 없다 — root={root} build={bid}: {e}") from e
    lit = ", ".join(_lit(g) for g in pb.globs)
    usable = kw_daily.ka10060_postclose_price_usable_sql("price_valid", "close_krw", "volume_shr")
    con = _connect()
    try:
        rows = con.execute(f"SELECT ticker, price_valid IS TRUE, {usable} "
                           f"FROM read_parquet([{lit}], hive_partitioning=true, "
                           "union_by_name=true) "
                           f"WHERE date = DATE '{t.isoformat()}'").fetchall()
    finally:
        con.close()
    return {str(tk): (STAGE_USABLE if ok else STAGE_UNUSABLE) if valid else STAGE_INVALID
            for tk, valid, ok in rows}


def read_targets(rt: Side, rd: Mapping[str, object], dprime: date) -> frozenset[str]:
    """장 마감 수집 대상(PR-1 순서) — ① 직전 판 모델 후보 = 수집기·FG5 와 같은 함수
    `daily.postclose.fi_candidates`(연구 fi 루트의 `_runs/<D'>_morning.json` eligible) ② v3 유니버스 =
    수집기와 같은 식 `compat.mappings.V3_STOCK_FILTER` 를 연구 판 D' 유니버스에(수집기 ② 는 인계 이력의
    universe_daily D' 행을 읽는다 — 층 유니버스 종목에서는 같은 판정이고, 재생 루트에 인계 이력이
    없어도 돈다)."""
    from daily import postclose  # 수집기 모듈은 무겁다 — 대조 때만 읽는다(fi build 와 같은 방식)
    try:
        cands, _ = postclose.fi_candidates(rt.fi_root, dprime.strftime("%Y%m%d"))
    except postclose.BOARD_ERRORS as e:
        raise CompareInputError(f"직전 판 모델 후보를 읽지 못했다 — {type(e).__name__}: {e}") from e
    part = (rt.fi_root / "fi_universe" / f"v={rd['build_id']}" / "*.parquet").as_posix()
    con = _connect()
    try:
        v3 = {str(t) for (t,) in con.execute(
            f"SELECT ticker FROM {_read(part)} u WHERE {V3_STOCK_FILTER}").fetchall()}
    finally:
        con.close()
    return frozenset(cands) | frozenset(v3)


def read_new_events(rt: Side, dprime: date, t: date) -> dict[str, tuple[date, ...]]:
    """(D', T] 에 공개된 기업행위 — 연구 판 T 가 읽은 equity `adj_factor` 판(판 기록 `equity_root`·
    `equity_builds`). 장 마감 판은 available ≤ D' 만 센다(asof)."""
    root, builds = rt.fi_run.get("equity_root"), rt.fi_run.get("equity_builds")
    bid = builds.get("adj_factor") if isinstance(builds, dict) else None
    if not root or not bid:
        raise CompareInputError("연구 판 기록에 equity_root·equity_builds[adj_factor] 가 없다 — "
                                "기업행위 공개를 확인할 수 없다")
    try:
        pb = eq_inputs.resolve(Path(str(root)), "adj_factor", str(bid))
    except FileNotFoundError as e:
        raise CompareInputError(f"연구 판 equity adj_factor 판이 없다 — root={root} build={bid}: "
                                f"{e}") from e
    lit = ", ".join(_lit(g) for g in pb.globs)
    con = _connect()
    try:
        rows = con.execute(f"SELECT ticker, apply_date FROM read_parquet([{lit}], "
                           "hive_partitioning=false) "
                           f"WHERE available_date > DATE '{dprime.isoformat()}' "
                           f"AND available_date <= DATE '{t.isoformat()}' ORDER BY 1, 2").fetchall()
    finally:
        con.close()
    out: dict[str, list[date]] = defaultdict(list)
    for tk, d in rows:
        out[str(tk)].append(d)
    return {tk: tuple(v) for tk, v in out.items()}


# ── 모델 층 ──────────────────────────────────────────────────────────────────
def _view(side: Side, spec_id: str) -> DayView:
    try:
        return load_day(side.model_root, side.fi_root, side.model_run, with_fi=False,
                        others=False, spec_id=spec_id)
    except DeliverError as e:
        raise CompareInputError(f"{side.label} {e}") from e


def _factor_cols(view: DayView) -> list[str]:
    if view.spec is None:
        return []
    comp = mgates.composite_col(view.spec)
    return [c for c, ty in mgates.score_dtypes(view.spec).items() if ty == "DOUBLE" and c != comp]


def _tick(tk: str, cats: Mapping[str, list[str]]) -> dict[str, object]:
    got = list(cats.get(tk, ()))
    out: dict[str, object] = {"ticker": tk, "categories": got}
    if not got:
        out["trace"] = CROSS_SECTION
    return out


def _getter(rows: Mapping[str, Mapping[str, object]], col: str) -> Callable[[str], float | None]:
    return lambda t: _num(rows[t].get(col))


def _col_diff(tickers: Sequence[str], ev: Callable[[str], float | None],
              rs: Callable[[str], float | None], cats: Mapping[str, list[str]],
              top_n: int) -> dict[str, object]:
    """점수 열 하나의 |Δ| — 한쪽만 NULL 이면 맨 앞(무한대)."""
    rows: list[tuple[float, str, float | None, float | None]] = []
    n_null = 0
    for tk in tickers:
        a, b = ev(tk), rs(tk)
        if a is None and b is None:
            continue
        if a is None or b is None:
            n_null += 1
            rows.append((math.inf, tk, a, b))
        elif abs(a - b) > SCORE_TOL:
            rows.append((abs(a - b), tk, a, b))
    rows.sort(key=lambda x: (-x[0], x[1]))
    finite = [d for d, *_ in rows if d != math.inf]
    return {"n_diff": len(rows), "n_null_mismatch": n_null,
            "max_abs": max(finite) if finite else None,
            "top": [{**_tick(tk, cats), "evening": a, "research": b,
                     "delta": None if a is None or b is None else a - b}
                    for _, tk, a, b in rows[:top_n]]}


def _excluded_reason(side: Side, spec_id: str) -> str:
    """판 기록 `excluded_specs[spec]` 사유 한 줄 — 예외 사유 또는 FAIL 게이트."""
    ex = side.model_run.meta.get("excluded_specs")
    got = ex.get(spec_id) if isinstance(ex, dict) else None
    if not isinstance(got, dict):
        return "기록 없음"
    if got.get("error"):
        return str(got["error"])
    gates = got.get("gates")
    fails = sorted(g for g, v in gates.items() if isinstance(v, dict) and v.get("status") == "fail"
                   ) if isinstance(gates, dict) else []
    return "게이트 FAIL " + ",".join(fails) if fails else "사유 불명"


def compare_model(ev: Side, rt: Side, tally: Tally, spearman_min: float,
                  list_n: int) -> dict[str, dict[str, object]]:
    """spec 마다 Spearman · 엑셀 후보 겹침 · 한 판에만 있는 점수 행 · 점수 열 |Δ| 상위. 미설명은 `tally`
    에 더한다. 종목에 붙이는 fi 범주는 그 spec 엔진이 읽는 표의 것만이다."""
    fi_same = tally.n_findings == 0
    e_specs, r_specs = set(ev.model_run.specs), set(rt.model_run.specs)
    for sid in sorted(e_specs ^ r_specs):
        where, missing = (("장 마감 판", rt) if sid in e_specs else ("연구 판", ev))
        tally.add(Finding(f"model:{sid}", "", {}, "evening_only" if sid in e_specs
                          else "research_only", UNEXPLAINED, (), {}, {},
                          f"spec 이 {where}에만 있다 — {missing.label} 제외 사유: "
                          f"{_excluded_reason(missing, sid)}"))
    out: dict[str, dict[str, object]] = {}
    for sid in sorted(e_specs & r_specs):
        ve, vr = _view(ev, sid), _view(rt, sid)
        cats = tally.ticker_categories(None if ve.spec is None else ve.spec.engine)
        er, rr = ve.by_ticker, vr.by_ticker
        e_only, r_only = sorted(set(er) - set(rr)), sorted(set(rr) - set(er))
        common = sorted(set(er) & set(rr))
        comp_e = {t: v for t, row in er.items() if (v := composite_of(row)) is not None}
        comp_r = {t: v for t, row in rr.items() if (v := composite_of(row)) is not None}
        rho = mgates.spearman(comp_e, comp_r)
        composite = _col_diff(common, comp_e.get, comp_r.get, cats, list_n)
        factors = {col: _col_diff(common, _getter(er, col), _getter(rr, col), cats, FACTOR_TOP_N)
                   for col in _factor_cols(ve)}
        cand_e, cand_r = list(ve.candidates), list(vr.candidates)
        out[sid] = {
            "primary": sid == ev.model_run.primary_spec,
            "spearman": rho, "n_common": len(set(comp_e) & set(comp_r)),
            "spearman_ok": rho is not None and rho >= spearman_min,
            "evening_only": [_tick(t, cats) for t in e_only],
            "research_only": [_tick(t, cats) for t in r_only],
            "candidates": {"top_n": ve.output.top_n, "evening": cand_e, "research": cand_r,
                           "overlap": len(set(cand_e) & set(cand_r)),
                           "entered": [_tick(t, cats) for t in cand_e if t not in set(cand_r)],
                           "dropped": [_tick(t, cats) for t in cand_r if t not in set(cand_e)]},
            "composite": composite, "factors": factors}
        # 점수 행이 한 판에만 — 종목 집합·eligible·시총·T 가격 범주로만 설명한다
        for kind, tickers in (("evening_only", e_only), ("research_only", r_only)):
            for t in tickers:
                if not tally.eligibility.get(t):
                    tally.add(Finding(f"model:{sid}", t, {}, kind, UNEXPLAINED, (), {}, {},
                                      "점수 행이 한 판에만 있는데 적격성 차이(종목 집합·eligible·"
                                      "시총·T 가격)가 없다"))
        # 한쪽만 종합점수가 비었다(적격성 뒤집힘) — 자기 fi 차이로만 설명한다
        for t in common:
            if (t in comp_e) != (t in comp_r) and not cats.get(t):
                tally.add(Finding(f"model:{sid}", t, {}, "value", UNEXPLAINED, (), {}, {},
                                  "한쪽만 종합점수가 비었는데 자기 fi 차이가 없다"))
        if fi_same and (composite["n_diff"] or e_only or r_only):
            tally.add(Finding(f"model:{sid}", "", {}, "value", UNEXPLAINED, (), {}, {},
                              "fi 8표가 같은데 점수가 다르다 — 엔진·규칙 차이 의심"))
    return out


# ── 대조 ─────────────────────────────────────────────────────────────────────
@dataclass
class Result:
    t: date
    dprime: date
    replay: bool
    evening: Side
    research: Side
    research_dprime: dict[str, object]
    fi: FiResult
    model: dict[str, dict[str, object]]
    spearman_min: float
    generated_at: str = field(default_factory=_now)

    @property
    def tally(self) -> Tally:
        return self.fi.tally

    @property
    def n_unexplained(self) -> int:
        return self.tally.count[UNEXPLAINED]

    @property
    def reasons(self) -> list[str]:
        out = []
        if self.n_unexplained:
            out.append(f"미설명 {self.n_unexplained:,}건")
        for sid, m in self.model.items():
            if not m["spearman_ok"]:
                rho = m["spearman"]
                shown = "셀 수 없음" if rho is None else f"{float(str(rho)):.6f}"
                out.append(f"{sid} Spearman {shown} < 하한 {self.spearman_min}")
        return out

    @property
    def rc(self) -> int:
        return 1 if self.reasons else 0

    def to_dict(self) -> dict[str, object]:
        t = self.tally
        cats = {k: {"label": c.label, "basis": c.basis, "definition": c.definition,
                    "count": t.count[k], "n_tickers": len(t.tickers[k]),
                    "by_table": dict(t.by_table[k]), "by_column": dict(t.by_column[k]),
                    "samples": [f.to_dict() for f in t.samples[k]]}
                for k, c in CATEGORIES.items()}
        rd = self.research_dprime
        return {
            "schema": SCHEMA, "tool": TOOL, "date": self.t.isoformat(),
            "dprime": self.dprime.isoformat(), "year_boundary": self.t.year != self.dprime.year,
            "replay": self.replay, "generated_at": self.generated_at,
            "verdict": "fail" if self.rc else "pass", "rc": self.rc, "reasons": self.reasons,
            "thresholds": {"spearman_min": self.spearman_min, "flow_rel_max": FLOW_REL_MAX,
                           "flow_flip_max": FLOW_FLIP_MAX, "flow_stat_min_n": FLOW_STAT_MIN_N,
                           "diff_row_max": DIFF_ROW_MAX},
            "boards": {"evening": self.evening.to_dict(), "research": self.research.to_dict(),
                       "research_dprime": {"fi_build_id": rd.get("build_id"),
                                           "date": rd.get("date")}},
            "categories": cats,
            "n_unexplained": self.n_unexplained,
            "n_unexplained_tickers": len(t.tickers[UNEXPLAINED]),
            "unexplained_by_table": dict(t.by_table[UNEXPLAINED]),
            "unexplained": [f.to_dict() for f in t.unexplained],
            "unexplained_truncated": self.n_unexplained > len(t.unexplained),
            "fi": self.fi.tables,
            "flow_stats": self.fi.flow_stats,
            "n_targeted": self.fi.targeted,
            "not_compared": {"all_rows": dict(NOT_COMPARED), "t_rows": dict(T_ROW_NOT_COMPARED)},
            "ticker_categories": t.ticker_categories(),
            "model": self.model,
        }


def compare(t: date, evening_root: Path, research_root: Path, *, calendar_dir: Path | None = None,
            replay: bool = False, spearman_min: float = SPEARMAN_MIN,
            list_n: int = LIST_N) -> Result:
    ev = load_side(evening_root, "evening", t)
    rt = load_side(research_root, "morning", t)
    dprime = _dprime(ev, rt, t, Path(calendar_dir or Path(research_root) / "calendar"))
    rd = load_fi_run(Path(research_root) / "factor_inputs", "morning", dprime, "연구 판 D'")
    if rd.get("asof") not in (None, dprime.isoformat()):
        raise CompareInputError(f"연구 판 D' asof={rd.get('asof')!r} ≠ D'={dprime.isoformat()}")
    _check_versions(ev, rt, rd)
    evidence = Evidence(read_stage(ev, t), read_new_events(rt, dprime, t),
                        read_targets(rt, rd, dprime))
    fi = compare_fi(ev.fi, rt.fi, FiBoard(rt.fi_root, str(rd["build_id"])), t, dprime, evidence,
                    replay=replay)
    if ev.fi_run.get("equity_builds") != rd.get("equity_builds"):
        fi.tally.add(Finding("boards", "", {}, "value", UNEXPLAINED, ("equity_builds",),
                             {"equity_builds": ev.fi_run.get("equity_builds")},
                             {"equity_builds": rd.get("equity_builds")},
                             "장 마감 판이 고정한 equity 판 ≠ 연구 판 D' 의 equity 판"))
    model = compare_model(ev, rt, fi.tally, spearman_min, list_n)
    return Result(t, dprime, replay, ev, rt, rd, fi, model, spearman_min)


# ── 출력 ─────────────────────────────────────────────────────────────────────
def report_path(out_root: Path, t: date) -> Path:
    return Path(out_root) / "compare" / f"{t:%Y%m%d}.json"


def write_report(result: Result, out_root: Path) -> Path:
    path = report_path(out_root, result.t)
    _write_json(path, result.to_dict())
    return path


def _write_error(out_root: Path, t: date, message: str) -> Path | None:
    """rc 2 판정 파일 — 출력 루트가 있을 때만(없는 루트를 만들지 않는다). 못 쓰면 None."""
    if not Path(out_root).is_dir():
        return None
    path = report_path(out_root, t)
    try:
        _write_json(path, {"schema": SCHEMA, "tool": TOOL, "date": t.isoformat(),
                           "generated_at": _now(), "verdict": "error", "rc": 2, "error": message})
    except OSError:
        log.exception("두 판 대조 오류 판정 파일을 쓰지 못했다 — %s", path)
        return None
    return path


def _len(v: object) -> int:
    return len(v) if isinstance(v, list | dict) else 0


def _key_text(f: Finding) -> str:
    return " ".join(f"{k}={v}" for k, v in f.key.items() if k != "is_t")


def render(result: Result, path: Path, list_n: int = LIST_N) -> str:
    """한 화면 요약."""
    r, t = result, result.tally
    verdict = "fail" if r.rc else "pass"
    lines = [f"두 판 대조 T={r.t.isoformat()} (D'={r.dprime.isoformat()}) — 판정 {verdict} "
             f"(rc {r.rc})" + (" · 재생" if r.replay else "")
             + (" · 연초 첫 거래일" if r.t.year != r.dprime.year else "")]
    for s in (r.evening, r.research):
        lines.append(f"  {s.label:<6} fi {s.fi.build_id} · model {s.model_run.build_id}  "
                     f"({s.root})")
    lines.append(f"  연구 판 D' fi {r.research_dprime.get('build_id')}")
    parts = [f"{c.label} {t.count[k]:,}/{len(t.tickers[k]):,}" for k, c in CATEGORIES.items()]
    lines.append("차이(기록/종목): " + " · ".join(parts)
                 + f" · 미설명 {t.count[UNEXPLAINED]:,}/{len(t.tickers[UNEXPLAINED]):,}")
    lines.append("  fi 표별 행: " + " · ".join(
        f"{n} {'=' if v['hash_equal'] else format(int(str(v['n_rows_diff'])), ',')}"
        + (" (상한 초과)" if v.get("bulk") else "") for n, v in r.fi.tables.items()))
    over = [s for s, v in r.fi.flow_stats.items() if v["over"]]
    if over:
        lines.append("  수급 정의 상한 초과 주체: " + ", ".join(over))
    lines.append("모델:")
    for sid, m in r.model.items():
        rho = m["spearman"]
        cand = m["candidates"]
        assert isinstance(cand, dict)
        shown = "—" if rho is None else f"{float(str(rho)):.4f}"
        lines.append(
            f"  {sid:<20} Spearman {shown} (n {m['n_common']}) "
            f"{'통과' if m['spearman_ok'] else '미달'} · 후보 {cand['top_n']} 겹침 "
            f"{cand['overlap']} (+{_len(cand['entered'])} −{_len(cand['dropped'])}) · "
            f"한 판에만 장 마감 {_len(m['evening_only'])} · 연구 {_len(m['research_only'])}")
    if t.unexplained:
        lines.append(f"미설명 상위 {min(list_n, len(t.unexplained))} (전체 {r.n_unexplained:,}):")
        for f in t.unexplained[:list_n]:
            lines.append(f"  {f.table} {f.ticker} {_key_text(f)} {f.kind} "
                         f"{','.join(f.columns) or '-'} — {f.note}".rstrip())
    for reason in r.reasons:
        lines.append(f"  사유: {reason}")
    lines.append(f"→ {path}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=TOOL, description="두 판 대조 — 장 마감 판 T 대 다음 날 연구 판 "
                                                       "T(컷오버 PR-7 · T-36)")
    ap.add_argument("--date", required=True, help="T (YYYYMMDD)")
    ap.add_argument("--evening-root", required=True, type=Path,
                    help="장 마감 판 루트 — factor_inputs/·model/·stage/ 가 있는 곳(운영 data/model_db)")
    ap.add_argument("--research-root", required=True, type=Path,
                    help="연구 판 루트 — factor_inputs/·model/ 가 있는 곳(운영 data)")
    ap.add_argument("--calendar-dir", type=Path, default=None,
                    help="daily.calendar 연도 파일 폴더(기본 <research-root>/calendar = 운영 data/calendar)")
    ap.add_argument("--replay", action="store_true",
                    help="재생 판 — T 행이 21:05 원장(애프터마켓 종가)이라 T 종가 차이를 '종가 정의'로 본다")
    ap.add_argument("--out-root", type=Path, default=None,
                    help="compare/<T>.json 을 쓸 곳(기본 --evening-root)")
    ap.add_argument("--spearman-min", type=float, default=SPEARMAN_MIN,
                    help=f"spec 마다 종합점수 Spearman 하한(기본 {SPEARMAN_MIN} 임시 — 0 이면 기록형)")
    ap.add_argument("--list-n", type=int, default=LIST_N, help="요약 목록 길이")
    args = ap.parse_args(argv)
    try:
        t = datetime.strptime(args.date, "%Y%m%d").date()
    except ValueError:
        print(f"{TOOL} 입력 오류(rc 2): --date 는 YYYYMMDD 여야 한다: {args.date!r}",
              file=sys.stderr)
        return 2
    out_root = args.out_root or args.evening_root
    try:
        result = compare(t, args.evening_root, args.research_root, calendar_dir=args.calendar_dir,
                         replay=args.replay, spearman_min=args.spearman_min, list_n=args.list_n)
        path = write_report(result, out_root)
    except CompareInputError as e:
        msg = str(e)
    except OSError as e:          # 보고서 쓰기 실패 — 판정이 남지 않으면 '대조 못 함'(rc 2)
        msg = f"보고서를 쓰지 못했다: {type(e).__name__}: {e}"
    except Exception as e:        # 예상 밖 예외도 '대조 못 함'(rc 2) — rc 1(미설명)로 읽히지 않게
        log.exception("두 판 대조 예외 — T=%s", t.isoformat())
        msg = f"예상 밖 예외 {type(e).__name__}: {e}"
    else:
        print(render(result, path, args.list_n))
        return result.rc
    print(f"{TOOL} 입력 오류(rc 2): {msg}", file=sys.stderr)
    _write_error(out_root, t, msg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
