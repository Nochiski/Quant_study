# 영역 H — 연구 소비자의 PIT·계약 (전수 조사 2차)

- 시작: 2026-10-06 03:47 KST
- 범위:
  1. 워크벤치 어댑터 `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/` (`_specs.py` FieldSpec 전부, `_adapter.py` 조회 경로) ↔ 서버 현판 `dataset_profile`·실제 열 의미
  2. 백테스트 커널 어댑터 `backend/src/backtest_engine/adapters/equity_duckdb.py` (가격·수정주가·기업행위 조정 방향, 폐지·정지, 생존 편향)
  3. 로컬 동기화 (`database/scripts/fetch_equity_local.sh`, main 의 ledger_sync.sh, 맥 로컬 사본 판 상태)
  4. 실데이터 대조 (서버 현판에서 어댑터 쿼리 조건으로 표본 조회)
- 정본 코드: `~/orca/workspaces/Quant_study/v3-merge` (feat/v3-merge, HEAD b220060a)
- 규칙: 읽기 전용. duckdb 메모리 연결 + read_parquet 만. 서버 쿼리는 05:30 KST 전까지.

## 결함

### H-01 워크벤치 컨센서스 필드가 '그날 처음 보인 행 묶음' 안에서만 FY1 을 골라, 몇 달 묵은 관측월 값을 현재 컨센서스로 낸다 [중]
- 상황: `v_consensus` 는 (ticker, obs_month, target_period, metric) 마다 **처음 보인 행 하나**만 남긴다(`database/src/equity/views.py:277-296`). 그래서 어떤 available_date 에 실린 행 묶음은 '그날 새로 보인 조합'뿐인 성긴 부분집합이다. 2026-09-01 WISE 첫 수집 묶음은 관측월 2023-09~2026-08 을 담았지만, 2026-04~08 관측월의 FY2026(202612) 행은 v3 가 먼저(04-03~) 보였으므로 이 묶음에 없다(005930 묶음: 2025-08~2026-03 은 202612·202712·202812, 2026-04~08 은 202712·202812 만).
- 인풋: 워크벤치 어댑터로 `consensus.forward_eps`·`consensus.forward_sales`·`consensus.eps_dispersion` 을 요청한다(`load_raw_observations`·`load_factor_observations`·`load_panel` 공통). 랙은 dataset_profile 의 1 세션이다.
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_specs.py:233-234·251-252`(`row_filter` + `pick_order="target_period ASC, obs_month DESC"`) → `_adapter.py:1432-1440`(`PARTITION BY ticker, available_date` 로 묶음마다 1행) → `_adapter.py:1495-1496`(컷오프 이하 **마지막 묶음**의 행). 정렬이 '최신 관측월'보다 '작은 target_period'를 앞세우고, 무엇보다 직전 묶음들의 상태를 합치지 않고 마지막 묶음 하나만 본다.
- 위험성: 그 세션에 이미 알려진 최신 관측월의 FY1 대신 몇 달~15개월 묵은 관측월 값이 '현재 컨센서스'로 나간다. 예) as_of 2026-09-02(컷오프 09-01): 005930 EPS 47,929(v3 08월) → **24,718(WISE 03월)** → 09-03 48,339, 000660 345,914 → **184,811** → 349,342. 매출도 005930 7,378,931 → 5,231,547(−29%) → 7,397,268(억원). 하루짜리 가짜 −48% 리비전이 생겨 리비전·선행 E/P 팩터가 그날 단면을 크게 오염시킨다 — 09-01 묶음에서 그때까지 EPS 값이 있던 807종목 중 488종목이 묵은 값(|오차| 중앙 15.9%, 20% 초과 209종목), 184종목은 값이 있는데 NULL 로 빠진다. 소수 종목은 15개월 묵은 값이 다음 묶음(10-02)까지 최대 19세션 이어진다(001800 관측월 2025-06 값, 최신 관측월 값은 NULL). 묶음이 다시 성기게 오는 날(신규 커버 종목·창 이동)마다 재발할 수 있다. 엑셀·모델 경로(fi/scope)는 이 어댑터를 쓰지 않는다. 카테고리: silent corrupt(연구 소비).
- 근거(서버 consensus_daily m_20261003T004552, v_consensus 본문을 read_parquet 위에 그대로 재현, as_of 2026-10-02, eps): 어댑터 규칙 pick 12,903건 중 '같은 시점까지 보인 최신 관측월의 FY1'과 다른 것 — stale_month 702건/694종목(값 있음↔값 있음 489건 504 종목·세션, 묵은 값↔최신 NULL 26건 237 종목·세션, NULL↔최신 값 있음 184건 184 종목·세션), 값 있는 오류의 |상대오차| 중앙값 15.9%. 2026-09-01 묶음 675건(최대 5개월 묵음), 09-03~10-01 묶음 27건(14~23개월 묵음). later_fy(FY2 를 FY1 자리에) 1,759건은 1,755건이 최신 값도 NULL 이라 실해 없음(값 있는 4건 66 종목·세션).
- 기존 여부: 신규. (rules_s17 선언 ③ 이 이 FY1 규칙을 '어댑터가 쓰던 규칙'으로 사양에 올렸지만, 어떤 관측월에서 고르는지는 정하지 않았다.)
- 보강(04:10): 워크벤치가 실제로 읽는 맥 로컬 사본(`~/quant-ledger/data/equity`, consensus_daily m_20260929T002135)에서도 같은 재현 — 005930 av 2026-09-01 pick = 202612/관측월 2026-03 24,717.89, 000660 184,810.59. 어댑터 단위 테스트(`backend/tests/test_adapters_equity_duckdb.py:344-363`)는 관측월마다 자기 공개일 묶음을 갖는 픽스처라 '성긴 묶음'을 재현하지 않는다.

### H-02 워크벤치 재무 필드는 '공개일이 가장 늦은 보고서'를 최신 재무로 내서, 옛 기간 정정이 접수되면 그 옛 기간 값으로 되돌아간다 [중]
- 상황: fin_std(`vintage_kind='api_restated'`)는 (법인, period_end, report_code) 마다 마지막 정정본 1행이고, 재무 항목을 고친 정정은 available_date = 정정 접수일이다. 그래서 이미 Q3 2023 이 공개된 법인에 FY2021 사업보고서 정정이 2023-12 에 접수되면, 시계열의 마지막 공개일 관측이 FY2021 이 된다.
- 인풋: 워크벤치 어댑터로 `financial.*` 8필드(revenue·gross_profit·operating_income·net_income·operating_cash_flow·total_assets·total_liabilities·book_equity)를 요청한다(랙 1 세션).
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_specs.py:202-220`(fin 원천 — `pick_order="period_end DESC, report_code DESC"` 는 **같은 접수일 안에서만** 최신 기간을 고른다) → `_adapter.py:1432-1440`(PARTITION BY corp_code, available_date) → `_adapter.py:1495-1496`(컷오프 이하 마지막 공개일 행). 기간 축(period_end)을 넘어선 '최신 기간' 판정이 없다. 팩터 경로(`_adapter.py:770-771` FactorFieldValue)와 raw 관측 경로(`:851-861` RawFieldValue)는 period_end(=source_effective_date)를 넘기지 않아 소비자가 되돌아감을 알아챌 수도 없다(load_panel 만 넘긴다).
- 위험성: 되돌아간 구간 동안 '최신 재무'가 옛 기간·다른 기간 길이 값이다 — 예) 001470 2023-12-12~2024-05-16: 매출 Q3 2023(3개월) 1,290억 → **FY2021 연간 3,570억**(2년 묵은 12개월 값), 269620 2024-01-05~05-16: 21.9억 → **169.7억**(7.8배). 매출·이익 기반 밸류·퀄리티 팩터의 단면 순위가 그 종목만 튄다. look-ahead 는 아니다(정정 값은 그날 공개됐다). 카테고리: silent corrupt(연구 소비).
- 근거(서버 fin_std m_20261003T004504, v_fin_latest 의 pick 을 read_parquet 위에 재현, as_of 2026-10-02): 어댑터 규칙 관측 92,864건 중 직전 관측들의 최대 period_end 보다 옛 기간으로 되돌아간 것 **975건 / 719법인**(1년 이상 되돌아감 177건), 활성 법인·일수 합 59,717일(중앙 43일), 그중 782건이 '분기 다음에 연간'(기간 길이 3→12개월). 연도별 28~159건(2016~2026)으로 매년 생긴다. CFS/OFS 공개일이 갈리는 키는 1건뿐이라 창 의존(fetch_end 로 매크로를 부르는 것)은 사실상 없음.
- 기존 여부: 신규. (기간 길이 혼재 자체는 `_FIN_PERIOD_NOTE` 가 밝힌 설계 — 이 결함은 '옛 기간으로의 역행'이다.)
- 보강(04:12) — 배당 필드도 같은 기제: `event.dividend_per_share` 도 (법인, 공개일) 묶음 + 마지막 공개일이라 옛 사업연도 정정이 접수되면 옛 연도 DPS 로 되돌아간다. 서버 dividend_event 관측 25,190건 중 직전 최대 bsns_year 보다 옛 연도로 역행 152건/141법인(값이 바뀐 것 49건), 활성 법인·일수 33,548일. 예) 00126955 2025-11-12~2026-03-18 DPS 3,300(FY2024) → **1,400(FY2022 정정)**, 00788773 2026-05-15~ 1,000(FY2025) → 800(FY2024).

