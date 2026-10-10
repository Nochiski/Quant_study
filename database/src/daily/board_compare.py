"""두 판 대조 — 장 마감 판 T 대 다음 날 연구 판 T(컷오버 트랙 PR-7).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 PR-7 · P5('장 마감 판 60거래일 재생 대 연구 판 — 차이가
등록 범주에 다 들어감, 미설명 0') · §4(과거 재생 + 실운영 3거래일).

  장 마감 판  <evening-root>/{factor_inputs,model}/_runs/<T>_evening.json
              (운영 `data/model_db` — T-3. T 15:5x 에 짓는 판, 정보 시점 asof = D')
  연구 판     <research-root>/{factor_inputs,model}/_runs/<T>_morning.json
              (운영 `data` — 다음 날 08:10 에 짓는 D = T 확정판)

fi 층: 표마다 content_hash(`factor_inputs.build._content_hash`)가 같으면 끝이다. 다르면 grain 으로 맞대어
(FULL OUTER JOIN) 다른 행을 찾고, 다른 칸마다 등록 범주(`CATEGORIES`) 하나로 분류한다. 맞는 범주가 없으면
`unexplained` 다 — 새 범주를 만들지 않는다. 범주 판정은 증거를 본다: 정보 표는 그 종목의 수집·공개일이
두 판에서 달라야 '정보 시점'이고, 장 마감 판에 D' 뒤 정보가 있으면(D' 자르기 위반) 미설명이다. T 전 행
(가격·수정주가·수급)은 두 판이 같아야 하므로 다르면 미설명이다(수정주가 계수·미해결 사건 표식만 정보 시점).
모델 층: spec 마다 종합점수 Spearman(`model.gates.spearman`) · 엑셀 후보(`deliver.view.load_day` 의 업종 상한
후보, spec output.top_n) 겹침 · 점수 열별 |Δ| 큰 종목. 종목마다 그 종목의 fi 범주를 붙인다 — 자기 입력
차이가 없으면 교차 단면 파급(`CROSS_SECTION`)이다. 점수 행이 한 판에만 있는데 그 종목의 fi 차이가 없으면,
또는 fi 8표가 같은데 점수가 다르면 미설명이다.

판정(rc): 0 = 미설명 0 이고 모든 spec 의 Spearman ≥ 하한 / 1 = 미설명 있음 또는 Spearman 하한 미달(셀 수
없음 포함) / 2 = 입력 오류(판 없음 · 실패 판 · date·basis 불일치 · 모델 판이 다른 fi 판을 가리킴 · 규칙
판본 다름 · 스키마 다름 · 예상 밖 예외).
산출: 표준 출력에 한 화면 요약, `<out-root>/compare/<T>.json`(기본 out-root = --evening-root). X-2 연속 창
집계·실운영 3거래일 판정이 읽는다 — rc 2 도 `verdict: error` 로 남긴다(판 루트가 있을 때).

사용: PYTHONPATH=src python -m daily.board_compare --date YYYYMMDD --evening-root data/model_db \\
          --research-root data [--out-root DIR] [--spearman-min 0.975] [--list-n 20]
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
from deliver.reader import DeliverError, ModelRun, load_run
from deliver.view import DayView, composite_of, load_day
from factor_inputs.build import _content_hash, _write_json
from model import gates as mgates
from model.build import ModelBuildError, _resolve_fi
from model.contracts import FI_TABLES
from stage import manifest

log = logging.getLogger(__name__)

TOOL = "daily.board_compare"
SCHEMA = 1                     # compare/<T>.json 모양 판본 — X-2 가 읽는다. 키를 바꾸면 올린다
# 점수 Spearman 하한 — 정본 P5 'v3 소비자 25거래일 재생: 점수 Spearman 분포(0.975~0.995)로 임계 등록'
# 의 아래 끝. 장 마감 판 60거래일 재생·그림자 3일로 이 대조의 분포를 잰 뒤 `--spearman-min` 으로 바꾸고,
# 등록하면 이 상수와 이 주석을 함께 고친다.
SPEARMAN_MIN = 0.975
LIST_N = 20                    # 요약의 미설명 목록·종합점수 |Δ| 상위 종목 수
FACTOR_TOP_N = 5               # 점수 열마다 |Δ| 상위 종목 수(JSON)
SAMPLE_N = 20                  # 범주마다 JSON 에 싣는 표본 기록 수
UNEXPLAINED_MAX = 1000         # JSON 에 싣는 미설명 기록 상한(넘으면 개수만)
SCORE_TOL = 1e-9               # 점수 |Δ| 를 '다르다' 고 볼 절대 허용치(model.compare 와 같은 값)
CROSS_SECTION = "교차 단면 — 자기 fi 입력 차이 없음(다른 종목 차이가 표준화·백분위로 번짐)"

# ── 등록 범주(한 곳) ──────────────────────────────────────────────────────────
CLOSE_DEF = "close_definition"
FLOW_DEF = "flow_definition"
CARRY = "carry"
INFO = "info_timing"
T6 = "corp_action_pending"
CUTOFF = "postclose_cutoff"
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
    Category(CLOSE_DEF, "종가 정의", "N-35 ①·N-13·T-30·T-33",
             "T 행 종가·거래량 — 장 마감 판은 15:41 키움 KRX 코드 정규장 값(postclose), 연구 판은 KRX "
             "공식 일별 값. 종가는 같아야 하지만 장 마감 동시호가 정정 등이 있을 수 있어 건수만 기록한다"
             "(by_column 으로 종가·거래량을 따로 센다). 시총·수정종가의 T 행 차이도 T 종가가 다를 때 여기 든다"),
    Category(FLOW_DEF, "수급 정의", "N-35 ②④",
             "T 행 수급 — 장 마감 판은 15:40 KRX 정규장 확정 수급, 연구 판은 21:05 하루 전체(애프터마켓 "
             "포함) 수급"),
    Category(CARRY, "이월", "T-2·FACTOR_INPUTS §2-1·§7 · PR-5(T 행 adj_ok 이월)",
             "장 마감 판 유니버스는 D' universe_daily 행 이월(종목 구성·시장·종류·정지·관리·adv20·주식수)과 "
             "고정 판 마스터(이름·상장일)다 — T 신규 상장·폐지·정지 변경과 D' 주식수 시총이 여기 든다. "
             "T 행 adj_ok 는 D' 값 이월"),
    Category(INFO, "정보 시점", "T-2·T-5·FACTOR_INPUTS §2-1",
             "장 마감 판은 정보 입력을 D' 로 자른다 — WISE(컨센서스·연간 컨센서스·재무·신선도)·DART(재무·"
             "배당·감사)·WICS·추정기관 수·기업행위 계수·미해결 사건. 연구 판은 T 에 도착한 것까지 본다"),
    Category(T6, "T-6 당일 기업행위", "T-6",
             "장 마감 판이 키움 기준가 ≠ 직전 KRX 종가·수익률 ±30% 초과·값 없음으로 eligible=false"
             "(corp_action_pending)로 둔 종목의 차이"),
    Category(CUTOFF, "16:00 컷오프", "PR-1·N-35 ①③·N-42 Q3",
             "16:00 까지 못 받았거나 16:00 뒤 받아 가격이 무효라 장 마감 판에 T 가격이 없다(못 받은 종목은 "
             "T 수급도) — no_price·시총 NULL"),
    Category(CREDIT_T, "신용 available_date ≤ T", "PR-4 리뷰·FACTOR_INPUTS §2-1(세션 축)",
             "신용잔고는 세션 축이라 장 마감 판도 available_date ≤ T(T 아침 실입수분)까지 싣는다 — "
             "available_date = T 인 행이 두 판에서 다르면"),
    Category(FY, "연도 창 = T 의 연도", "PR-4 리뷰·FACTOR_INPUTS §2-1·§5",
             "연초 첫 거래일(D' 와 T 의 해가 다름) — 당해 12월기·연간 컨센서스 연도 창은 T 의 연도인데 "
             "장 마감 판의 WISE 자료는 D' 판이라 기간 창이 다르다"),
    Category(FILING, "filing_late 경계", "PR-4 리뷰·FACTOR_INPUTS §2-1·§4",
             "filing_late — 장 마감 판은 법정기한 ∈ (D', T] 보고서를 세션 축 T 로 판정하고 T 에 공개된 "
             "공시는 보지 않는다(asof D'). 연구 판은 T 공시까지 본다"),
)}

# ── 제외 사유 어휘(fi `queries.EXCLUDE_REASONS` 와 같다 — 테스트가 대조) ─────────
NO_PRICE = "no_price"
CARRY_REASONS = ("sec_type", "market")
ESTIMATE_REASONS = ("estimates_lapsed", "estimates_none")
# T-6 제외 사유. fi 어휘에 넣는 것은 PR-5(T 행 얹기 + T-6)다 — 머지 때 그 상수를 읽게 바꾼다
T6_REASON = "corp_action_pending"

# ── 열 → 범주 ────────────────────────────────────────────────────────────────
UNI_CARRY = frozenset({"name", "listed_date", "market", "sec_type", "shares", "adv20", "is_admin",
                       "is_halted"})
UNI_INFO = frozenset({"sector_l1", "sector_l1_name", "sector_l2", "sector_l2_name", "n_analysts",
                      "audit_adverse"})
UNI_WISE = ("has_estimates", "coverage_state", "coverage_age_days")    # 신선도 — 당해 12월기(fy)
# 판 전체에서 대조하지 않는 열 — 판 기준 표식(장 마감 판 시총 기준 `t1_shares_x_t_close`, FG2·FG3 이
# 판마다 검사한다)
NOT_COMPARED: Mapping[str, tuple[str, ...]] = {"fi_universe": ("mktcap_basis",)}
# T 행에서 대조하지 않는 열 — PR-7 범위는 T 행 종가·거래량·수급이다. 시·고·저가·거래대금은 장 마감 원천
# (ka10060)이 주지 않아 연구 판과 채우는 법이 다르고 엔진이 읽지 않는다. price_source 는 T-30 어휘
# ('postclose' 대 'krx')라 구조상 다르다. T 전 행에서는 이 열들도 대조한다
T_ROW_NOT_COMPARED: Mapping[str, tuple[str, ...]] = {
    "fi_prices": ("open", "high", "low", "amount", "price_source")}
T_PRICE_COLS = frozenset({"close", "volume"})
# 정보 표의 수집·공개일 열 — 장 마감 판은 ≤ D'(asof), 다르면 정보 시점의 증거
INFO_DATE_COL: Mapping[str, str] = {"fi_consensus": "fetched_date",
                                    "fi_consensus_annual": "fetched_date",
                                    "fi_fin_summary": "available_date"}
ADJ_EVENT_COLS = frozenset({"adj_factor", "adj_ok"})


class CompareInputError(RuntimeError):
    """대조를 시작할 수 없는 입력(rc 2)."""


# ── 값 ───────────────────────────────────────────────────────────────────────
def _jsonable(v: object) -> object:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
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


# ── 기록 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Finding:
    """차이 하나를 범주 하나로 분류한 기록(행 하나가 범주별로 여러 기록이 될 수 있다)."""

    table: str                       # fi 표 또는 'model:<spec_id>'
    ticker: str
    key: dict[str, str]              # grain 열(종목 제외) → 값(ISO 문자열)
    kind: str                        # value | evening_only | research_only
    category: str
    columns: tuple[str, ...]         # 값이 다른 열(한 판에만 있는 행은 비어 있다)
    evening: dict[str, object]
    research: dict[str, object]
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"table": self.table, "ticker": self.ticker, "key": self.key, "kind": self.kind,
                "category": self.category, "columns": list(self.columns),
                "evening": self.evening, "research": self.research, "note": self.note}


@dataclass(frozen=True)
class _Pair:
    """grain 으로 맞댄 행 한 쌍(한쪽만 있을 수 있다)."""

    key: dict[str, object]
    in_e: bool
    in_r: bool
    e: dict[str, object]
    r: dict[str, object]
    diff: tuple[str, ...]            # 둘 다 있을 때 값이 다른 대조 열

    @property
    def ticker(self) -> str:
        return str(self.key["ticker"])

    @property
    def day(self) -> date | None:
        d = self.key.get("date")
        return d if isinstance(d, date) else None


@dataclass
class _Ctx:
    """분류에 쓰는 판 단위 사실."""

    t: date
    dprime: date
    uni_e: dict[str, dict[str, object]]
    uni_r: dict[str, dict[str, object]]
    tclose_e: dict[str, object]                  # T 행 종가(NULL 제외)
    tclose_r: dict[str, object]
    max_e: dict[str, dict[str, object]] = field(default_factory=dict)   # 표 → 종목 → 최근 날짜
    max_r: dict[str, dict[str, object]] = field(default_factory=dict)
    pre_t_adj: set[str] = field(default_factory=set)   # T 전 행 계수·표식이 다른 종목

    @property
    def boundary(self) -> bool:
        """연초 첫 거래일 — D' 와 T 의 해가 다르다."""
        return self.t.year != self.dprime.year

    @property
    def wise(self) -> str:
        return FY if self.boundary else INFO

    def both(self, tk: str) -> bool:
        return tk in self.uni_e and tk in self.uni_r

    def reason_e(self, tk: str) -> object:
        return self.uni_e.get(tk, {}).get("exclude_reason")

    def halted_r(self, tk: str) -> bool:
        return self.uni_r.get(tk, {}).get("is_halted") is True

    def cutoff(self, tk: str) -> bool:
        """장 마감 판에 T 종가가 없고 연구 판에는 있다."""
        return tk not in self.tclose_e and tk in self.tclose_r

    def cov_differs(self, tk: str) -> bool:
        e, r = self.uni_e.get(tk, {}), self.uni_r.get(tk, {})
        return any(not _same(e.get(c), r.get(c)) for c in UNI_WISE)


