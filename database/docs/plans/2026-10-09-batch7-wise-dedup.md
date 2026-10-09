# 배포 묶음 7 — WISE 재무 연속 판 접기 + 아침 확정판 재사용 (stage 2.7.0 · fi1.4.0)

> 결정은 [`DECISIONS.md`](../DECISIONS.md) N-36 ①(10-09 사용자 '묶음 7 플랜(1+2)' · 'fstrim 나중에')에 있다. 이 문서는 작업과 진행만 적는다.
> 상태: **10-09 사용자 승인(N-37, D7-1~D7-11 권고안 그대로).** 구현 착수 10-09 오후.

## 요약
- **원인**: 원장 `ws_raw` 는 수집일마다 같은 원문을 새 판으로 저장한다(PK 에 fetched_date, `backfill_wise.py:70-79`·`:410-412`). stage 두 표는 자연키에 fetched_date 가 있어(`rules_wise.py:172`·`:198`) 같은 판 접기가 날짜를 넘지 못한다(`build.py:292-296`). 10-08 원장 기준 stg_fin_wise 6,245,678행·15.4분, stg_fin_wise_q 3,233,349행·7.6분, 두 표 모두 RSS 6.8GB(한도 6GB 초과 — spill).
- **(1) 접기**: 파싱 전 blob 단계에서, 같은 (종목, ep, pkey) 의 **바로 앞** 원장 blob 과 sha256 이 같으면 풀지 않는다. 첫 판과 A→B→A 로 되돌아온 판은 남긴다. 대상은 stg_fin_wise·stg_fin_wise_q 두 표뿐.
- **효과(10-09 서버 실측 — 10-08 원장에 새 규칙을 적용한 계산, 빌드 아님)**: 행 6,245,678 → 1,380,846(22.1%) · 3,233,349 → 932,706(28.8%), 하루 증가 22만 → 4~7만 · 65만 → 4~10만, 두 표 소요 23분 → 약 3~6분(추정, 7-5 실측).
- **뜻이 바뀌는 점**: 두 표의 fetched_date 가 '마지막 확인일' → '그 원문을 처음 본 날'(A→B→A 는 남김). 이 열을 최신성으로 읽는 소비자는 없다(신선도는 접지 않는 stg_consensus_annual 의 fi `_cov` 와 원장 건전성). **고칠 곳 두 곳** — 종목별 max(fetched_date) 한 날짜로 cF3002·cF4002 를 함께 고르는 쿼리(fi `queries.py:549-560` wsnap, compat `mappings.py:365-374`). 안 고치면 cF4002 만 새 판인 날 cF3002 손익 열(매출·영업이익·순이익)이 조용히 빈다(P1 결함).
- **(2) 아침 재사용**: 두 표는 원장 내용 지문(키·sha256·fetched_at)·규칙 판본·코드 rev 가 저녁 판과 같으면 다시 짓지 않고 저녁 판 파일을 하드링크한 새 `m_` 판으로 커밋 — C1 예외 규칙 불필요. 하나라도 다르거나 실패하면 일반 빌드(P1).
- **판본**: equity 는 두 표를 읽지 않는다(`src/equity` grep 0) → e1.26.0 그대로, main 병합 e1.27.0 그대로. stage 2.6.0 → 2.7.0, fi fi1.3.0(묶음 6) → fi1.4.0, 모델 mb1.4.0 그대로.
- **배포**: 10-13(화) 낮 창. 첫 접힌 판 10-13 21:20, 첫 재사용 10-14 08:10.

## 진행률

| ID | 무엇 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|---|
| 7-1 | stage 연속 판 접기(blob 단계, 두 표): 접기 함수 한 곳, G8 보존 등식, 남긴 blob sha 재검증, 기록형 지표 | 완료 | 완료(7a0629f9 — 서버 원장 읽기 전용 대조 행 1,380,846·932,706 플랜과 정확히 일치, sha 불일치 0) | 통과(상 0·중 1 반영 중) | 대기 | 대기 | 10-13 21:20 첫 2.7.0 저녁판 |
| 7-2 | 소비자 최신성 정합: fi wsnap·compat latest 를 (종목, ep) 단위로, FG1 손익 비율, available_date 뜻 문서(fi1.4.0) | 완료 | 완료(2eb09226 — G1 옛 NULL→새 값, 접지 않은 절단본 골든 동일) | 통과 | 대기 | 대기 | 10-14 08:10 fi·엑셀 |
| 7-3 | 아침 재사용(두 표): 지문·판본·rev 판정, 하드링크 재커밋, 실패 시 일반 빌드, 끄기 파일 | 완료 | 완료(7a0629f9, 거절 조건 14종 테스트) | 통과(하 반영 중) | 대기 | 대기 | 10-14 08:10 |
| 7-4 | 판본(stage 2.7.0·fi1.4.0), C3 1회 처리, 문서, TECH_DEBT | 완료 | 대기 | 대기 | — | — | — |
| 7-5 | 서버 재연(읽기 전용 + 임시 폴더): 소요·행 수, 각 날짜 최신 판 동일성, fi 동일성, 재사용 연습, sha 전수 대조 | 완료 | 대기 | — | — | — | 10-11 |
| 7-6 | 배포, 수동 b_ 패스, 첫 저녁·아침 관측 | 완료 | — | — | 대기 | 대기 | 10-13 낮 창 |
| 구조 검토 | 단계 끝 전체 검토 1회 | — | — | 대기 | — | — | — |