### H-03 `event.dividend_per_share` 가 '연 1회(11011) 값'이라고 선언하지만 어댑터에 보고서 종류 필터가 없어, 반기·분기보고서 접수 뒤 연간 DPS 가 NULL 또는 중간배당으로 바뀐다 [하]
- 상황: 2026-03 부터 dividend_event 에 사업보고서(11011) 밖의 행(11012 반기 356행·11013 9행·11014 9행)이 실린다. 이 행들은 대부분 dps_krw 가 NULL 이고(11012 356행 중 값 7행) available_date 는 그 보고서 접수일(8월 등)이다.
- 인풋: 워크벤치 어댑터로 `event.dividend_per_share` 를 2026-03-17 이후 as_of 로 요청한다.
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_specs.py:278-297`(dividend 원천 `row_filter=None`, pick_order 의 `(dps_krw IS NULL)` 은 같은 접수일 안에서만 작동) · 설명 `:887-890`("연 1회(reprt_code='11011') 값") ↔ 선언 dataset_profile `event.dividend_per_share` evidence "연 1회(reprt_code='11011') 값이다". 컷오프 이하 마지막 공개일 = 반기보고서 접수일이라 `_adapter.py:1495-1496` 이 그 행을 고른다.
- 위험성: 반기·분기보고서를 낸 법인의 '최근 사업보고서 DPS'가 그날부터 다음 사업보고서(이듬해 3월)까지 NULL(MISSING)이 되거나 중간배당으로 바뀐다 — 배당수익률 팩터가 해당 종목을 조용히 빼거나 값을 1/2~1/3 로 줄인다. 행이 쌓일수록(반기·분기마다) 영향 법인이 는다. 카테고리: silent(데이터 손실·정의 혼입) + 문서 불일치.
- 근거(서버 dividend_event m_20261003, 어댑터 pick 재현 · 맥 로컬 사본 m_20260929 에도 11012 158행·11013 7행·11014 7행 존재): 비연간 보고서가 마지막 관측이 된 사례 — 직전 연간 DPS 가 있었는데 NULL 로 바뀐 법인 59(11012 56·11013 1·11014 2), 값이 중간배당으로 바뀐 법인 7(예 344820 1,600원 → 600원, 08-20~). 예) 170900 700원 → NULL(08-20~), 079550 2,950원 → NULL(08-25~), 451800 135원 → NULL(03-17~07-13).
- 기존 여부: 신규.

### H-04 백테스트 두 어댑터가 미해결 기업행위(factor_ok=false)를 경고 없이 버리고, 설계가 기대한 universe '사건 창' 보호도 보유 포지션을 못 막아 원주가 점프가 가짜 손익이 된다 [상]
- 상황: adj_factor 의 factor_ok=false 사건(krx_base_inconsistent·no_price_match·ratio_null·near_dup_suppressed 등)은 '사건은 실재하나 계수를 못 냈다'(EQUITY_DESIGN §4 adj_factor). 감자·병합·분할인데 계수가 없으면 원주가가 재개일에 수 배 뛴다. 설계(EQUITY_DESIGN:110 (5))는 "universe_daily `corp_action_window` 가 사건 전후 창에서 suspended 로 두어 엔진이 보유·진입하지 않게 한다"고 하지만, 창은 거래정지 구간에만 걸리고 정지 직전 마지막 거래일(d0)·재개 첫 거래일(d1)은 'listed' 다.
- 인풋: (1) 워크벤치 `load_backtest_dataset` 또는 커널 `EquityBarSource`+`EquityCorporateActionSource` 로, d0 에 보유한 종목이 미해결 감자·병합 뒤 d1 에 재개되는 창을 백테스트. (2) 워크벤치 `price.adj_close`(모멘텀·변동성 재료)를 같은 창에 걸쳐 요청.
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py:950-958`(`WHERE factor_ok` — 버린 미해결 사건 수를 세지도, 경고하지도 않는다; 경고는 reference·잠정·OHLC·정산불가 4종뿐 `:1071-1116`) · `backend/src/backtest_engine/adapters/equity_duckdb.py:649-651`(`factor_ok is not True` → continue, 상태·건수 없음) · `price.adj_close` 원천 `_specs.py:184-201`(price_adj_daily 의 `n_unadjusted_events` 를 읽지 않음 — 선언 `rules_s23.py:431-432` "n_unadjusted_events > 0 인 구간은 조정이 불완전하다 — 소비자가 거를 축이다").
- 위험성: 보유 포지션 수량이 조정되지 않은 채 재개일 원주가로 평가된다 — 무상감자·병합이면 수백% 가짜 이익, 분할이면 −50~−86% 가짜 손실. 결과는 '정상 완료 run'이라 아무 표식이 없다. 같은 점프가 `price.adj_close` 수익률에도 그대로 있어 팩터(모멘텀·저변동성) 단면이 그 종목에서 튄다. 감자 종목은 소형 부실주가 많아 소형·역발상 전략에서 수익을 부풀리는 방향으로 치우친다(상승 401 : 하락 159). 카테고리: silent corrupt(백테스트 손익).
- 근거(서버 adj_factor m_20261003T003851 + price_daily m_20261003T003809): not-ok 사건의 (마지막 거래일 d0, 재개 첫 거래일 d1) 창 중 그 창 (d0, d1] 안에 ok 사건이 하나도 없고 d0 종가 → d1 종가 |수익률| > 30% 인 것 **560건/453종목**(창 단위 중복 제거, 2016 이후 275건), 상승 401건 중앙 **+448%**, 하락 159건 중앙 −50%. 그중 d0·d1 둘 다 universe_daily status='listed' 인 것 **525건**(창 보호 밖), d0 에 krx.liquid 정책 술어를 만족한 것 227건. (처음 셈 — '±10일 안 ok 형제 없음' 기준 — 은 641건/494종목이었고, 창 안 ok 사건 기준으로 좁혀 다시 셌다.) 예) 083660 2026-07-10 230원 → 08-04 3,045원(+1,224%, capred krx_base_inconsistent), 291230 588 → 2,740(+366%), 276730 11,470 → 1,610(−86%, split no_price_match), 145210 2022 +2,711%(유동성 상위 25%). price_adj_daily 는 이 사건일 1,065건 전부 `n_unadjusted_events > 0` 로 표시하고 있다(소비자가 안 읽을 뿐).
- 기존 여부: 신규(소비자 측). 모델 경로의 같은 원인은 기존 D2-10·Q-5(D.md — scope 모멘텀), 사유 분류는 기존 C-05 와 맞닿는다.