_Verdict = tuple[str, tuple[str, ...], str]          # (범주, 열, 메모)


def _group(items: Iterable[tuple[str, str, str]]) -> list[_Verdict]:
    """(열, 범주, 메모) → 범주·메모별로 열을 모은다(처음 나온 순서)."""
    out: dict[tuple[str, str], list[str]] = {}
    for col, cat, note in items:
        out.setdefault((cat, note), []).append(col)
    return [(cat, tuple(cols), note) for (cat, note), cols in out.items()]


# ── 표별 규칙 ────────────────────────────────────────────────────────────────
def _rule_universe(p: _Pair, c: _Ctx) -> list[_Verdict]:
    tk = p.ticker
    items: list[tuple[str, str, str]] = []
    for col in p.diff:
        if col in UNI_CARRY:
            items.append((col, CARRY, ""))
        elif col in UNI_INFO:
            items.append((col, INFO, ""))
        elif col in UNI_WISE:
            items.append((col, c.wise, ""))
        elif col == "filing_late":
            items.append((col, FILING, ""))
        elif col == "market_cap":
            if c.cutoff(tk):
                items.append((col, CUTOFF, ""))
            elif not _same(p.e.get("shares"), p.r.get("shares")):
                items.append((col, CARRY, "D' 주식수 × T 종가"))
            elif not _same(c.tclose_e.get(tk), c.tclose_r.get(tk)):
                items.append((col, CLOSE_DEF, ""))
            else:
                items.append((col, UNEXPLAINED, "주식수·T 종가가 같은데 시총이 다르다"))
        elif col in ("eligible", "exclude_reason"):
            items.append((col, *_eligibility(p, c)))
        else:
            items.append((col, UNEXPLAINED, "등록 범주가 없는 열"))
    return _group(items)


