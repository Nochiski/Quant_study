# 영역 B — stage 층 전수 조사

- 범위: src/stage/* 전부(build·__main__·rules_*·parsers·model.py RULES_VERSION·health C1~C6·freshness·doc_prepass·snapshot·manifest·gates·baseline), scripts/run_stage_all.sh, scripts/doc_prepass_daily.sh, docs/STAGE_DESIGN.md·STAGE_SPEC.md 대비 코드, 서버 data/stage/*/MANIFEST.json·빌드 로그·health json·snapshots
- 저장소: ~/orca/workspaces/Quant_study/v3-merge/database (feat/v3-merge)
- 서버: kael-server ~/quant-ledger (읽기 전용)
- 시작: 2026-10-06 02:53 KST

## 결함

### B-01 stg_fin_wise 빌드 최대 메모리 13.4GB(서버 RAM 16.1GB) — 수집일마다 늘어 OOM 으로 확정판이 멈출 위험 [상]
- 상황: WISE cF3002·cF4002 원문을 매일 전량 저장(D-Q4)하고 stage 는 매 빌드 모든 수집일 원문을 다시 푼다. 서버 RAM 16,115,408kB·스왑 4.2GB, 저녁 빌드(21:21~)는 v3 daily_all(20:05)·리서치 잡과 겹칠 수 있다.
- 인풋: 10-03 아침 `run_stage_all.sh snap_20261002T232452Z --basis morning` 의 stg_fin_wise(5,569,188행).
- 에러 위치: `src/stage/build.py:418-448` `_load_blob_source` — `fetchall()` 로 ep IN (cF3002,cF4002) 블롭 전부(파서가 버리는 Q:IS·Y:BS·Y:CF pkey 포함)를 메모리에 올리고, `parsers._parse_fin`(`parsers.py:344-394`)이 출력 행 전부를 파이썬 dict 리스트로 쥔 채 JSONL 을 쓴다. 행 수에 선형인 메모리이고 상한이 없다(duckdb `memory_limit=6GB` 는 파이썬 쪽을 묶지 못한다).
- 위험성: 최대 RSS 13,375,880kB(=13.4GB, `/usr/bin/time -v`) — 다른 표는 전부 7.4GB 이하. 행 수는 수집일마다 약 22만(약 +4%) 늘어(summary.tsv 3,120,458 → 5,569,188, 11수집일) 행당 약 2.4KB(13.4GB/5.57M) × 수집일당 +22만 행 ≈ 하루 +0.5GB 라 다른 프로세스 몫(약 1~2GB)을 빼면 약 4~5수집일(10-08~10-12 무렵) 안에 물리 메모리를 넘는다(추정 — 메모리가 행 수에 선형이라는 가정. 코드상 출력 행 전부를 dict 로 쥔다). 넘으면 스왑·OOM kill → 표 rc≠0 → C1 FAIL → stage 건전성 FAIL → equity·확정판·엑셀 미생성. 저녁에는 v3 잡과 겹쳐 더 이르다. 같은 경로를 타는 stg_fin_wise_q 는 수집일당 약 64만 행(10-01 636,663 → 10-02 1,280,589, 소요 44 → 89초)으로 세 배 빠르게 자라 지금 RSS 6,959,184kB 에서 1~2주 안에 같은 벽에 닿는다(추정). 카테고리: 운영 중단. 소요 증가(10.7분)는 hardening:44·model-db Q3 에 있으나 메모리 한계는 기록이 없다(새 발견).
- 근거: `/proc/vmstat` 부팅(09-06) 뒤 pswpout 3,094,828쪽(≈11.8GB)·pswpin ≈13.3GB, 지금도 스왑 0.9GB 사용(SwapFree 3,280,260kB/4,194,300kB), oom_kill 0 — 이미 메모리 압박으로 스왑이 돈다. 같은 행수(5,569,188)인데 저녁 1,067s·아침 804s 로 저녁이 33% 느리다. `grep "Maximum resident" logs/stage_all/stg_fin_wise.log` → 13375880 · 다른 표 최대 stg_fin 7433816 · `/proc/meminfo` MemTotal 16115408kB · summary.tsv stg_fin_wise 행수 열 · 로컬 tracemalloc 측정: `FIN_WISE_COLUMNS` 42열 dict 한 행 ≈ 1.8KB → 5.57M 행 ≈ 10GB(+블롭 원문·duckdb) 로 실측 RSS 와 맞아 선형 모델을 받친다.

