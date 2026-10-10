"""v3 소비자 재생 대조기 — 날짜별 v3 사본 대 compat 반영본(컷오버 트랙 QL-G · T-45).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P1 QL-G · §2 T-45(판정 범위) · P5('v3 소비자 재생 — 등록 범주 밖 0, 점수
Spearman 분포로 임계 등록') · `docs/COMPAT_LAYER.md` §7(의도된 차이 — 재생 대조 범주)·§8-2(그림자 날 대조). 여러 날
실행기는 `scripts/v3_replay.sh` 다.

입력 — sqlite 는 전부 `mode=ro` 로만 연다(JSON 보고만 `--json` 경로에 쓴다. `shadow-merge` 는 `--out` 에만 쓴다).
  compat 반영본       그날 v3 사본에 compat 이 반영한 결과(실행기가 사본의 복사본에 `v3_post.sh` 와 같은 순서로
                      반영한다). `_compat_meta` 의 그날(date = D) ok 기록 중 가장 나중 것이 basis · 장 마감 판 T 행
                      원천(`tables.<표>.t_rows`) · 창 밖 다시 맞춘 종목(`tables.daily_prices.rebase`) · 그날 실제로 쓴
                      equity 판(`equity_builds`)을 알려 준다
  v3 사본             같은 날 v3 가 쓴 `quant_<D>.db`
  다음 거래일 사본    (선택) v3 가 다음 거래일 수집으로 기준일 행을 덮은 뒤의 사본 — §7 daily_prices 6 판정
  equity 루트         compat 기록의 판(NIT-1)으로 `price_daily`(KRX 기준가 사슬 K — v3_backfill · KRX 행 유무 · 시총의
                      종가)·`security`(신규 스팩·T 당일 상장)를 읽는다

대상 표 = compat 이 쓰는 v3 표 전부(`v3_post.TABLES` ← `mappings.MAPPINGS`). 키 = 매핑 PK, 대조 열 = 매핑 열
(`IGNORED` 를 뺀다). 두 파일에서 정확히 같은 행은 sqlite `EXCEPT` 로 먼저 걸러 내고, 남은 키만 허용 오차로 다시 본다.
  · 정수 열은 정확히 같아야 한다. 단위를 바꾸며 반올림한 열(`ROUNDED`)만 ±1. 실수 열은 부동소수 잡음(`REAL_REL`)만.
  · `daily_prices` 는 QL-E 서버 대조(10-08 사본 `--full`)의 분류를 옮겼다 — `_price_verdict`.
    가격·거래량·adj_close 는 ±1 또는 0.15%, 거래대금 ±1(§7 대조 기준).
  · 점수 두 표는 그날(score_date = D) 행만 compat 이 쓴다 — 한쪽에만 있는 행 · 공통 종목 값 차이 · val_ev_ebitda NULL
    을 범주로 세고, 공통 종목 종합점수 Spearman 을 spec 별로 기록한다(`--min-spearman` 을 주면 하한 미달 = 미설명,
    Spearman 을 셀 수 없는 날 — 공통 종목 2 미만 등 — 은 늘 미설명, P1).

판정 — 차이마다 등록 범주(`CATEGORIES`, 근거 = §7 항목) 하나, 또는 설명되지 않은 차이(사유를 붙인다). 새 범주는 이
목록에서만 만든다. 설명되지 않은 차이는 그 열을 누가 읽는가로 세 갈래다(T-45):
  · `other`            매일 도는 소비자가 읽는 열(`DAILY_CONSUMER` — COMPAT §2-1 crontab · §2-2 unitelegram) — rc 를 정한다
  · `manual_consumer`  §2-3 리서치센터(crontab 밖, 수동)만 읽는 열(`MANUAL_CONSUMER`) — 수만 기록
  · `no_consumer`      읽는 곳이 없는 표·열(§2-4 등) — 수만 기록
  한쪽에만 있는 행은 그 표의 소비 열이 있으면 그 갈래다. 기록 수는 일반 표(가격·점수 밖)는 허용 오차 밖 열마다 1건
  (`column:<열>`), 그 밖은 행마다 1건이다.
rc: 0 = `other` 0 · 1 = `other` 있음 · 2 = 입력 오류(파일·표·compat 기록·equity 판 없음, 잘못된 날짜).

사용:
  python -m compat.v3_replay compare --compat-db P --v3-db P --date YYYYMMDD --equity-root P
                                     [--next-v3-db P] [--min-spearman X] [--json P]
  python -m compat.v3_replay dates --from YYYYMMDD --to YYYYMMDD [--calendar-dir DIR]
                                     # 거래일과 다음 거래일(탭 구분 — 실행기가 읽는다)
  python -m compat.v3_replay tsv --out P JSON...      # 날짜별 JSON → 요약 tsv
  python -m compat.v3_replay shadow-merge --evening P --refill P --date YYYYMMDD --out P
                                     # 그림자 날 compat 상태 = ⑥ 점수 + refill 7표(COMPAT §8-2)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
from daily import calendar as daily_calendar
from model.compare import TABLES as SCORE_TOTAL_COLUMN
from model.compare import spearman

from .mappings import BY_TABLE, DATE_REPLACED, EQUITY, V3_OVERWRITE_ROWS, _k_chain
from .quant_db import META_TABLE, CompatError, _parquet_source, _reference_schema
from .t_rows import SOURCE_KIWOOM_2105
from .v3_post import SCORE_TABLES, TABLES, _meta_rows, _scope, snapshot

TOOL = "compat.v3_replay"
SCHEMA = 2                     # JSON 모양 판본 — 키를 바꾸면 올린다(2: T-45 세 갈래·범주 넷 추가)

# ── 판정 상수(근거를 같이 적는다) ─────────────────────────────────────────────
# T-33 · §7 daily_prices 1 — KRX 애프터마켓(16:00~20:00) 시행일. 이날부터 v3 종가는 장후 마지막 체결가라 수준이
# 아니라 adj/close 비와 거래량으로 대조한다
AFTERMARKET_FROM = "2026-09-14"
# §7 daily_prices 4 — 등록된 v3 전 종목 오류일. 오류일 탐지(그날 공통 행 중 종가가 다른 비율 > BAD_DAY_SHARE, 09-14 전)는
# 날마다 하고 보고에 싣지만, 범주로 인정하는 것은 여기 등록된 날뿐이다(등록 안 된 날은 미설명 — §7 에 없다)
V3_BAD_DAYS = frozenset({"2026-03-27"})
BAD_DAY_SHARE = 0.5
# §7 대조 기준 — 가격·거래량·adj_close 는 ±1 또는 0.15%(키움이 조정값을 원·주 단위로 반올림한다), 거래대금 ±1
PRICE_ABS, PRICE_REL, AMOUNT_ABS = 1, 0.0015, 1
ROUND_ABS = 1                  # 단위 환산(원 → 백만원·억원)을 반올림한 정수 열(§7 ROUNDED)
REAL_REL = 1e-9                # 실수 열 — 부동소수 잡음만 같다고 본다
SAMPLE_N = 20                  # 표·범주마다 JSON 에 싣는 표본 수
OTHER_MAX = 1000               # 갈래마다 JSON 에 싣는 기록 상한(넘으면 개수만)
OTHER_PER_REASON = 50          # 그중 (표, 사유)마다 싣는 상한 — 한 사유가 목록을 다 차지하지 않게
FETCH = 50_000                 # sqlite → arrow 조각 크기
DUCKDB_THREADS, DUCKDB_MEMORY_LIMIT = 3, "8GB"     # compat.quant_db 와 같은 한도(서버 4코어를 체인과 나눠 쓴다)

# daily_prices 분류는 열 뜻(시·고·저·종·거래량·거래대금·adj)에 기대므로 매핑 열이 바뀌면 이 도구도 같이 고친다
DAILY_PRICES_COLUMNS = ("stock_code", "trade_date", "open", "high", "low", "close", "volume",
                        "amount", "adj_close")

# ── 판정 범위(T-45) — 누가 그 열을 읽는가. 출처는 COMPAT_LAYER §2 소비자 감사 ────────────────
# 표만 적고 열을 '등'으로 적은 곳은 그 표의 전 열이다(ALL).
ALL = "*"
DAILY_CONSUMER: dict[str, frozenset[str] | str] = {
    # §2-2 워치리스트 피크 카드(표만) · §2-1 브리핑 기준일·등락·거래대금(close·amount), 가설(adj_close·close·volume·amount)
    "daily_prices": ALL,
    # §2-2 수급 'foreign_investor·institution_total 등' · §2-1 가설(foreign_investor·institution_total·individual)
    "investor_detail_flows": ALL,
    # §2-1 브리핑(stock_name·market·sector) · export(…·market_cap) · api health(COUNT) · 뉴스 preview·naver_ir
    # (stock_code·is_active·market_cap) · 리서치 브로커(is_active) · §2-2 종목 메타(stock_code·stock_name·market·sector·
    # market_cap)
    "stocks": frozenset({"stock_code", "stock_name", "market", "sector", "market_cap", "is_active"}),
    # §2-2 시그널 인사이트 'r1m·r3m·r6m 등' · §2-1 export 41열·브리핑 상위 8·api health(score_date)
    "score_history": ALL,
    # §2-1 export 20열(+ score_date 조건)
    "score_history_v2": ALL,
}
MANUAL_CONSUMER: dict[str, frozenset[str]] = {
    # §2-3 S10 컨센서스 — consensus_revision_daily.collected_date·op·stock_code·target_period / compare.op_1w
    "consensus_revision_daily": frozenset({"stock_code", "collected_date", "op", "target_period"}),
    "consensus_revision_compare": frozenset({"stock_code", "op_1w"}),
}
DAILY, MANUAL, NONE = "other", "manual_consumer", "no_consumer"     # 갈래 = JSON 키


def consumer_tier(table: str, cols: Iterable[str] | None) -> str:
    """열(들)을 읽는 소비자 갈래 — `cols` 가 None 이면 행 전체(한쪽에만 있는 행)."""
    daily, manual = DAILY_CONSUMER.get(table), MANUAL_CONSUMER.get(table, frozenset())
    if daily is not None and (cols is None or daily == ALL or set(cols) & set(daily)):
        return DAILY
    if manual and (cols is None or set(cols) & manual):
        return MANUAL
    return NONE


# ── 등록 범주(한 곳) ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Category:
    key: str
    label: str
    basis: str                 # COMPAT_LAYER §7 항목 · 결정 번호


NEW_SPAC = "new_spac"
SCORE_UNIVERSE = "score_universe"
SCORE_COMMON = "score_common"
SCORE_EV_NULL = "score_val_ev_ebitda_null"
EVE_POSTCLOSE = "evening_t_postclose"
EVE_FILL = "evening_t_fill"
EVE_STEP_NULL = "evening_t_step_null"
EVE_NEW_LISTING = "evening_t_new_listing"
EVE_MISSING = "evening_t_missing"
T33 = "T33"
V3_BACKFILL = "v3_backfill"
V3_ADJ_NULL = "v3_adj_null"
V3_BAD_DAY = "v3_bad_day"
BASE_DAY = "base_day_2005"
BASE_DAY_OPEN = "base_day_unconfirmed"
REBASE_ADJ_NULL = "rebase_adj_null"
MCAP_CLOSE = "market_cap_close_definition"
DELIST_TIMING = "delisting_timing"
V3_MISSED_DAY = "v3_missed_day"

CATEGORIES: tuple[Category, ...] = (
    Category(NEW_SPAC, "신규 스팩 — compat 에만 있는 행(v3 stocks 누락 교정)", "§7 신규 스팩 · T-25"),
    Category(SCORE_UNIVERSE, "그날 점수 행이 한쪽에만 있다(유니버스 결정 — 추정치 보유 종목만)",
             "§7 점수 종목 수 · T-17"),
    Category(SCORE_COMMON, "공통 종목 점수 값 차이(유니버스 안 표준화·원시 입력 열 — 순위 Spearman 으로 대조)",
             "§7 점수 종목 수 · T-17"),
    Category(SCORE_EV_NULL, "score_history.val_ev_ebitda 는 늘 NULL(scope 가 밸류에 안 쓴다)", "§7 점수 종목 수"),
    Category(EVE_POSTCLOSE, "장 마감 판 T 행 postclose 원천 — KRX 공식 종가·16:00 전 거래량·수급",
             "§7 장 마감 판 T 행 · QL-D · T-33"),
    Category(EVE_FILL, "장 마감 판 T 행 채움 — 시·고·저가 = 종가, 거래대금 = 종가 × 거래량 근사",
             "§7 장 마감 판 T 행 · T-32"),
    Category(EVE_STEP_NULL, "장 마감 판 T 단계 미상 — T 행 adj_close NULL", "§7 daily_prices 5"),
    Category(EVE_NEW_LISTING, "T 당일 신규 상장 — v3 에만 있다(대상 = D' 유니버스 이월)", "§7 장 마감 판 T 행"),
    Category(EVE_MISSING, "장 마감 판 T 행이 없는 종목 — v3 에만 있다(t_rows missing, 기록형)",
             "§7 장 마감 판 T 행"),
    Category(T33, "T-33 종가 정의 — 애프터마켓 뒤 수준만 다르고 adj/close 비·거래량은 같다",
             "§7 daily_prices 1 · T-33"),
    Category(V3_BACKFILL, "v3 옛 일괄 백필 — 행 d 가 d 뒤 4번째 행보다 뒤 날 기준 수정값", "§7 daily_prices 2"),
    Category(V3_ADJ_NULL, "v3 adj_close NULL(GAP-4 후보 · v3_defect)", "§7 daily_prices 3"),
    Category(V3_BAD_DAY, "v3 전 종목 오류일(V3_BAD_DAYS 에 등록된 날만, 거래대금만 다른 행 포함)",
             "§7 daily_prices 4"),
    Category(BASE_DAY, "기준일 행 = v3 20:05 수집 시점 값 — 다음 거래일 사본과는 맞다", "§7 daily_prices 6"),
    Category(BASE_DAY_OPEN, "기준일 행 미확정 — 다음 거래일 사본 없이 v3 20:05 값과 다르다", "§7 daily_prices 6"),
    Category(REBASE_ADJ_NULL, "창 밖 다시 맞춘 종목의 equity 행 없는 날 — adj_close 만 NULL(v3_post warn)",
             "§7 daily_prices 창 밖"),
    Category(MCAP_CLOSE, "stocks.market_cap 종가 정의 — 함의 주식수(시총 ÷ 그날 종가)가 같고 억원 반올림 ±1",
             "§7 T-45 ① · T-33"),
    Category(DELIST_TIMING, "상장폐지 반영 시점 — compat 은 KRX 폐지일로 is_active=0·시총·폐지일, v3 는 아직 1·NULL",
             "§7 T-45 ②"),
    Category(V3_MISSED_DAY, "v3 그날 수집 누락 — v3 가 아는 종목의 그날 행이 v3 에 없고 KRX price_daily 에 있다",
             "§7 T-45 ③"),
)

# 설명되지 않은 차이의 사유(범주가 아니다)
ONLY_COMPAT, ONLY_V3 = "only_in_compat", "only_in_v3"
ONLY_COMPAT_OLD = "only_in_compat_before_v3"   # v3 daily_prices 첫 날보다 이른 compat 행(`--full` 창이 v3 이력보다 길다)
T33_BAD = "T33_ratio_or_volume"          # 09-14 뒤 비 또는 거래량이 어긋남(결함 후보)
PRE_BAD = "pre_mismatch"                 # 09-14 전 가격·거래량·adj 가 어긋나고 어느 범주로도 설명 안 됨
AMOUNT = "amount"
AMOUNT_NULL_V3, AMOUNT_NULL_COMPAT = "amount_null_v3", "amount_null_compat"   # 한쪽만 거래대금이 비었다
NEXT_MISSING = "base_day_next_missing"   # 다음 거래일 사본에 그 행이 없다
SPEARMAN_LOW = "spearman_below_min"
SPEARMAN_NONE = "spearman_none"          # 공통 종목 2 미만 등으로 셀 수 없다(P1 — 늘 미설명)
OFF_DATE = "score_date_not_D"            # 점수 표에서 그날 밖 행이 다르다(compat 은 그날만 쓴다)

# 대조하지 않는 열 — 값이 아니라 쓰기 시각이다(v3 는 datetime('now'), compat 은 export 시각 — mappings stocks 주석)
IGNORED: dict[str, dict[str, str]] = {
    "stocks": {"updated_at": "쓰기 시각(v3 datetime('now') 자리에 compat 은 export 시각)"},
}
# 단위 환산을 반올림한 정수 열(±ROUND_ABS) — 그 밖 정수·문자 열은 정확히 같아야 한다
ROUNDED: dict[str, frozenset[str]] = {
    "stocks": frozenset({"market_cap"}),                                   # 원 → 억원
    "investor_detail_flows": frozenset(                                    # 원 → 백만원(12주체)
        c for c in BY_TABLE["investor_detail_flows"].columns
        if c not in BY_TABLE["investor_detail_flows"].pk),
    "consensus_revision_daily": frozenset({"eps", "bps"}),
    "consensus_revision_compare": frozenset(
        c for c in BY_TABLE["consensus_revision_compare"].columns if c.startswith(("eps_", "bps_"))),
    "consensus_annual": frozenset({"eps", "bps"}),
    "financial_summary": frozenset({"revenue", "op", "ni", "eps", "bps", "fcf", "capex", "shares",
                                    "gross_profit", "total_assets"}),  # 억원 반올림·주식수 반올림
}
# 장 마감 판 T 행 원천 기록(`t_rows._info`)의 키
KIWOOM_TICKERS_KEY = f"{SOURCE_KIWOOM_2105}_tickers"
MISSING_TICKERS_KEY = "missing_tickers"


class ReplayInputError(CompatError):
    """대조를 시작할 수 없다(파일·표·compat 기록·equity 판 없음, 잘못된 날짜) — rc 2."""


# ── 집계 ──────────────────────────────────────────────────────────────────────
@dataclass
class TableTally:
    """표 하나의 대조 결과."""

    table: str
    pk: tuple[str, ...]
    columns: tuple[str, ...]
    n_compat: int = 0
    n_v3: int = 0
    n_diff: int = 0            # 정확히 같지 않은 키(한쪽에만 있는 키 포함)
    n_within_tol: int = 0      # 허용 오차 안이라 같다고 본 키
    categories: Counter = field(default_factory=Counter)
    samples: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    tiers: dict[str, Counter] = field(default_factory=lambda: {DAILY: Counter(), MANUAL: Counter(),
                                                               NONE: Counter()})

    def to_dict(self) -> dict[str, object]:
        return {"pk": list(self.pk), "n_compat": self.n_compat, "n_v3": self.n_v3,
                "n_diff": self.n_diff, "n_within_tol": self.n_within_tol,
                "categories": {k: {"rows": n, "samples": self.samples.get(k, [])}
                               for k, n in sorted(self.categories.items())},
                **{tier: dict(sorted(c.items())) for tier, c in self.tiers.items()}}


@dataclass
class Tally:
    """표 전체 — 갈래마다 기록은 (표, 사유)마다 `OTHER_PER_REASON`, 모두 `OTHER_MAX` 까지만 싣고 개수는 다 센다."""

    tables: dict[str, TableTally] = field(default_factory=dict)
    records: dict[str, list[dict]] = field(default_factory=lambda: {DAILY: [], MANUAL: [], NONE: []})
    counts: Counter = field(default_factory=Counter)
    _kept: Counter = field(default_factory=Counter)

    def explain(self, t: TableTally, key: str, rec: dict) -> None:
        t.categories[key] += 1
        if len(t.samples[key]) < SAMPLE_N:
            t.samples[key].append(rec)

    def miss(self, t: TableTally, reason: str, rec: dict, cols: Iterable[str] | None) -> None:
        """설명되지 않은 차이 — `cols` 를 읽는 소비자 갈래로 센다(None 이면 행 전체)."""
        tier = consumer_tier(t.table, cols)
        t.tiers[tier][reason] += 1
        self.counts[tier] += 1
        kept = self.records[tier]
        if len(kept) < OTHER_MAX and self._kept[(tier, t.table, reason)] < OTHER_PER_REASON:
            self._kept[(tier, t.table, reason)] += 1
            kept.append({"table": t.table, "reason": reason, **rec})


@dataclass(frozen=True)
class Context:
    """분류에 쓰는 그날 사실."""

    d_iso: str
    basis: str
    kiwoom_2105: frozenset[str]      # 장 마감 판 T 행을 21:05 원장으로 만든 종목(나머지는 postclose)
    missing: frozenset[str]          # 장 마감 판 T 행이 없는 종목
    new_spacs: frozenset[str]        # equity security 의 스팩 중 v3 stocks 에 없는 종목
    listed_on_d: frozenset[str]      # equity security 상장일 = D
    has_next: bool
    v3_stocks: frozenset[str]        # v3 사본 stocks 종목
    v3_first: str | None             # v3 사본 daily_prices 첫 날
    v3_first_by_code: Mapping[str, str]  # 종목별 v3 사본 daily_prices 첫 날
    rebase_tickers: frozenset[str]   # compat 이 창 밖을 다시 맞춘 종목(`tables.daily_prices.rebase`)
    rebase_before: str | None        # 그 창 시작(이 날 앞 행이 대상)
    krx_on_d: frozenset[str]         # equity price_daily(그날 판) D 의 KRX 행 종목
    close_x: Mapping[str, float]     # compat 시총의 종가 — equity 판에서 시총이 있는 D 이하 마지막 KRX 종가
    close_v: Mapping[str, float]     # v3 사본 daily_prices D 종가

    @property
    def evening(self) -> bool:
        return self.basis == "evening"

    def v3_knows(self, code: str, day: str) -> bool:
        """v3 가 아는 종목 — v3 stocks 에 있거나 그날 앞에 v3 daily_prices 행이 있다(T-45 ③)."""
        first = self.v3_first_by_code.get(code)
        return code in self.v3_stocks or (first is not None and first < day)


@dataclass(frozen=True)
class Verdict:
    """판정 — 범주(설명됨) 또는 미설명 사유 중 하나. 둘 다 None 이면 허용 오차 안(같다)."""

    category: str | None = None
    reason: str | None = None


# ── 입력 ──────────────────────────────────────────────────────────────────────
def _iso(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as e:
        raise ReplayInputError(f"--date 는 YYYYMMDD 여야 한다: {value!r}") from e


def _ro_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _open(compat_db: Path, v3_db: Path) -> sqlite3.Connection:
    """compat 반영본(main) + v3 사본(v3) — 둘 다 읽기 전용."""
    for p, label in ((compat_db, "compat 반영본"), (v3_db, "v3 사본")):
        if not Path(p).is_file():
            raise ReplayInputError(f"{label} 이 없다: {p}")
    con = sqlite3.connect(_ro_uri(Path(compat_db)), uri=True)
    try:
        con.execute("ATTACH DATABASE ? AS v3", (_ro_uri(Path(v3_db)),))
        for schema, path in (("main", compat_db), ("v3", v3_db)):
            have = {r[0] for r in con.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}
            lack = [t for t in TABLES if t not in have]
            if lack:
                raise ReplayInputError(f"compat 표가 없다: {lack} (파일 {path})")
    except (sqlite3.DatabaseError, ReplayInputError) as e:
        con.close()
        if isinstance(e, ReplayInputError):
            raise
        raise ReplayInputError(f"sqlite 로 열 수 없다(compat={compat_db} v3={v3_db}): {e}") from e
    return con


def _record(con: sqlite3.Connection, d_iso: str) -> dict:
    """compat 반영본의 그날 ok 반영 기록 중 가장 나중 것."""
    rows = [r for r in _meta_rows(con)
            if r.get("date") == d_iso and r.get("status") == "ok" and r.get("basis") in ("evening", "morning")]
    if not rows:
        raise ReplayInputError(f"compat 반영본 _compat_meta 에 {d_iso} ok 반영 기록이 없다 — compat 이 그날 반영하지 "
                               "않은 파일이다")
    return max(rows, key=lambda r: str(r["exported_at"]))


def _json_field(record: Mapping[str, object], key: str) -> object:
    raw = record.get(key)
    return json.loads(str(raw)) if raw not in (None, "") else None


def _tables_info(record: Mapping[str, object], table: str, key: str) -> dict:
    """기록의 `tables.<표>.<key>` — 표 자기 것, 없으면 daily_prices 것(T 행은 두 표가 같은 선택이다)."""
    tables = _json_field(record, "tables")
    tables = tables if isinstance(tables, dict) else {}
    got = (tables.get(table) or {}).get(key) or (tables.get("daily_prices") or {}).get(key)
    return got if isinstance(got, dict) else {}


def _duck() -> duckdb.DuckDBPyConnection:
    duck = duckdb.connect()
    duck.execute(f"SET threads = {DUCKDB_THREADS}")
    duck.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    duck.execute("SET enable_progress_bar=false")
    return duck


def _equity(equity_root: Path, table: str, builds: Mapping[str, str]) -> tuple[str, str]:
    """compat 이 그날 실제로 쓴 equity 판(NIT-1 — 기록의 `equity_builds`)."""
    build = builds.get(table)
    if build is None:
        raise ReplayInputError(f"compat 기록 equity_builds 에 {table} 판이 없다({sorted(builds)}) — 그 표를 읽은 "
                               "반영이 아니다")
    try:
        return _parquet_source(Path(equity_root), table, EQUITY, str(build))
    except Exception as e:  # noqa: BLE001  # reason: MANIFEST 없음·판 GC 를 입력 오류(rc 2)로 모은다
        raise ReplayInputError(f"equity 판을 못 읽는다: root={equity_root} table={table} build={build} "
                               f"{type(e).__name__}: {e}") from e


def _context(con: sqlite3.Connection, duck: duckdb.DuckDBPyConnection, pd_expr: str, sec_expr: str,
             record: Mapping[str, object], d_iso: str, has_next: bool) -> Context:
    t_rows = _tables_info(record, "daily_prices", "t_rows")
    rebase = _tables_info(record, "daily_prices", "rebase")
    v3_stocks = {str(r[0]) for r in con.execute("SELECT stock_code FROM v3.stocks")}
    first_by_code = {str(r[0]): str(r[1]) for r in con.execute(
        "SELECT stock_code, min(trade_date) FROM v3.daily_prices GROUP BY stock_code")}
    spacs = {str(r[0]) for r in duck.execute(f"SELECT ticker FROM {sec_expr} WHERE sec_type = 'spac'").fetchall()}
    listed = {str(r[0]) for r in duck.execute(
        f"SELECT ticker FROM {sec_expr} WHERE list_date = DATE '{d_iso}'").fetchall()}
    krx_d = {str(r[0]) for r in duck.execute(
        f"SELECT ticker FROM {pd_expr} WHERE basis = 'krx' AND date = DATE '{d_iso}'").fetchall()}
    close_x = {str(r[0]): float(r[1]) for r in duck.execute(
        f"SELECT ticker, arg_max(close, date) FROM {pd_expr} WHERE basis = 'krx' AND date <= DATE '{d_iso}' "
        "AND mktcap_krw IS NOT NULL AND close IS NOT NULL GROUP BY ticker").fetchall()}
    close_v = {str(r[0]): float(r[1]) for r in con.execute(
        "SELECT stock_code, close FROM v3.daily_prices WHERE trade_date = ? AND close IS NOT NULL", (d_iso,))}
    return Context(d_iso=d_iso, basis=str(record["basis"]),
                   kiwoom_2105=frozenset(str(t) for t in t_rows.get(KIWOOM_TICKERS_KEY) or ()),
                   missing=frozenset(str(t) for t in t_rows.get(MISSING_TICKERS_KEY) or ()),
                   new_spacs=frozenset(spacs - v3_stocks), listed_on_d=frozenset(listed),
                   has_next=has_next, v3_stocks=frozenset(v3_stocks),
                   v3_first=min(first_by_code.values()) if first_by_code else None,
                   v3_first_by_code=first_by_code,
                   rebase_tickers=frozenset(str(t) for t in rebase.get("tickers") or ()),
                   rebase_before=None if rebase.get("before") is None else str(rebase["before"]),
                   krx_on_d=frozenset(krx_d), close_x=close_x, close_v=close_v)


def _k_table(con: sqlite3.Connection, duck: duckdb.DuckDBPyConnection, pd_expr: str, d_iso: str) -> None:
    """K 사슬 임시 표 `k` — compat 과 같은 정의(`mappings._k_chain`, T-40). raw = equity 원종가, rn = 그 종목 krx 행
    순서. 하한은 두 파일 daily_prices 의 첫 날. v3_backfill 판정과 'KRX 에 그날 행이 있다'(T-45 ③)가 같이 쓴다."""
    lo = min((r[0] for r in (con.execute("SELECT min(trade_date) FROM main.daily_prices").fetchone(),
                             con.execute("SELECT min(trade_date) FROM v3.daily_prices").fetchone())
              if r is not None and r[0] is not None), default=d_iso)
    k_sql = ("WITH " + _k_chain("from_date") + "\nSELECT ticker, date, close AS raw, k_d AS k, "
             "row_number() OVER (PARTITION BY ticker ORDER BY date) AS rn FROM chain")
    duck.execute("CREATE TEMP TABLE k AS " + k_sql.format(price_daily=pd_expr, from_date=str(lo), date=d_iso))


def _krx_row(duck: duckdb.DuckDBPyConnection, ctx: Context, code: str, day: str) -> bool:
    if day == ctx.d_iso:
        return code in ctx.krx_on_d
    row = duck.execute("SELECT count(*) FROM k WHERE ticker = ? AND date = CAST(? AS DATE)", [code, day]).fetchone()
    return bool(row and row[0])


def _compat_only(duck: duckdb.DuckDBPyConnection, ctx: Context, code: str, day: str | None) -> Verdict:
    """compat 에만 있는 행 — 신규 스팩 · v3 이력 앞 · v3 그날 수집 누락(T-45 ③) · 그 밖."""
    if code in ctx.new_spacs:
        return Verdict(category=NEW_SPAC)
    if day is None:
        return Verdict(reason=ONLY_COMPAT)
    if ctx.v3_first is not None and day < ctx.v3_first:
        return Verdict(reason=ONLY_COMPAT_OLD)
    if ctx.v3_knows(code, day) and _krx_row(duck, ctx, code, day):
        return Verdict(category=V3_MISSED_DAY)
    return Verdict(reason=ONLY_COMPAT)


# ── 일반 표(키 단위 차이 → 열 단위 허용 오차) ─────────────────────────────────
def _q(c: str) -> str:
    return f'"{c}"'


def _diff_rows(con: sqlite3.Connection, table: str, cols: Sequence[str], pk: Sequence[str]
               ) -> Iterable[tuple[tuple, tuple | None, tuple | None]]:
    """정확히 같지 않은 키마다 (키, compat 행 | None, v3 행 | None). 같은 행은 sqlite EXCEPT 가 거른다."""
    sel, keys = ", ".join(map(_q, cols)), ", ".join(map(_q, pk))
    t = _q(table)
    con.execute("DROP TABLE IF EXISTS temp._v3_replay_keys")
    con.execute(f"CREATE TEMP TABLE _v3_replay_keys AS "
                f"SELECT {keys} FROM (SELECT {sel} FROM main.{t} EXCEPT SELECT {sel} FROM v3.{t}) "
                f"UNION SELECT {keys} FROM (SELECT {sel} FROM v3.{t} EXCEPT SELECT {sel} FROM main.{t})")
    on = lambda side: " AND ".join(f"{side}.{_q(c)} = k.{_q(c)}" for c in pk)  # noqa: E731
    cur = con.execute(
        f"SELECT {', '.join('k.' + _q(c) for c in pk)}, {', '.join('a.' + _q(c) for c in cols)}, "
        f"{', '.join('b.' + _q(c) for c in cols)}, a.{_q(pk[0])} IS NOT NULL, b.{_q(pk[0])} IS NOT NULL "
        f"FROM temp._v3_replay_keys k LEFT JOIN main.{t} a ON {on('a')} LEFT JOIN v3.{t} b ON {on('b')} "
        f"ORDER BY {', '.join('k.' + _q(c) for c in pk)}")
    n_pk, n = len(pk), len(cols)
    for r in cur:
        yield (tuple(r[:n_pk]), tuple(r[n_pk:n_pk + n]) if r[-2] else None,
               tuple(r[n_pk + n:n_pk + 2 * n]) if r[-1] else None)


def _same(table: str, col: str, kind: str, a: object, b: object) -> bool:
    if a is None or b is None:
        return a is None and b is None
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        if col in ROUNDED.get(table, ()):
            return abs(a - b) <= ROUND_ABS
        if kind == "REAL":
            return abs(a - b) <= REAL_REL * max(1.0, abs(a), abs(b))
    return a == b


def _rec(pk: Sequence[str], key: tuple, cols: Sequence[str], a: tuple | None, b: tuple | None,
         only: Iterable[str] | None = None) -> dict:
    """표본·미설명 기록 — 키와 (고른) 열의 [compat, v3] 값."""
    pick = set(cols) - set(pk) if only is None else set(only)
    return {"key": dict(zip(pk, key, strict=True)),
            "columns": {c: [None if a is None else a[i], None if b is None else b[i]]
                        for i, c in enumerate(cols) if c in pick}}


def _key_date(table: str, key: tuple, pk: Sequence[str]) -> str | None:
    col = DATE_REPLACED.get(table)
    return None if col is None else str(dict(zip(pk, key, strict=True))[col])


def _stocks_column(duck: duckdb.DuckDBPyConnection, ctx: Context, col: str, a: Mapping[str, object],
                   b: Mapping[str, object]) -> Verdict:
    """stocks 열 — T-45 ① 시총 종가 정의 · ② 상장폐지 반영 시점 · ③ v3 그날 수집 누락(시총 NULL)."""
    code = str(a["stock_code"])
    delisted = a.get("delisted_date") is not None and str(a["delisted_date"]) <= ctx.d_iso
    x, v = a.get(col), b.get(col)
    if col == "market_cap" and isinstance(x, int | float) and isinstance(v, int | float):
        cx, cv = ctx.close_x.get(code), ctx.close_v.get(code)
        if cx and cv and abs(x - v * cx / cv) <= ROUND_ABS:
            return Verdict(category=MCAP_CLOSE)
    elif col == "market_cap" and x is not None and v is None:
        if delisted:
            return Verdict(category=DELIST_TIMING)
        if code not in ctx.close_v and ctx.v3_knows(code, ctx.d_iso) and _krx_row(duck, ctx, code, ctx.d_iso):
            return Verdict(category=V3_MISSED_DAY)
    elif col == "is_active" and x == 0 and v == 1 and delisted:
        return Verdict(category=DELIST_TIMING)
    elif col == "delisted_date" and delisted and v is None:
        return Verdict(category=DELIST_TIMING)
    return Verdict(reason=f"column:{col}")


def _compare_generic(con: sqlite3.Connection, duck: duckdb.DuckDBPyConnection, ctx: Context, tally: Tally,
                     t: TableTally, kinds: Mapping[str, str]) -> None:
    """점수 표·daily_prices 밖의 표 — 한쪽에만 있는 행 · 열마다 허용 오차 밖 차이."""
    cols, pk = t.columns, t.pk
    t_rows_table = ctx.evening and t.table in DATE_REPLACED
    for key, a, b in _diff_rows(con, t.table, cols, pk):
        t.n_diff += 1
        code = str(dict(zip(pk, key, strict=True)).get("stock_code"))
        day = _key_date(t.table, key, pk)
        rec = _rec(pk, key, cols, a, b)
        if a is None or b is None:
            if b is None:
                v = _compat_only(duck, ctx, code, day)
            elif t_rows_table and day == ctx.d_iso and code in ctx.listed_on_d:
                v = Verdict(category=EVE_NEW_LISTING)
            elif t_rows_table and day == ctx.d_iso and code in ctx.missing:
                v = Verdict(category=EVE_MISSING)
            else:
                v = Verdict(reason=ONLY_V3)
            if v.category:
                tally.explain(t, v.category, rec)
            else:
                tally.miss(t, str(v.reason), rec, None)
            continue
        diff = [c for i, c in enumerate(cols) if c not in pk and not _same(t.table, c, kinds[c], a[i], b[i])]
        if not diff:
            t.n_within_tol += 1
            continue
        if t_rows_table and day == ctx.d_iso and code not in ctx.kiwoom_2105:
            tally.explain(t, EVE_POSTCLOSE, _rec(pk, key, cols, a, b, diff))   # 수급 15:40 확정 대 v3 하루 전체
            continue
        ad, bd = dict(zip(cols, a, strict=True)), dict(zip(cols, b, strict=True))
        by_cat: dict[str, list[str]] = defaultdict(list)
        for c in diff:
            v = _stocks_column(duck, ctx, c, ad, bd) if t.table == "stocks" else Verdict(reason=f"column:{c}")
            if v.category:
                by_cat[v.category].append(c)
            else:
                tally.miss(t, str(v.reason), _rec(pk, key, cols, a, b, (c,)), (c,))
        for cat, cs in by_cat.items():
            tally.explain(t, cat, _rec(pk, key, cols, a, b, cs))


# ── 점수 두 표 ────────────────────────────────────────────────────────────────
def _compare_scores(con: sqlite3.Connection, ctx: Context, tally: Tally, t: TableTally,
                    kinds: Mapping[str, str], min_spearman: float) -> dict[str, object]:
    """그날 행: 한쪽에만 있음 = 유니버스, 공통 종목 값 차이 = 순위로 대조(Spearman), val_ev_ebitda NULL."""
    cols, pk = t.columns, t.pk
    i_date = cols.index("score_date")
    for key, a, b in _diff_rows(con, t.table, cols, pk):
        t.n_diff += 1
        row = a if a is not None else b
        assert row is not None
        rec = _rec(pk, key, cols, a, b)
        if row[i_date] != ctx.d_iso:
            tally.miss(t, OFF_DATE, rec, None)
            continue
        if a is None or b is None:
            tally.explain(t, SCORE_UNIVERSE, _rec(pk, key, cols, a, b, ()))
            continue
        diff = [c for i, c in enumerate(cols) if c not in pk and not _same(t.table, c, kinds[c], a[i], b[i])]
        if not diff:
            t.n_within_tol += 1
            continue
        i_ev = cols.index("val_ev_ebitda") if "val_ev_ebitda" in cols else None
        if i_ev is not None and "val_ev_ebitda" in diff and a[i_ev] is None:
            tally.explain(t, SCORE_EV_NULL, _rec(pk, key, cols, a, b, ("val_ev_ebitda",)))
            diff.remove("val_ev_ebitda")
        if diff:
            tally.explain(t, SCORE_COMMON, _rec(pk, key, cols, a, b, diff))
    total = SCORE_TOTAL_COLUMN[t.table]
    sides = {}
    for schema in ("main", "v3"):
        sides[schema] = {str(r[0]): float(r[1]) for r in con.execute(
            f"SELECT stock_code, {_q(total)} FROM {schema}.{_q(t.table)} WHERE score_date = ? "
            f"AND {_q(total)} IS NOT NULL", (ctx.d_iso,))}
    common = sorted(set(sides["main"]) & set(sides["v3"]))
    rho = spearman([sides["main"][k] for k in common], [sides["v3"][k] for k in common])
    judged = min_spearman > 0
    ok = rho is not None and (not judged or rho >= min_spearman)
    if not ok:
        tally.miss(t, SPEARMAN_NONE if rho is None else SPEARMAN_LOW,
                   {"key": {"score_date": ctx.d_iso},
                    "columns": {total: [rho, min_spearman], "n_common": [len(common), None]}}, (total,))
    compat_only = sorted(set(sides["main"]) - set(sides["v3"]))
    v3_only = sorted(set(sides["v3"]) - set(sides["main"]))
    return {"spec": BY_TABLE[t.table].sources[0], "total_column": total, "n_common": len(common),
            "n_compat_only": len(compat_only), "n_v3_only": len(v3_only),
            "compat_only": compat_only, "v3_only": v3_only,          # 종목 집합 차이(T-17 — 그날 수백 종목)
            "spearman": rho, "min_spearman": min_spearman, "judged": judged, "ok": ok}


# ── daily_prices(QL-E 서버 대조 분류) ─────────────────────────────────────────
_PRICE_SCHEMA = pa.schema([("stock_code", pa.string()), ("trade_date", pa.string())]
                          + [(c, pa.float64()) for c in DAILY_PRICES_COLUMNS[2:]])
_VALUES = DAILY_PRICES_COLUMNS[2:]


def _arrow(cur: sqlite3.Cursor, source: str) -> pa.Table:
    """sqlite 결과 → arrow 표(조각 단위 — 행 전체를 파이썬 객체로 한 번에 올리지 않는다)."""
    batches: list[pa.RecordBatch] = []
    while rows := cur.fetchmany(FETCH):
        try:
            cols = list(zip(*rows, strict=True))
            arrays = [pa.array([None if v is None else str(v) for v in cols[0]], pa.string()),
                      pa.array([None if v is None else str(v) for v in cols[1]], pa.string())]
            arrays += [pa.array([None if v is None else float(v) for v in col], pa.float64())
                       for col in cols[2:]]
        except (TypeError, ValueError) as e:
            raise ReplayInputError(f"{source} daily_prices 값을 숫자로 못 읽는다(첫 행 {rows[0]}): {e}") from e
        batches.append(pa.RecordBatch.from_arrays(arrays, schema=_PRICE_SCHEMA))
    return pa.Table.from_batches(batches, schema=_PRICE_SCHEMA)


def _load_prices(duck: duckdb.DuckDBPyConnection, name: str, cur: sqlite3.Cursor, prefix: str,
                 source: str) -> None:
    """arrow 표를 duckdb 임시 표 `name` 으로 — 열 이름은 `<prefix>_<열>`, trade_date 는 DATE."""
    tbl = _arrow(cur, source)
    duck.register(f"{name}_src", tbl)
    sel = ", ".join(f"{c} AS {prefix}_{c}" for c in _VALUES)
    try:
        duck.execute(f"CREATE TEMP TABLE {name} AS SELECT stock_code, CAST(trade_date AS DATE) AS trade_date, "
                     f"{sel} FROM {name}_src")
    except duckdb.Error as e:
        raise ReplayInputError(f"{source} daily_prices trade_date 를 날짜로 못 읽는다: {e}") from e
    finally:
        duck.unregister(f"{name}_src")


def _ok(a: str, b: str) -> str:
    """가격·거래량·adj — ±1 또는 0.15%, 둘 다 NULL 이면 같다."""
    return (f"coalesce(abs({a} - {b}) <= {PRICE_ABS} OR abs({a} / nullif({b}, 0) - 1) <= {PRICE_REL}, "
            f"{a} IS NULL AND {b} IS NULL)")


def _amt_ok(a: str, b: str) -> str:
    return f"coalesce(abs({a} - {b}) <= {AMOUNT_ABS}, {a} IS NULL AND {b} IS NULL)"


def _flags(s: str) -> str:
    """compat(x_) 대 상대(`s`_ — v3 사본 v · 다음 거래일 사본 n) 판정 열. 비(ratio)는 상대의 adj/close 비를 compat 종가에
    곱한 값이 compat adj 와 1원 또는 0.15% 안인가 — K(d)/K(L)(기준 사슬)이 같은가(T-33 구간 대조)."""
    ratio = (f"coalesce(abs(x_adj_close - x_close * {s}_adj_close / nullif({s}_close, 0)) <= {PRICE_ABS} "
             f"OR abs((x_adj_close / nullif(x_close, 0)) / ({s}_adj_close / nullif({s}_close, 0)) - 1) "
             f"<= {PRICE_REL}, FALSE)")
    return (f"{_ok('x_open', s + '_open')} AND {_ok('x_high', s + '_high')} AND {_ok('x_low', s + '_low')} "
            f"AS {s}_ohl_ok, {_ok('x_close', s + '_close')} AS {s}_close_ok, "
            f"{_ok('x_volume', s + '_volume')} AS {s}_vol_ok, {_ok('x_adj_close', s + '_adj_close')} AS {s}_adj_ok, "
            f"{_amt_ok('x_amount', s + '_amount')} AS {s}_amt_ok, {ratio} AS {s}_ratio_ok")


@dataclass(frozen=True)
class PriceRows:
    """daily_prices 차이 — 허용 오차 밖 행(판정 열 포함)과 셈."""

    rows: list[dict]           # 허용 오차 밖이거나 한쪽에만 있는 행
    n_diff: int                # 정확히 같지 않은 키
    n_within_tol: int          # 그중 양쪽에 있고 전 열이 허용 오차 안인 행
    bad_days: list[str]        # 탐지된 v3 전 종목 오류일 후보(09-14 전)


def _price_rows(con: sqlite3.Connection, duck: duckdb.DuckDBPyConnection, d_iso: str,
                next_db: Path | None) -> PriceRows:
    """두 파일 daily_prices 를 duckdb 로 올려 맞대고, 허용 오차 밖 행만 판정 열과 함께 파이썬으로 넘긴다
    (`--full` 이면 허용 오차 안 차이 — adj_close 실수 자릿수 등 — 가 수십만 행이다). K 사슬 `k` 는 먼저 만들어 둔다."""
    cols = ", ".join(DAILY_PRICES_COLUMNS)
    _load_prices(duck, "xp", con.execute(f"SELECT {cols} FROM main.daily_prices"), "x", "compat 반영본")
    _load_prices(duck, "vp", con.execute(f"SELECT {cols} FROM v3.daily_prices"), "v", "v3 사본")
    if next_db is not None:
        nxt = sqlite3.connect(_ro_uri(next_db), uri=True)
        try:
            _load_prices(duck, "np_", nxt.execute(f"SELECT {cols} FROM daily_prices WHERE trade_date = ?", (d_iso,)),
                         "n", "다음 거래일 사본")
        except sqlite3.Error as e:
            raise ReplayInputError(f"다음 거래일 사본 daily_prices 를 못 읽는다: {next_db} {e}") from e
        finally:
            nxt.close()
    else:
        _load_prices(duck, "np_", con.execute(f"SELECT {cols} FROM main.daily_prices WHERE 0"), "n", "-")
    same = " AND ".join(f"x.x_{c} IS NOT DISTINCT FROM v.v_{c}" for c in _VALUES)
    duck.execute(f"""CREATE TEMP TABLE j AS
