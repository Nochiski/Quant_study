# 영역 G — equity 중 모델 입력이 되는 표의 세부 규칙 (2차)

- 범위: src/equity/sql/·rules_s*.py 중 consensus_daily·opinion_daily·opinion_broker_daily·coverage_daily, fin_std(24계정 tier·pick·q4 파생·TTM 재료·연결/별도·비12월 결산), dividend_event·shares_outstanding·audit_opinion·treasury_stock·holder_daily·ownership_snapshot·sector_snapshot, universe_daily/universe_policy(adv20 외) + src/factor_inputs/queries.py 가 이 표에서 읽는 열의 의미 계약. 대조 문서 docs/EQUITY_DESIGN.md·EQUITY_GATES.md·EQUITY_FIELD_MAP.md·FACTOR_INPUTS.md. 서버 현판 표본으로 PIT·단위·부호·자연키·정정 접기·폴백·결측·날짜 경계 확인.
- 시작: 2026-10-06 03:48 KST
- 저장소: ~/orca/workspaces/Quant_study/v3-merge/database (feat/v3-merge, HEAD b220060a)
- 읽기 전용. 1차 기록(C.md 등)에 있는 결함은 "기존 C-NN" 으로 표시하고 새로 세지 않는다.

## 결함

### G-01 audit_opinion 이 판본을 '처음 관측한 행'으로 접어, 재감사로 바뀐 의견(의견거절→적정)이 equity·fi 에 영영 안 나온다 [하]
- 상황: `stg_audit` 은 append_only 이고 같은 (법인, 사업연도, 보고서, 기수 라벨) 에 정정 사업보고서(재감사)가 새 rcept_no 로 덧붙는다. equity grain 에는 rcept_no 가 없다.
- 인풋: 서버 현판 audit_opinion(m_20261003T004546) · stage stg_audit. 001470 삼부토건(00125974) FY2025 당기 — 원본 20260407002515 '의견거절'(관측 08-27) → 재감사 20260901000001 '적정의견'(available 09-01, 관측 09-09). 294090 이오플로우(01274310) FY2024 당기 — 20250331002328 '의견거절' → 20260930000004 '적정의견'(available 09-30).
- 에러 위치: `src/equity/sql/audit_opinion.sql:43-50` — `QUALIFY row_number() OVER (PARTITION BY corp_code, bsns_year, reprt_code, bsns_year_label ORDER BY observed_date NULLS LAST, row_hash NULLS LAST) = 1` (first_write_wins). 소비 `src/factor_inputs/queries.py:217-231`(aud: bsns_year DESC, available_date DESC 로 고르지만 접힌 뒤라 고를 판본이 하나뿐) → `:284-285` audit_adverse → v4 제외 플래그(`config/models/v4_rank_0_1.toml:36`·`v4_rank_0_2.toml:33` `exclude = [..., "audit_adverse", ...]`).
- 위험성: 같은 grain 의 나중 판본(재감사 적정)이 PIT 상 available_date 이후에는 정답인데, 접기가 가장 먼저 관측한 옛 판본(의견거절)을 남긴다. fi 10-02 판 001470·294090 audit_adverse = **True**(실제 현재 의견 적정). 지금은 둘 다 estimates_none 이라 v4 유니버스 밖(실해 0) — 추정치가 붙으면 v4 가 '비적정'으로 계속 뺀다. 연구 소비자(dataset_profile `event.audit_opinion`, FACTORS E08)도 옛 의견을 받는다. 재감사로 의견이 바뀌는 상장사는 매년 나오므로(3~9월) 일일 수집이 쌓일수록 늘어난다. 반대로 백필(08-26~29) 이전 이력은 API 가 최신 판본만 주므로 원래의 비적정이 이력에 없다(PIT 공백, 본 결함과 별개 한계). 카테고리: silent corrupt(낡은 값 고착).
- 근거: `stg_audit` grain 중 rcept_no 2개 이상 92그룹, 관측일 2개 이상 61그룹, 등급이 갈리는 그룹 8(MANIFEST EG3_audit_opinion `n_class_conflict_grain` = 8, 이력 7→8). 위 두 법인 stage 행·equity 남은 행(kept_rcept = 원본) 직접 조회, fi_universe(m_20261005T165747) audit_adverse 조회.
- 기존 여부: 새 발견(DESIGN §183 은 '연결/별도·중복 응답'만 접힘 원인으로 들고 재감사 판본은 언급 없음).

### G-02 연결/별도 감사보고서의 의견이 갈리는 grain 을 row_hash 순으로 하나만 남긴다 — 설계 문서의 '0 이 아니면 grain 을 다시 연다' 조건이 서버에서 충족됐는데 미조치 [하]
- 상황: 한 사업보고서에 연결·별도 감사보고서 2행이 같은 grain 으로 오고(구분은 자유 텍스트에만 있음), 두 의견이 다를 수 있다.
- 인풋: 376980 원티드랩(01441611) FY2025 당기 — 같은 rcept 20260323001529 에 '적정의견'·'한정의견' 2행 → equity 는 **적정** 을 남김. 101390 아이엠(00609634) FY2025 전기 — 적정·의견거절 → 적정 남김. 004430 송원산업 FY2024 전기 적정·한정 → 한정, 476080 엠83 FY2024 전전기 적정·한정 → 적정.
- 에러 위치: `src/equity/sql/audit_opinion.sql:43-50`(같은 observed_date 면 `row_hash` 순 — 해시 순서는 의미 없는 임의 선택) · 게이트 `src/equity/rules_s15.py:269-318` `n_class_conflict_grain` 은 metrics(기록형)라 판을 막지 않는다 · 문서 `docs/EQUITY_DESIGN.md:183`("서버에서 이 값이 0 이 아니면 grain 을 다시 연다(§11)")·`rules_s15.py:272-276` docstring 같은 약속.
- 위험성: 한쪽 재무제표에 한정·의견거절이 있어도 해시 순서에 따라 '적정' 이 남을 수 있다. fi 10-02 판 376980 audit_adverse = False(한정 행 존재). v4 의 비적정 제외가 무작위로 빠진다(지금은 estimates_none 이라 실해 0). 등급이 둘 다 비적정이면 영향 없음(코다코 한정·의견거절). 카테고리: silent(의견 손실) + 문서 불일치(약속된 조치 미이행).
- 근거: 위 G-01 과 같은 stage 대조 쿼리(등급 갈림 8그룹 중 같은 rcept_no 안 갈림 6), MANIFEST 이력 10판 전부 `n_class_conflict_grain` 7~8.
- 기존 여부: 새 발견.

### G-40 consensus_daily wise 축의 관측월 점이 2026-09 부터 '월말'에서 '월초(그 달 첫 수집)'로 바뀌었다 — obs_month 차분 리비전이 08→09 에서 가짜 0, 문서는 여전히 'wise = 월말' [하]
- 상황: WISE 월별 차트(cF5001·cF5002)는 첫 수집(09-01)에서 과거 37개월을 **월말 라벨**(예 '2026/08/31')로 주고, 그 뒤 매일 수집에서는 진행 중인 달의 마지막 점을 DT=T−1 날짜(09-01, 09-02 …)로 준다. equity 는 (ticker, obs_month, target_period, metric) 마다 `min(fetched_date)` 한 행만 남긴다.
- 인풋: 현판 consensus_daily(m_20261003T004552) src='wise'. 005930 eps 202612: obs_month 2026-08 = obs_date 08-31 · 48,338.64(09-01 수집) / obs_month 2026-09 = obs_date **09-01** · 48,338.64(09-02 수집) / 2026-10 = 10-01 · 47,495.94. stage 에는 같은 9월의 월말 점(10-01 수집 09-30 = 47,653.28, 10-02 수집 47,922.24)이 있으나 버려진다.
- 에러 위치: `src/equity/sql/consensus_daily.sql:97-104`(wise_pick — obs_month 로 묶고 fetched_date 오름차순 첫 행) · 주석 `:6-9`("월말 관측") · `src/equity/rules_s17.py:336-339`(EG8 docstring "wise 는 월말·v3 는 월초") · `docs/EQUITY_DESIGN.md:200`·`docs/EQUITY_GATES.md:2173`(같은 전제) · 소비 `src/equity/views.py:263-296`(v_consensus 가 obs_month 로 접어 노출, "리비전 팩터(G05)를 팩터층이 계산") · 소비자 규약 `rules_s17.py:445-461`(CONSENSUS_CONVENTION ①~④ — 관측점 기준일 언급 없음).
- 위험성: 같은 열(obs_month 점)의 기준일이 2026-08 이전 = 월말, 2026-09 이후 = 월초(사실상 전월 말)로 단절됐다. obs_month 차분으로 리비전(G05~G07)을 만드는 연구 소비자는 08→09 구간에서 92.9%(eps)·92.7%(revenue) 종목이 정확히 0 리비전을 받고, 이후 달은 한 달 밀린 값을 그 달 값으로 읽는다. 진행 중인 달의 '최초 관측'은 구조적으로 월초 값이라 월말 점은 영원히 안 들어온다. 모델 파이프라인(fi)은 consensus_daily 를 읽지 않아(stg_consensus_matrix 직독) 엑셀 영향 없음. 카테고리: silent 정의 단절 + 문서 불일치.
- 근거: wise 관측월별 obs_date 위치 — 2025-09~2026-08 은 월말형(일 ≥ 24) 100%, 2026-09 는 월초형(일 ≤ 7) 3,385/3,432, 2026-10 은 3,528/3,528. 2026-08 vs 2026-09 값 동일 eps 1,602/1,725 · revenue 1,559/1,682. 2026-09 관측월 중 stage 에 월말(≥09-25) 점이 있는데 월초 점을 남긴 행 3,201.
- 기존 여부: 새 발견. (EG8 이 'wise 월말 ↔ v3 월초' 축 차이는 알고 obs_date 로 비교하게 고쳤지만, wise 자신이 실시간 구간에서 월초로 바뀐 것은 기록 없음.)

