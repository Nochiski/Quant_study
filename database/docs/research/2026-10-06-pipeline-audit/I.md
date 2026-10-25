# 영역 I — stage 파서·규칙의 행 단위 의미 · 일일 수집기 미조사분

- 범위:
  1. `src/stage/parsers.py` WISE 블롭 파서 전부(consensus annual/quarterly/monthly/matrix, fin_wise cF3002·cF4002, fin_wise_q, analyst summary/broker, 그 밖) — 원장 `wisereport.db` ws_raw 원문 ↔ 같은 (종목, 수집일) stage 행 표본 대조
  2. `src/stage/parsers_doc.py`(문서층)·`src/stage/rules_dart_events.py`(DART 주요사항 15표) — 원장 dart.db 원문 ↔ stage 행 표본 대조
  3. `src/stage/rules_kis.py`·`rules_kiwoom.py` 단위·부호·날짜 규칙 표본 대조
  4. 일일 수집기 미조사분 — `src/backfill_docs.py`(키 선택·재시도·실패 기록), `src/backfill_kis.py` classify·normalize, 실패가 성공으로 기록되는 경로
- 저장소: ~/orca/workspaces/Quant_study/v3-merge/database (feat/v3-merge, b220060a)
- 서버: kael-server ~/quant-ledger (읽기 전용, sqlite -readonly / duckdb 메모리+read_parquet)
- 시작: 2026-10-06 03:48 KST

## 결함

### I-01 stg_fin_wise.fs_basis(=블롭 머리 FIN)는 '주재무제표'가 아니라 가장 오래된 칸(DATA1, 5년 전)의 기준이다 — 최근 실적 칸과 기준이 반대인 종목 약 100곳(12.5%) [하]
- 상황: cF3002·cF4002 pkey='Y' 응답은 기간 칸마다 기준 이름표('2021/12<br />(IFRS별도)', '2025/12<br />(IFRS연결)')가 따로 붙고, 머리에 `FIN` 하나가 있다. 파서는 `FIN` 을 행 전체의 `fs_basis` 로 싣는다. DQ-10(플랜 2026-09-24 v3-merge:123)·`rules_wise.py:129-134` 주석은 "09-26 이후 수집분의 fs_basis 라벨은 주재무제표(연결/별도 판정에 써도 된다)"로 적었다.
- 인풋: 09-26~10-02 수집분 cF3002 'Y' 블롭(수집일마다 약 800종목). 예: 0007C0 10-02 — YYMM = 2021/12(IFRS별도)·2022~2025/12(IFRS연결)·2026/12(E)(IFRS연결), FIN = 'IFRS별도'(같은 날 c1050001 T2Y 의 행별 MAIN 은 전부 'IFRS연결'). 072020 — 2021~22 연결·2023~26 별도, FIN = 'IFRS연결'.
- 에러 위치: `src/stage/parsers.py:369`(`fs_basis = top.get("FIN")` 를 모든 행·모든 칸에 부여) · `src/stage/rules_wise.py:169`(fs_basis 열, 기간별 기준 파생 없음 — 기간별 basis_i 는 stg_fin_wise_q 에만 있음 `rules_wise.py:181-190`) → 소비: `src/factor_inputs/queries.py:576`·`:735`(연간 fi_fin_summary 행 전 기간의 fs_basis = 이 값) · `src/compat/mappings.py:403`·`:455`·`:475`(v3 accounting_standard) · `src/model/contracts.py:170`(계약 주석 "연결 | 별도 | GAAP개별(DQ-5·DQ-10)").
- 위험성: silent corrupt(라벨)·문서 불일치. 10-02 수집분 808종목 중 101종목에서 2025/12(최근 확정 실적) 칸의 기준이 fs_basis 와 다르다 — 'IFRS별도' 로 찍혔지만 최근 칸이 연결 56곳(첫 칸이 빈 9곳 포함), 'GAAP개별' 로 찍혔지만 최근 칸이 IFRS연결 27·IFRS별도 10곳(GAAP개별 37곳 전부 최근 칸은 IFRS), 'IFRS연결' 로 찍혔지만 최근 칸이 별도 8곳. fi 연간 행과 v3 accounting_standard 가 이 종목들의 최근 실적을 반대 기준으로 표기한다. 지금 엔진의 기준 판정(`v4_rank._basis_kind`)은 분기 행(stg_fin_wise_q 칸별 basis)만 써서 점수 영향은 없지만, DQ-10 이 허용한 '라벨로 연결/별도 가르기'(예: 지주사 별도 제외를 연간에도 적용)를 하는 순간 약 12% 종목이 틀리게 갈린다. DQ-10 의 '전환 141종목(별도 101·GAAP개별 40)'도 '5년 전 칸 기준'을 주재무제표로 읽은 것이다 — 10-02 기준 fs_basis 'GAAP개별' 37곳은 전부, 'IFRS별도' 115곳 중 56곳은 최근 칸이 다른 기준이다. 수집기 주석(`src/backfill_wise.py:347-351` "기준은 YYMM 라벨의 (IFRS연결)/(IFRS별도) 로 stage fs_basis 에 그대로 남는다")도 실제(머리 FIN 하나)와 다르다.
- 근거: 원문 블롭(`ws_raw` cmp_cd=0007C0·072020, fetched_date 2026-10-02, ep cF3002, pkey 'Y') YYMM·FIN 직접 디코드. stage 쿼리(stg_fin_wise current m_20261003…, seq=0·ep='cF3002'): 09-26~10-02 6수집일 모두 `FIN = period_label_1 의 기준` 이 라벨 있는 행 100%(불일치 0 — 787~798행/일), `period_label_5 기준 ≠ fs_basis` 98~102행/일, period_label_1 이 빈 종목 10.
- 기존 여부: 새 발견. DQ-10 은 09-26 이전 라벨이 '요청 파라미터'였다는 점만 기록했고, 이후 라벨(FIN)의 의미가 '첫 칸 기준'이라는 점은 기록이 없다.

