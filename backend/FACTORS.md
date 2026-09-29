# Factor Registry v1

이 문서는 `domain.factor`의 `FactorRegistry`가 공개하는 50개 안정 ID와 Equity field 요구사항을
사람이 검토할 수 있게 고정한 카탈로그입니다. 실행 의미의 SoT는 코드의 versioned registry입니다.
테스트(`tests/domain/test_factor_research.py`)는 registry의 모든 factor ID가 이 문서에 있는지와
`implemented` 행이 7개인지만 확인합니다. 범주·선호·필드·최소 이력 열은 registry를 고칠 때 같은
PR에서 사람이 맞춥니다.

- 상태 `implemented`: 기본 graph가 registry에 있다. 편집기는 이 팩터만 예시 조각으로 넣어 주며,
  넣으면 graph가 전략 문서에 복사된다
- 상태 `catalog_only`: ID와 데이터 요구사항은 예약됐지만 기본 실행 graph는 후속 구현 대상
- 모든 입력은 `available_date <= as_of`인 PIT 관측값만 사용
- 가격 변화(수익률·모멘텀·이평·변동성·낙폭·고점 거리·베타)는 수정주가 `price.adj_close`(전방
  조정, 그날까지 적용·공개된 분할·증자·병합 계수만 곱해 과거 값이 바뀌지 않는다)를 읽는다. 원주가
  `price.close`는 같은 날 두 값을 견주는 비율(장중 수익률·목표주가 괴리·배당수익률)과 거래대금에만
  쓴다. 야간 수익률은 수정 시가 필드가 없어 `adj_close[t]/adj_close[t-1] × open[t]/close[t]`로 만든다
  (이슈 #214)
- **수정주가는 모든 사건을 잇지 않는다**(이슈 #220). 원장이 그날 사건을 접지 못한 적용일은 그 행이
  결측이다 — 계수가 적용일 다음 세션에 공개된 사건(그날 하루 스파이크, 최대 약 38배)과 KRX 기준가는
  바뀌었는데 주식수와 맞지 않아 계수를 못 낸 사건(`krx_base_inconsistent`, 층 이동). 창 연산은 창에
  결측이 하나라도 있으면 결측이라 그 행을 품는 창 전체가 결측이 된다. 실원장 2020-03-19 이후 12-1
  모멘텀 셀의 1.37%(278종목)가 결측이 되고, 그중 81%는 사건 불연속을 품어 틀렸던 값이다. 틀린 값보다
  결측을 낸다. 유상증자 권리락처럼 원장이 계수를 만들지 않는 사건(`unknown_price_only`)은 조정 없이
  남아 그날 수익률에 가격 변화가 그대로 들어간다
- 재무 흐름 필드 `financial.revenue`·`gross_profit`·`operating_income`·`net_income`·
  `operating_cash_flow`는 **최근 4분기 합(TTM)** 이다. 최신 공시가 분기보고서든 사업보고서든 늘
  12개월 값이고, 연속 4분기가 공시일 기준으로 전부 접수됐을 때만 값이 선다. 4분기를 채울 수 없으면
  결측이며 3개월·연간 값으로 대신하지 않는다(이슈 #212). 잔고 필드 `financial.book_equity`·
  `total_assets`·`total_liabilities`는 보고 기간 말 시점 값이라 TTM 대상이 아니다. ROE·ROA·이익수익률
  같은 비율은 TTM 분자와 최신 잔고 분모를 쓴다. field_id·registry version·data snapshot id는
  그대로라 이 변경(2026-09-27) 이전 실행과 데이터 스냅샷이 같아도 재무 팩터 값은 다르다 — 이전 실행
  결과와 재무 팩터 값을 직접 비교하지 않는다(#235)
- 연결재무제표(CFS)와 별도재무제표(OFS)를 오가는 법인은 전환 뒤 최대 약 7분기 동안 TTM이 비어 있다.
  창 네 분기와, 사업보고서 4분기를 만드는 앞 3분기가 모두 같은 구분이어야 하기 때문이다. 영구 결측은
  아니고 같은 구분의 분기가 다시 차면 값이 선다
- `factor_id`, registry version, graph hash, data snapshot, parameters, as-of range가 재현성 키를 구성
- `credit.margin_balance_change_20d`는 신용잔고율(잔고 주식수 ÷ 상장주식수)의 20세션 차이다(이슈 #234).
  원 주식수의 변화율은 분할·병합을 신용 급증으로 읽고(035720 5:1 분할 뒤 +300%) 작은 첫 값에서
  폭주했다. 두 필드는 각자 공개 랙(신용잔고 3세션 · 주식수 1세션)대로 나눈다 — **액면 분할·병합·감자**
  에서는 신용잔고 원천이 거래정지 첫날부터 새 주식수 단위로 바뀌어(잔고 척도 전환 비율 중앙값 분할·병합
  1.04 · 감자 1.00) 실원장에서는 이쪽이 사건 구간 튐이 가장 작다. 남는 튐은 사건 세션과 19세션 뒤 반대
  부호로 한 쌍이다(분할·병합 38%에서 |값|>0.005, 사건별 최대 |값| 중앙값 0.0023 · p90 0.027, 사건의
  약 10%가 p99 0.021 을 넘는다)
- **무상증자 척도 창은 결측이다**(이슈 #249). 무상증자에서는 신용잔고 원천이 새 단위로 바뀌지 않거나
  일부만 바뀌어(전환 비율 중앙값 0.41) 권리락일부터 옛 단위와 새 단위가 섞이고, 상장주식수는 신주
  상장일에야 바뀐다. 가리기 전에는 신주 상장 뒤 약 20세션 동안 큰 음수가 나와(247540 −0.0135 ·
  182360 −0.034 · 221610 −0.17) 무상증자 창 셀의 약 22%가 전체 하위 1%에 들어 선호 LOW 순위에서
  우대받았다. 원장 뷰 `v_credit_balance`가 권리락일부터 정해진 세션 수의 잔고를 결측으로 내고, 이
  팩터는 20세션 창 안에 결측이 하나라도 있으면 결측이라 그대로 따른다 — 창 25세션이면 권리락 3세션
  뒤부터 46세션 뒤까지 값이 없다. 창 길이와 근거의 정본은 원장 뷰(`database/src/equity/views.py`)다.
  실원장(2020-03-19 이후)에서 신주 상장 뒤 20세션 창의 하위 1% 셀은 1,545 → 3, 결측이 된 셀은 전체의
  0.58%(22,121)다. 계수로 되돌리는 척도 보정은 전환이 부분적이라 쓰지 않는다
- 가림은 공시 다음 행부터라(PIT) 권리락일 뒤에 공시된 사건(정기보고서 회고 원천·정정본)은 공시 전
  행을 가리지 못한다. 결측 정책이 `zero`·`cross_sectional_median`이면 가린 잔고도 다른 결측 잔고처럼
  채워져 창 양 끝에서 오히려 큰 변화가 난다 — 이 팩터는 기본값 `drop`(또는 `keep`)에서만 가림이 뜻을 갖는다
- 2026-09-27 전에 저장한 전략은 옛 graph(원 주식수 변화율·차이)를 문서에 복사해 두었으므로 자동으로
  바뀌지 않는다 — 같은 factor_id 아래 두 정의가 공존하고 값 척도도 다르다. 새 정의를 쓰려면 팩터를
  다시 넣는다

| # | Factor ID | Category | Preference | Required Equity fields | Min history | Status |
|---:|---|---|---|---|---:|---|
| 1 | `price.momentum_12_1` | price | high | `price.adj_close` | 273 | implemented |
| 2 | `price.momentum_6_1` | price | high | `price.adj_close` | 126 | catalog_only |
| 3 | `price.reversal_1m` | price | low | `price.adj_close` | 21 | catalog_only |
| 4 | `price.volatility_60d` | price | low | `price.adj_close` | 60 | catalog_only |
| 5 | `price.beta_252d` | price | low | `price.adj_close`, `benchmark.close` | 252 | catalog_only |
| 6 | `price.max_drawdown_252d` | price | low | `price.adj_close` | 252 | catalog_only |
| 7 | `price.distance_52w_high` | price | high | `price.adj_close` | 252 | catalog_only |
| 8 | `price.overnight_return_20d` | price | high | `price.open`, `price.close`, `price.adj_close` | 21 | catalog_only |
| 9 | `price.intraday_return_20d` | price | high | `price.open`, `price.close` | 21 | catalog_only |
| 10 | `price.liquidity_amihud_20d` | price | low | `price.adj_close`, `price.close`, `price.volume` | 21 | catalog_only |
| 11 | `financial.book_to_market` | financial | high | `financial.book_equity`, `price.market_cap` | 1 | implemented |
| 12 | `financial.earnings_yield` | financial | high | `financial.net_income`, `price.market_cap` | 1 | catalog_only |
| 13 | `financial.sales_to_price` | financial | high | `financial.revenue`, `price.market_cap` | 1 | catalog_only |
| 14 | `financial.roe` | financial | high | `financial.net_income`, `financial.book_equity` | 5 | catalog_only |
| 15 | `financial.roa` | financial | high | `financial.net_income`, `financial.total_assets` | 5 | catalog_only |
| 16 | `financial.gross_profitability` | financial | high | `financial.gross_profit`, `financial.total_assets` | 5 | catalog_only |
| 17 | `financial.operating_margin` | financial | high | `financial.operating_income`, `financial.revenue` | 5 | catalog_only |
| 18 | `financial.asset_growth` | financial | low | `financial.total_assets` | 5 | catalog_only |
| 19 | `financial.accruals` | financial | low | `financial.operating_cash_flow`, `financial.net_income`, `financial.total_assets` | 5 | catalog_only |
| 20 | `financial.leverage` | financial | low | `financial.total_liabilities`, `financial.total_assets` | 1 | catalog_only |
| 21 | `consensus.forward_eps_growth` | consensus | high | `consensus.forward_eps` | 20 | implemented |
| 22 | `consensus.earnings_revision_1m` | consensus | high | `consensus.forward_eps` | 21 | catalog_only |
| 23 | `consensus.target_price_upside` | consensus | high | `consensus.target_price`, `price.close` | 1 | catalog_only |
| 24 | `consensus.recommendation_change` | consensus | high | `consensus.recommendation` | 21 | catalog_only |
| 25 | `consensus.sales_revision_1m` | consensus | high | `consensus.forward_sales` | 21 | catalog_only |
| 26 | `consensus.dispersion` | consensus | low | `consensus.eps_dispersion` | 1 | catalog_only |
| 27 | `consensus.coverage_change` | consensus | high | `consensus.analyst_count` | 21 | catalog_only |
| 28 | `flow.foreign_net_buy_20d` | flow | high | `flow.foreign_net_buy` | 20 | implemented |
| 29 | `flow.institution_net_buy_20d` | flow | high | `flow.institution_net_buy` | 20 | catalog_only |
| 30 | `flow.retail_net_buy_20d` | flow | low | `flow.retail_net_buy` | 20 | catalog_only |
| 31 | `flow.foreign_ownership_change` | flow | high | `flow.foreign_ownership` | 20 | catalog_only |
| 32 | `flow.turnover_20d` | flow | high | `price.volume`, `price.shares_outstanding` | 20 | catalog_only |
| 33 | `flow.volume_surge` | flow | high | `price.volume` | 60 | catalog_only |
| 34 | `flow.block_trade_imbalance` | flow | high | `flow.block_buy`, `flow.block_sell` | 20 | catalog_only |
| 35 | `short.short_balance_ratio` | short | low | `short.short_balance_ratio` | 1 | implemented |
| 36 | `short.short_sale_ratio_20d` | short | low | `short.short_sale_value`, `price.trading_value` | 20 | catalog_only |
| 37 | `short.short_balance_change_20d` | short | low | `short.short_balance_ratio` | 20 | catalog_only |
| 38 | `short.borrow_utilization` | short | low | `short.borrowed_quantity`, `price.shares_outstanding` | 1 | catalog_only |
| 39 | `short.short_covering` | short | high | `short.short_balance_ratio`, `price.close` | 20 | catalog_only |
| 40 | `credit.margin_balance_change_20d` | credit | low | `credit.margin_balance`, `price.shares_outstanding` | 20 | implemented |
| 41 | `credit.margin_balance_ratio` | credit | low | `credit.margin_balance`, `price.shares_outstanding` | 1 | catalog_only |
| 42 | `credit.credit_net_buy_20d` | credit | low | `credit.net_buy` | 20 | catalog_only |
| 43 | `credit.collateral_ratio` | credit | high | `credit.collateral_value`, `credit.loan_value` | 1 | catalog_only |
| 44 | `credit.forced_liquidation_pressure` | credit | low | `credit.forced_liquidation`, `price.trading_value` | 20 | catalog_only |
| 45 | `event.earnings_surprise` | event | high | `event.earnings_surprise` | 1 | implemented |
| 46 | `event.dividend_yield_event` | event | high | `event.dividend_per_share`, `price.close` | 1 | catalog_only |
| 47 | `event.buyback_announcement` | event | high | `event.buyback_amount`, `price.market_cap` | 1 | catalog_only |
| 48 | `event.insider_trade` | event | high | `event.insider_net_buy`, `price.market_cap` | 1 | catalog_only |
| 49 | `event.index_rebalance` | event | high | `event.index_membership_change` | 1 | catalog_only |
| 50 | `event.disclosure_sentiment` | event | high | `event.disclosure_sentiment` | 1 | catalog_only |

## M3 executable subset

각 카테고리에서 하나씩, 총 7개 기본 graph를 제공합니다. 모든 graph는 같은 expression node 계약과
validator/compiler/evaluator를 통과하며 UI(YAML source editor, 그리고 같은 source 위의 Form·Graph
트랜잭션 편집기와 JSON/Diff read-only projection)가 별도 계산식을 소유하지 않습니다.

| Category | Executable default | Core operation |
|---|---|---|
| price | `price.momentum_12_1` | 수정주가 `price.adj_close`의 252-session momentum, 21-session skip, cross-sectional rank |
| financial | `financial.book_to_market` | PIT book equity / market cap(공개 랙은 원장 `dataset_profile`), rank |
| consensus | `consensus.forward_eps_growth` | rolling forward EPS growth |
| flow | `flow.foreign_net_buy_20d` | 20-session foreign net-buy mean |
| short | `short.short_balance_ratio` | short balance ratio |
| credit | `credit.margin_balance_change_20d` | 20-session change of margin balance ratio (balance / shares outstanding) |
| event | `event.earnings_surprise` | PIT earnings surprise |
