# Equity 층 워크플로우 개정 제안서 v1.2 (2026-09-05)

> 대상: `EQUITY_WORKFLOW.md` v1.1 · `EQUITY_DESIGN.md` v1.1 (둘 다 2026-09-03, 0단계 산출).
> 판정 기준: 사용자의 최종 목적 — **"팩터 만들 데이터(54개 재료)가 충분하고, 룰(PIT·정본·결측)이 잘 적용되고, 백테스팅에 적절히 구성된 equity DB"**.
> 전제(사용자 승인): 결정 5개 확정 · stage 66테이블(원장 62 + 문서층 4) 서버 빌드 완료 · equity 코드 0.
> 근거 표기: 파일§절 또는 `파일:라인`. 문서에 없는 수치는 "미측정"으로 적고 추정하지 않는다.

---

## 0. 한 줄 결론

설계(v1.1)는 **stage→equity 구조 정책 층으로서는 건전**하고 26테이블·뷰 7종·게이트 EG0~EG9+EG-C 는 목적을 담기에 충분한 골격이다. 그러나 **목적 도달을 막는 결함이 세 가지** 있다.

1. **소비자 계약이 틀렸다.** v1.1 결정 5 는 "엔진 포트 4종(`ports/`)뿐, 재무·컨센서스는 팩터층이 엔진 밖에서 주입"이라고 결론지었는데(`EQUITY_WORKFLOW.md` §0-1), 실제 소비자인 `backend/src/strategy_workbench` 에는 이미 **재무·컨센서스·수급·신용·이벤트·섹터 필드를 요구하는 포트 4종**(`EquityDataPort`·`RawObservationPort`·`FactorObservationPort`+`FactorMetadataPort`·`BacktestDataPort`)이 존재하고, `MockEquityDataAdapter` 한 클래스가 이 넷을 전부 구현하며, 로드맵 M9 가 `equity_duckdb` 로의 교체를 대기 중이다(`docs/superpowers/specs/2026-09-03-strategy-workbench-roadmap.md` §5·§8·M9). equity 문서 어디에도 이 이름들이 없다(grep 0건).
2. **"54개 재료가 충분한가"를 판정하는 산출·게이트가 없다.** EG-C ⑥ 이 팩터 4개(V01·F01·M01·G05)만 손계산으로 검증한다(`EQUITY_WORKFLOW.md` §2). 54 중 7%다.
3. **1단계가 2단계 산출에 의존한다.** `universe_daily` 가 `mktcap_krw`·`adv20_krw`·`no_trade_run` 을 담으려고 `stg_price_daily` 를 직접 읽는다(`EQUITY_DESIGN.md` §4-1). `price_daily` 정본은 2단계다(§4-2). "1단계가 유일한 직렬 병목"(`EQUITY_WORKFLOW.md` §4)이라는 전제가 성립하지 않는다.

아래 §1 에서 54 팩터 + 백테스트 요구를 전수 추적하고(갭 21건), §2 에서 단계별로 비판하고, §3 에서 23슬라이스 v1.2 를 제시한다.

---

## 1. 목적 추적 매트릭스

### 1-0. 읽는 법

- **산출**: equity 테이블.컬럼 또는 뷰. 없으면 "—".
- **단계**: v1.1 기준 단계 번호 / v1.2 슬라이스 ID(§3).
- **게이트**: 그 재료를 지키는 게이트(`EQUITY_WORKFLOW.md` §2).
- **시작일**: 그 팩터를 처음 계산할 수 있는 날. 재무는 `available_date = rcept_dt` 라 회계연도가 아니라 **접수일** 기준이다.
- **판정**: `즉시`(설계대로 만들면 나옴) / `조건부`(명시된 조건 충족 필요) / `불가`.

캘린더 하한이 **2010-01-04**(`EQUITY_DESIGN.md` §4-1 `trading_calendar`, `stg_index_daily` distinct date 4,094)이고 그 이전 원장 행은 `_reject/pre_calendar` 로 격리된다(§4-3, P11: short 58,211행 2008-06-23~ · credit 24,497행 2007-07-16~). 따라서 **`FACTORS.md` §0 의 "2008 ─── 공매도", "F09 신용 2010(원장 2007-07~)" 은 equity 산출에서는 전부 2010-01-04 로 잘린다.**

### 1-1. 밸류 V (7)

| ID | 팩터 | 산출 (테이블.컬럼 / 뷰) | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| V01 | PBR | `price_daily.mktcap_krw` · `v_firm_mktcap` · `fin_std.total_equity` / `v_fin_latest` | 2·4 → S04·S12 | EG2·EG3(`v_firm_mktcap`=Σ종류주)·EG4 | FY2015 첫 접수(4단계 실측 확정) | 즉시 |
| V02 | PER | + `fin_std.net_income`·`ttm_net_income` | 4 → S12 | EG2·EG6 | 동일. TTM 은 3개월 4개 필요 → FY2016 | 즉시 |
| V03 | PSR | + `fin_std.revenue`·**`revenue_basis`** | 4 → S12 | EG2 | 동일 | **조건부** — 금융업 470사 합산식 미확정(`FACTORS.md` §11-7 "은행 영업수익 합산식", §8 "아직 확정 못 한다"). `EQUITY_DESIGN.md` §4-4 는 `REVENUE_FALLBACK` 계승만 적고 `banking_gross`/`insurance_gross` 규칙이 없다 → **GAP-01** |
| V04 | PCR | + `fin_std.cf_operating_ytd` | 4 → S12 | EG2·EG7 | 동일 | 즉시 |
| V05 | EV/EBITDA | + **`depreciation`·`borrowings`**·`cash` | 4 → S12 | EG2 | 동일 | **조건부** — 계정 2종이 `fin_map.py` 21항목 밖. `stg_fin.account_id` 어휘 실재 확인 후 결정, 없으면 미착수(`EQUITY_DESIGN.md` §6) → **GAP-02** |
| V06 | 배당수익률 | `dividend_event.dps_krw` · `price_daily.close` | 4B·2 → S16·S04 | EG2·EG6 | `stg_dividend` FY2013~2015 | 즉시 (단 `dividend_event` 에 기준일·배당락일 없음 → 값을 붙일 날짜는 `rcept_dt` 뿐, §4-5) |
| V07 | 순현금비율 | `fin_std.cash`·**`borrowings`** | 4 → S12 | EG2 | 동일 | **조건부** — GAP-02 |

### 1-2. 퀄리티 Q (8)

| ID | 팩터 | 산출 | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| Q01 | ROE | `fin_std.net_income`·`total_equity` | 4 → S12 | EG2·EG6 | FY2015 접수 (평균자본 쓰면 FY2016) | 즉시 |
| Q02 | ROA | + `total_asset` | 4 → S12 | 동일 | FY2015 | 즉시 |
| Q03 | 영업이익률 | `op_profit`/`revenue`·`revenue_basis` | 4 → S12 | 동일 | FY2015 | **조건부** — GAP-01 |
| Q04 | 발생액 | `net_income`·`cf_operating_ytd`·`total_asset` | 4 → S12 | EG7(기간 정합) | FY2015 | 즉시 |
| Q05 | FCF 수익률 | `cf_operating_ytd`·`capex_ytd`·`mktcap_krw` | 4·2 → S12·S04 | 동일 | FY2015 | 즉시 |
| Q06 | 부채비율 | `total_liab`·`total_equity` | 4 → S12 | EG2 | FY2015 | 즉시 |
| Q07 | 이자보상배율 | `op_profit`·**`interest_expense`** | 4 → S12 | EG2 | FY2015 | **조건부** — GAP-02 |
| Q08 | NOA 비율 | `total_asset`·`cash`·`total_liab`·**`borrowings`** | 4 → S12 | EG2 | FY2015 | **조건부** — GAP-02 |

### 1-3. 성장 G (10)

| ID | 팩터 | 산출 | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| G01 | 매출성장률 | `fin_std.revenue` 전기 대비 | 4 → S12 | EG5(c) as-of 불변 | FY2016 접수 | **조건부** — GAP-01(금융업) |
| G02 | 영업이익성장률 | `op_profit` | 4 → S12 | 동일 | FY2016 | **조건부** — GAP-01 |
| G03 | 자산성장률 | `total_asset` | 4 → S12 | 동일 | FY2016 | 즉시 |
| G04 | EPS 성장률 | `eps_basic` | 4 → S12 | 동일 | FY2016 | 즉시. `FACTORS.md` §11-4 의 "EPS 소스 이원화(DART vs 카엘)"는 equity 가 카엘 EPS 를 싣지 않으므로 **해소** — 문서 정정 대상 |
| G05 | 영업이익 추정 리비전 | `consensus_daily(metric='op')` `obs_month` × `target_period` | 5 → S17 | EG6(max 선택 0)·EG-C ⑨ | v3 2026-04-03~ → 월 그레인 2관측 = **2026-05** | **조건부** — 표본 5개월. `FACTORS.md` §3 "2027년 이후" |
| G06 | 순이익 추정 리비전 | `metric='ni'` | 5 → S17 | 동일 | 동일 | **조건부** |
| G07 | 매출 추정 리비전 | `metric='revenue'` | 5 → S17 | 동일 | 동일 | **조건부** |
| G08 | 투자의견 리비전 | `opinion_daily.opinion_score`(v3 254,925행) | 5 → S18 | EG6·EG9 | v3 opinions 구간(5단계 실측) | **조건부** — `stg_v3_revision_compare.opinion_*` 는 전행 NULL(`STAGE_HANDOFF.md` §3) |
| G09 | 목표주가 리비전 | `opinion_daily.target_price_krw` · `opinion_broker_daily` | 5 → S18 | EG9 | WISE fetched_date **2일**(summary)·**3일**(broker) (P5) | **조건부** — 판본 2~3일뿐이라 지금은 리비전 계산 불가. 축적 대기 |
| G10 | 커버리지 수 | `opinion_daily.analyst_count` | 5 → S18 | EG9 | 동일 | **조건부** — 수준값은 즉시, 변화분은 축적 대기 |

### 1-4. 인컴·주주환원 I (5)

| ID | 팩터 | 산출 | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| I01 | 배당성향 | `dividend_event.cash_total_krw` · `fin_std.net_income` | 4B·4 → S16·S12 | EG2·EG3(PK) | FY2015 | 즉시 |
| I02 | 배당성장률 | `dividend_event.dps_krw` 전기 | 4B → S16 | 동일 | FY2016 | 즉시 |
| I03 | 자사주 순매입률 | `treasury_stock`(취득−처분) · `mktcap_krw` | 4B·2 → S16·S04 | EG1(`row_kind<>'aggregate'`) | FY2015 | 즉시 |
| I04 | 주주환원율 | I01 + I03 | 4B → S16 | 동일 | FY2015 | 즉시 |
| I05 | 자사주 소각률 | `treasury_stock` 소각 · `shares_outstanding` | 4B → S16 | 동일 | FY2015 | 즉시 |

### 1-5. 수급 F (9)

| ID | 팩터 | 산출 | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| F01 | 외국인 순매수 강도 | `flow_daily.frgnr_invsr_krw` / `price_daily.value_krw` | 3·2 → S08·S04 | EG9(커버율·evidence_rate) | 2010-01-04 | 즉시 |
| F02 | 외국인 보유비중 변화 | `flow_daily.foreign_wght_pct` | 3 → S08 | EG9 | 2010-01-04 | 즉시 |
| F03 | 기관 순매수 강도 | `flow_daily` 13주체 중 조합 | 3 → S08 | EG3(12주체 합=0) | 2010-01-04 | **조건부** — "12분류를 어떻게 묶을지" 미결(`FACTORS.md` §11-2). `orgn`(기관계)은 합계 컬럼이라 항등식에서 제외됨(`EQUITY_DESIGN.md` §4-3) → **GAP-03** |
| F04 | 연기금 순매수 | `flow_daily.penfnd_etc_krw` | 3 → S08 | EG9 | 2010-01-04 | 즉시 |
| F05 | 공매도 잔고비율 | `short_daily.short_volume_shr` / `price_daily.shares_out` | 3·2 → S09·S04 | EG9 | 2010-01-04 (원장 2008-06-23, 격리) | 즉시 — 다만 이름과 달리 **거래량 기반**이다. 진짜 공매도 잔고는 취득 불가(`FACTORS.md` §12 F45) → 명명 정정 대상 |
| F06 | 공매도 거래비중 | `short_daily.short_value_krw` / `value_krw` | 3 → S09 | EG7 | 2010-01-04 | 즉시 |
| F07 | 대차잔고 비율 | `short_daily.lending_balance_kis_shr` / `lending_balance_kiwoom_raw` | 3 → S09 | EG7·EG8 | 키움 2011-07-25 | **조건부** — 키움 `rmnd` **단위 미측정**(`STAGE_HANDOFF.md` §4), KIS 축은 유닛 `loan` **287 티커**만 커버(P11) → **GAP-04** |
| F08 | 외국인 한도소진율 | `flow_daily.limit_exh_rt_pct` | 3 → S08 | EG2(profile 행) | 2010-01-04 | 즉시 (단위 미측정 → `dataset_profile`) |
| F09 | 신용잔고율 | `credit_daily.whol_loan_rmnd_stcn_shr` / `shares_out` | 3·2 → S10·S04 | EG2·EG9 | **2010-01-04** (원장 2007-07-16, 24,497행 격리) | 즉시 — `FACTORS.md` §5 의 "2010(원장 2007-07~)" 표기는 유효하나 §0 인벤토리 정정 필요 |

