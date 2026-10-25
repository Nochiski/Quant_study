# 영역 J — 연구 DB 미조사 메우기 (3차)

- 시작: 2026-10-06 04:23 KST
- 범위:
  1. equity treasury_stock EG3 `n_ledger_total_mismatch` 651 원인 (G 미조사)
  2. fin_std 보조 계정 대응표(depreciation·interest_expense·borrowings·lease_liab) — `src/equity/rules_s12.py`·`sql/fin_std.sql` vs 원장 dart.db 원문 표본
  3. stage `rules_dart.py` DS005 밖 표(stg_fin·dividend·shares·audit·capital·holder 등)·`rules_krx.py` 행 단위 의미(단위·부호·날짜·정정)
  4. 워크벤치 백테스트(포트폴리오 컴파일러·실행) 정지·폐지 포지션 처리 (H 미조사)
  5. KIS 원장 금액 단위 미상 열(신용 금액·수급 매도/매수 대금·외국인 순매수 대금) (I 미조사)
- 정본 코드: `~/orca/workspaces/Quant_study/v3-merge` (feat/v3-merge, HEAD b220060a)
- 방식: 읽기 전용. 서버 sqlite `-readonly`/`mode=ro`, duckdb 메모리 + read_parquet 만.

## 결함


### J-01 treasury_stock 이 같은 접수 안 '같은 취득방법·주식종류의 서로 다른 행'을 한 행으로 접어 자사주 수량을 버린다 — JYP Ent. 2015~2023 기말 자사주 2,788,841주 → 2,418주 [하]
- 상황: 정기보고서 「자기주식 취득·처분 현황」은 같은 접수 안에 (acqs_mth1, acqs_mth2, acqs_mth3, stock_knd) 라벨이 같은 행을 여러 줄 둘 수 있다(예: '기타취득' 아래 '단수주 취득' 줄과 '합병으로 인한 자사주' 줄). stage `stg_tesstk`(m_20261003T001604_615596Z)는 두 줄을 다 싣는다. equity `treasury_stock`(m_20261003T004549_447636Z, e1.22.0)은 grain 에 rcept_no·행 순번이 없어 QUALIFY 로 한 줄만 남긴다.
- 인풋: JYP Ent.(00258689) FY2016 사업보고서 20170331003456 원장 `dart_tesstk` 원문 — 기타취득/기타취득/기타취득/보통주 두 줄: 기말 2,418(rm '단수주취득') · 2,786,423(rm '합병(기준일 2013.10.17)으로 인한 자사주'), 총계 2,788,841.
- 에러 위치: `database/src/equity/sql/treasury_stock.sql:67-73` — QUALIFY row_number() 정렬이 `available_date, rcept_no, stlm_dt, trmend_qy_shr NULLS LAST, …` 오름차순이라 같은 접수의 두 줄 중 **기말수량이 작은 줄**이 남고 나머지는 버려진다. 머리 주석 `:20-23` 은 "중복은 전부 stock_knd='-'·값 전 컬럼 결측"(절단본 58그룹 근거)이라 했지만 전량에서는 성립하지 않는다. 감시 `rules_s16.py:308-327` `n_ledger_total_mismatch` 는 기록형(임계 없음)이라 판이 그대로 통과한다.
- 위험성: 데이터 손실 / silent corrupt. equity 현판 JYP 2015~2023 `end_shr` = 2,418(실제 2,788,841·2,399,433), 2024 부터는 회사가 줄을 합쳐 2,399,433 → 연구 소비자가 자사주 변화를 보면 2024 에 가짜 239만 주 '증가'가 생긴다. FIELD_MAP `event.treasury_acquired/disposed/retired`, factor_readiness I03(자사주 순매입률)·I04(주주환원율)·I05(소각률)의 재료다. 현재 워크벤치 어댑터·fi 는 treasury_stock 을 읽지 않아 모델 영향은 0(그래서 하).
- 근거: 서버 duckdb(read_parquet) — 같은 접수·같은 grain 의 잎 행(소계 제외) 중 값이 있는 서로 다른 행이 2줄 이상인 그룹 **39**(11법인: JYP Ent. 9 · 이지바이오 6 · KG파이낸셜 5 · 세토피아 4 · 카이노스메드 4 · 영풍 3 · 영진약품 2 · EDGC 2 · 노블엠앤비 2 · 성신양회 1 · 골드앤에스 1), 버려진 기말 수량 합 약 3,301만 주. 값이 똑같은 줄 2개(진짜 중복일 수 있음) 101그룹 4법인. **EG3 `n_ledger_total_mismatch` 651 의 분해**: ① 원천 자체 불일치 518(같은 접수 stage 잎 합 ≠ 회사 총계 — 총계 칸 공란·회사 산수 오류, 예 00161462 FY2023 총계 기말 2,005,425 vs 잎 2,000,000) ② 총계와 잎의 stock_knd 라벨 불일치·잎 없음 44(예 파트론 FY2024 원장 stock_knd 칸에 수량 '5,941,120'·'5,383,910' 이 들어간 DART 원문 칸 밀림, '합계'·'총 계(a+b+c)') ③ 총계 행 2~3줄 56(정정 접수 2개가 둘 다 stage 에 있거나 같은 접수 안 총계 중복 — 지표가 총계는 접수를 가로질러 더하고 잎은 접힌 값을 써서 생기는 지표 자체의 거짓 불일치) ④ **접기로 생긴 손실 33**(stage 잎 합 = 총계인데 equity 잎 합 ≠ 총계 — 이 결함). 즉 651 의 대부분(①②③)은 원천·지표 구성 탓이고, 실제 산출 결함 신호 33 이 그 안에 묻혀 있다.
- 기존 여부: 신규. 같은 계열(같은 접수 안 다른 행 접기)은 G-08(ownership_snapshot), 첫 관측 판본 접기는 G-01·G-23 — treasury_stock 은 없음.

### J-40 stg_audit.adt_opinion_class 가 원문 '거절'(= 의견거절)을 'other' 로, '감사의견 : 적정 / 반기검토의견 : 범위제한한정' 을 '한정' 으로 분류한다 — fi audit_adverse 가 의견거절 법인에서 NULL(미상), 적정 법인에서 true [하]
- **상황**: stage 현판 stg_audit(m_20261003T001615_051462Z, 94,501행). 분류 술어는 공백 제거 뒤 `LIKE '%부적정%' → '%의견거절%' → '%한정%' → '%적정%'` 순서의 부분 문자열 매칭이고, 나머지는 'other'.
- **인풋**: 사업보고서(11011) 감사의견 원문이 한 단어 '거절'인 행(만호제강 00120872 FY2023 제71기(당기)·FY2024 전기 행, 세원이앤씨 00585219 FY2022·FY2023 당기, 레드로버 00364795, 폴루스바이오팜 00167280 FY2020·2021 당기, 엠피씨플러스 00302078 FY2020 당기 등) · 원문이 '감사의견 : 적정\n반기검토의견 : 범위제한한정'인 행(씨씨에스 00249441 FY2018 당기·FY2019·2020 전기/전전기).
- **에러 위치**: `database/src/stage/rules_dart.py:348`·`:377-383`(분류 CASE — '의견거절' 전체 문자열만 보고 '거절' 단독·'〃'(위 행과 같음)·'공정'(=적정 문구)을 모른다, 검토의견 문구 안의 '한정'을 감사의견으로 잡는다) → 소비: `database/src/factor_inputs/queries.py:284-285`(class 가 'other' 면 audit_adverse NULL) → `database/src/model/engines/v4_rank.py:539-543`(NULL 은 '미상' 메모만 남기고 제외하지 않음).
- **위험성**: silent corrupt(분류 오류). 의견거절(상장폐지 사유) 법인이 v4 의 `audit_adverse` 제외를 통과하고(정지 표식이 따로 막지 않는 기간이 있으면 유니버스에 남음), 적정 의견 법인(씨씨에스 FY2018)이 '비적정'으로 빠진다. 엑셀의 '감사의견 비적정' 표식(deliver/common.py:63)도 같은 값을 쓴다.
- **근거**: 서버 dart.db `dart_audit.adt_opinion` 전수 분류(읽기 전용) — 적정 83,654 · NULL 9,060 · 의견거절 904 · other 633 · 한정 241 · 부적정 9. other 상위: '공정' 60 · '지적사항 없음' 33 · '예외사항없음' 21 · **'거절' 19** · **'〃' 18** · 공정 표시 문장류. stage 대조: 원문 '거절'·'〃'·'공정' 3종 + 검토의견 혼합 행 = 11011 의 other 97행(당기 라벨 37행, 법인 17곳, 최근 FY2024) · 한정 오분류 3행(씨씨에스). 'other' 는 원문 보존이라 데이터 손실은 아니지만, 소비층은 class 만 본다.
- **기존 여부**: 신규(G-01·G-02·G-09 는 감사의견 판본 접기·grain·정정 지연이고 분류 술어는 다루지 않음).