### I-02 stg_consensus_matrix 가 WISE 의 '과거 컨센서스 없음 = 0.0' 표기를 실제 값 0 으로 싣는다 — 매출·영업이익·순이익 3계정만(다른 6계정은 null) [하]
- 상황: c1050001_data `T4:YYYYMM` 응답의 VAL2~5(1w·1m·3m·1y 전 컨센서스)는 그 시점에 컨센서스가 없으면 EPS·BPS·PER·PBR·ROE·투자의견은 `null`, 매출액·영업이익·순이익(억원 3계정)은 `0.0` 으로 온다. 파서는 둘을 구분하지 않고 `value` 로 싣고, stage 규칙에 0 결측 처리가 없다.
- 인풋: 10-02 수집 000500 T4:202612 원문 — 매출액 VAL1 33482.0·VAL4 29455.0·**VAL5 0.0**, 영업이익·순이익 VAL5 0.0, 같은 블롭 EPS·PER·BPS VAL5 `null`. 같은 날 cF5001 202612 원문은 2026/06/30 까지 select_item 이 전부 `null`(커버 시작 07-31) — 1년 전 컨센서스가 없었다는 뜻.
- 에러 위치: `src/stage/parsers.py:306`(`value=_s(r.get(f"VAL{i}"))` — 0.0 을 그대로) · `src/stage/rules_wise.py:122`(value NUMERIC, `zero_is_missing` 없음) → 소비: `src/factor_inputs/queries.py:405-433`(fi_consensus 1w·1m·3m 값을 그대로) → `src/model/engines/v4_rank.py:82-83`·`:429-436`(REV_OP_1M/3M·REV_NI_1M/3M — prev 0 이면 `분모≤0` 결측) · `src/model/engines/v3_zscore.py:153-155`(prev 0 → 변화율 0.0).
- 위험성: silent corrupt(0 과 NULL 혼동, 잠복). stage 행은 "3개월 전 영업이익 컨센서스 = 0억원" 이라는 거짓 값을 싣는다. 지금 엔진은 우연히 결측처럼 다루지만(v4 = 결측, v3 = 변화 없음) v4 의 결측 사유가 '원천 없음'이 아니라 '분모≤0' 으로 찍혀 신규 커버 종목과 진짜 음수·0 분모 종목이 섞인다. 이 표를 직접 읽는 다른 소비자(연구 DB·워크벤치, 예정된 equity `consensus_revision` 승격 W1-a S17b)가 (현재 − 과거)/|과거| 를 0 분모 보호 없이 쓰면 무한대·극단 리비전이, 차이(현재 − 과거)를 쓰면 '컨센서스 신규 = 대폭 상향'이 된다. 10-02 한 수집일 규모: 1y 0 이 매출 770·영업이익 789·순이익 804행(그 계정 비NULL 의 44%), 3m 251·257·260, 1w·1m 67·68·70 — EPS 는 1y 0행·3m 1·1m 2.
- 보충(현재값 VAL1 도 같은 표기): 10-02 current 0 인 5셀 — 013890 2028/12 순이익·EPS, 107640 2026/12 순이익·EPS, 246960 2026/12 영업이익 — 은 같은 날 T2Y 의 같은 칸이 전부 빈칸(NULL)이다. 즉 EPS 도 current 에서는 0 = 없음으로 온다. 이 종목들은 지금 fy 신선도(T2Y op·ni 둘 다 필요)에서 빠져 점수 영향이 없지만, 신선한 종목의 current 가 0 표기로 오면 v4 `EP_FWD = 0`(실값으로 순위에 들어감)·`REV_NI = (0 − 과거)/|과거| = −100%` 가 된다(`v4_rank.py:611`·`:613-621`).
- 근거: 원문 디코드(`ws_raw` 000500 2026-10-02 c1050001_data `T4:202612`, cF5001 `202612`; 024850 `T4:202612` 매출 VAL4·VAL5 0.0). stage 쿼리(stg_consensus_matrix current, fetched_date 2026-10-02): `count(*) FILTER (WHERE value = 0)` 를 acc_cd×lookback 별로 — 위 수치. 1y 비NULL 수가 매출·영업이익·순이익은 current 와 같고(1,739·1,804·1,789) EPS 는 1,124 로 줄어드는 비대칭이 '0 = 없음' 표기를 보여 준다.
- 기존 여부: 새 발견. B.md '문제없음'의 '빈칸 = 0 혼동 없음' 점검은 요약·브로커·monthly 만 봤다. v3_zscore 주석(`v3_zscore.py:16`)은 v3 의 '0 이면 변화율 0' 규약만 적었다.

### I-03 WISE 파서의 '모양 이탈' 계수 4종은 기록만 되고 G8 이 판정하지 않는다 — 라벨 형식이 바뀌면 기간·추정 구분이 조용히 NULL 이 되고 유니버스에서 빠진다 [하]
- 상황: 파서는 원문 모양이 실측과 다르면 행은 내되 파생 칸을 NULL 로 두고 계수만 센다 — T2Y/T2Q 기간 라벨이 `^\d{4}\.\d{2}\((A|E)\)$` 가 아니면 `period`·`period_kind` NULL + `n_label_unparsed`, cF5001/5002 항목명이 EPS·매출액이 아니면 metric 'parse_failed' + `n_metric_unknown`, cF3002/4002 YYMM 길이 이탈 `n_label_shape_other`, T4 의 VAL6 이상 `n_extra_val_slots`. G8 은 `n_parse_failed`·`n_value_mismatch`·방출=원장 행수만 본다.
- 인풋: WISE 가 라벨 표기를 바꾸는 날(예: '2026.12(E)' → '2026/12(E)' 또는 '(P)' 잠정 표기 도입)의 저녁·아침 빌드. 현재 판(m_20261003…) 계수: annual·quarterly n_label_unparsed 0, matrix n_extra_val_slots 0, fin_wise n_label_shape_other 3, monthly n_metric_unknown 21,357(전부 categories 가 빈 cF5002 빈 차트 — 표본 300종목 확인, 행 0 이라 무해).
- 에러 위치: `src/stage/gates.py:230-245`(G8 판정 항목) ↔ 계수 발생처 `src/stage/parsers.py:229-236`·`:101-104`·`:367-368`·`:297-298`. 소비: `src/factor_inputs/queries.py:135-141`(커버 신선도 = `period_kind = 'E' AND period = fy` 인 행이 있어야 fresh) · `:458-461`(fi_consensus_annual) · `src/compat/mappings.py:502`.
- 위험성: silent 데이터 손실(잠복). 라벨이 바뀐 종목은 stage 가 통과한 채 `period_kind` 가 NULL 이 되어 fi 신선도에서 fresh 를 잃고 유예(G 거래일) 뒤 유니버스에서 'lapsed' 로 빠진다 — 일부 종목만 바뀌면 원인 신호 없이 유니버스가 줄어든다(전 종목이면 유니버스 붕괴로 드러난다). 현재 발생 0.
- 근거: 서버 MANIFEST current 판 G8 metrics(위 수치) · `w9.py` 표본(10-02, 300종목 cF5001/5002 차트 이름×categories 유무 집계: 이름 '' 인 cF5002 차트 102개 전부 categories 비어 있음) · 코드 대조.
- 기존 여부: 새 발견(B-08 8번은 G8 의 커버 급락 감지 부재, B-09 는 반대로 G8 이 1행에 표를 버리는 문제 — 모양 이탈 계수 미판정은 기록 없음).