def _eligibility(p: _Pair, c: _Ctx) -> tuple[str, str]:
    tk = p.ticker
    er, rr = p.e.get("exclude_reason"), p.r.get("exclude_reason")
    if er == T6_REASON:
        return T6, ""
    if rr == T6_REASON:
        return UNEXPLAINED, "연구 판에 T-6 사유가 있다"
    if er == NO_PRICE and c.cutoff(tk):
        return CUTOFF, ""
    reasons = {er, rr}
    if reasons & set(CARRY_REASONS):
        return CARRY, ""
    if reasons & set(ESTIMATE_REASONS) and c.cov_differs(tk):
        return c.wise, ""
    if rr == NO_PRICE and tk not in c.tclose_r and c.halted_r(tk):
        return CARRY, "T 정지"
    return UNEXPLAINED, "제외 사유 차이를 설명할 입력 차이가 없다"


def _t_row_missing(p: _Pair, c: _Ctx) -> list[_Verdict] | None:
    """T 행이 한 판에만 있을 때(가격·수정주가). 둘 다 있으면 None."""
    if not p.in_e:
        return [(T6 if c.reason_e(p.ticker) == T6_REASON else CUTOFF, (), "")]
    if not p.in_r:
        return [(CARRY, (), "T 정지") if c.halted_r(p.ticker)
                else (UNEXPLAINED, (), "연구 판에 T 행이 없다")]
    return None