### G-03 법정 제출기한이 전 법인 90/45일 하나뿐이라, 외국 상장법인(sec_type foreign·dr)의 정기보고서 56~68% 가 fi `filing_late=True` 로 찍힌다 [하]
- 상황: 국내 상장 외국법인(티커 9xxxxx, security sec_type `foreign` 24·`dr` 16)은 실제 제출이 사업보고서 기말 +110~120일, 반기·분기 +56~62일에 몰린다(국내 법인은 +79일·+45일 중앙값). 즉 외국법인은 다른 기한 체계를 따른다(데이터 분포로 확인 — 법령 조문 대조는 외부 조회 금지라 미확인).
- 인풋: 서버 disclosure_version(m_20261003T004457) 2023 이후 원본(정정 제외) 정기보고서, fi 와 같은 '기한이 휴장이면 다음 거래일' 보정 후 지연 판정.
- 에러 위치: `src/equity/sql/disclosure_version.sql:228-233`(legal_deadline = 기말 + `deadline_days_annual` 90 / `deadline_days_interim` 45, 법인 구분 없음) · 상수 근거 `docs/EQUITY_GATES.md:847`("90·45(자본시장법 §159·§160). 규범이라 재측정 대상 아님") → 소비 `src/factor_inputs/queries.py:232-243·286-287`(filing_late = rcept_dt > 다음 거래일 보정 기한) → 계약 `src/model/contracts.py:117`("정기보고서 법정기한 지연 제출").
- 위험성: 외국법인 지연 판정 annual 48/85 · half 58/85 · quarter 104/153 이 '지연' — 대부분 +120/+60일 안 정상 제출이다(국내는 annual 138/10,803 · half 137/10,733 · quarter 226/18,535 = 1.2~1.3%). fi 10-02 판에서 filing_late=True 35종목 중 다수가 9xxxxx(헝셩그룹·오가닉티코스메틱·로스웰·크리스탈신소재·컬러레이 등). v4 는 sec_type common 만 받아 지금 모델 영향은 0(잠재) — 그러나 열 의미('법정기한 지연')가 외국법인에서 틀리고 equity `delay_days` 를 쓰는 연구 소비자도 같다. 카테고리: silent corrupt(열 의미 위반) + 문서 불일치('규범이라 재측정 대상 아님').
- 근거: 외국법인 제출일 − 기말 분포(annual d=120 11건·110~119 다수, half d=60 25·59 12·62 8, quarter d=60 54·59 15·62 14), 국내/외국 지연율 집계 쿼리, fi_universe(m_20261005T165747) filing_late 목록.
- 기존 여부: 새 발견(메모리의 '공시지연 달력 결함'은 주말·휴장 보정 문제로 fi 가 이미 보정 — 본 건은 기한 일수 자체).

### G-20 비12월 결산 법인의 4분기 파생(q4_derived)·현금흐름 분기 파생(cf_*_q)이 다음 회계연도 분기를 빼서 만든다 — 음수 매출 등 쓰레기 값, 공개일도 8개월 늦다 [하]
- 상황: fin_std 의 `bsns_year` 는 모든 행에서 `year(period_end)` 와 같다(서버 현판 94,018행 중 불일치 0 — GATES FX-4-002 가 적은 '종료 연도 관례'). 12월 결산이면 한 회계연도의 1Q·반기·3Q·사업보고서가 같은 bsns_year 에 모이지만, 3월 결산이면 bsns_year=Y 에 사업보고서(Y-1.04~Y.03)와 **다음 회계연도의** 1Q(Y.04~06)·반기(~09)·3Q(~12)가 모인다(6·8·9월 결산도 같은 꼴로 섞인다).
- 인풋: 서버 현판 fin_std(m_20261003T004504). 대동전자 00157104(3월 결산) bsns_year 2025 CFS: 사업보고서 2024-04-01~2025-03-31 revenue 30,074,181,636 / 같은 bsns_year 분기 = 2025-06·09·12 말(다음 회계연도).
- 에러 위치: `src/equity/sql/fin_std.sql:349-367`(`q4src`·`q4` — `(corp_code, bsns_year, fs_div)` 로 11012·11013·11014 를 더해 11011 에서 뺀다) · `:368-376`(`q4meta` — 같은 묶음의 max 공개일) · `:377-401`(`cfq`·`cfqmeta` — 11011 의 직전 누계를 같은 bsns_year 의 11014 로 잡는다). 소비: `src/factor_inputs/queries.py:690-705`(`qtr` 가 11011 행에 `*_q4_derived` 를 4분기 값으로 싣는다)·`:759`(WISE 분기 없는 종목은 DART 분기 경로). 계약 문서 `docs/EQUITY_FIELD_MAP.md:36·84`("사업보고서 − Σ3분기")는 같은 회계연도를 전제한다.
- 위험성: 4분기 값 = 회계연도 A 의 연간 − 회계연도 A+1 의 3분기 합이라 크기·부호가 무의미하다. 대동전자 FY2025 revenue_q4_derived **15,232,647,700**(정답 2025-01~03 = 30,074,181,636 − 25,149,869,985 = **4,924,311,651**, 3.1배), 모아텍 00241209 **940,298,505**(정답 8,420,693,152). cf_operating_q(11011) 대동전자 14,425,481,565(정답 8,220,799,202). 공개일 `q4_derived_available_date` 도 다음 회계연도 3Q 접수일(2026-02-13)이라 정답(2025-06-19)보다 8개월 늦다. 연구 소비자(워크벤치 TTM·YoY·분기 FCF)는 이 법인들의 분기 시계열을 조용히 틀리게 받는다(부호 뒤집힌 성장률 → 팩터 극단값). fi 는 fi_fin_summary 분기 행에 그대로 싣는다 — 현판 fi(m_20261005T165747) 020180 대신정보통신 '2025/03' 분기 매출 **−162억**. 다만 해당 종목 42개 전부 `estimates_none`·`sec_type` 로 eligible=false 라 모델(scope·v4) 영향은 현재 0. GATES·FIELD_MAP 어디에도 비12월 결산 분기 파생 예외 표기가 없다. 카테고리: silent corrupt(연구 DB) + 문서 불일치.
- 근거: a_5.py(대동전자·모아텍 bsns_year 2022~2026 행 — 각 bsns_year 분기의 period_start 가 사업보고서 period_end 다음 날), a_6.py: 11011 21,530 그룹 중 같은 bsns_year 분기 말일 > 사업보고서 말일 **336**(q4 값 있음 230 · cf_q 있음 286 · 법인 65 · 2020년 이후 q4 171). 법인 목록에 fiscal_month=12 인 결산월 변경 법인(아시아종묘·HLB이노베이션·만호제강 등 과도기)도 포함.
- 기존 여부: 새 발견. TECH_DEBT B-40(11013 1분기/3분기 판정이 기간 길이 기준)과 원인이 다르다(B-40 은 라벨, 이것은 bsns_year 묶음). fi GAPS·U22 는 비12월 '연간' 행만 다룬다.

### G-04 KOSPI 관리종목 파생 창이 '관리종목 지정 **우려**·예고·사유 해소' 안내로만 켜진다 — 우선주 안내가 보통주에 붙어 대형주가 365세션 동안 admin_state=true (investable·liquid 정책에서 빠짐) [중]
- 상황: 2026-09-01 이전(키움 마스터 측정 전) KOSPI 는 관리종목 측정값이 없어 `signal_admin` 공시 뒤 `admin_window_td`=365 거래일 창으로 admin_state 를 파생한다(`derived_kospi_window`). 신호는 report_nm 에 '관리종목' 이 있고 '해제' 가 없으면 켜진다.
- 인풋: 서버 stg_disclosure(has_ticker) 중 신호에 걸린 공시 — KOSPI 보통주 153건 **전부** '우려·예고·해소' 류(예: "투자유의안내 (한화 1우선주 관리종목지정 우려 예고)" 2024-12-04, "기타시장안내(관리종목 지정 사유 해소)"). DART 공시의 종목코드는 보통주라 우선주 안내도 보통주 행에 붙는다.
- 에러 위치: `src/equity/sql/universe_daily.sql:108`(`bool_or(nm LIKE '%관리종목%' AND nm NOT LIKE '%해제%') AS signal_admin` — 우려·예고·해소를 못 거름) · `:171-177`(KOSPI → `td_seq - last_admin < admin_window_td`) · 상수 `src/equity/baseline_locked.json` universe_daily.admin_window_td 365 · 소비 `src/equity/sql/universe_policy.sql:31-35`(investable·liquid = `NOT admin_state`) → 워크벤치 어댑터 `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py:25`(정책 술어를 universe_daily 에 적용) · fi `src/factor_inputs/queries.py:282`(is_admin → v4 exclude 'admin').
- 위험성: 실제 관리종목이 아닌 KOSPI 보통주 73종목·35,132행이 admin_state=true. 대형주 예: 012330 현대모비스 2014-05-30~2015-12-11(380행), 010140 삼성중공업 2022-12-01~2024-05-28(365), 000880 한화 2024-12-04~2026-06-26(378), 002790 아모레G·004990 롯데지주·001740 SK네트웍스·000070 삼양홀딩스(2015~2026-08-31 누적 1,541행). 연구 백테스트의 `krx.investable`·`krx.liquid` 유니버스에서 이 종목들이 그 기간 통째로 빠진다(선택 편향 — 우선주 유동성 문제를 가진 지주·대형주가 체계적으로 제외). 모델(fi)은 09-01 이후 마스터 측정값(`is_admin_issue`)이 우선이라 현재 엑셀 영향은 없다 — 마스터 행이 빠지는 날(A-08 재시도 없음)에는 파생 창으로 돌아가 v4 가 000070 등을 'admin' 으로 뺄 수 있다(잠재). 카테고리: silent corrupt(연구 유니버스) + 문서 불일치(DESIGN §4-1 '지정 신호').
- 근거: 신호 이름 분포(KOSPI common 153/153 우려형, KOSDAQ common 468/1,217 우려형 — KOSDAQ 은 sect_tp 측정이 우선이라 영향 263행), `admin_state_basis='derived_kospi_window' AND admin_state AND market='KOSPI' AND sec_type='common'` 연도별 2013~2026 합 35,132행, 종목별 기간·시총 조회.
- 기존 여부: 새 발견(DESIGN §4-1 은 '해제 공시 3건이라 미반영'만 한계로 적음).

### G-41 coverage_daily.covered 는 '그날 커버가 있었나'가 아니라 'WISE 월별 차트(수집기 판정 3개년 창) 어디에든 값이 있나'다 — 커버가 끊긴 종목이 1년 가까이 covered=true 로 남는다 [하]
- 상황: S24 는 수집기 술어(`backfill_wise.is_covered`)를 그대로 옮겨, 그날 스냅샷의 월별 차트 어느 관측점(과거 달 포함)에든 추정치나 목표가가 있으면 covered 로 본다. 차트는 과거 관측점을 계속 싣는다.
- 인풋: 현판 coverage_daily(b_20261005T100502) date=2026-10-02 covered=true 808종목 × 같은 날 stage stg_consensus_monthly(10-02 수집)의 '값 있는 마지막 obs_date'.
- 에러 위치: `src/equity/sql/coverage_daily.sql:52-58`(cov — metric·obs_date 무관 `consensus IS NOT NULL OR target_price_krw IS NOT NULL`) → `:79-99`(last_covered_date·streak_days 가 이 covered 로 이어짐). 문서 `docs/EQUITY_FIELD_MAP.md:171-175`("그날 그 종목에 컨센서스 커버가 있었는가", 이 표의 존재 이유 = "언제부터 붙었나 / **언제 끊겼나**").
- 위험성: 10-02 covered 808 중 147(18%)은 09-01 이후 추정치·목표가 관측이 하나도 없고(그중 44 는 마지막 추정이 2025-10-31~2026-03-31, 예 008490·188040 은 2025-10-31), 당해 FY 추정 0·추정기관수 0 이다. 이들의 last_covered_date 는 매일 그날로 갱신되고 streak 가 이어진다 → 커버 상실은 옛 값이 차트에서 빠질 때(실측 최장 약 11개월 묵은 값이 아직 covered — 수집기 술어는 `rules_s24.py:13` '09-10 핫픽스 뒤 3개년 판정')에야 보인다. 문서가 약속한 '끊긴 날'을 이 표로는 알 수 없다. 지금 이 열들(covered·first/last_covered_date·streak_days)을 읽는 코드 소비자는 없다(fi 는 analyst_count 만, dataset_profile 미등재) → 잠재. 카테고리: 문서 불일치(정의) + silent(커버 상실 감지 지연).
- 근거: `b_9.py` — 10-02 covered 808 = 최근(≥09-01) 661(그중 FY 신선 628) · 04~08월 103 · 04월 이전 44. 비교: fi 신선 판정(당해 12월기 op·ni) 628.
- 기존 여부: 새 발견.