### I-04 WISE stage 규칙·명세의 수집 시각 서술이 원장과 다르다 3건 [하]
- 상황: WISE 수집은 09-11(06:06 KST)까지 아침 06:00 체인, 09-14(18:11 KST)부터 저녁 18:05 슬롯이다(ws_run_log). 세 문서·주석이 이 전환을 다르게 적었다.
- 인풋/에러 위치(문서 ↔ 원장):
  1. `src/stage/rules_wise.py:52` `lag_known=True  # 06:00 KST 수집 = 그날 장 시작 전 가용 (측정된 수집 시각)` · `docs/STAGE_DESIGN.md:286` "WISE — 06:00 수집이라는 지식은 카탈로그로" ↔ 09-14 뒤 ws_raw.fetched_at 은 전부 09:05 UTC(18:05 KST, 장 마감 뒤). 내용 기준일은 두 시기 모두 fetched_date 직전 영업일(stg_analyst_summary.base_date·stg_consensus_matrix.base_date 09-01~10-02 수집일마다 단일값 = 전 영업일)이라 '그날 장 시작 전 가용' 이라는 결론은 맞지만, 근거('측정된 06:00 수집')는 더 이상 측정이 아니다.
  2. `docs/STAGE_SPEC.md:463-466` "09-11 부터는 D 저녁(18:05)" · "09-11 아침 06:00 은 마스터만 돌았다" ↔ ws_run_log `2026-09-10T21:06:08 full 2,610종목 19,416요청`(= 09-11 06:06 KST WISE 전량), 첫 저녁 런은 `2026-09-14T09:11:04`. fetched_date 09-11 행 45개(005930·000660·035720)의 fetched_at 이 전부 `2026-09-10T21` 시대.
  3. `src/stage/parsers.py:5` "blob 이 1.3만 개라 수 초다" ↔ 지금 판 G8 n_blobs — monthly cF5001 159,057·cF5002 60,516(약 22만), fin_wise cF3002·cF4002 각 20,172(약 4만) · 소요 수백~천 초(B-01·B-03).
- 위험성: 문서 불일치. 1·2 는 WISE 의 PIT(가용일) 근거를 읽는 후속 세션이 '측정된 장전 수집'으로 오해하거나, 09-11 경계에 존재하지 않는 하루 공백·어긋남을 찾게 만든다(값 축은 두 시기 모두 '수집일 = 기준일 + 1영업일'로 연속).
- 근거: `sqlite3 -readonly wisereport.db "select * from ws_run_log where run_at between '2026-09-09' and '2026-09-15'"` → 09-09T21:04·09-10T21:06·09-14T09:11 세 줄 · `ws_raw` 005930 c1010001 fetched_at 이력(09-03~09-11 21:00 UTC, 09-14~ 09:05 UTC) · stage base_date 분포 쿼리.
- 기존 여부: 새 발견(STAGE_SPEC §2-21 이 전환 자체는 기록 — 날짜와 '마스터만' 서술이 틀림).

### I-21 숫자 칸의 소수 자릿수가 선언 scale 을 넘으면 stage 가 조용히 반올림한다 — 우선주 무상 배정비율(scale 2)이 대표 자리 [하 · 잠복]
- 상황: 빌더는 원장 VARCHAR 를 `TRY_CAST(replace(x, ',', '') AS DECIMAL(p+2, s))` 로 바꾼다. 정수부가 넘치면 NULL → `cast_failed` → G2(임계 0)로 표를 버리지만(시끄러운 실패), **소수부가 scale 을 넘으면 duckdb 가 반올림한 값을 돌려줘 miss_kind 도 G2 도 아무것도 남기지 않는다**. (p,s) 는 09-02 survey 시점 최대 자릿수다. `stg_event_fric.nstk_ascnt_ps_estk_ratio`(우선주 1주당 신주배정 주식수)는 scale 2 로 선언됐는데, 같은 뜻의 `stg_event_pifric.fric_nstk_ascnt_ps_estk_ratio` 원장에는 이미 `0.49999869`(소수 8자리)가 있고, 보통주 쪽 `nstk_ascnt_ps_ostk` 는 `0.0185559`·`5.4970488`·`5.5862507` 처럼 소수 7자리가 흔하다(자기주식 제외 배정).
- 인풋: 보통주·우선주 동시 무상증자를 비정수 배정비율(예 0.0362318841)로 공시한 주요사항보고서가 dart_fric_decsn 에 들어온 뒤의 stage 빌드(`stg_event_fric`).
- 에러 위치: `src/stage/rules_dart_events.py:200`(`_num("nstk_ascnt_ps_estk", 3, 2, "_ratio")` → DECIMAL(5,2)) · `src/stage/build.py:110-127`(`_cast_expr` — DECIMAL TRY_CAST 는 :123, 반올림을 실패로 보지 않음) · `build.py:130-137`(`_miss_kind_expr` 은 결과가 NULL 일 때만 cast_failed) → 소비 `src/equity/sql/corp_event.sql:77-81`(우선주 bonus ratio = 1 + nstk_ascnt_ps_estk_ratio).
- 위험성: silent corrupt(잠복). 0.0362318841 → 0.04 로 실려 우선주 무상증자 계수가 1.0362 대신 1.04(+0.37%)가 되고, 어떤 게이트에도 흔적이 없다(stage G2·G3 통과, 원장 원문과 다른 값). 같은 메커니즘은 scale 이 실측 최대치에 딱 맞춰진 모든 숫자 칸(DS005 의 `*_estk_rt`·`cr_rt_estk` 등, 그리고 다른 원천 표)에 적용된다 — 정수부 초과는 표 폐기(B-09), 소수부 초과는 무음 반올림으로 처리가 비대칭이다. 현재 실해는 0.
- 근거: 서버 duckdb 실측 `SELECT TRY_CAST('0.0362318841' AS DECIMAL(5,2))` → `0.04`(NULL 아님), `TRY_CAST('1234567890123' AS DECIMAL(10,0))` → NULL. 원장 대조(현판 m_20261003T0016…, 15표 전 행 · 스냅샷 컷 2026-10-02T23:24:52 이하): 숫자 칸 원문(쉼표 제거) = stage 값 불일치 0, 소수부가 scale 을 넘는 원문 0 — 지금은 반올림된 행이 없다. `dart_pifric_decsn.fric_nstk_ascnt_ps_estk` 값 분포에 `0.49999869` 1행, `dart_fric_decsn.nstk_ascnt_ps_estk` 비결측 70행은 전부 소수 2자리 이하.
- 기존 여부: 새 발견. B-09 는 정수부 초과(표 폐기) 쪽만 다룸.

