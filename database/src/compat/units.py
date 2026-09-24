"""v3 `quant.db` ↔ equity/stage 단위 대조표 (플랜 `2026-09-24-v3-merge.md` §1-4 GAP-3 · §5 T1.1).

우리 어휘는 이름에 단위가 박혀 있다(`*_krw` 원 · `*_shr` 주 — `docs/EQUITY_FIELD_MAP.md` §2).
v3 는 표마다 단위가 다르고 이름에 표시가 없다. 그 사이의 **나눗셈 상수**를 여기에 선언하고
`tests/test_compat_units.py` 가 리터럴로 고정한다.

근거(전부 v3 스냅샷 읽기 전용 확인, 2026-09-24):
  · `backend/db/column_units_data.py:88`      stocks.market_cap = 억원 (source kiwoom)
  · `backend/db/CLAUDE.md:29`                 daily_prices: OHLC 원 · volume 주 · amount 백만원
  · `backend/db/CLAUDE.md:37`                 investor_detail_flows 12주체 = **백만원**
                                              ("키움 ka10059 unit_tp=1000")
  · `backend/clients/CLAUDE.md:54,56,64`      같은 내용(환산식 `raw / 1,000,000`)
  · `backend/scoring/factors/flow.py:64-70`   z 입력 `net / (market_cap * 100_000)` 과
                                              "비율의 1/1000 … Z-Score 에서 상수 상쇄" 주석
  · `backend/db/column_units_data.py:49-75`   consensus_*·financial_summary 금액 = 억원

⚠ 플랜 §1-4 GAP-3 은 `investor_detail_flows` 를 **천원**이라고 적었는데, v3 코드·문서는 셋 다
**백만원**이라고 말한다(위 세 근거 + flow.py 주석의 '1/1000'). 천원이라면 flow.py 의 식이 곧
진짜 비율이어야 하는데 v3 주석은 1/1000 이라고 못 박는다. 여기서는 v3 쪽을 따랐다 —
스코어링은 상수배에 불변이라 영향이 없지만, 리서치센터·가설·unitelegram 은 이 값을 그대로
읽으므로 1,000배 차이는 그쪽에서 조용한 오염이 된다. 서버 대조(T1.3)에서 재확인한다.
"""
from __future__ import annotations

from dataclasses import dataclass

KRW_PER_MN = 1_000_000          # 원 → 백만원
KRW_PER_EOK = 100_000_000       # 원 → 억원
# v3 `backend/scoring/factors/flow.py:67` 의 분모 상수. 억원 시총을 백만원 순매수 축에 맞춘다.
V3_FLOW_MCAP_DIVISOR = 100_000


@dataclass(frozen=True)
class UnitRule:
    """v3 컬럼 1개의 단위와, 우리 소스에서 그 값으로 가는 나눗셈 상수."""

    v3_table: str
    v3_column: str
    v3_unit: str            # 억원 · 백만원 · 원 · 주 · 배 · %
    source_column: str      # equity/stage 원천 컬럼(문서용 실명)
    source_unit: str
    divisor: int            # source 값 ÷ divisor = v3 값. 1 이면 변환 없음
    evidence: str           # 단위 근거(파일:줄)


_UNITS_DATA = "v3 backend/db/column_units_data.py"
_DB_DOC = "v3 backend/db/CLAUDE.md"
_CLIENTS_DOC = "v3 backend/clients/CLAUDE.md"
_FIELD_MAP = "docs/EQUITY_FIELD_MAP.md"

# v3 `investor_detail_flows` 12주체 ← equity `flow_daily` 13주체(`natfor_krw` 는 v3 에 자리가
# 없어 버린다). 주체 대응은 `src/equity/sql/flow_daily.sql:52-72` 의 선언 순서를 따른다.
# `orgn_krw`(기관계)는 기관 7주체 합과 **다르다**(FIELD_MAP GAP-03, 절단본 18,581행 중 10,783행
# 불일치·편차 최대 2,834억원) — v3 `institution_total` 도 같은 성격의 합계 컬럼이라 그대로 나른다.
FLOW_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("individual", "ind_invsr_krw"),
    ("foreign_investor", "frgnr_invsr_krw"),
    ("institution_total", "orgn_krw"),
    ("financial_investment", "fnnc_invt_krw"),
    ("insurance", "insrnc_krw"),
    ("investment_trust", "invtrt_krw"),
    ("etc_financial", "etc_fnnc_krw"),
    ("bank", "bank_krw"),
    ("pension_fund", "penfnd_etc_krw"),
    ("private_equity", "samo_fund_krw"),
    ("nation", "natn_krw"),
    ("etc_corporation", "etc_corp_krw"),
)