def _other_day(p: _Pair, c: _Ctx) -> list[_Verdict]:
    d = p.day
    note = "T 전 행이 다르다" if d is not None and d < c.t else "T 뒤 행"
    return [(UNEXPLAINED, p.diff, note)]


def _rule_prices(p: _Pair, c: _Ctx) -> list[_Verdict]:
    if p.day != c.t:
        return _other_day(p, c)
    missing = _t_row_missing(p, c)
    if missing is not None:
        return missing
    skip = set(T_ROW_NOT_COMPARED.get("fi_prices", ()))
    return _group((col, CLOSE_DEF if col in T_PRICE_COLS else UNEXPLAINED, "")
                  for col in p.diff if col not in skip)


def _rule_adj(p: _Pair, c: _Ctx) -> list[_Verdict]:
    d = p.day
    if d is not None and d < c.t:
        if not (p.in_e and p.in_r):
            return _other_day(p, c)
        moved = "adj_factor" in p.diff
        return _group(
            (col, INFO, "기업행위 계수·미해결 사건")
            if col in ADJ_EVENT_COLS or (col == "adj_close" and moved)
            else (col, UNEXPLAINED, "T 전 행이 다르다") for col in p.diff)
    if d != c.t:
        return _other_day(p, c)
    missing = _t_row_missing(p, c)
    if missing is not None:
        return missing
    if c.reason_e(p.ticker) == T6_REASON:
        return [(T6, p.diff, "")]
    earlier = p.ticker in c.pre_t_adj          # T 전 행부터 계수·표식이 다르다 → T 에 온 기업행위 정보
    factor: tuple[str, str] = ((INFO, "") if earlier else
                               (UNEXPLAINED, "T 행 계수만 다르다 — T-6 이 못 잡은 당일 기업행위?"))
    items: list[tuple[str, str, str]] = []
    for col in p.diff:
        if col == "adj_factor":
            items.append((col, factor[0], factor[1]))
        elif col == "adj_close":
            if "adj_factor" in p.diff:
                items.append((col, factor[0], factor[1]))
            elif not _same(c.tclose_e.get(p.ticker), c.tclose_r.get(p.ticker)):
                items.append((col, CLOSE_DEF, ""))
            else:
                items.append((col, UNEXPLAINED, "계수·T 종가가 같은데 수정종가가 다르다"))
        elif col == "adj_ok":
            items.append((col, INFO, "") if earlier else (col, CARRY, "T 행 adj_ok 는 D' 값"))
        else:
            items.append((col, UNEXPLAINED, "등록 범주가 없는 열"))
    return _group(items)


def _rule_flows(p: _Pair, c: _Ctx) -> list[_Verdict]:
    if p.day != c.t:
        return _other_day(p, c)
    if not p.in_e:
        return [(CUTOFF, (), "") if p.ticker not in c.tclose_e
                else (UNEXPLAINED, (), "장 마감 판에 T 가격은 있는데 T 수급이 없다")]
    if not p.in_r:
        return [(UNEXPLAINED, (), "연구 판에 T 수급이 없다")]
    return [(FLOW_DEF, p.diff, "")]


def _rule_credit(p: _Pair, c: _Ctx) -> list[_Verdict]:
    if c.t in (p.e.get("available_date"), p.r.get("available_date")):
        return [(CREDIT_T, p.diff, "")]
    return [(UNEXPLAINED, p.diff, "available_date < T 인 행이 다르다")]


def _info_evidence(table: str, p: _Pair, c: _Ctx) -> bool:
    """그 종목의 정보가 두 판에서 다른 날짜의 것이라는 증거."""
    col, tk = INFO_DATE_COL[table], p.ticker
    if p.in_e and p.in_r and not _same(p.e.get(col), p.r.get(col)):
        return True
    if not _same(c.max_e.get(table, {}).get(tk), c.max_r.get(table, {}).get(tk)):
        return True
    if table != "fi_consensus_annual" and c.cov_differs(tk):
        return True                                  # 신선도가 바뀌면 WISE 판이 바뀐다
    return table == "fi_consensus" and not _same(c.uni_e[tk].get("n_analysts"),
                                                 c.uni_r[tk].get("n_analysts"))