### 1-6. 모멘텀 M (3) · 위험 R (4)

| ID | 팩터 | 산출 | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| M01 | 1/3/6/12M 수익률 | `v_adj_price(asof)` | 2 → S06 | EG8(분할일 점프)·EG-C ④ | 2011-01(2010-01-04 + 252거래일) | 즉시 |
| M02 | 52주 신고가 근접도 | `v_adj_price` high | 2 → S06 | EG7 | 2011-01 | **조건부** — `price_daily` 는 O/H/L '0' → NULL 유지(`STAGE_HANDOFF.md` §3). high NULL 행 수 미측정 → close 기반으로 정의하지 않으면 결측 다발 → **GAP-14 파생** |
| M03 | 거래량 급증 | `v_adj_volume(asof)` | 2 → S06 | EG8(조정 거래량 점프) | 2010-03(20거래일) | 즉시 |
| R01 | 변동성 | `v_adj_price` 일간수익률 | 2 → S06 | EG8 | 창 길이 의존 | 즉시 |
| R02 | 최대낙폭 | `v_adj_price` | 2 → S06 | EG8 | 동일 | 즉시 |
| R03 | 유동성(회전율) | `price_daily.value_krw` / `mktcap_krw` | 2 → S04 | EG7 | 2010-01-04 | 즉시 |
| R04 | 시장 베타 | `index_daily`(`코스피`·`코스닥`) · `v_adj_price` | 1·2 → S02·S06 | EG1(=347,821)·EG2 | 2011-01 | 즉시 |

### 1-7. 이벤트·지분 E (8)

| ID | 팩터 | 산출 | 단계 → 슬라이스 | 게이트 | 시작일 | 판정 |
|---|---|---|---|---|---|---|
| E01 | 내부자 순매수 | `holder_daily`(elestock 32,668, 지분 전/후 **비율**) | 4B → S15 | EG2·EG6 | **2024-08** (롤링 2년) | 즉시 |
| E02 | 5% 대량보유 변동 | `holder_daily`(majorstock 22,209) | 4B → S15 | 동일 | 2024-10 | 즉시 |
| E03 | 최대주주 지분율 | `ownership_snapshot`(`stg_hyslr` 228,226 비집계) | 4B → S15 | EG1(비집계) | FY2015 | 즉시 |
| E04 | 실질 유통비율 | `ownership_snapshot` + `treasury_stock` + `shares_outstanding` | 4B → S15·S16 | EG1·EG3 | FY2015 | 즉시 |
| E05 | 자사주 취득 발표 | `corp_event`(`stg_event_tsstk_aq` 1,951) | 2 → S05 | EG2(announce 축) | 2015 | 즉시 |
| E06 | 유상증자·CB 발행 | `corp_event`(`piic` 5,538 · `cvbd_is` 5,386) | 2 → S05 | 동일 | 2015 | 즉시 |
| E07 | 상폐 위험 | `corp_event`(`df_ocr` 47·`ds_rs_ocr` 134·`ctrcvs_bgrq` 168·`bnk_mngt_pcbg` 15) + `universe_daily.signal_delist` 3,952·`signal_admin` 1,218·`signal_liquidation` 349·`admin_state` | 1·2 → S03·S05 | EG3(halt 열린 구간 0)·정리매매 recall | 2010-01-04 | **조건부** — ① KOSPI 관리종목 **해제 공시 3건뿐** → 상태 종료 불가, 편향 KOSDAQ 비대칭(`EQUITY_DESIGN.md` §11) → **GAP-06** ② `FACTORS.md` §7 의 파생 팩터 "공시 지연일수 = rcept_dt − (fiscal_end + 법정기한 90/45일)" 을 담을 산출이 26테이블에 없다 → **GAP-05** |
| E08 | 비적정 감사의견 | `audit_opinion.adt_opinion_class`(93,037) | 4B → S15 | EG1(1:1) | FY2015 | 즉시 |

### 1-8. 판정 집계

| 판정 | 수 | 팩터 |
|---|---:|---|
| 즉시 | **36** | V01·V02·V04·V06 · Q01·Q02·Q04·Q05·Q06 · G03·G04 · I01~I05 · F01·F02·F04·F05·F06·F08·F09 · M01·M03 · R01·R02·R03·R04 · E01~E06·E08 |
| 조건부 | **18** | V03·V05·V07 · Q03·Q07·Q08 · G01·G02·G05~G10 · F03·F07 · M02 · E07 |
| 불가 | **0** | — |

**조건부 18 의 원인은 5개뿐이다**: GAP-01 금융업 revenue(5개) · GAP-02 계정 3종(4개) · 컨센서스 축적(6개) · GAP-03/04 정의·단위(2개) · GAP-05/06/14(2개). 설계를 바꿀 필요는 없고 **조건을 게이트로 승격**하면 된다(§3 S12·S17·S20).

### 1-9. 백테스트 요구 → 산출 → 게이트

| 요구 | equity 산출 | 단계 → 슬라이스 | 게이트 | 판정 |
|---|---|---|---|---|
| **유니버스 PIT** | `security_span` → `Membership` · `universe_daily.status` · `universe_policy` | 1 → S01~S03 | EG1·EG3(span 비중첩)·**폐지 909 span 종료**·EG-C ②③⑩ | 즉시 |
| **수정가격** | `price_daily`(원주가) + `adj_factor`(price·share 두 축) + `v_cum_adj`/`v_adj_price` | 2 → S04·S06 | EG3(시총 불변 `price×share=1`)·EG8(분할일 점프·`krx_kis_close_ratio`·recall)·EG-C ④ | 즉시. **PR 수익률만** — 배당락일 원천 없음(`EQUITY_WORKFLOW.md` §6, CA-05) |
| **체결가** | `price_daily.open` | 2 → S04 | EG7 | **조건부** — 엔진 시장가 체결은 `bar.open`(`backend/src/backtest_engine/engine/pricing.py:31`), `Bar.open: float` 은 필수·>0·OHLC 불변식 강제(`types/market.py:26-56`). equity 는 O/H/L NULL 유지 정책(§4-2)이고 **`open` NULL ∧ `volume>0` 행 수가 미측정** → **GAP-14** |
| **캘린더** | `trading_calendar`(4,094일 + gap 축) | 1 → S02 | EG1(=4,094+gap) | 즉시. 엔진 커널은 캘린더 포트가 없고 세션 축 = Bar 합집합(`data/feed.py`), 워크벤치 파이프라인은 `RawObservationSet.sessions` 로 어댑터가 선언(`portfolio_design/ports/outgoing/raw_observations.py`) → **어댑터가 캘린더 소유자**임을 명시해야 함 |
| **월간 리밸런싱** | — | — | — | 즉시. v1.1 §0-1 의 "`MonthEndSession` 미구현" 은 **소비 경로와 무관**하다: 리밸런싱은 `domain/portfolio/_compiler.py:89-111` `_rebalance_pairs` 가 (signal=세션 i, execution=세션 i+1) 쌍으로 컴파일한다 → 문서 정정 대상 |
| **기업행위** | `corp_event`(13유형) + `adj_factor` | 2 → S05·S06 | EG2(announce 축)·EG8(탐지 recall) | **조건부** — 엔진 `CorporateActionType` 3종(SPLIT·REVERSE_SPLIT·SHARE_COUNT_CHANGE)에 13유형을 접으면 rights·spinoff·merger·capred 가 전부 알림용 SHARE_COUNT_CHANGE 로 뭉개진다(`EQUITY_DESIGN.md` §7) → 정보 손실은 설계가 인정한 것이나, **손실 목록을 `_meta` 에 건수로 남기는 규칙이 없다** |
| **벤치마크** | `index_daily.close_pt` | 1 → S02 | EG1 | **조건부** — 엔진 `BacktestDataQuery.benchmark_security_id: str \| None` 은 **security_id** 를 요구하는데 지수는 `security` 가 아니다. registry 의 `benchmark.close`(R04·price.beta_252d)도 같은 문제 → **GAP-09** |
| **거래비용 규칙표** | **없음(범위 밖)** | — | — | **갭** — 엔진은 `RunConfig.fee_bps` 평면 bps + `SlippageModel` 포트만 갖는다(`types/results.py:25`, `ports/execution.py`). 거래세·호가단위·상하한가(±30%)·**공매도 금지 구간(2011-07 이후 34.6%, SH-03)** 의 소유자가 어느 층에도 없다. `EQUITY_WORKFLOW.md` §6 은 "팩터층 비용·제약 모형 몫" 이라 넘겼고 팩터층 문서는 없다 → **GAP-15** |
| **섹터/중립화** | — | — | — | **갭** — mock 은 `classification.sector`(dataset `classification_pit`)를 내고 `RawObservation.sector_id` 가 중립화 축이다. equity 는 업종 과거 PIT 가 없다(`EQUITY_DESIGN.md` §11: `induty_code` 현재값, WICS 보류) → **GAP-07** |
| **이벤트 필드** | — | — | — | **갭** — mock dataset `event_pit`(`event.earnings_surprise`). equity 26테이블에 `event_pit` 대응이 없고 `corp_event` 는 자본변동 축이다 → **GAP-08** |
| **재현성** | `MANIFEST.inputs` · `_pinned/` · `content_hash` | 전 단계 | EG0·EG5(a)(c) | 즉시 (§2-7 비판 참조) |

### 1-10. 갭 목록 (21건)

각 갭에 대해 **어느 항목(재료/게이트/순서)이 없는가 → 해소안 → 어느 슬라이스에서** 형식.