## 브랜치
- `wip/b7-stage`(7-1·7-3)·`wip/b7-fi`(7-2) 모두 feat/v3-merge 위 — 10-11 재연은 서버 equity e1.25.0 위에서 돌아야 하고, wip/b6-int 의 fi1.3.0 은 `_check_columns` 때문에 e1.26.0 판이 없으면 멈춘다. `wip/b7-int` 는 10-12 묶음 6 이 feat 에 들어간 뒤 다시 통합.

## 진행 기록(10-09 오후)
- 통합 `wip/b7-int` e8d08b26 = 7-1·7-3(7a0629f9) + 7-2(2eb09226), 전체 2,136 passed · 12 skipped. 브랜치는 둘 다 feat/v3-merge(faa41f7d) 위 — 묶음 6 이 feat 에 들어간 뒤(10-12) 재통합, fi 판본 줄에 1.3.0 이력 넣기.
- 묶음 전체 검토: 상 0 · 중 1(G8 원장 count 독립성을 지키는 부정 테스트 부재 — 추가 중) · 하 8(sha 재계산 압축 판정 규칙을 파서와 맞춤, 저녁 CLI 게이트 임계 override 기록·비교, 재사용 결과 객체를 커밋 앞에서, code_rev 를 코드 루트 DEPLOYED.json 에서(deliver E-08 과 같게), 'C4 독립 확인' 문구 정정, freshness 근거 정정, 지문 스트리밍 해시 — 반영 중). PIT·접기 정확성·재사용 안전성·소비자 전수(두 표를 읽는 곳은 fi·compat 뿐) 통과.
- **7-5 재연 때 챙길 것(검토)**: ④ fi 동일성 예외 목록을 '미수집 날 + 빈 응답(ok·빈 DATA) 날'로 정의해 건수 보고 — 빈 응답 날에도 직전 손익 값이 이어지는 동작(검토 하-2)은 D7-4 의 '결손'보다 넓다, 건수를 보고 사용자 판단으로 D7-4 보강 여부 결정. ⑤ 임시 루트 코드 사본에 DEPLOYED.json 을 둬야 재사용 연습이 거절되지 않는다.

## 사실 근거

### 코드(feat/v3-merge eca2fbb4 = 운영 d12085bd 코드)
- 원장: `backfill_wise.py:70-79` ws_raw PK (cmp_cd, ep, pkey, fetched_date), `sha256 TEXT NOT NULL`. `:405-412` ok 응답마다 `INSERT OR REPLACE`, sha256 = 압축 전 원문 해시, body = zlib 압축본. 같은 날 재실행(A-03, `:310-316`)은 같은 키를 덮어써 (키, 날짜)당 blob 1개. `:51-57` 재무 요청 cF3002 Y·Q:IS·Y:BS·Y:CF, cF4002 Y. `:168-175` cF5001·5002 요청에 `dt=오늘`.
- stage: `rules_wise.py:64-75` `_blob_table` append_only, available = fetched_date(measured). `:142-173` STG_FIN_WISE 키 (ticker, fetched_date, ep, seq), `:193-200` STG_FIN_WISE_Q 키 (ticker, fetched_date, pkey, seq). `build.py:292-296` 접기 창 (자연키, payload_hash). `:445` CHUNKED_PARSERS 두 파서. `:454-489` 한 번에 푸는 길, `:492-` `_write_jsonl_by_company`(회사 1곳씩, blob SELECT 에 ORDER BY 없음 `:528-531`, 파서가 출력만 정렬 `parsers.py:398-399`). `:612-619` baseline·G7 연도 범위가 빌드 연도에 묶임. `parsers.py:20-27` RawBlob, `:354-404` `_parse_fin`(pkey 밖 blob 은 n_skipped_pkey). `gates.py:106-112` G1, `:179-193` G5(감소해도 등식이면 통과), `:230-246` G8(방출 = n_src). `health.py:214-258` C3(append_only 전체 행 비감소, 판본 안 봄), `:261-302` C4(판본 바뀐 판 대조 제외), `:340-370` C6(max_available_date ≥ D − 허용지연, `freshness.py:63-64` 두 표 10일). `manifest.py:22-45` BuildRecord(equity 가 import), `:58-68` 모르는 키 버림, `:76-91` keep 3. `snapshot.py:92-104` 현재 판 snapshot GC 보호. `model.py:13` RULES_VERSION 2.6.0(전 표 공통).
- 소비자: fi `queries.py:549-560` wsnap(종목별 max(fetched_date) 하나로 두 ep — 컨트롤러 직접 확인), `:589` w_fetched, `:594-606` wqsnap(pkey='Q:IS' 고정), `:624` q_fetched, `:753`·`:798` available_date, `:119-153` `_cov`·D*(stg_consensus_annual), `:405-407` matrix 를 `last_fresh = fetched_date` 같은 날짜로 조인, `:449-460` consensus_annual 최신 ≤ D. fi 게이트 `gates.py:36-38` FIN_COVERAGE_MIN 은 per·eps(cF4002)만, `:39-42` COLLECTION_LAG_MAX 1거래일, `:142-143` available_after_d. compat `mappings.py:365-374` latest(fi 와 같은 모양). 계약 `model/contracts.py:184-187` available_date 는 엔진이 읽지 않음. 엑셀 `excel_daily.py:545-546`·`:569` '재무 접수일', `:1021-1028` 메타 '재무 최신 접수', `excel_weekly.py:267` 주간 '기준일'. equity 는 두 표를 읽지 않음(다른 WISE 표: `rules_s17.py:274-299` consensus_monthly min(fetched_date), `rules_s18.py:184-187` opinion_daily obs_date = fetched_date, `rules_s24.py:66-74`·`:103-104` coverage_daily 날짜 축 = DISTINCT fetched_date). 원장 건전성 `ledger_health.py:426-431`·`:470-471` 원장 직독. 체인 `stage/__main__.py:47-60` 출력 줄 → `run_stage_all.sh:56-66` 해석(끝이 `<초>s`).
- 결정 장부: §2 '증분 금지 — 10-14 까지 증분 빌드 금지(표 통째 재사용만 허용)', §3-C D-Q4(WISE 원문 매일 전체 저장) ↔ RM:74.