### I-22 정정신고 첫 장의 항목표 해석이 중첩 표·rowspan 에 깨져 n_items·items·(2025 서식) reason_raw 가 오염된다 — 정정사유 없는 원문은 사유가 본문 전체를 삼킨다 [하]
- 상황: `stg_doc_correction` 은 정정신고 첫 장의 '정정사항' 표를 항목별 dict 로 펴서 `items`·`n_items` 를 만들고, 2025 서식(`3. 정정사유` 앵커가 사라짐)에서는 그 표의 `정정사유` 열 값을 모아 `reason_raw` 를 만든다(p1.5). 2025 서식 정정의 정정전/정정후 칸에는 재무표가 **중첩 표**로 들어가고, 정정사유·정정요구 칸은 여러 행을 `ROWSPAN` 으로 묶는 경우가 많다.
- 인풋: ① 20260319001313(n_items 1,085) — 머리 `항목|정정요구ㆍ명령관련여부|정정사유|정정전|정정후`, 정정전 칸 안의 '금융상품의 공정가치' 표 행(셀 1~4개)이 항목으로 집힘. ② 20250311000450 — 1행의 정정요구·정정사유 칸이 `ROWSPAN=13`, 다음 행부터 셀이 3개뿐이라 정정전 본문이 `정정요구ㆍ명령관련여부` 열에, 정정후가 `정정사유` 열에 들어감. ③ 20170915000145 — 원문이 `3. 정정사유 … 3. 정정사항`(4. 가 아니라 3.)으로 번호를 매김.
- 에러 위치: `src/stage/parsers_doc.py:486-495` — `tbl.iter("TR")` 가 중첩 표의 TR 까지 내려가고, 셀을 머리와 **위치로만** 짝짓는다(rowspan·colspan 무시) · `:497-500` 이 그렇게 어긋난 `정정사유` 값을 사유로 잇는다 · `:389` `_REASON_RE`(re.S, 종료 = `4. 정정사항` 또는 문자열 끝) — 다음 앵커가 없으면 첫 장 끝까지 삼킨다.
- 위험성: silent corrupt(기록 열). ① n_items 가 실제 항목 수의 수십 배(1,085), items 의 `항목`·`정정사유` 키에 표 조각·금액이 들어간다. ② 2025~ 서식은 사유가 이 경로로만 만들어지므로 `reason_raw` 가 "Xbrl 기재 정정 | … | 공시금액 | 28,874,335,505 | 104,542,682,615 …" 처럼 금액·표 머리로 오염된다(2025년 76/1,289행 · 2026년 63/1,107행이 3자리 쉼표 금액 2개 이상을 포함). ③ 앵커 변형 원문은 사유가 13,940자(첫 장 21,848자)로 정정 본문 전체가 된다(사유 1,000자 초과 305행, 5,000자 초과 70행). 소비: equity `disclosure_version.reason_raw`(소비자 노출 열)로 그대로 나가고, C-11 보강의 '사유 키워드로 확실한 look-ahead 21행' 추정도 이 열을 읽는다 — 정정전/정정후 본문의 '재무제표 수정' 같은 문구가 사유로 잡혀 추정을 부풀릴 수 있다. `corr_has_fin_item` 은 items 문자열 전체 LIKE 라 어긋남·중첩으로 키워드가 사라지지는 않는다(판정 자체는 영향 없음).
- 근거: 서버 문서 캐시 ZIP 을 저장소 파서(b220060a, `python -B`)로 다시 풀어 표 구조 대조(위 ①②③, 셀 수·ROWSPAN 출력) · stage `stg_doc_correction`(m_20261003T003753) 조회: items 원소 중 키가 3개 미만인 '짧은 행'을 가진 정정 3,137/17,476행(n_items>0 인 정정 기준, 연 10~25%), `regexp_matches(reason_raw, '\d{1,3}(,\d{3}){2,}')` 연도별 계수, `length(reason_raw)` 분포(중앙 18자, 최대 74,873자).
- 기존 여부: 새 발견. C-11 은 items 가 빈 목록('[]')인 경우만 다룸(빈 목록 자체는 원문이 자유 서식 — 표본 40건 중 표 없는 서술형 14·내용 표만 있는 22, 파서 누락 아님).