def _rule_info(table: str) -> Callable[[_Pair, _Ctx], list[_Verdict]]:
    col = INFO_DATE_COL[table]

    def rule(p: _Pair, c: _Ctx) -> list[_Verdict]:
        got = p.e.get(col)
        if p.in_e and isinstance(got, date) and got > c.dprime:
            return [(UNEXPLAINED, p.diff, f"장 마감 판 {col} {got.isoformat()} > D' — D' 자르기 위반")]
        if not _info_evidence(table, p, c):
            return [(UNEXPLAINED, p.diff, f"{col} 가 같은데 값이 다르다")]
        fy = c.boundary and (table != "fi_fin_summary" or c.cov_differs(p.ticker))
        return [(FY if fy else INFO, p.diff, "")]
    return rule


def _rule_unknown(p: _Pair, c: _Ctx) -> list[_Verdict]:
    return [(UNEXPLAINED, p.diff, "대조 규칙이 없는 표")]


_RULES: dict[str, Callable[[_Pair, _Ctx], list[_Verdict]]] = {
    "fi_universe": _rule_universe, "fi_prices": _rule_prices, "fi_adj_prices": _rule_adj,
    "fi_flows": _rule_flows, "fi_credit": _rule_credit,
    **{t: _rule_info(t) for t in INFO_DATE_COL}}


def classify(table: str, p: _Pair, c: _Ctx) -> list[_Verdict]:
    """행 한 쌍 → (범주, 열, 메모) 목록. 한 판에만 있는 종목은 표와 상관없이 이월."""
    if not c.both(p.ticker):
        return [(CARRY, p.diff, "T 유니버스 구성 — 한 판에만 있는 종목")]
    return _RULES.get(table, _rule_unknown)(p, c)


# ── fi 층 ────────────────────────────────────────────────────────────────────
@dataclass
class FiResult:
    findings: list[Finding]
    tables: dict[str, dict[str, object]]

    def ticker_categories(self, engine: str | None = None) -> dict[str, list[str]]:
        """종목 → fi 범주. `engine` 을 주면 그 엔진이 읽는 표(계약 `readers`)의 차이만 — 예: v3 엔진
        점수 차이를 신용(v4 만 읽는다) 범주로 설명하지 않는다."""
        out: dict[str, set[str]] = defaultdict(set)
        for f in self.findings:
            if f.ticker and (engine is None or engine in FI_TABLES[f.table].readers):
                out[f.ticker].add(f.category)
        return {t: sorted(v) for t, v in sorted(out.items())}


def _part(root: Path, table: str, bid: str) -> str:
    return (Path(root) / table / f"v={bid}" / "*.parquet").as_posix()


def _read(path: str) -> str:
    return f"read_parquet({_lit(path)}, hive_partitioning=false)"


def _describe(con: duckdb.DuckDBPyConnection, path: str) -> list[tuple[str, str]]:
    return [(str(r[0]), str(r[1])) for r in con.execute(
        f"DESCRIBE SELECT * FROM {_read(path)}").fetchall()]


def _records(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, object]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _pairs(con: duckdb.DuckDBPyConnection, table: str, pe: str, pr: str,
           cols: Sequence[str]) -> list[_Pair]:
    """grain 으로 맞대어 한쪽에만 있거나 대조 열 값이 다른 행(grain 순)."""
    grain = FI_TABLES[table].grain
    on = " AND ".join(f'e."{g}" = r."{g}"' for g in grain)
    keys = ", ".join(f'coalesce(e."{g}", r."{g}") AS "k__{g}"' for g in grain)
    sel = ", ".join(f'e."{x}" AS "e__{x}", r."{x}" AS "r__{x}"' for x in cols)
    neq = " OR ".join(f'e."{x}" IS DISTINCT FROM r."{x}"' for x in cols) or "false"
    g0 = grain[0]
    sql = (f'SELECT {keys}, e."{g0}" IS NOT NULL AS in_e, r."{g0}" IS NOT NULL AS in_r'
           + (f", {sel}" if sel else "")
           + f" FROM {_read(pe)} e FULL OUTER JOIN {_read(pr)} r ON {on}"
           + f' WHERE e."{g0}" IS NULL OR r."{g0}" IS NULL OR {neq}'
           + " ORDER BY " + ", ".join(f'"k__{g}"' for g in grain))
    out: list[_Pair] = []
    for row in _records(con, sql):
        in_e, in_r = bool(row["in_e"]), bool(row["in_r"])
        e = {x: row[f"e__{x}"] for x in cols} if in_e else {}
        r = {x: row[f"r__{x}"] for x in cols} if in_r else {}
        diff = tuple(x for x in cols if not _same(e[x], r[x])) if in_e and in_r else ()
        out.append(_Pair({g: row[f"k__{g}"] for g in grain}, in_e, in_r, e, r, diff))
    return out


def _finding(table: str, p: _Pair, cat: str, cols: tuple[str, ...], note: str) -> Finding:
    kind = "value" if p.in_e and p.in_r else ("evening_only" if p.in_e else "research_only")
    key = {g: str(_jsonable(v)) for g, v in p.key.items() if g != "ticker"}
    return Finding(table, p.ticker, key, kind, cat, cols,
                   {x: _jsonable(p.e.get(x)) for x in cols},
                   {x: _jsonable(p.r.get(x)) for x in cols}, note)


def _by_ticker(con: duckdb.DuckDBPyConnection, path: str) -> dict[str, dict[str, object]]:
    return {str(r["ticker"]): r for r in _records(con, f"SELECT * FROM {_read(path)}")}