### G-21 일일 수집이 정정본을 덧붙인 재무 그룹에서 fin_std 는 원본 값을 영구히 고정하고(정정 값 미반영), 줄 순서가 바뀐 계정은 모호(NULL)로 떨어진다 [중]
- 상황: 09-09 부터 일일 DART 수집이 정기보고서 정정을 같은 (corp_code, bsns_year, reprt_code, fs_div) 로 다시 받아 `stg_fin` 에 덧붙인다(append_only). 서버 stage(m_20261002T234713) 에서 rcept_no 가 둘 이상인 그룹 **162**(마지막 관측 09-09~10-02), 그중 정정이 값을 바꾼 그룹 8 · 줄 순서(ord)를 바꾼 그룹 3.
- 인풋: 오브젠 01472930 FY2025 CFS — 원본 20260319001177(03-19) 영업이익 267,873,584·순이익 −3,165,668,440·매출 24,017,071,711 → [기재정정] 20260928000253(09-28) 6,365,380·−3,427,176,644·23,411,536,664. 이오플로우 01274310 FY2024 CFS — 20250331002328 → [기재정정] 20260930000004: 순이익 −64.7B → −91.9B, 자산총계 57.6B → 45.7B, 매출 5.03B → 5.24B.
- 에러 위치: `src/equity/sql/fin_std.sql:124-133`(`grp` 가 그룹당 `min(rcept_no)` = 원본 접수를 고른다 — 머리말 `:4` "그룹당 rcept_no 는 하나다" 전제) · `:153-156`(`fin` 이 자연키 8열(rcept_no 미포함)마다 first_write_wins(`observed_date` 최소) → 같은 줄은 **원본 값**이 이기고, ord 가 바뀐 줄은 원본·정정 두 줄이 다 살아남는다) · `:248-254`(`pick` 은 값이 갈리면 NULL, `sum`(lease_liab·금융업 매출 대체)은 두 줄을 더한다). 소비: `src/factor_inputs/queries.py:618-627·653-668`(fi `fin`·`dart` 가 이 행을 roe·roa·debt_ratio·fcf·total_assets 재료로 쓴다).
- 위험성: 정정이 재무 수치를 고쳐도 fin_std 는 원본 값·원본 접수일을 계속 싣는다 — 그 법인의 최신 연간 재무가 정정 뒤에도 틀린 채로 모델 입력(fi)·연구에 간다(백필 구간은 API 가 정정본만 줘서 '정정 값 + 정정일'인데 일일 구간은 '원본 값 + 원본일' — 같은 표 안에서 판본 정책이 둘). 문서 정책(B-41 '정정본은 정정 접수일에야 보인다', DESIGN `vintage_kind='api_restated'`)과 반대다. 또 정정이 줄 순서를 바꾸면 원본·정정 줄이 함께 남아 `pick` 이 모호 NULL 이 된다 — 이오플로우 FY2024 total_equity·cf_operating_ytd NULL(→ fi roe·debt_ratio·fcf NULL), 162 그룹의 fin_std 행 중 total_equity NULL 3(2024년 이후 전체 기준선 11/26,108 대비 이례적). `sum` 계정은 같은 경로로 이중계상될 수 있다(lease_liab — 실례 미확인). 정정 수집이 쌓일수록(정기보고서 정정 시즌) 늘어난다. 현판 fi eligible 종목 중 해당 0 — 지금 모델 영향 없음(잠재·증가형). 카테고리: 데이터 손실(정정 값 영구 미반영) + silent(모호 NULL) + 문서 불일치(판본 정책).
- 근거: a_8.py·a_9.py·a_10.py — 162 그룹 fin_std 행 161 중 rcept_no = 원본 158 · 정정 0, 위 두 법인 fin_std 값 = 원본 값 확인. 덤: 한화리츠 01669226 은 bsns_year 2026 11012 에 서로 다른 반기보고서 두 건(2026.01·2026.07 — 반기 결산 리츠)이 있어 뒤 보고서가 통째로 빠진다(sec_type reit, fi 적격 밖).
- 기존 여부: 새 발견. C-11(정정본 원본 공시일 승계)·B-41(정정 원본 복원 보류)과 다른 경로 — 그 둘은 'API 가 정정본만 준다'는 백필 전제 위의 이야기이고, 이것은 일일 재수집으로 원본·정정이 함께 쌓인 경우다.

### G-22 fi dps 가 stock_knd 를 '보통주·보통주식·-' 세 낱말로만 걸러 KCC·KCC글라스·에스피지(적격) 등 배당주의 DPS 가 NULL → v4 DY0 = 0(무배당 취급) [하]
- 상황: equity `dividend_event` 는 원장 `stock_knd`(자유 텍스트)를 정규화 없이 grain 으로 싣는다(`src/equity/sql/dividend_event.sql:32-46`, 종류 대응표 없음 — `rules_s16.py:51-64` 의 S05 어휘표는 주식총수 원장 전용). 사업보고서(11011) 배당 행의 보통주 표기는 '보통주' 외에 '의결권 있는 주식'·'의결권있는주식수'·'결산 배당'/'결산배당'+'중간배당'·'대주주'/'소액주주'(차등배당)·'보통주*'·'보통주(소액주주)'·'보 통 주'·오타('보퉁주'·'보통투') 등이 있다.
- 인풋: fi 아침판 `fi_fin_summary`(m_20261005T165747) — KCC 002380 FY2025 dividend_event 행 '의결권 있는 주식' dps 15,000 · KCC글라스 344820 '의결권 있는 주식' 1,600 · 에스피지 058610 '결산배당' 150 + '중간배당' 100. 셋 다 fi eligible.
- 에러 위치: `src/factor_inputs/queries.py:77-78`(`DPS_STOCK_KINDS = ("보통주", "보통주식")`, 폴백 '-') · `:638-652`(`div` CTE 가 `trim(stock_knd) IN (…)` 완전일치로만 고른다 — 행이 둘(결산·중간)이면 합산도 없다). 소비 `src/model/engines/v4_rank.py:299-309`(`_dy0` — 730일 안 dps 있는 연간 행이 없으면 `Val(0.0, NO_DIVIDEND)`).
- 위험성: 실제 배당주가 v4 비교 모델에서 배당수익률 0 으로 채점된다(KCC 는 FY2024 도 같은 표기라 폴백도 없음 → DY0 = 0, WISE dividend_yield 는 3.57%). 결측 표식이 아니라 '무배당'으로 들어가 조용히 순위가 내려간다. 놓치는 법인-연도: 2019 14 · 2020 17 · 2021 12 · 2022 11 · 2023 9 · 2024 10 · 2025 8(보통주 계열 표기만 있고 일치 표기 행에 dps 가 없는 경우). scope·v3_zscore 는 WISE `dividend_yield` 를 써 영향 없음 — v4@0.1·0.2(비교 모델)와 연구 패널만. 카테고리: silent corrupt(층 간 어휘 계약 공백).
- 근거: a_11.py(11011 stock_knd 어휘 분포 — dps 있는 행 기준 '의결권 있는 주식' 26 · '대주주'·'소액주주' 각 20 · '보통주(소액주주)' 12 · '의결권있는주식수' 11 · '결산 배당' 10 …), a_12.py(위 연도별 건수, FY2025 fi 노출 — 002380·058610·344820 eligible, fi dps NULL).
- 기존 여부: 새 발견. queries.py 머리 주석(`:73-76`)은 '-' 폴백 155곳만 다루고 다른 표기는 언급 없음.

#### G-40 보강(03:59) — 같은 원인으로 '달 중간 커버 개시'가 그 달 점에서 NULL 로 고정된다
- 진행 중인 달의 첫 관측(월초)이 NULL 이고 같은 달 뒤 수집에서 값이 생긴 관측점(커버 개시 등)은 그 달 점이 영구 NULL 이다 — 실시간 달(2026-09·10) 303 관측점(예 0011T0 2026-09 eps·revenue: 09-11 수집 NULL 고정, 10-02 수집에 9월 값 등장 · 005610 2026-09 202612 eps: 10-01 에 값). 다음 달 첫 관측에서야 값이 보이므로 개시 신호가 한 달 늦다. (과거 달 1,168 관측점은 WISE 가 과거 점을 뒤늦게 채운 경우로, '최초 관측' 설계 의도 범위 — 결함으로 세지 않음.)
- 근거: `b_14.py` — wise 산출 est_mean NULL 인 관측점 중 stage 뒤 수집에 값이 있는 것: live 303 / 과거 1,168.

### G-05 정리매매 창(liquidation_window)이 '정리매매 보류·상장폐지 무효 판결'로 닫히지 않는다 — 192410 오늘이엔엠이 2018-09~2026-08 정상 거래 1,400여 일 동안 정리매매 중으로 남아 investable 밖 [하]
- 상황: 2026-09-01 이전(마스터 측정 전)에는 '정리매매 개시' 공시 신호일부터 그 구간(span) 끝까지 liquidation_window=true 로 둔다. 상장폐지 효력정지 가처분 인용·대법원 무효 판결로 정리매매가 취소되고 거래가 재개되는 경우를 닫는 신호가 없다.
- 인풋: 192410 — 2018-09-27 "주권매매거래정지해제(상장폐지에 따른 정리매매 개시)" → 10-05 "기타시장안내(정리매매 보류)" → 10-08 가처분 인용 → 2020-08-13 "주권매매거래정지해제(대법원의 상장폐지결정무효 판결…)" → 이후 정상 거래(액면분할·병합 반복, 시총 800~2,237억).
- 에러 위치: `src/equity/sql/universe_daily.sql:105`(signal_liquidation = '정리매매 개시' 만) · `:153`(last_liquidation = 구간 누적 max) · `:183`(`liquidation_window = coalesce(is_liquidation, last_liquidation IS NOT NULL)` — 한 번 켜지면 구간 끝까지) · 소비 `src/equity/sql/universe_policy.sql:31-35`(investable·liquid = `NOT liquidation_window`).
- 위험성: universe_daily 192410 은 2018-09-27~2026-08-31 1,945행 liquidation_window=true, 그중 거래일 1,414(2021·2023·2025 는 연중 전부 거래, status listed). 연구 백테스트의 `krx.investable`·`krx.liquid` 에서 6년 가까이 빠진다. 구간 길이 >100 세션인 정리매매 창 63종목 14,677행 중 나머지는 대부분 정지 상태(거래 0~7일)라 영향이 작다. 09-01 이후는 마스터 `is_liquidation` 측정값이 우선이라 모델(fi)에는 영향 없음. 카테고리: silent corrupt(연구 유니버스).
- 근거: liquidation_window 종목별 길이 분포(≤10 세션 123종목 · 11~30 6 · 31~100 43 · >100 63), 192410 연도별 liq·traded 집계, stg_disclosure 192410 공시 목록.
- 기존 여부: 새 발견.