### B-02 C3 행수 비감소가 upsert 날짜축 8표(KRX 4·키움 4)의 올해(2026) 파티션을 보지 않는다 — 최근 구간 행 손실이 무검출 [중]
- 상황: KRX·키움 원장(upsert)은 `INSERT OR REPLACE`/`INSERT OR IGNORE` 만 하고 데이터 표에서 행을 지우지 않는다(`backfill_krx.py:136-137`, `kw_daily.py:355·747`, `load_kiwoom_raw.py:64`; DELETE 는 ingest_log·_kw_incoming 뿐). 그래서 올해 파티션 행수가 줄 정상 경로가 없다.
- 인풋: 원장 사고(예: kiwoom.db 를 토요일 주간 백업 세트로 복원 — backup_raw.sh 는 최신 1세트만 보관) 뒤 다음 체인이 stage 를 다시 짓는 경우. 복원으로 월~수 dt 행이 빠지고 목요일 행이 새로 들어온다.
- 에러 위치: `src/stage/health.py:240-251` `_c3_monotonic` — `mode == "upsert" and year >= this_year` 이면 `continue`. 근거로 적힌 "올해 파티션은 원장이 아직 쓰고 있어 감소가 정상일 수 있다"(:222-223)는 수집기 코드와 맞지 않는다. 설계 문서(STAGE_DESIGN.md:349)와 테스트 `tests/test_stage_health.py:211-213`(`test_c3_는_upsert_표의_올해_파티션_감소는_넘어간다`)가 이 가정을 고정한다.
- 위험성: 가격 정본 `stg_price_daily`·`stg_listing_daily`·키움 수급 4표의 2026 파티션(모델이 쓰는 유일한 구간)이 줄어도 G1·G5(등식)·C3(올해 제외)·C4(계수가 움직여 대조 생략)·C6(최신일만 봄, 10일 허용)가 모두 통과한다. 사라진 세션 행은 equity·fi·엑셀까지 조용히 결측이 된다. 카테고리: 데이터 손실(silent). 현재 감소 사례는 없다(10-03 health C3 pass).
- 근거: health JSON `C3 pass append_only 52표 행수 비감소 · 파티션 축 13표(upsert 는 2026년 이전 파티션만)` · `grep -n "DELETE FROM" src/daily src/*.py` → 데이터 표 삭제 0.