### J-02 KIS 신용 금액 6열의 단위는 09-09 에 '만원'으로 확정됐는데 stage·equity 코드 주석과 인계·필드 문서는 '단위 미상'이고, STAGE_SPEC 은 같은 절 안에서 표와 본문이 모순된다 — 2차 감사 I.md 의 '천원 추정'도 틀렸다 [하]
- 상황: `kis_credit_balance` 의 `whol_loan_{new,rdmp,rmnd}_amt`·`whol_stln_{new,rdmp,rmnd}_amt` 6열. STAGE_SPEC §2-4 표(`docs/STAGE_SPEC.md:132`)와 I4(`:611`)는 "09-09 확정: 만원·취득금액 기준(키움 ka10013 백만원과 정확히 100배)"이라 적고 "×10,000 → `_krw` 는 stage 규칙 판본 변경 후속"으로 미뤘다. 후속은 DECISIONS·TECH_DEBT 어디에도 등재돼 있지 않다.
- 인풋: 서버 원장 `kis.db`(mode=ro) 005930 2026-09-15 대주 신규 1,356주 × 종가 248,500 = 336,966,000원 vs `whol_stln_new_amt` 33,804(×1e4 = 338,040,000, 비 1.003). 20종목 2026-08~09 대주 신규 391행 `amt×1e4 ÷ (주수 × 당일 (고+저)/2)` 중앙값 **1.002**(p10 0.982 · p90 1.014), 대주 상환 417행 1.004, 융자 신규 808행 0.885(p10 0.836 · p90 0.926 — 융자금액 = 매수금액 − 현금보증금이라 1 보다 작다). 천원이면 대주 비가 0.10 이 되어 성립하지 않는다.
- 에러 위치: `database/src/stage/rules_kis.py:12-13`(docstring '원장 단위 미측정')·`:321-335`(`_unit_unknown`) · `database/docs/STAGE_SPEC.md:134-135`(본문 "어떤 10의 거듭제곱과도 맞지 않는다 … stcn×종가/amt 평균 11,596" — 같은 절 표 `:132` 의 만원 확정과 모순; 11,596 은 만원·취득가 기준이면 자연스러운 값) · `database/docs/STAGE_HANDOFF.md:40,103` · `database/docs/EQUITY_FIELD_MAP.md:60`(`amt_basis='unknown'`) · `database/src/equity/rules_s20.py:261` caveat · `database/src/equity/rules_s10.py:482-485` 기록형 지표 주석("원화면 1 에 몰려야 한다" — 실제 기대치는 약 0.9×1e-4) · 감사 기록 `docs/research/2026-10-06-pipeline-audit/I.md`(스크래치 I.md:129) "천원으로 추정되나 시가와 비례하지 않는다(담보비율 8~14% 꼴)".
- 위험성: 문서 불일치. 지금 신용 금액 열을 쓰는 소비자는 없어 값 오염은 없다. 그러나 저장소에 들어간 I.md 의 '천원' 서술이나 SPEC 본문을 근거로 환산하면 10배 틀린 신용잔고 금액(예: 삼성전자 융자잔고 5.03조 원을 5,030억 원으로)이 나오고, 확정된 단위가 장부에 후속으로 잡히지 않아 `credit.margin_balance` 의 금액축·신용잔고 금액 비율류 필드는 계속 '단위 미상'으로 막혀 있다.
- 근거: 위 인풋 실측(서버 sqlite mode=ro, 표본 20종목) + `docs/reviews/2026-09-09-kis-credit-balance-scope.md` 존재·SPEC I4 문구. 같은 표본법으로 KIS 수급의 '단위 미상' 열도 확정했다(백만원 — 문제없음 절).
- 기존 여부: 신규(I 미조사 항목을 메움 · I.md:129 서술 정정).

### J-60 정리매매 없이 상장폐지되는 종목(합병·주식교환·지주 전환·자진상폐)을 들고 있으면 워크벤치 백테스트가 그 포지션을 run 끝까지 마지막 체결가로 동결한다 — 인수 측 주식·현금으로 바뀌지 않고, 매 프레임 청산 주문은 'no bar' 로 취소되며, 결과에 표식이 없다 [중]
- **상황**: 워크벤치 백테스트(`BacktestEngineExecutorAdapter` → 커널, Rust·Python 코어 공통). 보유 종목이 정리매매 없이 상장폐지되는 경우다. equity `price_daily` 는 상폐 전 매매정지 구간을 reference 행(volume 0)으로만 갖는다. 어댑터는 이 행을 bar 로 내지 않으므로, 마지막 체결일 뒤 그 종목의 bar 는 다시 나오지 않는다. 커널에는 구간 끝(상폐)에서 강제 청산하거나 다른 종목으로 전환하는 처리가 없다. `Membership` 구간은 `ctx.universe()` 조회에만 쓰이고, `TargetTapeStrategy` 는 이것을 읽지 않는다.
- **인풋**: 예) `krx.investable` 또는 `krx.liquid` 유니버스로 2015-07~09 를 포함하는 대형주 전략을 돌린다. 이 전략이 003600 SK(2015-07-29 마지막 체결, 08-13 상폐, SK C&C 합병)나 000830 삼성물산(08-26 마지막 체결, 09-14 상폐, 제일모직 합병)을 들고 있고, 다음 프레임의 REPLACE 목표에서 그 종목이 빠진다.
- **에러 위치**:
  - 평가: `backend/src/backtest_engine/engine/portfolio.py:177-181` 의 `mark` 는 그날 bar 가 있는 종목만 갱신한다. 그래서 상폐 종목은 마지막 종가로 계속 평가된다. Rust `backend/rust/backtest_core/src/portfolio.rs:186` 도 같다.
  - 청산 시도: `engine/router.py:319-322` 에서 REPLACE 가 목록 밖 보유를 0 목표로 넣어 청산 주문을 만든다. 이 주문은 `engine/loop.py:592-607` 에서 DAY 주문으로 처리돼 'no bar for instrument in session' 으로 취소된다. 프레임마다 같은 일이 되풀이된다.
  - 목표 금액: `engine/router.py:348` 은 목표 금액을 `weight × portfolio.equity` 로 잡는다. equity 에 동결분이 들어 있어서 나머지 목표를 현금으로 다 사지 못한다.
  - 무표식: 워크벤치 쪽 `strategy_workbench/adapters/outbound/backtest_engine/_adapter.py:95-114` 는 bar 없는 보유를 현 수량으로 유지할 뿐이다. 데이터 경고(`strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py:1071-1116`)는 reference·잠정·OHLC·정산불가 사건 4종뿐이고, 상폐 보유 동결은 기록하지 않는다.
  - 회귀 테스트: `backend/tests/test_core_parity.py:299-302` 는 이 동작("바가 올 때까지 대기")을 'run 이 죽지 않는다'는 수준으로만 고정했다. 실례는 028150 GS홈쇼핑(2021-07 합병 상폐)이다.