### G-06 무거래 이유 ③ corp_action_window 가 D 이후 최대 45세션 앞 사건을 공개일(available_date) 확인 없이 쓴다 — 해당 행의 80% 가 D 에 아직 공개되지 않은 사건, 상태 규칙 'PIT' 문서와 어긋남 [하]
- 상황: S03C 는 무거래(price_kind='reference') 행에 adj_factor 사건 적용일이 [D−5, D+45] 세션 안이면 `no_trade_reason='corp_action_window'` → `status='suspended'` 로 둔다. 창을 사건 쪽에서 펼치며 사건의 available_date 는 보지 않는다.
- 인풋: 서버 universe_daily(m_20261003T004013) corp_action_window 23,800행 × adj_factor(m_20261003, unknown_price_only 제외) 창 조인.
- 에러 위치: `src/equity/sql/universe_daily.sql:79-94`(ca_win — `c.td_seq BETWEEN ca.td_seq - corp_action_lookahead_sessions AND ca.td_seq + corp_action_lookback_sessions`, available_date 조건 없음) · `:202`·`:212-215`(status) · 상수 lookahead 45(`baseline_locked.json`) · 문서 `docs/EQUITY_DESIGN.md:84`("상태 규칙(PIT …) — 전부 … D 이전 정보만") vs `:91`(③ 창 D+45).
- 위험성: 23,800행 중 19,037행(80%, 951종목)은 창 안 모든 사건의 available_date > D — 그날 시장이 몰랐던 감자·병합·분할·KRX 기준가 사건(unknown_krx 10,094 · reverse_split 5,716 · capred 4,615 · split 4,210 · bonus 357, 사건-행 쌍 기준)으로 상태를 정한다. run<5 행 5,611 은 이 규칙이 없으면 listed 였다 → `krx.common-stock`·`krx.investable` 에서 미래 정보로 빠진다(거래량 0 인 날이라 체결 영향은 작고, 유니버스 구성·횡단면 정규화·보유 판정에 미래 사건이 새는 정도). fi 는 listed·suspended 를 모두 받아 모델 영향 없음. 카테고리: look-ahead(상태 라벨) + 문서 불일치.
- 근거: 위 조인 쿼리(n_cw_rows 23,800 · n_unknown_at_D 19,037 · 사건 적용일이 D 뒤 18,973), status × run 분할(suspended run<5 5,611 · run≥5 18,189), 2026-08-31 표본(001000·002880 reverse_split apply 09-11 = available 09-11 인데 08-31 에 이미 창).
- 기존 여부: 새 발견(설계는 lookahead 를 의도로 적었지만 available_date 미확인과 PIT 문구 충돌은 기록 없음).

#### G-04 보강(04:05) — 파생 규칙은 진짜 KOSPI 관리종목도 거의 못 잡는다(재현율 1/39)
- 측정이 시작된 경계(08-31 파생 → 09-01 키움 마스터 `is_admin_issue`)에서 KOSPI 행 대조: 파생 False → 측정 True **38**, 파생 True → 측정 False 1(000070), 둘 다 True 1. 즉 09-01 전 KOSPI admin_state 는 실제 관리종목 39 중 1 만 잡고, 켜진 것은 대부분 '우려' 안내 오탐이다. 2010~2026-08 연구 `krx.investable` 은 KOSPI 의 실제 관리종목을 거의 그대로 포함하고(제외 실패) 우선주 안내가 붙은 대형주를 뺀다(제외 오탐) — 양방향 오염.

#### G-05 보강(04:05) — 경계 대조
- 08-31 파생 liquidation_window=true 24종목 중 23종목이 09-01 마스터 `is_liquidation` 에서 false(192410·066410 버킷스튜디오·121800 비덴트·068940 셀피글로벌·043220 등) — 열린 채 남은 창 대부분이 실제 정리매매가 아니었다.

### G-07 09-01 마스터 측정 전환으로 admin_state(→ fi is_admin)의 정의가 조용히 바뀌었다 — KOSDAQ 투자주의환기종목이 빠졌는데 fi 계약·주석은 여전히 '투자주의환기 포함' [하]
- 상황: universe_daily admin_state 는 2026-09-01 부터 키움 마스터 `is_admin_issue`(측정)가 최우선이고, 그 전 KOSDAQ 은 소속부 `sect_tp ∈ {관리종목(소속부없음), 투자주의환기종목(소속부없음)}`(측정)였다. 마스터 플래그는 관리종목만 true 다.
- 인풋: 서버 universe_daily(m_20261003T004013) 08-31 vs 09-01, stg_listing_daily sect_tp, stg_master_daily `is_admin_issue`·`state`.
- 에러 위치: `src/equity/sql/universe_daily.sql:171-173`(`CASE WHEN is_admin_issue IS NOT NULL THEN is_admin_issue WHEN market='KOSDAQ' AND sect_available THEN sect_tp IN ('관리종목(소속부없음)','투자주의환기종목(소속부없음)') …`) — 두 측정 원천의 정의가 다른데 한 열로 잇는다. 소비 계약 `src/factor_inputs/queries.py:187-188`(docstring "is_admin = admin_state(관리종목·KOSDAQ 투자주의환기 소속부 …)")·`:828-830`(GAPS "KOSDAQ 투자주의환기 소속부 포함") → v4 `exclude = ["admin", …]`.
- 위험성: 08-31 측정 True → 09-01 측정 False 37종목 = 전부 `투자주의환기종목(소속부없음)`(소속부 표는 09-01 이후에도 43~44종목 그대로). fi 10-02: 투자주의환기 40종목 중 마스터 관리종목 겹침 5 만 is_admin=True, 297090 씨에스베어링은 eligible·is_admin=False(이번엔 v4 가 adv20 으로 먼저 뺌 — 실해 0). v4 의 'admin' 제외가 09-01 부터 투자주의환기를 안 거르는데 계약 문서는 거른다고 말한다. 연구 `krx.investable` 도 09-01 을 경계로 투자주의환기를 빼다가 넣는다(정의 단절, 표식 없음). 카테고리: 문서 불일치(층 간 계약) + 정의 단절.
- 근거: 08-31→09-01 admin 전이 집계(measured→measured True→False 37 · KOSPI derived→measured False→True 38), 37종목 sect_tp 전부 투자주의환기, fi_universe(m_20261005T165747) 투자주의환기 종목 is_admin·eligible, v4@0.1·0.2 10-02 scores 297090 `excluded=True, adv20`.
- 기존 여부: 새 발견.

### G-23 dividend_event 가 한 (법인, 사업연도, 보고서, 종류)의 여러 접수를 '첫 관측 값' 하나로 접는다 — 정정 DPS 미반영·분기결산 리츠의 연 4회 배당 중 1회만 [하]
- 상황: `stg_dividend` 에 같은 grain·라벨이 접수 둘 이상으로 실린 경우 — (a) 일일 수집이 정정본을 덧붙임(G-21 과 같은 경로) (b) 한 사업연도에 사업보고서가 여러 번 나오는 법인(분기·반기 결산 리츠, 결산월 변경 법인). 서버 stage 에서 접수 둘 이상인 (grain, 라벨) 2,034 · 값이 갈리는 것 617 · 그중 '주당 현금배당금(원)' 87(전부 11011).
- 인풋: 엠브레인 00877174 bsns_year 2026 11011 '-' — 사업보고서(2026.06) 20260921000287 DPS **90** → [기재정정] 20260929000427 DPS **45**. 01180118(417310 리츠) bsns_year 2025 — 사업보고서(2025.02·05·08·11) 4건 DPS 94·94·72·94.
- 에러 위치: `src/equity/sql/dividend_event.sql:48-54`(`picked` — 라벨마다 `thstrm IS NULL, observed_date, rcept_no` 순 첫 행 = 첫 관측·가장 작은 접수번호 값) · `:60-61·79`(식별 축 `rcept_no`·`stlm_dt`·`available_date` 는 `picked` 에서 살아남은(=값을 낸) 행들의 `max` — 라벨이 전부 첫 접수에서 뽑히면 식별 축도 첫 접수가 된다. 머리말 `:28-30` 의 '기여한 접수 중 가장 나중 것'과 글자로는 맞지만, 정정 접수는 기여하지 못하므로 정정 값·정정일이 끝내 안 실린다).
- 위험성: 엠브레인 dividend_event dps_krw 90(정정 전 값), 01180118 FY2025 dps 94(연 합계 354 중 한 회차). 배당수익률·배당성향 연구에서 정정 전 값·한 회차 값이 연간 DPS 로 쓰인다. fi 는 연간 12월 결산 행만 써서(엠브레인 6월 결산) 그리고 리츠는 sec_type 으로 eligible 밖이라 모델 영향 0. 머리말이 약속한 'PIT 은 어느 기여 행보다 이르지 않다'도 a)에서는 값이 원본·식별이 원본이라 일관되나 정정 값은 영원히 안 들어온다(B-41 정책과 반대). 카테고리: 데이터 손실 + 문서 불일치.
- 근거: a_13.py(stg_dividend 다중 접수 집계), a_14.py(엠브레인·01180118 판본별 값, dividend_event 실린 값·rcept_no).
- 기존 여부: 부분 기지. 서버 MANIFEST EG3_dividend_event(기록형, pass)가 `n_value_conflict` 647 · `n_grain_multi_rcept` 302 를 이미 세고 있고 머리말 `:26-27` 은 '승격 판단은 서버 실측 뒤'로 미뤘다 — 그 뒤 판단 기록 없음. 정정 값 영구 미반영·분기결산 리츠의 회차 소실이라는 결과는 새로 확인.