### B-03 stage 소요가 거래일마다 늘어 워치독 시각(아침 10:00·저녁 23:30)을 곧 넘는다 — 정상 판에 crit 오탐·후속 지연 [중]
- 상황: 워치독 시각은 "stage 실측 43~66분 + equity 9~11분 = 상한 23:06", "실측 종료 09:23~09:30" 을 근거로 잡았다(`scripts/watchdog.sh:5-8` 주석). 그 뒤 stg_fin_wise(모든 수집일 원문 재해석, B-01 과 같은 원인)·stg_fin_wise_q·WISE 컨센서스 표가 수집일마다 느려졌다.
- 인풋: 매일 08:10 `daily_build.sh` → `build_morning.sh`, 21:20 `build_evening.sh` (둘 다 `run_stage_all.sh` 68표 전량).
- 에러 위치: `scripts/run_stage_all.sh:55-69`(표 통째 재빌드) · `src/stage/build.py:418-448`(WISE blob 전 수집일 재파싱) — 시각 판정은 `scripts/watchdog.sh`(morning_build 10:00, evening_build 23:30).
- 위험성: 아침 체인 종료 09-22 09:21 → 09-30 09:41 → 10-02 09:51 → 10-03 09:49, stage 초 2,709 → 4,463. 저녁 stage 3,172s(09-21) → 5,416s(10-02), 종료 22:25 → 23:02. stg_fin_wise 한 표가 325s → 1,067s(저녁). 같은 기울기(아침 거래일당 약 +2~4분)면 아침은 수 거래일, 저녁은 1~2주 안에 워치독 시각을 넘는다(추정). 넘으면 정상 진행 중인 판에 "보고 없음" crit 가 찍히고(10-01 부터 알림은 로그에만 남는다 — N-3, watchdog.log 'logged only'), 진짜 실패와 지연을 가를 수 없게 된다. 아침 확정판에 의존하는 후속(모델·엑셀 수동/예정 작업)도 그만큼 늦어진다. (참고: 09-24 에도 같은 crit 가 났는데 그때는 09-23 저녁 원장 실패로 확정판이 13:10 에 끝난 진짜 지연이었다 — 판정이 '10:00 에 인계 파일 날짜'만 보므로 느린 정상 판과 구분하지 못한다, `watchdog.sh:143-157`.) 카테고리: 운영 중단(오탐·지연). 소요 증가 자체는 hardening:44·gate-register GH4 에 기록돼 있으나 워치독 충돌 시점은 기록이 없다.
- 근거: `logs/morning/build_*.log`·`logs/evening/build_*.log` 의 `════ 종료` 줄, `logs/stage_all/summary.tsv` 블록별 elapsed(아래), health C5 `73.1분`(10-03 아침)·`90.2분`(10-02 저녁).
  - stg_fin_wise elapsed 최근 9블록(저녁·아침 교대): 400 → 603 → 520 → 751 → 603 → 922 → 805 → 1,067 → 804초

### B-04 건전성 C6 이 표 0개를 판정해 SKIP 이어도 체인 판정은 통과(ok) — §6-6 목록 밖 다섯 번째 'SKIP = 통과' [하]
- 상황: C6 은 표마다 `max_available_date` 로 신선도를 본다. 옛 판·포맷 회귀로 이 값이 전 표에서 비거나, freshness 선언이 통째로 빠지면 판정 대상이 0이 된다.
- 인풋: `python -m stage.health --basis morning --date D ...` 에서 judged 표 전부가 `NO_RECORD`·`UNDECLARED`·FROZEN 등으로 빠지는 경우.
- 에러 위치: `src/stage/health.py:370-377` 은 n_checked==0 이면 `Status.SKIP`("통과로 세지 않는다" 주석)을 내지만, `StageHealth.ok`(`health.py:71-73`)는 FAIL 만 실패로 세고 `main()`(`:443`)은 ok 면 rc 0 → `build_chain.sh:279` 가 H_STAGE=ok 로 equity·인계·_READY 를 진행한다.
- 위험성: 데이터 축 신선도 검사(DEFECT-B01 의 유일한 방어선)가 통째로 꺼져도 확정판이 나간다. 주석의 의도("통과로 세지 않는다")와 실제 동작이 반대다. 지금은 33표를 판정해 발생하지 않는다(10-03 C6 pass 33표). 카테고리: silent(검사 무력화)·문서 불일치. DECISIONS §6-6 이 꼽은 넷(ledger_health 3곳·`freshness.py:113-128`)에는 이 자리가 없다.
- 근거: 코드 대조(`health.py:370-377`, `:71-73`, `:443`), `logs/health/stage_20261002_morning.json` C6 `33표 신선도 (건너뜀 35표)`.

