# 영역 A — 일일 수집·운영(원장·크론·체인·로그)

- 범위: database/scripts/ 운영 스크립트 전부, src/daily/*, 일일 경로가 부르는 src/backfill_*.py·src/api.py, 서버 crontab ↔ README 크론표·DECISIONS R1·결정 11, 최근 7일 로그, data/raw/*.db 최신 날짜
- 시작: 2026-10-06 02:53 KST
- 방식: 읽기 전용(저장소·서버 쓰기 금지, 외부 API 금지)

## 결함

### A-01 신규 상장 종목의 DART corp 매핑(dart_corp_map)이 09-09 뒤로 갱신되지 않는다 — equity corp_code NULL [중]
- 상황: `dart_corp_map`(→ stage `stg_corp_map` → equity `corp`·`corp_ticker`·`security`)을 만드는 `src/dart_universe.py` 를 어느 크론·체인도 부르지 않는다. 서버 `data/corps.txt` 마지막 수정 09-09 07:10. 플랜 v1 Task 1.6 Step 2("daily_ledger.sh 에서 주 1회(월요일) 재작성")는 [x] 인데 `daily_ledger.sh` 에 호출이 없다.
- 인풋: 09-10 이후 상장한 보통주·스팩 — 키움 마스터(유니버스)에는 들어와 키움·KIS·WISE 수집 대상이 되지만 corp 매핑에는 없음.
- 에러 위치: `scripts/daily_ledger.sh:61-81`(dart_universe 호출 없음) · `scripts/dart_company_gap.sh:12-15`(공백 계산이 `dart_corp_map` 기준이라 신규 corp 를 못 봄) · `src/equity/sql/corp_ticker.sql:26-71`(corp_map LEFT JOIN → NULL).
- 위험성: silent 데이터 결손(신규 상장 누적). 현 equity `corp_ticker`(m_20261003T003807) 에서 9종목이 `corp_code=NULL, link_basis='none'`: 0197V0(09-10)·0161M0 네오사피엔스(09-21)·0200G0·0209J0(09-22)·0010S0(09-23)·0035S0·486510(09-29)·266690(09-30)·468670(10-01). 이들의 DART 재무·공시·회사정보(`dart_company` 도 gap 스크립트가 못 채움)가 equity 에 연결되지 않는다. 지금은 9종목 모두 `fi_universe.has_estimates=False`(estimates_none)라 scope 엑셀 영향 0 이지만, 애널리스트 커버가 붙는 순간 재무 없이 유니버스에 들어오거나 퀄리티·밸류가 결측으로 빠진다. 워크벤치·연구 DB 소비자는 이미 영향.
- 근거: `grep -rn dart_universe scripts/ src/daily/` 0건 · `ls -la data/corps.txt` Sep 9 07:10 · kiwoom.db 최신 마스터 보통주 2,652 중 dart_corp_map 미등재 9(위 목록, 첫 스냅샷일) · duckdb `corp_ticker` 조회 corp_code None.
- 기존 여부: 10-05 감사 §3 표가 "Task 1.6 은 [x]인데 코드에 없다"만 적음 — 영향(신규 상장 corp 미연결 9건)은 이번이 처음.

### A-02 확정·잠정 빌드 종료 시각이 워치독 판정 시각에 붙었다(아침 여유 9~11분) — 문서의 실측 종료값은 낡음 [중]
- 상황: stage 전량 소요가 매 거래일 늘고 있다(인계 이력 `data/deliver/history/*_{morning,evening}.json` elapsed_s.stage). 아침: 09-28 2,919s → 09-29 3,884 → 09-30 3,885 → 10-01 4,463 → 10-02 4,386(73분). 저녁: 3,327 → 4,582 → 5,042 → 5,093 → 5,416s(90분).
- 인풋: 평일 08:10 `daily_build.sh`(KRX → ka10008 14분 → 머지 → 건전성 → build_morning) · 21:20 `build_evening.sh`. 워치독은 10:00(`0 1 * * *`)·23:30(`30 14 * * 1-5`) 한 번씩.
- 에러 위치: `scripts/watchdog.sh:6-8`(주석 "실측 종료 09:23~09:30", "상한 23:06") · `README.md:144-149`(같은 값) · 판정 `watchdog.sh:143-160`(latest_morning.json date ≠ D 이면 crit). KRX 재시도는 `daily_build.sh:44-70` 10분 × 최대 6회.
- 위험성: 운영 오탐·감시 공백. 아침 확정판 실제 종료 09-29 09:25 → 09-30 09:41 → 10-01 09:41 → 10-02 09:51 → 10-03 09:49 로 10:00 판정까지 9~11분 남는다. KRX 가 08:10 에 미공표라 재시도가 걸리면(1회 +10분이면 종료 09:59~10:01 로 경계, 2회면 확실히) 빌드가 도는 중에 워치독이 "확정판 인계 파일이 D 것이 아니다" crit 을 낸다(오탐). 아침 stage 는 09-28 49분 → 10-02 73분으로 4거래일 사이 +24분 늘었다(WISE 분기 콜 추가 같은 계단식 증가 + `stg_fin_wise` 누적 재파싱의 완만한 증가, HD §1). 이 추세면 재시도 없이도 10:00 을 넘는 날이 곧 온다. 워치독은 하루 1회라, 10:00 뒤에 빌드가 실패해도 다시 보지 않는다. 저녁도 10-02 23:02 종료로 23:30 까지 28분(09-28 22:28 → 10-02 23:02, 4거래일 +34분) — 증가가 이어지면 몇 주 안에 넘는다(RM 은 저녁 빌드 중단 예정). 문서가 09:23~09:30 을 실측으로 적고 있어 판정 시각 근거가 사라졌다.
- 근거: `grep -nE "build_morning 종료" logs/daily_build_2026*.log` → 09:25:02 · 09:41:06 · 09:41:06 · 09:51:06 · 09:49:17 · history elapsed_s · notify.log "잠정판 준비 22:57/23:02".
- 기존 여부: HD DoD-8(아침 ≤ 60분, "지금 약 85분")·B-22 는 '느리다'만 기록. 워치독 판정 시각과의 여유 소진·문서 실측값 불일치는 새 발견.

### A-03 WISE 저녁 수집이 한 번 어긋나면 그날은 재실행으로 복구할 수 없다(건전성 영구 FAIL) — 수집기 rc 도 실패를 싣지 않는다 [중]
- 상황: 18:05 `daily_evening.sh` WISE 갈래(`backfill_wise.py --mode full`). 수집기는 종목마다 커밋하고 `ws_run_log` 는 런 끝에 한 줄 쓴다. 재실행은 "오늘 cF5001 을 받은 종목"을 통째로 건너뛴다.
- 인풋: (a) 런 중간 중단(encparam 추출 실패 RuntimeError·OOM·재부팅·kill) 뒤 같은 KST 날 재실행, (b) 런은 끝났지만 일부 콜 실패(http 5xx·exc → n_ok < n_req, 또는 validate 실패 n_bad>0).
- 에러 위치: `src/backfill_wise.py:288-290`(cF5001 받은 종목 전부 skip — 다른 엔드포인트 실패도 다시 안 부름) · `:292-293`(대상 0 이면 run_log 없이 return) · `:388-405`(실패가 있어도 run_log 만 쓰고 정상 종료 — `main()` 은 예외 외엔 항상 rc 0) · `src/daily/ledger_health.py:395-409`(`wise.run` 은 그날 **마지막 런** 1행으로 판정) · `:418-434`(`wise.req_identity` 는 마지막 런의 `n_req` 를 **그날 호출 원장 전체**(covered×18 + none×4)와 비교).
- 위험성: 운영 정지(그날 확정판 소실, 복구 경로 없음). (a) 재실행 런의 n_req 는 남은 종목분뿐이라 항등식이 반드시 깨진다 — 로컬 재현: 1차(A 커버 18콜·B 무커버 4콜, run_log 없음) + 재실행(C 18콜, run_log n_req=18) → `wise.req_identity required fail {'expected': 40, 'actual': 18}`. (b) 재실행은 "대상 0 종목"으로 끝나 run_log 를 안 남기므로 첫 런의 n_ok<n_req 가 그대로 남는다. 둘 다 08:10 `ledger_health` REQUIRED FAIL → `daily_build.sh:112-118` 이 확정 빌드를 건너뛴다 → 그 D 의 확정판·fi·모델 판이 없다(사람이 `--skip wise` 로 전체 WISE 판정을 꺼야만 지어진다). 또 (b) 에서 수집기 rc 가 0 이라 저녁 체인은 `wise_rc=0`·"daily_evening 완료" info 를 내고 잠정 빌드가 부분 WISE 로 진행된다 — 이상은 다음 날 08:10 에야 드러난다.
- 근거: 코드 경로 + 로컬 재현(`scratchpad/audit/sim/sim_wise.py`, uv python 3.11, 저장소 코드 import·바이트코드 미기록). 최근 7일 실발생은 없음(ws_run_log n_bad 0, wise_rc 0).
- 기존 여부: gate-register MD-F ③ 가 '모델 DB 저녁 게이트는 wise_rc 미사용·n_bad=1·rc 0 런 FAIL'로 미래 게이트에만 적음. 재실행 불능은 기록 없음 — 새 발견.

### A-04 정기보고서 마감일 다음 날 06:00 체인이 08:10 을 넘겨 확정 체인이 통째로 건너뛰어진다(재시도 없음) [중 · 소요는 실측 단가 기반 추정]
- 상황: 06:00 `daily_ledger.sh` 가 원장 락을 잡고 키움 ka20068(≈14분) → KIS(≈28분) → DART(스윕 + 상세 재호출 + 문서)를 순서대로 돈다. DART 는 D 의 정기보고서마다 7종 유닛을 `unlock` 해 **다시** 부른다 — 전날 18:05 저녁 런이 같은 D 를 이미 다 불렀어도 06:00 에 한 번 더 전량 재호출한다.
- 인풋: 분기·반기·사업보고서 제출 마감일 D(다음: 3분기 마감 11-14 토 → 11-16 월 전후). findings-B 실측 08-14 하루 정기보고서 2,273건 → 상세 ≈18,200콜.
- 에러 위치: `src/daily/dart_daily.py:704-717`(plan → unlock → 전 유닛 재호출, 저녁·아침 같은 D 반복) · `scripts/daily_build.sh:13-19`(원장 락 `flock -n` 실패 시 warn 후 `exit 3`, 재시도 없음).
- 위험성: 운영 중단(예측 가능한 날). dart_call_log 실측 단가: 10-03 06:41 런 상세 867콜 8.5분(0.59초/콜) · 스윕 548콜 32분(3.5초/콜). 마감일 상세 18,200콜 ≈ 3시간 → 06:00 체인 종료 ≈ 10:00 이후. 08:10 `daily_build.sh` 는 락을 못 잡고 종료 → 그 D 의 KRX·ka10008·머지·건전성·확정판·(수동)모델 판이 하나도 안 생기고, 다음 날은 다음 D 로 넘어가 그 D 는 영영 안 지어진다. 경보는 warn·crit 이 로그에만 남는다(N-3). 같은 날 저녁 18:05 런도 4~5시간 걸려 원장 락을 23시 전후까지 쥔다(B-26 의 3시간이 더 길어짐). findings-B 는 '키 한도 여유'만 봤고 소요 시간은 보지 않았다.
- 근거: `sqlite3 dart.db` dart_call_log(k2·k3, ts 10-02T21:41~22:30) 구간별 콜 수·초당 · `docs/reviews/2026-09-09-daily-findings-B-dart-docs-wise.md:193`(마감일 ~18,200콜) · daily_build.sh 락 분기 코드. 마감일 실발생은 아직 없음(일일 체인 09-09 가동 뒤 첫 마감일이 11월) — 소요는 추정.
- 기존 여부: B-26 은 저녁 락 3시간 15분만, "06:00·08:10 은 겹치지 않는다"고 적음 — 마감일 겹침은 새 발견.

### A-05 DART 일일 완료 게이트가 '콜을 했다'만 보고 '받았다'는 보지 않는다 — 하위 도구는 중단해도 rc 0 [하 · 잠복]
- 상황: `dart_daily.run` 은 스윕(`sweep_disclosure.py`)·상세(`backfill_dart.py`)·문서(`backfill_docs.py`)를 subprocess 로 부르고 rc≠0 이면 TOOL_FAILED 로 본다. 완료 판정은 `periodic_followed`(fnlttSinglAcntAll 콜 유무)·`major_followed`(DS005 콜 유무)·`filings`·`documents` 넷.
- 인풋: DART 측 800/900·네트워크 예외가 재시도(RETRY_MAX) 뒤에도 남은 유닛, 또는 키 전부 접힘("전 키 사용 불가 — 중단") 같은 중도 중단.
- 에러 위치: `src/daily/dart_daily.py:504-516`(`_gate_periodic` — dart_call_log 를 status 조건 없이 셈) · `:519-530`(`_gate_major` 동일) · 지분(elestock·majorstock)·정기보고서 부속 6종(dividend·shares·capital·tesstk·hyslr·audit)은 게이트 없음 · `src/backfill_dart.py:724-728·736`·`src/sweep_disclosure.py:384-388` 등 모든 '✖' 경로가 `return`(rc 0) → `dart_daily.py:726` bad_rc 에 안 잡힘. `backfill_dart.py:397` 은 실패 시도도 dart_call_log 에 남긴다("실패도 예산에 계상").
- 위험성: silent 데이터 결손. 06:00 재스윕(그 D 의 마지막 기회)에서 어느 corp 의 fin 콜이 끝내 실패해도 콜 기록이 있으니 게이트 통과, `ingest_log` 에는 error 로 남지만 다음 날 플랜은 다른 D 라 그 유닛을 다시 부르지 않는다(그 corp 이 다시 공시해야 재호출). 지분·부속 6종은 실패해도 아무 신호가 없다. 최근 실발생은 없음(09-20 뒤 k2·k3 비정상 status 는 document 014 7건뿐, ingest_log error 0).
- 근거: 코드 경로 · `dart_call_log` 09-20~ status 집계 · `ingest_log` status 집계(open 1).
- 기존 여부: 기록 없음(새 발견, 잠복).

### A-06 원장 백업이 원장과 같은 물리 디스크 한 장에만 있다 — 디스크 장애 시 다시 받을 수 없는 시계열이 영구 소실 [중]
- 상황: 서버 디스크는 `sda`(476.9G) 하나이고 `/`(ubuntu-lv)만 있다. 원장(`data/raw` 20 GB+문서 27 GB)·스냅샷 3판(54 GB)·주간 백업 1세트(`~/backups/quant-ledger/20261003`, 19 GB)가 모두 이 LV 에 있다. 맥 로컬에는 `~/quant-ledger/data/equity`(09-29)만 있고 원장 사본은 없다.
- 인풋: 디스크·LVM 장애, 파일시스템 손상, 서버 분실.
- 에러 위치: `scripts/backup_raw.sh:17`(`BACKUP_ROOT=$HOME/backups/quant-ledger` — 같은 파일시스템, `:44` 여유 계산도 같은 FS 를 전제) · README:177-186(위치·보관만 적고 매체 분리는 없음).
- 위험성: 데이터 손실(복구 불가). 원천이 과거를 다시 주지 않는 축이 있다 — 키움 ka10008(날짜 파라미터 없음, 최신 50영업일만, `kw_daily.py:104-111`)·ka10099 마스터 일별 스냅샷(관리·정지 상태 이력, 그날만 관측)·WISE 일별 컨센서스 스냅샷(`backfill_wise.py:5-9` "일별 해상도는 소스가 주지 않으므로 매일 1회 스냅샷으로 우리가 만든다", 09-01~)·KIS 신용 최초 관측판. 주간 백업은 논리 손상만 막고 매체 장애는 못 막는다(백업도 같이 사라짐).
- 근거: `ssh kael-server 'df -h; lsblk'` → sda 1개·LV 1개 · `ls ~/backups` · 로컬 `ls ~/quant-ledger/data` → equity 만.
- 기존 여부: 결정 9(주 1회·1세트)는 보관 주기만 정함. 매체 분리 논의 기록 없음 — 새 발견.

- A-02 보충(체인 순서): `daily_build.sh` 는 원장 락을 시작(08:10)부터 build_morning·daily_report 가 끝날 때까지 쥔다(`:13-20`, 실측 ~09:50 해제). 토요일 10:00 `wics_weekly.sh --retry`(`0 1 * * 6`)도 같은 원장 락을 `flock -n` 으로 잡으므로(`wics_weekly.sh:25-29`), 금요일 판 빌드가 10:00 을 넘기면 재시도가 락 실패 warn 으로 끝난다. 03:00 런이 빈 응답을 남긴 주라면 WICS 스냅샷이 불완전한 채 남고, `wics.integrity` 는 REQUIRED 라(`ledger_health.py:478-480`) 다음 주 내내 확정 빌드가 막힌다(사람이 재실행해야 풀림). README:202 의 "daily_build.sh 끝(≈08:55 KST)"도 실제(≈09:50)와 다르다.

  - 근거 보충: 10:00 재시도가 실제로 일한 주가 있다 — `logs/wics_weekly_20260923.log` 09-26 03:00 `ok=37 fail=1 rc=1` → 10:00 `ok=1 skip=37`(그날은 연휴라 08:10 빌드가 없어 락 충돌이 없었다). 정상 토요일이면 금요일 판 빌드(08:10~)가 같은 락을 쥐고 있다.

  - 보고 문구도 오도한다: morning_build 정상 알림 제목의 시각은 원장 건전성 JSON 의 mtime 이다(`watchdog.sh:122-123·161`) — 10-03 확정판은 09:49 에 끝났는데 로그에는 "watchdog morning_build 정상 08:24" 로 남는다. 빌드 종료 시각을 읽으려는 사람이 여유가 70분 넘게 있다고 오해한다.

### A-07 WISE 수집 유니버스가 '규모구분 있는 종목'만 받아, 규모구분이 아직 없는 신규 상장 보통주의 컨센서스를 몇 달씩 한 번도 받지 않는다 — scope 유니버스에서 조용히 빠짐 [중]
- 상황: 키움 유니버스(`daily/universe.py:43-54`, 조건 `:52`)는 09-09 에 "규모구분은 상장 몇 주 뒤에야 붙는다"를 이유로 `OR (marketName IN ('거래소','코스닥') AND 6번째 자리 '0')` 를 더했다. WISE 수집기는 옛 규칙(`upSizeName<>''`) 그대로다. 플랜 v1 Task 1.1 Step 2 는 "backfill_wise.py 는 이 함수(kiwoom_common)를 import" 를 [x] 로 적었지만 import 하지 않는다.
- 인풋: 10-05 마스터 기준 키움 유니버스 2,652 중 `upSizeName=''` 48종목 — 국내 보통주 20(상장 06-08~10-01) · 해외 DR 22(900xxx·950xxx, sec_type=dr 이라 scope 대상 아님) · 스팩 6. 이 중 시총 ≥ 1,000억(scope 하한, U7) 국내 보통주 12종목.
- 에러 위치: `src/backfill_wise.py:235-264`(`universe()` — `:247` `upSizeName<>''` 만, `kiwoom_common` import 없음) · 그 결과 `fi_universe.has_estimates=False`, `exclude_reason='estimates_none'`(factor_inputs 는 WISE 원장만 본다).
- 위험성: silent 유니버스 결손(D-10 '추정치 보유 종목' 정의와 어긋남, 원본 v3 와 대조 차이). 실증: 0039P0 매드업(07-01 상장, 시총 1,497억) — 원본 v3 `quant.db consensus_annual` 에는 2026/12E 매출 706·영업이익 125·순이익 130·EPS 731, 2027/12E 도 값이 있다. 우리 `wisereport.db ws_raw` 는 0039P0 행 0건, fi_universe 는 estimates_none 으로 제외. 브릴스(3,864억)·스카이랩스(3,381억) 등 나머지 11종목도 커버가 붙는 순간 같은 경로로 빠진다(규모구분이 붙을 때까지; 06-08·06-10 상장 종목이 4개월째 공백).
- 근거: kiwoom.db 최신 마스터 `upSizeName=''` 48 · fi_universe(m_20261005T165747) 조회 · `sqlite3 -readonly ~/kael-system-v3/data/quant.db` consensus_annual 0039P0 값 · `ws_raw where cmp_cd='0039P0'` 0.
- 기존 여부: 09-09 플랜 표(:350)는 키움 쪽 87종목 누락만 기록·수정. WISE 쪽 같은 결함은 기록 없음 — 새 발견.

### A-08 키움 마스터 스냅샷(06:00, 2콜)에 재시도가 없다 — 한 번 실패하면 그날 확정판이 막히고 그날 상태 이력은 영구 결손 [하 · 잠복]
- 상황: `daily_wise.sh` 가 06:00 에 `master_daily.py` 를 한 번 부른다(코스피·코스닥 각 1콜). 이 스냅샷(snap_date=D)은 그날만 관측되는 축(관리·정지·증거금 상태)이고, 08:10 D+1 의 `ledger_health` 가 `kiwoom.master`(snap_date=D, n≥4200, 2시장)를 REQUIRED 로 본다.
- 인풋: 06:00 키움 일시 장애(5xx·HTML 응답 → `api.kiwoom` ValueError, 또는 8005 재발급 뒤에도 실패).
- 에러 위치: `src/master_daily.py:36-42`(실패 시장은 건너뛰고 재시도 없음; 비JSON 은 예외로 프로세스 종료) · `scripts/daily_wise.sh:27-39`(crit 후 종료, 재시도 없음) · 18:05 저녁 체인은 마스터를 다시 받지 않는다 · `src/daily/ledger_health.py:298-303`(REQUIRED).
- 위험성: 운영 중단 + 데이터 손실. 같은 KST 날 안에 사람이 `daily_wise.sh` 를 다시 돌리지 않으면 그 D 의 확정판·fi·모델 판이 생기지 않고(다음 날은 다음 D), 그날 마스터 행은 다시 받을 수 없다(스냅샷 날짜 = 실행일 KST). 알림은 crit 이지만 N-3 로 로그에만 남아 당일 복구 가능성이 낮다. 실발생 1회(로그: kospi·kosdaq 모두 8005 — 그날은 수동 재실행으로 09-02 스냅샷 존재), 09-01~10-05 스냅샷 35일 모두 존재.
- 근거: `daily_wise_*.log(.gz)` grep "✖" · `ka10099_stock_master` 날짜별 건수.
- 기존 여부: U9 는 하한(4,200)만. 단발 수집·재시도 부재는 기록 없음 — 새 발견(잠복).

### A-09 서버 crontab 주석이 실제 시각과 다르고, 지운 프로브의 주석이 남아 있다 [하 · 문서 불일치]
- 상황: 서버에서 운영자가 가장 먼저 읽는 것이 `crontab -l` 이다.
- 인풋: `crontab -l` 68행 "# quant-ledger 워치독(결정 V2-7 → 결정 11): 21:50 저녁 원장 보고 · 23:00 잠정판(보류) · 09:15 확정 빌드 보고" — 실제 줄은 23:30(`30 14`)·10:00(`0 1`). 65행 "# [임시 · 플랜 P0 Task 0.7 · 3거래일 뒤 제거] 키움·KIS T-1 확정 시각 프로브" — 명령 줄 없이 주석만 남음(README:155 "09-16 제거").
- 에러 위치: 서버 crontab 65·68행(코드 귀속 없음). README 복구용 원문(README:161-171)에는 주석이 없어 원문 대조로는 안 잡힌다.
- 위험성: 문서 불일치. 장애 때 워치독 시각을 23:00·09:15 로 잘못 읽고 "아직 판정 전"으로 오판하거나, 이미 없는 프로브를 찾는다. 결정 11(23:00)·U18(23:30) 혼동을 키운다.
- 근거: `ssh kael-server crontab -l | grep -n …` 61·65·68·69·70·72행.
- 기존 여부: §6-4 는 README 복구 원문의 WICS 3줄 누락만. crontab 주석은 기록 없음.

## 기존 항목 — 이번 실측으로 재확인(새 결함으로 세지 않음)
- K-1 (HD H0-3 · v2 T1-2) 일일 리포트가 KIS `partial`(rc 0, 게이트 통과)을 "수집 실패 런"으로 crit 처리(`scripts/daily_report.py:259-261`). 실측: 10-02 리포트(D=10-01)·10-03 리포트(D=10-02) 모두 crit, 원인은 각 1종목 실패(263720 EGW00316 · 271830 OPSQ1002, run_id 164·173). README:209 "런 failed" 와도 다름. 매일 아침 점검(Q-6)을 하면 이틀 연속 '즉시' 오탐.
- K-2 (HD H0-3 · v2 T1-3, 미구현) DART 결산월 미상 라벨 → `periodic_followed` FAIL. 10-01 원인은 일화모직공업(00146719, 001590) — 키움 마스터·dart_corp_map 에 없는 비유니버스 법인이라 `dart_company.acc_mt` 가 영원히 비어 `resolve_with_acc_mt` 로 못 푼다(`dart_daily.py:371-374` 은 `stock_code<>''` 전부를 플랜에 넣음). 저녁(run 160)·06:00(run 165) 두 번 실패, 06:00 실패로 `dart company gap` 도 건너뜀(`daily_ledger.sh:80-81` `&&`). ledger_chain 20261001 은 failed 로 영구 남음(다음 날은 다른 D).
- K-3 (N-3 · Q-6 대기) crit 이 어디에도 전달되지 않는다. 10-01 이후 notify.log 의 crit 4건(10-01 21:20 저녁 DART · 10-02 07:21 daily_ledger dart · 10-02 09:51·10-03 09:49 일일 리포트)은 로그에만 있다. 워치독·일일 리포트의 "알림 실패(24h) 0건"은 텔레그램을 안 쓰므로 항상 0 — 의미 없는 지표.
- K-4 (B-26) 저녁 체인 원장 락 3시간 15분 — A-04 마감일에는 더 길어짐.
- K-5 (HD R-5/T1-7·H1-5) 저녁 키움 실패 시 다음 날 REQUIRED FAIL, 06:00 자동 보강 없음 — 코드 그대로(`daily_ledger.sh` 는 ka20068 만 받음).
- K-6 (HD H0-3) `deploy.sh` 가 빌드·원장 락을 보지 않는다 — 코드 그대로(`deploy.sh:62-133`). 이번 배포 10-05T17:52Z 는 체인 공백 시간이었다.
- K-7 (§6-6 · 10-05 감사 C) SKIP=통과 — 기록은 "넷"이지만 실제 REQUIRED/HALT 에서 SKIP 이 나오는 곳이 더 있다: `ledger_health.py:279`(ka10008.stale_pct, D·D-1 겹침 0)·`:296`(krx_cross, matched 0)·`:472`(wics.raw, 표 없음), `kw_daily.py:623`(저녁 직행 기준선 0). 형제 검사(rows·krx.rows)가 같은 결손을 FAIL 로 잡아 지금은 실해 없음 — 기록 수치만 정정 필요.
- K-8 (U10·U11·U19) KIS 신용 fresh·WISE 분기 콜·WICS integrity 가 REQUIRED — 코드 그대로.
- K-9 (U18) 워치독 23:30 — crontab 그대로.
- K-10 (U9) KRX 행수 하한 중 `krx_kosdaq_dd_trd >= 40` 은 여유가 0 이다 — 08-03~10-02 매일 정확히 40(`ledger_health.py:241-245`, REQUIRED). KRX 가 코스닥 지수 하나만 폐지·통합해도 그날부터 확정 빌드가 막힌다(코스피 쪽은 09-11 지수 추가로 51→54, 여유 3). 하한값 자체는 U9 기존, '여유 0' 은 이번 관찰.

- A-04 보충(단가 재측정, 10-02 18:05 저녁 런 dart_call_log): 스윕 list.json 548콜 54분(5.9초/콜) · 정기보고서 부속 7종 156콜 약 2분(0.74초/콜) · 지분 400콜 3.8분 · DS005 330콜 2.9분 · 문서 20건 7초. 06:00 런과 저녁 런의 콜 수가 거의 같다(1,415 vs 1,435) — 같은 D 전량 반복 확인. 마감일 정기보고서 18,200콜이면 0.55~0.75초/콜로 2.8~3.8시간.

- K-5 보충: 사람이 다음 날 아침에 `daily_evening.sh --date D` 로 복구하려 해도 키움 갈래가 `QL_KW_EVENING_HHMM`(기본 2105)까지 `sleep 60` 으로 기다리고(`daily_evening.sh:64-67`, 현재 시각만 비교 — 날짜 무관), 그 뒤에도 `--not-before 20:00` 이 시각만 비교해(`kw_daily.py:_too_early`) 20:00 전이면 rc 3 이다. 복구하려면 `QL_KW_EVENING_HHMM=0000 QL_EVENING_NOT_BEFORE=00:00` 두 개를 알고 줘야 하는데 런북이 없다(README:173 은 변수 이름만).

### A-10 원장 건전성의 DART 예산·v3 키 검사가 하루의 절반(06:00 런)만 본다 [하]
- 상황: DART 는 하루 두 번 돈다 — D 저녁 18:05(접수일 D) · D+1 06:00(같은 D 재스윕). `ledger_health` 는 08:10 D+1 에 "오늘 KST 0시 이후" 콜만 센다.
- 인풋: `check_dart(..., kst_day_start_utc)` — `today` = 실행일(D+1) 0시 KST.
- 에러 위치: `src/daily/ledger_health.py:372-378`(`dart.key.kael` HALT · `dart.budget` WARN ≤ 2,500/일) · `:519-521`(`kst_start_utc` = 실행일 0시).
- 위험성: 감시 공백·오판. 저녁 런(D 의 KST 날)은 어느 건전성 리포트에도 안 잡힌다. 실측: 10-02 KST 하루 우리 키 사용은 2,639콜(그날 저녁 런 runlog "ours=2,639 since 10-01T15:00Z"), 09-29 2,511콜로 WARN 기준 2,500 을 넘었지만 리포트 값은 `dart.budget {"k2": 1415}` PASS. 기준 2,500("평시 830~2,000")도 하루 1회 시절 실측이라 2회 체제에선 평일 대부분이 기준선 근처다. v3 키 사용은 `dart_daily` 가 런마다 따로 막으므로(pre/post budget) 실해는 작다.
- 근거: `logs/health/20261002.json` dart.budget · daily_run.db dart 런 detail(ours=…).
- 기존 여부: 기록 없음(새 발견, 하).

## 문제없음 확인
- 배포 정합: 서버 `DEPLOYED.json` rev b220060a = 로컬 feat/v3-merge HEAD, scripts/*.sh md5 전부 동일, src/daily·src/*.py md5 동일(서버 전용 rebuild_share.py·sync_v3_wise.py 만 다름 — 의도된 제외).
- crontab ↔ README 크론표 12줄 시각·인자 일치(백업 토 03:30 · 06:00 · 08:10 · 10:00 · 18:05 QL_KW_EVENING_HHMM=2105 · 21:20 · 21:50 · 23:30 · WICS 토 03:00·10:00·11:30 · gc 일 04:30). R1·결정 7·R5·R6·R9·결정 9·V2-1·V2-3·D-6(21:05 유지)이 코드·크론과 일치. 10-06 프로브 설치는 postclose 블록 7줄 추가뿐(백업 crontab 과 diff).
- 10-05 대체공휴일 처리: 캘린더(kis_holidays_2026.json 121건)에 20261005 포함 → 06:00 D=10-02 '이미 완료' 건너뜀 · 08:10 '확정판 완료' 건너뜀 · 18:05 저녁 '휴장' · 21:20 잠정 '휴장' · 21:50·23:30 워치독 '휴장' · 10:00 워치독 D=10-02 정상. KRX ingest 에 10-05 행 없음(다음 수집 때 holiday 로 기록될 경로 확인). 10-09(금) 한글날도 목록에 있음.
- 원장 최신일(가벼운 조회): KRX ingest_log 10-02 7/7 ok · 키움 4TR(005930) 10-02 · 마스터 20261005 · KIS(005930) deal_date 09-30(= D-2 정상) · DART 마지막 콜 10-02T22:22Z · WISE run 10-02 full n_ok=n_req n_bad 0 · WICS dt 10-02 38코드.
- 10-02 원장 건전성 26/26 pass, SKIP 0. 최근 7일 KRX 재시도 0회(18회 연속 첫 시도 성공), 키움 저녁 errors 0, WISE n_bad 0.
- N-3 준수: 텔레그램 직접 발송 경로는 `notify.sh`(기본 로그만)와 `deliver`(모델 엑셀)뿐. crontab·환경에 QL_NOTIFY_TELEGRAM 없음.
- 비밀: 토큰 캐시 2종 0600, 최근 원장·DART 로그에 crtfc_key 평문 0건(grep 파일 0개), logs/ 의 tg_*.py 는 .env 를 읽기만(토큰 리터럴 0).
- 시계·자원: NTP 동기화 yes, 디스크 여유 200 GB(56%), 스냅샷 3판 54 GB·백업 1세트 19 GB 로 keep 규칙대로 유지, logs 49 MB.
- 락: 원장 락(raw)·빌드 락(build)·compat 락 규약 일관, 자식은 QL_*_LOCK_HELD 로 상속. 스냅샷은 VACUUM INTO(락 없음) — 설계 주석과 일치.
- 런로그: daily_run.db 에 'running' 고착 행 0.
- 캘린더 연 경계: `kis_holidays_<year>.json` 누적 + 미보유 연도 KeyError(조용한 폴백 없음) — 2027 파일은 v3 연말 갱신(12-30 05:00 KST) 뒤 06:00 동기화로 들어오는 경로.
- PIPESTATUS(if/else 뒤) 동작: 서버 bash 5.2 에서 실험으로 정상 확인(daily_evening WISE rc 수집 경로).

### A-11 KIS 신용 적재가 종목마다 900만 행 전체 COUNT(*)를 두 번 돈다 — 06:00 체인의 07:00 여유를 깎는다 [하 · 성능]
- 상황: 06:00 체인의 KIS 단계(실측 06:13→06:41, 27.5분)는 v3 토큰 재발급(07:00) 전에 끝나야 한다(daily_ledger.sh:7, HD T1-4 '06:55 마감 가드' 미구현).
- 인풋: 종목 2,655개 × `store_new_facts` 1회.
- 에러 위치: `src/daily/kis_daily.py:289`·`:294` — 삽입 전후 `SELECT COUNT(*) FROM kis_credit_balance`(현재 9,046,263행)로 증가분을 잰다(`con.total_changes` 로 충분).
- 위험성: 성능·운영 여유 감소. 서버 실측 COUNT(*) 1회 0.11초(캐시 따뜻할 때, 차가울 때 18.9초) × 2 × 2,655 ≈ 9.7분 — KIS 단계 소요의 약 35%. 행이 하루 ~2,500 씩 늘어 선형으로 느려진다. 첫 COUNT 가 캐시 냉각 상태면 그 한 번에 19초.
- 근거: `sqlite3 -readonly kis.db "select count(*) …"` 3회 시간(18.88·0.12·0.11초) · daily_run.db kis_credit started/ended.
- 기존 여부: 기록 없음(새 발견, 하). 07:00 충돌 자체는 HD T1-4 기존.

- A-01 보충: 신규 상장사는 `dart_company` 도 비므로(gap 스크립트가 corp_map 기준) `acc_mt` 가 없다. 비12월 결산 신규 상장사의 분기보고서는 K-2 와 같은 경로로 `periodic_followed` unresolved → DART 게이트 FAIL(저녁·06:00 두 번)로 이어진다. 라벨 월이 03·09 인 비12월 결산 신규 상장사(예: 6월 결산의 1분기 (YYYY.09))는 acc_mt 가 없어 12월 기준 월 규칙(`dart_daily.py:295` `_QUARTER_REPRT`)대로 1Q↔3Q 가 뒤바뀐 reprt_code 로 호출되고, 게이트는 '콜했다'로 통과한다(A-05) — 조용한 결손(`:393-401` 은 acc_mt 가 있을 때만 덮어씀).

- A-04 보충(원장 실측 마감일 물량): dart_disclosure 상장사 정기보고서·분할병합 접수 건수 상위 일자 — 2025-08-14 2,418 · 2026-08-14 2,276 · 2025-11-14 2,159 · 2025-05-15 2,096 · 2026-05-15 2,066(3월 사업보고서는 하루 최대 702 로 분산). 1년에 4번(5·8·11월 중순) 하루 2,000건대가 실제로 온다. 다음은 3분기 마감 2026-11-16(월, 11-14 이 토요일). 문서 ZIP 상한 3,000(`dart_daily.py` DEFAULT_MAX_DOCS)은 이 물량 안이라 문제없음.

  - 마감 D 가 금요일이면 토요일 08:10 이 실패해도 일요일 08:10 이 같은 D 를 다시 돈다(`daily_build.sh:86-92` 가드는 확정판이 있을 때만 건너뜀, 06:00 은 '이미 완료'로 건너뜀) — 주말에 자가 복구. 평일 D(예: 11-16 월)는 다음 날 08:10 이 다음 D 로 넘어가 그 D 판이 영구 결손.

## 가설(확인 못 함 — 결함으로 세지 않음)
- H-1 키움 토큰 강제 재발급 경쟁: `api.py:105-132` `_kw_token(force=True)` 에 락이 없다. 저녁 2TR(ka10060·ka10014)이 스레드로 동시에 8005 를 받으면 두 번 발급 → 먼저 받은 토큰이 폐기돼 한쪽이 TOKEN 실패(rc 2)할 수 있다. 09-23 비JSON 사고("두 스레드가 동시에 빈 본문")와 같은 계열로 의심되나 최근 7일 token_failed 0 — 재현 없음.
- H-2 키움 마스터 `cont-yn=Y`(다음 페이지 있음)는 `master_daily.py:43-45` 가 '!' 만 찍고 받은 만큼 적재하며 `daily_wise.sh:33` 은 '✖' 만 실패로 센다 → 부분 마스터가 성공으로 보고된다. 4,200 하한(U9)이 큰 절단은 잡지만 작은 절단은 유니버스 축소(키움 유예 5일·WISE 즉시 제외)로 조용히 번질 수 있다. 실발생 0(로그 grep cont-yn=Y 0건).
- H-3 KIS 06:00 단계가 07:00 v3 토큰 재발급과 겹칠 위험(HD T1-4 기존) — 현재 06:41 종료로 19분 여유, A-11 이 그중 약 10분을 소비.
- H-4 키움 앱키 공유: 10-02 v3 daily_pipeline 이 20:05~22:09(UTC 11:05~13:09) 돌아 우리 21:05~21:20 키움 수집과 시간상 겹친다(HD T1-6 '측정 먼저' 기존). 우리 쪽 errors 0, v3 쪽 키움 오류는 adj_prices(22:09~) 502 1건뿐 — 충돌 증거 없음.

## 미조사
- `src/backfill_docs.py` 전체(일일 경로의 키 선택·자정 대기만 확인 — 키당 40,000 한도라 실해 없음), `src/backfill_kis.py`(kis_daily 가 쓰는 classify·normalize 외), `src/backfill_kw.py`(동결 백필, 일일 미사용).
- `scripts/run_stage_all.sh`·`run_stage.sh`·`equity_rebuild_all.sh`·`equity_gate_all.sh`·`run_equity.sh` 의 표 단위 rc 전파(영역 B·C 몫, B-28 기존).
- `scripts/compat_export.sh`·`model_compare.sh`(크론 없음, 영역 E), `src/probe_postclose.py`·postclose 크론 블록(지시상 범위 밖), `src/rt.py`·`sustain.py`(옛 유량 실측 스크립트 — 서버 src 에 배포돼 있으나 크론 없음, import 시 토큰을 발급하므로 실수 실행 주의).
- 09-28 이전 로그 전수(gz 압축분은 표본만), WISE·KRX 응답 **내용** 검증(행수·항등식 외).
- 모델 파이프라인(fi → model → deliver)은 크론이 없고 수동(logs/manual/*) — Q-3 기존, 영역 D·E.

- A-07 보충: 규모구분 공백 국내 보통주 20종목 전수를 원본 v3 `consensus_annual`(data_type=estimate, 값 있음)과 대조 → 2종목에 추정치가 있다: 0039P0 매드업(시총 1,497억, scope 시총 하한 위 — estimates_none 으로 먼저 빠지므로 N-6 추정기관수 조건은 판정조차 안 됨; c1010001 을 안 받아 기관수는 모름) · 0017J0 세미티에스(975억 — scope 하한 1,000억 아래라 scope 영향은 없음. v3 추정 2026/12E 매출 316·영업이익 98·순이익 101). 비교 모델의 시총 하한은 확인 안 함.


- 종료: 2026-10-06 03:33 KST · 새 결함 11(중 6 · 하 5), 기존 재확인 10, 가설 4

## 끝