### G-24 4분기 파생이 사업보고서와 분기의 정의·기간이 같은지 보지 않는다 — 금융지주(매출 기준 상이)·반기 칸에 누계가 실린 법인에서 음수 4분기 매출 [하]
- 상황: q4 = 사업보고서 값 − Σ(1Q·반기·3Q). 매출은 tier 가 보고서마다 따로 정해져(`revenue_basis`) 사업보고서는 `banking_gross`(이자+수수료+보험 합), 분기는 `standard`(Revenue 태그)인 법인이 있고, 일부 법인은 반기·3분기 손익의 3개월 칸(`thstrm_amount`)에 누계를 싣는다(원장 `thstrm_amount = thstrm_add_amount` · 1분기 ≠ 0 인 그룹 24).
- 인풋: 한국금융지주 00432102 FY2025 CFS — 사업보고서 매출 5,939,090,000,000(banking_gross) / 1Q 5,363,814,187,996 · 반기 7,033,792,000,000 · 3Q 5,988,214,000,000(standard) → q4 **−12,446,730,187,996**. 메리츠금융지주 00860332 FY2025 q4 −10.84조. DL 00109693 FY2024 — 1Q 1.404조 · 반기 2.896조(=누계) · 3Q 4.319조(=누계) · 연간 5.615조 → q4 **−3.003조**.
- 에러 위치: `src/equity/sql/fin_std.sql:349-367`(`q4src`·`q4` 가 `revenue_basis` 일치·3개월 여부를 보지 않고 `sum(v)` 를 뺀다 — `basis_prev`(`:501-523`)는 '전년 같은 보고서'만 비교) · 게이트 `src/equity/rules_s12.py:406-421`(부분합·공개일 순서만 검사, 음수·정의 혼합 검사 없음).
- 위험성: 현판 사업보고서 q4 매출 20,163 중 **음수 272**(12월 기말 258), 연간의 −20% 보다 작은 것 160(원인: G-20 정렬 6 · 매출 기준 혼합 10 · 그 밖 150 — 누계 칸·단위 오류(G-25) 등). 연구 소비자의 TTM·분기 성장률이 금융지주·해당 법인에서 부호가 뒤집힌다. 위 3사는 fi eligible 이지만 WISE 분기 경로를 써서 모델 영향 0(DART 분기 경로는 WISE 분기 없는 종목만 — `queries.py:759`). 카테고리: silent corrupt(연구 DB) + 게이트 공백.
- 근거: a_15.py(반기·3분기 누계 칸 159·34 그룹, 1Q≠0 15·9, 음수 q4 5·6), a_16.py(음수 q4 272·258·160), a_17.py(원인 분류·예시), a_18.py(00432102 행).
- 기존 여부: 새 발견. FIELD_MAP `:36·:84` 는 '11012·11013·11014 는 3개월'을 계약으로 적는다(원천이 어기는 경우의 처리 없음).

### G-25 fin_std 에 단위가 1,000배·100만배 부풀린 보고서 행 20건이 그대로 실린다 — 단위 검산 게이트 없음 [하]
- 상황: DART 원천(XBRL)이 원 단위 값을 천·백만 배로 적은 보고서가 있다. fin_std 는 통화(`is_krw`)만 보고 크기 검산이 없다.
- 인풋: 현판 fin_std 에서 같은 법인·fs_div 의 앞뒤 보고서 모두보다 자산총계가 500배 이상 큰 행 **20**(11011 5 · 11012 6 · 11013 6 · 11014 3, 2023년 이후 10). 예: 00818472(160600) 2025 반기 CFS 매출 31,790,974,711,000,000 · 자산 148,625,362,188,000,000(앞뒤 분기 자산 1,595억·1,389억) · 00104810(007720) 2024 3Q 자산 159,266,074,177,000,000 · 00204226(032680) FY2022 사업보고서 자산 122,129,876,850,000,000.
- 에러 위치: `src/equity/sql/fin_std.sql:331-342`(격리는 non_krw·period_unresolved·rcept_lag_out_of_range·duplicate_vintage 4종뿐) · 게이트 `src/equity/rules_s12.py`(크기·연속성 검사 없음). 제안 게이트 EG12(단위 접미사)는 미구현(기존 C-12).
- 위험성: 이 행이 4분기 파생을 망가뜨리고(00818472 FY2025 q4 매출 **−31,790,938,185,728,361**) 분기 TTM·자산 성장률·ROA 를 극단값으로 만든다. 연간 행 5건은 fi `dart`(roa·total_assets) 경로에 들어갈 수 있다 — 현판 해당 종목 eligible 0 이라 모델 영향 0. 원천 오류를 격리·표식 없이 정상 값처럼 싣는 점이 문제(연구 DB silent corrupt). 덤: 00818472 반기·00295370 FY2021 의 단위 오류 판은 정정본인데 C-11 승계로 공개일이 원공시일로 당겨져 있다(정정이 수치를 바꿨는데 '재무 무관' 판정 — 기존 C-11 의 실례 추가). 카테고리: silent corrupt + 게이트 공백.
- 근거: a_18.py·a_19.py(500배 점프 41쌍 → 단독 부풀림 행 20, 목록·eligible 대조).
- 기존 여부: 새 발견(값 실측). 게이트 미구현 자체는 기존 C-12 ①.

### G-42 stage 2.5.0(10-05, '3개월 의견 없음 → analyst_count 0')이 opinion_daily EG8(v3⋈wise 일치율 ≥ 0.99)을 0.636 으로 떨어뜨린다 — 다음 전량 equity 빌드(10-06 21:20 저녁판부터)가 opinion_daily 에서 gate_failed → 패스 전체 롤백 [상]
- 상황: 커밋 0ac2f413(10-05 18:59, stage RULES_VERSION 2.4.0→2.5.0)이 WISE 요약 표의 '최근 3개월 이내에 제시된 의견이 없습니다'를 analyst_count NULL 대신 **0** 으로 싣는다. 서버 stage `stg_analyst_summary` 현판 b_20261005T100452 가 이미 이 규칙이고 stage 는 매 빌드 전 수집일을 다시 푼다(09-01 행도 0). 같은 날 손으로 다시 지은 equity 표는 coverage_daily·security 둘뿐이고(기존 C-09), opinion_daily 는 아직 옛 stage(m_20261003, NULL)로 지은 판이다. v3 미러(`stg_v3_analyst_opinions`, 동결)는 같은 경우를 NULL 로 갖고 있다.
- 인풋: 다음 체인의 `scripts/equity_rebuild_all.sh evening_20261006 --basis evening` → 빌드 순서 26번째 표 opinion_daily(`scripts/equity_order.txt:31`, 비주석 30표 중 26번째) 빌드 → EG8.
- 에러 위치: 원인 `src/stage/parsers.py:488-500`(no-opinion → analyst_count "0") ↔ 게이트 `src/equity/rules_s18.py:61-62`(_OVERLAP_COLUMNS 에 analyst_count 포함)·`:218-249`(eg8_src_overlap — 5열 IS NOT DISTINCT FROM 전부 일치해야 agree, rate < 임계면 FAIL) · 임계 `data/equity/baseline.json`(= `src/equity/baseline_locked.json:1332-1337` opinion_daily.src_overlap_agree_min **0.99**) → `src/equity/__main__.py:77-88`(gate_failed 면 rc 1) → `scripts/equity_rebuild_all.sh:74-90`(첫 실패에서 이번 패스 커밋분 rollback 후 exit) → `scripts/build_chain.sh:282-284`(equity 전량 실패 → H_EQUITY≠ok → _READY 미갱신·equity contract 생략).
- 위험성: 서버 현 stage 로 EG8 을 같은 SQL 로 재현하면 겹침 796키 중 일치 **506 = 0.636 < 0.99** — 불일치 290키 전부 'wise 0 vs v3 NULL'(나머지 4열은 일치). 따라서 다음 전량 빌드부터 매번 opinion_daily 가 폐기되고 그 패스에서 먼저 커밋된 25표(trading_calendar~consensus_daily)가 패스 시작 판으로 되돌려지고 뒤 4표(opinion_broker_daily·coverage_daily·dataset_profile·factor_readiness)는 빌드되지 않는다 → equity 현재 판이 m_20261003(+10-05 수동 b_ 2표)에 묶이고, 완료 신호·인계(latest_*)·fi/모델(수동)이 새 거래일 판을 못 받는다. 저녁판(e_)·아침판(m_) 모두 같은 원인으로 실패하므로 원인을 고칠 때까지 매일 반복된다. crit 는 notify.log 에만 남아(기존 F-08) 사람이 늦게 알 수 있다. 게이트를 고쳐도(예: analyst_count 를 겹침 비교에서 빼거나 v3 NULL↔wise 0 동치) opinion_daily.analyst_count 열은 v3 구간(04-04~08-31)의 '의견 없음' = NULL, wise 구간(09-01~) = 0 으로 정의가 갈린다(커버리지 변화 G10 계열 연구 팩터의 구조 단절). 카테고리: 운영 중단(임박) + 정의 단절.
- 근거: `b_17.py`(서버 stage 현판으로 EG8 재현) — n_overlap 796 · n_agree 506 · analyst_count 일치 506 · (wise 0 ∧ v3 NULL) 290. 현 equity opinion_daily(m_) 09-01 wise 809행 중 analyst_count NULL 297 · 0 0 → 지금은 둘 다 NULL 이라 EG8 1.0(현판 MANIFEST metrics agree_rate 1.0, n_agree_by_column.analyst_count 796). baseline_locked opinion_daily.src_overlap_agree_min 0.99. 10-05 수동 재빌드 로그는 `logs/equity/coverage_daily_20261005T100502Z.log`·`security_20261005T103717Z.log` 둘뿐(opinion_daily 재빌드 없음).
- 기존 여부: 새 발견(C-09 는 '판 섞임'까지만 — 다음 전량 빌드의 게이트 실패는 기록 없음). 06:00 이전 확인 시 오늘 저녁 21:20 체인 전에 조치 가능.

#### G-03 보강(04:08) — 대상은 sec_type `foreign`(900xxx)이 핵심
- FY2025 사업보고서 접수일: `foreign` 10종목 전부 2026-04-20~04-30(= 기말 + 120일 이내, 넘긴 곳 0) → 우리 기한(03-31) 기준 delay +20~+30 으로 '지연'. `dr`(950xxx) 6종목은 03-16~03-23 으로 90일 안. 즉 외국 지주회사 직상장(foreign)은 체계적으로 120/60일 기한을 쓰고 있고, 90/45 단일 기한이 그들을 매년 지연으로 찍는다(dr 의 분기·반기 지연은 별개로 섞여 있음).

#### G-01 보강(04:08) — 같은 접기 패턴이 다른 표에도
- `shares_outstanding`(`sql/shares_outstanding.sql:58-62`, first_write_wins = available_date 가장 이른 판본)도 원본+정정 접수 429그룹 중 값이 다른 37그룹에서 정정 값을 버린다(KRX 가 주식수 정본이라 보조 표 — 영향 작음). 갈래 A 의 G-21(fin_std)·G-23(dividend_event)과 같은 뿌리: grain 에 rcept_no 가 없는 DART 표가 '첫 판본 고정'으로 접혀 정정·재감사가 반영되지 않는다.