SELECT coalesce(x.stock_code, v.stock_code) AS stock_code, coalesce(x.trade_date, v.trade_date) AS trade_date,
       x.stock_code IS NOT NULL AS in_x, v.stock_code IS NOT NULL AS in_v,
       {', '.join('x.x_' + c for c in _VALUES)}, {', '.join('v.v_' + c for c in _VALUES)},
       coalesce(x.stock_code IS NOT NULL AND v.stock_code IS NOT NULL AND {same}, FALSE) AS same
FROM xp x FULL JOIN vp v ON x.stock_code = v.stock_code AND x.trade_date = v.trade_date""")
    bad_days = [str(r[0]) for r in duck.execute(f"""
SELECT trade_date FROM j WHERE in_x AND in_v AND trade_date < DATE '{AFTERMARKET_FROM}'
GROUP BY trade_date HAVING avg(CASE WHEN {_ok('x_close', 'v_close')} THEN 0 ELSE 1 END) > {BAD_DAY_SHARE}
ORDER BY 1""").fetchall()]
    duck.execute(f"""CREATE TEMP TABLE dd AS
SELECT j.*, j.trade_date >= DATE '{AFTERMARKET_FROM}' AS post, {_flags('v')}, k.raw, k.k, k.rn,
       k.ticker IS NOT NULL AS krx_row
