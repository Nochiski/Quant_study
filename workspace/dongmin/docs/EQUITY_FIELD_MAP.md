# Equity 필드 대응표 (`EQUITY_FIELD_MAP.md` v1.0, 2026-09-05)

> 워크벤치 팩터 레지스트리(`backend/FACTORS.md`, `domain.factor.FactorRegistry` 50개)가 요구하는 **field_id 42종** ↔ equity 테이블·컬럼·뷰의 대응. 어댑터 `adapters/outbound/equity_duckdb`(워크벤치 5포트) 와 `backtest_engine/adapters/equity_duckdb.py`(커널 3포트) 구현의 사양이며, S00 의 `check_field_map.py` 가 레지스트리와의 집합 차 0 을 CI 로 확인한다.
> 판정 어휘: **지원** = 컬럼이 있고 PIT 규칙이 닫힘 · **부분** = 재료는 있으나 조건(시작일·단위·의미)이 붙음 · **미지원** = 원천 없음(카탈로그에서 `unavailable` 로 명시, mock fallback 금지) · **미확인** = 슬라이스 착수 시 판정.
> 출처: `reviews/2026-09-05-equity-workflow-proposal.md` §7-3 (오케스트레이터 검증 · `close_pt` → `close_idx` 정정).

## 1. 축 어휘 (계약)

| 축 | 규칙 | 근거 |
|---|---|---|
| `security_id` | **`{ticker}:{span_seq}`** — 재상장 2종(036220·101970)은 구간마다 다른 id. mock 의 `"sec-005930-1"` 형식은 쓰지 않는다 | `security_span` grain |
| `universe_id` | `krx.all`(정책 미적용) · **`krx.common-stock`**(`sec_type='common'`, 계약 테스트 `backend/tests/contract/test_raw_observation_port.py:33` 의 값) · `krx.investable`·`krx.liquid`(`universe_policy` 확정 후) | 계약 테스트 |
| `market` | `"KRX"` (계약 테스트) · `venue='XKRX'`(커널 `InstrumentId`) | 두 어휘 병기 |
| `benchmark` | `security_id` 예약 접두 **`idx:`** — `idx:코스피`·`idx:코스닥`·`idx:코스피 200`·`idx:코스닥 150` → `index_daily`. `security` 테이블에 지수 행을 넣지 않는다 | GAP-09 |
| `sessions` | `trading_calendar`(거래일만). `history_sessions_before_start` 는 캘린더에서 역산 | `RawObservationSet` |
| 랙 단위 | `dataset_profile.recommended_lag_sessions`(**세션**). 일 단위 `recommended_lag_days` 는 병기하되 어댑터는 세션을 쓴다 | `DatasetFieldProfile` |
| 결측 어휘 | equity `fill_kind.kind` → 엔진 `CellKind`: `measured`→OBSERVED · `src_omitted`→SOURCE_OMITTED_ZERO · `empty_response`·stage `miss_kind`→MISSING · `not_collected`→NOT_COLLECTED · `security_span` 밖·`backfill_end` 이후→COVERAGE_GAP | `dataset_profile.supported_cell_kinds` |
| `data_snapshot_id` | 카탈로그 생성 시 전 테이블 `build_id` 정렬 해시 = `equity.duckdb` 의 `snapshot_id` | `FactorMatrixCacheKey` |
| `previous_weight`·`forward_return` | equity 소유 아님 — 어댑터가 `0.0`·`None` 고정 | 포트 docstring |
| 가격 조정 | **`price.close` = 원주가(불변)**, **`price.adj_close` = `v_adj_price(asof)`**(효력일·공개일 모두 ≤ asof 인 계수만, base = asof). 레지스트리의 수익률·모멘텀·변동성 팩터는 `adj_close` 를 써야 한다 — 레지스트리 개정은 워크벤치 이슈(결정 6) | 원칙 ② · GAP `price.close` |

## 2. field_id 대응 (42)