### G-08 ownership_snapshot 이 같은 접수 안 '이름·종류가 같은 서로 다른 행'(1우선주·2우선주, 같은 법인 주주 두 줄)을 하나로 접어 지분율을 버린다 — 기록형 n_grain_folded 625, 조치 없음 [하]
- 상황: 최대주주·특수관계인 현황(stg_hyslr)은 종류주를 '우선주' 한 낱말로 적는 법인이 있고, 같은 이름이 한 접수에 두 줄 오는 경우가 있다. grain (corp, bsns_year, reprt_code, nm, stock_knd) 에 rcept_no·행 구분이 없다.
- 인풋: 서버 stg_hyslr(stage 현판) 비집계 행 → equity ownership_snapshot(m_20261003T004545). 예: 대상홀딩스(00121941) 2015·2016 사업보고서 한 접수에 '대상홀딩스/우선주' 8,900주 3.76% 와 89,000주 6.49% 2행, '임창욱/우선주' 3.27% 와 3.14% 2행 → 각 한 행만 남음. (주)삼라(00113535) 2019 '보통주' 1.87% 와 0.00%.
- 에러 위치: `src/equity/sql/ownership_snapshot.sql:15-21`(first_write_wins → row_hash 순 1행, 주석 "0 이 아니면 그만큼 지분율이 산출에서 빠졌다는 뜻") · 게이트 `src/equity/rules_s15.py` EG3_ownership_snapshot `n_grain_folded`(기록형).
- 위험성: 접힌 그룹 494 중 같은 접수 안에서 값이 갈리는 그룹 55(버려진 쪽 지분율 하한 합 48.2%p), 접수가 둘 이상(원본+정정)인 그룹 430(첫 판본 고정 — G-01 과 같은 뿌리). dataset_profile 이 노출하는 `event.largest_holder_stake`(trmend_rate_pct, FACTORS E03·E04)를 합산·최대값으로 쓰는 연구 소비자는 지분율을 덜 센다. 모델(fi)은 이 표를 안 읽는다. 카테고리: 데이터 손실(silent) — 감시 지표가 손실을 재는데 판을 막지도, 문서의 후속 조치도 없다.
- 근거: MANIFEST EG3_ownership_snapshot `n_grain_folded=625`·`n_source_rows_gt1=494`, stg_hyslr 그룹 집계(multi_rcept 430 · val_conflict 136 · same_rcept_val_conflict 55), 위 예시 행 조회.
- 기존 여부: 새 발견.

#### G-40 보강(04:04) — 소비 뷰(v_consensus)에서는 단절이 2026-03→04 에 온다
- v_consensus 는 겹치는 달에 먼저 알 수 있던 v3(월초 관측)를 고르므로(`views.py:277-296`) 소비자가 받는 시계열은 ≤2026-03 = wise 월말, 2026-04~08 = v3 월초, 2026-09~ = wise 월초다. 005930 eps 202612: 03월 = 03-31 24,717.89 → 04월 = **04-03** 26,367(3일 차) → 05월 05-04 40,286 → … → 09월 09-01 48,338.64 → 10월 10-01 47,495.94. 즉 '1개월 리비전'이 03→04 에서는 3일치다. src='wise' 만 쓰는 소비자는 08→09 에서 0 리비전(본문). 어느 쪽이든 obs_month 한 칸의 기간이 일정하지 않다.

### G-26 v4 DY0 = '분할·무상증자 전' 주당배당금 ÷ '사건 뒤' 원종가 — 기말 뒤 주식 사건이 있는 적격 종목의 배당수익률이 share_factor 배(최대 10배) 부풀어 상위로 간다 [중]
- 상황: fi `dps` 는 사업보고서 DPS 원값(그 결산기 주식 수 기준)이고, v4 `DY0` 는 D 의 **원**종가로 나눈다. 결산기 말 이후 D 사이에 액면분할·무상증자(adj_factor share_factor ≠ 1)가 있으면 분자·분모의 주식 단위가 다르다. 현판 fi(m_20261005T165747, D=10-02) 적격 626 중 2026-01-01~D 에 split·bonus(factor_ok) 가 있고 FY2025 dps 가 있는 종목 12.
- 인풋: 포스코스틸리온 058430 — 2026-04-23 액면분할 10:1, FY2025 dps 1,085, D 종가 4,585. 나이스정보통신 036800 — 08-18 분할 5:1, dps 1,100, 종가 8,430. 엠앤씨솔루션 484870 — 06-26 무상 3.0배, dps 2,491, 종가 18,650.
- 에러 위치: `src/factor_inputs/queries.py:638-652`(`div` — `dividend_event.dps_krw` 원값, adj_factor 미조정) → `:729`(`dps`) · `src/model/engines/v4_rank.py:590`(`close_d` = fi_prices 원종가) · `:299-309`(`_dy0` = dps ÷ close_d). 계약 문서 `docs/FACTOR_INPUTS.md:94`·`config/models/v4_rank_0_2.toml:83`("최근 연간 보통주 DPS ÷ D 종가")에 주식 단위 맞춤 언급이 없다(FIELD_MAP 은 eps_basic 만 '분할 미조정'이라 경고).
- 위험성: v4@0.2 현판(m_20261005T165751) DY0 — 058430 **0.2366**(449 종목 중 1위, 맞는 값 ≈ 1,085/10/4,585 = 0.0237) · 484870 0.1336(≈0.0445) · 036800 0.1305(≈0.0261) · 340570 0.0544(≈0.0276) · 183300 0.0411(≈0.0084) · 010120 0.0143(≈0.0029). 밸류 버킷에서 '고배당'으로 상위 백분위(100·97.8·96.8)를 받는다 — 비교 모델 순위가 체계적으로 틀린다(분할·무상증자가 흔한 종목일수록). 주 모델 scope 는 WISE `dividend_yield`(WISE 가 계산)를 써서 영향 없음. 같은 원리로 전년도 DPS 를 쓰는 연구 배당수익률도 같다. 카테고리: silent corrupt(층 간 단위 계약 — 주식 수 기준 불일치).
- 근거: a_22.py(적격 종목 × 2026 주식 사건 × FY2025 dps × D 종가), a_24.py(v4@0.2 indicators DY0 값·분포 — n 449, 중앙값 0.0062, p99 0.1125, 최대 0.2366 = 058430).
- 기존 여부: 새 발견. (C-04·C-07 은 adj_factor 자체의 계수·날짜 결함, 이것은 계수가 맞아도 생기는 소비 측 미조정.)

#### G-41 보강(04:05) — 예시 행
- 188040 2026-10-02: covered=true · first_covered 09-01 · last_covered 10-02 · streak 25(전 스냅샷) · analyst_count 0 · 차트의 마지막 추정치 obs_date 2025-10-31. 008490: covered=true(09-29 부터 streak 4) · analyst_count 0 · 마지막 추정치 2025-10-31. 둘 다 원장 `ws_coverage.status_current='covered'` — 수집기 판정과 같다(즉 원장 쪽 '커버' 의미도 같은 한계).

### G-09 사업보고서 감사의견의 22% 가 '정정본' 판본으로만 실려, 원본 접수일부터 정정일까지(중앙값 77일, p90 502일) 그 해 의견이 이력에 없다 — 원본 의견은 영영 없다 [하]
- 상황: DART 감사의견 API 는 (법인, 연도, 보고서)에 대해 마지막 판본만 준다. 백필(08-26~29) 때 이미 정정된 보고서는 정정본 rcept_no·접수일로 들어왔다. equity 는 available_date = 저장된 판본의 rcept_dt 다(원본 공시일 승계 없음 — fin_std 는 e1.22.0 에서 승계를 넣었지만(C-11) audit_opinion 은 아님).
- 인풋: 서버 audit_opinion(m_20261003T004546) 사업보고서(11011) 당기 행 29,435 × disclosure_version(rcept_no → is_correction·orig_rcept_no).
- 에러 위치: `src/equity/sql/audit_opinion.sql:23`·`:65`(available_date = rcept_dt, 판본 축 없음) → fi `src/factor_inputs/queries.py:217-231`(available_date ≤ D 인 가장 최근 사업연도 → 공백 기간엔 **전년도** 의견을 쓴다).
- 위험성: 정정본으로 저장된 당기 행 6,482(22%), 의견 있는 5,956행의 원본→정정 간격 중앙값 77일·p90 502일·1년 초과 789. 그 기간 동안 연구 PIT 패널·fi 재생성(과거 D)은 그 해 감사의견을 모르고 전년도 의견을 쓴다. 원본이 비적정이고 재감사로 적정이 된 경우(G-01 의 반대 방향) 원래의 비적정은 이력 어디에도 없다 — 비적정 필터(v4 audit_adverse·E08)를 쓰는 백테스트가 당시 시장이 본 '의견거절'을 못 본다(생존 편향 쪽). 정정본 행 등급: 적정 5,723 · NULL 526 · 의견거절 120 · other 80 · 한정 33. 현재 운영 모델(10-02)에서는 영향 미미(최근 정정 소수). 카테고리: PIT 공백(지연·원본 소실) — look-ahead 는 아님.
- 근거: 위 조인 집계(n_corr_version 6,482 · 30일 초과 4,450), 간격 분위수 쿼리(n 5,956 · med 77 · p90 501.5 · >1y 789 · 2020 이후 3,482).
- 기존 여부: 새 발견(같은 원인의 fin_std 쪽은 C-11·e1.22.0, corp_event 쪽은 C-07).

#### G-42 보강(04:05) — 변경 범위
- 0ac2f413 은 stage 파서·STAGE_DESIGN·FIELD_MAP(coverage_daily 행)·stage 테스트 6파일만 바꿨다 — equity `rules_s18`(EG8 겹침 열)·`opinion_daily.sql`·equity RULES_VERSION·opinion 소비자 선언(`consensus.analyst_count`, `rules_s18.py:477-481`)은 그대로다. 로컬 테스트는 stage 만 보므로 이 충돌을 못 잡는다(서버 실데이터에서만 겹침 796키가 생김 — 절단본 겹침 5키에는 '의견 없음' 종목이 없었다, DESIGN §10 P37).
- 조치 후보(미검증): EG8 비교에서 analyst_count 를 `coalesce(x.analyst_count, 0)` 처럼 v3 NULL≡wise 0 으로 보거나, opinion_daily 의 v3 행에도 같은 정규화를 적용(정의 통일) — 어느 쪽이든 equity RULES_VERSION 상향 필요.