### B-05 C6 신선도가 미래 날짜 available_date 에 가려진다 — stg_disclosure max_available_date 10-06 (10-03 빌드) [하]
- 상황: DART 목록 API 는 일부 주요사항보고서에 접수번호 날짜보다 늦은 `rcept_dt` 를 준다(예: 20261002000673 이 10-02 09:58 UTC 수집 때 이미 rcept_dt 20261006, 20260911000607 은 09-11 수집 때 20260914). stg_disclosure available = greatest(rcept_dt, 접수번호 접두)라 수집 시점보다 미래 날짜가 된다(룩어헤드 아님 — 보수 방향).
- 인풋: 10-03 아침 빌드 m_20261003T001619 — `available_date > observed_date` 15행, max_available_date = 2026-10-06.
- 에러 위치: `src/stage/build.py:581` `_max_date(stage_pq, "available_date")` 를 상한 없이 C6 입력으로 싣고, `src/stage/health.py:359-367` 이 그 값을 D − 허용지연과만 비교한다. 비키 날짜 허용 범위는 [1900, 현재+40](`gates.py:20`)이라 rcept_dt 오타(예: 2062년)가 한 행만 있어도 C6 는 그 표에서 사실상 영구 통과한다.
- 위험성: DART 수집이 멈춰도 C6 가 미래 날짜만큼(지금 3~4일, 오타면 무기한) 늦게 잡는다. 카테고리: silent(검사 지연). 현재 손실 없음.
- 근거: stage parquet 조회 `count(*) FILTER (WHERE available_date > observed_date)` = 15, max(available_date)=2026-10-06 · 원장 `dart_disclosure` 행(rcept_no 20261002000673, rcept_dt 20261006, collected_at 2026-10-02T09:58:53).

### B-06 규칙 판본(RULES_VERSION)은 손으로 올리는 전역 상수뿐 — 산출을 바꾼 커밋이 판본 없이 나간 이력 3건, 문서층 파서 판본은 MANIFEST 에 없다 [하]
- 상황: stage 산출 규칙은 rules_*.py·parsers*.py·build.py 에 흩어져 있고, 판본은 `model.RULES_VERSION` 하나를 사람이 올린다. 강제 장치는 `tests/test_stage_model.py:173`(값 고정 단언)뿐이라 규칙을 바꾸고 판본을 안 올려도 테스트가 통과한다.
- 인풋(git 대조): ① 6f38d8d6(09-05) 보조원장 6표 키 라벨 정규화 — 2.2.3 유지 ② a46b13a2(09-11) 같은 날 판본 접기(rn_day) — 2.2.3 유지 ③ 8363fb4d(09-19 19:14) stg_disclosure available_date greatest — C4 가 잡은 뒤 8c2fc6ca(09-20 00:39)에서 2.3.0. 문서층 43178f40(09-28) 은 `parsers_doc.PARSER_VERSION` p1.5 만 올렸다(RULES_VERSION 2.4.0 은 3시간 뒤 다른 이유로).
- 에러 위치: `src/stage/model.py:11`(전역 상수), `src/stage/build.py:628·647`(MANIFEST·_meta 에 rules_version 만 기록, 문서층 행을 만든 파서 판본은 `_tmp/doc/<snap>/summary.json` 에만 있음), `src/stage/health.py:275-281`(C4 는 판본이 같고 소스 계수가 동결일 때만 해시를 대조).
- 위험성: 판본 없이 규칙이 바뀌면 소스가 자라는 표(거의 전부)에서는 C4 가 대조를 건너뛰어 아무 데서도 안 걸리고, 같은 rules_version 의 두 판이 다른 규칙으로 지어진다(재현·추적 불가, equity EG5a 도 같은 판본끼리만 비교). 현재 판(2.4.0/2.5.0)에는 영향 없음. 카테고리: 비결정(추적성)·문서 불일치.
- 근거: `git log -G'^RULES_VERSION = "' -- src/stage/model.py`(2.2.3→2.3.0→2.4.0→2.5.0 네 번), `git log -- src/stage/rules*.py src/stage/parsers*.py src/stage/build.py` 대조.