- **위험성**: silent corrupt(백테스트 손익·노출).
  - 실제 주주는 인수 법인 주식(삼성물산→제일모직, 셀트리온헬스케어→셀트리온 등)이나 현금을 받는다. 백테스트는 그 자본을 수익률 0 인 죽은 자산으로 run 끝까지 들고 간다.
  - 그 결과 세 가지가 생긴다.
    1. 그 몫의 이후 등락·배당이 빠진다. 방향은 사건마다 다르다.
    2. 동결분이 든 equity 기준으로 비중을 잡아 살아 있는 목표를 다 못 사므로, 그만큼 노출이 빈다(현금 드래그).
    3. gross_exposure·포지션 목록에 죽은 포지션이 섞인다.
  - 장기·대형주 전략일수록 누적되는데, 결과 어디에도 표식이 없다.
- **근거** (로컬 equity 사본 m_20260929 의 security_span·price_daily·universe_daily, 읽기 전용):
  - 보통주 상폐 구간은 560 이다.
    - 정리매매(liquidation_window) 체결 bar 가 있는 것 183: 전부 마지막 bar 가 구간 끝이다. 정리매매 동안 중앙 −93.6% 하락이 bar 에 반영돼 있어, 동결해도 현실적이다.
    - 없는 것 **374**: 마지막 체결에서 상폐까지 간격이 0세션 236 · 6~20세션 125 · 21~60세션 10 · 60 초과 3 이다.
  - 이 374건 중 마지막 체결일 기준 시총 상위 200 안에 든 것이 **31건**, adv20 상위 절반이 96건이다. 예: 한국외환은행(2013) · 우리금융지주(2014) · SK·삼성물산(2015) · 우리은행(2019, 지주 전환) · 에스케이 머티리얼즈(2021) · 메리츠화재·메리츠증권(2023) · 셀트리온헬스케어(2023-12) · HD현대미포(2025-11) · HD현대인프라코어(2026-01).
  - 대형 10건 표본은 전부 마지막 체결 전 30일 동안 19~23세션이 `status='listed' ∧ NOT admin_state ∧ NOT liquidation_window` 였다(krx.investable 통과). 정책 유니버스가 미리 빼 주지 않는다.
  - 크기 감(추정): 시총 상위 200 에서 연 약 0.9% 의 종목이 이 경로로 빠진다. 20종목 동일가중이면 16년에 3건 안팎이 생기고, 건마다 그때 비중(약 5%)이 동결된다.
- **기존 여부**: 신규.
  - H 의 문제없음 절 "정지 동결 낙관 편향은 작다(마지막 거래일~구간 끝 20세션 초과 13건)"는 부실 상폐의 동결 가격만 본 것이다. 합병·전환 상폐의 자본 동결(수익률 0·현금 드래그)은 다루지 않았다.
  - H-04(계수 없는 사건 구간의 가짜 손익)와는 원인 축이 다르다.

### J-20 fin_std `depreciation` 이 대부분 '판관비 안 감가상각비'(SG&A 몫)만 잡는다 — 매출원가 쪽 감가상각이 빠져 EBITDA 재료가 1/4~1/20 로 작다 [하 · 잠복]
- **상황**: equity fin_std 현판 `m_20261003T004504_836809Z`(e1.2x) · stage stg_fin `m_20261002T234713_080258Z`. 기능별 손익계산서를 쓰는 법인은 감가상각비를 손익계산서 본문에 따로 적지 않고, **판매비와관리비 세부 줄**로만 적는다(2015~2022 태그 `dart_DepreciationExpense`, 2023~ `dart_DepreciationExpenseSellingGeneralAdministrativeExpenses`). 매출원가에 들어간 감가상각은 손익계산서 어디에도 없다.
- **인풋**: fin_std 빌드의 `_acct` 대응표 — `depreciation` tier b `concept=['DepreciationExpense']`(접두어 `dart_`/`ifrs-full_` 를 떼고 대조하므로 `dart_DepreciationExpense` 가 걸린다) · tier c `nm=['감가상각비및상각비','감가상각비']`(이름이 '감가상각비' 인 판관비 세부 줄이 걸린다).
- **에러 위치**: `database/src/equity/sql/fin_std.sql:77-79`(`_acct` depreciation 3줄) · `database/src/equity/rules_s12.py:113-117,125-128`(주석이 `dart_DepreciationExpense` 를 같은 개념의 '2차 태그'로 기술 — 실제로는 판관비 세부 항목).
- **위험성**: silent corrupt(잠복) · 문서 불일치. 사업보고서(11011, 12개월) 기준으로 fin_std `depreciation` 을 같은 보고서 현금흐름표의 감가상각 조정액(`AdjustmentsForDepreciation*`, 같은 12개월)과 견주면 tier b 승자 804행 중 대조 가능 614행의 **중앙값 0.234**, **447행(73%)이 절반 미만**이다. tier c 의 판관비 태그 승자 53행은 중앙값 0.122. tier a(`DepreciationAndAmortisationExpense`, 17행)만 1.21(무형자산 상각 포함)로 정상 범위다. 즉 한 열에 '감가상각+상각'(a) · '감가상각만'(b 일부) · '판관비 몫만'(b 대부분·c)의 세 정의가 행 표식 없이 섞인다. 현재 fi·모델·워크벤치 어댑터는 이 열을 읽지 않지만(grep 0), `factor_readiness` 가 V05 EV/EBITDA 를 이 열로 `ready` 라고 선언한다(J-23). 이 열로 EBITDA 를 만들면 감가상각이 큰 장치산업의 EV/EBITDA 가 체계적으로 높게 나온다.
- **근거**: 로젠(00155948) FY2022 CFS — 원장 dart.db `dart_fin_raw`(rcept 20230323001139): CIS `dart_DepreciationExpense` '감가상각비' 6,107,801,399 · CF `dart_AdjustmentsForDepreciationExpense` '감가상각비' 26,024,124,774. stage 행 순서상 이 줄은 `dart_TotalSellingGeneralAdministrativeExpenses`(판매비와관리비) 바로 뒤의 세부 항목이다. fin_std `depreciation` = 6,107,801,399(비 0.235). 같은 꼴: 미원에스씨(01234297) FY2022 0.86억×10 vs CF 200.6억(0.043) · 한국알콜(00158565) FY2022 30.3억 vs 122.3억(0.248) · 미원홀딩스(00706715) 0.129. tier b 히트 법인 수는 2015~2022 연 70~139곳, 2023~ 연 11~12곳(2023 택소노미 개편 뒤 판관비 줄은 tier c 이름 일치로 일부만 걸림 — 이름이 '감가상각비,판관비' 인 19곳은 안 걸림).
- **기존 여부**: 새 결함(A~I 에 없음 — G 미조사 '보조 계정 대응표'의 실측).

