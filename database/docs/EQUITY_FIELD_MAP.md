# Equity 필드 대응표 (`EQUITY_FIELD_MAP.md` v1.0, 2026-09-05)

> 워크벤치 팩터 레지스트리(`backend/FACTORS.md`, `domain.factor.FactorRegistry` 50개)가 요구하는 **field_id 42종** ↔ equity 테이블·컬럼·뷰의 대응. 어댑터 `adapters/outbound/equity_duckdb`(워크벤치 5포트) 와 `backtest_engine/adapters/equity_duckdb.py`(커널 3포트) 구현의 사양이며, S00 의 `check_field_map.py` 가 레지스트리와의 집합 차 0 을 CI 로 확인한다.
> 판정 어휘: **지원** = 컬럼이 있고 PIT 규칙이 닫힘 · **부분** = 재료는 있으나 조건(시작일·단위·의미)이 붙음 · **미지원** = 원천 없음(카탈로그에서 `unavailable` 로 명시, mock fallback 금지) · **미확인** = 슬라이스 착수 시 판정.
> 출처: `reviews/2026-09-05-equity-workflow-proposal.md` §7-3 (오케스트레이터 검증 · `close_pt` → `close_idx` 정정).

## 1. 축 어휘 (계약)

| 축 | 규칙 | 근거 |
|---|---|---|
| `security_id` | **`{ticker}:{span_seq}`** — 재상장 2종(036220·101970)은 구간마다 다른 id. mock 의 `"sec-005930-1"` 형식은 쓰지 않는다 | `security_span` grain |
| `universe_id` | `krx.all`(정책 미적용) · **`krx.common-stock`**(`sec_type='common'` ∧ `status='listed'` — S03B `universe_policy` 행, 계약 테스트 `backend/tests/contract/test_raw_observation_port.py:33` 의 값) · `krx.investable`(S03B 플래그 4행: common-stock + `NOT admin_state` + `NOT liquidation_window`) · `krx.liquid`(S03B-2, 09-05 결정 "날짜별 상위 비율": investable 4행 + **S03C flag 1행 `no_trade_reason <> 'illiquid'`** + `adv20_rank_pct >= 1 − liquid_top_pct` quantile 1행(rule_seq 6) — `adv20_rank_pct` 는 같은 날 보통주·listed·adv20 있음 모집단 안 `adv20_krw` 의 cume_dist, baseline `universe_policy.liquid_top_pct` 0.5 = 상위 50%) | 계약 테스트 |
| `no_trade_reason` | (S03C) `universe_daily` 무거래 이유 폐쇄 어휘 — `halt_disclosed`(정지 공시) · `liquidation`(정리매매 창) · `corp_action_window`(같은 티커 `adj_factor` 적용일이 [D − 5, D + 45] 세션 창 안, `factor_ok` 무관) · `admin`(관리종목이고 지정 신호가 5세션 안) · `illiquid`(설명되지 않는 무거래) · `none`(거래 행·가격 행 없는 날 = 판정 대상 아님). 판정은 `price_kind='reference'` 행에만, 우선순위는 나열 순. `status='suspended'` = `halt_state` ∨ `corp_action_window` ∨ (`illiquid` ∧ `no_trade_run ≥ no_trade_run_k`) | DESIGN §4-1 · `sql/universe_daily.sql` `reasoned` |
| `market` | `"KRX"` (계약 테스트) · `venue='XKRX'`(커널 `InstrumentId`) | 두 어휘 병기 |
| `benchmark` | `security_id` 예약 접두 **`idx:`** — `idx:코스피`·`idx:코스닥`·`idx:코스피 200`·`idx:코스닥 150` → `index_daily`. `security` 테이블에 지수 행을 넣지 않는다 | GAP-09 |
| `sessions` | `trading_calendar`(거래일만). `history_sessions_before_start` 는 캘린더에서 역산 | `RawObservationSet` |
| 랙 단위 | `dataset_profile.recommended_lag_sessions`(**세션**). 일 단위 `recommended_lag_days` 는 병기하되 어댑터는 세션을 쓴다 | `DatasetFieldProfile` |
| 결측 어휘 | equity `fill_kind.kind` → 엔진 `CellKind`: `measured`→OBSERVED · `src_omitted`→SOURCE_OMITTED_ZERO · `empty_response`·stage `miss_kind`→MISSING · `not_collected`→NOT_COLLECTED · `security_span` 밖·`backfill_end` 이후→COVERAGE_GAP | `dataset_profile.supported_cell_kinds` |
| `data_snapshot_id` | 카탈로그 생성 시 전 테이블 `build_id` 정렬 해시 = `equity.duckdb` 의 `snapshot_id` | `FactorMatrixCacheKey` |
| `previous_weight`·`forward_return` | equity 소유 아님 — 어댑터가 `0.0`·`None` 고정 | 포트 docstring |
| 가격 조정 | **`price.close` = 원주가(불변)**, **`price.adj_close` = `v_adj_price_fwd(asof)`**(**전방 조정**, 결정 09-05: 각 행 d 에 apply_date ≤ d ∧ available_date ≤ d 인 계수의 share_factor 누적곱 — 첫 관측 수준 고정, 005930 2018-05-03 2,650,000 · 05-04 2,595,000; 값은 (security, date) 의 순수 함수라 창·asof 에 무관, `available_date` = greatest(원주가 공개일, 접힌 계수 공개일) = d). 차트·EG8 은 base = asof 인 `v_adj_price(asof)`. 레지스트리의 수익률·모멘텀·변동성 팩터는 `adj_close` 를 써야 한다 — 레지스트리 개정은 워크벤치 이슈(결정 6) | 원칙 ② · GAP `price.close` · DESIGN §11 ① |
| KRX 기준가(S06-2) | `price_daily.change_krw`(KRX 전일 대비)·`base_price_krw`(= close − change, 그날 기준가)는 **field_id 가 아니다** — `adj_factor` 의 `krx_base_price` 원천(계수·적용 세션의 정본)과 EG3 기록형이 읽는 내부 축. 어댑터는 노출하지 않고, `adj_factor.event_type` `unknown_krx`(corp_event 밖의 기준가 사건, 시총 불변)는 커널 어댑터가 share_factor 방향으로 SPLIT/REVERSE_SPLIT 로 보낸다 | DESIGN §4-2 v3 · GATES §9 S06-2 |