### H-05 로컬 동기화 두 도구 모두 '한 시점의 판 집합'을 보장하지 못한다 — ledger_sync(main)의 빌드 창 경고 상수는 실제 빌드 시각과 어긋났고, fetch_equity_local.sh 는 드리프트 검사조차 없다 [하]
- 상황: 서버 equity 빌드는 표 30개를 10~15분에 걸쳐 차례로 커밋한다. 실제 시각(UTC, MANIFEST build_id): 저녁 e_ 13:16–13:26(09-28) → 13:45–13:55(09-30·10-01) → **13:51–14:01(10-02)**, 아침 m_ 00:29–00:39(10-01) · **00:38–00:49(10-02·10-03)**, 09-29 은 00:13 시작 후 fin_std·disclosure_version 재빌드가 **01:29–01:30** 까지 이어졌다.
- 인풋: (1) main `database/scripts/ledger_sync.sh sync` 를 서버 빌드 도중(예 22:50~23:01 KST, 09:38~09:49 KST)에 시작. 증분 동기화는 수십 초에 끝나므로(09-29 실행 29.1s) 남은 표가 커밋되기 전에 끝날 수 있다. (2) 이 브랜치 `database/scripts/fetch_equity_local.sh` 를 같은 시간대에 실행.
- 에러 위치: main `database/src/ledger_sync/__main__.py:62-66`(`BUILD_WINDOWS_UTC = (13:15–13:50), (00:00–00:35)` — 실제 빌드 꼬리 13:50–14:01·00:35–00:49 를 덮지 못해 경고가 안 뜬다) · main `database/src/ledger_sync/pull.py`(execute_plan 끝의 드리프트 검사는 '계획 이후 바뀐 표'만 잡는다 — 계획 시점에 이미 섞여 있던 판 집합, 동기화가 끝난 뒤 커밋된 표는 못 잡는다; `_READY.json`·basis 를 보지 않는다) · `database/scripts/fetch_equity_local.sh:38-42`(표마다 rsync, 잠금·드리프트·`_READY`·basis 확인 없음).
- 위험성: 일부 표는 새 판(D+1), 일부는 옛 판(D)인 로컬 사본이 '성공'으로 남을 수 있다. 예) price_daily 새 판 + adj_factor 옛 판이면 D+1 에 생긴 분할·병합이 백테스트에서 빠져 마지막 날 가짜 점프, trading_calendar 새 판 + universe_daily 옛 판이면 마지막 날 패널이 빈다. 워크벤치는 판 집합 지문(snapshot_id)을 카탈로그와만 대조하고 표 사이 판 일관성은 보지 않는다. 카테고리: 운영(판 섞임) + 문서 불일치(경고 창).
- 근거: 서버 MANIFEST 전 표 build_id 시각 집계(위 수치) · main `BUILD_WINDOWS_UTC` 원문. 맥 로컬 사본 `~/quant-ledger/data/equity` 는 지금 30표 전부 `m_20260929T0013~0022`(e1.20.0, basis morning, `_sync/last_run.json` drifted [] , 09-29 01:13:52Z 29.1s)로 **한 판 집합이고 저녁판이 아니다** — 다만 그 동기화가 그날 서버 빌드 꼬리(01:29 fin_std m_ 재빌드 e1.21.0) 16분 전에 끝나 그날 최종판보다 옛 fin_std·disclosure_version 을 갖고 있다(드리프트 미보고, 실해는 미확인).
- 기존 여부: fetch_equity_local.sh 부분은 기존 C.md 미조사 메모(섞인 판·잠정판 수신). ledger_sync 창 상수·계획 시점 섞임은 신규.