### J-21 fin_std `borrowings`(선언 '차입금', V07 '총차입금')는 금융사 '차입부채' 아니면 비금융사의 **차입금 한 구성요소**(단기·유동성장기 등)다 [하 · 잠복]
- **상황**: fin_std 현판 `borrowings` non-null 1,491행(커버율 1.59%). 비금융 법인 일부가 재무상태표의 **단기차입금 한 줄**(또는 유동성장기차입금·장기차입금 한 줄)에 일반 태그 `ifrs-full_Borrowings` 를 달고, 나머지 차입 줄에는 `ShorttermBorrowings`·`dart_LongTermBorrowingsGross`·`CurrentPortionOfLongtermBorrowings`·`BondsIssued` 를 단다.
- **인풋**: `_acct` `borrowings` tier a `concept=['Borrowings']`(pick) · tier c `nm=['차입부채']`. 다른 차입 태그는 합산하지 않는다(이중계상 회피 설계).
- **에러 위치**: `database/src/equity/sql/fin_std.sql:100-101` · `database/src/equity/rules_s12.py:118-121,129`(설계 주석은 '커버율이 낮다'만 인정하고 '잡힌 값이 부분합'인 경우는 다루지 않음) · 선언 `rules_s12.py:905-907`('차입금') · `rules_s20.py:115-116`(V07 = (현금 − **총차입금**)/시총).
- **위험성**: silent corrupt(잠복) · 문서 불일치. 전 기간 non-null 1,491행의 승자 줄 이름: '차입부채'(금융사) 959 + 'III.차입부채' 70 + '4.차입부채' 7 ≈ 1,036 · **'단기차입금' 239 · '단기차입부채' 37 · '유동성장기차입금' 27 · '장기차입금' 10 · '단기차입금및사채'·'단기금융부채' 각 7 · '유동성장기차입부채' 4** ≈ 331행(22%)이 차입금 총액의 한 조각이다('차입금' 72·'차입금(사채포함)' 42 는 유동 구역 한 줄일 가능성 — 미판정). 소비 선언은 '차입금'·'총차입금'이라 순현금비율(V07)·EV(V05)·NOA(Q08)에서 순차입금이 과소, 순현금이 과대로 나온다. 금융사 959행은 경제적 의미(예수금 외 조달)가 달라 V07 횡단면에 섞이면 안 되는데 표식이 없다.
- **근거**: 원장 dart.db JW생명과학(00225706) FY2024 11011(rcept 20250318001174) BS — `ifrs-full_Borrowings` '단기차입금' 27,700,000,000 · `dart_LongTermBorrowingsGross` '장기차입금' 31,899,000,000 · `ifrs-full_BondsIssued` '사채' 7,943,835,911(+ stage `dart_CurrentPortionOfBonds` '유동성장기부채' 168,000,000) → fin_std `borrowings` = 27,700,000,000(총 67.7억×10 의 41%). 일성건설(00146232) FY2024 CFS: `ifrs-full_Borrowings` 가 '유동성장기차입금' 73,336,930,000 → fin_std 733.4억, 단기차입금 49.8억·장기차입금 515.9억 누락(56%). 화천기공(00166519) FY2024: 164.6억 vs 단기+유동성장기+장기 294.8억(56%). 아세아제지(00138729) FY2024: 638.3억 vs 671.0억. 또 397 그룹은 `Borrowings` 태그 줄이 2개 이상(391 그룹 값이 달라 pick → NULL).
- **기존 여부**: 새 결함.

### J-22 fin_std `lease_liab` 합산이 '한쪽만 표준 태그'인 법인에서 유동 또는 비유동 한쪽만 싣는다 — 3,112 그룹(357법인) 과소 [하]
- **상황**: 리스부채를 유동·비유동 두 줄로 적되 한쪽만 `ifrs-full_CurrentLeaseLiabilities`/`NoncurrentLeaseLiabilities` 를 달고, 다른 쪽은 `dart_NonCurrentFinanceLeaseLiabilities`·`dart_CurentPortionOfFinanceLeaseLiabilities`·`ifrs-full_OtherNoncurrentFinancialLiabilities`·표준계정코드 미사용으로 적는 법인.
- **인풋**: `_acct` `lease_liab` tier a `concept=['CurrentLeaseLiabilities','NoncurrentLeaseLiabilities']`(sum) · tier b `['LeaseLiabilities']`(sum) · **이름 폴백 없음**(`fin_map.py:88-97` `nm=[]`). `best_tier` 가 tier a 히트가 한 줄이라도 있으면 거기서 끝낸다.
- **에러 위치**: `database/src/equity/sql/fin_std.sql:98-99`(대응표) · `:221-262`(`best_tier`→`val` 의 `sum` — 그룹 안에 짝이 다 있는지 확인하지 않음) · `database/src/fin_map.py:88-97`(주석 "하나만 집으면 절반이 된다"가 바로 이 실패를 경고).
- **위험성**: silent corrupt. stg_fin 전 기간 리스 줄이 있는 46,329 그룹 중 **tier a 가 한쪽뿐이고 반대쪽이 다른 태그·이름으로 있는 그룹 3,112**(357법인, 연도별 2019 274 · 2020 436 · 2021 502 · 2022 498 · 2023 500 · 2024 428 · 2025 346 · 2026 123), 추가로 tier a 한쪽 + `LeaseLiabilities` 다른 쪽 209 그룹(tier b 무시). fin_std `lease_liab` non-null 40,006행의 약 8% 가 한쪽 값이다. 반대로 태그가 전부 dart_*FinanceLease*·비표준인 5,935 그룹은 NULL(커버리지 손실 — 결함 아닌 설계 범위). 소비처: dataset_profile `financial.lease_liabilities`(internal) · 옛 `build_views.py:41`.
- **근거**: 원장 dart.db 웅진(00143651) FY2024 11011 CFS(rcept 20250320001474): `ifrs-full_CurrentLeaseLiabilities` '유동성리스부채' 27,148,990,439 · `ifrs-full_OtherNoncurrentFinancialLiabilities` '장기리스부채' 14,189,921,543 → fin_std `lease_liab` = 27,148,990,439(총 413.4억의 66%). stage 대조: 윌비스(00104999) FY2024 68.1억 vs +비유동금융리스부채 102.1억(40%) · 케이비아이동국실업(00114765) 35.5억 vs 116.6억(30%) · 한세엠케이(00357360)·위지트(00258360)·선샤인푸드(01068658) 같은 꼴.
- **기존 여부**: 새 결함.

### J-23 factor_readiness 가 V05·V07·Q07·Q08 을 `ready`(시작일 2015~2016)로 내고 caveat 는 "커버 구간이 짧다"고 쓴다 — 실제는 행 커버율 1.6~4.8% 와 정의 불일치(J-20·J-21·J-24) [하]
- **상황**: equity `factor_readiness` 현판 `m_20261003T004716_317656Z` · `dataset_profile` 현판 `m_20261003T004600_516228Z`.
- **인풋**: S20 판정 규칙 — 요구 필드의 `estimated_coverage_pct = 0` 일 때만 `no_observations` 로 막고 그 밖은 `ready`, `first_usable_date` = 요구 필드 `coverage_from` 최댓값. 보조 3계정의 profile: `financial.borrowings` 1.590671% (1,491/93,734, coverage_from 2016-03-30) · `financial.depreciation` 3.714462% (from 2015-09-30) · `financial.interest_expense` 4.826544% (from 2015-06-29). 셋 다 `requires_confirmation=False` 라 `partial_support` 에도 안 걸린다.
- **에러 위치**: `database/src/equity/rules_s20.py:13-20`(판정 규칙: 커버율 하한이 0 하나뿐) · `:104-117`(V05·V07 caveat "차입금·감가상각은 … 커버 구간이 짧다 — first_usable_date 가 그 사실이다") · `:141-147`(Q07·Q08) · `database/src/equity/rules_s12.py:903-911`(보조 4계정 `_fin` 선언에 `requires_confirmation` 없음).
- **위험성**: 문서 불일치(소비자 오도). 현판 4행 모두 `status='ready'` · `first_usable_date` 2016-03-30(V05·V07·Q08)/2015-06-29(Q07) — caveat 가 가리키는 '짧은 커버 구간'은 09-06 절단본 실측(2023-11-14, `EQUITY_DESIGN.md` P39)의 흔적이고 서버 값과 반대다. 실제 제약은 ① 행 커버율 2~5%(유니버스 대부분 NULL) ② 잡힌 값의 정의가 선언과 다름(감가상각 = 판관비 몫 J-20 · 차입금 = 한 구성요소 J-21 · 이자비용 = 금융사 예금이자 J-24)인데 표 어디에도 없다. 팩터층이 이 표를 보고 V05·V07 을 만들면 2~5% 종목만 남은 데다 값이 체계적으로 치우친 팩터가 'ready' 근거로 나간다.
- **근거**: 서버 duckdb read_parquet — factor_readiness 행 `('V07','순현금비율',…,'ready',None,'factor_layer',2016-03-30,'차입금 커버 구간이 짧다 — first_usable_date 참조.')`, `('V05',…,'ready',…,2016-03-30,'차입금·감가상각은 fin_map 매핑이 늦게 붙어 커버 구간이 짧다 …')`, Q07 `ready` 2015-06-29, Q08 `ready` 2016-03-30. dataset_profile 위 수치.
- **기존 여부**: 새 결함(C-12 '문서 묶음'과 별개).