### 서버(읽기만, 10-09 14:1x KST, rev d12085bd)
- wisereport.db 1.24GB, journal_mode=delete, 수정 시각 10-08 18:12 KST.
- 현판(m_20261009, D=10-08, 2.6.0): stg_fin_wise 6,245,678행 · dedup 0 · 수집일 28일(09-01~10-08) · G8 blob cF3002 22,622 · cF4002 22,622 · skipped 12,171. stg_fin_wise_q 3,233,349행 · 수집일 5일(10-01~10-08) · cF3002 12,171. 하루 행 수 fin_wise cF3002 196,910 + cF4002 29,520, _q Q:IS 196,910 · Y:BS 203,671 · Y:CF 253,029.
- 소요(`logs/stage_all/summary.tsv`): fin_wise 624.5 → 951.1초, _q 140.8 → 480.1초. 10-09 아침 `/usr/bin/time`: fin_wise 15:22 · RSS 6.85GB · 쓰기 76,046,936 블록, _q 7:36 · 6.75GB · 33,697,392 블록.
- 체인 종료: 10-09 아침 stage 08:29 → 09:47(77.5분), 체인 09:58, 10:30 워치독까지 32분. 10-08 저녁 stage 88.9분.
- 바로 앞 blob 과 sha 같은 비율(10-01 / 10-02 / 10-06 / 10-07 / 10-08): cF3002:Q:IS — / 91.5 / 93.6 / 87.7 / 82.8%, cF3002:Y 88.4 / 90.5 / 91.6 / 86.2 / 81.1%, Y:BS — / 93.1 / 93.2 / 89.3 / 86.1%, Y:CF — / 90.9 / 92.8 / 87.3 / 83.2%, cF4002:Y 4.0 / 1.8 / 4.2 / 2.8 / 2.6%. A→B→A 는 cF4002 하루 1·19·11·5건, cF3002 0~1건. 같은 비율이 떨어지는 건 3분기 실적 시즌 접근 때문일 수 있다(추정).
- 접은 뒤 계산(10-08 원장 전체 이력, 시뮬레이션): 남김/전체 — cF3002:Y 2,829/22,622 · cF4002:Y 19,588/22,622 · Q:IS 1,188/4,057 · Y:BS 1,139/4,057 · Y:CF 1,199/4,057, 공백을 넘어 접힌 경우 1건(cF3002:Y). 표: stg_fin_wise 1,380,846행(날짜별 남는 행 09-28 0 · 09-29 41,786 · 10-01 50,066 · 10-06 44,133 · 10-07 57,274 · 10-08 65,787), stg_fin_wise_q 932,706행(10-01 636,663 첫 판 · 10-02 65,277 · 10-06 43,527 · 10-07 83,319 · 10-08 103,920).
- WISE 다른 표 현판: consensus_monthly 4,805,148행·108초, matrix 3,011,715·54초, annual 158,137, quarterly 158,333, analyst_summary 22,619, analyst_broker 92,650.

## 설계