### H-06 워크벤치 유니버스 소속(universe_member)은 랙 0 으로 평가되는데, 정본 선언은 universe.* 를 1 세션 랙으로 둔다 — 같은 시총이 필드로는 1 세션, 유니버스 술어로는 0 세션 [하]
- 상황: dataset_profile 이 `universe.status`·`universe.admin_state`·`universe.adv20`·`universe.mktcap`·`universe.delist_signal` 을 전부 `recommended_lag_sessions=1` 로 선언한다(KRX 일별 마스터 `stg_listing_daily` lag_known=false). 어댑터는 필드 셀에는 대장 랙을 적용하지만, 정책 술어(`universe_policy` — krx.investable 의 `NOT admin_state`, krx.liquid 의 `adv20_rank_pct >= 0.5` 등)는 as_of 와 **같은 날** universe_daily 행 위에서 평가한다.
- 인풋: `load_raw_observations`(universe_id=krx.investable·krx.liquid 등) 또는 `load_factor_observations`(krx.common-stock) — 행마다 `universe_member` 가 나간다.
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py:1278-1280·1328-1333`(`member_expr` 를 `u.date = 세션` 행에 바로 적용, 랙 없음) ↔ 대장 랙 적용 `:1145-1153`(field_id 에만). 대장 내부도 엇갈린다: `price.trading_value` 랙 0 인데 그 20세션 평균 `universe.adv20` 은 랙 1, `price.market_cap` 1 = `universe.mktcap` 1.
- 위험성: 그날 생긴 관리종목 지정·정지·유동성 순위 변화를 그날 as_of 의 유니버스에 반영한다 — 선언 기준 1 세션 look-ahead. 실행 규약이 기본 `next_open`(`domain/portfolio/_models.py:105`)이라 실제 이득은 작다(관리종목 지정일 당일 중앙 −4.0%, 다음 날 −0.9%·평균 +1.3% — 랙 0/1 어느 쪽도 당일 하락은 못 피함). 계약 문서와 구현의 불일치가 요지이고, `same_close` 류 실행 규약을 쓰면 실해가 된다. 카테고리: 문서 불일치(층 간 계약) + 잠재 look-ahead.
- 근거(서버 universe_daily m_20261003, 2016~): 7,921,629 종목·세션 중 krx.investable 술어가 전날과 달라지는 경우 6,157(0.08%), krx.liquid 72,172(0.91%), 관리종목 지정 시작 902건(전날 investable 이던 333건의 당일 수익률 중앙 −3.99%). universe_daily.available_date = date 전 행(11,004,298). 저녁판·아침판 dataset_profile 랙 차이 0(84필드).
- 기존 여부: 신규.

### H-07 `list_fields()` 가 랙 숫자만 대장에서 가져오고 공시 기준·근거 문장은 어댑터 자체 문장(낡음)을 내서, 한 프로필 안에서 숫자와 설명이 서로 어긋난다 [하]
- 상황: 어댑터는 `recommended_lag_sessions`·`available_date_basis` 는 dataset_profile 에서 읽지만, `disclosure_basis`·`evidence`·`description` 은 `_specs.py` 의 고정 문장을 그대로 싣는다. 대장(소비자용 정본 선언)이 바뀌어도 어댑터 문장은 09-06 시점에 머문다.
- 인풋: 워크벤치 `list_fields()`(필드 카탈로그 화면·소비자 문서가 읽는 프로필).
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py:485-498`(`disclosure_basis=spec.disclosure_basis`, `evidence=spec.evidence`) ← `_specs.py:872-876`(credit "KIS 신용잔고는 실제로 **T+1 공표**이나 랙 축은 dataset_profile(S19)이 확정한다") · `:908-909`(buyback "event_type='tsstk_aq', 서버 1,951건" — 기존 C-08) · `:887-890`(dividend "연 1회(11011) 값" — H-03) · `:455`(price.close "정규장 종가 확정 시점" — 기존 C-02 와 같은 축). 대조: 대장 credit.margin_balance disclosure_basis "공표는 T+2 이나 우리 체인은 T+3 아침 06:00 KST 에 받는다 … 3 세션 뒤부터 쓴다".
- 위험성: 같은 프로필 행이 '랙 3 세션'과 'T+1 공표'를 함께 말한다. 소비자가 lag_overrides 를 설명 문장(T+1)에 맞춰 1 로 낮추면 화면 경고(lag_below_recommended)는 뜨지만, 근거 문장이 그 선택을 정당화한다. 대장 정정(DEFECT-E01, 09-19 credit 랙 1→3)이 소비자 문서에 반영되지 않는 구조다. 카테고리: 문서 불일치.
- 근거: 서버 dataset_profile m_20261003T004600 credit.margin_balance 행(랙 3·4일, 위 문장) vs `_specs.py` 원문. 어댑터 30필드 field_id 는 대장에 전부 있고(84행 중 30) 랙은 대장 값이 쓰인다(대장 행 있음 → 폴백 미사용).
- 기존 여부: 신규(개별 문장 중 tsstk_aq 는 기존 C-08).