### G-27 외화(USD 등) 재무제표 법인은 fin_std 에서 non_krw 로 격리되는데 fi 는 퀄리티 재료를 DART 에서만 가져온다 — 두산밥캣(scope 적격)의 퀄리티 점수가 변동성 한 지표로만 계산된다 [하]
- 상황: fin_std 는 통화가 원이 아닌 그룹을 `non_krw` 로 격리한다(현판 격리 913행 · 34법인, 2024년 이후 USD 13 · CNY 10 · JPY 2 · GBP 1 법인). fi `fi_fin_summary` 연간 행의 total_assets·roa·debt_ratio·fcf·capex 는 DART(fin_std)만 재료로 쓰고 WISE 대체가 없다. scope(v3 이식) 퀄리티는 gpa·roa·fcf_assets·debt_ratio·gpa_change·std_20d 를 최신 연간 1행에서 읽는다.
- 인풋: 두산밥캣 241560(corp 01032486) — FY2024·2025 사업보고서·분기 CFS 전부 currency USD → 격리. fi(m_20261005T165747) 2025/12 연간 행: WISE 매출 87,919억은 있으나 total_assets·roa·fcf NULL.
- 에러 위치: `src/equity/sql/fin_std.sql:333`(`non_krw` 격리) → `src/factor_inputs/queries.py:653-668·721-734`(`dart` CTE 만 roa·debt_ratio·fcf·total_assets 재료) → `src/model/engines/v3_zscore.py:306-331`(`_quality_raw` — 있는 지표만 모아 점수화). 문서(FACTOR_INPUTS·DECISIONS·FIELD_MAP)에 외화 재무 법인 처리 언급 0.
- 위험성: scope 현판(m_20261005T165751) 241560 — qual_gpa·roa·fcf_assets·debt_ratio·gpa_change 전부 NULL, `quality_score` **1.14** 가 qual_std_20d(0.020, 저변동) 하나로만 나온다. 퀄리티 버킷이 '저변동 = 고퀄리티'로 치우쳐 종합 순위가 조용히 달라진다(현재 372위). 결측 표식은 E-02 대로 엑셀에 안 드러난다. 같은 처지 법인이 유니버스에 들어오면 똑같다(USD 보고 13법인). 카테고리: silent(층 간 계약 공백 — 격리 사실이 모델 입력 결측으로 바뀌는데 표식·대체 없음).
- 근거: a_25.py(적격 626 최신 연간 행 total_assets NULL 3 — 241560·477850·0011T0, 뒤 둘은 corp 매핑·신규상장 문제로 보임=기존 A-01 계열), a_26.py(fin_std `_reject/reject_reason=non_krw` 241560 행), a_28.py(scope scores 241560 행, scope 510 중 roa NULL 3).
- 기존 여부: 새 발견(격리 규칙 자체는 설계, 모델 쪽 결과는 기록 없음).

#### G-06 보강(04:07) — 정확한 상태 뒤집힘 수
- 창 안 사건이 전부 D 에 미공개이고 run<5(이 규칙이 없으면 listed)인 행 = **4,445행·885종목**(2016 이후 2,769) → 이만큼이 미래 정보로 `listed → suspended` 가 됐다(나머지 run≥5 행은 이유 라벨만 illiquid → corp_action_window 로 바뀜).

- G-42 위치 정정: 체인 쪽 정확한 줄은 `scripts/build_chain.sh:284`(equity 전량 → H_EQUITY) · `:287`(contract 생략) · `:293-297`(_READY 미갱신), 롤백은 `scripts/equity_rebuild_all.sh:74-88`.

#### G-04 정정(04:12) — 행수 표기
- '35,132행·73종목' 은 KOSPI 전 sec_type(리츠 등 포함) 기준이고, 보통주만은 연도별 합 **33,601행**(2012~2026)이다. 본문의 "KOSPI 보통주 73종목·35,132행" 을 "KOSPI 73종목·35,132행(보통주 33,601)" 으로 읽는다.

### G-28 첫 사업연도가 1~10개월인 신설 법인(분할 신설·합병 상장)의 '12월 사업보고서'가 fi 에서 12개월 연간 행으로 쓰인다 — scope 퀄리티 gpa_change 18.5·ROA 과소 [중]
- 상황: 분할 신설·SPAC 합병 등으로 설립된 법인의 첫 사업보고서는 설립일~12월 말(1~10개월)이다. fin_std 는 `period_start` 로 이 길이를 갖고 있지만(`fin_std.sql:313`), fi `fi_fin_summary` 는 결산월 12 인 사업보고서를 기간 길이와 무관하게 'annual' 로 싣고 기간 길이 열을 넘기지 않는다. WISE 연간 표도 같은 짧은 기간을 그 해 실적으로 싣는다. 현판 fin_std 2024년 이후 12월 기말·12개월 미만 사업보고서 약 71행.
- 인풋: fi(m_20261005T165747) 적격 종목 — GS피앤엘 499790 FY2024 = 2024-12-01~12-31(1개월, 매출 383억·매출총이익 54억) / FY2025 12개월(4,817억·1,139억) · 한화비전 489790 FY2024 = 09-01~12-31(4개월) · 삼성에피스홀딩스 0126Z0 FY2025 = 11-01~12-31(2개월, 순이익 853억 · 자산 77,156억) · SK이터닉스 475150 FY2024 = 10개월.
- 에러 위치: `src/factor_inputs/queries.py:680-686`(`annual` — `substr(period, 6, 2) = '12'` 만 보고 기간 길이를 안 본다) · `:653-668`(`dart` CTE 가 `period_start` 를 버린다) · `:713-740`(계약 열에 기간 길이·부분연도 표식 없음) → `src/model/engines/v3_zscore.py:306-331`(`_quality_raw` 가 최신 2기로 gpa·roa·fcf_assets·gpa_change 계산).
- 위험성: scope 현판(m_20261005T165751) — 499790 `qual_gpa_change` **18.47**(scope 510 중앙값 0.021 · p99 5.36) → 퀄리티 0.329, 489790 gpa_change 2.30, 0126Z0 은 2개월 순이익 ÷ 연말 자산이라 qual_roa 1.11·qual_gpa 0.012 로 과소(연환산하면 약 6배). 네 종목 모두 scope 510 안(233·236·303·366위). 결측이 아니라 '틀린 연간 값'이라 표식 없이 순위에 들어간다. 분할 신설·합병 상장이 생길 때마다 되풀이된다. v4 EP(WISE 연간 순이익 ÷ 시총)도 같은 부분연도 값을 쓴다. 원본 v3 도 WISE 연간 값을 같은 방식으로 썼다면 동작 동등(추정 — 미확인). 카테고리: silent corrupt(층 간 의미 계약 — '연간' 정의에 기간 길이 조건 없음).
- 근거: a_32.py(사업보고서 기간 길이 분포, 2024+ 짧은 12월 연간 목록·eligible), a_33.py(fi 연간 행·scope 점수·gpa_change 분포).
- 기존 여부: 새 발견. fi GAPS·FACTOR_INPUTS 는 비12월 결산(U22)만 다룬다.

#### G-42 독립 재확인(04:09, 본 에이전트)
- 0ac2f413(10-05 18:59, stage 2.5.0)은 HEAD b220060a 의 조상 → 서버 배포본에 들어 있다. stage 현판 `stg_analyst_summary` = b_20261005T100452(2.5.0 규칙), `stg_v3_analyst_opinions` = m_20261003T003453.
- 별도 SQL(두 stage 표를 (ticker, 날짜)로 직접 조인, 각 1행)로 겹침 **796키(전부 2026-09-01)**, analyst_count 일치 **506**, wise 0 ∧ v3 NULL **290** — 갈래 B 수치와 같다. wise 09-01 809행 중 0 이 297·NULL 0(10-02 도 0 276·NULL 0).
- `src/equity/sql/opinion_daily.sql:49·67` 이 두 원천의 analyst_count 를 변환 없이 싣고, `rules_s18.py:218-249` EG8 은 5열 IS NOT DISTINCT FROM 전부 일치를 요구, `build.py:232-245` 는 FAIL 하나면 GATE_FAILED → 결론 동일(다음 전량 빌드 opinion_daily 폐기 확정적).

#### G-28 독립 재확인(04:13, 본 에이전트 — 입력 쪽만)
- 499790(corp 01882845) fin_std 사업보고서: FY2024 period_start **2024-12-01**~12-31 매출 383억·매출총이익 54억 / FY2025 01-01~12-31 4,817억·1,139억. fi_fin_summary(현판) annual 2024/12·2025/12 두 행이 기간 길이 표식 없이 그대로 실림(roa −0.05 → 1.22). 점수 쪽 수치(gpa_change 18.47)는 갈래 A 기록을 따른다(본 에이전트 미재현).

### G-29 fin_std capex_q(분기 capex = 누계 차)가 보고서마다 다른 부호를 그대로 빼서 크기가 틀린다 — LG화학 FY2025 4분기 capex 24.3조(실제 약 3조), 계약 문서 경고 없음 [하]
- 상황: `capex_ytd` 의 `standard` tier 는 원천 부호를 그대로 두고(`ppe_parts` 는 크기 합), 같은 법인도 분기보고서는 음수·사업보고서는 양수로 적는 경우가 있다. `capex_q` = 자기 누계 − 직전 보고서 누계라 부호가 다르면 두 크기의 합이 된다.
- 인풋: 현판 fin_std — 00356361(LG화학 051910) FY2025 CFS 3Q 누계 −10,679,577,000,000 → 사업보고서 +13,660,731,000,000 → capex_q **24,340,308,000,000**(부호를 맞추면 약 2.98조). 00138279(S-Oil 010950) FY2025 3Q −2.66조 → 연간 +3.91조 → capex_q 6.57조.
- 에러 위치: `src/equity/sql/fin_std.sql:377-389`(`cfq` — `s.v - p.v`, 부호 정규화 없음) · 부호 규약 `:242-254`(`pick` 원부호 / `sum_abs` 크기). 계약 `docs/EQUITY_FIELD_MAP.md:84`(`cf_operating_ytd`/`_q` 를 '지원'으로 적고 capex_q 경고 없음).
- 위험성: 현판 capex_q 63,811행 중 직전 보고서와 누계 부호가 다른 것 **824** · 누계가 줄어든(capex_q<0, 누계>0) 것 2,219 · capex_basis 가 직전과 다른 것 603. 연구 소비자의 분기 FCF·TTM capex 가 대형주에서도 수 배 틀린다. fi 는 이 열을 쓰지 않는다(`src/factor_inputs/queries.py:527-529` "부호가 섞인 누계의 차라 크기가 틀어진다" — 원인을 알고 피함) → 모델 영향 0. 카테고리: silent corrupt(연구 DB) + 문서 불일치.
- 근거: a_36.py(capex_q 정합 집계), a_37.py(2025 부호 혼합 상위 예시).
- 기존 여부: 부분 기지 — fi docstring 이 절단본 003540 예로 원인을 적었으나 equity 쪽 계약·게이트·TECH_DEBT 에는 기록 없음.

#### G-26 독립 재확인(04:14, 본 에이전트)
- 058430: adj_factor split 2026-04-23 share_factor 10.0(ok, available 04-23), 종가 04-22 48,000 → 04-23 5,090 → 10-02 4,585. fi_fin_summary 2025/12 dps **1,085**(분할 전 주식 기준). v4@0.2 indicators DY0 raw **0.23664** = 1,085 / 4,585, pct 100.0(449 종목 중 최대, 중앙값 0.0062). 036800 0.1305(pct 96.8)·484870 0.1336(97.8) 도 기록과 같다 → 결함 확정.

#### G-21 독립 재확인(04:15, 본 에이전트)
- stg_fin: 01472930 FY2025 CFS 원본 20260319001177(관측 08-26)·정정 20260928000253(관측 09-28) 두 판 / 01274310 FY2024 CFS 원본 20250331002328(294줄)·정정 20260930000004(219줄). fin_std 현판은 두 법인 모두 **원본 rcept_no** 행(01472930 op_profit 267,873,584 · 01274310 FY2024 total_equity NULL) → 기록과 같다.

#### G-07 보강(04:16) — 문서 근거 추가
- `docs/FACTOR_INPUTS.md:115` 도 "is_admin = admin_state — 관리종목(KOSDAQ 투자주의환기 소속부 포함)" 이라 적는다. v4 는 제외 플래그를 adv20 보다 먼저 본다(`src/model/engines/v4_rank.py:536-549`) — 297090 이 계약대로 is_admin=True 였다면 제외 사유가 'admin' 이었을 것(결과는 같은 제외).