### J-24 fin_std `interest_expense` 는 행의 23% 가 금융사 '예수금 이자'(영업비용)이고, 그 일부는 음수 부호로 실린다 — 이자보상배율(Q07) 재료로 쓰면 부호·의미가 섞인다 [하 · 잠복]
- **상황**: 은행·보험·증권·VC 는 손익계산서의 '순이자손익' 세부로 `ifrs-full_InterestExpense` '이자비용'을 적는데, 일부는 비용을 **음수**로 적는다(같은 표의 다른 비용 줄도 음수). 비금융사의 이자비용은 대부분 `ifrs-full_FinanceCosts`(금융비용) 안에 있어 본문에 없다.
- **인풋**: `_acct` `interest_expense` tier a `concept=['InterestExpense']` · tier c `nm=['이자비용']`(pick, 부호 정규화 없음). 금융업 판정·표식 열 없음.
- **에러 위치**: `database/src/equity/sql/fin_std.sql:80-81` · `:248-256`(`pick` 은 원값 그대로) · 선언 `database/src/equity/rules_s12.py:910-911`('이자비용', Q07 재료) · `rules_s20.py:141-143`.
- **위험성**: silent corrupt(잠복). 사업보고서 승자 행 약 1,200 중 금융업(KSIC 64~66) 약 278(23%) — 예금이자라 '영업이익/이자비용' 의 의미가 없다. 전 보고서 `interest_expense < 0` **120행(36법인)** · `= 0` 102행 — Q07 = 영업이익/이자비용이면 음수 행은 부호가 뒤집힌다. 또 '금융비용'(금융원가 전체)에 `InterestExpense` 를 단 3행은 이자비용이 금융비용 전체로 커진다. 반대로 비금융사의 `dart_InterestExpenseFinanceExpense` '이자비용(금융원가)'(FY2024 36+3곳)는 이름이 달라 NULL — 비금융 커버리지가 더 낮아진다. 현재 이 열을 읽는 소비자는 없다(grep 0, J-23 의 `ready` 선언만 있음).
- **근거**: stage stg_fin(=원장 행) — 제주은행(00148832) FY2025 CFS(rcept 20260316000708): `ifrs-full_InterestExpense` '이자비용' −166,723,000,000 · `RevenueFromInterest` 329,645,000,000 · `InterestRevenueExpense` '순이자손익' 162,922,000,000 → fin_std `interest_expense` −166,723,000,000, `op_profit` 9,934,000,000. 서울보증보험(00112998) FY2025 −5,860,480,215 · 아주IB투자(00156895) −1,402,391,911 · TS인베스트먼트(00778235) −705,917,090.
- **기존 여부**: 새 결함.

> J2 표기 정정: J-20 의 미원에스씨는 fin_std 8.6억 vs CF 200.6억, J-21 의 JW생명과학 총차입은 약 677억(277억은 그 41%).

### J-41 E08 의 'rcept_dt 가 접수번호 날짜보다 과거면 늦은 쪽' 보정이 stg_disclosure.available_date 에만 걸려, 정기보고서 보조 7표·fin_std 는 원천 rcept_dt 를 그대로 공개일로 쓴다 — 박셀바이오 재제출 8건의 재무·감사·배당·자기주식·주식수·최대주주가 최대 654일 앞당겨 보인다 [하]
- **상황**: stage 현판(2.4.0, snap_20261002T232452Z)·equity 현판(m_20261003T0045xx). DART 목록 API 가 박셀바이오(01335851)의 정기보고서 8건을 2025-08-28 접수번호(20250828000123·…404·…446·…451·…453·…457·…461·…534)로 주면서 `rcept_dt` 는 원래 제출일(2023-11-13 ~ 2025-08-14)로 준다. 목록에는 그 기간 원래 접수번호가 없다(재제출본만 남음) — 원장 재무·보조표도 이 재제출본 값만 갖는다.
- **인풋**: stage 빌드(일일 체인) → `stg_rcept_dt_map`(rcept_dt 원문) 룩업으로 stg_fin·stg_dividend·stg_shares·stg_capital·stg_tesstk·stg_hyslr·stg_audit 의 available_date 부여 → equity fin_std 는 `stg_disclosure.rcept_dt`(원문 열)로 avail_dt 를 만든다.
- **에러 위치**: `database/src/stage/rules_dart.py:22-42`(STG_RCEPT_DT_MAP 값 = 원문 rcept_dt) · `:92-93`·`:142-143`(보조 7표 available = 룩업 rcept_dt — greatest 미적용) · 대조: `:496`(stg_disclosure 만 `greatest_ymd8`, `stage/build.py:397-405`) · `database/src/equity/sql/fin_std.sql:158-164`(dt CTE 가 `stg_disclosure.rcept_dt` 원문을 읽음)·`:275`(avail_dt).
- **위험성**: look-ahead. 2025-08-28 에야 존재한 판본(재제출본 — 감리 뒤 재작성이면 숫자도 다름)이 원래 제출일부터 보인다. stg_fin 787행(8접수, 최대 −654일: FY2023 사업보고서 값이 2024-03-19 부터), 보조 6표 각 8~85행(2접수 = FY2023·FY2024 사업보고서, −527일). equity 전파 확인: fin_std 8건(FY2023 OFS 연간 영업이익 −115억 등이 available 2024-03-19)·audit_opinion 6행·dividend_event 4·treasury_stock 32·shares_outstanding 6·ownership_snapshot 80행이 available_date < 접수번호 날짜(−527일). 설계 자신이 같은 현상을 look-ahead 로 규정했다(rules_dart.py:489-495 주석).
- **근거**: 서버 stage parquet 대조(읽기 전용, duckdb 메모리) — `available_date < strptime(substr(rcept_no,1,8))` 행: stg_fin 787 / stg_dividend 30 / stg_shares 8 / stg_capital 34 / stg_tesstk 36 / stg_hyslr 85 / stg_audit 12, 전부 corp 01335851. 같은 접수번호의 stg_disclosure.available_date 는 2025-08-28 로 올바르다(greatest). 참고: 같은 원문 rcept_dt 를 쓰는 `equity/sql/universe_daily.sql:96-100`(공시 신호) 은 현판에서 접두보다 이른 has_ticker 행 54건(28종목, 대부분 −1일) 중 신호 낱말(정지·관리·정리매매·상장폐지) 해당 0건이라 지금은 영향 없음.
- **기존 여부**: 신규. (C-11 은 fin_std `redate` 의 '정정본 → 원본 공시일 승계'이고 이것은 원천 rcept_dt 자체가 접수번호보다 이른 경우다. B 의 rcept_dt 미래 오타 관찰은 반대 방향 — 보수적.)