## 요약(심각도순)
| ID | 심각도 | 한 줄 | 카테고리 |
|---|---|---|---|
| H-04 | 상 | 백테스트 두 어댑터가 미해결 기업행위(factor_ok=false)를 경고 없이 버림 + universe 사건 창이 보유 포지션을 못 막음 → 감자·병합 재개일 원주가 점프가 가짜 손익(560건/453종목, 상승 중앙 +448%, 525건은 universe 창 밖) | silent corrupt |
| H-01 | 중 | 컨센서스 FY1 을 '그날 처음 보인 행 묶음' 안에서만 골라 몇 달~15개월 묵은 관측월 값이 현재 값으로 나감(as_of 09-02 에 488종목 묵은 값·184종목 NULL, 005930 −48%; 전체 702건) | silent corrupt |
| H-02 | 중 | 재무 필드가 '공개일이 가장 늦은 보고서'를 최신으로 내 옛 기간 정정 접수 시 옛 기간 값으로 역행(975건/719법인, 중앙 43일; 배당도 같은 기제 152건) | silent corrupt |
| H-03 | 하 | DPS '연 1회(11011)' 선언인데 보고서 종류 필터 없음 → 반기·분기보고서 뒤 NULL·중간배당(66법인, 증가 중) | silent 손실·문서 |
| H-05 | 하 | 동기화가 한 시점 판 집합을 보장 못 함 — ledger_sync 빌드 창 상수 낡음·계획 시점 섞임 미검출, fetch_equity_local.sh 는 검사 없음 | 운영·문서 |
| H-06 | 하 | 유니버스 소속은 랙 0 평가, 대장은 universe.* 랙 1 — 계약 불일치(next_open 이라 실해 작음) | 문서 불일치·잠재 look-ahead |
| H-07 | 하 | list_fields 가 랙 숫자는 대장, 설명 문장은 낡은 어댑터 문장(credit 'T+1 공표' vs 랙 3) | 문서 불일치 |

