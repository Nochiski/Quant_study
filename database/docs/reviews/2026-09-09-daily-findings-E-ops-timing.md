# findings_E — 운영 환경 · 시간 창 · 자원 경합 · 모니터링

조사일 2026-09-09 (KST) · 대상 `kael-server:~/quant-ledger` · 읽기 전용 · 저장소 워크트리 main `b9f2aaf`
표기: **실측** = 서버 명령·DB 질의로 직접 잰 값. **추정** = 문서·코드 유추. 서버 TZ 가 UTC 이므로 **KST = UTC + 9**.

## 1. 서버 자원

### 1-1. 하드웨어 (실측)
| 항목 | 값 |
|---|---|
| CPU | 4 코어 |
| RAM | 15 GiB total · 13 GiB available · swap 4 GiB(미사용) |
| 루트 FS | 466 G 중 176 G 사용 / **271 G 여유** |
| 시스템 TZ | `Etc/UTC`, NTP 동기 — crontab 은 전부 UTC, 새 크론은 KST−9 로 적는다. `CRON_TZ=` 는 안 쓰는 것이 관례 |

### 1-2. `~/quant-ledger` 디스크 (실측 du)
전체 101 G. `data/raw` 45 G(documents 27 G · dart.db 6.8 · krx 3.8 · kiwoom 3.6 · kis 3.1 · wisereport 0.3+bak 0.6) · `data/snapshots` **37 G(7세트, GC 없음)** · `data/equity` 9.0 G(`_pinned/` 7.3 G) · `data/stage` 6.5 G(`_tmp/doc/` 3.7 G 삭제 가능) · `share/` 2.7 G(latest → v/20260827T0930) · `logs/` 4.3 M.
`~/backups/quant-ledger/` 17 G — `2026-09-02/` 하나뿐, 정기 백업 크론 없음.

### 1-3. memory-limit·threads 근거
- `run_stage.sh` 는 기본값을 강제하지 않고 인자를 그대로 넘긴다. 실제 상수는 빌더: `memory_limit 6GB · threads 3 · temp_directory data/stage/_tmp/spill`(STAGE_DESIGN.md:347, 근거 :358 벤치). 4코어에서 threads 3 = 코어 1개 남김.
- equity 는 `equity_rebuild_all.sh:23` 이 `run_equity.sh "$t" --threads 3 --memory-limit 8GB`.

### 1-4. 실측 최대 RSS·경과 (`/usr/bin/time -v`)
stage: `stg_fin` **7.16 GB / 11:06** · `stg_doc_section` 6.71 GB / 0:59 · `stg_price_daily` 6.35 GB / 0:59 · `stg_flow_daily_kiwoom` 6.35 GB / 1:46 · `stg_credit_daily` 6.34 GB / 3:01.
→ memory_limit 6GB 를 걸어도 RSS 7.16 GB. stage 1개 + equity 1개 동시 = 13.3 GB > available 13 GB. `stg_fin` 은 duckdb 스필 15~18 GB(STAGE_DESIGN.md:397).
stage 전량 1패스(`logs/stage_all_summary_pass1.tsv`): 68행 합 **1,389초 = 23.1분**, ok 57 / gate_failed 11.
equity 28표: universe_daily 1:19.6 (5.0 GB) · dataset_profile 1:11 · credit_daily 1:07 · price_adj_daily 1:00 (5.8 GB) · flow_daily 0:55 (6.1 GB) · short_daily 0:54 (6.1 GB) · fin_std 0:35 · price_daily 0:26. **합계 ≈ 512초(8.5분), 표별 최대 6.1 GB**, 역대 최대 6.51 GB.
스냅샷: `VACUUM INTO` 5 DB **17.7 GB/회**(logs/snapshot_all.log). krx+kiwoom 61초, dart 7.07 GB ≈ 80초.

## 2. 시간 창 지도 (KST)

### 2-0. 외부 데이터 확정 시각 — 실측
**KRX 일별 데이터는 T+1 08:00 KST 공표.** `data/evidence/krx_timing.db` 의 `probe` 154행: target 20260827 은 08-28 **07:55 에 0행, 08:00 에 944행**. 20260824·25 도 08:00 최초 비0. → 당일 저녁 배치는 KRX 를 못 받는다.
WISE `ws_raw.fetched_date` 최신 2026-09-09(당일 15,617행) · 키움 `ka10099 snap_date` 최신 20260909 · DART 상시.