def _t_close(con: duckdb.DuckDBPyConnection, path: str, t: date) -> dict[str, object]:
    return {str(tk): close for tk, close in con.execute(
        f"SELECT ticker, close FROM {_read(path)} WHERE date = DATE '{t.isoformat()}' "
        "AND close IS NOT NULL").fetchall()}


def _max_dates(con: duckdb.DuckDBPyConnection, path: str, col: str) -> dict[str, object]:
    return {str(tk): d for tk, d in con.execute(
        f'SELECT ticker, max("{col}") FROM {_read(path)} GROUP BY ticker').fetchall()}


def compare_fi(e_root: Path, e_bid: str, r_root: Path, r_bid: str, t: date,
               dprime: date) -> FiResult:
    """fi 8표 대조. `e_root`·`r_root` 는 factor_inputs 루트, `*_bid` 는 판 id, `dprime` = 장 마감 판
    asof. 스키마가 다르면 `CompareInputError`."""
    con = duckdb.connect()
    try:
        paths = {n: (_part(e_root, n, e_bid), _part(r_root, n, r_bid)) for n in FI_TABLES}
        cols: dict[str, list[str]] = {}
        for name, (pe, pr) in paths.items():
            se, sr = _describe(con, pe), _describe(con, pr)
            if se != sr:
                raise CompareInputError(f"스키마가 다르다 — table={name} 장 마감 판 {se} · 연구 판 {sr}")
            skip = {*FI_TABLES[name].grain, *NOT_COMPARED.get(name, ())}
            cols[name] = [c for c, _ in se if c not in skip]
        uni_e, uni_r = paths["fi_universe"]
        px_e, px_r = paths["fi_prices"]
        ctx = _Ctx(t, dprime, _by_ticker(con, uni_e), _by_ticker(con, uni_r),
                   _t_close(con, px_e, t), _t_close(con, px_r, t))
        for name, col in INFO_DATE_COL.items():
            ctx.max_e[name] = _max_dates(con, paths[name][0], col)
            ctx.max_r[name] = _max_dates(con, paths[name][1], col)
        findings: list[Finding] = []
        tables: dict[str, dict[str, object]] = {}
        for name, (pe, pr) in paths.items():
            he, hr = _content_hash(con, Path(pe)), _content_hash(con, Path(pr))
            info: dict[str, object] = {"hash_equal": he == hr, "hash_evening": he,
                                       "hash_research": hr, "n_rows_diff": 0, "by_category": {}}
            tables[name] = info
            if he == hr:
                continue
            pairs = _pairs(con, name, pe, pr, cols[name])
            if name == "fi_adj_prices":
                ctx.pre_t_adj = {p.ticker for p in pairs
                                 if p.in_e and p.in_r and p.day is not None and p.day < t
                                 and set(p.diff) & ADJ_EVENT_COLS}
            found: list[Finding] = []
            n_rows = 0
            for p in pairs:
                verdicts = [v for v in classify(name, p, ctx) if v[1] or not (p.in_e and p.in_r)]
                n_rows += bool(verdicts)
                found += [_finding(name, p, cat, cs, note) for cat, cs, note in verdicts]
            info["n_rows_diff"] = n_rows
            info["by_category"] = dict(Counter(f.category for f in found))
            findings += found
    finally:
        con.close()
    return FiResult(findings, tables)


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
    def fi_build_id(self) -> str:
        return str(self.fi_run["build_id"])

    def to_dict(self) -> dict[str, object]:
        return {"root": str(self.root), "basis": self.basis, "fi_build_id": self.fi_build_id,
                "model_build_id": self.model_run.build_id,
                "fi_rules_version": self.fi_run.get("rules_version"),
                "asof": self.fi_run.get("asof"), "primary_spec": self.model_run.primary_spec,
                "excluded_specs": sorted(ex) if isinstance(ex := self.model_run.meta.get(
                    "excluded_specs"), dict) else []}


def load_side(root: Path, basis: str, t: date) -> Side:
    """`root/factor_inputs/_runs/<T>_<basis>.json`(성공 판·date·basis) → fi 8표(`_meta.json`
    date·basis, `model.build._resolve_fi`) → `root/model/_runs/<T>_<basis>.json`
    (`deliver.reader.load_run`) → 모델 판이 그 fi 판을 가리키는지."""
    label = "장 마감 판" if basis == "evening" else "연구 판"
    root = Path(root)
    path = root / "factor_inputs" / "_runs" / f"{t:%Y%m%d}_{basis}.json"
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
    if run.get("date") != t.isoformat():
        raise CompareInputError(f"{label} factor_inputs 판 date={run.get('date')!r} — 기대 "
                                f"{t.isoformat()}: {path}")
    bid = str(run.get("build_id"))
    try:
        _resolve_fi(root / "factor_inputs", bid, t.isoformat(), basis)
        mrun = load_run(root / "model", t, basis)
    except (ModelBuildError, DeliverError) as e:
        raise CompareInputError(f"{label} {e}") from e
    if mrun.fi_build_id != bid:
        raise CompareInputError(f"{label} model 판 {mrun.build_id} 의 fi_build_id="
                                f"{mrun.fi_build_id} ≠ factor_inputs 판 {bid} — 다른 fi 판으로 "
                                "지은 점수다")
    return Side(label, basis, root, run, mrun)