## 기존 결함의 소비자 측 확인
- 기존 C-08: 어댑터 buyback `row_filter="event_type = 'tsstk_aq'"` 그대로(`_specs.py:309`), 서버 corp_event 어휘 treasury_buy 1,944·cb_issue 4,734·bonus 1,284·capred 1,158·reverse_split 411·split 372 — tsstk_aq 0 재확인.
- 기존 C-02 보강(가설: 팩터 관측 경로가 저녁 행을 안 거른다) — **반증**. 저녁판(e_20261002) trading_calendar·universe_daily·security_span 의 max(date) = 10-01(D), price_daily·price_adj_daily 만 10-02(T) evening 2,651행. 어댑터 `_window` 가 `end > backfill_end(캘린더 max)` 를 거절하고(`_adapter.py:1160-1164`) 격자 행이 universe_daily×security_span 에서만 나오므로(`:1328-1340`) T 행은 팩터·raw·패널 어느 경로로도 닿지 않는다. flow/short/credit 저녁판에도 T 행 0. 백테스트 경로는 basis≠'krx' 를 버린다(워크벤치 `:970-976`, 커널 `equity_duckdb.py:449-452`).
- 기존 C-07(무상증자 fold 지연 → price_adj_daily 하루 가짜 점프): 워크벤치 `price.adj_close`(팩터 경로)에는 그대로 들어간다. 백테스트 경로는 원주가 + apply_date 사건이라 영향 없음.
- 기존 C-04(미래 배정기준일 무상증자 effective=D): 큰 비율은 factor_ok=false 라 두 백테스트 어댑터가 버린다(실해 없음). 작은 비율(≤5.26%, factor_ok=TRUE)은 두 어댑터가 D 에 SPLIT(수량 ×1.0x)을 적용해 마지막 날 가짜 +r% — 코드 경로 확정, 현판 대기 사례 0(아래 가설 1).
- 기존 C-11(fin_std 원공시일 승계 look-ahead): 워크벤치 financial.* 이 서버 현판(e1.22.0)을 읽으면 그대로 들어간다. 맥 로컬 사본은 e1.20.0(m_20260929)이라 아직 승계 전 — **다음 동기화 때 로컬에 들어간다**.
- 기존 C-03(애프터마켓 포함 거래량·거래대금): price.volume·price.trading_value 랙 0 필드로 그대로 노출.

