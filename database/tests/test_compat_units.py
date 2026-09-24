"""T1.1 — 단위 대조표 고정 (플랜 §1-4 GAP-3 · §5 T1.1 3).

equity 는 `*_krw`(원)·`*_shr`(주) 접미로 단위를 이름에 박아 두지만 v3 `quant.db` 는 표마다
억원·백만원·원이 섞여 있다. 여기서 두 어휘 사이의 나눗셈 상수를 리터럴로 고정한다.

특히 **v3 flow 팩터의 z 입력**(`backend/scoring/factors/flow.py:64-70`)
  ratio = net / (market_cap * 100_000)
이 우리 단위표와 정합한지 검산한다 — net 이 백만원이고 market_cap 이 억원이면 이 식은
'진짜 비율의 1/1000' 이고, 상수배는 z-score 에서 상쇄되므로 스코어가 같다.
"""
from __future__ import annotations

import pytest
from compat import units


def test_krw_constants() -> None:
    assert units.KRW_PER_MN == 1_000_000
    assert units.KRW_PER_EOK == 100_000_000
    # v3 flow.py:67 이 쓰는 분모 상수 — 억원 시총을 '백만원 순매수' 축으로 맞추는 값
    assert units.V3_FLOW_MCAP_DIVISOR == 100_000


@pytest.mark.parametrize(("table", "column", "v3_unit", "src_column", "divisor"), [
    # v3 `backend/db/column_units_data.py:88` — 키움 mac
    ("stocks", "market_cap", "억원", "price_daily.mktcap_krw", 100_000_000),
    # v3 `backend/db/CLAUDE.md:29` · `backend/clients/CLAUDE.md:56` — ka10081 거래대금
    ("daily_prices", "amount", "백만원", "price_daily.value_krw", 1_000_000),
    ("daily_prices", "close", "원", "price_daily.close", 1),
    ("daily_prices", "volume", "주", "price_daily.volume_shr", 1),
    ("daily_prices", "adj_close", "원", "price_adj_daily.adj_close", 1),
    # v3 `backend/db/CLAUDE.md:37` · `backend/clients/CLAUDE.md:54,64` — ka10059 amt_qty_tp=1
    ("investor_detail_flows", "individual", "백만원", "flow_daily.ind_invsr_krw", 1_000_000),
    ("investor_detail_flows", "foreign_investor", "백만원", "flow_daily.frgnr_invsr_krw",
     1_000_000),
    ("investor_detail_flows", "institution_total", "백만원", "flow_daily.orgn_krw", 1_000_000),
    ("investor_detail_flows", "private_equity", "백만원", "flow_daily.samo_fund_krw", 1_000_000),
    # v3 `column_units_data.py:59-61,69-70` — WISE/네이버 요약은 이미 억원
    ("financial_summary", "revenue", "억원", "stg_fin_wise.val_*(accode 200000)", 1),
    ("financial_summary", "gross_profit", "억원", "stg_fin_wise.val_*(accode 200810)", 1),
    ("consensus_revision_daily", "op", "억원", "stg_consensus_matrix.value(acc_cd 121500)", 1),
    ("consensus_annual", "ni", "억원", "stg_consensus_annual.ni", 1),
])
def test_unit_rule_literals(table: str, column: str, v3_unit: str, src_column: str,
                            divisor: int) -> None:
    r = units.rule(table, column)
    assert (r.v3_unit, r.source_column, r.divisor) == (v3_unit, src_column, divisor)


def test_flow_has_all_twelve_v3_subjects() -> None:
    """v3 `investor_detail_flows` 12주체 전부에 규칙이 있다 — 하나라도 빠지면 매핑이 조용히 센다."""
    got = {r.v3_column for r in units.UNIT_RULES if r.v3_table == "investor_detail_flows"}
    assert got == {"individual", "foreign_investor", "institution_total", "financial_investment",
                   "insurance", "investment_trust", "etc_financial", "bank", "pension_fund",
                   "private_equity", "nation", "etc_corporation"}


def test_no_duplicate_rules() -> None:
    keys = [(r.v3_table, r.v3_column) for r in units.UNIT_RULES]
    assert len(keys) == len(set(keys))


def test_unknown_column_raises() -> None:
    with pytest.raises(KeyError):
        units.rule("stocks", "sector")


def test_to_v3_converts() -> None:
    # 시총 12,345,678,900,000원 = 123,456.789 억원
    assert units.to_v3("stocks", "market_cap", 12_345_678_900_000) == pytest.approx(123_456.789)
    # 외국인 순매수 −3,456,000,000원 = −3,456 백만원
    assert units.to_v3("investor_detail_flows", "foreign_investor", -3_456_000_000) == \
        pytest.approx(-3456.0)
    # 거래대금 1,234,000,000원 = 1,234 백만원
    assert units.to_v3("daily_prices", "amount", 1_234_000_000) == pytest.approx(1234.0)
    # 종가는 그대로
    assert units.to_v3("daily_prices", "close", 70_100) == pytest.approx(70_100)
    assert units.to_v3("stocks", "market_cap", None) is None


def test_v3_flow_z_input_is_true_ratio_over_1000() -> None:
    """검산 — v3 flow.py:64-70 의 z 입력은 진짜 비율의 정확히 1/1000 이다.

    net(원) → 백만원, mktcap(원) → 억원 으로 옮긴 뒤 v3 식 `net/(mcap*100_000)` 을 계산하면
    `net_krw/mktcap_krw / 1000` 과 같다. 상수배이므로 z-score 는 영향을 받지 않는다
    (v3 주석 flow.py:65-66 의 '상수 상쇄' 와 같은 말). 만약 flows 가 천원이었다면 이 비가
    1 이 됐어야 한다 — 그 경우와 구분되는 것이 이 테스트의 목적이다.
    """
    net_krw = -3_456_000_000
    mktcap_krw = 12_345_678_900_000
    net_v3 = units.to_v3("investor_detail_flows", "foreign_investor", net_krw)
    mcap_v3 = units.to_v3("stocks", "market_cap", mktcap_krw)
    assert net_v3 is not None and mcap_v3 is not None
    z_in = units.v3_flow_z_input(net_v3, mcap_v3)
    true_ratio = units.true_flow_ratio(net_krw, mktcap_krw)
    assert z_in == pytest.approx(true_ratio / 1000, rel=1e-12)
    # 천원 가정이었다면 z_in == true_ratio 였을 것 — 실제로는 1000배 차이가 난다.
    assert z_in != pytest.approx(true_ratio, rel=1e-3)


def test_v3_flow_z_input_zero_mcap_raises() -> None:
    """v3 는 `market_caps[code] > 0` 인 종목만 넘긴다(flow.py:26) — 0 은 계약 위반이다."""
    with pytest.raises(ZeroDivisionError):
        units.v3_flow_z_input(1.0, 0.0)