### 7-1 연속 판 접기
- **어디서**: 파싱 전 blob 단계(`_write_jsonl_by_company`, 한 번에 푸는 길도 같은 함수). 비용 대부분이 파싱 뒤(1행 약 2.2KB JSONL → 6.25M행 약 13.7GB 쓰기·재읽기, 창 함수 spill, 추정)라 접힌 blob 은 압축 해제·JSON·JSONL·duckdb 를 모두 건너뛴다.
- 기각: 파싱 뒤 행 단위 접기(모든 blob 을 파싱해야 해 비용이 남고, 삭제 표식·소비자 쿼리 확대 필요, blob 의 한 시점 일관성 상실 — cF4002 만 이 방식은 TECH_DEBT) · 날짜를 넘는 SQL 접기(파싱 비용 그대로, 자연키에서 fetched_date 를 빼야 해 G6·키 유일성 선언까지 바뀜).
- **규칙**(`stage/fold.py` 한 곳 — 빌드 두 길과 7-3 재사용 판정이 같은 함수, P4):
  1. 단위 (cmp_cd, ep, pkey) — ep·pkey 마다 따로.
  2. 파이썬에서 (ep, pkey, fetched_date) 로 정렬한 뒤 판정(SQL 정렬에 기대지 않음).
  3. 남김: 단위의 첫 blob, sha256 이 **바로 앞** blob 과 다른 blob. '바로 앞' = 같은 단위에서 fetched_date 가 바로 앞인 원장 blob(수집 공백 무시, D7-1).
  4. A→B→A 보존 — 비교 상대는 바로 앞 판 하나(DART 선례 `backfill_dart.py:481-484` 는 두 번째 A 를 버리는데 그 부분은 따르지 않음 — cF4002 하루 최대 19건 되돌아옴).
  5. 같은 날 여러 번 수집 — 원장 PK 로 하루 1개, 덮어쓴 마지막 blob 만. 저녁 판 뒤 같은 날 재실행이 덮으면 아침 지문이 달라져 7-3 재사용이 빠지고 일반 빌드가 다시 접는다.
  6. 커버 끊김 — A(d1)·없음(d2)·A(d3) 이면 d1 만(10-08 원장 전체 1건). 끊긴 동안 소비자는 `_cov` 로 빼거나 유예. '마지막 blob 날짜 < 그 ep·pkey 의 최신 수집일' 키 수는 기록형.
  7. 첫 판·새 종목은 늘 남김.
  8. 결정성 — 원장만의 함수, 회사 단위라 묶음 크기 무관, 한 번에 푸는 길도 sha256 을 읽어 같은 함수.
  9. sha 신뢰 — 원장 sha256 열 사용, 남긴 blob 은 파싱 전 압축 해제해 sha256 재계산 대조(`n_sha_mismatch > 0` 이면 G8 FAIL — 열 뜻 오류를 첫 빌드에서 잡음), 압축 해제 실패는 지금처럼 `n_parse_failed`.
  10. 선언 `BlobSource.fold_consecutive: bool = False`, 두 표만 True.
- **지표·게이트**: 파서 지표 뒤에 `n_ledger_blobs`(sqlite 에서 따로 센 `ep IN eps` 행 수) · `n_folded{ep:pkey}` · `n_sha_mismatch` · `n_keys_stale{ep:pkey}`(기록형) · `input_fingerprint`. G8 폐기형 추가: `n_ledger_blobs = Σ n_blobs + n_skipped_pkey + Σ n_folded`, `n_sha_mismatch = 0`(원장 쪽은 접기 함수가 아닌 별도 count — 항진명제 회피). G1·G5 그대로, n_dedup 0 그대로, `n_blobs`·`n_skipped_pkey` 뜻이 '남긴 blob 중'으로 바뀜(문서).
- **대상 표 판단**: stg_fin_wise(fi wsnap·compat latest — 'D 기준 최신 값'은 같고 두 곳만 고침) · stg_fin_wise_q(wqsnap 이 이미 (종목, pkey) 단위) → 대상. stg_consensus_annual(fi `_cov` D* = 마지막 수집일, FG-fresh 수집 지연 — 접으면 신선도 판정이 틀어짐) · matrix(같은 날짜 조인 → 그날 행이 없으면 fi_consensus 가 조용히 빔) · monthly(리비전 PIT 최초 관측은 안 바뀌나 coverage_daily 가 접힌 날을 무커버로 봄, `dt=오늘` 이라 원문이 매일 다를 가능성·효과 작음, 추정) · analyst_summary(opinion_daily 일별 행이 빠짐) · broker·quarterly(작음) → 제외.
- **남는 증가**: 접은 뒤 stg_fin_wise 증가의 절반 이상이 cF4002(하루 96~98% 바뀜 — 가격 의존 비율, 추정). 두 표 합 하루 약 8.7~17만 행 → 지금 크기(948만 행)로 돌아오기까지 약 40~80거래일(추정, 실적 시즌엔 더 짧음). 12월 초 재측정(TECH_DEBT).

### 7-2 소비자 최신성 정합

| 소비자 | 지금 | 접은 뒤 | 할 일 |
|---|---|---|---|
| fi wsnap(`queries.py:549-560`) | 종목별 max(fetched_date) 하나로 두 ep | cF4002 만 새 판인 날 cF3002 행이 없어 revenue·op·ni·gross_profit·fs_basis 가 **조용히 NULL**(FG1 은 per·eps 만 봐서 못 잡음) | (종목, ep) 단위 최신 ≤ D. 한쪽 ep 가 그날 없으면 그 ep 의 직전 판(D7-4, 재연에서 건수 보고) |
| fi wqsnap(`:594-606`) | pkey='Q:IS' 고정 | 같은 값 | 없음 |
| fi w_fetched·q_fetched → available_date(`:589`·`:624`·`:753`·`:798`) | 마지막 확인일 | 처음 본 날(같거나 이름). 연간 행 = greatest(두 ep 판 날짜, DART) — cF4002 가 매일 바뀌어 대부분 그대로, 분기 행은 Q:IS 판 날짜로 이르게 | 엔진은 안 읽음(`contracts.py:184-187`), FG `available_after_d` 0 유지. 계약 설명·docstring 뜻 고침 |
| 엑셀 '재무 접수일'·메타·주간 기준일 | 연간 available_date | cF4002 가 안 바뀐 종목(하루 2~4%)만 날짜가 앞당겨짐 | 정의 '알게 된 날' 그대로 맞음 — 코드 변경 없음, 문서만 |
| fi `_cov`·fi_consensus·annual | 컨센서스 표 | 접지 않음 | 없음 |
| fi FG1(`gates.py:155-166`) | per·eps 비율 ≥ 0.9 | 이번 결함 부류(cF3002 열만 빔)를 못 봄 | `n_eligible_with_wise_is`(op 또는 ni 가 있는 연간 행) 비율 추가 — 폐기형 여부 D7-8 |
| compat financial_summary(`mappings.py:365-374`) | fi 와 같은 모양 | 같은 결함(체인 밖 — 다음 수동 export 에서 드러남) | (종목, ep) 단위로, fi 와 같은 G1 |
| stage C3(`health.py:214-258`) | 행 수 비감소 | 첫 2.7.0 판에서 22%·29% 로 줄어 FAIL → equity 미빌드·crit | 1회 처리(D7-5) |
| stage C4 | 소스 계수 동결 → 해시 동결 | 2.7.0 첫 판은 전 표 대조 제외, 그 뒤 그대로 맞음 | 없음 |
| stage C6(10일) | 최신 수집일 | 최근 원문이 바뀐 날(cF4002 가 매일 바뀌어 사실상 그날), 10일 동안 전 종목 불변일 때만 FAIL — 수집 정지와 같은 신호 | `freshness.py` 주석. 수집 정지는 컨센서스 6표 C6·원장 `wise.run`·fi FG-fresh 가 계속 봄 |
| 원장 건전성·equity·모델·워치독·daily_report | 두 표를 안 읽음 | 변화 없음 | 없음 |
| 외부 공유 소비자(stage 디렉터리 바인드) | 사용 방식 모름 | 날짜별 행 존재·fetched_date 뜻이 바뀜 | STAGE_HANDOFF 기록, 알림은 D7-9 |