### I-41 KIS 신용 일일 수집이 유닛 로그(kis_ingest_log·kis_call_log)를 안 써서 08-21 부터 equity credit_daily 의 결측 사유가 전부 'not_collected' 로 바뀐다 [하]
- 상황: 신용잔고는 08-26~27 백필(`backfill_kis.py`)이 `kis_call_log`·`kis_ingest_log` 에 유닛(종목·조회창·ok/empty)을 남겼고, 09-09 부터는 일일 러너 `daily/kis_daily.py` 가 같은 표 `kis_credit_balance` 를 매일 채운다. equity `credit_daily` 는 행이 없는 격자 셀의 이유를 `stg_units_kis`(= kis_ingest_log) 창으로 판정한다(ok 창이 덮음 → `src_omitted`, empty 창 → `empty_response`, 창 없음 → `not_collected`).
- 인풋: 매일 06:00 `python -m daily.kis_daily` — 요청 유니버스 전 종목에 1콜씩(d2=T, asof 30행). 응답이 비거나 그날 행이 없는 종목(신용 비대상·신규 상장·KIS 생략분)이 매일 약 50~60종목.
- 에러 위치: `src/daily/kis_daily.py:406-490` `run()` — `fetch_credit`·`store_new_facts`·`runlog`(daily_run.db)만 부르고 `kis_call_log`·`kis_ingest_log` 에는 한 줄도 쓰지 않는다(백필 `backfill_kis.py:307-327` 과 다름). 판정 쪽은 `src/equity/sql/credit_daily.sql:209-216`(kinded CASE: unit_ok → src_omitted, unit_empty → empty_response, ELSE not_collected).
- 위험성: silent(지식 축 라벨 변질)·문서 불일치. 같은 구조의 빈칸이 백필 구간에서는 `src_omitted`(엔진 `SOURCE_OMITTED_ZERO` → 0 으로 읽음, `rules_s10.py:28-36`)·`empty_response` 였다가 08-21 부터는 `not_collected`(= "묻지 않았다") 로 찍힌다. 실제로는 매일 물었으므로 라벨이 거짓이고, 워크벤치 엔진·커버리지 진단이 이 경계(08-20/08-21)에서 신용 축의 0/결측 해석을 조용히 바꾼다. 모델 fi(`factor_inputs/queries.py:361-377` fi_credit)는 fill_kind 를 안 보고 NULL 행만 버리므로 모델 값 영향은 없다. 키움 샤드 로그(`stg_shards_kiwoom`)는 "08-23~24 2일뿐" 한계가 `rules_kiwoom.py:258` 에 적혀 있으나 KIS 신용의 같은 한계는 어디에도 적혀 있지 않다.
- 근거: 서버 `sqlite3 -readonly kis.db "SELECT name,max(ts),count(*) FROM kis_ingest_log GROUP BY name"` → credit 2026-08-27T20:55:31 (kis_call_log 도 같음) — 09-09 뒤 일일분 0행. `kis_credit_balance` deal_date 08-19~09-30 은 min(collected_at) 09-09T07:10~10-02T21:13(일일 러너가 적재). equity `credit_daily`(m_20261003T004323) fill_kind 날짜별: 08-10~08-20 = measured ≈2,530 · not_collected 178 · src_omitted 50~54 · empty_response 1~4 → 08-21~09-30 = measured ≈2,525 · not_collected 233~243 · src_omitted 0 · empty_response 0.
- 기존 여부: 새 발견(A-11 은 같은 러너의 COUNT 성능, 무관).

### I-42 KIS 공매도·수급 표는 한 행 안에서 가격·거래량은 수정(조회 시점) 기준, 공매도·투자자 수량은 원주 기준이다 — `ssts_vol_rlim_pct` 가 섞인 기준의 비율이라 100% 초과 3,688행(최대 2,961%) [하]
- 상황: `kis_short_sale`·`kis_investor_flow` 는 08-26 백필(폐지 652종목, 2009~2026-08-14)로만 채워진 동결 원장이다. KIS 는 이 두 TR 에서 `stck_clpr/oprc/hgpr/lwpr`·`acml_vol` 을 조회 시점 수정주가·수정거래량으로 소급 환산해 주지만, 공매도 수량(`ssts_cntg_qty`)·평균가(`avrg_prc`)·투자자별 `*_vol`/`*_qty` 와 금액 칸은 원주 기준 그대로 준다. KIS 자체 계산 비율 `ssts_vol_rlim`(= 공매도수량 ÷ 거래량)은 원주 분자 ÷ 수정 분모다.
- 인풋: 백필 뒤 기업행위(액면병합·감자·무상증자 등)가 있었던 종목의 과거 행 — 예 024660 2010-01-05(KRX 종가 3,110·거래량 1,505,549 vs KIS close_krw 2,797·acml_vol 1,673,475, 공매도 5,720주 × 평균가 3,154 ≈ 금액 18,041,705 → 원주), 214310 2017-03-13(KRX 거래량 1,186,061 vs KIS 3,972, 공매도 9,572주 → KIS 비율 240.99% · 원주 기준 0.81%).
- 에러 위치: `src/stage/rules_kis.py:213-265`(STG_SHORT_DAILY_KIS — `close_krw`·`acml_vol_shr` 를 원주 이름·접미사로 싣고 가격 기준 라벨이 없다. 같은 파일 stg_credit_daily 는 `:351-355` 에 `price_basis_close='adjusted_asof_collect'` 를 단다)·`:232-233`(`ssts_vol_rlim_pct` 원값 적재) → `src/equity/sql/short_daily.sql:57-58·159`(`short_volume_ratio_kis_pct` 로 그대로 나름). 같은 섞임이 `rules_kis.py:85-192` stg_flow_split_daily(`close_krw`·`acml_vol_shr` 수정 vs `*_ntby_qty_shr`·`*_seln_vol_shr` 원주)에도 있다.
- 위험성: silent corrupt. 공매도 비율이 같은 종목 안에서도 기업행위 전후로 수십~수백 배 틀어진다(그 종목들은 전부 폐지 종목이라 생존편향 축이다). equity `short_daily.short_volume_ratio_kis_pct` 에 100% 초과 3,688행·50% 초과 8,530행이 그대로 실려 있고 게이트가 없다. stage 이름(`close_krw`·`acml_vol_shr`)만 보면 원주 값으로 읽혀 KRX 가격·거래량과 섞는 소비자는 같은 식으로 틀린다. 현재 등록 소비자: FIELD_MAP §2 의 `short.short_balance_ratio` 는 `short_volume_kis_shr`(원주 수량 — 정상)만 쓰고 이 비율 열은 어떤 field_id 에도 안 걸려 있으며, equity flow_daily 는 KIS 수급에서 `*_ntby_tr_pbmn_krw`(원주 금액 — 정상)만 쓴다. 그래서 모델·현 필드 값 영향은 없고 equity 열과 stage 이름이 틀린 상태다.
- 근거: 서버 duckdb(read_parquet) — stg_short_daily_kis ⋈ stg_price_daily(공매도>0 307,230행): KIS 거래량/KRX 거래량 배율 1% 넘게 어긋난 행 140,443(45.7%)·2배 넘게 95,009(30.9%)·종목 244/443, 배율 분위 p1 0.02 · p50 1.0 · p99 5.0 · 최대 537. 2배 넘게 어긋난 95,009행 중 `ssts_cntg_qty × KRX 종가 ≈ ssts_tr_pbmn`(원주형) 94,997행 · 수정형 0행, `avrg_prc ≈ KRX 종가` 94,955행. `ssts_vol_rlim` 은 307,230행 전부 `q ÷ KIS 거래량` 과 일치(KRX 거래량 기준 일치는 179,253행). 수급: 033180 2010-05-11 KRX 종가 1,555·거래량 2,266,883 vs stg_flow_split_daily close_krw 20,916·acml_vol_shr 168,523, `acml_tr_pbmn_krw` 3,815,242,615 = KRX 거래대금(원주), `prsn_shnu_vol_shr` 2,263,983(원주). equity short_daily(m_20261003T004228) `short_volume_ratio_kis_pct > 100` 3,688행, max 2,961.57.
- 기존 여부: 새 발견. (KIS 신용의 수정종가는 `price_basis_close` 로 기존에 표시돼 있고, KIS 축 정지(08-14)는 EQUITY_FIELD_MAP:81 기존 — 둘 다 이 결함과 다름.)

