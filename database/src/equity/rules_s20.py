"""S20 팩터 준비도 — `factor_readiness` (DESIGN v1.2 §4-8 · GATES v1.0 §6 EG10 · §9 「EG10 이름」 ·
WORKFLOW §0-3 「목적 자체(54 재료)」·§3-2 S20·§4 DoD 6단계).

`FACTORS.md` 정본 54개가 **지금 이 빌드로 계산 가능한가** 를 한 행씩 판정한다. 이 표가 equity 층의
목적 판정 자체다 — 게이트가 전부 통과해도 "54개 중 몇 개를 만들 수 있는가" 를 묻는 술어가 없으면
재료가 조용히 비어도 아무도 실패하지 않는다(GATES §6 EG10 의 왜).

**판정은 선언이 아니라 계산이다.** 선언(`FactorSpec`)이 주는 것은 팩터 정의에서 나오는 것뿐이다 —
`required_field_ids`(계산식이 요구하는 재료) · `owner`(막혔을 때 누가 풀어야 하나) · `caveat` ·
`registry_factor_id`. `status`·`blocked_reason`·`required_columns`·`first_usable_date` 는
`sql/factor_readiness.sql` 이 **`dataset_profile`(S19) 과 조인해서** 만든다:

  blocked 판정 우선순위 (앞의 것이 이기고, 사유 문자열에 걸린 field_id 를 붙인다)
    `field_unavailable`   요구 필드에 프로파일 행이 없다 — 원천 부재이거나 그 슬라이스 미구현
    `not_point_in_time`   프로파일 행이 `point_in_time=false` (현재값 라벨)
    `lag_unresolved`      `recommended_lag_sessions` 가 NULL — 공표 시점을 못 정했다
    `no_observations`     `estimated_coverage_pct = 0` — 선언은 있는데 값이 하나도 없다
    `partial_support`     `requires_confirmation=true` — 단위·산출 규칙·축 선택이 미결
  전부 아니면 `ready`, `first_usable_date` = 요구 필드 `coverage_from` 의 최댓값(가장 늦게 열린
  재료가 팩터의 시작일이다).

**EG10** 은 그 표를 판정한다 — 54행 전수 · blocked 는 reason·owner 필수 · ready 는
`first_usable_date` NOT NULL · **ready 행의 `required_columns` 가 실물 컬럼으로 실재**(폐기형:
"ready 인데 컬럼이 없다" 는 표가 거짓말을 하는 것이다) · ready 수 ≥ baseline
(`factor_readiness.ready_min`, 미등재면 `skip(no_baseline)` + 측정치 기록).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
from stage.gates import GateResult, GateStatus

from . import rules_s19, views
from .gates import EquityGateContext, SkipGate, require_const
from .model import AVAILABLE_NONE, EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"
TABLE_NAME = "factor_readiness"
PROFILE_TABLE = rules_s19.TABLE_NAME

STATUS_VOCAB: tuple[str, ...] = ("ready", "blocked")
# `.sql` 의 CASE 가 이 순서대로 판정한다 — 나열 순서가 곧 우선순위다.
BLOCKED_REASONS: tuple[str, ...] = (
    "field_unavailable", "not_point_in_time", "lag_unresolved", "no_observations",
    "partial_support")
# 막힌 것을 푸는 책임자. ready 행은 계산이 남았으므로 `factor_layer`.
OWNER_VOCAB: tuple[str, ...] = ("equity", "factor_layer", "unavailable")

REJECT_REASONS: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorSpec:
    """`FACTORS.md` 정본 팩터 하나의 선언. 계산식에서 나오는 것만 담는다."""

    factor_id: str
    label: str
    required_field_ids: tuple[str, ...]
    owner: str
    caveat: str
    evidence: str
    registry_factor_id: str | None = None

    def __post_init__(self) -> None:
        if self.owner not in OWNER_VOCAB:
            raise ValueError(f"owner outside vocabulary: factor_id={self.factor_id} "
                             f"got={self.owner!r} allowed={list(OWNER_VOCAB)}")
        if not self.required_field_ids:
            raise ValueError(f"required_field_ids is empty: factor_id={self.factor_id}")


def _f(factor_id: str, label: str, fields: tuple[str, ...], owner: str, evidence: str,
       *, caveat: str = "", registry: str | None = None) -> FactorSpec:
    return FactorSpec(factor_id=factor_id, label=label, required_field_ids=fields, owner=owner,
                      caveat=caveat, evidence=evidence, registry_factor_id=registry)


# ── FACTORS.md 정본 54 (V7 · Q8 · G10 · I5 · F9 · M3 · R4 · E8) ───────────────
# `required_field_ids` 는 그 문서의 **계산식** 을 field_id 로 옮긴 것이고, 대응이 애매한 곳은
# `reviews/2026-09-05-equity-workflow-proposal.md` §1-1~§1-8 의 산출 열을 따랐다.
# `registry_factor_id` 는 워크벤치 레지스트리 50(backend/FACTORS.md) 대응 — **재료가 같고 방향만
# 역수인 것까지** 잇고(PBR ↔ book_to_market), 재료가 다르면 NULL 로 둔다(추측 금지).
FACTORS: tuple[FactorSpec, ...] = (
    # 1. 밸류
    _f("V01", "PBR", ("price.market_cap", "financial.book_equity"), "factor_layer",
       "FACTORS §1 V01 = 시가총액 / 자본총계.", registry="financial.book_to_market"),
    _f("V02", "PER", ("price.market_cap", "financial.net_income"), "factor_layer",
       "FACTORS §1 V02 = 시가총액 / 당기순이익. 기간 축(분기 3개월 / 사업 12개월)은 report_code "
       "가 정하므로 TTM 합성은 팩터층 몫이다.", registry="financial.earnings_yield"),
    _f("V03", "PSR", ("price.market_cap", "financial.revenue"), "equity",
       "FACTORS §1 V03 = 시가총액 / 매출액.",
       caveat="금융업 470사는 매출액 개념이 성립하지 않는다 — banking_gross·insurance_gross "
              "합산식이 미확정(GAP-01, FACTORS §8·§11-7). equity 가 revenue_basis 규칙을 "
              "확정해야 풀린다.", registry="financial.sales_to_price"),
    _f("V04", "PCR", ("price.market_cap", "financial.operating_cash_flow"), "factor_layer",
       "FACTORS §1 V04 = 시가총액 / 영업활동현금흐름(연초누계 축)."),
    _f("V05", "EV/EBITDA", ("price.market_cap", "financial.borrowings", "financial.cash",
                            "financial.operating_income", "financial.depreciation"),
       "factor_layer",
       "FACTORS §1 V05 = (시총 + 순차입금) / (영업이익 + 감가상각). GAP-02 의 3계정 중 "
       "borrowings·depreciation 이 여기 걸린다 — 실재 판정은 dataset_profile 커버율이 한다.",
       caveat="차입금·감가상각은 fin_map 매핑이 늦게 붙어 커버 구간이 짧다 — first_usable_date "
              "가 그 사실이다."),
    _f("V06", "배당수익률", ("event.dividend_per_share", "price.close"), "factor_layer",
       "FACTORS §1 V06 = 주당배당금 / 주가.",
       caveat="배당 기준일·락일 원천이 없어 DPS 를 붙일 수 있는 날짜는 사업보고서 접수일뿐이다 "
              "(DESIGN §4-5 확정 5). TR(배당 재투자) 수익률은 여전히 불가하다."),
    _f("V07", "순현금비율", ("financial.cash", "financial.borrowings", "price.market_cap"),
       "factor_layer", "FACTORS §1 V07 = (현금성자산 − 총차입금) / 시가총액. GAP-02 borrowings.",
       caveat="차입금 커버 구간이 짧다 — first_usable_date 참조."),
    # 2. 퀄리티
    _f("Q01", "ROE", ("financial.net_income", "financial.book_equity"), "factor_layer",
       "FACTORS §2 Q01 = 순이익 / 자본총계. 평균자본을 쓰면 전기 자본이 필요하다.",
       registry="financial.roe"),
    _f("Q02", "ROA", ("financial.net_income", "financial.total_assets"), "factor_layer",
       "FACTORS §2 Q02 = 순이익 / 자산총계.", registry="financial.roa"),
    _f("Q03", "영업이익률", ("financial.operating_income", "financial.revenue"), "equity",
       "FACTORS §2 Q03 = 영업이익 / 매출액.",
       caveat="V03 과 같은 GAP-01 — 금융업 매출액 규칙 미확정.",
       registry="financial.operating_margin"),
    _f("Q04", "발생액", ("financial.net_income", "financial.operating_cash_flow",
                       "financial.total_assets"), "factor_layer",
       "FACTORS §2 Q04 = (순이익 − 영업활동현금흐름) / 자산총계. 현금흐름표는 DART 에만 있다.",
       caveat="현금흐름은 전 보고서가 연초누계다(DEFECT-C02) — 손익(분기 3개월)과 기간을 맞추는 "
              "것은 팩터층 몫이다.", registry="financial.accruals"),
    _f("Q05", "FCF 수익률", ("financial.operating_cash_flow", "financial.capex",
                          "price.market_cap"), "factor_layer",
       "FACTORS §2 Q05 = (영업활동현금흐름 − CAPEX) / 시가총액.", caveat="Q04 와 같은 기간 축."),
    _f("Q06", "부채비율", ("financial.total_liabilities", "financial.book_equity"),
       "factor_layer", "FACTORS §2 Q06 = 부채총계 / 자본총계.", registry="financial.leverage"),
    _f("Q07", "이자보상배율", ("financial.operating_income", "financial.interest_expense"),
       "factor_layer", "FACTORS §2 Q07 = 영업이익 / 이자비용. GAP-02 interest_expense.",
       caveat="이자비용 커버 구간이 짧다 — first_usable_date 참조."),
    _f("Q08", "NOA 비율", ("financial.total_assets", "financial.cash",
                        "financial.total_liabilities", "financial.borrowings"), "factor_layer",
       "FACTORS §2 Q08 = 순영업자산 / 자산총계. GAP-02 borrowings.",
       caveat="순영업자산의 계정 조합 정의는 FACTORS §11-3 에서 아직 미결이다 — 재료는 있고 "
              "정의가 팩터층 몫이다."),
    # 3. 성장
    _f("G01", "매출성장률", ("financial.revenue",), "equity",
       "FACTORS §3 G01 = (당기 매출 / 전기 매출) − 1.", caveat="V03 과 같은 GAP-01."),
    _f("G02", "영업이익성장률", ("financial.operating_income",), "factor_layer",
       "FACTORS §3 G02 = (당기 영업이익 / 전기) − 1."),
    _f("G03", "자산성장률", ("financial.total_assets",), "factor_layer",
       "FACTORS §3 G03 = (당기 자산 / 전기) − 1. 역방향 팩터다.",
       registry="financial.asset_growth"),
    _f("G04", "EPS 성장률", ("financial.eps_basic",), "equity",
       "FACTORS §3 G04 = (당기 EPS / 전기) − 1.",
       caveat="`fin_std.eps_basic` 은 **주식분할 미조정** 원장 값이라(삼성전자 2018 1분기 85,435 "
              "vs 사업보고서 6,461) 시계열 비율이 분할 구간에서 가짜 점프를 낸다 — adj_factor 로 "
              "조정한 EPS 축을 equity 가 내야 풀린다(FIELD_MAP §3)."),
    _f("G05", "영업이익 추정치 리비전", ("consensus.forward_op",), "factor_layer",
       "FACTORS §3 G05 = (op(t) / op(t−1M)) − 1.",
       caveat="관측점이 v3 2026-04-03~ 로 짧고, 겹치는 달의 wise·v3 중 어느 축을 쓸지도 팩터층이 "
              "골라야 한다. 리비전은 서로 다른 두 obs_month 관측점을 요구한다."),
    _f("G06", "순이익 추정치 리비전", ("consensus.forward_ni",), "factor_layer",
       "FACTORS §3 G06 = (ni(t) / ni(t−1M)) − 1.", caveat="G05 와 같다."),
    _f("G07", "매출 추정치 리비전", ("consensus.forward_sales",), "factor_layer",
       "FACTORS §3 G07 = (revenue(t) / revenue(t−1M)) − 1.",
       caveat="G05 와 같고, 단위가 억원이라 스케일 변환도 소비자 몫이다.",
       registry="consensus.sales_revision_1m"),
    _f("G08", "투자의견 리비전", ("consensus.recommendation",), "factor_layer",
       "FACTORS §3 G08 = opinion(t) − opinion(t−1M).",
       caveat="grain 에 `src` 가 있어 wise(measured 2일)와 v3(default·coverage_degraded)를 "
              "골라야 한다. 판본이 짧아 리비전은 축적 대기다.",
       registry="consensus.recommendation_change"),
    _f("G09", "목표주가 리비전", ("consensus.target_price",), "factor_layer",
       "FACTORS §3 G09 = (target(t) / target(t−1M)) − 1.",
       caveat="G08 과 같은 src 선택 + WISE 판본이 2일뿐이라 수준값만 즉시다."),
    _f("G10", "애널리스트 커버리지 수", ("consensus.analyst_count",), "factor_layer",
       "FACTORS §3 G10 = analyst_count(t) 및 변화.",
       caveat="수준값은 즉시, 변화분은 축적 대기. src 선택은 G08 과 같다.",
       registry="consensus.coverage_change"),
    # 4. 인컴 · 주주환원
    _f("I01", "배당성향", ("event.dividend_total", "financial.net_income"), "factor_layer",
       "FACTORS §4 I01 = 현금배당총액 / 순이익. 총액은 법인 축이라 `stock_knd='-'` 행이다.",
       caveat="원장이 준 `event.dividend_payout` 과 값이 다를 수 있다 — 그쪽은 연결/별도/개별 "
              "라벨(payout_basis)이 붙은 공표치다."),
    _f("I02", "배당성장률", ("event.dividend_per_share",), "factor_layer",
       "FACTORS §4 I02 = (당기 DPS / 전기 DPS) − 1.",
       caveat="종류주 축(stock_knd)을 소비자가 맞춰야 한다."),
    _f("I03", "자사주 순매입률", ("event.treasury_acquired", "event.treasury_disposed",
                            "price.market_cap"), "factor_layer",
       "FACTORS §4 I03 = (취득 − 처분) / 시가총액.",
       caveat="취득방법 3축 중 **잎 행만**(`acqs_mth3 <> '소계'`) 더해야 한다 — 소계·총계 행이 "
              "같은 테이블에 있다(FX-4B-001).", registry="event.buyback_announcement"),
    _f("I04", "주주환원율", ("event.dividend_total", "event.treasury_acquired",
                        "event.treasury_disposed", "price.market_cap"), "factor_layer",
       "FACTORS §4 I04 = (현금배당 + 자사주 순매입) / 시가총액 = I01 + I03.",
       caveat="I03 의 소계 행 주의가 그대로 적용된다."),
    _f("I05", "자사주 소각률", ("event.treasury_retired", "financial.shares_issued"),
       "factor_layer", "FACTORS §4 I05 = 소각수량 / 발행주식수. 소각 수량은 DART 전체에서 "
       "`treasury_stock.retired_shr` 뿐이다.",
       caveat="분모는 DART 발행주식총수다 — KRX 상장주식수(price.shares_outstanding)와 뜻이 "
              "다르므로 섞지 않는다."),
    # 5. 수급
    _f("F01", "외국인 순매수 강도", ("flow.foreign_net_buy", "price.trading_value"), "equity",
       "FACTORS §5 F01 = 외국인 순매수 / 거래대금(N일 누적). 재료는 키움 ka10060.",
       caveat="`flow_daily`(S08 수급 격자)가 아직 없다 — 원천은 백필 완료이고 슬라이스만 남았다.",
       registry="flow.foreign_net_buy_20d"),
    _f("F02", "외국인 보유비중 변화", ("flow.foreign_ownership",), "equity",
       "FACTORS §5 F02 = 보유비중(t) − 보유비중(t−N). 재료는 키움 ka10008.",
       caveat="F01 과 같은 S08 미구현.", registry="flow.foreign_ownership_change"),
    _f("F03", "기관 순매수 강도", ("flow.institution_net_buy", "price.trading_value"), "equity",
       "FACTORS §5 F03 = 기관 순매수 / 거래대금.",
       caveat="S08 미구현 + `orgn` 은 합계 컬럼이라 12분류를 어떻게 묶을지는 팩터층이 정한다"
              "(GAP-03, FACTORS §11-2 — 재료는 13주체 전부를 싣는다).",
       registry="flow.institution_net_buy_20d"),
    _f("F04", "연기금 순매수", ("flow.pension_net_buy",), "equity",
       "FACTORS §5 F04 = penfnd_etc 누적. 재료는 키움 ka10060 의 연기금 주체 컬럼.",
       caveat="F01 과 같은 S08 미구현."),
    _f("F05", "공매도 거래비중", ("short.short_sale_volume", "price.shares_outstanding"),
       "equity", "FACTORS §5 F05 = 공매도 거래량 / 상장주식수. 재료는 키움 ka10014.",
       caveat="**이름을 정정했다(2026-09-07)** — 이 값은 공매도 잔고가 아니라 **거래량**이다. "
              "진짜 잔고는 취득 불가로 확정됐다(FACTORS §12 F45). 요구 재료도 정정했다: 옛 "
              "선언은 `short.short_balance_ratio` 를 가리켰는데 그 필드는 **만들지 않기로 확정한 "
              "것**이다(분모가 다른 표에 있어 셀 하나로 굽지 않는다). 나눗셈은 팩터층 몫이다.",
       registry="short.short_balance_ratio"),
    _f("F06", "공매도 거래비중", ("short.short_sale_value", "price.trading_value"), "equity",
       "FACTORS §5 F06 = 공매도 대금 / 거래대금.", caveat="F05 와 같은 S09 미구현.",
       registry="short.short_sale_ratio_20d"),
    _f("F07", "대차잔고 비율", ("short.borrowed_quantity", "price.shares_outstanding"), "equity",
       "FACTORS §5 F07 = 대차잔고 / 상장주식수. 공매도 선행 지표.",
       caveat="S09 미구현 + 키움 `rmnd` 는 **단위 미측정**이고 KIS 축은 287 티커만 커버한다"
              "(GAP-04, STAGE_HANDOFF §4).", registry="short.borrow_utilization"),
    _f("F08", "외국인 한도소진율", ("flow.foreign_limit_exhaustion",), "equity",
       "FACTORS §5 F08 = limit_exh_rt (원장에 직접 있다).",
       caveat="F01 과 같은 S08 미구현 + 단위 미측정이라 노출 시 profile 에 단위를 못박아야 한다."),
    _f("F09", "신용잔고율", ("credit.margin_balance", "price.shares_outstanding"), "equity",
       "FACTORS §5 F09 = 융자잔고 / 상장주식수. 재료는 KIS FHPST04760000.",
       caveat="`credit_daily`(S10)가 아직 없다. 금액축 `*_amt` 6컬럼은 단위 미상이라 주식수 축을 "
              "쓴다(STAGE_HANDOFF §4).", registry="credit.margin_balance_ratio"),
    # 6. 모멘텀 · 위험
    _f("M01", "1/3/6/12개월 수익률", ("price.adj_close",), "factor_layer",
       "FACTORS §6 M01 = 종가(t) / 종가(t−N) − 1. **조정가**를 써야 한다 — 원주가를 쓰면 분할 "
       "구간 수익률이 틀린 채 게이트를 전부 통과한다(결정 6).",
       caveat="PR 수익률이다 — 배당 재투자(TR) 원천이 없다(GAP-18).",
       registry="price.momentum_12_1"),
    _f("M02", "52주 신고가 근접도", ("price.adj_close", "price.high"), "factor_layer",
       "FACTORS §6 M02 = 종가 / 52주 최고가.",
       caveat="`price.high` 는 KRX '0' 을 NULL 로 두는 정책이라 결측 행이 있다 — 크기는 "
              "dataset_profile 의 estimated_coverage_pct 가 잰다(구 GAP-14 파생 미측정 해소).",
       registry="price.distance_52w_high"),
    _f("M03", "거래량 급증", ("price.volume",), "factor_layer",
       "FACTORS §6 M03 = 거래량 / 20일 평균 거래량.",
       caveat="분할 구간을 넘길 때는 조정 거래량 뷰(v_adj_volume_fwd)를 써야 한다.",
       registry="flow.volume_surge"),
    _f("R01", "변동성", ("price.adj_close",), "factor_layer",
       "FACTORS §6 R01 = 일간 수익률 표준편차(N일).", registry="price.volatility_60d"),
    _f("R02", "최대낙폭", ("price.adj_close",), "factor_layer",
       "FACTORS §6 R02 = (고점 − 현재) / 고점.", registry="price.max_drawdown_252d"),
    _f("R03", "유동성(회전율)", ("price.trading_value", "price.market_cap"), "factor_layer",
       "FACTORS §6 R03 = 거래대금 / 시가총액.", registry="flow.turnover_20d"),
    _f("R04", "시장 베타", ("price.adj_close", "benchmark.close"), "unavailable",
       "FACTORS §6 R04 = Cov(r_i, r_m) / Var(r_m), 자기시장 지수.",
       caveat="재료 자체는 `index_daily.close_idx` 로 실재하지만 **지수는 security 축이 아니라** "
              "`benchmark.close` field_id 가 카탈로그에 없다(GAP-09). `idx:` 접두 어휘"
              "(FIELD_MAP §1)를 어댑터 계약으로 올리는 것이 선결이고 그것은 엔진 저장소 이슈다.",
       registry="price.beta_252d"),
    # 7. 이벤트 · 지분
    _f("E01", "내부자 순매수", ("event.insider_stake_change",), "factor_layer",
       "FACTORS §7 E01 = Σ 임원·주요주주 지분 **변동률**. 수량을 쓰면 액면병합이 대량매도로 "
       "읽힌다 — 그래서 rate_change_pct 축이다.",
       caveat="커버는 롤링 2년 창뿐이고 과거로 갈 수 없다(DART API 제약, 재수집 불가) — "
              "first_usable_date 가 그 창의 시작이다.", registry="event.insider_trade"),
    _f("E02", "5% 대량보유 변동", ("event.major_holder_stake",), "factor_layer",
       "FACTORS §7 E02 = 신규 진입·지분 변화(majorstock).",
       caveat="E01 과 같은 롤링 2년 창."),
    _f("E03", "최대주주 지분율", ("event.largest_holder_stake",), "factor_layer",
       "FACTORS §7 E03 = 최대주주·특수관계인 기말 지분율(hyslrSttus)."),
    _f("E04", "실질 유통비율", ("event.largest_holder_stake", "financial.shares_treasury",
                          "financial.shares_issued"), "factor_layer",
       "FACTORS §7 E04 = 1 − 최대주주 지분율 − 자사주비율. free float 이라 시총 가중에 영향.",
       caveat="세 재료의 축(법인·종류주)을 소비자가 맞춰야 한다."),
    _f("E05", "자사주 취득 발표", ("event.buyback_amount",), "equity",
       "FACTORS §7 E05 = 취득 결정 공시 이벤트 더미(발표일 초과수익).",
       caveat="`corp_event` 가 MVP 4유형(split·reverse_split·bonus·capred)만 적재해 "
              "`treasury_buy` 행이 0 이다 — 원천 stg_event_tsstk_aq 1,951 건은 실재(S05 후속)."),
    _f("E06", "유상증자·CB 발행", ("event.capital_raise_amount",), "equity",
       "FACTORS §7 E06 = 희석 이벤트(piicDecsn·cvbdIsDecsn).", caveat="E05 와 같은 S05 후속."),
    _f("E07", "상폐 위험", ("universe.delist_signal", "universe.admin_state"), "equity",
       "FACTORS §7 E07 = 부도·해산·회생·관리 이벤트. 생존편향 26.75% 제거의 근거.",
       caveat="KOSPI 관리종목 **해제 공시가 3건뿐**이라 상태 종료를 잴 수 없고 신호가 KOSDAQ 으로 "
              "기운다(GAP-06). `admin_state_basis` 가 행마다 근거를 남기지만 비대칭 자체는 "
              "원천 부재라 개선 불가다."),
    _f("E08", "비적정 감사의견", ("event.audit_opinion",), "factor_layer",
       "FACTORS §7 E08 = adt_opinion ≠ 적정. 상장폐지 선행 신호.",
       caveat="`n_source_rows > 1` 인 행은 원장 여러 행이 접힌 것이라 건수 집계에 그대로 쓰면 "
              "안 된다."),
)

FACTOR_IDS: tuple[str, ...] = tuple(f.factor_id for f in FACTORS)
N_FACTORS = len(FACTORS)

_DECL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("factor_id", "VARCHAR"), ("label", "VARCHAR"), ("registry_factor_id", "VARCHAR"),
    ("required_field_ids", "VARCHAR[]"), ("owner", "VARCHAR"), ("caveat", "VARCHAR"),
    ("evidence", "VARCHAR"),
)


def declaration_rows() -> list[tuple[object, ...]]:
    """`_decl_factor` 에 들어갈 54행. `FACTORS.md` 의 표를 옮긴 선언면의 정본이다."""
    return [(f.factor_id, f.label, f.registry_factor_id, list(f.required_field_ids), f.owner,
             f.caveat, f.evidence) for f in FACTORS]


def install_declarations(con: duckdb.DuckDBPyConnection, rule: EquityTable) -> None:
    """`_decl_factor` 를 올린다 — `.sql` 은 이 표와 `dataset_profile` 조인만 한다."""
    cols = ", ".join(f'"{c}" {t}' for c, t in _DECL_COLUMNS)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _decl_factor ({cols})")
    marks = ", ".join("?" for _ in _DECL_COLUMNS)
    con.executemany(f"INSERT INTO _decl_factor VALUES ({marks})", declaration_rows())


# ── EG10 ─────────────────────────────────────────────────────────────────────

def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def missing_required_columns(ctx: EquityGateContext, status: str = "ready") -> list[str]:
    """`status` 행의 `required_columns` 중 실물에 없는 것 (EG10 폐기형 술어 (a)).

    `table_name` 이 매크로 이름(`v_adj_price_fwd`)이면 빌드 세션에 실체가 없다 — 카탈로그 단계가
    EG11 로 따로 판정하므로 여기서는 이름이 `views.SIGNATURES` 에 등재됐는지만 본다.
    """
    v = _q(ctx.out_view)
    pairs = ctx.con.execute(
        f"SELECT DISTINCT u.c FROM {v}, unnest(required_columns) AS u(c) "
        f"WHERE status = '{status}' ORDER BY 1").fetchall()
    missing: list[str] = []
    for (entry,) in pairs:
        table, _, columns = str(entry).partition(".")
        if table in views.SIGNATURES:
            continue
        if table not in ctx.pinned:
            missing.append(str(entry))
            continue
        actual = {str(r[0]) for r in ctx.con.execute(f"DESCRIBE {_q(table)}").fetchall()}
        missing += [f"{table}.{c}" for c in columns.split(",") if c not in actual]
    return missing


def eg10_factor_readiness(ctx: EquityGateContext) -> GateResult:
    """EG10 — 팩터 준비도 (GATES §6 · §9 「EG10 이름」 · WORKFLOW §4 DoD 6단계).

    폐기형: 54행 전수 · blocked 는 reason·owner 필수 · ready 는 first_usable_date 필수 ·
    **ready 행의 재료 컬럼 실재** · 어휘 폐쇄. ready 하한(`ready_min`)은 baseline 미등재면
    `skip(no_baseline)` 이 아니라 그 술어만 빼고 측정치를 남긴다 — 나머지 폐기형 술어는 첫 빌드에도
    돌아야 한다.
    """
    v = _q(ctx.out_view)
    ids = [str(r[0]) for r in ctx.con.execute(
        f"SELECT factor_id FROM {v} ORDER BY 1").fetchall()]
    declared = set(FACTOR_IDS)
    status_vocab = ", ".join(f"'{s}'" for s in STATUS_VOCAB)
    owner_vocab = ", ".join(f"'{o}'" for o in OWNER_VOCAB)
    reason_vocab = ", ".join(f"'{r}'" for r in BLOCKED_REASONS)
    missing_cols = missing_required_columns(ctx)
    n_ready = _n(ctx, f"SELECT count(*) FROM {v} WHERE status = 'ready'")
    checks = {
        "n_rows_off_expected": abs(len(ids) - N_FACTORS),
        "n_factor_id_undeclared": len([i for i in ids if i not in declared]),
        "n_factor_id_absent": len(declared - set(ids)),
        "n_status_outside_vocab": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE status NOT IN ({status_vocab})"),
        "n_owner_outside_vocab": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE owner IS NULL OR owner NOT IN ({owner_vocab})"),
        "n_reason_outside_vocab": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE blocked_reason IS NOT NULL "
                 f"AND split_part(blocked_reason, ':', 1) NOT IN ({reason_vocab})"),
        "n_blocked_without_reason": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE status = 'blocked' "
                 "AND coalesce(trim(blocked_reason), '') = ''"),
        "n_ready_with_reason": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE status = 'ready' AND blocked_reason IS NOT NULL"),
        "n_ready_without_first_usable_date": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE status = 'ready' AND first_usable_date IS NULL"),
        "n_required_field_ids_empty": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE len(required_field_ids) = 0"),
        # 폐기형 (a): ready 인데 재료 컬럼이 실물에 없다 = 표가 거짓말을 한다
        "n_ready_required_column_absent": len(missing_cols),
    }
    reasons = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT split_part(blocked_reason, ':', 1), count(*) FROM {v} "
        "WHERE blocked_reason IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()}
    metrics: dict[str, object] = {
        "n_factors": len(ids), "n_ready": n_ready, "n_blocked": len(ids) - n_ready,
        "ready_ratio": round(n_ready / len(ids), 6) if ids else 0.0,
        "blocked_reason_counts": reasons,
        "n_by_group": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f"SELECT substr(factor_id, 1, 1), count(*) FILTER (WHERE status = 'ready') "
            f"FROM {v} GROUP BY 1 ORDER BY 1").fetchall()},
        "blocked": {str(r[0]): str(r[1]) for r in ctx.con.execute(
            f"SELECT factor_id, blocked_reason FROM {v} WHERE status = 'blocked' "
            "ORDER BY 1").fetchall()},
        "owner_counts": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f"SELECT owner, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()},
        "first_usable_date_max": str(_one_value(
            ctx, f"SELECT max(first_usable_date) FROM {v} WHERE status = 'ready'")),
        "missing_required_columns": missing_cols,
    }
    try:
        ready_min = require_const(ctx, "ready_min", metrics)
    except SkipGate:      # 하한만 미등재 — 나머지 폐기형 술어는 첫 빌드에도 그대로 돈다
        metrics["ready_min"] = None
        metrics["ready_min_note"] = (
            "baseline 미등재 — 하한 술어만 빼고 나머지 폐기형은 그대로 돈다(GATES §7-3). "
            "측정치는 n_ready 로 남는다.")
    else:
        checks["n_ready_below_baseline"] = int(n_ready < ready_min)
        metrics["ready_min"] = ready_min
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult("EG10", GateStatus.PASS,
                          f"팩터 준비도 {n_ready}/{len(ids)} ready", merged)
    return GateResult("EG10", GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


def _one_value(ctx: EquityGateContext, sql: str) -> object:
    row = ctx.con.execute(sql).fetchone()
    return None if row is None else row[0]


eg10_factor_readiness.gate_name = "EG10"                    # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

FACTOR_READINESS = register(EquityTable(
    name=TABLE_NAME,
    grain=("factor_id",),
    # 순서 = DESIGN §4-8 + `label`(인계 문서의 "팩터 × 컬럼 × 시작일 표" 가 이 테이블이라 이름이
    # 없으면 사람이 읽을 수 없다).
    columns={"factor_id": "VARCHAR", "label": "VARCHAR", "registry_factor_id": "VARCHAR",
             "required_columns": "VARCHAR[]", "required_field_ids": "VARCHAR[]",
             "status": "VARCHAR", "blocked_reason": "VARCHAR", "owner": "VARCHAR",
             "first_usable_date": "DATE", "caveat": "VARCHAR", "evidence": "VARCHAR"},
    # `dataset_profile` 이 판정의 유일한 입력이고, 나머지는 EG10 이 ready 행의 재료 컬럼 실재를
    # 실물 스키마로 확인하기 위한 것이다(선언만 보고 통과시키면 표가 거짓말을 할 수 있다).
    inputs=(PROFILE_TABLE, *rules_s19.SOURCE_TABLES),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,      # 준비도 판정 — 행 자체에 공개시점이 없다
    eg1_lhs_sql="",                     # 선언표 — skip(declaration_table)
    eg1_rhs_sql="",
    sql_path=SQL_DIR / f"{TABLE_NAME}.sql",
    input_columns=dict.fromkeys((PROFILE_TABLE, *rules_s19.SOURCE_TABLES), ()),
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg10_factor_readiness,),
    declaration_table=True,
    declarations=install_declarations,
))

TABLES: tuple[EquityTable, ...] = (FACTOR_READINESS,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s20.json"
"""`factor_readiness.ready_min` 은 **사람 승인 대기** — 파일이 실측과 이유를 기록한다."""

__all__ = ["BASELINE_SEED", "BLOCKED_REASONS", "FACTORS", "FACTOR_IDS", "FACTOR_READINESS",
           "N_FACTORS", "OWNER_VOCAB", "STATUS_VOCAB", "TABLES", "declaration_rows"]