> J2 원장 대조 보충(04:34): dart.db `dart_fin_raw` -readonly 로 J-21 일성건설(00146232) FY2024 11011(rcept 20250808000499) `ifrs-full_Borrowings` '유동성장기차입금' 73,336,930,000 · `ShorttermBorrowings` 4,980,170,382 · `dart_LongTermBorrowingsGross` 51,593,070,000, J-24 제주은행(00148832) FY2025 `ifrs-full_InterestExpense` '이자비용' −166,723,000,000 을 원문에서 확인했다(stage 값과 동일).

### J-61 2010~2015 정리매매의 대부분이 liquidation_window 로 표시되지 않아, `krx.investable`·`krx.liquid` 백테스트가 정리매매 중인 종목을 사고 판다 [하]
- **상황**: 2026-09-01 전 universe_daily 의 liquidation_window 는 공시 '정리매매 개시' 신호로만 켜진다(마스터 측정 이전 구간). 이 신호가 없는 정리매매는 마지막 7거래일 내내 `status='listed'` 이고 `liquidation_window=false` 다. 워크벤치 정책 술어 `NOT liquidation_window` 가 걸러 주지 못한다.
- **인풋**: 2010~2015 를 포함하는 창에서 `krx.investable` 또는 `krx.liquid` 유니버스로 백테스트를 돌린다. 예) 저PBR·역추세·저가주처럼 정리매매 종목이 상위로 오기 쉬운 신호. 정리매매 종목은 시총이 급감하고, 일중 +187% 같은 급등락이 난다.
- **에러 위치**:
  - `database/src/equity/sql/universe_daily.sql:105` 의 signal_liquidation 은 공시 제목 '%정리매매 개시%' 하나뿐이다.
  - `:153`·`:183` 에서 liquidation_window = master is_liquidation, 없으면 공시 신호 누적이다.
  - 정책 술어는 `universe_policy`(krx.investable: `NOT liquidation_window`) 이다.
  - 문서는 `database/docs/EQUITY_DESIGN.md:446` 한계 목록에 '정리매매 개시 공시 349 ≈ 32%' 한 줄로만 적었다. 소비자 영향은 적혀 있지 않다.
- **위험성**: silent corrupt(연구 유니버스·백테스트 손익).
  - 정책이 약속한 '정리매매 제외'가 2010~2015 에는 대부분 작동하지 않는다.
  - 정리매매 구간의 극단 수익(최대 +187%/일)이 신호·손익에 들어간다. 사서 상폐까지 들고 가면 J-60 동결과 겹쳐 마지막 정리매매 종가로 남는다.
  - 2016 이후는 대부분 표시돼서, 홀드아웃(2016-01~2019-12)·2020+ 연구 영향은 작다.
- **근거** (로컬 equity 사본 m_20260929, 읽기 전용):
  - 보통주 상폐 구간 중 '마지막 체결 = 구간 끝 ∧ 마지막 8체결 종가 −50% 이하'(정리매매 대리 지표)는 354 다. 이 가운데 liquidation_window 표시가 있는 것 154(2016+ 117), **없는 것 200(2016+ 18)** 이다.
  - 표시 없는 200 구간의 마지막 7거래일 1,393행 중에서:
    - krx.investable 통과(`listed ∧ NOT admin_state ∧ NOT liquidation_window`)는 **773행·111종목**(2016+ 14종목)이다.
    - krx.liquid 통과(+ `no_trade_reason<>'illiquid' ∧ adv20_rank_pct>=0.5`)는 **118행·25종목**이다.
  - investable 통과 행의 하루 수익률은 최대 +187%, +30% 초과가 51회다.
  - 2016+ 예: 051310 포스코플랜텍(2016-04, 972→90), 004740 보루네오가구(2017-07), 005980 성지건설(2018-10), 093230 이아이디(2025-09, 1,392→33), 091090 세원이앤씨(2025-10).
- **기존 여부**:
  - 설계 문서의 알려진 한계(EQUITY_DESIGN §11 '정리매매 개시 공시 349 ≈ 32%')다. 결함 장부(A~I)에는 없고, 이번이 소비자 영향의 첫 실측이다.
  - 반대 방향(창이 안 닫힘)은 기존 G-05 다. admin_state 결측 쪽은 기존 G-04·G-07 과 맞닿는다.

> J-60 보충 1(04:37, 로컬 m_20260929): 월초 리밸런스로 시총 상위 N(보통주·listed)을 들고 가는 단순 전략을 가정했다. 각 상폐 종목의 '마지막 체결 직전 리밸런스일' 순위로 셌다. 동결되는 보유는 정리매매 없는 합병형만 나왔고, 상위 20 0건 · 상위 50 **6건** · 상위 100 16건 · 상위 200 31건이다. 상위 50 의 6건은 한국외환은행(2013-04) · 우리금융지주(2014-10) · SK(2015-07) · 삼성물산(2015-08) · 우리은행(2019-01) · 셀트리온헬스케어(2023-12) 이다. 동일가중 50종목이면 건마다 약 2%, 16년 누적 약 12%(명목)의 자본이 수익률 0 으로 묶인다.

> J-60 보충 2(04:38~04:44):
> - **'무표식' 범위 확인**:
>   - 'no bar for instrument in session' 취소 사유는 커널 이벤트 저장소에만 남는다. 워크벤치 결과의 주문 기록 `RawOrder`(`backend/src/strategy_workbench/domain/backtest/_models.py:154-162`)에는 상태·사유 열이 없어서, 결과에서는 '체결 없는 주문'으로만 보인다.
>   - 결정 사유의 `no_bar=` 는 목표 목록 안 종목만 적는다. REPLACE 가 만든 목록 밖 청산은 적지 않는다.
>   - 데이터 경고(`DataWarning`)·지표에도 표식이 없다.
> - **실제 연구 run 에서 확인** (로컬 `~/quant-data/backtests/EXP-001/*_FULL.bt.json`, 2021-01-04~2026-06-30, rust 코어, 읽기만 함): 마지막 스냅샷 보유 중 시장가가 20 스냅샷 이상 그대로인 포지션을 셌다.
>
>   | run | 동결 포지션 | 비중(최종 equity 대비) | 그중 상폐 | 상폐 비중 |
>   |---|---|---|---|---|
>   | A | 81 | 4.89% | 53 | 2.73% |
>   | B | 32 | 4.45% | 20 | 2.38% |
>   | C | 1 | 1.74% | 1 | 1.74% |
>   | D | 1 | 1.89% | 1 | 1.89% |
>
>   - 48종목 전략 C·D 의 1건은 둘 다 028150 GS홈쇼핑이다. 2021-06-28 종가 154,900 으로 1,225세션(run 끝까지) 동결됐다.
>   - 실제 주주가 받은 GS리테일(007070)의 원주가는 같은 기간 37,050 → 23,500(−36.6%, 사건 미보정)이다. 이 사례에서는 동결 평가가 낙관 쪽으로 보인다.
> - 로컬 사본(m_20260929)이 현 규칙을 반영하는지 확인했다. 그 뒤 `universe_daily.sql`·`security_span.sql`·커널 engine·Rust·워크벤치 실행 어댑터의 커밋은 0 이다(git log --since 09-28).

## 문제없음 확인