### B-07 체인 밖 수동 판(b_)이 current_build 를 바꿔 MANIFEST 와 _READY·latest_morning 이 서로 다른 판을 가리킨다 — CLI 는 빌드 락도 없다 [하]
- 상황: 10-05 19:04 KST 수동 재빌드(`logs/manual/rebuild_20261002_20261005T100452Z.log`)가 stg_analyst_summary 를 2.5.0 규칙으로 다시 지어 b_20261005T100452 를 커밋했다(같은 스냅샷 snap_20261002T232452Z). 나머지 67표는 m_20261003…(2.4.0). 10-06 아침 체인은 D=10-02 확정판이 이미 있어 가드로 건너뛰므로(`daily_build.sh` 확정판 가드) 이 혼합 상태가 10-06 저녁 체인까지 간다.
- 인풋: 소비자가 표 판을 고르는 두 경로 — MANIFEST `current_build`(fi·SFTP 소비자) vs `data/stage/_READY.json`·`data/deliver/latest_morning.json`(체인 완료 신호·Kael-alpha 포인터).
- 에러 위치: `src/stage/__main__.py:17-57`·`build.py:640-650` — 단독 CLI 가 락 없이 `manifest.commit` 으로 포인터를 바꾼다(락은 `run_stage.sh:8-13`·`run_stage_all.sh:9-14` 에만 있음). `_READY.json`·`latest_*` 는 체인 끝에만 쓰인다(`build_chain.sh:131-210`).
- 위험성: 지금 MANIFEST 는 stg_analyst_summary=b_(추정기관수 0 반영), _READY·latest_morning 은 m_20261003T001938(같은 칸이 NULL)이라 두 경로의 소비자가 같은 D 에 다른 값을 본다. 수동 CLI 가 체인과 겹치면 MANIFEST 읽고-쓰기 경합으로 포인터·keep GC 가 꼬일 수 있다(가설 — 겹친 실사례는 확인 못 함). 데이터 자체는 같은 스냅샷이라 일관. 카테고리: 비결정(포인터 불일치)·운영.
- 근거: 서버 MANIFEST(stg_analyst_summary cur=b_20261005T100452_285208Z rv=2.5.0, 나머지 rv=2.4.0) · `_READY.json` builds.stg_analyst_summary = m_20261003T001938_586669Z · latest_morning.json 동일.

### B-08 stage 문서·주석이 현재 동작과 다른 곳 8건 [하]
- 상황: 09-12~10-02 사이 운영 변경(결정 11 키움 21:05·애프터마켓, 빌드 락 정책, 소요 증가)과 빌더 수정이 문서·주석에 반영되지 않았다.
- 인풋/에러 위치(문서 ↔ 실제):
  1. `docs/STAGE_SPEC.md:81-90` §2-2 "키움 수급은 정규장 기준이다 · NXT(~20:00) 고려 불필요" ↔ 09-14 KRX 애프터마켓 뒤 21:05 수집분은 애프터마켓 수급·체결가를 포함(DECISIONS N-13 근거, `rules_krx.py:79-85` 주석). §2-21(`STAGE_SPEC.md:442`) 제목 "키움은 당일 18:10" ↔ 크론 `QL_KW_EVENING_HHMM=2105`.
  2. `docs/STAGE_DESIGN.md:72` "빌드와 크론이 같은 락 파일 /tmp/quant_ledger_raw.lock" ↔ `scripts/build_chain.sh:92-94` "raw 락은 잡지 않는다"(검수 R4-01).
  3. `docs/STAGE_DESIGN.md:351`(C5 근거 "실측 아침 43~54분·저녁 61~70분")·`scripts/watchdog.sh:5-8`("stage 실측 43~66분") ↔ health C5 실측 아침 73.1분·저녁 90.2분(10-02~03).
  4. `src/stage/rules_kis.py:274-275` "빌더의 `= '0'` 비교는 발화하지 않아 현재는 0.00 이 그대로 실린다" ↔ `build.py:105-107` 정규식이 '0.00' 을 잡아 NULL+ledger_zero 로 싣는다(서버 stg_loan_daily_kis close_krw ledger_zero 281행·값 0 은 0행).
  5. `src/stage/parsers.py:428-429` "추정기관수 = 제공처별 표(cTB24) 행 수와 같다" ↔ 10-02 수집분 808종목 중 21종목이 다르다(예 078600 요약 0·제공처 1행 07-01 의견, 016360 17 vs 19) — 3개월 창 경계 차이.
  6. `src/stage/rules_dart.py:516-517`·`build.py:208` 주석 "내용일 범위 [1990, 현재+40]" ↔ `gates.py:20` 실제 [1900, 현재+40](09-03 정정). `rules_dart.py:494` 는 rcept_dt 미래값을 '오타'로 적었지만 주요사항보고서에서 반복되는 원천 규약이다(B-05).
  7. `docs/STAGE_DESIGN.md:327` G6 "자연키 중복 중 payload 동일 비율 기록(임계 초과 = 재수집 잡음 과다)" ↔ `gates.py:195-204` 는 `n_dup` 하나만 싣는다(비율·임계 없음).
  8. `docs/STAGE_DESIGN.md:329` G8 "coverage 급락 감지(일별 행수 < 직전 중앙값 50% 플래그)" ↔ `gates.py:230-245` 에 없다 — WISE 응답이 절반으로 줄어도 stage 는 통과한다(원장 건전성 WISE ≥ 2,400 행 하한(U9)만 남는다).