def _asof(ev: Side, rs: Side, t: date) -> date:
    """장 마감 판 asof = D'(< T). 연구 판 asof 는 T(PR-4 이전 판은 키가 없다)."""
    raw = ev.fi_run.get("asof")
    try:
        dprime = date.fromisoformat(str(raw))
    except ValueError as e:
        raise CompareInputError(f"장 마감 판 asof={raw!r} 를 날짜로 읽지 못했다") from e
    if dprime >= t:
        raise CompareInputError(f"장 마감 판 asof={dprime.isoformat()} 가 T={t.isoformat()} 보다 "
                                "앞이 아니다(D' 여야 한다)")
    got = rs.fi_run.get("asof")
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


def _check_versions(ev: Side, rs: Side) -> None:
    """두 판이 같은 규칙 판본이어야 차이를 데이터 범주로 설명할 수 있다."""
    fe, fr = ev.fi_run.get("rules_version"), rs.fi_run.get("rules_version")
    if fe != fr:
        raise CompareInputError(f"factor_inputs rules_version 이 다르다 — 장 마감 판 {fe} · 연구 판 {fr}")
    for sid in sorted(set(ev.model_run.specs) & set(rs.model_run.specs)):
        me, mr = _model_rules(ev, sid), _model_rules(rs, sid)
        if me != mr:
            raise CompareInputError(f"model {sid} rules_version 이 다르다 — 장 마감 판 {me} · "
                                    f"연구 판 {mr}")


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


def compare_model(ev: Side, rs: Side, fi: FiResult, spearman_min: float,
                  list_n: int) -> tuple[dict[str, dict[str, object]], list[Finding]]:
    """spec 마다 Spearman · 엑셀 후보 겹침 · 한 판에만 있는 점수 행 · 점수 열 |Δ| 상위. 종목에 붙이는
    fi 범주는 그 spec 엔진이 읽는 표의 것만이다."""
    fi_same = not fi.findings
    e_specs, r_specs = set(ev.model_run.specs), set(rs.model_run.specs)
    findings = [Finding(f"model:{sid}", "", {}, "evening_only" if sid in e_specs
                        else "research_only", UNEXPLAINED, (), {}, {},
                        "spec 이 한 판에만 있다(비교 모델 격리 excluded_specs 등)")
                for sid in sorted(e_specs ^ r_specs)]
    out: dict[str, dict[str, object]] = {}
    for sid in sorted(e_specs & r_specs):
        ve, vr = _view(ev, sid), _view(rs, sid)
        cats = fi.ticker_categories(None if ve.spec is None else ve.spec.engine)
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
        for kind, tickers in (("evening_only", e_only), ("research_only", r_only)):
            findings += [Finding(f"model:{sid}", t, {}, kind, UNEXPLAINED, (), {}, {},
                                 "점수 행이 한 판에만 있는데 그 종목의 fi 입력 차이가 없다")
                         for t in tickers if not cats.get(t)]
        if fi_same and (composite["n_diff"] or e_only or r_only):
            findings.append(Finding(f"model:{sid}", "", {}, "value", UNEXPLAINED, (), {}, {},
                                    "fi 8표가 같은데 점수가 다르다 — 엔진·규칙 차이 의심"))
    return out, findings


# ── 대조 ─────────────────────────────────────────────────────────────────────
@dataclass
class Result:
    t: date
    dprime: date
    evening: Side
    research: Side
    fi: FiResult
    model: dict[str, dict[str, object]]
    model_findings: list[Finding]
    spearman_min: float
    generated_at: str = field(default_factory=_now)

    @property
    def findings(self) -> list[Finding]:
        return self.fi.findings + self.model_findings

    @property
    def unexplained(self) -> list[Finding]:
        return [f for f in self.findings if f.category == UNEXPLAINED]

    @property
    def reasons(self) -> list[str]:
        out = []
        if self.unexplained:
            out.append(f"미설명 {len(self.unexplained):,}건")
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
        by_cat: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            by_cat[f.category].append(f)
        cats = {k: {"label": c.label, "basis": c.basis, "definition": c.definition,
                    "count": len(by_cat[k]),
                    "n_tickers": len({f.ticker for f in by_cat[k] if f.ticker}),
                    "by_table": dict(Counter(f.table for f in by_cat[k])),
                    "by_column": dict(Counter(f"{f.table}.{col}" for f in by_cat[k]
                                              for col in f.columns)),
                    "samples": [f.to_dict() for f in by_cat[k][:SAMPLE_N]]}
                for k, c in CATEGORIES.items()}
        un = self.unexplained
        return {
            "schema": SCHEMA, "tool": TOOL, "date": self.t.isoformat(),
            "dprime": self.dprime.isoformat(), "year_boundary": self.t.year != self.dprime.year,
            "generated_at": self.generated_at,
            "verdict": "fail" if self.rc else "pass", "rc": self.rc, "reasons": self.reasons,
            "thresholds": {"spearman_min": self.spearman_min},
            "boards": {"evening": self.evening.to_dict(), "research": self.research.to_dict()},
            "categories": cats,
            "n_unexplained": len(un),
            "unexplained_by_table": dict(Counter(f.table for f in un)),
            "unexplained": [f.to_dict() for f in un[:UNEXPLAINED_MAX]],
            "unexplained_truncated": len(un) > UNEXPLAINED_MAX,
            "fi": self.fi.tables,
            "not_compared": {"all_rows": dict(NOT_COMPARED), "t_rows": dict(T_ROW_NOT_COMPARED)},
            "ticker_categories": self.fi.ticker_categories(),
            "model": self.model,
        }


