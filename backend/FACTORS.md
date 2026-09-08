# Factor Registry v1

이 문서는 `domain.factor`의 `FactorRegistry`가 공개하는 50개 안정 ID와 Equity field 요구사항을
사람이 검토할 수 있게 고정한 카탈로그입니다. 실행 의미의 SoT는 코드의 versioned registry이며,
문서는 테스트로 registry와 드리프트하지 않는지 확인합니다.

- 상태 `implemented`: M3 mock Equity adapter에서 즉시 preview 가능
- 상태 `catalog_only`: ID와 데이터 요구사항은 예약됐지만 기본 실행 graph는 후속 구현 대상
- 모든 입력은 `available_date <= as_of`인 PIT 관측값만 사용
- `factor_id`, registry version, graph hash, data snapshot, parameters, as-of range가 재현성 키를 구성

| # | Factor ID | Category | Preference | Required Equity fields | Min history | Status |
|---:|---|---|---|---|---:|---|
| 1 | `price.momentum_12_1` | price | high | `price.close` | 252 | implemented |
| 2 | `price.momentum_6_1` | price | high | `price.close` | 126 | catalog_only |
| 3 | `price.reversal_1m` | price | low | `price.close` | 21 | catalog_only |
| 4 | `price.volatility_60d` | price | low | `price.close` | 60 | catalog_only |
| 5 | `price.beta_252d` | price | low | `price.close`, `benchmark.close` | 252 | catalog_only |
| 6 | `price.max_drawdown_252d` | price | low | `price.close` | 252 | catalog_only |
| 7 | `price.distance_52w_high` | price | high | `price.close` | 252 | catalog_only |
| 8 | `price.overnight_return_20d` | price | high | `price.open`, `price.close` | 21 | catalog_only |
| 9 | `price.intraday_return_20d` | price | high | `price.open`, `price.close` | 21 | catalog_only |
| 10 | `price.liquidity_amihud_20d` | price | low | `price.close`, `price.volume` | 21 | catalog_only |
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
| 40 | `credit.margin_balance_change_20d` | credit | low | `credit.margin_balance` | 20 | implemented |
| 41 | `credit.margin_balance_ratio` | credit | low | `credit.margin_balance`, `price.market_cap` | 1 | catalog_only |
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
validator/compiler/evaluator를 통과하며 UI(YAML source editor와 read-only projection)가 별도
계산식을 소유하지 않습니다.

| Category | Executable default | Core operation |
|---|---|---|
| price | `price.momentum_12_1` | 252-session momentum, 21-session skip, cross-sectional rank |
| financial | `financial.book_to_market` | PIT book equity / lagged market cap, rank |
| consensus | `consensus.forward_eps_growth` | rolling forward EPS growth |
| flow | `flow.foreign_net_buy_20d` | 20-session foreign net-buy mean |
| short | `short.short_balance_ratio` | short balance ratio |
| credit | `credit.margin_balance_change_20d` | 20-session margin balance delta |
| event | `event.earnings_surprise` | PIT earnings surprise |