### 2-1. 하루 지도 (crontab·systemd timers 실측; P=우리, V3=kael-system-v3, UNI=unitelegram)
| KST | UTC | 주체 | 작업 | API 점유 |
|---|---|---|---|---|
| 03:00 | 18:00 | V3 | DB 백업 | 디스크 I/O |
| 04:30 | 19:30 | V3 | event_extract | LLM |
| **06:00–06:05** | 21:00 | **P** | **daily_wise.sh** — 키움 마스터 2콜 + WISE full ~15,600 req @55/s | 키움 2콜, WISE. 디스크 81 tps(일중 최고) |
| 07:00 (일~목) | 22:00 | V3 | briefing morning | **KIS·키움 토큰 재발급 실측** |
| **07:45** | 22:45 | V3 | `dart.filings --mode incremental` | **DART `DART_API_KEY`** |
| **08:00 ★** | 23:00 | — | **KRX T+1 공표 (실측)** | — |
| **08:05–12:00** | 23:05–03:00 | — | **비어 있음** (30분 뉴스 틱·5분 GPU 경보만), idle 96–97 % | — |
| 09:00 | 00:00 | SYS | n8n 업데이트·logrotate·dpkg backup | 가벼움 |
| 12:15 (평일) | 03:15 | UNI | lunch brief | — |
| **12:20–15:30** | 03:20–06:30 | — | **비어 있음** | — |
| 15:35/15:40 | 06:35/06:40 | UNI | EOD·종가 브리핑 | 시세 |
| 18:15 | 09:15 | V3 | `dart.filings --mode incremental` | DART |
| **20:05–22:33 ~ 23:48** | 11:05–13:35 | **V3** | **`chain:daily_all`** (flock) | **KIS·키움 대량**, 일중 유일 CPU 피크(%user 55–66 %) |
| 21:00–22:10 | 12:00–13:10 | V3 | consensus(64분)·brokers·adj_prices | KIS |
| 매 30분 | */30 | V3 | news.ingest | RSS |

죽은 타이머 3개(`quant-fetch/enrich/consensus.timer`, ExecMainStatus=203, 04-03 이후 정지) — 자원 무점유, 제외 가능.
v3 daily_all 실측 창: 09-01~09-08 20:05 → 22:32~22:40, **09-04 는 23:48**(최장).

### 2-2. CPU 실측 — 기저 %user 2.0–2.6 %, idle 96–97 %. 유일 정기 피크 21:00–22:10 KST(v3). 09-04 10:00–11:00 UTC 99 %·09-06 은 우리 수동 빌드.
**결론: 08:05~15:30 KST 는 CPU·디스크·API 가 전부 비어 있는 7시간 25분 창.**

### 2-3. `daily_wise` 06:00 과 "실행 창 06:30~익일 05:30" 근거
- 크론 `0 21 * * *`(2026-09-02 에 18:00 → 06:00 변경). 실측 소요 06:00:01→06:04:47 **4.7분**, 요청 15,617.
- "실행 창" 출처: `run_stage.sh:4`, `STAGE_DESIGN.md:72` — wisereport.db 가 **delete 저널**이라 쓰기 중 읽기가 막히는 것을 피하는 잠금 회피 창(자원 창 아님).

## 3. API 키·한도 공유

### 3-1. 우리는 v3 와 같은 `.env` 를 읽는다 (실측)
`api.py:5-22` `_find_env()` 가 `~/kael-system-v3/.env` 를 잡는다. 키 이름: KIS 앱키 1개(v3 공유) · 키움 앱키 1개(공유) · DART 3개(`_2`,`_3` 우리 + `DART_API_KEY` v3). `_4/_5` 없음.

### 3-2. DART — kael 키가 마지막이지만 실제로 세 번 소진시켰다
`dart_call_log` 실측 `key_id=kael`: 08-26 19,500 · 08-27 29,316 · **08-28 35,588**(한도 40,000/일·KST 자정 리셋, DART_DESIGN.md:446-448). 우리 키 2개(80,000) 소진 후 자동 폴백. v3 는 같은 키로 07:45/18:15 증분. 최근 7일 status: 000 404,306 / 013 122,502 / 014 3,130 / **020 17** / exc 3.