### 7-3 아침 재사용
- **판정**(`stage/reuse.py`, 전부 참일 때만): ① `TableRule.morning_reuse=True`(두 표), basis morning, 끄기 파일 `data/stage/REUSE_OFF` 없음 ② MANIFEST 현재 판이 basis evening 이고 그 자신이 재사용 판이 아님 ③ 현재 판 rules_version = 코드 RULES_VERSION ④ 현재 판 code_rev = **코드 루트** `DEPLOYED.json` rev(deliver E-08 과 같은 곳 — 10-09 검토 하-5, 둘 중 하나라도 없으면 재사용 안 함) ⑤ 아침 스냅샷에서 다시 계산한 원장 지문 = 저녁 판 기록(지문 = sha256(머리줄 RULES_VERSION·파서·eps + (cmp_cd, ep, pkey, fetched_date, sha256, fetched_at) 정렬 전부), 본문 없이 sqlite 에서 — 10-09 실측 57,415 blob 7.4초) ⑥ baseline.json 그 표 항목 해시·골든 픽스처 해시·빌드 연도(UTC — G7 범위가 `datetime.now(UTC).year` 라 그와 맞춤, 10-09 구현)·CLI 게이트 임계 override(저녁·아침 같아야 함, 10-09 검토 하-3) 같음 ⑦ 하드링크한 parquet 의 content_hash 재계산 = 기록. src_bytes·src_mtime 은 기록만(D7-6).
- **재커밋**: 새 `m_` id, `_tmp/<id>/<표>` 에 저녁 판 parquet·`_reject` 하드링크 뒤 `v=<m_>` 로, `_meta.json` 은 새 파일(하드링크된 저녁 파일을 제자리에서 쓰면 저녁 판이 바뀜). 레코드 snapshot_id = 아침 스냅샷, gates 는 저녁 판에서 복사, 새 선택 필드 `reused_from`·`input_fingerprint`·`code_rev`(BuildRecord 기본 None, equity 쪽 통지). 출력 줄에 `reused_from=e_…`, 끝은 `<초>s` 유지.
- **건전성**: m_ 판이라 C1 그대로 통과(예외 규칙 없음), C4 는 계수·해시 동결로 통과(재사용 정합의 독립 확인), C3·C6 같은 값.
- **실패**: 판정 아님·예외면 사유 한 줄(`reuse_declined reason=…`), 반쯤 만든 판 지우고 일반 `build_table`(P1).
- **끄기**: `data/stage/REUSE_OFF` 를 만들면 다음 아침부터 일반 빌드(코드·크론 변경 없음).
- **결정 정합**: '10-14 까지 증분 금지, 표 통째 재사용만 허용' 안.
- **비용·절감**(추정): 지문 약 10초 + 하드링크·해시 수 초, 7-1 뒤 아침 절감 약 3~6분. 7-1+7-3 합하면 10-09 아침 기준 stage 77.5분 → 약 55분, 체인 종료 약 09:35.

### 7-4 판본·재빌드·되돌리기
- 판본: stage 2.6.0 → 2.7.0(전 표 공통 — 첫 판에서 전 표 C4 대조 제외), fi1.3.0 → fi1.4.0(묶음 6 위), equity e1.26.0·모델 mb1.4.0 그대로, main 병합 e1.27.0 그대로(main 은 merge-base 이후 `src/stage`·`src/factor_inputs`·`src/compat` 변경 0 → 충돌 없음, main 이 바꾸면 stage 2.8.0·fi1.5.0).
- C3 1회 처리(D7-5): 배포 때 두 표를 수동 b_ 로 한 번 빌드(코드 없음) → 저녁 C3 이 e_(2.7.0) 대 b_(2.7.0). 빠뜨리면 저녁 C3 FAIL(실패 쪽, P1).
- 되돌리기(원장 불변 — 데이터 손실 없음): 재사용만 문제면 끄기 파일. 접기가 문제면 ① 기록 rev(묶음 6 rev) 재배포 ② 다음 체인이 옛 규칙으로 전량 재빌드(C3 증가라 통과, C4 판본 변경으로 제외) ③ **재배포부터 다음 체인까지 fi·모델을 손으로 돌리지 않는다**(옛 fi 가 접힌 판을 읽으면 cF3002 열을 비움 — 꼭 돌려야 하면 두 표 먼저 수동 재빌드, 약 25분) ④ 이미 fi1.4.0 판으로 보낸 엑셀은 값 차이가 없으면 정정하지 않음(근거 7-5 ④·배포 ⑤).