- 위험성: 소비층·후속 세션이 문서를 근거로 수급을 정규장 값으로, 소요를 1시간 안으로, G6·G8 이 재수집 잡음·커버 급락을 잡는다고 가정한다. 카테고리: 문서 불일치.
- 근거: 위 파일:줄 대조, 서버 parquet 조회(stg_loan_daily_kis, stg_analyst_summary ⋈ stg_analyst_broker 10-02: 일치 787·불일치 21).

### B-09 원천 1행 오류가 매일 수집되는 표를 '영구 폐기'로 만든다 — 지우지 않는 원장 위의 폐기형 게이트(G2 임계 0·G3 불변식·키 유일·G8 파싱 실패) [하]
- 상황: 매일 쌓이는 원장은 append_only·first_write_wins 라 한 번 들어온 행이 사라지지 않고, stage 는 매 빌드 전 이력을 다시 짓는다. G7 은 이 문제 때문에 '행 격리형'으로 바뀌었지만(STAGE_DESIGN.md:328 "v2.1 의 테이블 폐기형은 두 테이블을 영구 빌드 불가로 만들었다") G3·G8 은 여전히 표 전체 폐기형이고, G2 는 행 격리형이지만 기본 임계가 0 이라 1행이면 표를 버린다.
- 인풋: 다음 중 하나가 원천에서 1행 들어오는 경우 — KIS 신용 `stlm_date < deal_date` 또는 잔고 음수(`rules_kis.py:356-360`), DART 재무 통화 빈칸(`rules_dart.py:113-115`), 감자 후 주식수 > 감자 전(`rules_dart_events.py:283`), 키움 마스터 listCount ≤ 0(`rules_kiwoom.py:251-254`, first_write_wins), 같은 접수번호의 rcept_dt 가 다음 스윕에서 바뀜(`rules_dart.py:40` key_unique), WISE 블롭 1개 손상·값 불일치(`gates.py:237-242` n_parse_failed·n_value_mismatch > 0), 숫자 칸에 새 비숫자 토큰 1개(예 '미정'·'N/A') — G2 기본 임계가 0 이라(`gates.py:15-16`, 09-02 survey '비숫자 0' 실측 기반) DART DS005 15표·KRX·키움·WISE 블롭 표 대부분이 해당.
- 에러 위치: `src/stage/gates.py:127-141`(G3 위반 > 0 → FAIL)·`:230-245`(G8) → `build.py:601-609` 판 폐기 → `health.py:154-178` C1(그 표가 오늘 판 아님) → `build_chain.sh:282-290` equity·인계·_READY 중단.
- 위험성: 원천 오류 1행이 다음 날부터 매 빌드를 같은 이유로 실패시켜, 규칙을 고치거나 원장을 손대기 전까지 확정판이 매일 멈춘다(자동 회복 경로 없음, 격리·임계 없음). 현재 위반 0(서버 MANIFEST G3·G8 전부 pass), 09-11 G6 폐기 2건이 같은 꼴의 실제 사례였다(코드 수정으로 해소). 카테고리: 운영 중단(구조 위험).
- 근거: 서버 MANIFEST G3 metrics(stg_credit_daily `stlm_before_deal_violations 0`, stg_master_daily `list_shrs_nonpositive_violations 0` 등), summary.tsv 의 과거 폐기 3건(stg_fin·stg_disclosure G6, stg_price_daily G9).