def compare(t: date, evening_root: Path, research_root: Path, *,
            spearman_min: float = SPEARMAN_MIN, list_n: int = LIST_N) -> Result:
    ev = load_side(evening_root, "evening", t)
    rs = load_side(research_root, "morning", t)
    dprime = _asof(ev, rs, t)
    _check_versions(ev, rs)
    fi = compare_fi(ev.fi_root, ev.fi_build_id, rs.fi_root, rs.fi_build_id, t, dprime)
    model, mf = compare_model(ev, rs, fi, spearman_min, list_n)
    return Result(t, dprime, ev, rs, fi, model, mf, spearman_min)


# ── 출력 ─────────────────────────────────────────────────────────────────────
def report_path(out_root: Path, t: date) -> Path:
    return Path(out_root) / "compare" / f"{t:%Y%m%d}.json"


def write_report(result: Result, out_root: Path) -> Path:
    path = report_path(out_root, result.t)
    _write_json(path, result.to_dict())
    return path


def _write_error(out_root: Path, t: date, message: str) -> Path | None:
    """rc 2 판정 파일 — 출력 루트가 있을 때만(없는 루트를 만들지 않는다)."""
    if not Path(out_root).is_dir():
        return None
    path = report_path(out_root, t)
    _write_json(path, {"schema": SCHEMA, "tool": TOOL, "date": t.isoformat(),
                       "generated_at": _now(), "verdict": "error", "rc": 2, "error": message})
    return path


def _len(v: object) -> int:
    return len(v) if isinstance(v, list | dict) else 0


def _key_text(f: Finding) -> str:
    return " ".join(f"{k}={v}" for k, v in f.key.items())


def render(result: Result, path: Path, list_n: int = LIST_N) -> str:
    """한 화면 요약."""
    r = result
    verdict = "fail" if r.rc else "pass"
    lines = [f"두 판 대조 T={r.t.isoformat()} (D'={r.dprime.isoformat()}) — 판정 {verdict} "
             f"(rc {r.rc})" + (" · 연초 첫 거래일" if r.t.year != r.dprime.year else "")]
    for s in (r.evening, r.research):
        lines.append(f"  {s.label:<6} fi {s.fi_build_id} · model {s.model_run.build_id}  "
                     f"({s.root})")
    counts = Counter(f.category for f in r.findings)
    tickers: dict[str, set[str]] = defaultdict(set)
    for f in r.findings:
        if f.ticker:
            tickers[f.category].add(f.ticker)
    cols = Counter(f"{f.table}.{c}" for f in r.findings if f.category == CLOSE_DEF
                   for c in f.columns)
    parts = []
    for k, c in CATEGORIES.items():
        extra = (" [" + " · ".join(f"{col} {n:,}" for col, n in sorted(cols.items())) + "]"
                 if k == CLOSE_DEF and cols else "")
        parts.append(f"{c.label} {counts[k]:,}/{len(tickers[k]):,}{extra}")
    lines.append("차이(기록/종목): " + " · ".join(parts)
                 + f" · 미설명 {counts[UNEXPLAINED]:,}/{len(tickers[UNEXPLAINED]):,}")
    lines.append("  fi 표별 행: " + " · ".join(
        f"{t} {'=' if v['hash_equal'] else format(int(str(v['n_rows_diff'])), ',')}"
        for t, v in r.fi.tables.items()))
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
    un = r.unexplained
    if un:
        lines.append(f"미설명 상위 {min(list_n, len(un))} (전체 {len(un):,}):")
        for f in un[:list_n]:
            lines.append(f"  {f.table} {f.ticker} {_key_text(f)} {f.kind} "
                         f"{','.join(f.columns) or '-'} — {f.note}".rstrip())
    for reason in r.reasons:
        lines.append(f"  사유: {reason}")
    lines.append(f"→ {path}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=TOOL, description="두 판 대조 — 장 마감 판 T 대 다음 날 연구 판 "
                                                       "T(컷오버 PR-7)")
    ap.add_argument("--date", required=True, help="T (YYYYMMDD)")
    ap.add_argument("--evening-root", required=True, type=Path,
                    help="장 마감 판 루트 — factor_inputs/·model/ 가 있는 곳(운영 data/model_db)")
    ap.add_argument("--research-root", required=True, type=Path,
                    help="연구 판 루트 — factor_inputs/·model/ 가 있는 곳(운영 data)")
    ap.add_argument("--out-root", type=Path, default=None,
                    help="compare/<T>.json 을 쓸 곳(기본 --evening-root)")
    ap.add_argument("--spearman-min", type=float, default=SPEARMAN_MIN,
                    help=f"spec 마다 종합점수 Spearman 하한(기본 {SPEARMAN_MIN}, 정본 P5)")
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
        result = compare(t, args.evening_root, args.research_root,
                         spearman_min=args.spearman_min, list_n=args.list_n)
    except CompareInputError as e:
        msg = str(e)
    except Exception as e:      # 예상 밖 예외도 '대조 못 함'(rc 2) — rc 1(미설명)로 읽히지 않게
        log.exception("두 판 대조 예외 — T=%s", t.isoformat())
        msg = f"예상 밖 예외 {type(e).__name__}: {e}"
    else:
        path = write_report(result, out_root)
        print(render(result, path, args.list_n))
        return result.rc
    print(f"{TOOL} 입력 오류(rc 2): {msg}", file=sys.stderr)
    _write_error(out_root, t, msg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