UNIT_RULES: tuple[UnitRule, ...] = (
    UnitRule("stocks", "market_cap", "억원", "price_daily.mktcap_krw", "원", KRW_PER_EOK,
             f"{_UNITS_DATA}:88 · {_FIELD_MAP} price.market_cap"),
    UnitRule("daily_prices", "open", "원", "price_daily.open", "원", 1, f"{_DB_DOC}:29"),
    UnitRule("daily_prices", "high", "원", "price_daily.high", "원", 1, f"{_DB_DOC}:29"),
    UnitRule("daily_prices", "low", "원", "price_daily.low", "원", 1, f"{_DB_DOC}:29"),
    UnitRule("daily_prices", "close", "원", "price_daily.close", "원", 1, f"{_DB_DOC}:29"),
    UnitRule("daily_prices", "volume", "주", "price_daily.volume_shr", "주", 1, f"{_DB_DOC}:29"),
    UnitRule("daily_prices", "amount", "백만원", "price_daily.value_krw", "원", KRW_PER_MN,
             f"{_DB_DOC}:29 · {_CLIENTS_DOC}:56"),
    UnitRule("daily_prices", "adj_close", "원", "price_adj_daily.adj_close", "원", 1,
             f"{_DB_DOC}:29 (전방 조정 — 비율은 v3 소급 조정과 같다)"),
    *(UnitRule("investor_detail_flows", v3_col, "백만원", f"flow_daily.{src_col}", "원",
               KRW_PER_MN, f"{_DB_DOC}:37 · {_CLIENTS_DOC}:54,64 · flow.py:64-70")
      for v3_col, src_col in FLOW_SUBJECTS),
    UnitRule("financial_summary", "revenue", "억원", "stg_fin_wise.val_*(accode 200000)", "억원",
             1, f"{_UNITS_DATA}:59 · STAGE_DESIGN §4 WISE(표시 단위 그대로)"),
    UnitRule("financial_summary", "op", "억원", "stg_fin_wise.val_*(accode 201370)", "억원", 1,
             f"{_UNITS_DATA}:60"),
    UnitRule("financial_summary", "ni", "억원", "stg_fin_wise.val_*(accode 203170)", "억원", 1,
             f"{_UNITS_DATA}:61"),
    UnitRule("financial_summary", "gross_profit", "억원", "stg_fin_wise.val_*(accode 200810)",
             "억원", 1, "v3 _wisereport_parsers.py:49(매출총이익) · WISE 손익계산서 억원"),
    UnitRule("financial_summary", "eps", "원", "stg_fin_wise.val_*(accode 312000)", "원", 1,
             f"{_UNITS_DATA}:62"),
    UnitRule("financial_summary", "bps", "원", "stg_fin_wise.val_*(accode 314000)", "원", 1,
             f"{_UNITS_DATA}:63"),
    UnitRule("financial_summary", "shares", "주", "stg_fin_wise.val_*(accode 701250)", "주", 1,
             "v3 _wisereport_parsers.py:43(발행주식수, '주' 문자 제거)"),
    UnitRule("consensus_revision_daily", "revenue", "억원",
             "stg_consensus_matrix.value(acc_cd 121000)", "억원", 1,
             f"{_UNITS_DATA}:5 · acc_nm '매출액(억원)'"),
    UnitRule("consensus_revision_daily", "op", "억원",
             "stg_consensus_matrix.value(acc_cd 121500)", "억원", 1, f"{_UNITS_DATA}:6"),
    UnitRule("consensus_revision_daily", "ni", "억원",
             "stg_consensus_matrix.value(acc_cd 122710)", "억원", 1, f"{_UNITS_DATA}:7"),
    UnitRule("consensus_annual", "revenue", "억원", "stg_consensus_annual.revenue", "억원", 1,
             f"{_UNITS_DATA}:49"),
    UnitRule("consensus_annual", "op", "억원", "stg_consensus_annual.op", "억원", 1,
             f"{_UNITS_DATA}:51"),
    UnitRule("consensus_annual", "ni", "억원", "stg_consensus_annual.ni", "억원", 1,
             f"{_UNITS_DATA}:52"),
)

_BY_KEY: dict[tuple[str, str], UnitRule] = {(r.v3_table, r.v3_column): r for r in UNIT_RULES}


def rule(v3_table: str, v3_column: str) -> UnitRule:
    """단위 규칙 1건. 선언에 없는 컬럼은 KeyError — 조용히 1배로 나르지 않는다."""
    return _BY_KEY[(v3_table, v3_column)]


def to_v3(v3_table: str, v3_column: str, value: float | None) -> float | None:
    """우리 소스 값을 v3 단위로 옮긴다. NULL 은 NULL 이다(0 으로 채우지 않는다)."""
    if value is None:
        return None
    return value / rule(v3_table, v3_column).divisor


def v3_flow_z_input(net_v3: float, market_cap_v3: float) -> float:
    """v3 `backend/scoring/factors/flow.py:67` 의 z 입력식 그대로.

    net 은 백만원, market_cap 은 억원. 결과는 '진짜 순매수/시총 비율'의 1/1000 이지만
    z-score 정규화에서 상수가 상쇄돼 점수에는 영향이 없다(flow.py:65-66 주석).
    """
    return net_v3 / (market_cap_v3 * V3_FLOW_MCAP_DIVISOR)


def true_flow_ratio(net_krw: float, mktcap_krw: float) -> float:
    """원 단위끼리 나눈 진짜 순매수/시총 비율 — 위 식의 검산축."""
    return net_krw / mktcap_krw