FROM j LEFT JOIN k ON k.ticker = j.stock_code AND k.date = j.trade_date
WHERE NOT j.same""")
    # v3_backfill — v3 종가가 '원종가 × K(d)/K(x), x = d 뒤 4번째 행보다 뒤 행' 과 같다(qle 와 같은 식)
    duck.execute(f"""CREATE TEMP TABLE bf AS
SELECT DISTINCT d.stock_code, d.trade_date FROM dd d
JOIN k x ON x.ticker = d.stock_code AND x.rn > d.rn + {V3_OVERWRITE_ROWS}
WHERE d.in_x AND d.in_v AND NOT d.post AND NOT d.v_close_ok AND d.raw IS NOT NULL
  AND (abs(d.v_close - d.raw * d.k / x.k) <= {PRICE_ABS}
       OR abs(d.v_close / nullif(d.raw * d.k / x.k, 0) - 1) <= {PRICE_REL})""")
    all_ok = "d.in_x AND d.in_v AND d.v_ohl_ok AND d.v_close_ok AND d.v_vol_ok AND d.v_adj_ok AND d.v_amt_ok"
    n_diff, n_tol = duck.execute(f"SELECT count(*), count(*) FILTER (WHERE {all_ok}) FROM dd d").fetchone() or (0, 0)
    cur = duck.execute(f"""