## 2. field_id 대응 (42)

| field_id | equity 산출 | 판정 | 비고 |
|---|---|---|---|
| `price.close` | `price_daily.close`(원주가) | **부분** | **핵심 충돌**: registry `price.momentum_12_1` 은 조정가를 전제한다. 원주가를 그대로 주면 분할 구간 모멘텀이 틀린다. **해소안: `price.close`(원주가) + `price.adj_close`(`v_adj_price_fwd`, 전방 조정 — 09-05 결정) 2필드로 분리하고 엔진 registry 의 가격 그래프를 `price.adj_close` 로 바꾸는 이슈를 엔진 저장소에 발행** |
| `price.open` | `price_daily.open` | **부분** | NULL 유지 정책 · `Bar.open` 은 필수·>0 (GAP-14) |
| `price.volume` | `price_daily.volume_shr` / `v_adj_volume`·`v_adj_volume_fwd` | 지원 | 조정 여부 명시 필요 |
| `price.market_cap` | `price_daily.mktcap_krw` / `v_firm_mktcap` | 지원 | 랙 1세션(익일 지식) — **S21 축소 어댑터는 0**(`price_daily.available_date = date`, 종가와 같은 시점 확정; `dataset_profile`(S19)에서 확정, DESIGN §11) |
| `price.shares_outstanding` | `price_daily.shares_out` | 지원 | 정본은 KRX(`stg_listing_daily.list_shrs`) — DART 발행주식총수(`shares_outstanding.issued_shr`, S16)는 **검산·보조**이고 이 필드로 나가지 않는다(DESIGN §4-2 · §4-5 확정 6). 둘은 뜻이 다르다: KRX 는 상장주식수, DART 는 발행주식총수라 비상장 종류주·신주 상장 전 구간에서 갈린다(절단본 비교 73 중 6) |
| `price.trading_value` | `price_daily.value_krw` | 지원 | |
| `benchmark.close` | `index_daily.close_idx` | **미지원(현 설계)** | GAP-09 — security 축이 아님 |
| `financial.book_equity` | `fin_std.total_equity` / `v_fin_latest` | 지원 | |
| `financial.net_income` | `fin_std.net_income`(+ `net_income_q4_derived`) | 지원 | 분기는 3개월·사업보고서는 12개월 값이다(`report_code` 가 기간을 정한다). **TTM 합성은 팩터층** — `fin_std` 에 `ttm_net_income` 컬럼은 없다(S12 구현 09-06) |
| `financial.revenue` | `fin_std.revenue`(+`revenue_basis`) | **부분** | GAP-01 금융업 470사 |
| `financial.total_assets` | `fin_std.total_asset` | 지원 | |
| `financial.operating_income` | `fin_std.op_profit` | 지원 | |
| `financial.operating_cash_flow` | `fin_std.cf_operating_ytd`·`cf_operating_q` | **부분** | 두 축을 다 싣는다(S12 구현 09-06). `_q` 는 직전 보고서 누계와의 차라 직전 판본이 없으면 NULL(`cf_q_n_rows` 가 구성 수를 남긴다) — 소비 측이 축을 골라야 한다 |
| `financial.total_liabilities` | `fin_std.total_liab` | 지원 | |
| `financial.gross_profit` | `fin_std.gross_profit`(+ `_q4_derived`) | **지원**(09-06 판정 변경) | `fin_map.FIN_MAP['gross_profit']`(concept `GrossProfit`, nm 매출총이익)이 실재해 S12 가 24계정에 실었다 — 절단본 커버율 0.945, 삼성전자 2018 111,377,004백만원 = 매출 − 매출원가 원 단위 일치(§10 P30). `FACTORS.md` 정본 54 에 매출총이익 팩터는 여전히 없고 엔진 factor #16 `gross_profitability` 만 이 필드를 쓴다 |
| `consensus.forward_eps` | `consensus_daily(metric='eps')` | **부분** | mock 라벨은 "12개월 선행 EPS". equity 는 `target_period` 별 값만 준다 — **12M forward 합성은 팩터층** 이라는 경계를 `field_map` 에 명시 |
| `consensus.forward_sales` | `consensus_daily(metric='revenue')` | 부분 | 동일 |
| `consensus.target_price` | `opinion_daily.target_price_krw` | **부분** | 판본 2일(P5) |
| `consensus.recommendation` | `opinion_daily.opinion_score` | 부분 | 동일 |
| `consensus.analyst_count` | `opinion_daily.analyst_count` | 부분 | WISE 커버 804 |
| `consensus.eps_dispersion` | `consensus_daily.est_min`·`est_max` | **부분** | v3 구간은 min/max NULL(§4-6) |
| `flow.foreign_net_buy` | `flow_daily.frgnr_invsr_krw` | 지원 | S08 구현(09-06). 원 단위(stage 가 백만원 ×1e6 완료) · 값 없는 셀은 NULL + `fill_kind` |
| `flow.institution_net_buy` | `flow_daily.orgn_krw` | **부분** | GAP-03 — `orgn` 은 합계 컬럼이고 **기관 7주체 합과 다르다**(S08 절단본 실측: 18,581행 중 10,783행 불일치, 편차 최대 2,834억원). 12주체 항등식(EG3-P06)에서 제외 |
| `flow.retail_net_buy` | `flow_daily.ind_invsr_krw` | 지원 | S08 구현(09-06) |
| `flow.foreign_ownership` | `flow_daily.foreign_wght_pct` | **미확인** | **S08 미구현 → S08-2**. 원천이 `stg_flow_daily_kiwoom`(ka10060)이 아니라 `stg_foreign_daily`(ka10008)인데 절단본에 없어 검증축이 0이다 — 컬럼을 만들고 NULL 로 두는 대신 만들지 않았다(F08 `limit_exh_rt_pct`·F43 `foreign_poss_shr` 도 같다). 선행 조건: 절단본에 `stg_foreign_daily` 절단 |
| `flow.block_buy`·`flow.block_sell` | — | **미지원** | 대량매매·프로그램매매 미수집(`FACTORS.md` §9) |
| `short.short_balance_ratio` | `short_daily.short_volume_kiwoom_shr` 또는 `short_volume_kis_shr` ÷ `price_daily.shares_out` | **부분** | 진짜 잔고 아님(F45 취득 불가) — 라벨 정정 필요. **S09 구현(09-06)은 두 원천을 합치지 않는다** — 어댑터가 원천을 고르고, 고른 원천의 `fill_kind_short_<src>` 로 결측 3분류를 읽는다 |
| `short.short_sale_value` | `short_daily.short_value_kiwoom_krw`(stage ×1e3) 또는 `short_value_kis_krw`(원) | 지원 | 원천별 컬럼. 키움 공매도 평균가는 단위 미측정이라 `short_avg_price_kiwoom_raw` + `_basis='unknown'` 로 간다(FX-3-009 규약) |
| `short.borrowed_quantity` | `short_daily.lending_balance_kis_shr`(+ 금액축 `lending_balance_kis_krw`, stage ×1e6) | **부분** | GAP-04. **S09 는 KIS 축만** — 키움 `lending_balance_kiwoom_raw` 는 원장 `stg_lending_daily` 가 입력에 들어오는 후속 슬라이스 몫이고, 두 축의 단위 대조(겹침 비율 분포)도 그때 선다(DESIGN §4-3 구현 결과 ①). 원장이 주는 음수 잔고는 그대로 보존한다 |
| `credit.margin_balance` | `credit_daily.whol_loan_rmnd_stcn_shr`(주식수) | **부분** | 금액축 `*_amt` 6컬럼 단위 미상(`STAGE_HANDOFF.md` §4) |
| `credit.net_buy` | — | **미확인** | `stg_credit_daily` 에 순매수 축이 있는지 미확인 → S10 에서 판정 |
| `short.short_balance_ratio` | `short_daily.short_volume_shr / shares_out` | **부분** | 진짜 잔고 아님(F45 취득 불가) — 라벨 정정 필요 |
| `short.short_sale_value` | `short_daily.short_value_krw` | 지원 | |
| `short.borrowed_quantity` | `lending_balance_kis_shr` / `_kiwoom_raw` | **부분** | GAP-04 |
| `credit.margin_balance` | `credit_daily.whol_loan_rmnd_stcn_shr`(주식수) | **부분** | 금액축 `*_amt` 6컬럼 단위 미상(`STAGE_HANDOFF.md` §4) — 산출은 원값 보존 + `credit_daily.amt_basis = 'unknown'`(S10). 대주 잔고는 `whol_stln_rmnd_stcn_shr`. **격자 빈칸은 0 이 아니라 NULL** 이고 뜻은 `fill_kind.kind` 가 나른다(`src_omitted` → `CellKind.SOURCE_OMITTED_ZERO`, §1 결측 어휘) — 소비자가 0 으로 읽을지는 셀 종류를 보고 정한다 |
| `credit.net_buy` | — | **미지원** | **S10 판정(09-06): 원천에 축이 없다.** `stg_credit_daily` 39컬럼에 순매수 항목이 없고, 유일한 후보 `whol_*_new_stcn_shr − whol_*_rdmp_stcn_shr`(신규 − 상환)는 순매수가 아니라 잔고 증감의 구성요소인데 실제 증감과도 맞지 않는다 — 절단본 융자 17,364/24,711(70.3%)·대주 24,672/24,711, 신규·상환 음수 6행. 매 빌드 `EG3_credit_daily.net_buy_axis`·`loan_balance_step` 이 근거를 갱신한다(DESIGN §9 결정 8 · GATES §9) |
| `credit.collateral_value`·`credit.loan_value`·`credit.forced_liquidation` | — | **미지원** | 원천 없음 |
| `event.dividend_per_share` | `dividend_event.dps_krw` | **부분** | S16 구현(09-06)으로 판정 하향. ① **락일·기준일이 없다** — 값이 서는 시점은 `available_date`(사업보고서 접수일, 결산일 + 3~8개월)뿐이라 TR·배당 재투자 팩터는 불가하다(DESIGN §4-5 확정 5 · §11). ② 축이 **(corp_code, bsns_year, reprt_code, stock_knd)** 라 티커 축으로 쓰려면 `corp_ticker` 전개 + 종류 대응이 필요하다(equity 는 전개하지 않는다 — grain 을 넘는 복제 금지). ③ 연 1회(`reprt_code='11011'`) 값이다 |
| `event.buyback_amount` | `corp_event.amount_krw`(`tsstk_aq` 1,951) | 지원 | |
| `event.insider_net_buy` | `holder_daily.qty_change_shr`(`src='elestock'`) | **부분** | 커버 구간이 **롤링 2년**이다(DART API 가 그 창만 준다 — DART_DESIGN P3e "재수집 불가"). 절단본 실측 창 2024-08-26 ~ 2026-08-26. `qty_shr`(보고 후)·`qty_prev_shr`(= 후 − 증감)도 함께 준다 |
| `event.earnings_surprise` | — | **미지원** | 잠정실적 공시일 필요(`FACTORS.md` §9) |
| `event.index_membership_change` | — | **미지원** | 지수 구성종목 PIT 없음(`EQUITY_WORKFLOW.md` §6) |
| `event.disclosure_sentiment` | — | **미지원** | 텍스트층(문서층 P4) |
| `classification.sector` | `corp.induty_code`(현재값) | **부분/미지원** | GAP-07 — `point_in_time=False` 라벨 필수 |