### I-43 공시 원문 수집이 DART 014(문서 없음)를 '영구 실패'로 굳혀 다시 안 받는다 — 접수 당일 받은 [기재정정] 문서는 014 비율이 70배라 일시 미생성일 가능성이 높고, 일일 게이트는 014 를 허용해 조용히 빠진다 [중]
- 상황: 일일 DART 단계(`dart_daily.run`)가 접수 당일 저녁(18:05 체인)·다음 날 06:00 에 `backfill_docs.py --max-calls 3000` 을 부른다. `build_plan` 은 `zip_ok=1` 과 함께 `http_status='014'` 인 접수번호를 계획에서 영구히 뺀다(09-09 수정 — 근거는 "014 3,130건 전부 정정공시, 재시도해도 같은 답"). 문서 게이트 `_gate_docs` 는 014 를 '허용 실패'로 센다.
- 인풋: 접수 당일 수집되는 사업·반기·분기보고서·주식분할/병합결정의 [기재정정] 공시 — 실례(09-16~09-30, 접수 당일 수집 7·익일 1 — 20260915000292 만 익일 09-16 18:45 KST, 나머지 당일 19:02~19:18 KST): 20260915000292 지란지교시큐리티 [기재정정]반기보고서 · 20260929000169 [첨부정정]반기보고서 · 20260930000788·792·794·802 [기재정정]반기보고서 4건 · 20260930000867 제이알글로벌리츠 [기재정정]사업보고서 · 20260930901120 E8(418620) **[기재정정]주식병합결정**(우선순위 ① "조정계수의 정답지").
- 에러 위치: `src/backfill_docs.py:135-138`(`build_plan` — 014 를 done 집합에 넣어 재시도 안 함, `--retry-014` 는 어떤 크론·체인도 안 씀) · `:217-221`(014 를 `zip_ok=0, http_status='014'` 로 확정 기록) · `src/daily/dart_daily.py:549-557`(`_gate_docs` — `n_failed_other` 에서 014 제외 → 게이트 통과).
- 위험성: 데이터 손실(silent, 영구). 2025~26 접수 [기재정정] 문서를 받은 시점별로 나누면 — 접수 뒤 하루 넘어 받은 것(백필) 2,421 성공 · 014 2건(0.08%), 접수 당일~익일 받은 것(일일) 153 성공 · 014 9건(5.6%). 같은 종류의 문서가 늦게 받으면 거의 다 있으므로 당일 014 는 DART 쪽 문서 생성 전 응답일 가능성이 높다(직접 재호출 검증은 외부 API 금지라 못 함 — 비율 근거). 한 번 014 를 받으면 계획에서 영구 제외되고 게이트도 통과하므로, 정정된 사업·반기보고서와 정정된 주식병합결정 원문이 문서층(L1)에 영영 안 들어오고 소비층은 정정 전 원문만 본다. 지금 속도로 한 달 약 8~9건, 정기보고서 정정이 몰리는 마감 직후(3·5·8·11월)에 더 늘어난다(추정).
- 근거: 서버 `doc_store`(http_status 분포 000 171,376 · 014 3,140) ⋈ stage `stg_disclosure`(is_correction·report_nm) — 연도·정정 여부별 014: 2015~2019 정정 014 비율 39~49%(구 문서 영구 부재가 주류), 2025~26 [첨부정정]은 늦게 받아도 136/173 이 014(구조적). `dart_call_log`(key_id·ts 인덱스, 09-10 이후 document.xml 비000) = 위 8건 전부 k3·014·접수 당일 09:45~10:18 UTC 호출. `doc_store` fetched_at ≥ 09-10: 000 179 · 014 8.
- 기존 여부: 014 제외 자체는 09-09 결정(`docs/plans/2026-09-09-daily-incremental.md:334`, findings-B :115 "전부 정정공시")이고 그때 근거는 2015~2019 구 문서였다. 접수 당일 014 의 일시성·영구 유실은 새 발견. A-05(게이트가 '콜했다'만 봄)와는 다른 결함.

## 문제없음 확인