## 작업 규칙

### 7-1 접기 (wip/b7-stage)
- Files: `src/stage/fold.py`(신설) · `model.py`(BlobSource.fold_consecutive, RULES_VERSION 2.7.0·이력) · `rules_wise.py`(두 표) · `parsers.py`(RawBlob `sha256: str = ""`) · `build.py`(두 길에서 sha256 SELECT·접기·지표, code_rev·지문 기록) · `gates.py`(G8 등식 2개) · `freshness.py`(주석) · `tests/test_stage_wise.py`(`_row` sha 를 실제 해시로, 같은 원문 두 날짜 픽스처 `:598-604` 기대값 수정) · `tests/test_stage_fold.py`(신설).
- G1(옛 코드 FAIL): 같은 원문 이틀 → 둘째 날 행 0·`n_folded=1`(옛 코드는 행 2배) · A(d1)·A(d2)·B(d3)·A(d4) → d1·d3·d4 세 판, 각 판 행 수 = 원문 DATA 수.
- 회귀 가드: 첫 판·중간 진입 종목 보존 · pkey·ep 독립 · 공백 A·없음·A → d1 · 입력 순서를 섞어도 같은 결과 · 묶음 1·3·전체와 한 번에 푸는 길의 JSONL 바이트·지표 동일(`test_fin_blob_source_split_by_company_equals_one_shot` 확장) · 두 번 빌드 content_hash 같음 · 접지 않는 WISE 6표는 같은 스냅샷에서 새 코드 전후 content_hash 같음 · G1·G5 등식, n_dedup 0.
- 부정 테스트(FAIL 해야): 비교 상대를 '처음 본 판'으로 바꾸면 A→B→A 셋째 판이 사라짐 · 단위에서 pkey 를 뺌 · `n_ledger_blobs` 1 감소 · sha256 열에 압축본 해시 → `n_sha_mismatch > 0` · 깨진 본문은 parse_failed 로 FAIL(sha 불일치로는 안 셈).

### 7-2 소비자 (wip/b7-fi — feat/v3-merge 위, 통합 때 wip/b6-int 와 판본 줄만 맞춤)
- Files: `src/factor_inputs/queries.py:549-560`(wsnap)·`:529-536`(docstring) · `gates.py`(FG1 손익 비율) · `build.py:45`(fi1.4.0) · `src/compat/mappings.py:365-374` · `src/model/contracts.py:184-187` · `tests/test_factor_inputs.py`·`test_factor_inputs_slice.py`·`test_compat_export.py`.
- G1: 합성 stage — 종목 X 의 cF3002 판은 d1 만, cF4002 판은 d1·d2 → D=d2 에서 fi_fin_summary 연간 revenue·op·ni 가 d1 값(옛 쿼리 NULL → FAIL). compat 도 같은 G1.
- 회귀 가드: 접지 않은 절단본(stage_slice stg_fin_wise 2,712행)에서 fi_fin_summary 전 열이 지금 코드 출력과 같음(지금 코드로 골든 먼저 고정) · A→B→A 를 각 D 에서 읽으면 A·B·A · available_date ≤ D 이고 '처음 본 날' · `_cov`·fi_consensus·annual 출력 그대로.
- 부정 테스트: wsnap 을 종목 단위로 되돌리면 G1 FAIL.

### 7-3 재사용 (wip/b7-stage, 7-1 뒤)
- Files: `src/stage/reuse.py`(신설) · `__main__.py`(morning 시도·끄기 파일·출력 줄) · `manifest.py`(BuildRecord 선택 필드 3) · `model.py`(TableRule.morning_reuse) · `tests/test_stage_reuse.py`(신설) · `test_stage_health.py`.
- G1: 같은 원장으로 저녁 e_ 뒤 아침 → `build_table` 을 부르지 않고 m_ 커밋(옛 코드는 다시 지어 FAIL), 재사용 판 content_hash = 일반 빌드 판.
- 회귀 가드(조건을 하나씩 깨면 일반 빌드): 새 blob · 같은 날 같은 키를 다른 원문으로 덮음 · 원문 같고 fetched_at 만 다름 · rules_version 다름 · code_rev 다름·없음 · 현재 판이 m_·b_·재사용 판 · baseline 항목 다름 · 연도 다름 · 끄기 파일 · 저녁 판 parquet 1개 손상(해시 재계산 불일치). 그 밖에 저녁 판 `_meta.json` 바이트 그대로 · keep 3 으로 e_ 디렉터리가 지워진 뒤에도 m_ 판 읽힘 · 재사용 판에서 C1·C3·C4·C6 통과 · 출력 줄 해석 그대로.
- 부정 테스트: 지문에서 sha256 을 빼면 '같은 키·다른 원문' 시험 FAIL.