| ID | 없는 것 | 무엇을 막는가 | 해소안 | 슬라이스 |
|---|---|---|---|---|
| **GAP-01** | 재료·규칙 — 금융업 `revenue_basis` 합산식 | V03·Q03·G01·G02 (470사) | `EQUITY_DESIGN.md` §4-4 에 `revenue_basis ∈ {standard, banking_gross, insurance_gross, consensus, unavailable}` 어휘와 각 산식을 명시. 은행 합산식 미확정이면 **`unavailable` 로 두고 게이트에 "financial 470사 중 `revenue_basis='unavailable'` 비율 = baseline" 기록형** 추가. 추정 합산 금지 | S12 |
| **GAP-02** | 재료 — `depreciation`·`borrowings`·`interest_expense` | V05·V07·Q07·Q08 | S12 **착수 첫 작업**으로 `stg_fin.account_id` 어휘를 실측(콜 0)해 3계정 실재 판정. 없으면 4팩터를 `factor_readiness.status='blocked'` 로 등재하고 `fin_std` 컬럼도 만들지 않는다 | S12 |
| **GAP-03** | 규칙 — 기관 12분류 묶음 정의 | F03 | equity 는 13주체 컬럼을 전부 실어 두므로 **재료는 충분**. 정의는 팩터층이 소유한다고 `factor_readiness` 에 명시(`status='ready'`, `owner='factor_layer'`). `FACTORS.md` §11-2 를 "재료 확보, 정의는 팩터층" 으로 정정 | S08·S20 |
| **GAP-04** | 재료 — 대차잔고 단위·커버리지 | F07 | S09 에서 ① KIS `rmnd_stcn_shr`(주식수, 287 티커) 는 그대로 ② 키움 `rmnd` 는 **단위 미측정 컬럼으로 분리 보존**(`lending_balance_kiwoom_raw`, 이미 설계됨) ③ 두 축 겹침 구간에서 `KIS/키움` 비율 분포를 `_meta` 에 기록형으로 남겨 단위 후보 추정 근거 확보. 확정 전에는 `dataset_profile.evidence='unit_unmeasured'` | S09·S19 |
| **GAP-05** | 산출 — 공시 지연일수 | E07 선행 신호 | `disclosure_version` 에 `legal_deadline`·`delay_days`(= `rcept_dt` − (`period_end` + 90/45일)) 2컬럼 추가. 재료(`rcept_dt`·`period_end`·`reprt_code`)가 이미 그 테이블에 있어 조인 0 | S11 |
| **GAP-06** | 규칙 — KOSPI 관리종목 상태 종료 | E07·유니버스 정책 | 해소 불가(원천 부재). **완화**: `admin_state_basis='derived_kospi_window'` 를 필수 컬럼으로 두고, `factor_readiness` 의 E07 행에 "시장 비대칭" 을 `caveat` 로 싣는다. 게이트: 시장별 `admin_state` 지속일 중앙값 차이를 `_meta` 기록형 | S03·S20 |
| **GAP-07** | 산출 — 섹터 PIT | 중립화·`classification.sector` | 3안 비교 후 택1: (a) `sector_pit` 를 만들지 않고 어댑터가 `classification.sector` 를 **unavailable 로 선언**(로드맵 §8-5 "mock fallback 금지" 준수) (b) `corp.induty_code`(현재값)를 `basis='current_snapshot'` 라벨과 함께 노출하고 중립화 사용 시 경고 (c) WICS 수집 재개(분기 1,716콜, 라이선스 미결). **권고 = (a)+(b) 병행**: 필드는 내되 `DatasetFieldProfile.point_in_time=False`·`requires_confirmation=True` | S21 |
| **GAP-08** | 산출 — `event_pit` 데이터셋 | mock `event.*` 6필드 | 6필드를 개별 판정: `dividend_per_share`→`dividend_event`(가능) · `buyback_amount`→`corp_event.amount_krw`(가능) · `insider_net_buy`→`holder_daily`(2024-08~) · `index_membership_change`·`earnings_surprise`·`disclosure_sentiment` → **unavailable 선언**(`FACTORS.md` §9). 대응표를 `field_map.md` 에 고정 | S00·S21 |
| **GAP-09** | 표현 — 벤치마크 | R04 · `benchmark.close` · `benchmark_security_id` | `security` 에 `sec_type='index'` 행을 넣지 **않고**, 어댑터가 `index_daily` 를 `security_id='idx:KOSPI'` 같은 예약 네임스페이스로 매핑. `field_map.md` 에 예약 접두 규칙 명시. 대안(=`security` 에 지수 편입)은 EG1 등식(=distinct ticker) 을 깨뜨리므로 기각 | S00·S21 |
| **GAP-10** | 계약 — 워크벤치 4포트 미배선 | 백테스트 실행 자체 | §7 참조. 결정 5 를 v1.2 에서 **재기술**하고 S07·S21 을 신설 | S00·S07·S21 |
| **GAP-11** | 어휘 — `security_id` 축 | 모든 포트 | 엔진은 `security_id`(mock: `"sec-005930-1"`), equity 키는 `ticker`+`span_seq`. **`security_id = f"{ticker}:{span_seq}"`** 로 고정하고 `field_map.md` 에 기재. 재상장 2종(036220·101970)이 서로 다른 `security_id` 를 갖는 것이 생존편향 방지의 요점 | S00·S01 |
| **GAP-12** | 어휘 — `universe_id` | `RawObservationQuery.universe_id` | 계약 테스트가 `"krx.common-stock"` 을 쓴다(`backend/tests/contract/test_raw_observation_port.py:32`). equity `universe_policy.policy ∈ {all, investable, liquid}` 와 다른 축(시장·증권종류 vs 유동성 정책). **`universe_id = "<market>.<sec_type_group>[.<policy>]"`** 문법을 `universe_policy` 에 컬럼으로 등재 | S03·S21 |
| **GAP-13** | 문서 — field_id ↔ equity 컬럼 대응표 | 어댑터 구현 전부 | `workspace/dongmin/docs/EQUITY_FIELD_MAP.md` 신설. 엔진 registry 가 요구하는 **42 field_id 전수** 행 + 미지원 사유. §7-3 이 초안 | S00 |
| **GAP-14** | 측정 — `open` NULL 행 | 체결 가능성 | S04 통과 조건에 "`open IS NULL ∧ volume_shr>0` 행 수" 를 `_meta` 기록형으로 추가. 어댑터는 `OhlcPolicy` 를 명시 선택(`STRICT` 기본, `CLAMP` 는 baseline 승인 후) | S04·S07 |
| **GAP-15** | 소유자 — 거래비용·거래제약 규칙표 | 백테스트 현실성 | equity 는 **데이터만** 소유: `universe_daily.adv20_krw`(충격모형 입력)·`price_daily.close`(호가단위 계산 입력). 규칙표(거래세·호가단위·상하한가·공매도 금지구간)는 **엔진 저장소 이슈**로 분리 발행. `EQUITY_HANDOFF.md` 에 "equity 는 만들지 않는다 + 필요한 입력 컬럼 목록" 명시 | S22 |
| **GAP-16** | 순서 — `stg_doc_correction` 의존 | `disclosure_version` 확정 링크 | 문서층 P1 은 **서버 빌드 완료**(`stg_doc_correction` 17,600행)이나 코드는 `stage/doc-p1` 브랜치에 있고 **main 미병합**(확인: `git merge-base --is-ancestor 5617b49 origin/main` = false). equity 는 데이터만 읽으므로 빌드는 가능하나 EG0(입력 `_meta.gates` fail 0)을 위해 병합 필요 → **S13 을 별도 슬라이스로 분리**하고 착수 조건에 "doc-p1 병합" 명시 | S13 |
| **GAP-17** | 순서 — 문서층 P2(`stg_fin_asreported`) 미착수 | 재무 원본 판본 | `fin_std.restated_unknown=true` 전행으로 가고, **S14 를 대기 슬라이스**로 등록(착수 조건: P2 완료). PIT 결측률 교차표를 baseline 에 남겨 정정 편향 크기를 계량 | S12·S14 |
| **GAP-18** | 한계 — TR 수익률 | 팩터별 성과 부호 | 해소 불가. `EQUITY_HANDOFF.md` 에 PR-04 실측(저변동성 −4.35%p·밸류 +3.01%p·모멘텀 −1.10%p) 고지. `dividend_event` 를 계수로 만들지 않는 결정 유지 | S22 |
| **GAP-19** | 문서 — 시작연도 불일치 | 기대 관리 | `FACTORS.md` §0 의 "2008 공매도"·"2007 신용" 을 **"원장 하한 / equity 격자 시작 2010-01-04"** 2열로 정정 | S00 |
| **GAP-20** | 게이트 — 54 팩터 커버 판정 부재 | 목적 판정 자체 | `factor_readiness` 테이블 신설 + 게이트 **EG-F**(54행 전수, `status ∈ {ready, blocked}`, blocked 는 `reason` 필수 · `first_usable_date` NOT NULL) | S20 |
| **GAP-21** | 등식 — `universe_daily` coverage_gap 행 | 재현성·소비 금지의 모순 | gap 구간(2026-08-21~) 행을 "backfill_end 활성 티커 수 × gap 거래일" 로 **만들지만**(`EQUITY_WORKFLOW.md` §2-1), 소비는 `UniverseQuery.end > backfill_end` 거절로 **금지**된다(EG-C ③). 만들 이유가 없는 데이터이고 항등식도 아니다(gap 중 상장·폐지를 모른다). **해소: gap 행을 만들지 않고 `v_universe` 가 `d > backfill_end` 를 `coverage_gap` 상태로 반환**. EG1 은 `= Σ span n_days` 로 단순화 | S03 |

---

## 2. 워크플로우 v1.1 §3 비판 — "이대로 하면 목적에 도달하나"

각 항목: **지적 → 근거 → 도달 방해 → 개정안**.

### 2-1. 1단계가 2단계 산출에 의존한다 (순서 오류, 치명)

- **근거**: `EQUITY_DESIGN.md` §4-1 `universe_daily` 원천에 `stg_price_daily`(`volume_shr`·`value_krw`·`mktcap_krw`)가 들어 있고 컬럼에 `mktcap_krw`·`adv20_krw`·`no_trade_run`·`price_kind='reference'` 판정이 있다. 같은 문서 §4-2 는 `price_daily` 를 2단계 산출로 선언한다.
- **방해**: 정본이 둘이 된다. 1단계 `universe_daily.mktcap_krw` 와 2단계 `price_daily.mktcap_krw` 가 다른 빌드에서 나와 값이 어긋나면 어느 쪽이 정본인지 규칙이 없다. `EQUITY_WORKFLOW.md` §4 의 "1단계가 유일한 직렬 병목, 2·3·4·4B·5 병렬" 도 성립하지 않는다 — 3단계 격자는 `universe_daily.status`·`sec_type` 을 쓰고, `status` 는 `price_kind`·`no_trade_run` 에 의존한다(§4-1 상태 규칙).
- **개정**: `universe_daily` 를 **두 슬라이스로 분리**한다. S03 = 존재·상태(`status`·`market`·`sec_type`·`halt_state`·`admin_state`·`liquidation_window`·`signal_*`), S03B = 시장 파생(`mktcap_krw`·`adv20_krw`·`listing_age_days`·`no_trade_run`)을 `price_daily` 를 입력으로 **2단계 뒤에** 채운다. `status` 의 `suspended` 술어 중 `price_kind='reference' ∧ no_trade_run ≥ k` 항도 S03B 로 이동, S03 은 공시 축(`halt_state`)만으로 `suspended` 를 판정한다.

### 2-2. 소비자 계약이 코드 사실과 어긋난다 (결정 5)

- **근거**: `EQUITY_WORKFLOW.md` §0-1 "재무·컨센서스는 엔진 포트가 없다(`ports/` 4종, `PriceField` 5종)". 그러나 `backend/src/strategy_workbench/application/` 아래에 `EquityDataPort`·`RawObservationPort`·`FactorObservationPort`·`FactorMetadataPort`·`BacktestDataPort` 가 있고, `MockEquityDataAdapter`(`adapters/outbound/equity_mock/_adapter.py:82-403`) 한 클래스가 이 전부를 구현하며 `financial.book_equity`·`consensus.forward_eps`·`credit.margin_balance`·`event.earnings_surprise` 를 낸다. 조립 지점은 `bootstrap/_container.py:55-63` 의 `equity_adapter: str = "mock"` 한 줄이다.
- **방해**: "팩터층이 엔진 밖에서 시그널 패널을 주입" 이라는 결정 5 를 그대로 따르면, 이미 존재하는 파이프라인(포트 → 팩터 그래프 평가 → `TargetTape` → 엔진)을 우회하는 **두 번째 경로**를 만들게 된다. 로드맵 §8-5 는 "mock fallback 금지, 미지원 필드는 catalog 에서 unavailable" 을 명시한다.
- **개정**: 결정 5 를 **"엔진 커널 3포트 + 워크벤치 4포트, 단일 어댑터 `equity_duckdb`"** 로 재기술. §7 참조.

### 2-3. EG-C(소비자 계약)가 순환 의존이다

- **근거**: `EQUITY_WORKFLOW.md` §3-1 통과 조건에 "EG-C ②", §3-2 에 "EG-C ④·⑤" 가 있는데, 그 항목들을 검증할 어댑터 `adapters/equity_duckdb.py` 는 §3-7(7단계) 산출이다.
- **방해**: 1·2단계를 "통과" 시킬 방법이 없거나, 통과 조건을 형식적으로 넘기고 7단계에서 처음 실패를 발견한다. 이것이 "끝까지 만들고 나서 소비자가 못 쓰는 것을 아는" 전형적 순서다.
- **개정**: **S07(어댑터 v0)을 2단계 직후로 앞당긴다.** 3포트만 구현하고 `tests/test_bar_source_contract.py::BUILDERS` 에 `equity_duckdb` 를 등록해 EG-C ①②③④⑤⑩ 을 그 시점에 실행한다. S21(워크벤치 4포트)은 5단계 뒤.

### 2-4. 문서층 P1 산출을 쓰지 않는다 (의존 누락)

- **근거**: `DOC_DESIGN.md` §8.1 은 `correction_link` 를 **equity 4단계가 만든다**고 명세하고 입력을 `stg_doc_correction`(P1, 17,600행 서버 빌드 완료) + `stg_disclosure` 모집단 20,579 로 지정하며, 게이트 E-G6a(링크 성립 ≥99%)·E-G6b·E-G7(≥99%)까지 준다. 그런데 `EQUITY_DESIGN.md` §4-4 `disclosure_version` 원천은 `stg_disclosure` + `stg_doc_index.zip_ok` 뿐이고 `link_basis='grouped'` 근사로 간다.
- **방해**: 이미 서버에 있는 확정 링크를 안 쓰고 근사로 가면 EG6 "그룹 오판율" 을 잴 기준이 없다(오판율의 정답이 곧 `stg_doc_correction` 이다). 4단계 통과 조건의 "오판율 ≤ baseline" 이 순환한다.
- **개정**: `disclosure_version` 을 **S11(grouped 근사, 즉시 착수)** 과 **S13(`stg_doc_correction` 기반 `parsed` 링크 + E-G6a/6b/E-G7)** 로 분리. S13 착수 조건 = `stage/doc-p1` main 병합(GAP-16). S13 이 S11 의 오판율을 측정해 baseline 에 등재한다.

### 2-5. 한 단계에 너무 많다 (4단계 · 4B단계 · 3단계)