### 갈래 1 — WISE 블롭 파서(원문을 독립 디코드·파싱해 stage 와 대조, 스크립트 `scratchpad/auditI/w5~w9.py`)
- T2Y·T2Q·T4: 10-02 60종목 · 09-29 70종목 · 특수 15종목(금융 105560·032830, 지주 003550, 결산 변경·비12월 024850·042520·088260, 리츠 357120·448730, 적자 0007C0·000500, 신규 0126Z0, 대형 005930·000660·035720·072020) — T2Y 1,013행 · T2Q 1,015행 · T4 19,035셀 불일치 0(값·MAIN·base_date).
- fin_wise(cF3002·cF4002 'Y'): 145종목 39,632행 불일치 0(DATA1~6·DATAQ·YoY/QoQ 13칸·accode). 첫 대조의 1e-6 차 396건은 내 검산을 반올림 half-even 으로 한 탓 — duckdb DECIMAL 캐스트는 half-up, 결함 아님.
- fin_wise_q(Q:IS·Y:BS·Y:CF): 특수 15종목 11,557행 — 값과 파생 열 period_i·is_est_i·basis_i 불일치 0.
- consensus monthly(cF5001·5002): 50종목 4,739셀 불일치 0(consensus·min·max·종가·목표가·in_5001/5002). 5002 만 있는 행(10-02 3,114행)은 5002 이력이 5001(13점)보다 길어서다.
- analyst summary·broker: 120종목 html.parser 독립 파싱 — 요약 5칸(추정기관수 0 규칙 포함)·제공처 행 수·(제공처, 최종일자) 쌍 불일치 0. 변동률 부호: 10-02 3,381행 중 목표가 하향 930행에 양수 0, 계산식(목표가/직전 − 1) 대비 불일치 0. 의견 어휘 09-01~ 전부 buy/hold/sell 로 분류(other 0).
- 캐스팅: WISE 6표 `_cast_fail_cols` 0. 기간 라벨 모양은 '9999.99(A|E)' 뿐(n_label_unparsed 0).
- 단위: T2Y·T4·cF5001 매출·이익 억원, EPS·BPS 원(005930 2026.12(E) 매출 7,313,926.6 = T4 VAL1 7,313,927 = cF5001 7,313,926.65). fin_wise UNT_TYP 은 계정별로 일관(2=억원·1=원·8=배·6=%·11=주, 10-02 808종목). WICS MKT_VAL ×1e6(005930 유동주식 4,384,708,956 × 종가 ≈ 1,210조 원 = float_mktcap_krw).
- T2Y(E) ↔ T4 current: 10-02 2,391쌍 중 영업이익·순이익 차 > 1억원 0.
- DATAQ 칸 의미 확인(005930 10-02, YOY·QOQ·YOY_E·QOQ_E 산식 역산): Q1 = 최근 실적 분기의 전년 동기, Q2 = 추정 분기의 전년 동기, Q4 = 직전 분기, Q5 = 최근 실적 분기, Q6 = 추정 분기. fi 는 이 칸을 쓰지 않는다.
- PIT: WISE 5표 전 행 observed_date = available_date = fetched_date. 09-01~10-02 모든 수집일에서 내용 기준일(base_date) = 직전 영업일(같은 날 내용 0). 브로커 최종일자 > 수집일 0행, = 수집일 0행.
- v3 미러 이음매 단위: stg_v3_revision_daily 08-31 ↔ WISE 09-01 수집 08-31 점(005930·000660·035720 매출·EPS) 같은 단위·값(반올림 차만).
- 월말 관측점 사후 재계산 실측: '2026/08/31' 점을 09-01 최초 관측과 10-02 판이 다르게 준다(EPS 1,640쌍 중 0.1% 넘게 다름 140·1% 넘게 81, 매출 71·33) — equity consensus_daily 가 min(fetched_date) 판본을 고르는 근거를 실측으로 확인. fi_consensus 의 1w·1m·3m 은 WISE 가 그 수집일에 재계산한 값이라 그날 가용(PIT 위반 아님).
- monthly G8 n_metric_unknown 21,357 은 categories 가 빈 cF5002 빈 차트뿐(행 0).
- 참고: 리츠(6개월 결산, 예 088260)의 stg_fin_wise 'Y' 칸은 6개월 기간인데 freq 가 '연간'이다 — fi_universe 가 sec_type 으로 리츠를 빼서 모델 영향 없음. 비12월 결산 종목이 fy=YYYY12 신선도 규칙으로 유니버스에서 빠지는 것은 기존 U22.
- 키움 일일(kw_daily) 훑음: rc 0 + 빈 목록은 OK 로 세지만 저녁 직행 커버리지 게이트(≥0.98)와 다음 날 이력 응답의 신규 (ticker, dt) 삽입(`merge_tr`)으로 영구 결손이 되지 않는다.

### 갈래 2 — 문서층·DS005 15표(포크 I-2 보고)
- DS005 15표: 스냅샷 컷 이하 전 행에서 숫자 칸(쉼표 제거)·날짜 칸 원문 = stage 불일치 0. 예외 1건(tsstk_dp 20160108000502 '2106년')은 설계대로 out_of_range NULL. stage NULL 수 = 원장 결측('-'·''·NULL) 수, 원장 행 수 = stage 행 수(15표), 숫자 칸 비숫자 토큰 0.
- 단위·비율·부호: 금액 원(루멘스 시설+운영 = 3,571,428주 × 700원), `_pct` 는 백분율(동성제약 0.26 = 250,000/96,619,507), 무상 배정비율 = 주당 주식수(미원상사 5.4970488 = 4,259,883/774,940), 음수 보존·'0' 은 0, 감자 후 > 감자 전 위반 0, 삼성전자 자사주 취득 2건 원문 일치.
- available_date: 15표 전부 derived, 룩업 미스 0(available > observed 3행은 B-05 와 같은 원인 — 기존). 같은 rcept_no 2판본 6건은 회사명 변경뿐(corp_event 가 접어 영향 없음).
- 문서층: main 16.9만 건 period_to 연·월 ↔ report_nm 라벨 불일치 0, doc_acode ↔ 보고서 종류 불일치 0, 정기보고서 기간 NULL 0(lenient 65건 포함). filed_date ↔ 실제 접수일 일치 연도별 97~99%(불일치 표본은 공시인 오기). 재파싱 대조(사업 1·분기 1·정정 2) meta·correction·section 열 단위 diff 0. 프리패스 캐시 문서 수 171,376 = doc_store zip_ok=1.
- 서버 쓰기 0(python `-B`, pyc 시각 불변).