## 문제없음 확인
- 어댑터 30 field_id 전부 서버 dataset_profile 에 행이 있고(84행 중), 어댑터는 그 `recommended_lag_sessions` 를 쓴다(폴백 미사용): price.close/open/volume/trading_value·adj_close 0, market_cap·shares_outstanding 1, financial 8 = 1, consensus 6 = 1, flow 3·short 2 = 1, credit 3, event 3 = 1. 저녁판·아침판 대장 랙 차이 0. lag_overrides 음수는 도메인이 거절(`domain/equity/_models.py:146-150`), 권장보다 짧으면 경고+확인 요구(`_panel_preview.py:152-168`).
- 랙 3 세션(credit)이 실제 입수와 맞음: m_20261003(D=10-02) credit_daily measured 마지막 날 09-30(2,523), 10-01·10-02 0 → as_of 10-02 컷오프 09-29 는 이미 입수. flow·short·lending 은 D 까지 measured(키움 저녁 수집) → 랙 1 보수적.
- 격자 5표(price_daily·price_adj_daily·flow·short·credit) available_date = date 전 행(available_date > date 0, < date 0, NULL 0) — GRID 셀의 available_date ≤ as_of 가 랙만으로 선다.
- row_filter·pick_order·kind_expr 가 참조하는 어휘·컬럼 실재: consensus metric eps·revenue, holder src elestock(34,132)·majorstock, opinion coverage_degraded/src, dividend dps_krw·bsns_year·reprt_code·stock_knd, fill_kind 3종 컬럼. fill_kind 어휘(measured·not_collected·src_omitted·empty_response)가 어댑터 대응표와 일치, 값 있는 셀이 measured 아닌 경우 0(flow·credit).
- 단위: consensus eps 원 / revenue 억원이 v3·wise 두 원천에서 같고 겹친 키 값 비 중앙 1.0(eps 0.9999), opinion 목표가 원(005930 v3 456,875 / wise 488,409), flow 원 단위(005930 |외국인| 중앙 6,163억). 부호 뒤집힘 없음.
- corp_ticker 는 ticker 당 1행(5,119행, 중복 0) → `_corp_map` dict 가 결정적. security 도 ticker 당 1행 → `load_universe` 이름 조인 중복 없음.
- 전방 조정 방향: price_adj_daily 종목 첫 행 cum_share_factor = 1 이 5,119/5,119, 005930 2018-05-03 2,650,000 → 05-04 51,900×50 = 2,595,000, 2026-10-02 276,000×50. 커널은 원주가 + 사건(ratio = share_factor = 신주/구주, 엔진 수량 × ratio·평단 ÷ ratio)이라 조정 방향과 무관.
- factor_ok 2,036행: 어댑터 어휘·방향 규칙 위반 0(split·bonus ratio>1, reverse_split·capred <1, unknown_krx ≠1), apply_date NULL 0 → 두 백테스트 어댑터가 예외로 죽을 행 없음. 구간 밖 ok 사건 5행(폐지 공백기 — 설계상 미적용).
- 폐지·정지: 커널·워크벤치 모두 security_span(폐지 포함 1,184 delisted 구간)으로 유니버스를 만들어 생존 편향 없음. 보통주 마지막 구간 폐지 556건 중 마지막 거래일~구간 끝 20세션 초과 13건(60 초과 3) — 정지 동결 낙관 편향은 작다. 정지일(reference) 행 미방출.
- holder_daily available_date = rcept_dt 전 행, elestock 같은 날 같은 보고자 중복 7묶음뿐. dividend_event available_date < 정정 접수일 4행은 전부 dps NULL(실해 없음).
- opinion: v3(available 04-04~09-01, default/degraded)·wise(09-01~, measured) available_date = obs_date. v3 마지막 목표가가 있던 603종목 중 wise 행이 아예 없어 v3 값이 계속 이월되는 종목 2개뿐.
- v_fin_latest 를 window 끝(fetch_end)으로 부르는 창 의존: CFS/OFS 공개일이 갈리는 키 1건 → 사실상 창 독립. v_consensus 는 최초 관측 고정이라 창 독립.
- 알려진 설계(결함 아님): 격자 `src_omitted`(원천이 행을 안 준 = 사실상 0) 셀을 어댑터가 MISSING 으로 접는다 — short_daily 키움 공매도 3,602,686셀(전체 9,281,689 의 39%)·credit 103,562·flow 2,218. 대장 evidence 와 `_FILL_KIND_NOTE` 가 함께 밝히고 있다(도메인이 SOURCE_OMITTED_ZERO 에 값을 요구). 공매도 계열 팩터는 '0'이 아니라 결측으로 받는다는 점만 소비 측 유의.
- 커널 BarQuery 기본 `OhlcPolicy.STRICT`(`backtest_engine/ports/market_data.py:62`) — GAP-14 류 비양수·NULL 가격은 FORMAT_ERROR 로 드러난다(조용한 누락 아님). 워크벤치 백테스트 경로는 같은 행을 버리고 `equity.invalid_ohlc_rows_dropped` 경고로 센다.
- 맥 로컬 사본 `~/quant-ledger/data/equity`: 30표 전부 m_20260929(e1.20.0) 한 판 집합, `_catalog_meta.json` basis morning·snapshot 1070ad1f…, `_sync/last_run.json` drifted 없음 — 저녁판·섞인 판 아님(_READY.json 은 동기화 대상 아님). 서버 현판보다 1주 뒤(C-09 의 b_ 2표·e1.22.0 미반영).