### 7-4 문서·판본
- 판본 주석(stage 2.7.0, fi1.4.0) · STAGE_DESIGN §1 예외 (f) '연속 판 접기 — 원장에는 남는다'·§4 두 표 행·§9 G8 등식 · STAGE_HANDOFF `:85`·`:102` fetched_date 뜻 · FACTOR_INPUTS `:77` available_date · README 운영(아침 재사용·끄기 파일) · DECISIONS N-36 진행·§3-C D-Q4 주석(stage 쪽만 해소, 원장 그대로)·N-34 이름 정정(D7-11) · TECH_DEBT B-69~.

## 서버 재연 (7-5, 10-11 일, 체인 밖, 한 번에 하나)
- 입력: 10-09 08:10 아침 스냅샷(D=10-08 — 원장은 10-12 18:05 까지 안 바뀜), GC 됐으면 임시 폴더로 wisereport.db 만 VACUUM INTO(1.24GB). `mktemp -d`·코드 사본, 운영 data/stage·factor_inputs 무흔적, 끝나면 삭제 전 보고.
- ① 구현 코드의 접기 수 = 이 문서 시뮬레이션(남긴 blob 2,829 / 19,588 / 1,188 / 1,139 / 1,199, 행 1,380,846 · 932,706).
- ② 격리 빌드 두 표 각 2회: content_hash 같음, G8 등식·sha 불일치 0, `/usr/bin/time` 소요·RSS·쓰기 블록 — 23분 → 3~6분 가설 확인.
- ③ 각 날짜 최신 판 동일성: 운영 현판(2.6.0) 대 ② 판 — 모든 수집일 d(fin_wise 28, _q 5)·모든 단위 (ticker, ep, pkey) 에 대해 '옛 판의 d 행 집합' = '새 판의 fetched_date ≤ d 최신 행 집합'(키 seq, fetched_date·observed_date·observed_n·available_date 제외 전 열) 차이 0, 새 판 모든 행 = 옛 판 같은 (키, fetched_date) 행.
- ④ fi 동일성: 임시 stage 루트(② 두 표 + 나머지 운영 판 읽기 전용 링크), wip/b7-fi on 접힌 stage 대 feat/v3-merge fi1.2.0 on 운영 stage(둘 다 운영 equity e1.25.0), D ∈ {09-15, 10-01, 10-02, 10-06, 10-07, 10-08}: fi_fin_summary available_date 제외 전 열 차이 0(예외는 미리 센 '한쪽 ep 결손 날' 목록과 정확히 같을 때만), available_date 새 ≤ 옛·바뀐 행 수, 다른 fi 7표 차이 0, scope 순위 같음, compat financial_summary 1일.
- ⑤ 재사용 연습(임시 루트): e_ 뒤 같은 스냅샷 morning → 재사용·content_hash = 일반 빌드 · 이전 스냅샷 → 일반 · 임시 DEPLOYED rev 변경 → 일반 · 끄기 파일 → 일반 · `stage.health --basis morning` C1·C3·C4 통과 · 지문 소요.
- ⑥ sha 전수: cF3002·cF4002 전 blob(57,415) sha256(zlib 해제 원문) = sha256 열, 불일치 0(약 30~60초, 추정).

## 배포 (7-6)

| 날짜 | 할 일 |
|---|---|
| 10-09(금, 휴장) | 플랜 보고·승인 |
| 10-09~10 | 7-1~7-4 구현, 갈래별 검토 |
| 10-11(일) | 7-5 재연, 구조 검토 |
| 10-12(월) | 묶음 6 배포(확정). 묶음 6 이 feat/v3-merge 에 들어간 뒤 wip/b7-int 재통합·전체 테스트 |
| 10-13(화) 10:45~15:10 | 묶음 7 배포 — 조건: 묶음 6 첫 관측(10-12 21:20 잠정 · 10-13 08:10 확정 · 10:30 워치독·발송 장부) 정상, D6-7 22거래일 재생성 끝 |
| 10-13 21:20 | 첫 2.7.0 저녁판(두 표 접힘) |
| 10-14 08:10 | 첫 재사용 아침판 + 자동 발송(D=10-13), 10:30 워치독 |
| 예비 | 10-14 낮 창. 위험 첫 아침 10-16 08:10(D=10-15) |

1. 체인·원장 락·빌드 락 비었는지 확인, 되돌릴 rev(묶음 6 rev) 기록.
2. `deploy.sh --apply --allow-branch feat/v3-merge`(전량 테스트), 바뀐 파일 md5 대조.
3. 빌드 락 아래 두 표 수동 b_ 빌드(`python -m stage --table … --snapshot-id <10-13 아침 스냅샷>`, 각 약 2~4분, 추정) — G8 등식·sha 0·행 수가 재연 예측 + 10-12 분과 맞는지.
4. 7-5 ③ 을 운영 직전 판(m_ 10-13, 2.6.0)과 b_ 판으로 한 번 더(읽기 전용, 수 분).
5. fi 를 임시 out-root 로 D=10-12 1회(발송 없음) — 10-13 08:10 운영 fi 판과 fi_fin_summary 비교, available_date 제외 차이 0 또는 예측 목록과 같음.
6. 관측: 저녁 — 두 표 소요·행 수·C1~C6·equity·체인 종료. 아침 — `reused_from` 줄·지문 소요·C1·C4·종료 시각·fi·엑셀·10:30 워치독.
- 묶음 6 과 겹치는 파일: stage 는 안 겹침. `factor_inputs/build.py`(RULES_VERSION 같은 줄) · `queries.py`(묶음 6 adj `:308-347`, 묶음 7 fin `:529-606`) · `model/contracts.py`(`:79-89` 대 `:184-187`) · 테스트·문서. 병합 순서 묶음 6 → 7, 판본 줄은 fi1.4.0 + 1.3.0 이력 주석.