### B-10 게이트 둘은 구성상 실패할 수 없다 — G5(직전 판 Δ등식)·G6(같은 날 판본 유일) [하]
- 상황: 판은 G0~G9 전부 통과해야 커밋되므로 직전 판은 항상 G1(`n_stage = n_src×fanout − n_dedup − n_reject`)을 만족한다. 09-11 부터 같은 (자연키, observed_date) 의 rn=1 행은 `rn_day` 로 하나만 남긴다.
- 인풋: 아무 빌드.
- 에러 위치: `src/stage/gates.py:179-192` G5 — 현재 판이 G1 을 만족하면 Δ등식은 대수적으로 항상 성립(직전 판 계수는 `build.py:326-334` 가 MANIFEST 마지막 커밋 판 G1 metrics 에서 읽는다). `gates.py:195-204` G6 — `build.py:535-540` 의 `rn = 1 AND rn_day <= 1 AND reject_reason IS NULL` 필터 뒤에는 (자연키, observed_date) 중복이 생길 수 없다. 한편 `gates.py:230-245` G8 docstring 의 "방출 행수 = 원장 행수(n_src)" 는 실제로는 파서가 쓴 JSONL 을 다시 읽은 행수끼리의 비교(`build.py:440-447`·`545`)다.
- 위험성: 설계 문서(STAGE_DESIGN §9 G5 "설명 안 되는 증감 실패"·STAGE_SPEC §2-12 "G6 은 그대로 남아 구성 오류를 잡는다")가 기대하는 보호가 실제로는 없다. 원장 축소·재적재는 G5 로 못 잡는다(B-02 와 같은 공백). 카테고리: silent(검사 무력)·문서 불일치. gate-register RG-C1-05 가 "stage G0·G1·G5·G6 음성 대조 테스트 없음"을 적었지만 G5·G6 이 원리상 발화 불가라는 점은 기록이 없다.
- 근거: 코드 대조(위 줄), summary.tsv 전 이력에서 G5 실패 0·G6 실패는 rn_day 도입 전(09-11) 2건뿐.

## 기존 항목과 겹친 것(새 결함으로 세지 않음)
- U8(keep 3): fi 가 직접 읽는 stage 4표(stg_consensus_annual·stg_consensus_matrix·stg_fin_wise·stg_fin_wise_q)는 equity `_pinned/` 목록에 없다(서버 `ls data/equity/_pinned` 70표 중 없음) → keep 3(약 1.5거래일) 밖으로 나가면 그 fi 판이 쓴 stage 판이 지워진다. 지금 fi `_runs` 22건은 전부 현존 판(m_20261003…)을 가리킨다(10-05 일괄 재생성). WISE 표는 수집일 축 append 라 값은 뒤 판에서 다시 거를 수 있다.
- U14·계획감사 B(문서층 D0·D10 무관용, 프리패스 실패는 C1 밖): 현행 동작 그대로 확인. 최근 4회 프리패스 D0~D10 위반 0.
- §6-6(SKIP=통과 넷): `freshness.py:113-128` 그대로. 다섯 번째 자리는 B-04.
- hardening:44·model-db Q3(stg_fin_wise 소요 증가): 메모리 한계(B-01)·워치독 충돌(B-03)은 새 발견.
- gate-register RG-C1-05(stage G4 no_fixtures·음성 대조 없음): 68표 중 57표 G4 skip(no_fixtures) 확인. G5·G6 발화 불가(B-10)는 새 발견.