## 요약(심각도순)
- 진행: 본 에이전트(G-01~G-09: 감사의견·공시기한·유니버스 상태·지분 표) + 갈래 A(G-20~G-29: fin_std·dividend_event·fi_fin_summary) + 갈래 B(G-40~G-42: 컨센서스·의견·커버리지·업종). 상·중 5건 중 G-42·G-21·G-26·G-28(입력 쪽)은 본 에이전트가 서버에서 독립 재현했다.

| ID | 심각도 | 한 줄 | 카테고리 | 지금 모델 영향 |
|---|---|---|---|---|
| G-42 | 상 | stage 2.5.0 이 analyst_count 를 NULL → 0 으로 바꿔 opinion_daily EG8(v3⋈wise 일치율 0.99)이 0.636 → 다음 전량 equity 빌드(10-06 21:20 저녁판~)가 매번 실패·패스 롤백 | 운영 중단(임박) | equity·fi 새 판 정지 |
| G-28 | 중 | 첫 사업연도 1~10개월 신설 법인의 12월 사업보고서가 fi 연간 행으로 쓰임 → scope 퀄리티 왜곡(GS피앤엘 gpa_change 18.47 등 scope 510 안 4종목) | silent corrupt | **scope 4종목** |
| G-21 | 중 | 일일 재수집으로 정정본이 쌓인 재무 그룹에서 fin_std 가 원본 값에 고정, 줄 순서가 바뀐 계정은 모호 NULL | 데이터 손실·silent | 적격 0(증가형) |
| G-26 | 중 | v4 DY0 = 분할·무상증자 전 DPS ÷ 사건 뒤 원종가 → 배당수익률 최대 10배(포스코스틸리온 1위) | silent corrupt | v4(비교) 12종목 |
| G-04 | 중 | KOSPI 관리종목 파생 창이 '지정 우려·예고·해소' 안내로만 켜짐(우선주 안내가 보통주에) — 한화·삼성중공업 등 365세션 오탐, 실제 관리종목 재현율 1/39 | silent corrupt(연구 유니버스) | 0(09-01~ 측정값) |
| G-01 | 하 | audit_opinion first_write_wins 로 재감사 결과(의견거절→적정) 미반영 → fi audit_adverse 고착(삼부토건·이오플로우) | silent(낡은 값) | 0(비적격) |
| G-02 | 하 | 연결/별도 의견 충돌 grain 을 row_hash 순으로 1행 — 원티드랩 한정 소실, 문서의 'grain 재개방' 약속 미이행 | silent·문서 | 0 |
| G-03 | 하 | 법정기한 90/45일 단일 — 외국법인(foreign, 120/60일)이 매년 filing_late | 열 의미 위반·문서 | 0(v4 common 만) |
| G-05 | 하 | 정리매매 창이 '정리매매 보류·무효 판결'로 안 닫힘 — 192410 이 6년간 정리매매 중 | silent(연구 유니버스) | 0 |
| G-06 | 하 | corp_action_window 가 미공개 미래 사건(최대 D+45)으로 status 결정 — 4,445행 listed→suspended | look-ahead(라벨)·문서 | 0 |
| G-07 | 하 | 09-01 마스터 전환으로 admin_state 에서 KOSDAQ 투자주의환기 37종목이 빠짐 — fi 계약·FACTOR_INPUTS 는 '포함' | 문서 불일치·정의 단절 | 0(297090 은 adv20 로 제외) |
| G-08 | 하 | ownership_snapshot 이 같은 접수 안 동명·동종 행(1·2우선주)을 접어 지분율 소실(n_grain_folded 625) | 데이터 손실 | 0(fi 미사용) |
| G-09 | 하 | 사업보고서 감사의견 22% 가 정정본 판본만 — 원본→정정 중앙값 77일 공백, 원래 의견 소실 | PIT 공백 | 미미 |
| G-20 | 하 | 비12월 결산 법인의 q4_derived·cf_q 가 다음 회계연도 분기를 뺌(65법인) | silent corrupt | 0(비적격) |
| G-22 | 하 | fi dps 의 stock_knd 3낱말 필터로 KCC 등 DPS NULL → v4 무배당 취급 | silent | v4 일부 |
| G-23 | 하 | dividend_event 첫 관측 고정 — 정정 DPS·분기결산 리츠 연 4회 배당 소실 | 데이터 손실 | 미미 |
| G-24 | 하 | q4 파생이 매출 기준·3개월 여부 미확인 → 음수 4분기 매출 272건 | silent corrupt | 0 |
| G-25 | 하 | 단위 1,000배·100만배 부푼 fin_std 행 20건 무검산 | silent corrupt | 0 |
| G-27 | 하 | 외화 재무(non_krw) 격리 → 두산밥캣 scope 퀄리티가 변동성 한 지표로만 | 결측 | scope 1종목 |
| G-29 | 하 | capex_q 가 부호 섞인 누계 차(LG화학 4Q 24.3조) — 계약 경고 없음 | 문서·silent | 0(fi 미사용) |
| G-40 | 하 | consensus_daily wise 관측월 점이 09월부터 월말→월초로 단절(08→09 리비전 가짜 0) | 정의 단절·문서 | 0(fi 미사용) |
| G-41 | 하 | coverage_daily.covered 가 '차트 창 안 값 존재' — 커버 상실이 최대 11개월 늦게 보임 | 문서·silent | 0 |

- 집계: 상 1 · 중 4 · 하 17 = 22건. 공통 뿌리 하나: grain 에 rcept_no 가 없는 DART 표의 '첫 판본 고정' 접기(G-01·G-08·G-21·G-23, shares_outstanding 보강) — 백필 구간은 '정정본만'(G-09·C-07·C-11), 일일 구간은 '원본만'으로 같은 표 안 판본 정책이 둘이다.

## 문제없음 확인
- (도우미) 갈래 A 가 04:00 에 qh.py 의 `_reject/` 혼입을 고쳤다. 본 에이전트가 04:00 전에 읽은 표(audit_opinion·disclosure_version·universe_daily·adj_factor·stg_disclosure·stg_audit)는 `_reject/` 파케이가 0개라 G-01~G-09 수치는 영향 없음.
- (감사·공시) fi filing_late 의 휴장 보정 ASOF 정상(2024-05-15 부처님오신날 기한 → 05-16 제출 1,540건 지연 아님), 국내 법인 지연율 1.2~1.3%(2023+). `[첨부추가]` 접두 2,892행은 원본 접수 자체라 기한 판정 왜곡 없음. v4 10-02 filing_late 제외 2건은 국내 법인.
- (유니버스) admin_state NULL 0, 09-01 이후 halt_state=true 인데 거래한 행 0, 마스터 날짜 = 06:00 스냅샷 당일(개장 전 정보) → look-ahead 없음. 09-01 측정 전환 전후 suspended 127 → 127 연속(halt 라벨만 65 → 124, 늘어난 59종목은 원래 illiquid·corp_action 으로 suspended). 비ETF market NULL 0. 분할·병합에서 주식수만 먼저 바뀌어 시총이 튀는 날 없음(주식수 2배 변동·종가 불변은 연 4~12건, 정지 중 대규모 증자).
- (지분) holder_daily 접힘 63행 값 충돌 0, rate 0~100, 음수 수량 0. shares_outstanding available < stlm 0, 음수 0, 항등 위반 4.
- (재무, 갈래 A) fin_std·dividend_event 선언 grain 중복 0, 미래 공개일 0, 공개일 ≤ 기말 0, q4·cf_q 공개일 역전 0. 삼성전자 FY2024 q4 매출 검산 일치. fi CFS 우선 선택으로 생긴 결측 0, DART 분기 경로를 쓰는 적격 종목 0, 적격 종목이 격리로 잃은 재무 행 0(non_krw 두산밥캣 제외 — G-27). dividend 의 `bsns_year||'/12'` 폴백 실발동 0.
- (컨센서스·의견·업종, 갈래 B) 다섯 표 자연키 중복 0, est_min ≤ mean ≤ max 위반 0, available < obs 0, opinion_broker change_pct % 단위 77,544행 검산, WICS 유동시총 ÷ 유동주식수 = 그날 KRX 종가, fi 10-02 eligible 626 중 업종 NULL 0·n_analysts NULL 0, scope 형 510(D.md 일치), n_analysts 의 last_fresh ASOF 정확.

## 가설
- (본) 2024-11-15 3분기보고서 다수가 기한 하루 뒤 접수 — 실제 지연인지 자정 넘김 접수인지 미확인. dr(950xxx) 분기·반기 지연이 해외 특례인지 실제 지연인지 미확인(법령 대조는 외부 조회 금지). halt_state 파생 규칙의 '지정일 자체 거래는 못 닫음'(연 150~400행)은 DESIGN §4-1 에 적힌 한계라 결함으로 세지 않음.
- (갈래 A) H-A1 1~3월에는 WISE 연간이 DART 사업보고서보다 먼저 들어와 scope 퀄리티 DART 재료(roa·debt·fcf·total_assets)가 대량 결측일 수 있음(1월 스냅샷 없어 미검증). H-A2 G-21 경로에서 sum 계정(lease_liab 등) 이중계상 가능(실례 미확인).
- (갈래 B) v_consensus 기본 랙 0 주석 vs rules_s17 랙 설명 불일치(워크벤치는 dataset_profile 랙 1 로 막힘). consensus_daily EG8 현판 0.95(임계 0.93) — 규약 문자열 '값 충돌 없음'은 절단본 기준이라 낡음. G-42 와 같은 모양(지우지 않는 원장 위 폐기형 게이트 — 기존 B-09 계열)의 잠재 지점: consensus_daily `n_wise_band_violation`(지금 0). sector_snapshot 백필 없음으로 v4 재생성 09-01~09-17 이 업종중립 없이 계산됨(flag 표시, 엑셀 영향 없음). dataset_profile 증거 문자열('wise 2일') 낡음.

## 미조사
- (본) treasury_stock EG3 `n_ledger_total_mismatch` 651 원인, holder_daily 의 2024-08-26 하한(majorstock 도 2년 창인지), signal_halt '…기간변경' 류 공시 표본 대조.
- (갈래 A) 보조 계정(depreciation·interest_expense·borrowings·lease_liab) 대응표 정확성, eps_basic_q4_derived 의미, B-40·C-11 현판 재측정(기존), fi WISE 경로 기간 라벨(stage 영역), 결산월 변경 법인의 과거 판 재생성.
- (갈래 B) 워크벤치 어댑터의 opinion·consensus 읽기(영역 H), dataset_profile·factor_readiness 수치 대조, consensus_daily EG9 임계 근거.
- 공통: G-42 조치 후보(EG8 에서 v3 NULL ↔ wise 0 동치 또는 정의 통일 + equity 판본 상향)는 검증하지 않았다. 06:00 전 서버 쿼리 마감 규칙에 따라 05:30 이후 확인 없음.

## 끝