- **4단계**: `fin_std`(계정 매트릭스 · 분기 3개월 처리 · Q4 파생 · CF 전분기 차감 · 비12월 105사 `period_end` 후보 규칙 · `revenue_basis` · 금융업 470사)와 `disclosure_version`(report_nm 정규화 · 접두어 제거 · 제외 종류 4 · 기간 라벨 없음 586 격리 · `correction_seq`)을 한 PR 에 넣는다(`EQUITY_WORKFLOW.md` §3-4). 두 테이블의 결합은 `has_correction` 컬럼 하나다. 픽스처만 8종이다.
- **4B단계**: 6테이블(`holder_daily`·`ownership_snapshot`·`shares_outstanding`·`treasury_stock`·`audit_opinion`·`dividend_event`)이 한 슬라이스다. `row_kind='aggregate'` 제외는 3테이블만 적용되고(`EQUITY_DESIGN.md` §4-5), `dividend_event` 는 `se` 라벨 wide 변환이라 성격이 다르다.
- **3단계**: `flow_daily`(13주체 + KIS 대응표 10행) · `short_daily`(공매도 2축 + 대차 2축) · `credit_daily`(융자·대주)를 한 슬라이스에. 원천도 게이트도 다르고 격자만 공유한다.
- **방해**: PR 하나가 커지면 EG4 픽스처가 뭉치고, 실패 시 어느 규칙이 원인인지 좁히는 비용이 커진다. stage 는 62테이블을 PR 21건(#14~#34, `EQUITY_KICKOFF.md` §0)으로 나눴다 — 테이블 3개/PR. equity 4단계는 그 기준으로도 과적재다.
- **개정**: 4 → S11·S12(+S13·S14 대기), 4B → S15(지분·감사 3)·S16(주식수·자사주·배당 3), 3 → S08·S09·S10. **슬라이스 = 테이블 1~3개** 규칙 고정.

### 2-6. 너무 이른 최적화 3건

| 항목 | 근거 | 왜 이른가 | 개정 |
|---|---|---|---|
| `universe_policy` 분위수 초기값 실측 | `EQUITY_WORKFLOW.md` §3-1 판단 "정책 3종 분위수 초기값(서버 실측: 연도별 시총·ADV20 분포)" | 정책 **적용**은 팩터층이고(§0-2), 백테스트 1회에는 `'all'` 만 필요하다. 임계값을 지금 정하면 근거 없이 굳는다(D6 재발) | S03 은 `universe_policy` **스키마와 `'all'` 1행만**. 분위수는 S03B(ADV20 확보 후) 또는 팩터층 착수 시 |
| `krx_kis_close_ratio` 를 `price_daily` 10,890,251행에 실수 컬럼으로 저장 | `EQUITY_DESIGN.md` §4-2 | 검산축은 **집계 통계**로 충분하고(EG8 "일치율 ≥ baseline"), 행마다 굽으면 KIS 재수집 때 전 행이 바뀌어 `content_hash` 재현성 축이 흔들린다(EG5a). KIS 커버는 3,175종목뿐이라 대부분 NULL | 컬럼을 지우고 EG8 을 **조인 시점 집계 게이트**로. 불일치 상위 20종목은 `_meta` 에 기록(§5 기록 규약에 이미 있음) |
| `fill_kind` STRUCT + stage `miss_kind` 동반 | `EQUITY_DESIGN.md` §3 | 격자 3테이블에만 쓰는데 공통 골격에 올려 두면 나머지 23테이블 스키마에 잡음 | 공통 골격에서 빼고 §4-3 로컬 규약으로 (문서 변경만) |

### 2-7. 검수 타이밍 — as-of 불변이 마지막에 있다

- **근거**: EG5(c) as-of 불변 검사는 "`inputs` 변경 시" 발동하고 첫 빌드는 `skip(no_baseline)` 이다(`EQUITY_WORKFLOW.md` §2). 전량 통과는 7단계 통과 조건(§3-7)이다.
- **방해**: as-of 불변(같은 asof 로 두 번 물으면 같은 답)은 **PIT 층의 존재 이유 그 자체**다. 이걸 마지막에 재면, 예컨대 `v_cum_adj(asof)` 의 정규화 기준(base=asof)이 슬라이스 간에 달라진 것을 6단계 뒤에 발견한다.
- **개정**: `baseline.json` 에 **고정 asof 표본(날짜 5 × 종목 20)** 을 S02 시점에 등재하고, 뷰를 내는 모든 슬라이스(S06·S12·S17)의 통과 조건에 "같은 asof 2회 호출 결과 동일 + `rcept_dt/fetched_date > asof` 로 설명 불가한 차이 0" 을 넣는다.

### 2-8. 목적을 판정하는 게이트가 없다 (가장 중요)

- **근거**: EG-C ⑥ = "샘플 팩터 4개(V01·F01·M01·G05)가 profile 랙 적용 상태에서 손계산과 일치"(§2). §3-7 인계 산출에 "팩터 ID × 컬럼 × 시작일 표" 가 있지만 **게이트가 아니라 문서**다.
- **방해**: 사용자의 목적은 "54개 재료가 충분한가" 인데, 이를 pass/fail 로 답하는 술어가 워크플로우 어디에도 없다. 문서 표는 드리프트한다(실제로 `FACTORS.md` 는 08-26 까지 "37개/DART 25개" 로 틀려 있었다 — §10 정정 기록).
- **개정**: **`factor_readiness` 테이블 신설 + 게이트 EG-F**. §3 S20.
  - grain: `factor_id`(54행 강제)
  - 컬럼: `factor_id`·`group`·`formula`·`required_columns` TEXT[]·`required_tables` TEXT[]·`first_usable_date`·`status`(`ready`/`blocked`)·`blocked_reason`·`owner`(`equity`/`factor_layer`)·`caveat`
  - EG-F 술어: `count(*)=54` ∧ `status='blocked' ∧ blocked_reason IS NULL` 인 행 = 0 ∧ `status='ready' ∧ first_usable_date IS NULL` 인 행 = 0 ∧ `required_columns` 의 모든 원소가 실제 parquet 스키마에 존재.
  - 엔진 저장소의 `backend/FACTORS.md` 50 ID 와의 교차도 같은 테이블에 `engine_factor_ids` 로 싣는다.

### 2-9. 그 밖의 정합성 결함 (6건)

| # | 지적 | 근거 | 개정 |
|---|---|---|---|
| a | 5단계 위치가 두 문서에서 다름 | `EQUITY_WORKFLOW.md` §4 그래프 = 1 의존 병렬 / `EQUITY_DESIGN.md` §4-6 마지막 줄 = "실행 순서상 마지막(6 직전)" | v1.2 에서 **5단계 = 6단계 직전**으로 통일(데이터가 얇아 선행 가치 없음) |
| b | 폐지 909 분해가 두 가지 | WORKFLOW §0-3 "909 = KIS 652 + 스팩 176 + 우선주 81" / DESIGN §4-1·WORKFLOW §3-1 "652 + listing 소멸 257" | 게이트 술어를 **`stg_delisted_master` 652 ∪ (span `last_date` < `backfill_end` ∧ `delist_date_krx` NOT NULL)** 로 SQL 고정. 라벨 분해는 기록 |
| c | `universe_daily` EG1 이 항등식이 아님 | WORKFLOW §2-1 "gap 거래일 × gap 직전 활성 티커 수" | GAP-21 — gap 행 생성 폐기 |
| d | `MonthEndSession 미구현` 이 소비 경로와 무관 | `domain/portfolio/_compiler.py:89-111` | WORKFLOW §0-1 엔진 사실 목록에서 삭제 또는 "커널 한정" 로 한정 |
| e | 결정 5개가 문서상 "권고안 → 확정 요청" 상태 | `EQUITY_DESIGN.md` §9 제목 | 사용자 승인 반영 → §9 를 "확정(2026-09-05)" 로. 0단계 통과 조건 충족 |
| f | `EQUITY_HANDOFF.md` 가 7단계 산출인데 팩터층 착수를 막음 | WORKFLOW §3-7 | `factor_readiness`(S20)가 인계 문서의 핵심 표를 **테이블로** 대체하므로, 팩터층은 6단계 끝에 착수 가능 |

---

## 3. 개정 워크플로우 v1.2 — 23슬라이스

### 3-0. 규칙

- **슬라이스 = 브랜치 하나 = PR 하나 = 테이블 1~3개.** 브랜치명 `equity/<slice-id>-<slug>`.
- 코드 위치 `workspace/dongmin/src/equity/`, 테스트 `workspace/dongmin/tests/test_equity_*.py`, 픽스처 `data/equity/fixtures/<table>.json`(EG4 강제).
- 각 슬라이스는 §1 공통 길(설계 → TDD 픽스처 → 로컬 빌드 → 서버 실측 → 게이트 → MANIFEST 원자 교체 → 기록 → PR self-merge)을 그대로 따른다.
- **예상 소요**는 근거가 있을 때만 적는다. 근거: stage 1차 풀 빌드 62테이블 23분(`STAGE_HANDOFF.md` §3) · 문서층 P1 `stg_doc_section` 7,929,624행 74초 RSS 6.7GB(`DOC_DESIGN.md` §7) · `stg_fin` 스필 15~18GB(`EQUITY_KICKOFF.md` §4). 개발 시간은 stage 실적(62테이블 / PR 21건)만 있고 PR 당 시간 기록이 없어 **미측정**으로 둔다. 대신 규모 라벨 S/M/L 을 쓴다.

### 3-1. 슬라이스 표

| ID | 이름 | 테이블/산출 | 입력 | 규모 | 병렬 | 선행 |
|---|---|---|---|---|---|---|
| **S00** | 계약 문서 정정 | `EQUITY_FIELD_MAP.md` · DESIGN v1.2 · WORKFLOW v1.2 · FACTORS 정정 | 이 제안서 | S | — | — |
| **S01** | 법인·종목 식별 | `corp` · `security` · `corp_ticker` | `stg_corp_map`·`stg_company`·`stg_listing_daily`·`stg_etf_price_daily`·`stg_delisted_master` | M | — | S00 |
| **S02** | 캘린더·구간·지수 | `security_span` · `trading_calendar` · `index_daily` | S01 + `stg_index_daily`·`stg_flow_split_daily`·`stg_credit_daily`(gap 축) | M | — | S01 |
| **S03** | 유니버스(존재·상태) | `universe_daily` v1 · `universe_policy`(스키마+`all`) | S02 + `stg_listing_daily`·`stg_master_daily`·`stg_disclosure` | L | — | S02 |
| **S04** | 가격 정본 | `price_daily` | S02·S03 + `stg_price_daily`·`stg_etf_price_daily`·`stg_listing_daily` | M | ∥ S05 | S03 |
| **S05** | 기업행위 | `corp_event` | `stg_event_*` 15 · `stg_capital` · `stg_disclosure` 락일·배당결정 | L | ∥ S04 | S03 |
| **S06** | 조정계수·가격 뷰 | `adj_factor` · `v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap` | S04·S05 + `stg_listing_daily`(`list_shrs`·`par_value_krw`) | L | — | S04·S05 |
| **S03B** | 유니버스(시장 파생) | `universe_daily` v2 (`mktcap_krw`·`adv20_krw`·`listing_age_days`·`no_trade_run`·`suspended` 완성) | S04·S03 | M | ∥ S06 | S04 |
| **S07** | **엔진 어댑터 v0** | `equity_duckdb`(BarSource·UniverseSource·CorporateActionSource) + `BUILDERS` 등록 | S03B·S06 | M | — | S06·S03B |
| **S08** | 수급 격자 | `flow_daily` | S03B + `stg_flow_daily_kiwoom`·`stg_flow_split_daily`·`stg_foreign_daily`·`stg_shards_kiwoom`·`stg_units_kis` | L | ∥ S09·S10·S11·S15·S17 | S03B |
| **S09** | 공매도·대차 격자 | `short_daily` | S03B + `stg_short_daily_kiwoom/kis`·`stg_lending_daily`·`stg_loan_daily_kis` | L | ∥ | S03B |
| **S10** | 신용 격자 | `credit_daily` | S03B + `stg_credit_daily` | M | ∥ | S03B |
| **S11** | 공시 판본(근사) | `disclosure_version`(`link_basis='grouped'`, +`delay_days`) | `stg_disclosure`·`stg_doc_index`·S01 | M | ∥ | S01 |
| **S12** | 재무 PIT | `fin_std` · `v_fin_latest` | `stg_fin`·`stg_rcept_dt_map`·S01·S11 | L | ∥ | S11 |
| **S13** | 공시 판본(확정 링크) | `disclosure_version` `link_basis='parsed'` + E-G6a/6b/E-G7 | + `stg_doc_correction` | M | 대기 | S11 · **doc-p1 병합** |
| **S14** | 재무 원본 판본 | `fin_std.vintage_kind` 결합 | + `stg_fin_asreported` | L | 대기 | S12 · **문서층 P2 완료** |
| **S15** | 지분·감사 | `holder_daily` · `ownership_snapshot` · `audit_opinion` | `stg_holder_*`·`stg_hyslr`·`stg_audit`·S01 | M | ∥ | S01 |
| **S16** | 주식수·자사주·배당 | `shares_outstanding` · `treasury_stock` · `dividend_event` | `stg_shares`·`stg_tesstk`·`stg_dividend`·S01 | M | ∥ | S01 |
| **S17** | 컨센서스 | `consensus_daily` | `stg_consensus_*`·`stg_v3_*`·S01 | M | ∥ | S01 |
| **S18** | 의견·목표가 | `opinion_daily` · `opinion_broker_daily` | `stg_analyst_summary`·`stg_analyst_broker`·`stg_v3_analyst_opinions`·`stg_wise_coverage` | S | ∥ | S01 |
| **S19** | 공개시점 대장 | `dataset_profile` | 전 슬라이스 `_meta` | M | — | S08~S18 |
| **S20** | **팩터 준비도** | `factor_readiness`(54행) + 게이트 EG-F | S19 + `FACTORS.md` + `backend/FACTORS.md` | M | — | S19 |
| **S21** | **워크벤치 어댑터** | `equity_duckdb` 4포트 완성 + contract suite parameterize | S07·S19·S20 | L | — | S20 |
| **S22** | 마무리·인계 | `baseline.json` 고정 · `EQUITY_HANDOFF.md` · 재현성 | 전부 | M | — | S21 |

병렬 최대 폭: S03B 완료 후 **6갈래**(S08·S09·S10·S11+S12·S15·S16·S17+S18). 단 **서버 빌드는 `flock` 직렬**(RAM 15GB) — 병렬은 픽스처·TDD·로컬 빌드까지다(`EQUITY_WORKFLOW.md` §4 규칙 유지).

### 3-2. 1단계 상세 (파일·테스트 단위)

#### S00 — 계약 문서 정정 (코드 0)

- **입력**: 이 제안서 · `backend/src/strategy_workbench/**` · `backend/FACTORS.md` · `docs/superpowers/specs/2026-09-03-strategy-workbench-roadmap.md`
- **산출**:
  - `workspace/dongmin/docs/EQUITY_FIELD_MAP.md` — 엔진 registry 요구 **42 field_id 전수** × (equity 테이블.컬럼 / 뷰 / 미지원 사유) + `security_id` 문법(GAP-11) + `universe_id` 문법(GAP-12) + 벤치마크 예약 네임스페이스(GAP-09). §7-3 이 초안.
  - `EQUITY_DESIGN.md` v1.2: §7 에 워크벤치 4포트 표 추가 · §9 "확정(2026-09-05)" · §4-1 `universe_daily` 를 v1/v2 로 분리 · §4-2 `krx_kis_close_ratio` 컬럼 삭제 · §4-4 `revenue_basis` 어휘 확정 · §11 에 GAP-06·07·08·15 를 한계로 등재.
  - `EQUITY_WORKFLOW.md` v1.2: §3 을 §3-1 슬라이스 표로 교체 · §2 에 EG-F 추가 · §0-1 `MonthEndSession` 문구 정정.
  - `FACTORS.md`: §0 시작연도 2열화(원장 하한 / equity 격자 2010-01-04, GAP-19) · §11-2 를 "재료 확보, 정의는 팩터층" 으로 · §11-4 해소 표기 · F05 명명 주석.
- **TDD**: 없음(문서). 대신 **자동 대조 스크립트** `workspace/dongmin/scripts/check_field_map.py` — `backend` registry 의 `required_field_ids` 집합과 `EQUITY_FIELD_MAP.md` 행 집합의 차 = 0 을 검사. CI lint 로 등록.
- **통과 조건**: ① 대조 스크립트 차 0 ② `FACTORS.md` 54 ID 전부가 `EQUITY_FIELD_MAP.md` 또는 `factor_readiness` 초안에 등장 ③ 결정 5개가 "확정" ④ GAP 21건 각각이 슬라이스에 배정됨.
- **병렬**: 없음(선행).

#### S01 — `corp` · `security` · `corp_ticker`

- **입력(stage)**: `stg_corp_map`(3,478) · `stg_company`(`acc_mt`·`induty_code_current`) · `stg_listing_daily`(`isin`·`name`·`list_date`·`market`·`secugrp`·`stkcert_tp`·`sect_tp`) · `stg_etf_price_daily` · `stg_delisted_master`(652)
- **산출**: `corp`(whole) · `security`(whole) · `corp_ticker`(whole) + `data/equity/_pinned/` 5개
- **파일**:
  - `src/equity/__init__.py`
  - `src/equity/manifest.py` — stage `manifest.py` 의 파일 규약 재사용(`BuildRecord.inputs` 포함), `_pinned/` 하드링크 생성·`MANIFEST.json` 복사
  - `src/equity/pinning.py` — 입력 build 고정·해제
  - `src/equity/gates.py` — EG0·EG1·EG2·EG3·EG4·EG5·EG7 골격(`run_all`, `skip(reason)`)
  - `src/equity/baseline.py` — stage `baseline.py` 재사용 래퍼
  - `src/equity/rules_master.py` — `corp`·`security`·`corp_ticker` 선언(컬럼·타입·EG1 등식·파티션 클래스)
  - `src/equity/build_master.py` — duckdb SQL 빌더
  - `src/equity/__main__.py` — `python -m equity build <table>`
- **테스트 / 픽스처**:

| 테스트 파일::함수 | 픽스처 | 검증 |
|---|---|---|
| `test_equity_manifest.py::test_inputs_pinned_survives_stage_gc` | `fixtures/_pinned_stub/` | keep=3 GC 후에도 `_pinned/` 경로 존재 |
| `test_equity_manifest.py::test_commit_is_atomic_on_gate_fail` | — | 게이트 실패 시 구 `MANIFEST.json` 무손 |
| `test_equity_gates.py::test_eg0_rejects_missing_stage_column` | `fixtures/eg0_schema.json` | 선언 컬럼이 stage 컬럼명(`close_krw`)과 다르면 fail |
| `test_equity_master.py::test_sec_type_mapping_full_vocabulary` | `fixtures/security.json` | P11 어휘 11종 → `sec_type` 10종 매핑 전수 (`주권/보통주`→common, `sect_tp='SPAC(소속부없음)'`→spac 우선) |
| `test_equity_master.py::test_spac_beats_common` | 동일 | 스팩이 `주권/보통주` 이면서 spac 으로 판정 |
| `test_equity_master.py::test_ticker_stays_text` | `fixtures/ticker_0001A0.json` | `0001A0` 정수 캐스팅 없음 |
| `test_equity_master.py::test_isin8_groups_kr7_only` | `fixtures/corp_ticker.json` | KR7 3,438 그룹당 보통주 1 / 비KR7(HK0 900050·900060 포함) 단독 corp |
| `test_equity_master.py::test_preferred_maps_to_common_ticker` | `fixtures/003540.json` | 003540 우선주 → `common_ticker` 부여, corp 1:N |
| `test_equity_master.py::test_delist_date_krx_first_kis_fallback` | `fixtures/delist_conflict.json` | KRX 우선, 둘 다 있고 상이 → `delist_conflict=true` |
| `test_equity_master.py::test_delist_date_null_at_backfill_end` | `fixtures/backfill_end.json` | 마지막 존재일 = `backfill_end`(2026-08-20) → `delist_date` NULL, basis `unknown` |
| `test_equity_master.py::test_corp_cls_not_loaded` | — | `corp` 스키마에 `corp_cls` 부재 |
| `test_equity_master.py::test_etf_list_date_unknown` | `fixtures/etf_security.json` | ETF `list_date` NULL·basis `unknown` |

- **통과 조건**: EG0 · EG1(`corp`=distinct corp_code(`stg_corp_map`) ∧ `security`=distinct ticker(listing ∪ etf) ∧ `corp_ticker`=`security` 행수) · EG2(해당 없음, `skip(no_fact_rows)`) · EG3(KR7 isin8 그룹당 `is_common` 정확히 1 ∧ `induty_code` 공란 0 ∧ PK 유일) · EG4(위 픽스처 전량) · EG7(`sec_type='other'` 격리 ≤ 0.1%) · `corp_ticker` 매핑률 `_meta` 기록.
- **소요**: 개발 미측정(규모 M). 서버 빌드는 whole 3테이블·최대 5,088행이라 stage 최소 테이블(`stg_company` 3,478행) 수준 = 수 초.
- **병렬**: 불가(전 슬라이스의 선행).

#### S02 — `security_span` · `trading_calendar` · `index_daily`

- **입력**: S01 `security` · `stg_listing_daily`·`stg_etf_price_daily`(존재일) · `stg_index_daily`(4,094 거래일, 347,821행) · gap 축 `stg_flow_split_daily`·`stg_credit_daily`
- **산출**: `security_span`(whole) · `trading_calendar`(whole) · `index_daily`(date_axis)
- **파일**: `src/equity/rules_calendar.py` · `src/equity/build_calendar.py` · `src/equity/spans.py`(연속 run 추출)
- **테스트 / 픽스처**:

| 테스트::함수 | 픽스처 | 검증 |
|---|---|---|
| `test_equity_calendar.py::test_trading_days_equal_index_distinct_dates` | — | EG1 = 4,094 + gap |
| `test_equity_calendar.py::test_prev_next_td_are_calendar_neighbors` | `fixtures/trading_calendar.json` | `prev_td`/`next_td` 무결 |
| `test_equity_calendar.py::test_no_is_trading_day_column` | — | 항진명제 컬럼 부재 |
| `test_equity_span.py::test_relisting_creates_two_spans` | `fixtures/span_036220.json`·`fixtures/span_101970.json` | 036220(~2016-05-04 / 2024-03-13~) · 101970(~2015-03-16 / 2025-03-28~) 각 2구간 |
| `test_equity_span.py::test_spans_do_not_overlap` | 동일 | 비중첩 |
| `test_equity_span.py::test_span_sum_equals_listing_rows` | `fixtures/span_sum.json` | Σ n_days = count(ticker,date)(listing ∪ etf) |
| `test_equity_span.py::test_span_stops_at_backfill_end` | `fixtures/backfill_end.json` | `last_date ≤ 2026-08-20` |
| `test_equity_span.py::test_delisted_preferred_span_closes` | `fixtures/preferred_delist.json` | 우선주 폐지 1건 span 종료 |
| `test_equity_index.py::test_index_key_is_class_name_date` | `fixtures/index_daily.json` | 업종지수명 양시장 중복 → PK 3열 |
| `test_equity_index.py::test_index_available_is_content_date` | 동일 | available=date, basis=default |

- **통과 조건**: EG0·EG1(3식)·EG2(`index_daily`)·EG3(span 비중첩·PK)·EG4·EG5(a) · **listing 완결성**(distinct date 4,094 = `trading_calendar` KRX 거래일, P11) · **고정 asof 표본 baseline 등재**(§2-7).
- **병렬**: 불가.

#### S03 — `universe_daily` v1 · `universe_policy`

- **입력**: S02 + `stg_listing_daily`(`market`·`sect_tp`) · `stg_etf_price_daily` · `stg_master_daily`(2026-09-01~) · `stg_disclosure`(신호 술어 7종)
- **산출**: `universe_daily`(date_axis; `status`·`market`·`sec_type`·`halt_state`·`admin_state`·`admin_state_basis`·`liquidation_window`·`signal_*` 5·`admin_flag`·`available_*`) · `universe_policy`(whole; `'all'` 1행 + 스키마)
- **작업**: `report_nm` 정규화(대괄호 접두 제거) → 신호 7종 추출 · `halt_state` 구간화(지정 8,415 / 해제 2,287 / 동일일 1,214, 재거래 암묵 해제) · KOSDAQ `sect_tp` 일별 `admin_state`(measured) / KOSPI 365거래일 창(derived) · `liquidation_window`(349) · **gap 행 생성 안 함(GAP-21)**
- **테스트 / 픽스처**:

| 테스트::함수 | 픽스처 | 검증 |
|---|---|---|
| `test_equity_universe.py::test_halt_signal_excludes_release` | `fixtures/signal_halt.json` | `%매매거래정지%` ∧ NOT `%해제%` → 8,415 술어 |
| `test_equity_universe.py::test_halt_and_release_same_day` | `fixtures/halt_same_day.json` | 지정+해제 동일일 1,214 → 그날 halt 아님 |
| `test_equity_universe.py::test_halt_closes_on_first_trade` | `fixtures/halt_resume.json` | 해제 공시 없이 `volume>0` 이면 종료 |
| `test_equity_universe.py::test_halt_never_left_open_at_span_end` | 동일 | EG3: 열린 채 끝난 구간 0 |
| `test_equity_universe.py::test_kospi_admin_is_derived_window` | `fixtures/admin_kospi.json` | `admin_state_basis='derived'`·365거래일 |
| `test_equity_universe.py::test_kosdaq_admin_is_measured_sect_tp` | `fixtures/admin_kosdaq.json` | 소속부 measured |
| `test_equity_universe.py::test_liquidation_window_to_delist` | `fixtures/liquidation.json` | 개시 공시 → `delist_date` |
| `test_equity_universe.py::test_no_rows_after_backfill_end` | `fixtures/coverage_gap.json` | gap 행 0 (GAP-21) |
| `test_equity_universe.py::test_etf_row_present_no_policy_flag` | `fixtures/etf_universe.json` | ETF 행 존재·정책 플래그 없음 |
| `test_equity_universe.py::test_policy_table_has_only_all` | `fixtures/universe_policy.json` | `'all'` 1행, 임계 없음 |
| `test_equity_universe.py::test_bracket_prefix_stripped_before_match` | `fixtures/report_nm_prefix.json` | `[기재정정]주권매매거래정지` 매칭 |

- **통과 조건**: EG0·EG1(`= Σ span n_days`)·EG2(available/basis)·EG3(halt 열린 구간 0)·EG4·EG7 · **폐지 909 전부 `delist_date` 보유**(술어 §2-9b) · **정리매매·폐지 recall**(폐지 909 중 `signal_liquidation ∨ signal_delist` 가 폐지일 −30거래일 내 존재 비율, 첫 빌드 `skip(no_baseline)` + 측정치 기록).
- **소요**: 규모 L. 서버 빌드는 date_axis 약 9.2M 행 — `stg_doc_section` 7.9M행 74초·RSS 6.7GB 실적을 근거로 **분 단위**, `memory_limit` 6GB 로 연도 파티션 직렬 권장.
- **병렬**: 불가.

### 3-3. 2단계 이후 슬라이스 요약 (입력·산출·핵심 픽스처·통과 조건)

| ID | 입력 | 산출 | 핵심 픽스처(EG4) | 통과 조건 | 병렬 |
|---|---|---|---|---|---|
| **S04** `price_daily` | `stg_price_daily` 9,201,516 · `stg_etf_price_daily` 1,688,735 · `stg_listing_daily` | 원주가 O/H/L/C·`volume_shr`·`value_krw`·`mktcap_krw`·`shares_out`·`par_value_krw`·`price_kind` | `price_reference_row`(volume=0) · `ohl_null_row` · `etf_price` · `disjoint_check` | EG0·EG1(=10,890,251)·EG2·EG3(PK)·EG4·EG7 · **`open IS NULL ∧ volume>0` 행 수 `_meta` 기록(GAP-14)** | ∥ S05 |
| **S05** `corp_event` | `stg_event_*` 15종 · `stg_capital` · `stg_disclosure` 락일 257/200/618/455/452·배당결정 19,275 | `event_id`·`event_type` 13·`announce_date`·`effective_date`·`effective_basis`·`ratio`·`amount_krw` | `split_20180504_005930` · `bonus_1p2` · `capred_1` · `spinoff_207940` · `rights_piic_1` · `cb_1` · `treasury_aq_1` · `dedup_same_day` | EG0·EG1(선언 원천 − dedup)·**EG2 announce 축**·EG4·EG7·EG8(기준가≠전일종가 5,214건 recall, `skip(no_baseline)`) | ∥ S04 |
| **S06** `adj_factor`·뷰 4 | S04·S05·`stg_listing_daily`(`list_shrs`·`par_value_krw`) | `price_factor`·`share_factor`·`factor_source`·`factor_ok`·`available_date` + `v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap` | `cum_adj_005930_50to1`(분할 전 `cum_price`=1/50·`cum_share`=50, `adj_volume(2018-05-03)` 손계산) · `krx_observed_only_factor`(available=효력일+1TD) · `factor_ok_false_207940` · `firm_mktcap_003540` | EG3(`price×share=1`, `v_firm_mktcap`=Σ종류주)·EG8(분할일 수정수익률 점프·조정 거래량 점프)·**as-of 불변 표본**(§2-7)·EG-C ④ | — |
| **S03B** `universe_daily` v2 | S04·S03 | `mktcap_krw`·`adv20_krw`(창 [D−19,D])·`listing_age_days`·`no_trade_run`·`suspended` 완성 | `adv20_window` · `no_trade_run_1` · `suspended_by_reference` | EG1(행수 불변)·EG3·EG4 · `adv20` 창 정의 테스트 | ∥ S06 |
| **S07** 엔진 어댑터 v0 | S03B·S06 | `src/equity/adapters/engine_duckdb.py` (3포트) + `BUILDERS['equity_duckdb']` | 계약 테스트 재사용 | **EG-C ①②③④⑤⑩** 전량 · `OhlcPolicy` 명시 선택 · `security_span ∩ [start,end]` 질의 축소 | — |
| **S08** `flow_daily` | `stg_flow_daily_kiwoom` 7,621,338 · `stg_flow_split_daily` 949,023 · `stg_foreign_daily` 7,682,844 · 로그 2 | 키움 13주체 + `foreign_wght_pct`·`limit_exh_rt_pct`·`foreign_poss_shr`·`src`·`fill_kind` | `src_omitted_shard_done` · `not_collected_no_log` · `shard_empty` · `kis_only_delisted` · `investor_sum_zero` · `kis_to_kiwoom_map_row` | EG1(격자·컬럼 수·pre_calendar 7,609)·EG2(profile 행)·EG3(12주체 합, kiwoom·`orgn` 제외)·EG9 4항 | ∥ |
| **S09** `short_daily` | `stg_short_daily_kiwoom` 3,971,630 · `stg_short_daily_kis` 939,610 · `stg_lending_daily` 6,988,296 · `stg_loan_daily_kis` 493,445 | `short_volume_shr`·`short_value_krw`·`short_ratio_pct`·`lending_balance_kis_shr`·`lending_balance_kiwoom_raw`·`src` | `short_zero_20200630`(src_omitted) · `kiwoom_kis_overlap_zero` · `lending_unit_unmeasured` | EG1(pre_calendar 58,211)·EG8(겹침 0)·EG9 · **GAP-04 단위 비율 분포 기록** | ∥ |
| **S10** `credit_daily` | `stg_credit_daily` 8,404,204 | 융자·대주 잔고 + `stlm_date` | `credit_pre_2010_reject`(24,497) · `stlm_lag_distribution` | EG1(pre_calendar 24,497)·EG2·EG9 | ∥ |
| **S11** `disclosure_version` | `stg_disclosure`·`stg_doc_index` | `group_key`·`bsns_year`·`reprt_code`·`correction_seq`·`corrected_at`·`link_basis='grouped'`·**`legal_deadline`·`delay_days`(GAP-05)** | `prefix_stripped_group` · `no_label_586_isolated` · `excluded_kinds_4` · `multi_correction_seq2` · `delay_days_90d` | EG1(정기보고서 3종 접수 행수)·EG3·EG4·EG6(오판율 `skip(no_baseline)`) | ∥ |
| **S12** `fin_std`·`v_fin_latest` | `stg_fin` 15,375,024 · `stg_rcept_dt_map` · S01·S11 | 표준계정 wide · `is_months` · `*_q4_derived` · CF `_ytd`+`_q` · `revenue_basis` · `has_correction` · `restated_unknown` | `samsung_fy2025_q1q2_halfsum` · `march_fiscal_period_end` · `doosan_fy2020_missing_at_D`(정정 지연 1,232일) · `bank_revenue_basis` · `receipt_delay_2875d` · `q4_derived_mixed_vintage` | **착수 첫 작업 = GAP-02 계정 실측** · EG1·EG2(rcept_dt ≥ period_end, 참조표 미스 0, 파생 available=max)·EG6 전수·EG7 격리 · **PIT 결측률 교차표 baseline** · as-of 불변 | ∥ |
| **S13** 확정 링크 | + `stg_doc_correction` 17,600 | `link_basis='parsed'`·`orig_rcept_no`·`candidate_status`·`date_check`·`prior_corr_count` | `c340_unique_339` · `no_zip_2976` · `multi_resolved` · `filed_date_unparsed` | **E-G6a ≥99%** · E-G6b 기록 · **E-G7 ≥99%** · S11 오판율 baseline 등재 | **대기**(doc-p1 병합) |
| **S14** 원본 판본 | + `stg_fin_asreported` | `vintage_kind` 결합 시계열 | `no_correction_100pct_match` · `correction_delta` | 정정 없는 보고서 두 소스 100% 일치 | **대기**(문서층 P2) |
| **S15** 지분·감사 | `stg_holder_elestock` 32,668 · `stg_holder_majorstock` 22,209 · `stg_hyslr` 228,226 · `stg_audit` 93,037 | `holder_daily`·`ownership_snapshot`·`audit_opinion` | `elestock_ratio_not_qty` · `majorstock_5pct` · `hyslr_aggregate_excluded` · `audit_non_clean` · `rolling_window_first_day` | EG1(3식)·EG2(rcept_dt)·EG3·EG6·EG7 | ∥ |
| **S16** 주식수·자사주·배당 | `stg_shares` 97,194 · `stg_tesstk` 328,704 · `stg_dividend` 384,232 | `shares_outstanding`·`treasury_stock`·`dividend_event` | `tesstk_aggregate_excluded` · `tesstk_cancel_qty` · `dividend_se_wide_3` · `shares_row_kind` | EG1(3식)·EG2·EG3·EG7 | ∥ |
| **S17** `consensus_daily` | `stg_consensus_*` · `stg_v3_revision_daily` 63,175 · `stg_v3_consensus_annual` | `est_mean/min/max`·`unit`·`src`·`obs_month`·`target_period` | `three_vintages_min_selected` · `revision_from_two_fetched_dates`(compare·matrix lookback 유래 금지) · `unit_eps_krw_vs_sales_100m` · `v3_wise_boundary` · `v3_collected_date_null` · `v3_unpivot_row` | EG1·EG2(구간별 basis)·EG6(max 선택 0)·EG8(v3⋈WISE 09-01~02 일치율)·EG9·EG-C ⑨ | ∥ |
| **S18** 의견·목표가 | `stg_analyst_summary` 1,612 · `stg_analyst_broker` 9,717 · `stg_v3_analyst_opinions` 254,925 | `opinion_daily`·`opinion_broker_daily`(+`prev_opinion_date`) | `broker_change_pct_needs_interval` · `wise_covered_804` | EG1(1:1)·EG2·EG3(PK+`opinion_date`) | ∥ |
| **S19** `dataset_profile` | 전 슬라이스 `_meta` | table×column_scope 행 + `coverage_*`·`coverage_by_mktcap_quintile` | `lag_known_false_requires_lag1` · `default_basis_requires_evidence` | **EG2**(`lag_known=false` 테이블 전수 ∧ `lag ≥ 1`)·`coverage_from` 전수·`source_stage_tables` 전수 | — |
| **S20** `factor_readiness` | S19 · `FACTORS.md` 54 · `backend/FACTORS.md` 50 | 54행 준비도 대장 | `54_rows_exact` · `blocked_requires_reason` · `ready_requires_first_usable_date` · `required_columns_exist` | **EG-F**(§2-8) · 엔진 50 ID 교차 | — |
| **S21** 워크벤치 어댑터 | S07·S19·S20 | `equity_duckdb` 4포트 + `ADAPTERS` parameterize | `backend/tests/contract/test_raw_observation_port.py` 전량 · `test_mock_equity_adapter.py` 대응 | 두 포트 셀 일치(로드맵 §5) · 미지원 필드 `unavailable` 선언 · `composition root` 설정만으로 전환 | — |
| **S22** 마무리 | 전부 | `baseline.json` 고정 · `EQUITY_HANDOFF.md` · 재현성 | — | EG5(a)(c) · EG-C ①~⑩ · EG-F · 전 테이블 `gates` fail 0 | — |

---

## 4. MVP 경로 — 백테스트 1회를 가장 빨리

### 4-1. 경로

```
S00 → S01 → S02 → S03 → S04 → S05(축소) → S06 → S03B → S07 → [MVP-A]
                                                        └→ S21(축소) → [MVP-B]
```

- **S05 축소판**: `corp_event` 를 **`split`·`reverse_split`·`bonus`·`stock_dividend` 4유형만** 만든다. 이 4개가 시총 불변 이벤트(`price×share=1`)라 EG3 로 자기검증되고, 나머지 9유형(rights·capred·spinoff·merger·cash_dividend·cb_issue·treasury_*)은 팩터 재료일 뿐 **가격 조정에 필요 없다**(rights·spinoff 는 `factor_ok=false` 로 가므로 어차피 계수를 못 만든다). 이벤트 원천 15종 파싱을 MVP 밖으로 뺀다.
- **S21 축소판**: `RawObservationPort` **하나만** 구현하고 필드는 `price.close`(→ `v_adj_price`)·`price.market_cap`·`price.volume`·`price.trading_value` 4개. `sector_id=None`, `previous_weight=0.0`(포트 문서가 허용). `EquityDataPort.list_fields()` 는 이 4개만 낸다.

### 4-2. MVP-A — 엔진 커널 단독 (`BarSource`·`UniverseSource`·`CorporateActionSource`)

- **가능한 것**: 단일·다종목 buy-and-hold, 분할 이벤트가 있는 구간의 수량·평단 조정, 폐지 종목 포함 구간 로드, `test_bar_source_contract.py` 전 케이스.
- **검증**: EG-C ①②③④⑤⑩ — 특히 **⑩ 폐지 909 중 무작위 20종목 포함 전 구간 BarQuery 가 OK ∧ 반환 종목 집합 = 요청 집합**(생존편향 방지의 실증).
- **한계**: 팩터 없음. 전략은 `TargetTape` 없이 커널 전략 API 로 직접 써야 한다.

### 4-3. MVP-B — 워크벤치 파이프라인 1회 (권고)

- **가능한 것**: `RawObservationPort` → `FactorGraph` 평가(`price.momentum_12_1` = 252세션 모멘텀·21스킵·횡단면 rank, `_registry.py:_implemented_graphs`) → `_rebalance_pairs` 월간(signal=T, execution=T+1) → `TargetTape` → 엔진 실행 → `BacktestRunResult`.
- **필요한 필드**: `price.close` 만으로 `price.momentum_12_1` 이 돈다. `financial.book_to_market` 을 추가하려면 S12 가 필요하다.
- **시작 가능 구간**: 2011-01 ~ 2026-08-20 (2010-01-04 + 252거래일 워밍업, `backfill_end` 상한).

### 4-4. MVP 시점의 한계 (명시)

| 한계 | 내용 | 언제 풀리나 |
|---|---|---|
| 팩터 7개뿐 | M01·M02(조건부)·M03·R01·R02·R03·R04 — §1 의 54 중 가격·지수만 | S08~S18 |
| 수익률 PR | 배당 미반영. PR-04 실측 편의: 저변동성 −4.35%p·밸류 +3.01%p·모멘텀 −1.10%p | 영구(GAP-18) |
| 섹터 중립화 불가 | `sector_id=None` | GAP-07 결정 후 |
| 거래비용 = 평면 `fee_bps` | 거래세·호가단위·상하한가·공매도 금지구간 없음 | GAP-15 (엔진 저장소 이슈) |
| 유니버스 정책 없음 | `'all'` 만. 소형주·스팩·리츠·ETF 가 전부 들어온다 | S03B·팩터층 |
| 관리·정지 상태 비대칭 | KOSPI 관리종목 종료 불가 | GAP-06 (영구 완화만) |
| 이벤트 4유형만 | rights·spinoff·merger 구간의 가격 불연속이 실현손익으로 기록됨(`EQUITY_DESIGN.md` §7 엔진 한계) | S05 완전판 |
| 2026-08-21 이후 불가 | `coverage_gap` | KRX 재백필 |
| 재무·컨센서스 필드 unavailable | catalog 에서 명시 거부 | S12·S17 |

### 4-5. 전체 경로와의 차이

MVP 는 **슬라이스 9개**(S00·S01·S02·S03·S04·S05축소·S06·S03B·S07[+S21축소]), 전체는 **23개**. 빠진 14개가 만드는 것은 팩터 47개 · 결측 3분류 · 정정 PIT · 공개시점 대장 · 준비도 대장이다. **MVP 에서 내린 결정 중 되돌리기 비싼 것은 하나뿐** — `security_id`·`universe_id` 어휘(GAP-11·12)다. 그래서 S00 을 MVP 안에 넣었다.

---

## 5. 위험 대장

| ID | 위험 | 징후(무엇을 보면 아는가) | 완화 | 롤백 |
|---|---|---|---|---|
| **R-01** | 서버 RAM 15GB 초과 — 격자 9.2M×3 · `fin_std` 스필 15~18GB | 빌드 중 RSS > 8GB, `_tmp/spill` 급증, SSH 무응답 | 테이블 직렬(`flock`) · `memory_limit=6GB`·`threads=3` · **연도 파티션 단위 COPY** · 첫 슬라이스는 1개 연도만 실빌드 후 RSS·초 기록 | 파티션 단위 커밋이므로 실패 연도만 재실행. `MANIFEST` 미교체 → 구 버전 무손 |
| **R-02** | stage 재빌드가 고정 입력을 지움(keep=3 GC) | `_pinned/` 경로 stat 실패, EG0 fail | `data/equity/_pinned/<stg_x>/v=<build>/` **하드링크**(동일 디바이스 확인 P9) + `BuildRecord` 1건 복사 | 하드링크가 살아 있으면 stage GC 와 무관. 링크마저 없으면 해당 equity build 를 `_failed/` 로 내리고 stage 신 build 로 재고정 |
| **R-03** | 정정 링크 커버리지 부족 → restated 값이 원본 행세 | E-G6a < 99%, `date_check='no_zip'` 비율 상승 | 실측치로 관리: 원장 규칙 링크 성립 **339/340 = 99.7%**(C340) · `rm` 플래그 교차 **6,156/6,162 = 99.9%** · ZIP 보유 정정 **17,603/20,579 = 85.5%** · `filed_date` 해석 **14,856/17,188 = 86.4%**(P1 D6). ZIP 없는 2,976건은 원장 모집단으로 커버(`DOC_DESIGN.md` §8.1) | `link_basis='grouped'`(S11)로 되돌아가고 오판율을 `_meta` 에 기록. **주의: 지시문의 "커버 92%" 는 문서 어디서도 재현되지 않는다** — 위 4개 수치 중 무엇을 뜻하는지 확정 필요 |
| **R-04** | 관리종목 KOSPI 비대칭 → E07·유니버스 정책이 KOSDAQ 쪽으로 기움 | 시장별 `admin_state` 지속일 중앙값 차이가 baseline 밖 | `admin_state_basis` 필수 노출 · `factor_readiness.caveat` · 게이트는 **기록형**(임계로 막지 않는다 — 원천 부재라 개선 불가) | 없음(한계 고지) |
| **R-05** | 재현성 깨짐 — payload 가 `content_hash` 에 섞임 | 같은 `inputs` 재빌드에서 파티션 해시 불일치 | 문서층 P1 이 실제로 겪음(`stg_doc_parse_log.t_*_ms` 가 실행마다 달라짐, `DOC_DESIGN.md` §7 미결). equity 는 **빌드 시각·소요·RSS 를 팩트 컬럼에 넣지 않는다**(전부 `_meta`) | 해당 컬럼을 `payload_exclude` 로 빼고 재빌드. EG5(a) 가 잡는다 |
| **R-06** | 엔진 소비자 계약이 이동 중 | `backend` 에 P4 커밋이 계속 들어옴(a2fa034 등), 포트 시그니처 변경 | `EQUITY_FIELD_MAP.md` 대조 스크립트를 **CI lint** 로 걸어 registry 변경을 즉시 검출 · S21 은 계약 테스트를 **parameterize** 해서 엔진 저장소가 테스트를 소유하게 한다 | 어댑터만 수정. equity 테이블은 불변 |
| **R-07** | `stage/doc-p1` 미병합 | `git merge-base --is-ancestor` false | S13·S14 를 **대기 슬라이스**로 격리해 주경로를 막지 않는다 | 없음(대기) |
| **R-08** | 정의 미결(금융업 revenue·기관 12분류)이 빌드 뒤 발견 | `revenue_basis='unavailable'` 비율이 470사 대부분 | GAP-01·03 을 **S12 착수 조건**(계정 실측)과 `factor_readiness.blocked_reason` 으로 앞당겨 노출 | `revenue` 컬럼은 그대로 두고 `revenue_basis` 만 갱신 — 재빌드 1테이블 |
| **R-09** | 키움 샤드 1,070 티커 미커버 → `src_omitted` 오판(진짜 0 이 아닌데 0) | `evidence_rate` 하락, `fill_kind` 분포 이상 | EG9 에 `evidence_rate` 이미 있음. **근거 없는 구간은 `not_collected` 로 강등**(설계됨). 샤드 `collected_at` 2일뿐이라 보수적으로만 | 판정 규칙만 바꿔 3단계 3테이블 재빌드 |
| **R-10** | 우선주 시총 누락 → 밸류 롱사이드 오류 | `v_firm_mktcap` 대 `price_daily.mktcap_krw` 차 (003540 55.9%) | `corp_ticker`(isin8, KR7 만) + `v_firm_mktcap` + EG3 | 뷰 수정만 |
| **R-11** | `coverage_gap`(2026-08-21~) 구간 데이터 없음 | `UniverseQuery.end > 2026-08-20` | EG-C ③ 이 거절. gap 행 생성 폐기(GAP-21) | KRX 재백필 후 `backfill_end` 갱신 → S02·S03 재빌드 |
| **R-12** | PR 수익률만 → 팩터별 성과 부호가 다르게 나옴 | 저변동성·밸류·모멘텀 결과가 문헌과 반대 | `EQUITY_HANDOFF.md` 고지 + `factor_readiness.caveat` | 없음(GAP-18) |
| **R-13** | 격자 하한 2010-01-04 로 공매도·신용 시작연도 축소 | `_reject/pre_calendar` 건수(short 58,211·credit 24,497) | `dataset_profile.coverage_from` 에 **원장 하한과 격자 시작 2열** 기록 · `FACTORS.md` §0 정정 | 캘린더를 KRX 이전으로 늘리려면 가격·유니버스가 없어 횡단면 불가 — 되돌리지 않는다 |
| **R-14** | duckdb 매크로 절대경로 → 서버·로컬 이식성 | 로컬에서 `No files found`(P1c 실측) | 카탈로그를 **빌드마다 재생성**(임시 파일 → `os.replace`), 경로는 `MANIFEST.current_build` 에서 조립 | 카탈로그 재생성 1회 |
| **R-15** | `open` NULL 로 체결 불가(GAP-14) | `Bar` 생성 실패율, `dropped_rows` 급증 | S04 통과 조건에 행수 기록 · S07 에서 `OhlcPolicy` 명시 선택 | `CLAMP` 로 전환(보정 행 수 보고 필수) |

---

## 6. 완료 정의(DoD)

### 6-1. 슬라이스 공통 (모든 PR)

측정 가능한 조건만. 하나라도 미충족이면 미완.

1. 선언한 테이블의 `MANIFEST.json` `current_build` 존재 ∧ `builds[].gates` 에 fail 0 (`skip` 은 사유 문자열 필수).
2. `BuildRecord.inputs` 의 모든 stage build 가 `_pinned/` 에 실존(EG0).
3. `data/equity/fixtures/<table>.json` 존재 ∧ 그 픽스처로 도는 테스트 전량 green(EG4).
4. 같은 `inputs` 재빌드에서 파티션 `content_hash` 전량 동일(EG5a).
5. `EQUITY_DESIGN.md` §기록에 **입력 build_id · 산출 행수 · 게이트 판정 · baseline diff · 빌드 시간·RSS** 5항 기재. "완료" 만 적는 것 금지.
6. 새로 만든 상수가 `baseline.json` 에만 있고 코드·본문에 없다(grep 으로 확인).

### 6-2. 단계별

| 단계 | DoD |
|---|---|
| **S00** | `check_field_map.py` 가 registry ↔ `EQUITY_FIELD_MAP.md` 차 0 을 반환 ∧ GAP 21건이 전부 슬라이스에 배정 ∧ 결정 5개 "확정" 표기 |
| **1단계(S01~S03)** | EG1 7식 성립 ∧ **폐지 909 전부 `delist_date` 보유(위반 0)** ∧ KR7 isin8 3,438 그룹당 보통주 1 ∧ `security_span` 비중첩 ∧ Σ n_days = listing∪etf 행수 ∧ `trading_calendar` = 4,094 + gap ∧ `induty_code` 공란 0 ∧ 고정 asof 표본이 `baseline.json` 에 등재 |
| **2단계(S04~S06,S03B)** | `price_daily` = 10,890,251 ∧ 시총 불변 이벤트 `price_factor × share_factor = 1` 위반 0 ∧ `v_firm_mktcap` = Σ 종류주 시총(003540 검증) ∧ 분할일 수정수익률 점프 ≤ baseline ∧ 조정 거래량 점프 ≤ baseline ∧ 같은 asof 2회 호출 결과 동일 |
| **S07** | `BUILDERS['equity_duckdb']` 등록 후 `test_bar_source_contract.py` 전 케이스 green ∧ EG-C ①②③④⑤⑩ pass ∧ 폐지 20종목 포함 BarQuery 가 OK 이고 반환 종목 집합 = 요청 집합 |
| **3단계(S08~S10)** | 격자 행수 = Σ_d 활성 주권 티커 수 ∧ **"미수집 → 0" 행 0** ∧ `evidence_rate` ≥ baseline ∧ 월별 커버율 ↔ 시장 월수익률 상관 절댓값 ≤ baseline ∧ 키움 12주체 합 = 0 ± baseline ∧ 키움⋈KIS 겹침 0 ∧ pre_calendar 격리 건수가 실측치와 일치(short 58,211·flow 7,609·foreign 67,994·credit 24,497) |
| **4단계(S11·S12)** | `available_date = rcept_dt ≥ period_end` 위반 0 ∧ 참조표 미스 0 ∧ `rcept_dt ≤ D` 판본 선택 전수(max 선택 0) ∧ 파생 컬럼 `available = max(구성 행)` 위반 0 ∧ **PIT 결측률 교차표(has_correction × 접수지연 분위수)가 baseline 에 등재** ∧ GAP-02 3계정 판정이 `factor_readiness` 에 반영 |
| **S13**(대기) | E-G6a `candidate_status ∈ {unique, multi_resolved}` ≥ 99% ∧ E-G7 `rm` 플래그 도달률 ≥ 99% ∧ S11 오판율이 baseline 에 등재 |
| **4B(S15·S16)** | 6테이블 EG1 성립 ∧ `row_kind='aggregate'` 제거 건수 기록 ∧ 롤링 2년 창 경계가 `dataset_profile.coverage_from` 에 |
| **5단계(S17·S18)** | 관측점별 `min(fetched_date)` 선택 전수 ∧ v3⋈WISE 겹침(09-01~02) 일치율 ≥ baseline ∧ **`obs_month` 로 계산한 G05 가 `v_consensus` 로 재현되지 않음**(EG-C ⑨) ∧ 시총 분위수별 커버율 기록 |
| **6단계(S19·S20)** | `lag_known=false` stage 테이블 중 `dataset_profile` 행이 없거나 `recommended_lag_days < 1` 인 것 = 0 ∧ `coverage_from` 전수 ∧ **EG-F: `factor_readiness` 54행, blocked 는 reason 필수, ready 는 `first_usable_date` 필수, `required_columns` 전량 실재** |
| **7단계(S21·S22)** | 워크벤치 contract suite 가 `ADAPTERS` 에 `equity_duckdb` 를 포함해 전량 green ∧ `EquityDataPort` 와 `RawObservationPort` 가 같은 (security, date, field) 에 같은 값·같은 `available_date` ∧ 미지원 필드가 catalog 에서 `unavailable`(mock fallback 0) ∧ `bootstrap/_container.py` 설정만으로 mock↔duckdb 전환 |

### 6-3. 전체 DoD (equity 층 "끝났다")

동시 충족:

1. **26 + 1(`factor_readiness`) 테이블** 전부 `MANIFEST.gates` fail 0.
2. **EG-F pass** — `factor_readiness` 54행, `status='ready'` 인 팩터가 §1-8 의 36개 이상, `blocked` 는 전부 `blocked_reason` 보유.
3. **EG-C ①~⑩ 전량 pass**.
4. 워크벤치 4포트 계약 테스트 전량 pass, `equity_adapter='duckdb'` 로 컨테이너 부팅.
5. **EG5(a)+(c)**: 같은 `inputs` 재빌드 해시 동일 ∧ 고정 asof 표본에서 설명 불가한 차이 0.
6. `EQUITY_HANDOFF.md` 에 (읽기 계약 · 테이블별 메모 · **팩터 ID × 컬럼 × 시작일 표는 `factor_readiness` 참조** · PR 수익률 고지 · 한계 목록 GAP-06/07/08/15/18)가 있다.
7. **MVP-B 백테스트 1회가 재현된다**: 같은 run manifest 로 두 번 돌려 `tape_hash`·최종 equity 곡선 동일.

---

## 7. 엔진 소비자 인터페이스 확인

### 7-1. 실제 소비 경로 (코드 확인)

```
frontend / HTTP
      │
strategy_workbench.application
  ├─ equity_workspace      → EquityDataPort        (snapshot / list_fields / load_universe / load_panel)
  ├─ factor_research       → FactorMetadataPort    (resolve_factor_fields)
  │                        → FactorObservationPort (load_factor_observations)
  ├─ portfolio_design      → RawObservationPort    (load_raw_observations)   ← 진짜 파이프라인
  └─ backtest_run          → BacktestDataPort      (load_backtest_dataset)
      │
   [ 하나의 어댑터가 5메서드군 전부 구현 ]  ← 현재 MockEquityDataAdapter
      │
backtest_engine.ports  (BarSource / UniverseSource / CorporateActionSource / SlippageModel)
```

- `MockEquityDataAdapter`(`adapters/outbound/equity_mock/_adapter.py`)가 `snapshot`·`list_fields`·`resolve_factor_fields`·`load_universe`·`load_panel`·`load_factor_observations`·`load_raw_observations`·`load_backtest_dataset` **8메서드**를 한 클래스로 구현한다.
- 조립 지점은 `bootstrap/_container.py:55-63` 한 곳: `equity_adapter: str = "mock"`, 다른 값이면 예외.
- 계약 테스트 파라미터화 지점: `backend/tests/contract/test_raw_observation_port.py:38` `ADAPTERS = [pytest.param(MockEquityDataAdapter.demo(), id="mock")]`.
- 엔진 커널의 3포트는 **그 아래**에 있다. `BacktestDataPort` 가 `MarketBarRecord`·`UniverseMembershipRecord`·`CorporateActionRecord` 를 내면 `adapters/outbound/backtest_engine` 이 커널 타입으로 변환한다.

**따라서 `EQUITY_WORKFLOW.md` §0-1·결정 5 의 "엔진 어댑터 = 가격·유니버스·CA 3포트, 재무·컨센서스는 팩터층이 엔진 밖에서 주입" 은 소비 경로를 절반만 본 결론이다.** 재무·컨센서스는 `RawObservationPort`/`EquityDataPort` 로 **어댑터가 직접 낸다**. 팩터 값은 `domain/factor/_evaluation.py` 가 계산하고 어댑터는 raw 만 준다(포트 docstring: "hands over only *raw* facts, never factor values").

### 7-2. 팩터 재료를 받는 형태 (FactorGraph 쪽)

- `FactorGraph` = `FieldNode(field_id)` → `TimeSeriesNode`/`BinaryNode`/`CrossSectionalNode`/`GroupNode` DAG, 출력 1노드(`domain/factor/_nodes.py`).
- 실행 계획 `compile_factor_plan` 이 `required_field_ids`·`minimum_history_sessions`·`as_of_policy="available_date_lte_as_of"` 를 고정하고 `plan_hash` 를 만든다(`_planning.py`).
- 캐시 키 = `(data_snapshot_id, plan_hash, registry_version, parameters, as_of_start, as_of_end)`. **`data_snapshot_id` 는 어댑터 소유**(P1.5-01) → equity 의 `MANIFEST` build_id 가 여기 들어가야 재현성이 성립한다.
- 관측 단위: `FactorObservation(as_of, security_id, fields, references, forward_return, universe_member)` — **행 = (as_of, security)**, 필드는 `(field_id, value)` 목록.
- `RawObservationSet` 은 그보다 엄격하다: `sessions`·`history_sessions`(워밍업) 를 **어댑터가 선언**하고, 선언 밖 날짜가 관측에 있으면 `__post_init__` 이 거절한다("The evaluator counts lag and rolling windows by row position, not by calendar"). → **equity `trading_calendar` 가 곧 `sessions` 다.**
- 결측 표현: `value=None` = "원천이 관측한 결측", **필드 자체를 생략** = "as_of 시점에 공표된 것이 없음". equity 의 `fill_kind` 4종을 이 2단계로 접어야 한다 — 매핑 규칙이 없다(§7-4 참조).

### 7-3. field_id ↔ equity 대응 (42건, `EQUITY_FIELD_MAP.md` 초안)

판정: **지원** / **부분**(값은 있으나 정의·단위·커버가 제한) / **미지원**(catalog 에서 unavailable 선언).

| field_id | equity 산출 | 판정 | 비고 |
|---|---|---|---|
| `price.close` | `price_daily.close`(원주가) | **부분** | **핵심 충돌**: registry `price.momentum_12_1` 은 조정가를 전제한다. 원주가를 그대로 주면 분할 구간 모멘텀이 틀린다. **해소안: `price.close`(원주가) + `price.adj_close`(`v_adj_price`, base = query.end) 2필드로 분리하고 엔진 registry 의 가격 그래프를 `price.adj_close` 로 바꾸는 이슈를 엔진 저장소에 발행** |
| `price.open` | `price_daily.open` | **부분** | NULL 유지 정책 · `Bar.open` 은 필수·>0 (GAP-14) |
| `price.volume` | `price_daily.volume_shr` / `v_adj_volume` | 지원 | 조정 여부 명시 필요 |
| `price.market_cap` | `price_daily.mktcap_krw` / `v_firm_mktcap` | 지원 | 랙 1세션(익일 지식) |
| `price.shares_outstanding` | `price_daily.shares_out` | 지원 | |
| `price.trading_value` | `price_daily.value_krw` | 지원 | |
| `benchmark.close` | `index_daily.close_pt` | **미지원(현 설계)** | GAP-09 — security 축이 아님 |
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

집계: **지원 17 · 부분 15 · 미지원 9 · 미확인 1**.

### 7-4. equity 뷰/어댑터가 그 형태를 지원하는가 — 갭

| # | 갭 | 근거 | 해소안 |
|---|---|---|---|
| **E-1** | **뷰 시그니처가 포트와 맞지 않는다.** equity 뷰는 `v_fin_latest(asof, lag)` 처럼 **asof 1개**를 받는 스칼라 질의다. `RawObservationQuery` 는 `[start, end]` 전 세션 × 전 종목 × 전 필드 패널을 한 번에 요구하고, 세션마다 다른 as-of 컷오프를 적용해야 한다 | `EQUITY_DESIGN.md` §5 · `raw_observations.py` `RawObservationSet` | 어댑터가 뷰를 세션마다 호출하면 O(세션수) 왕복이라 실용 불가. **`v_panel(start, end, field_ids, lag_overrides)` 형태의 패널 매크로를 §5 에 추가**하거나, 어댑터가 `available_date ≤ as_of` 를 **한 번의 as-of 조인**으로 푸는 SQL 을 소유한다(권고: 후자, 뷰 계약을 늘리지 않음) |
| **E-2** | **결측 어휘 매핑 부재.** equity `fill_kind ∈ {measured, src_omitted, empty_response, not_collected}` + stage `miss_kind` 6종 ↔ 엔진 `CellKind ∈ {OBSERVED, SOURCE_OMITTED_ZERO, MISSING, NOT_COLLECTED, COVERAGE_GAP}` ↔ `RawFieldValue` 의 (값 None / 필드 생략) 2단계 | `EQUITY_DESIGN.md` §3 · `domain/equity/_models.py` `CellKind` · `raw_observations.py` docstring | `EQUITY_FIELD_MAP.md` 에 3열 매핑표 고정: `measured→OBSERVED` · `src_omitted→SOURCE_OMITTED_ZERO`(값 0) · `empty_response→MISSING`(값 None) · `not_collected→NOT_COLLECTED`(값 None) · `status='coverage_gap'→COVERAGE_GAP`. `RawObservationPort` 에서는 OBSERVED/SOURCE_OMITTED_ZERO 만 값을 싣고 나머지는 **필드 생략** |
| **E-3** | **`security_id` 축 미정의** | mock `"sec-005930-1"` vs equity `ticker`+`span_seq` | GAP-11 — `"{ticker}:{span_seq}"` 고정 |
| **E-4** | **`universe_id` 축 미정의** | `test_raw_observation_port.py:32` `"krx.common-stock"` vs `universe_policy.policy` | GAP-12 |
| **E-5** | **`data_snapshot_id` 규약 없음** | `FactorMatrixCacheKey` 가 이걸 재현성 키에 넣는다 | equity `MANIFEST` build_id 를 그대로 쓰되, **테이블마다 build 가 다르므로 조합 해시**가 필요. `equity.duckdb` 카탈로그 생성 시 전 테이블 build_id 의 정렬 해시를 `snapshot_id` 로 발급 — S21 통과 조건에 추가 |
| **E-6** | **`DatasetFieldProfile` ↔ `dataset_profile` 컬럼 불일치** | 엔진: `available_date_basis`·`recommended_lag_sessions`(**세션 수**)·`FieldCoverageCapability(starts_on, ends_on, venues, estimated_coverage_pct, supported_cell_kinds, point_in_time, requires_confirmation)` / equity: `basis`·`recommended_lag_days`(**일수**)·`coverage_from/to`·`universe_coverage_pct` | **랙 단위가 일 vs 세션으로 다르다** — 조용히 어긋나면 PIT 가 틀린다. `dataset_profile` 에 `recommended_lag_sessions` 를 **추가 컬럼**으로 두고 `trading_calendar` 로 환산해 저장. `supported_cell_kinds`·`point_in_time`·`requires_confirmation` 3컬럼도 추가 → **S19 산출 변경** |
| **E-7** | **`OhlcPolicy` 선택 미정** | `ports/market_data.py` `OhlcPolicy ∈ {STRICT, CLAMP}`; KRX 원장에 close>high 행 존재 가능 | S07 통과 조건에 정책 선언 + `repaired_rows`·`dropped_rows` 보고 |
| **E-8** | **`sessions` 소유자 미선언** | `RawObservationSet` 이 세션 축을 어댑터에서 받는다 | `trading_calendar` = `sessions` 임을 `EQUITY_HANDOFF.md` 계약으로 명시. 워밍업 `history_sessions_before_start` 는 `minimum_history_sessions`(팩터 252 등)만큼 캘린더에서 역산 |
| **E-9** | **`previous_weight`·`forward_return` 은 equity 소유 아님** | 포트 docstring: 답할 수 없으면 0.0 | 어댑터가 `previous_weight=0.0`·`forward_return=None` 고정. `EQUITY_FIELD_MAP.md` 에 기재 |

### 7-5. 결론

- **지원됨**: 유니버스(구간→`Membership`·`UniversePoint`) · 가격 3포트 · 필드 카탈로그 골격 · PIT 컷오프 사상 · 결측 5분류의 개념 대응.
- **갭 9건(E-1~E-9) + 필드 25건(부분 15 + 미지원 9 + 미확인 1)**. 이 중 설계 변경이 필요한 것은 **E-1(패널 질의)·E-6(랙 단위·capability 컬럼)** 둘뿐이고, 나머지는 어휘 고정(문서)과 어댑터 구현으로 끝난다.
- **가장 위험한 단일 항목은 `price.close`의 조정 여부**다. 원주가 불변(equity 원칙 ②)과 팩터의 조정가 요구가 정면으로 만나는 지점이고, 조용히 원주가를 흘리면 **분할 구간 모멘텀이 틀린 채 게이트를 전부 통과한다**. `price.adj_close` 필드 분리 + `factor_readiness` 의 M01 행에 `required_columns` 로 못 박는 것이 방어다.

---

## 8. 부록 — 이 제안서가 바꾸자고 하는 것 (무엇을 · 왜)

| # | 무엇을 | 왜 |
|---|---|---|
| 1 | 결정 5 재기술(엔진 3포트 → 커널 3 + 워크벤치 4, 단일 어댑터) | 소비자 코드에 이미 4포트가 있고 mock 이 재무·컨센서스를 낸다(§7-1) |
| 2 | `universe_daily` 를 S03(상태) / S03B(시장 파생)로 분리 | 1단계가 2단계 산출에 의존하는 순환 제거(§2-1) |
| 3 | 어댑터 S07 을 2단계 직후로 이동 | EG-C 가 1·2단계 통과 조건인데 검증 코드가 7단계에 있는 순환 제거(§2-3) |
| 4 | `disclosure_version` 을 S11(근사)/S13(확정 링크)로 분리 | 문서층 P1 산출을 쓰지 않는 의존 누락 해소, 오판율 기준 확보(§2-4) |
| 5 | 4·4B·3단계를 9슬라이스로 분해 | 슬라이스 = 테이블 1~3개 규칙 준수, 실패 원인 좁히기(§2-5) |
| 6 | `factor_readiness` 테이블 + 게이트 EG-F 신설 | 사용자의 목적("54 재료 충분한가")을 pass/fail 로 판정할 술어가 없었다(§2-8) |
| 7 | `EQUITY_FIELD_MAP.md` + CI 대조 스크립트 | registry 42 field_id 와 equity 컬럼 대응이 어디에도 없다(GAP-13) |
| 8 | `krx_kis_close_ratio` 컬럼 삭제, `universe_policy` 임계 연기 | 이른 최적화 — 재현성 축만 늘린다(§2-6) |
| 9 | coverage_gap 행 생성 폐기 | 만들고 소비를 금지하는 데이터, 등식도 항등이 아니다(GAP-21) |
| 10 | 고정 asof 표본을 S02 시점에 baseline 등재 | as-of 불변은 PIT 층의 존재 이유인데 마지막에 잰다(§2-7) |
| 11 | `dataset_profile` 에 `recommended_lag_sessions`·`supported_cell_kinds`·`point_in_time`·`requires_confirmation` 추가 | 엔진 `DatasetFieldProfile` 과 랙 단위(일 vs 세션)가 다르면 PIT 가 조용히 틀린다(E-6) |
| 12 | `disclosure_version` 에 `legal_deadline`·`delay_days` 추가 | `FACTORS.md` §7 의 공시지연 파생 팩터(E07 선행 신호)를 담을 곳이 없었다(GAP-05) |
| 13 | `FACTORS.md` §0 시작연도 2열화 | equity 격자 하한 2010-01-04 로 공매도 2008·신용 2007 이 잘린다(GAP-19) |
| 14 | `MonthEndSession 미구현` 문구 정정 | 소비 경로(`_rebalance_pairs`)와 무관하다(§1-9) |