### 범위 1 — treasury_stock EG3 `n_ledger_total_mismatch` 651 (포크 J1)
- 651 그룹은 다음 넷으로 나뉜다. 대부분(①②③)은 원천·지표 구성 탓이고, 실제 산출 결함은 ④ 33건뿐이다(= J-01).
  1. 원천 자체 불일치 518: 회사 총계 ≠ 잎 합이거나 총계 칸이 비었다.
  2. 총계·잎의 주식종류 라벨 불일치 또는 잎 없음 44: 파트론 FY2024 처럼 DART 원문 주식종류 칸에 수량이 밀려 들어간 경우다.
  3. 총계 행 2~3줄 56: 지표 자체의 거짓 불일치다. 지표가 총계는 접수를 가로질러 더하고 잎은 접힌 값을 쓴다.
  4. 접기 손실 33: J-01.
- treasury_stock available_date 와 접수번호 날짜:
  - 261,375행은 같고, 1,931행은 1~6일 늦다(보수 방향).
  - 이른 32행은 박셀바이오 재제출본이다. J1 은 '원천 특성'으로 봤지만, 설계(E08, `rules_dart.py:489-495`)가 같은 현상을 look-ahead 로 규정하고 stg_disclosure 에만 보정한다. 그래서 **J-41 판단을 따른다**(treasury_stock 32행은 J-41 전파 목록에 들어 있다).
- 워크벤치 어댑터·fi 는 treasury_stock 을 읽지 않는다(`_specs.py:906` 은 언급뿐). J-01 의 모델 영향은 0 이다.

### 범위 5 — KIS 금액 단위 (포크 J1)
- **수급 '단위 미상' 열은 전부 백만원으로 확정했다**: `*_seln_tr_pbmn`·`*_shnu_tr_pbmn`·`frgn_reg/nreg_askp/bidp/ntby_pbmn`.
  - 매수 − 매도 = 순매수: 12,900행 전부 성립한다(순매수 대금은 이미 백만원으로 확정돼 있다).
  - (개인+외국인+기관+기타) 매도 합 ×1e6 ÷ 거래대금(원): 602행·285종목에서 중앙 1.0000 (p1 0.9993 · p99 1.0006).
  - 외국인 순매수 = 등록 + 비등록: 2,006행 전부 성립한다.
  - equity flow_daily 는 순매수 대금만 쓰므로 값 영향은 없다. 라벨만 바꾸면 된다.
- **신용 금액 6열은 만원이다**(J-02 의 근거).
  - 전량 보충: equity `EG3_credit_daily` 의 금액 대 시가 비율이 7,870,828행에서 중앙 8.94e-5 다. ×1e4 하면 0.894 로 표본 결과(융자 0.885)와 맞는다.

### 범위 2 — fin_std 보조 4계정 (포크 J2)
- 이중계상은 없다. lease_liab 에서 같은 개념이 반복되는 그룹은 0 이고, 총계와 분할이 함께 있으면 tier a 만 쓴다.
- FY2024 표본 값이 원문과 일치한다.
  - lease_liab 유동+비유동 합: 대한항공 10.93조, LG 1,380.8억, 롯데케미칼 2,587억, CJ CGV 1.56조.
  - borrowings: 미래에셋증권 77.1조, 신한지주 49.9조('차입부채').
  - 해당 줄이 본문에 없는 삼성전자·SK·이마트·엘앤에프는 NULL 이 맞다.
- 네 열 모두 지금 fi·모델·워크벤치 어댑터가 읽지 않는다(grep 0). 그래서 J-20~J-24 는 잠복 상태다. 영향이 닿는 곳은 factor_readiness·dataset_profile·옛 build_views 뿐이다.
- 커버율(FY2024 사업보고서):

  | 구분 | 행 | depreciation | interest_expense | borrowings | lease_liab |
  |---|---|---|---|---|---|
  | 비금융 | 2,407 | 1.2% | 1.0% | 2.3% | 65.5% |
  | 금융 | 237 | 2.5% | 30% | 22.4% | 31.6% |

### 범위 3 — stage DS005 밖 표·KRX 행 의미 (포크 J3)
- **숫자 변환**: replace(',') 뒤 TRY_CAST 를 쓴다(duckdb 1.5.5 직접 시험).
  - '△123'·'(123)'·'−5'·'--5' 는 NULL(cast_failed) 이 된다. '-1,234'·'+5'·' 123 ' 는 정상 변환된다.
  - stg_fin 1,540만 행은 G2 n_partial 0 이다. △·괄호 표기는 없고, 음수는 전부 '-' 접두다.
  - 보조표 cast 실패는 규칙 주석에 이미 적힌 것뿐이다: dividend·hyslr '#######', shares 각주.
  - scale 을 넘는 소수는 majorstock 1건이다(기존 I-21 원리).
- **날짜**: capital isu_dcrs_de 는 'YYYY.MM.DD' 278,360행과 '-' 8,339행뿐이다. stlm_dt 와 holder rcept_dt 는 ISO 다. 이상 모양은 0 이다.
- **원문 ↔ stage 대조**: stg_fin 표본 4행(음수·CF·1Q 누계·비지배)이 일치했다.
- **KRX 005930 분할 정지 구간**(2018-04-27~05-04): OHL 은 NULL(ledger_zero), 종가는 전일 값 유지, 거래량·대금은 0 이다.
- **KRX 단위**: 가격·대금·시총·지수 대금은 모두 원이다. 시총 = 종가 × 상장주식수 위반은 0 이다.
  - listing 마스터는 과거 시점 값이다(2018-05-04 액면 5000→100, LIST_SHRS 가 bydd 와 100% 일치).
  - 키움 ka10060 acc_trde_prica 는 거래량이고, stage 는 volume_shr 로 올바르게 싣는다(G9 대조 정상).
- **공개일 매핑**: 보조표 available_date NULL 은 0 이다. stg_rcept_dt_map 에서 rcept_no 당 rcept_dt 가 둘 이상인 경우도 0 이다.
- **holder 2024-08-26 하한**: DART 롤링 2년 창이고 문서(DART_CENSUS·STAGE_SPEC·FIELD_MAP)에 적혀 있다. 월별 접수 커버리지는 목록 대비 97~100% 다.
  - v1·v2 가 같은 (rcept_no, repror) 를 둘 다 가진 37·26쌍은 값이 같다.
- **결산월 변경 55법인**: acc_mt 는 현재값이며 equity corp 가 current_snapshot 으로 선언했다. fin_std 11011 은 문서 기준이라 불일치는 1건이다.
- tesstk '소계' 행은 treasury_stock.sql·FIELD_MAP 이 우회한다.
- dividend se 라벨은 2종으로 표준화돼 있다.

### 범위 4 — 워크벤치 백테스트 정지·폐지 (본체)
- **정지 보유**:
  - 어댑터는 reference 행(volume 0)을 bar 로 내지 않는다(`equity_duckdb/_adapter.py:981-983`). 커널은 마지막 체결가로 평가한다.
  - 정지 세션의 주문은 DAY 로 'no bar' 취소된다(`engine/loop.py:592-607`). 보유 목표는 현 수량으로 유지된다(`backtest_engine/_adapter.py:95-114`).
  - 재개일 첫 bar 부터 평가·매매가 재개된다. 정지 중 매매가 불가능한 현실과 맞는다.
  - 신호일에 bar 가 없는 미보유 목표는 건너뛰고 그 예산은 현금으로 남는다(보수 방향).