| field_id | equity 산출 | 판정 | 비고 |
|---|---|---|---|
| `price.close` | `price_daily.close`(원주가) | **부분** | **핵심 충돌**: registry `price.momentum_12_1` 은 조정가를 전제한다. 원주가를 그대로 주면 분할 구간 모멘텀이 틀린다. **해소안: `price.close`(원주가) + `price.adj_close`(`v_adj_price`, base = query.end) 2필드로 분리하고 엔진 registry 의 가격 그래프를 `price.adj_close` 로 바꾸는 이슈를 엔진 저장소에 발행** |
| `price.open` | `price_daily.open` | **부분** | NULL 유지 정책 · `Bar.open` 은 필수·>0 (GAP-14) |
| `price.volume` | `price_daily.volume_shr` / `v_adj_volume` | 지원 | 조정 여부 명시 필요 |
| `price.market_cap` | `price_daily.mktcap_krw` / `v_firm_mktcap` | 지원 | 랙 1세션(익일 지식) |
| `price.shares_outstanding` | `price_daily.shares_out` | 지원 | |
| `price.trading_value` | `price_daily.value_krw` | 지원 | |
| `benchmark.close` | `index_daily.close_idx` | **미지원(현 설계)** | GAP-09 — security 축이 아님 |
| `financial.book_equity` | `fin_std.total_equity` / `v_fin_latest` | 지원 | |
| `financial.net_income` | `fin_std.net_income`·`ttm_net_income` | 지원 | |
| `financial.revenue` | `fin_std.revenue`(+`revenue_basis`) | **부분** | GAP-01 금융업 470사 |
| `financial.total_assets` | `fin_std.total_asset` | 지원 | |
| `financial.operating_income` | `fin_std.op_profit` | 지원 | |
| `financial.operating_cash_flow` | `fin_std.cf_operating_ytd`·`_q` | **부분** | ytd/분기 축 선택 필요 |
| `financial.total_liabilities` | `fin_std.total_liab` | 지원 | |
| `financial.gross_profit` | — | **미지원** | `FACTORS.md` 정본 54 에 매출총이익 팩터가 없고 `EQUITY_DESIGN.md` §6 계정 매트릭스에도 없다. 엔진 factor #16 `gross_profitability` 는 unavailable |
| `consensus.forward_eps` | `consensus_daily(metric='eps')` | **부분** | mock 라벨은 "12개월 선행 EPS". equity 는 `target_period` 별 값만 준다 — **12M forward 합성은 팩터층** 이라는 경계를 `field_map` 에 명시 |
| `consensus.forward_sales` | `consensus_daily(metric='revenue')` | 부분 | 동일 |
| `consensus.target_price` | `opinion_daily.target_price_krw` | **부분** | 판본 2일(P5) |
| `consensus.recommendation` | `opinion_daily.opinion_score` | 부분 | 동일 |
| `consensus.analyst_count` | `opinion_daily.analyst_count` | 부분 | WISE 커버 804 |
| `consensus.eps_dispersion` | `consensus_daily.est_min`·`est_max` | **부분** | v3 구간은 min/max NULL(§4-6) |
| `flow.foreign_net_buy` | `flow_daily.frgnr_invsr_krw` | 지원 | |
| `flow.institution_net_buy` | `flow_daily.orgn_krw` | **부분** | GAP-03 — `orgn` 은 합계 컬럼 |
| `flow.retail_net_buy` | `flow_daily.ind_invsr_krw` | 지원 | |
| `flow.foreign_ownership` | `flow_daily.foreign_wght_pct` | 지원 | |
| `flow.block_buy`·`flow.block_sell` | — | **미지원** | 대량매매·프로그램매매 미수집(`FACTORS.md` §9) |
| `short.short_balance_ratio` | `short_daily.short_volume_shr / shares_out` | **부분** | 진짜 잔고 아님(F45 취득 불가) — 라벨 정정 필요 |
| `short.short_sale_value` | `short_daily.short_value_krw` | 지원 | |
| `short.borrowed_quantity` | `lending_balance_kis_shr` / `_kiwoom_raw` | **부분** | GAP-04 |
| `credit.margin_balance` | `credit_daily.whol_loan_rmnd_stcn_shr`(주식수) | **부분** | 금액축 `*_amt` 6컬럼 단위 미상(`STAGE_HANDOFF.md` §4) |
| `credit.net_buy` | — | **미확인** | `stg_credit_daily` 에 순매수 축이 있는지 미확인 → S10 에서 판정 |
| `credit.collateral_value`·`credit.loan_value`·`credit.forced_liquidation` | — | **미지원** | 원천 없음 |
| `event.dividend_per_share` | `dividend_event.dps_krw` | 지원 | 락일 없음 |
| `event.buyback_amount` | `corp_event.amount_krw`(`tsstk_aq` 1,951) | 지원 | |
| `event.insider_net_buy` | `holder_daily`(elestock) | **부분** | 2024-08~ 롤링 2년 |
| `event.earnings_surprise` | — | **미지원** | 잠정실적 공시일 필요(`FACTORS.md` §9) |
| `event.index_membership_change` | — | **미지원** | 지수 구성종목 PIT 없음(`EQUITY_WORKFLOW.md` §6) |
| `event.disclosure_sentiment` | — | **미지원** | 텍스트층(문서층 P4) |
| `classification.sector` | `corp.induty_code`(현재값) | **부분/미지원** | GAP-07 — `point_in_time=False` 라벨 필수 |

## 3. 집계

- 지원 17 · 부분 15 · 미지원 9 · 미확인 1 (2026-09-05, 슬라이스 착수 전 판정).
- 미지원 9 는 원천 부재(대량매매·반대매매·담보·잠정실적·지수구성 PIT·텍스트 감성·매출총이익·차입금 계열)로, 어댑터 `list_fields()` 가 `unavailable` 로 답한다. 레지스트리 50 팩터 중 이 필드에 걸린 팩터는 `factor_readiness.status='blocked'`.
- `financial.gross_profit` 은 `fin_map.py` 에 `gross_profit` 항목이 있으므로 4단계 계정 매트릭스에 추가하면 **지원**으로 바뀐다(S12 첫 작업에서 판정).

## 4. 유지 규약

- 레지스트리(`backend/FACTORS.md`)가 바뀌면 이 표를 같은 PR 에서 갱신한다. `check_field_map.py` 는 레지스트리 필드 집합 − 표 필드 집합 = ∅ 를 검사한다.
- 판정 변경은 근거(슬라이스·실측 절)를 남긴다.