## 가설
1. (기존 C-04 연장) 작은 무상증자(≤5.26%)가 공시~권리락 사이에 있으면 워크벤치 `load_backtest_dataset`·커널 `EquityCorporateActionSource` 가 apply_date = D 에 SPLIT 을 내 마지막 세션 보유 수량이 ×(1+r) 로 부푼다(가격 하락 없음). 코드 경로(`_adapter.py:950-1045`, `equity_duckdb.py:640-705`)는 확정, 현판 대기 사례 0 → 실재 재현 미확인.
2. fin 원천의 CFS/OFS 혼재: (법인, 기간, 보고서) 마다 CFS 우선으로 고르므로 시계열이 연결↔별도 사이를 오간다 — 1,140회/738법인(2024~ 299회/250법인), 전환 시 총자산 비 중앙 1.02·p10 0.85·p90 1.65. 대부분 연결 대상 변동(정상)일 수 있어 결함 여부 미판정. 어댑터는 fs_div_used 를 내지 않는다.
3. universe_daily.sec_type 이 security 현재값과 전 행 일치 — 일별 sec_type 이 시점값이 아니라 현재 라벨일 수 있다(SPAC 합병 뒤 'common' 소급 등). 사례 확인 못 함.

## 미조사
- 워크벤치 백테스트 엔진(포트폴리오 컴파일러)의 정지·폐지 포지션 처리 상세(어댑터 경고 문구만 확인).
- GAP-14 행(open NULL ∧ 거래량>0)의 서버 현재 건수 실측(코드만 확인 — 아래 문제없음).
- `load_universe`·`list_fields` 의 커버율 계산 정확도(PIT 무관).
- H-04 의 '실제 보유 확률'(어떤 전략이 d0 에 그 종목을 들고 있었는지) — 사건 수·유동성 분포까지만 측정.
- main `ledger_sync` 의 verify·hash 경로 자체 정확도(코드만 일부 읽음), Windows `ledger_sync.ps1`.
- 서버 루트를 워크벤치가 직접 읽는 경우: C-09(10-05 b_ 2표) 뒤 `_catalog_meta` snapshot 불일치 → 카탈로그 매크로 원천(financial·consensus) unavailable 예상(코드상), 서버에서 워크벤치를 띄우지 않아 미확인.

## 끝