## 문제없음 확인
- 원장→stage 행 손실 없음: 스냅샷 원장 행수 = stage n_src — KRX stk+ksq 9,281,689 · ka10060 7,702,146 · ka10008 7,756,102 · KIS credit 9,046,263 · DART disclosure 3,511,468 · fin_raw 15,406,681. WISE 블롭 ep 별 수 = 파서 계상(c1010001 20,170 · cF3002 20,172+4,821 · c1050001_data 165,613 · cF5001 159,057 · cF5002 60,516). 전 표 reject 0.
- MANIFEST: 68표 전부 같은 스냅샷 snap_20261002T232452Z, keep 3, 판 3개씩. 67표 m_·2.4.0, stg_analyst_summary 만 b_·2.5.0(B-07).
- 건전성 최근 12회(09-22~10-03) 전부 ok. `_failed/` 5건은 09-04~09-12 옛 것. summary.tsv 전 이력 폐기 3건(09-11 G6 2·09-12 G9 1)뿐.
- observed_date +9h 변환: 원장 시각이 전부 UTC 무표기임을 표마다 확인(ka10060 2026-10-02T12:05:02, ws_raw 09:11:49, KRX 23:10:04, KIS 21:41:13 등). 잠재 주의: duckdb 1.5.5 는 '+09:00' 오프셋을 무시하고 +9h 를 또 더한다(서버 실측) — 수집기가 오프셋 시각을 쓰기 시작하면 하루 밀린다.
- 최초 관측 기준 접기의 A→B→A 판본 소실(설계상 가능) 실측 0: disclosure 다판본 2,227키 · KIS credit 1,583키 · fin_raw 25,085키 중 이전 페이로드로 되돌아온 키 0.
- 추정기관수 2.5.0: 10-02 수집분 808종목 NULL 0 · 0 이 276(문구 2종). 같은 '빈칸 = 0' 혼동을 다른 WISE 파서에서 찾았으나 대체값 0 없음(요약 목표가·EPS·의견·PER 0 = 0행, 브로커 목표가 0 = 0행, consensus parse_failed 지표 행 0).
- G2 임계 근접 표 추세: listing 신규 행 부분실패 0.5%(임계 1%) · credit 3e-5(1e-4) · shares 8.5%(12%) — 누적 비율은 내려가는 쪽.
- G9 거래량 일치율 0.99986555 vs 기준 0.99986518 − 5e-5 → 여유 약 380행.
- 룩업 21표 rcept_map_miss 0. WICS MKT_VAL 정수(×1e6 손실 없음). stg_loan '0.00' → NULL+ledger_zero 281행.
- 스냅샷 3세트 54GB · 문서 캐시 1개 3.7GB · stage 11GB · `_tmp` 잔재 없음 · 디스크 여유 200GB.
- RULES_VERSION 2.5.0 뒤 stage 규칙 변경 커밋 없음(배포 rev b220060a = 로컬 HEAD).
- 10-06 아침 체인은 D=10-02 확정판 가드로 빌드를 건너뛴다(10-05 로그로 동작 확인) → 다음 stage 빌드는 10-06 21:20 저녁.

## 미조사·가설
- (가설) stage 전량 1회의 디스크 쓰기 약 144GB(`/usr/bin/time` File system outputs 합, stg_fin 54GB·stg_fin_wise 29GB — 대부분 duckdb spill), sda 누적 쓰기 부팅 29일 5.35TB(하루 약 184GB). 보급형 512GB SSD 수명 영향은 SMART 를 못 읽어 미확인.
- (가설) N-13 으로 키움 수집을 20:00 전으로 당기면 G9 의 '20:00 KST 이후 관측' 컷오프 때문에 새 날짜가 대조 모집단에 안 들어가 G9 가 사실상 꺼진다(`rules_krx.py:30-33`).
- 저녁 빌드 시각대(21:21~23:00) 메모리 경합 — 시각별 RSS·v3 잡 메모리 기록이 없어 못 봤다(B-01 의 저녁 쪽 위험 크기).
- parsers_doc.py·rules_dart_events.py 세부 규칙의 행 단위 정합, STAGE_SPEC §6 회귀 고정 수치 전수 대조, stage.baseline 측정 SQL 재현 — 게이트 결과·계수만 봤다.
- equity `_pinned` GC 와 stage 판 보존의 상호작용 세부(영역 C).
- 조사 중 서버 쓰기 1건 고지: A→B→A 조회(dart_fin_raw, 56초) 때 duckdb spill 경로로 `/tmp/auditB_spill_ro` 를 지정했고 끝난 뒤 지웠다(프로젝트·원장 무관).

## 끝