### 3-3. 키움·KIS 토큰 캐시 — 파일은 따로, 앱키는 하나
우리 `~/quant-ledger/src/.kis_token.json`·`.kw_token.json`, v3 `~/kael-system-v3/data/.kis_token.json`·`.kiwoom_token.json`. mtime 실측: v3 둘 다 09-09 07:00 KST, 우리 `.kw_token.json` 06:00 KST. **매일 06:00 우리 발급 → 07:00 v3 발급** 반복. `api.py:118-127` 주석: "2026-09-02 실측: 만료 9시간 전에 8005 — 마스터 스냅샷 0행". 추정: 같은 앱키 재발급이 이전 토큰을 폐기. 배치가 07:00 을 가로지르면 8005 재발. `master_daily.py:39-42` 는 실패 시장을 조용히 건너뛴다.

## 4. 잠금·중복 실행 방지

### 4-1. flock 현황 (실측)
`run_equity.sh` 만 `/tmp/quant_ledger_equity.lock`. `daily_wise.sh`·`daily_dart.sh`(pkill)·`run_stage.sh`·`run_stage_all.sh` **없음**. v3 는 크론 전 줄에 `flock -n /tmp/kael_*.lock`. 설계가 규정한 `/tmp/quant_ledger_raw.lock` 은 아직 존재하지 않는다.

### 4-2. WAL (실측)
krx·kiwoom·kis·dart **wal**(-wal 0바이트, 체크포인트 완료) · **wisereport.db 만 delete 저널**(WAL 전환 미실시). 시나리오: ① 06:00–06:05 wisereport 읽기 차단 → VACUUM INTO 실패 가능 ② 동일 표 이중 빌드 RSS 14.3 GB > 13 GB → OOM ③ stage×equity 동시 13.3 GB 초과.

### 4-3. `daily_dart.sh` pkill 위험 4건 (`daily_dart.sh:16-24`)
① `pgrep -f` 문자열 매칭이라 수동 세션·ssh 까지 죽임 ② 5초 뒤 무조건 `kill -9` → finally/commit 건너뜀, `dart_call_log` 계상 누락 → 롤링 예산 과소 ③ `$PPID` 예외 조합 ④ kill↔재시작 사이 락 없음. → `flock -n /tmp/quant_ledger_dart.lock` 으로 교체 권고. 현재 crontab 미등록, 로그 09-02 마지막.

## 5. 로그·감시 현황
- `logs/` 4.3 MB, 로테이션 **없음**(`/etc/logrotate.d/` 0건). `daily_wise_MMDD.log` 는 1년 뒤 같은 파일에 덧붙는다. `logs/equity/` 실행마다 새 파일.
- **실패를 사람이 알게 되는 경로 없음.** `daily_wise.sh` 종료코드 미검사, MAILTO 없음. 09-03 로그 `⚠⚠ 본문 검증 실패 1건 {'invalid_stock': 1}` 무알림. `COLLECT_PLAN.md:265-270` 알림 등급표(즉시/일일 요약/무음)는 정의만 있음.
- "stage 건전성 대시보드 66/66" 은 **서버에 없다**(09-05 1회성 Claude 아티팩트). 재료는 남아 있다: `data/stage/*/MANIFEST.json` 66개 · `baseline.json` · `logs/stage_all_summary_pass1.tsv` · `scripts/equity_gate_all.sh`(빌드 없이 재판정, `logs/equity/gate_all/summary.txt`). stage 쪽 판정 전용 러너는 없다 — MANIFEST 66개 읽는 스크립트 1개면 콜 0·빌드 0 으로 매일 판정 가능.
- 알림 채널: **텔레그램** `~/infra/gpu_alert.sh`(bash 12줄, `.env` 의 `BOT_TOKEN`·`CHAT_ID_LOG`, 30분 쿨다운 패턴) · `.env` 에 **`CHAT_ID_DATA`** 가 데이터 알림용으로 이미 분리 · v3 `_job_notify.py` · n8n 상시 구동(과함). 권고: `gpu_alert.sh` 본떠 `scripts/notify.sh` → `CHAT_ID_DATA`.