- **정리매매 bar**: price_kind='trade'(volume>0)라 bar 로 나간다. 표시된 정리매매 183구간은 전부 마지막 bar 가 구간 끝이다. 정리매매 하락(중앙 −93.6%)이 평가에 반영돼, 그 경우 동결은 현실적이다.
- **이전상장은 구간을 끊지 않는다**: 같은 티커가 2구간인 경우는 036220·101970 재사용 2건뿐이다. KOSDAQ→KOSPI 이전 종목은 J-60 처럼 동결되지 않는다.
- **run 이 죽는 경로 없음**: price_daily 의 volume_shr NULL 행은 0 이다(로컬 m_20260929). 그래서 `_as_int` 예외로 run 이 죽는 경로가 없다.
- **GAP-14**(체결 행인데 OHLC 가 결측·비양수): 127행(2010-02~2025-03, 2016+ 21행)이다. H 미조사 '현재 건수'를 로컬 사본으로 대리 측정했다. 어댑터가 경고(`equity.invalid_ohlc_rows_dropped`)와 함께 버린다.
- **생존 편향 없음**: 백테스트 bar·구간은 security_span(폐지 포함)이다. 팩터 관측 기본 유니버스 `krx.common-stock` 도 폐지 종목을 포함한다. H 결론과 같다.
- **배당 미반영**(가격 수익률만, 커널 `CorporateActionType` 에 배당 없음): `EQUITY_DESIGN.md:177·446` 의 'TR 불가(배당락 원천 없음, PR 고지)'로 문서화된 한계라 결함으로 세지 않는다. 다만 지표 이름은 `total_return`(`domain/analytics/_calculation.py:57`)이다.

## 가설
- (J1) treasury_stock 에서 한 (법인, 사업연도, 보고서)에 접수가 2개 이상인 그룹이 165개다.
  - 그중 9그룹은 접수끼리 값이 다르다. 결산월 변경으로 한 사업연도에 사업보고서가 2개인 경우로 보인다(유유제약·비츠로셀 FY2017).
  - equity 는 가장 이른 접수만 남기므로 뒤 결산기의 자사주가 빠질 수 있다(G-20·G-28 계열). 사례별로는 확인하지 않았다.
- (J3) dart_tesstk 원문 부호가 섞여 있다: change_qy_dsps 음수 383행, trmend_qy 음수 83, bsis_qy 음수 39, change_qy_incnr 음수 112.
  - 처분을 음수로 적는 공시 때문에 잎 합이 총계와 어긋나는 몫이 범위 1의 ① 518 안에 섞여 있을 수 있다.
- (J3) 보조 6표의 이력 빈도가 바뀌었다.
  - 백필(08-26~27)은 사업보고서(11011)만 받았는데, 09-09 부터 일일 수집이 11012·11013·11014 를 섞어 받는다.
  - 그래서 과거 구간은 연 1회, 운영 구간은 분기 갱신이다. 일일 쪽 빈도를 정한 문서는 찾지 못했다(DART_CENSUS:229 '사업보고서 한정'은 백필 결정이다).
- (J3) shares '비고' 행 25,964행 중 5~6행에 숫자가 실려, shares_outstanding 에 se='비고' 행으로 남는다(극소).
- (J2) `depreciation_q4_derived` 음수 27/664, `interest_expense_q4_derived` 음수 34/809.
  - 반기·분기 칸에 3개월이 아닌 값이 실린 탓으로 보여(00161426 FY2018 은 반기 > 연간), 기존 G-24 계열로 두고 새로 세지 않았다.
  - borrowings 승자 이름 '차입금' 72행·'차입금(사채포함)' 42행이 유동 한 줄인지 총액인지는 판정하지 못했다.
- (본체) 아래 두 종목은 정리매매 체결이 원장에 없다. stage 공시에 '정리매매 보류'(2021-11-29)와 이후 '상장폐지 절차' 안내만 있다.
  - 058220 아리온테크놀로지: 2020-03-19 마지막 체결 → 2024-05-02 상폐(1,016세션).
  - 058530 파나케이아: 933세션.
  - 실제로 정리매매가 없었는지, 원천 누락인지는 확인하지 못했다. 원천 누락이라면 보유분이 4년 전 가격으로 동결된다(낙관). H 의 '60세션 초과 3건'과 같은 집단이다.
- (본체) 합병형 상폐 직전 무거래 520 종목일(130종목)이 status='listed' 로 남는다. 원인은 `no_trade_reason='illiquid'` 이면서 no_trade_run < k 인 것이고, 그중 496 종목일이 krx.investable 을 통과한다. halt_disclosed 로 잡힌 상폐 정지는 2종목뿐이다.
  - 영향: 컴파일러가 이 종목을 목표로 잡으면 bar 가 없어 예산이 현금으로 남는다(보수 방향). IC 단면에는 수익률 0 인 정지 종목이 섞인다. 크기는 작을 것으로 보지만 측정하지 않았다.
- (본체) 팩터 관측 기본 유니버스 `krx.common-stock`(`_adapter.py:150`)에는 admin·liquidation 술어가 없다. 그래서 IC 단면에 정리매매 구간이 그대로 들어간다.
  - 정리매매 대리 354구간 × 약 7일 ≈ 2,500 종목일이다(전체의 0.03% 미만). IC 영향은 작을 것으로 추정하며, 측정하지 않았다.
- (본체) 컴파일러 장부(`previous_weights`, `_compiler.py:188-211`)는 의도 비중을 이어받을 뿐, 엔진의 실제 보유(동결·미체결)를 모른다. turnover buffer·minimum_trade 판단이 실제 보유와 어긋날 수 있다(크기 미측정).

## 미조사
- (J1) 원천 불일치 518그룹의 개별 원인(표본 6건만 봤다), factor_readiness I03~I05 의 현판 판정, 주식종류 칸에 숫자가 든 행의 전수, 신용 금액에 ×1e4 를 반영할 때의 영향 범위.
- (J2) 같은 회계연도 안에서 보고서마다 승자 tier 가 달라 정의가 섞이는 경우의 전수, 금융사 '차입부채'에 사채가 포함되는지의 업종별 차이, 옛 `build_views.py` 의 실제 사용 여부.
- (J3) stg_doc_index·stg_calls_dart·stg_units_dart(로그·문서층), stg_company 의 그 밖의 _current 열, KRX ETF·지수 행 표본 대조(단위만 확인), 3분기 보고서에서 thstrm 과 add 가 같은 행의 규모(판정은 fin_std 몫).
- (본체)
  - J-60 의 손익 영향 크기: 같은 전략을 '상폐 시 인수 측 주식으로 전환' 가정과 비교해야 한다. 워크벤치 실행이 로컬 산출물을 쓰므로 돌리지 않았고, EXP-001 결과 파일을 읽어 동결 비중만 쟀다.
  - 커널 직접 경로(`backend/src/backtest_engine/adapters/equity_duckdb.py`)의 정지·폐지 세부. 평가·청산 동작은 커널 공통이라 J-60 이 그대로 적용된다고 보지만 경로별로 확인하지는 않았다.
  - J-61 의 실제 전략별 정리매매 매수 건수: EXP 런은 2021+ 라 해당 구간 밖이다.

## 종료 기록
- 종료: 2026-10-06 04:42 KST · 결함 11건(상 0 · 중 1 · 하 10).
  - 범위 1·5 (J1): J-01, J-02
  - 범위 2 (J2): J-20~J-24
  - 범위 3 (J3): J-40, J-41
  - 범위 4 (본체): J-60, J-61
- 서버 쓰기 0 · 외부 API 0. 본체·포크 3 모두 sqlite mode=ro, duckdb 메모리 + read_parquet 만 썼다.
- 로컬 스크래치 auditJ1/·auditJ3/ 에 조회 스크립트가 있다. 본체 측정은 로컬 equity 사본 m_20260929 과 EXP-001 결과 파일을 읽기만 했다.
- 마지막 서버 쿼리는 04:40 KST(stage stg_disclosure 2종목 조회)다.

## 끝