## 범위 밖 (TECH_DEBT B-69~)
1. 원장 적재 때 같은 sha 재적재 금지 — ledger_health 항등식(`ledger_health.py:470-471`, REQ_COVERED)·D-Q4·RM:74 재결정이 걸림.
2. fstrim 매일화(N-36 ②) — 묶음 7 효과를 보고.
3. stage 열 축소·parquet·JSONL 성능(raw__ 열 중복, 1행 2.2KB JSONL, 창 함수 spill).
4. cF4002 매일 변경 — 접은 뒤 stg_fin_wise 증가의 절반 이상, 행 단위 접기나 가격 의존 비율 분리.
5. 두 표가 948만 행으로 돌아오는 시점(약 40~80거래일, 추정) — 12월 초 재측정.
6. 아침 재사용 일반화(WISE 6표 등).
7. 접을 수 없는 표의 증가 감시 — stg_consensus_monthly 하루 +20.7만 행·108초, matrix +10.9만·54초.
8. '마지막 확인일'이 stage 에서 사라진 것 — 필요해지면 원장 직독 지표로(기록형 `n_keys_stale` 가 일부 대신).

## 결정할 것

| ID | 갈림길 | 권고 | 대안 | 근거 |
|---|---|---|---|---|
| D7-1 | '직전 판' 정의·수집 공백 | 같은 단위의 바로 앞 원장 blob(공백 무시) | 전역 수집일 기준 — 공백 뒤 첫 blob 은 같아도 남김 | 공백을 넘어 접힌 경우 10-08 원장 전체 1건, 공백 동안 신선도는 `_cov` 가 판정, 대안은 수집일 목록이 하나 더 필요 |
| D7-2 | 대상 표 | stg_fin_wise·_q 두 표 | WISE blob 표 전부 | 컨센서스·의견 표는 fi `_cov`·matrix 조인·equity coverage_daily 가 fetched_date 를 수집일로 씀 — 리비전 PIT 값은 같지만 신선도·커버 판정이 깨짐 |
| D7-3 | 같은 원문 판정 근거 | 원장 sha256 열 + 남긴 blob 재계산 대조(G8) | stage 가 전부 풀어 해시(+15~20초, 추정) / 압축 바이트 비교 | 적재 때 원문으로 계산한 값(`backfill_wise.py:412`), 대조로 열 뜻 오류를 첫 빌드에서 잡음 |
| D7-4 | fi·compat 스냅샷 단위 | (종목, ep) 최신 ≤ D, 한쪽 ep 결손 날엔 직전 판을 이음 | 옛 동작(종목 한 날짜) 유지하고 _q 만 접기 | 옛 동작은 접기와 함께 쓸 수 없음, 차이는 '한쪽 ep 결손 날'뿐(재연에서 건수 보고), 결손은 N-30 ③ 경보가 따로 알림 |
| D7-5 | C3 첫 판 감소 1회 처리 | 배포 때 두 표 수동 b_ 빌드(코드 없음) | health 에 (표, 판본) 면제 선언 | 코드 총량을 줄임(P4), 배포 확인과 같은 일, 빠뜨리면 FAIL 쪽(P1) |
| D7-6 | 아침 재사용 판정 입력 | 원장 내용 지문 + 규칙 판본 + 코드 rev + baseline·픽스처·연도 | src_bytes·원천 수정 시각 | 내용이 정확, 크기·시각은 DB 전체 축이라 넓고 내용 동일 보장 없음 — 기록만 |
| D7-7 | 아침 재사용 범위 | 두 표(구조는 표 선언으로 일반) | WISE 8표 | 승인 범위, 나머지 절감 약 3분(추정)이라 측정 뒤 |
| D7-8 | FG1 손익(cF3002) 비율 | 재연 6개 D 에서 비율 ≥ 0.95 면 폐기형(하한 0.9 공유), 아니면 기록형 | 기록형만 | 지금 FG1 은 cF4002 값만 봐서 이번 결함 부류를 못 잡음 |
| D7-9 | 외부 공유 소비자 알림 | 사용자가 1회 알림 + STAGE_HANDOFF 기록 | 문서만 | 날짜별 행 존재·fetched_date 뜻이 바뀜, 외부 발송은 사람이 정함(P3) |
| D7-10 | 배포일 | 10-13(화) 10:45~(묶음 6 첫 관측·D6-7 뒤) | 10-14 낮 | 위험 첫 아침(10-16 08:10) 전 하루 여유, 묶음 6 과 원인 분리 |
| D7-11 | 묶음 이름 충돌 | N-34 의 '배포 묶음 7 안(업종 시트 개편)'을 '대기 목록 묶음'으로 고쳐 적음 | 그대로 | 같은 이름이 두 뜻 |