SELECT d.*, b.stock_code IS NOT NULL AS backfill, n.stock_code IS NOT NULL AS n_present,
       {', '.join('n.n_' + c for c in _VALUES)}, {_flags('n')}
FROM dd d LEFT JOIN bf b ON b.stock_code = d.stock_code AND b.trade_date = d.trade_date
LEFT JOIN np_ n ON n.stock_code = d.stock_code AND n.trade_date = d.trade_date
WHERE NOT ({all_ok})
ORDER BY d.stock_code, d.trade_date""")
    names = [x[0] for x in (cur.description or [])]
    return PriceRows([dict(zip(names, r, strict=True)) for r in cur.fetchall()], int(n_diff), int(n_tol),
                     bad_days)


def _base_day(r: Mapping[str, object], ctx: Context, reason: str, core_only: bool) -> Verdict:
    """§7 daily_prices 6 — 기준일 행은 다음 거래일 사본의 같은 행으로 판정한다(v3 가 다음 날 덮은 최종값).
    `core_only` 면 종가·거래량·adj 만 본다(장 마감 판 T 행 — 시·고·저·거래대금은 채움이다)."""
    if not ctx.has_next:
        return Verdict(category=BASE_DAY_OPEN)
    if not r["n_present"]:
        return Verdict(reason=NEXT_MISSING)
    level = bool(r["n_close_ok"] and r["n_vol_ok"] and r["n_adj_ok"])
    t33 = bool(r["post"] and r["n_ratio_ok"] and r["n_vol_ok"])
    tail = True if core_only else bool(r["n_amt_ok"] and (r["n_ohl_ok"] or t33))
    if (level or t33) and tail:
        return Verdict(category=BASE_DAY)
    return Verdict(reason=f"base_day_next:{reason}")


def _price_verdict(r: Mapping[str, object], ctx: Context) -> Verdict:
    """daily_prices 차이 행 하나. 위에서부터 먼저 맞는 것(QL-E 서버 대조 qle_server_cmp 순서 + 이 도구가 더한 것:
    한쪽에만 있는 행 · 장 마감 판 T 행 · 창 밖 adj NULL · 등록 오류일만 · 거래대금 · 기준일 행)."""
    code, day = str(r["stock_code"]), str(r["trade_date"])
    if not r["in_v"]:
        if code in ctx.new_spacs:
            return Verdict(category=NEW_SPAC)
        if ctx.v3_first is not None and day < ctx.v3_first:
            return Verdict(reason=ONLY_COMPAT_OLD)
        if ctx.v3_knows(code, day) and r["krx_row"]:
            return Verdict(category=V3_MISSED_DAY)
        return Verdict(reason=ONLY_COMPAT)
    if not r["in_x"]:
        if ctx.evening and day == ctx.d_iso:
            if code in ctx.listed_on_d:
                return Verdict(category=EVE_NEW_LISTING)
            if code in ctx.missing:
                return Verdict(category=EVE_MISSING)
        return Verdict(reason=ONLY_V3)
    core = bool(r["v_close_ok"] and r["v_vol_ok"] and r["v_adj_ok"])
    if ctx.evening and day == ctx.d_iso:                       # 장 마감 판 T 행(QL-D)
        if r["x_adj_close"] is None and r["v_adj_close"] is not None:
            return Verdict(category=EVE_STEP_NULL)
        if core:                                               # 시·고·저·거래대금만 다르다(T-32 채움)
            return Verdict() if r["v_ohl_ok"] and r["v_amt_ok"] else Verdict(category=EVE_FILL)
        if code not in ctx.kiwoom_2105:
            return Verdict(category=EVE_POSTCLOSE)
        # 21:05 원장 행은 지금 v3 와 같은 뜻(애프터마켓 포함)이다 — 남는 차이는 v3 20:05 수집 시점 값(§7 daily_prices 6)
        return _base_day(r, ctx, SOURCE_KIWOOM_2105, True)
    if (r["x_adj_close"] is None and r["v_adj_close"] is not None and code in ctx.rebase_tickers
            and ctx.rebase_before is not None and day < ctx.rebase_before
            and r["v_close_ok"] and r["v_vol_ok"] and r["v_ohl_ok"] and r["v_amt_ok"]):
        return Verdict(category=REBASE_ADJ_NULL)               # §7 창 밖 — equity 행 없는 날 adj 만 NULL
    if not (core and r["v_ohl_ok"]):
        if r["v_adj_close"] is None and r["x_adj_close"] is not None:
            return Verdict(category=V3_ADJ_NULL)
        if r["post"]:
            if r["v_ratio_ok"] and r["v_vol_ok"]:
                return Verdict(category=T33)
            reason = T33_BAD
        elif day in V3_BAD_DAYS:
            return Verdict(category=V3_BAD_DAY)
        elif r["backfill"]:
            return Verdict(category=V3_BACKFILL)
        else:
            reason = PRE_BAD
    elif not r["v_amt_ok"]:
        if day in V3_BAD_DAYS:
            return Verdict(category=V3_BAD_DAY)
        reason = (AMOUNT_NULL_V3 if r["v_amount"] is None else
                  AMOUNT_NULL_COMPAT if r["x_amount"] is None else AMOUNT)
    else:
        return Verdict()
    return _base_day(r, ctx, reason, False) if day == ctx.d_iso else Verdict(reason=reason)


def _price_rec(r: Mapping[str, object], with_next: bool) -> dict:
    cols = {c: [r[f"x_{c}"], r[f"v_{c}"]] + ([r[f"n_{c}"]] if with_next else []) for c in _VALUES}
    return {"key": {"stock_code": r["stock_code"], "trade_date": str(r["trade_date"])}, "columns": cols}


def _compare_prices(con: sqlite3.Connection, duck: duckdb.DuckDBPyConnection, ctx: Context, tally: Tally,
                    t: TableTally, next_db: Path | None) -> dict[str, object]:
    got = _price_rows(con, duck, ctx.d_iso, next_db)
    t.n_diff, t.n_within_tol = got.n_diff, got.n_within_tol
    for r in got.rows:
        v = _price_verdict(r, ctx)
        if v.category is None and v.reason is None:
            t.n_within_tol += 1
            continue
        rec = _price_rec(r, next_db is not None and str(r["trade_date"]) == ctx.d_iso)
        if v.category is not None:
            tally.explain(t, v.category, rec)
        else:
            tally.miss(t, str(v.reason), rec, None)
    return {"aftermarket_from": AFTERMARKET_FROM, "v3_first_trade_date": ctx.v3_first,
            "bad_days_detected": got.bad_days, "bad_days_registered": sorted(V3_BAD_DAYS),
            "bad_days_unregistered": [d for d in got.bad_days if d not in V3_BAD_DAYS],
            "next_v3_db": None if next_db is None else str(next_db)}


# ── 진입 ──────────────────────────────────────────────────────────────────────
def _kinds(table: str) -> dict[str, str]:
    """열 → v3 선언 타입(INTEGER·REAL·TEXT) — `v3_schema.sql` 이 정본."""
    return {c: (t or "").upper() for c, t in _reference_schema()[table]}


def compare(compat_db: Path, v3_db: Path, date_ymd: str, equity_root: Path,
            next_v3_db: Path | None = None, min_spearman: float = 0.0) -> dict[str, object]:
    """대조 한 번 — JSON 으로 내보낼 보고 dict(`rc` 포함). 입력 오류는 `ReplayInputError`."""
    d_iso = _iso(date_ymd)
    if BY_TABLE["daily_prices"].columns != DAILY_PRICES_COLUMNS:
        raise ReplayInputError(f"compat daily_prices 열이 바뀌었다 {BY_TABLE['daily_prices'].columns} — "
                               f"대조기 분류({DAILY_PRICES_COLUMNS})를 같이 고쳐야 한다")
    nxt = None if next_v3_db is None else Path(next_v3_db)
    if nxt is not None and not nxt.is_file():
        raise ReplayInputError(f"다음 거래일 사본이 없다: {nxt}")
    con = _open(Path(compat_db), Path(v3_db))
    duck = _duck()
    try:
        record = _record(con, d_iso)
        builds = _json_field(record, "equity_builds")
        builds = {str(k): str(v) for k, v in builds.items()} if isinstance(builds, dict) else {}
        pd_expr, pd_build = _equity(equity_root, "price_daily", builds)
        sec_expr, sec_build = _equity(equity_root, "security", builds)
        ctx = _context(con, duck, pd_expr, sec_expr, record, d_iso, nxt is not None)
        _k_table(con, duck, pd_expr, d_iso)
        tally = Tally()
        scores: dict[str, object] = {}
        prices: dict[str, object] = {}
        for table in TABLES:
            m = BY_TABLE[table]
            cols = tuple(c for c in m.columns if c not in IGNORED.get(table, {}))
            t = tally.tables[table] = TableTally(table, m.pk, cols)
            t.n_compat = int(con.execute(f"SELECT count(*) FROM main.{_q(table)}").fetchone()[0])
            t.n_v3 = int(con.execute(f"SELECT count(*) FROM v3.{_q(table)}").fetchone()[0])
            if table == "daily_prices":
                prices = _compare_prices(con, duck, ctx, tally, t, nxt)
            elif table in SCORE_TABLES:
                scores[table] = _compare_scores(con, ctx, tally, t, _kinds(table), min_spearman)
            else:
                _compare_generic(con, duck, ctx, tally, t, _kinds(table))
    except sqlite3.DatabaseError as e:
        raise ReplayInputError(f"sqlite 를 읽지 못했다(compat={compat_db} v3={v3_db}): {e}") from e
    finally:
        duck.close()
        con.close()
    totals: Counter = Counter()
    for t in tally.tables.values():
        totals.update(t.categories)
    n_other = tally.counts[DAILY]
    return {
        "tool": TOOL, "schema": SCHEMA, "date": d_iso, "basis": ctx.basis,
        "status": "ok" if n_other == 0 else "other", "rc": 0 if n_other == 0 else 1,
        "n_other": n_other, "n_manual_consumer": tally.counts[MANUAL], "n_no_consumer": tally.counts[NONE],
        "categories_total": dict(sorted(totals.items())),
        "undetermined": {BASE_DAY_OPEN: totals.get(BASE_DAY_OPEN, 0)},
        "inputs": {"compat_db": str(compat_db), "v3_db": str(v3_db),
                   "next_v3_db": None if nxt is None else str(nxt),
                   "equity_root": str(equity_root), "price_daily_build": pd_build, "security_build": sec_build,
                   "builds_fallback": _json_field(record, "builds_fallback") or [],
                   "compat_exported_at": record["exported_at"], "compat_window": _json_field(record, "window")},
        "registry": {c.key: {"label": c.label, "basis": c.basis} for c in CATEGORIES},
        "consumers": {"daily": {t: (c if c == ALL else sorted(c)) for t, c in DAILY_CONSUMER.items()},
                      "manual": {t: sorted(c) for t, c in MANUAL_CONSUMER.items()}},
        "ignored_columns": IGNORED,
        "tables": {k: v.to_dict() for k, v in tally.tables.items()},
        "scores": scores, "daily_prices": prices,
        **{tier: recs for tier, recs in tally.records.items()},
        "truncated": {tier: tally.counts[tier] > len(recs) for tier, recs in tally.records.items()},
    }


def summary_lines(report: Mapping[str, object]) -> list[str]:
    """표준 출력 요약 — 표마다 범주·미설명(갈래별) 수, 점수 Spearman."""
    out = [f"v3_replay D={report['date']} basis={report['basis']} rc={report['rc']} "
           f"미설명(매일 소비자)={report['n_other']} 리서치센터만={report['n_manual_consumer']} "
           f"소비자 없음={report['n_no_consumer']} 범주={report['categories_total']}"]
    tables = report["tables"]
    assert isinstance(tables, dict)
    for name, t in tables.items():
        cats = " ".join(f"{k}={v['rows']}" for k, v in t["categories"].items())
        tiers = " ".join(f"{tier}[{' '.join(f'{k}={v}' for k, v in t[tier].items())}]"
                         for tier in (DAILY, MANUAL, NONE) if t[tier])
        out.append(f"  {name}: compat={t['n_compat']} v3={t['n_v3']} 차이 키={t['n_diff']} "
                   f"허용오차 안={t['n_within_tol']} | {cats or '-'} | {tiers or '미설명 0'}")
    scores = report["scores"]
    assert isinstance(scores, dict)
    for name, s in scores.items():
        rho = s["spearman"]
        out.append(f"  {name}({s['spec']}) Spearman={'-' if rho is None else f'{rho:.4f}'} 공통={s['n_common']} "
                   f"v3만={s['n_v3_only']} compat만={s['n_compat_only']}"
                   + (f" 하한={s['min_spearman']} {'통과' if s['ok'] else '미달'}" if s["judged"] else " (기록형)"))
    prices = report["daily_prices"]
    if isinstance(prices, dict) and prices.get("bad_days_unregistered"):
        out.append(f"  daily_prices 미등록 전 종목 오류일 후보 {prices['bad_days_unregistered']}")
    return out


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


# ── 그림자 날 대조 준비(COMPAT §8-2) ──────────────────────────────────────────
def shadow_merge(evening: Path, refill: Path, date_ymd: str, out: Path) -> None:
    """그림자 날의 compat 상태 = ⑥(점수 포함 9표) + refill(점수 없는 7표). 컷오버 뒤 다음 날 03:00 v3 사본이 담는 것과 같다.

    `v3_post.sh --shadow` 는 본 파일에 쓰지 않으므로 refill 스테이징(21:05 뒤 본 파일 사본 + compat 7표)의 점수 두 표 D
    행은 v3 자기 점수다. 그 D 행을 ⑥ 스테이징(16:xx 본 파일 사본 + compat 9표)의 D 행으로 바꾼다 — 범위는 반영과 같은
    `v3_post._scope`(score_date = D). 두 스테이징은 읽기 전용으로 열고 `out` 에만 쓴다(refill 을 온라인 백업으로 뜬 뒤).
    ⑥·refill 의 그날 ok 기록이 없거나 ⑥ 기록에 점수 두 표가 없으면 `ReplayInputError`(그날 ⑥ 이 실패한 날은 v3 점수가
    다음 날 아침 T-34 로 채워지므로 이 합치기가 맞지 않다).
    """
    d_iso = _iso(date_ymd)
    for p, label in ((evening, "⑥ 스테이징"), (refill, "refill 스테이징")):
        if not Path(p).is_file():
            raise ReplayInputError(f"{label} 이 없다: {p}")
    for p, label, need in ((evening, "⑥", set(SCORE_TABLES)), (refill, "refill", set())):
        con = sqlite3.connect(_ro_uri(Path(p)), uri=True)
        try:
            rows = [r for r in _meta_rows(con) if r.get("date") == d_iso and r.get("basis") == "evening"
                    and r.get("status") == "ok"]
        finally:
            con.close()
        if not rows or not need <= set(json.loads(str(max(rows, key=lambda r: str(r["exported_at"]))["tables"]))):
            raise ReplayInputError(f"{label} 스테이징 {p} 에 {d_iso} 장 마감 ok 기록"
                                   + ("(점수 두 표 포함)" if need else "") + " 이 없다")
    snapshot(Path(refill), Path(out))
    # ATTACH 의 URI(`mode=ro`)는 주 연결을 URI 로 열었을 때만 풀린다(v3_post._move 와 같다)
    con = sqlite3.connect(f"{Path(out).resolve().as_uri()}?mode=rw", uri=True, isolation_level=None)
    try:
        con.execute("ATTACH DATABASE ? AS ev", (_ro_uri(Path(evening)),))
        con.execute("BEGIN")
        for table in SCORE_TABLES:
            where, params = _scope(table, (d_iso, d_iso), d_iso)
            con.execute(f'DELETE FROM main."{table}" WHERE {where}', params)
            con.execute(f'INSERT INTO main."{table}" SELECT * FROM ev."{table}" WHERE {where}', params)
        con.execute(f"INSERT OR IGNORE INTO main.{META_TABLE} SELECT * FROM ev.{META_TABLE} "
                    "WHERE date = ? AND basis = 'evening' AND status = 'ok'", (d_iso,))
        con.execute("COMMIT")
    finally:
        con.close()


# ── 실행기 보조(날짜·요약) ────────────────────────────────────────────────────
def trading_days(a: str, b: str, calendar_dir: Path | None = None) -> list[tuple[date, date | None]]:
    """[a, b] 거래일과 그 다음 거래일(판정 달력에 그 해가 없으면 None) — 날짜 계산은 `daily.calendar` 하나로."""
    try:
        lo, hi = (datetime.strptime(x, "%Y%m%d").date() for x in (a, b))
    except ValueError as e:
        raise ReplayInputError(f"날짜는 YYYYMMDD 여야 한다: {a}-{b}") from e
    if hi < lo:
        raise ReplayInputError(f"날짜 구간이 거꾸로다: {a}-{b}")
    try:
        cal = daily_calendar.load() if calendar_dir is None else daily_calendar.load(calendar_dir)
    except daily_calendar.CalendarUnavailable as e:
        raise ReplayInputError(str(e)) from e
    out: list[tuple[date, date | None]] = []
    cur = lo
    while cur <= hi:
        try:
            trading = cal.is_trading_day(cur)
        except KeyError as e:
            raise ReplayInputError(f"판정 달력이 {cur.year} 년을 덮지 않는다(calendar_dir={calendar_dir}): {e}") from e
        if trading:
            nxt: date | None = None
            probe = cur
            for _ in range(31):                     # 최장 연휴보다 넉넉하게
                probe += timedelta(days=1)
                try:
                    if cal.is_trading_day(probe):
                        nxt = probe
                        break
                except KeyError:
                    break                           # 다음 해 판정 파일이 아직 없다 — 다음 거래일 모름
            out.append((cur, nxt))
        cur += timedelta(days=1)
    return out


TSV_HEAD = ("date", "status", "rc", "n_other", "n_manual_consumer", "n_no_consumer",
            *(f"spearman_{t}" for t in SCORE_TABLES), "categories", "other", "json")


def tsv_rows(paths: Sequence[Path]) -> list[tuple[str, ...]]:
    """날짜별 JSON(대조 보고 또는 실행기가 남긴 상태 기록) → 요약 행. 날짜순. `other` 열은 매일 소비자 갈래 사유다."""
    rows: list[tuple[str, ...]] = []
    for p in paths:
        try:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ReplayInputError(f"날짜별 JSON 을 못 읽는다: {p} {e}") from e
        scores = doc.get("scores") or {}
        sp = []
        for t in SCORE_TABLES:
            rho = (scores.get(t) or {}).get("spearman")
            sp.append("-" if rho is None else f"{rho:.4f}")
        cats = ";".join(f"{k}={v}" for k, v in (doc.get("categories_total") or {}).items())
        other: Counter = Counter()
        for name, t in (doc.get("tables") or {}).items():
            for reason, n in (t.get(DAILY) or {}).items():
                other[f"{name}.{reason}"] += n
        rows.append((str(doc.get("date", "-")).replace("-", ""), str(doc.get("status", "-")),
                     str(doc.get("rc", "-")), str(doc.get("n_other", "-")),
                     str(doc.get("n_manual_consumer", "-")), str(doc.get("n_no_consumer", "-")), *sp,
                     cats or "-", ";".join(f"{k}={v}" for k, v in sorted(other.items())) or "-", str(p)))
    return sorted(rows)


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m compat.v3_replay")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="v3 사본 대 compat 반영본 한 날 대조")
    c.add_argument("--compat-db", required=True, type=Path, help="compat 이 반영한 v3 사본 복사본")
    c.add_argument("--v3-db", required=True, type=Path, help="같은 날 v3 사본(quant_<D>.db)")
    c.add_argument("--date", required=True, help="기준일 D YYYYMMDD")
    c.add_argument("--equity-root", required=True, type=Path,
                   help="equity 루트 — compat 기록의 판(price_daily·security)을 읽는다")
    c.add_argument("--next-v3-db", type=Path, default=None,
                   help="다음 거래일 v3 사본 — 기준일 행(§7 daily_prices 6) 판정. 없으면 '기준일 행 미확정'")
    c.add_argument("--min-spearman", type=float, default=0.0,
                   help="점수 공통 종목 Spearman 하한 — 0(기본)이면 기록형, 주면 미달 = 미설명")
    c.add_argument("--json", type=Path, default=None, help="보고 JSON 경로")
    d = sub.add_parser("dates", help="[from, to] 거래일과 다음 거래일 — 탭 구분")
    d.add_argument("--from", dest="date_from", required=True)
    d.add_argument("--to", dest="date_to", required=True)
    d.add_argument("--calendar-dir", type=Path, default=None)
    s = sub.add_parser("tsv", help="날짜별 JSON → 요약 tsv")
    s.add_argument("--out", required=True, type=Path)
    s.add_argument("paths", nargs="*", type=Path)
    m = sub.add_parser("shadow-merge", help="그림자 날 compat 상태 = ⑥ 점수 + refill 7표(COMPAT §8-2)")
    m.add_argument("--evening", required=True, type=Path, help="⑥ 스테이징(staging_evening_shadow.db 의 복사본)")
    m.add_argument("--refill", required=True, type=Path,
                   help="refill 스테이징(staging_evening_noscores_shadow.db 의 복사본)")
    m.add_argument("--date", required=True, help="그날 T YYYYMMDD")
    m.add_argument("--out", required=True, type=Path, help="합친 파일 — 있으면 지우고 새로 뜬다")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "dates":
            for d, nxt in trading_days(args.date_from, args.date_to, args.calendar_dir):
                print(f"{d:%Y%m%d}\t{'-' if nxt is None else format(nxt, '%Y%m%d')}")
            return 0
        if args.cmd == "tsv":
            lines = ["\t".join(TSV_HEAD)] + ["\t".join(r) for r in tsv_rows(args.paths)]
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("\n".join(lines))
            return 0
        if args.cmd == "shadow-merge":
            shadow_merge(args.evening, args.refill, args.date, args.out)
            print(f"v3_replay shadow-merge {args.date} → {args.out}")
            return 0
        report = compare(args.compat_db, args.v3_db, args.date, args.equity_root, args.next_v3_db,
                         args.min_spearman)
    except ReplayInputError as e:
        print(f"v3_replay 입력 오류({args.cmd}): {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  # reason: 실행기가 rc 로만 보므로 어떤 예외든 원인을 남긴다
        print(f"v3_replay 실패(예상 밖, {args.cmd}): {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if args.json is not None:
        _write_json(args.json, report)
    print("\n".join(summary_lines(report)))
    rc = report["rc"]
    assert isinstance(rc, int)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