### 갈래 3·4 — 키움·KIS 규칙, 일일 수집기(포크 I-3·4 보고)
- 키움 ka10060(10-02 5종목) 원문 = stage, 백만원 ×1e6, 투자자 합 0·기관계 = 7세부 합, 부호('-14860' → 14,860·방향 −1). ka10014 천원 ×1e3(1천만원 넘는 2,031,930행 전부 수량 × 평균가 ≈ 금액 2% 안). ka20068 잔고 × 종가 = 잔고금액 × 1e6(백만원). ka10008 보유비중·소진가능 산식·'+' 제거 정상. ka10099 상장주식수·현재가·상장일 정상(스냅샷 당일 세션 KRX 와 88,300/88,428 일치). 거래정지 3종목 정합. 키움 3표 거래량 대 KRX 09-01~10-02 100% 일치(종가 차는 애프터마켓 — B-08 1번·결정 11 기존).
- KIS 신용 4표본 원문 = stage 전 열, 결제일 T+2·잔고율 %·공여율 정상, 9월 종가 50,430/50,533·거래량 100% KRX 와 같은 날짜로 일치, 08~09월 다판본 1,233키는 가격만 달라 잔고 변경 0(equity 최초판 선택 안전), fi 3세션 지연 기준 look-ahead 없음. KIS 대차 가격·거래량 415,463행 KRX 와 100% 일치(원주), 잔고금액 백만원. G2 캐스팅 실패 키움 5표·KIS 6표 0(신용 437행은 기존 등락률 문자열).
- backfill_docs: 정상 수신 171,376건 파일 수 1~3, 빈 ZIP 0, 014 외 남은 실패 0, 오류 응답 status 정규식이 XML·JSON 둘 다 잡음, 예외·HTTP 오류는 실패로 기록되어 게이트에서 걸림.
- backfill_kis classify·normalize(kis_daily 경로): 출력 키 누락은 'empty' 로 가지만 kis_daily 게이트(기대 종목 대비 0.98)가 막음, 오류·재시도 소진은 실패 비율 2% 초과 시 게이트 실패, 백필 대차 빈 응답 634유닛 중 잔고 구간 사이에 낀 것 0, 백필 신용 콜 실패 판정 0.

## 가설(4요소 미충족 — 결함으로 세지 않음)
- (갈래 1) WISE 런이 KST 자정을 넘기면 `fetched_date` 는 런 시작 날짜(`backfill_wise.py:287` `today`)인데 행별 `fetched_at`(observed_date 원천)은 다음 날이라 그 행들은 available_date < observed_date(하루 이른 가용)가 된다. 정상 18:05 런은 약 1시간이라 발생 0.
- (갈래 1) analyst summary 의 base_date 는 페이지 첫 '[기준:…]'(시세 절)을 잡는다(`parsers.py:484`) — 투자의견 컨센서스 절의 기준일이 아니다. 지금은 808종목 전부 두 날짜가 같아(수집일마다 단일값) 실해 0.
- (갈래 1) WICS 는 available_date = dt(basis default)인데 수집은 토요일(observed = dt + 1)이고, 같은 (dt, sec_cd) 재수집은 최신 판본이 available = dt 로 소급 대체된다(`rules_wics.py:49-61`). 설계상 기본값(lag_known False), 재수집 사례 미확인.
- (갈래 2) I-43 보강: doc_store zip_ok=0 3,140행은 전부 014·전부 [기재정정]/[첨부정정], 2026년 102건 중 7건이 접수 당일 수신이며 뒤 정정으로 대체된 문서도 아니다(일시성은 외부 호출 없이 확인 불가).
- (갈래 2) DS005 정정 처리가 백필 구간(API 가 최종 정정본만 줘 원본 rcept_no 없음)과 일일 구간(원본·정정본 둘 다)에서 달라 같은 사건의 announce_date 가 이력·실시간에서 다를 수 있다(C-07 과 같은 원인의 보강, 120일 창 휴리스틱이라 수치 미확정).
- (갈래 2) [첨부정정] 1,970행의 filed_date 는 감사보고서 멤버 첫 장이라 감사보고서 최초제출일이다(접수일 일치 86%, main 97%). disclosure_version 은 member_name 을 보지 않음 — 표본 3건은 같은 날이라 무해.
- (갈래 2) '?' 로 깨진 원문 ZIP(예 20220802000208)은 n_repl 0 이라 어떤 계수에도 안 잡힌다(규모 미측정). 범위 안 원문 날짜 오기는 그대로 실린다(예 cvbd_is 납입일 2053-02-28, 20230425000722).
- (갈래 3·4) KIS 신용 금액 6열은 천원으로 추정되나 시가와 비례하지 않는다(담보비율 8~14% 꼴) — 현 '단위 미상' 라벨이 맞다.
- (갈래 3·4) kis_daily 는 `api.kis()` 가 HTTP 상태를 버려 늘 200 으로 판정 — 오류 코드 없는 JSON 5xx 는 재시도 없이 실패(실패로는 남아 조용하지 않음).
- (갈래 3·4) KIS 대차 잔고 > KRX 상장주식수 5행 — equity 게이트 여부 미확인. KIS 폐지 마스터 상장자본금(652행 중 638행 0)·발행가 '0' 은 결측 표기인데 0 으로 실림(소비자 없음 추정). backfill_docs 는 ZIP 목록만 열고 CRC 검사 안 함(손상은 프리패스 D0 에서야 걸림, 실례 없음). 키움 `close_krw_dir` 보합 = 1(쓰는 곳 없음).

## 미조사
- (갈래 1) `rules_dart.py` 의 DS005 밖 표(stg_fin·dividend·shares·audit·capital·holder 등)와 `rules_krx.py` 의 행 단위 의미, v3 미러 4표의 행 단위 대조(이음매 3종목 표본만), WICS 원문 대조(1종목 표본만), WISE 수집 유니버스(A-07 기존).
- (갈래 2) stg_doc_section 의 section_kind·level 의미(소비처 없음), I-22 의 정확한 규모(rowspan 반영 실제 항목 수 전수 미계수 — 지금 수치는 짧은 행 기준 근사), DS005 텍스트 칸 사용처·cmp_dvmg·stk_extr 세부, 프리패스 증분이 같은 rcept_no ZIP 교체(sha256 변경)를 따라가는지(코드상 rcept_no 집합만 비교), fin_std 1Q/3Q 판정 전수(영역 C).
- (갈래 3·4) kw_daily fetch·게이트 행 단위 정합과 공매도 누적 수량 리셋 판정, KIS 신용 금액 열 단위 확정, KIS 수급 단위 미상 열(매도·매수 대금, 외국인 순매수 대금), equity flow_daily 의 KIS→키움 투자자 항목 대응(영역 C), KIS 백필 경로 실행 이력 전수.

- 종료: 2026-10-06 04:16 KST · 결함 9(상 0 · 중 1 · 하 8) — 갈래 1: I-01~I-04(하 4) · 갈래 2: I-21·I-22(하 2) · 갈래 3·4: I-41·I-42(하 2)·I-43(중 1). 서버 쓰기 0(본인·두 포크), 외부 API 0. I-41 은 종합 때 equity credit_daily(m_20261003T004323) fill_kind 를 다시 조회해 08-20→08-21 경계(src_omitted 50·empty_response 4 → 0, not_collected 178 → 234)를 재확인했다.

## 끝