## 6. 배포 경로 (md5 대조 실측)
`database/{src,scripts}` vs `~/quant-ledger/{src,scripts}`: 서버 117 / 저장소 88 / 공통 77 · 동일 75 · 상이 2:
- `scripts/run_stage_all.sh` **저장소가 앞섬**(문서층 4표 프리패스 캐시 가드 7줄 `:26-32`). 서버 판은 stg_doc_* 를 못 짓는다.
- `scripts/doc_fixtures_from_sample.py` 서버가 앞섬(주석 차이뿐).
서버에만 33개: **`src/equity_s23/` 29모듈**(S23 조정가), **`src/rebuild_share.py`**, `sync_v3_wise.py`, `export_csv.py`, `probe_krx_timing.py`. 저장소에만 4개(미배포): `check_baseline_lock.py`, `check_field_map.py`, `fetch_equity_local.sh`, `run_mvp_backtest.py`.
`.venv`: Python 3.12.3 · duckdb 1.5.5 · pyarrow 25.0.1 · requests 2.34.2 (pandas·polars 없음).
share 리빌드 크론(`30 0 * * *` UTC, 설계서 "완료")은 **crontab 에 없다** — `share/latest` 08-27 정지.

## 7. 휴장일 처리
- v3 `~/kael-system-v3/data/.kis_holidays.json`(1.6 KB, 09-01 갱신; 매월 1일 09:00 KST 점검, 12/29 연간 갱신) · v3 체인 `holiday_check` 게이트.
- 우리: 캘린더 파일 없음. `backfill_krx.py:41,53,112-116` 이 빈 `OutBlock_1` 을 휴장으로 판정해 `ingest_log(status='holiday')`(실측 `20260817|holiday|7`). `COLLECT_PLAN.md:210` §4-1 0단계 "KIS 캐시 → API → 둘 다 실패면 영업일 가정".
- `daily_wise.sh` 는 **휴장일에도 전부 돈다**(WISE 15,600 req 낭비). v3 daily_all 은 평일+holiday_check.

## 플랜 작성자에게 — 결정이 필요한 쟁점
- **A. 실행 시각**: 전제 KRX T+1 08:00 · v3 체인 20:05→23:48 · daily_wise 06:00→06:05 · v3 DART 07:45/18:15 · v3 토큰 재발급 07:00 · 08:05~15:30 완전 유휴. 후보 ① **08:30 시작(추천)** ② 09:00 ③ 10:00. ✗ 19:00(COLLECT_PLAN 원안 — KRX 미공표) ✗ 06:00~06:30 ✗ 07:00~08:00. 예산: 수집 ~40분 + 스냅샷 ~3분 + stage 23분 + equity 8.5분 ≈ **75분** → 08:30~10:00.
- **B. 락**: `/tmp/quant_ledger_raw.lock` 신설. RSS 실측이 직렬화를 강제(7.16+6.5 > 13). v3 관례 `flock -n`. daily_dart pkill → flock.
- **C. API 키(최대 미결)**: KIS·키움 앱키가 v3 와 물리적으로 하나(COLLECT_PLAN.md:235 "일일 운영에서도 앱키 분리 필요"). (a) 별도 앱키 (b) 시각 분리 (c) 콜 축소. DART kael 키 폴백 제거 권고(`api.py:148-149`). 키움 토큰은 07:00 을 가로지르지 않게.
- **D. 알림**: 텔레그램 `CHAT_ID_DATA` 권장. `.env` 를 quant-ledger 로 분리할지 함께 결정.
- **E. 감시**: stage 판정 전용 러너 신설. 최소 항목: 원장 5종 최신일 / MANIFEST fail 수 / stage 최신 빌드일 / DART key_id별 콜 / 디스크 / daily_wise rc. **원장 정지 감지 최우선** — 실측 최신일 KRX 08-20 · 키움 flow/short/lending 08-20 · 키움 foreign 08-24 · KIS credit 08-18 · KIS short/flow/loan 08-14 · DART 09-01. 20일간 아무도 몰랐다.
- **F. 배포**: scp 수동. 서버 전용 `equity_s23/`·`rebuild_share.py` 를 저장소로 되돌릴지. `run_stage_all.sh` 를 서버로 밀 것. rsync vs git pull(.venv·data·토큰 제외). share 크론 별건 여부.
- **G. 휴장일**: `.kis_holidays.json` 직접 읽기 vs 복사본. 폴백 "영업일 가정".
- **H. 디스크**: GC 없는 곳 셋 — snapshots 37 G(일일 17.7 G 면 2주 내 소진) · `_tmp/doc/` 3.7 G · logs. **스냅샷 GC(keep=N) 를 먼저 정할 것.**