## 3. 집계

- **§2 표 42 의 판정 집계(2026-09-06 S21 본판 재계수)**: 지원 **15** · 부분 **16** · 미지원 **9** · 미확인 **1** · 부분/미지원 **1**(`classification.sector`) = 42. (초안의 "지원 18/16" 두 줄은 서로 어긋나 있었다 — 표를 한 행씩 세어 정정한다.)
- **판정 ↔ 어댑터 노출의 대응**: 지원 15 + 부분 16 = 31 중 어댑터가 실제로 내는 것은 **23** 이고, 나머지 8(`flow.*` 4 지원/부분 · `short.*` 3 · `credit.margin_balance`)은 판정이 아니라 **테이블 미병합·산식 문제**로 빠진다. 즉 판정표는 "원천이 있느냐" 를, `list_fields()` 는 "이 빌드에서 읽히느냐" 를 말한다 — 둘이 다를 때 사유는 `UNSUPPORTED_FIELDS` 가 문장으로 남긴다.
- **S21 본판 어댑터(09-06)가 실제로 내는 field_id 는 24** — 위 표의 **23** + equity 내부 스코프 `price.adj_close`(표 밖). 나머지 19 는 `list_fields()` 에 없고 질의하면 `INVALID_QUERY`(detail `unavailable` + 사유 문장)다. 사유는 셋으로 갈린다(정본 `adapters/outbound/equity_duckdb/_specs.py::UNSUPPORTED_FIELDS`): **원천 부재 10**(`benchmark.close` GAP-09 · `flow.block_*` 2 · `credit.collateral_value`·`credit.loan_value`·`credit.forced_liquidation` · `event.earnings_surprise`·`event.index_membership_change`·`event.disclosure_sentiment` · `credit.net_buy` 미확인) · **테이블 미병합 7**(`flow.*` 4 · `short.short_sale_value`·`short.borrowed_quantity` · `credit.margin_balance` — S08~S10 은 브랜치 `equity/s08-s10` 에 있고 미병합) · **굽지 않기로 한 2**(`short.short_balance_ratio` 는 분모가 `price_daily.shares_out` 이라 셀 하나가 아니고 원장 값이 잔고가 아니다 · `classification.sector` 는 시점축 없는 현재값 라벨이라 과거 세션에 붙이면 look-ahead — DESIGN §7 `sector_id=None` 규약). `price.adj_close` 는 **전방 조정**(`v_adj_price_fwd`, 09-05 결정)이라 수준·비율 모두 PIT 이고 창에 무관하다(DESIGN §7·§11 ①). 레지스트리 가격 팩터가 `price.close` 를 요구하는 충돌(#64)은 미해결 — `scripts/run_mvp_backtest.py` 는 FieldNode 를 `price.adj_close` 로 바꿔 돈다.
- 미지원 9 는 원천 부재(대량매매·반대매매·담보·잠정실적·지수구성 PIT·텍스트 감성·매출총이익·차입금 계열)로, 어댑터 `list_fields()` 가 `unavailable` 로 답한다. 레지스트리 50 팩터 중 이 필드에 걸린 팩터는 `factor_readiness.status='blocked'`.
- **`financial.*` 가 요구하는 `fin_std` 컬럼(S12 구현 09-06, 단위 원 KRW · 결측은 NULL · 부분합·보간 금지)**: `revenue`(+`revenue_basis`) · `op_profit` · `net_income` · `total_asset` · `total_liab` · `total_equity` · `gross_profit` · `cf_operating_ytd`/`_q`. 손익 계정은 전부 `<계정>_q4_derived` 를 동반하고(사업보고서 − Σ3분기, 하나라도 없으면 NULL) 파생 블록마다 `q4_derived_available_date`·`q4_derived_n_rows` / `cf_q_available_date`·`cf_q_n_rows` 가 붙는다. 기간 어휘는 `report_code` 가 정한다 — 11011 은 12개월, 11012·11013·11014 는 3개월 손익이고 현금흐름은 전부 연초누계다(DEFECT-C02). `eps_basic` 은 **주식분할 미조정**(원장 그대로 — 삼성전자 2018 1분기 85,435 vs 사업보고서 6,461)이라 시계열로 쓰려면 `adj_factor` 가 필요하다. `fin_std` 의 24 계정 중 `financial.*` 밖(cost_of_sales·pretax_income·net_income_owners·eps_basic·equity_owners·cash·inventories·current_assets·current_liab·lease_liab·borrowings·depreciation·interest_expense·cf_investing_ytd·cf_financing_ytd·capex_ytd)는 equity 내부 스코프이고 `dataset_profile`(S19)이 노출 여부를 정한다.

- **S15(4B 지분·감사, 09-06)가 낸 3테이블 중 레지스트리 field_id 에 닿는 것은 `event.insider_net_buy` 하나**다(`holder_daily`, 판정 **부분** 유지 — 롤링 2년 창). 나머지는 **equity 내부 스코프**이고 `dataset_profile`(S19)이 노출 여부를 정한다: `holder_daily` 의 5% 대량보유 축(`src='majorstock'` · `rate_pct`·`rate_change_pct`·`report_tp`·`report_resn` — 경영권 분쟁·행동주의·기관 대량 진입의 PIT 이벤트 스트림, DATA_CATALOG CA-10) · `ownership_snapshot`(최대주주·특수관계인 지분율 — free float 분자, DATA_CATALOG CA-09) · `audit_opinion`(`adt_opinion_class ∈ {적정, 한정, 의견거절, 부적정, other}` — 상장폐지 위험 필터). 레지스트리 50 팩터 중 이 셋을 요구하는 팩터는 없고 `FACTORS.md` 정본 54 에도 지배구조·감사 팩터가 없다 — 필드를 만들려면 레지스트리 쪽 이슈가 먼저다(§4 유지 규약).
- **세 테이블의 소비 규약**: 전부 법인 축(`corp_code`)이고 **티커 컬럼이 없다** — 종목 축으로 쓰려면 `corp_ticker` 로 조인한다(법인→티커는 우선주·재상장 때문에 1:N 이라 equity 가 전개하지 않는다). PIT 축은 셋 다 `available_date = rcept_dt`(basis `derived`)이고 `ownership_snapshot`·`audit_opinion` 은 내용일 `stlm_dt`(결산기준일)와 접수일 사이 랙이 있다(절단본 위반 0). `n_source_rows > 1` 인 행은 stage 원장 여러 행이 한 grain 으로 접힌 것이므로(`audit_opinion` 절단본 52행, 최대 6) 건수 집계에 그대로 쓰면 안 된다.
- 지원 16 · 부분 16 · 미지원 9 · 미확인 1 (2026-09-06 — `event.dividend_per_share` 를 S16 실측으로 지원 → 부분 하향, 근거는 §2 비고).
- 지원 17 · 부분 15 · 미지원 9 · 미확인 1 (2026-09-05, 슬라이스 착수 전 판정). **S08 뒤(09-06) 재판정: 지원 17 → 16 · 미확인 1 → 2** — `flow.foreign_ownership` 이 지원에서 미확인으로 내려갔다(원천 `stg_foreign_daily` 미절단, S08-2). 집합은 그대로라 `check_field_map.py` 판정은 불변이다.
- **S08 `flow_daily`(09-06)** — 실제로 내는 flow field_id 는 **3**: `flow.foreign_net_buy`(`frgnr_invsr_krw`) · `flow.retail_net_buy`(`ind_invsr_krw`) · `flow.institution_net_buy`(`orgn_krw`, 부분). 전부 **원 단위**이고 stage 가 백만원 ×1e6 환산을 마친 값을 그대로 나른다 — 어댑터가 다시 스케일하면 안 된다. 값 없는 셀은 **NULL + `fill_kind`** 이고 어휘 대응은 §1 '결측 어휘' 그대로다(`measured`→OBSERVED · `src_omitted`→SOURCE_OMITTED_ZERO · `empty_response`→MISSING · `not_collected`→NOT_COLLECTED). `src` 는 `not_collected` 셀에서 **NULL**(그 셀을 덮는 수집 로그가 없어 원천이 없다) — 어댑터는 `src` 를 필드로 노출하지 않는다. 랙은 `available_date = date`(basis `default`)이며 세션 랙 확정은 `dataset_profile`(S19).
- 지원 17 · 부분 15 · 미지원 **10** · 미확인 **0** (2026-09-06). 착수 전 판정은 지원 17 · 부분 15 · 미지원 9 · 미확인 1 이었고, **S10 이 마지막 미확인 `credit.net_buy` 를 미지원으로 확정**했다(원천 부재 — 위 표의 근거). 미확인 칸은 이제 비어 있다.
- **S21 축소 어댑터(09-05)가 실제로 내는 field_id 는 3** — `price.close`·`price.market_cap`·`price.adj_close`(equity 내부 스코프, 위 표 밖). 나머지 39 는 `list_fields()` 에 없고 질의하면 `INVALID_QUERY`(detail `unavailable`)다. `price.adj_close` 는 **전방 조정**(`v_adj_price_fwd`, 09-05 결정)이라 수준·비율 모두 PIT 이고 창에 무관하다(DESIGN §7·§11 ①; 이전 base = 창 end 절충은 폐기). 레지스트리 가격 팩터가 `price.close` 를 요구하는 충돌(#64)은 미해결 — `scripts/run_mvp_backtest.py` 는 FieldNode 를 `price.adj_close` 로 바꿔 돈다.
- 미지원 10 은 원천 부재(대량매매·반대매매·담보·잠정실적·지수구성 PIT·텍스트 감성·매출총이익·차입금 계열 + **신용 순매수**(S10 판정))로, 어댑터 `list_fields()` 가 `unavailable` 로 답한다. 레지스트리 50 팩터 중 이 필드에 걸린 팩터는 `factor_readiness.status='blocked'`.
- `financial.gross_profit` 은 `fin_map.py` 에 `gross_profit` 항목이 있으므로 4단계 계정 매트릭스에 추가하면 **지원**으로 바뀐다(S12 첫 작업에서 판정).
- (판정 변경 이력) 2026-09-06 S12 가 `financial.gross_profit` 을 미지원 → 지원으로, S16 실측이 `event.dividend_per_share` 를 지원 → 부분으로 바꿨다. 근거는 §2 비고.
- 미지원 9 는 원천 부재(대량매매·반대매매·담보·잠정실적·지수구성 PIT·텍스트 감성·매출총이익·차입금 계열)로, 어댑터 `list_fields()` 가 `unavailable` 로 답한다. 레지스트리 50 팩터 중 이 필드에 걸린 팩터는 `factor_readiness.status='blocked'`.
- **S16(09-06)이 낸 equity 내부 스코프 필드 9** — 레지스트리(`backend/FACTORS.md`)가 요구하지 않으므로 위 표에 행을 두지 않는다(`price.adj_close` 와 같은 규약). 전부 축이 **(corp_code, bsns_year, reprt_code, …)** 이고 PIT 축은 `available_date`(사업보고서 접수일)다. 티커 축 팩터로 쓰려면 소비자가 `corp_ticker` 로 전개하고 종류(`se`·`stock_knd`)를 대응시켜야 한다.
  - `financial.shares_issued` = `shares_outstanding.issued_shr`(발행주식총수) · `financial.shares_treasury` = `.treasury_shr`(자기주식수) · `financial.shares_distributed` = `.distributed_shr`(유통주식수). **`price.shares_outstanding` 의 대체가 아니다** — 그쪽 정본은 KRX 상장주식수다.
  - `event.treasury_acquired`·`event.treasury_disposed`·`event.treasury_retired` = `treasury_stock.acquired_shr`·`disposed_shr`·`retired_shr`. 취득방법 3축이 grain 에 있으므로 종목·연도 합계는 **`acqs_mth3 <> '소계'` 인 잎 행만** 더해야 한다(소계 행이 함께 실려 있다 — GATES FX-4B-001). 기존 `event.buyback_amount`(`corp_event.amount_krw`, 결정공시 축)와는 축도 시점도 다르다: 이쪽은 사업보고서 확정치다.
  - `event.dividend_yield`·`event.dividend_payout`·`event.dividend_total` = `dividend_event.yield_pct`·`payout_pct`(+`payout_basis`)·`cash_total_krw`. 뒤 둘은 법인 축이라 `stock_knd='-'` 행에만 실린다. `yield_pct` 는 원장 값 그대로이고 DPS/종가로 재계산하지 않는다(원장이 어느 기준가를 썼는지 공표되지 않는다).

### `consensus.*` — S17 판정 (2026-09-06, `consensus_daily`·`v_consensus`)

`consensus_daily` 는 (`ticker`, `obs_month`, `target_period`, `metric`, `src`) 격자에 **관측점별 최초 관측**을 싣는다. 어댑터는 `v_consensus(as_of)` 를 부르고, 그 뷰는 `available_date ≤ cutoff` 로만 자른 뒤 겹치는 달의 wise·v3 2행을 먼저 알 수 있던 한 행으로 접는다(DESIGN §5).

| field_id | `metric` | 단위 | 판정(S17 뒤) |
|---|---|---|---|
| `consensus.forward_eps` | `eps` | **원** | 부분 — `target_period` 별 값. 12M forward 합성은 팩터층 |
| `consensus.forward_sales` | `revenue` | **억원** | 부분 — 동일. 원 단위가 아니다(스케일 변환은 소비자 몫) |
| `consensus.eps_dispersion` | `eps` 의 `est_min`·`est_max` | 원 | 부분 — **wise 구간에만 있다**. v3 행은 min/max NULL(원장이 안 준다) → 겹치는 달에 `v_consensus` 가 v3 를 고르면 dispersion 은 결측이다 |
| `consensus.target_price`·`consensus.recommendation`·`consensus.analyst_count` | — | — | S18 `opinion_daily` 축(이 테이블에 `n_analyst` 는 없다) |

- **`target_period` 어휘 = `YYYYMM`** (예 `202612` = 2026 회계연도 12월 결산). WISE 계열 정본 표기이고 v3 의 `YYYY/MM` 은 슬래시를 지워 맞춘다(DESIGN §4-6). 소비자는 이 문자열을 그대로 받고, "12개월 선행" 같은 합성 축은 팩터층이 `obs_month` 와 `target_period` 로 만든다.
- **단위는 field_id 별 상수가 아니라 행의 `unit` 컬럼**이다 — wise 는 stage `unit`(데이터 값), v3 는 카탈로그 지식(revenue·op·ni 억원 · eps·bps 원 · per·pbr 배 · roe_pct %). 어댑터가 스케일을 바꾸려면 `unit` 을 읽어야 한다. 절단본 교차검증: 005930 2026-08-03 v3 revenue 7,378,931 = wise 2026-07-31 revenue 7,378,930.54 (같은 억원 축).
- `metric` 어휘 9(revenue·op·ni·eps·bps·per·pbr·roe_pct·parse_failed) 중 위 표 밖의 여섯(op·ni·bps·per·pbr·roe_pct)은 레지스트리 field_id 가 없다 — v3 가 주므로 싣되 `list_fields()` 에는 올리지 않는다(FIELD_MAP 42 는 그대로). `parse_failed` 는 파서가 값 칸을 못 읽은 관측 자리표시자다(값 NULL, 격리하지 않는다 — 격리하면 커버율이 조용히 부푼다).
- **S18(09-06) 판정 — `consensus.target_price`·`consensus.recommendation`·`consensus.analyst_count` 는 `opinion_daily` 로 실재하되 `부분` 유지.** 셋 다 grain 이 (`ticker`, `obs_date`, **`src`**) 이라 어댑터는 **`src` 를 골라야 한다** — `wise`(`stg_analyst_summary`, `available_basis='measured'`, 2026-09-01~ 2일)와 `v3`(`stg_v3_analyst_opinions`, `available_basis='default'` + `coverage_degraded=true`, 2026-04-04~09-02)가 같은 (ticker, obs_date) 에 공존한다. 겹친 구간의 값은 절단본 5키에서 5축 전부 일치했다(DESIGN §10 P37 ①). PIT 를 엄격히 보려면 `coverage_degraded=false` (= `src='wise'`)만 쓰고, 이력이 필요하면 v3 를 쓰되 `available_date` 가 잰 수집일이 아님을 받아들여야 한다 — 이 선택은 팩터층 몫이고 equity 는 두 축을 다 준다. 커버는 보통주에만 있고(WISE 804 / v3 810, 우선주·ETF·외국주 0) 시총 분위별 커버율은 S19 `dataset_profile.coverage_by_mktcap_quintile` 이 낸다.
- **`opinion_broker_daily` 는 field_id 가 아니다** — 제공처별 원문 의견·목표주가는 레지스트리 42 필드에 대응이 없다. 어댑터는 노출하지 않고, 목표가 리비전 분산 같은 팩터를 뒤에 만들 때 읽는 내부 축이다. 읽을 때 **`change_pct` 는 기간이 없는 값** 임에 주의한다 — 직전 의견의 날짜를 WISE 가 주지 않아 `prev_opinion_date` 를 우리 관측 이력에서 되찾고, 못 찾으면 NULL 이다(FX-5-008, 절단본 249행 전부 NULL).
- **S09 `short_daily`(09-06) 실제 컬럼명** — `short.*` 3필드가 읽는 컬럼은 **원천별 접미사**를 갖는다(사용자 확정 "합치지 않는다", DESIGN §4-3): 공매도 키움 `short_volume_kiwoom_shr`·`short_value_kiwoom_krw`·`short_weight_kiwoom_pct`·`short_avg_price_kiwoom_raw`(+`_basis`) / 공매도 KIS `short_volume_kis_shr`·`short_value_kis_krw`·`short_volume_ratio_kis_pct`·`short_value_ratio_kis_pct`·`short_avg_price_kis_krw` / 대차 KIS `lending_new_kis_shr`·`lending_redeem_kis_shr`·`lending_balance_kis_shr`·`lending_balance_kis_krw`. 결측 3분류는 원천마다 하나씩(`fill_kind_short_kiwoom`·`fill_kind_short_kis`·`fill_kind_loan_kis`) — §1 「결측 어휘」의 `fill_kind.kind` → `CellKind` 대응은 그대로이되 **어댑터가 어느 원천을 읽는지에 따라 셀 종류가 갈린다**(같은 (ticker, date) 에서 키움은 `measured`, KIS 는 `not_collected` 인 셀이 절단본에 33,081행). `short_balance_ratio` 의 분모 `shares_out` 은 `price_daily` 에 있다 — `short_daily` 는 주식수를 재수록하지 않는다.
- `check_field_map.py` 가 보는 것은 **field_id 집합**이라 위 컬럼명 변경은 CI 대상이 아니다(registry 39 · map 42 · missing 0, 09-06 재확인). 어댑터가 이 컬럼들을 실제로 노출하는 시점은 S19 `dataset_profile` 이 랙(`recommended_lag_sessions`)을 확정한 뒤다 — stage 세 원천 모두 `lag_known=false` 이므로 랙 0 을 가정하면 안 된다.

### S21 본판 어댑터의 소비 규약 (2026-09-06)

어댑터는 **필드별 분기 없이 선언표를 해석**한다(`_specs.SOURCE_SPECS` × `FIELD_SPECS`). 읽는 방식은 둘뿐이고, grain 이 (security, session) 보다 굵은 원천은 표에 적힌 규칙으로 줄인다.

| 원천 | 축 | 읽는 방식 | grain 축소 규칙 | field_id |
|---|---|---|---|---|
| `price_daily` | ticker | GRID (ticker, date) | 없음 | `price.close`·`open`·`volume`·`market_cap`·`shares_outstanding`·`trading_value` |
| `v_adj_price_fwd` | ticker | GRID | 없음 | `price.adj_close`(내부 스코프) |
| `v_fin_latest` | **corp** | LATEST(available_date) | 판본·`fs_div` 는 뷰가 접고, 같은 접수일의 여러 기간은 `period_end DESC, report_code DESC` | `financial.*` 8 |
| `v_consensus`(metric=eps / revenue) | ticker | LATEST | 관측 달 이후로 끝나는 `target_period` 중 가장 가까운 것(**FY1**), 동률 `obs_month DESC` | `consensus.forward_eps`·`forward_sales`·`eps_dispersion` |
| `opinion_daily` | ticker | LATEST | 잰 판본 우선(`coverage_degraded=false` = wise), 동률 `src` 사전순 | `consensus.target_price`·`recommendation`·`analyst_count` |
| `dividend_event` | **corp** | LATEST | 종류(`stock_knd`) 축을 접는다 — 값 있는 행 우선 → 최신 `bsns_year`·`reprt_code` → `stock_knd` 사전순(한글 정렬상 '보통주' 가 '우선주' 앞) | `event.dividend_per_share` |
| `corp_event`(`event_type='tsstk_aq'`) | ticker | LATEST | 같은 공시일의 여러 건은 `sum(amount_krw)` | `event.buyback_amount` |
| `holder_daily`(`src='elestock'`) | **corp** | LATEST | 같은 접수일의 보고자는 `sum(qty_change_shr)`(전부 NULL 이면 NULL) | `event.insider_net_buy` |

- **GRID** 는 랙 n 을 "정확히 n 세션 전 행" 으로 읽고 그 세션에 원천 행이 없으면 셀을 내지 않는다 — 재상장 구간 첫날이 직전 구간 값을 물지 않는다(합성 금지). **LATEST** 는 컷오프(as_of 에서 n 세션 전 거래일) 이하의 마지막 관측이고 `available_date` 는 **그 관측의 공개일**이라 as_of 보다 몇 달 앞일 수 있다(계약은 `available_date ≤ as_of` 만 요구한다). 관측이 하나도 없으면 셀이 없고, 값이 NULL 이면 셀은 나가되 `CellKind.MISSING` 이다(`load_panel` 과 같은 규칙).
- **corp 축 3원천은 `corp_ticker` 로 전개하고 한 법인의 종류주 티커 전부가 같은 값을 받는다.** 재무·임원지분은 법인 사실이라 옳지만 **배당은 종류마다 다르다** — 우선주 티커가 보통주 DPS 를 받는 것이 `event.dividend_per_share` 부분 판정의 근거 ②이고, 종류별 DPS 가 필요하면 소비자가 `dividend_event` 를 직접 읽어야 한다. `corp_ticker` 는 시점축 없는 현재 스냅샷이라 폐지·티커 재사용 구간의 대응은 DESIGN §4-5 6 의 한계를 그대로 물려받는다.
- **랙은 전부 0 세션**이고 근거는 원천마다 `SourceSpec.lag_basis` 에 적혀 `DatasetFieldProfile.available_date_basis` 로 나간다 — 가격류는 `available_date = date`, DART 축은 `available_date = rcept_dt`(접수일 자체가 공개일이라 세션 랙을 더하면 이중 계산), 컨센서스·의견은 stage 가 잰 수집일이다. `dataset_profile`(S19)이 오면 프로필 값으로 갈아 끼운다.
- **`event.*` 3 은 최근 관측이 그대로 이어진다** — 창·감쇠·이벤트 스터디는 팩터층 몫이고, 언제 공시된 값인지는 `available_date` 가 말한다.
- **단위는 field_id 별 상수가 아닐 수 있다** — `consensus.forward_sales` 는 억원이고(`unit` 컬럼이 정본) `consensus.eps_dispersion` 은 표준편차가 아니라 `est_max − est_min` 범위다.

## 4. 유지 규약

- 레지스트리(`backend/FACTORS.md`)가 바뀌면 이 표를 같은 PR 에서 갱신한다. `check_field_map.py` 는 레지스트리 필드 집합 − 표 필드 집합 = ∅ 를 검사한다.
- 판정 변경은 근거(슬라이스·실측 절)를 남긴다.
