# 일일 증분 파이프라인 구축 플랜 — 원장 → stage → equity

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2026-08-20 에 멈춘 원장(KRX·키움·KIS·DART)을 오늘까지 메우고, 이후 매 거래일 아침 원장 → stage → equity 가 사람 손 없이 갱신되며, 실패하면 텔레그램으로 사람에게 도달하는 체계를 만든다.

**Architecture:** 체인 두 개(flock 직렬). **06:00 KST 수집 체인** [캘린더 → 키움 마스터·WISE → 키움 시계열 fetch → KIS → DART·문서] 은 KRX 를 뺀 전 소스를 받고(다른 플랫폼은 06:00 에 전날 데이터가 있다는 사용자 추정, 키움은 P0 프로브로 확인), **08:10 KST 빌드 체인** [KRX(T+1 08:00 공표) → 키움 KRX 대조·머지 → 원장 건전성 → stage 전량 → equity 전량 → catalog·contract → 요약] 이 이어진다. 각 단계는 실측 기대치로 고정된 게이트를 통과해야 다음 단계로 간다. 기존 `daily_wise.sh` 는 06:00 체인의 첫 단계가 된다. 증분 러너는 백필 코드를 건드리지 않고 별도 파일로 신설한다(백필 코드 동결). stage·equity 는 증분 경로가 없으므로 **전량 재빌드**를 매일 산다(실측 22.5분 + 8분, 유휴 창 안).

**Tech Stack:** Python 3.12 · sqlite3 · duckdb 1.5.5 · bash/cron(UTC) · flock · 텔레그램 Bot API(curl). 서버 `kael-server:~/quant-ledger`, 저장소 `database/`.

**근거:** 2026-09-09 서버 실측 조사 5건 — `docs/reviews/2026-09-09-daily-findings-{A,B,C,D,E}-*.md`. 이 문서의 숫자는 전부 그 조사에서 왔고, `[A §2-1]` 식으로 절을 가리킨다. 리뷰 2회(1차 blocking 6건·2차 blocking 4건) 반영본.

---

## 상태 (작업이 끝날 때마다 이 블록과 체크박스를 갱신한다)

| 항목 | 값 |
|---|---|
| 최종 갱신 | 2026-09-10 23:30 — **시간표·P4~P6 은 `2026-09-11-daily-incremental-v2.md` 로 이관**(당일 저녁 스코어링, 사용자 결정 V2-1~5). 이 문서의 P0~P3 결과·결정은 유효. 20:00 — **목표 개정 후보(결정 8)**: 사용자 최종 목표 = 당일 20시 전 멀티팩터 스코어링. 저녁 체인(18:30) + 아침 확정 체인의 2단 구조로 재설계 필요, 선행 조사 = 프로브 T 대상 판독 **추가 완료**(`98de5c7`, 추가 콜 0 — 09-11 하루면 키움 당일 행 확정 시각·밤사이 불변 여부가 나온다). 사용자 방향(09-10 20:00): 새 파이프라인 완성 뒤 v3 파이프라인 종료, 원장은 소스별 가장 이른 확정 시각에 수집, stage·equity 는 원장이 다 모인 뒤 1회. 결정 7: **결정 7: 외국인 보유(ka10008)만 08:10 체인으로 이동**(프로브 실측: T-1 행이 07시 전후 정정, 삼성전자 +45,960주) — `kw_daily --tr`, 체인 2개 수정·배포. DART 접수는 ~18시(09-09 공시 525건 중 124건이 16:40 이후 접수 → 06:47 스윕이 전량 커버). P5 에 Task 5.5(검수 후속 5건: KIS 최초 관측판·액면병합 확인·잔고율 라벨·WISE 커버 이력·정정 링크) 등재.  P3 관찰 1/5 통과 + 첫 적재분 검수 완료(`reviews/2026-09-10-intake-audit-summary.md`) + 핫픽스 3건 배포(`52d0f48`) + **사용자 결정 3건 반영**(DECISIONS_PENDING 결정 6: ① 키움 머지 신규 행만 적재 — 구현·배포 ② KIS 재수집 판본은 최초 관측판 — P5 항목 ③ G3 #3·#4 개정). 09-11 실행에서 확인할 것: WISE 무커버 4콜 항등식, 036010·190510·342870 커버 복귀, 키움 merge `changed=0`. PR #96(병합 09-09)·**#102**(09-10 후속, 리뷰 대기) |
| 결정 R1~R10 | **전부 승인**(09-09, R1 은 사용자 수정판) |
| P0 안전장치·정렬 | **작업 완료, 게이트 2건 대기**(09-09) — 0.1~0.9 전부 완료. G0 통과 #1~#9·#11·#12. 남은 것: **#10 키움 프로브 판독(3거래일 → 09-15)** · **R5 후속 키움 앱키 발급(사용자 행동)**. 이 둘은 P3 키움 단계의 전제이지 P1 코드 작성의 전제가 아니라 P1 을 병행 시작 |
| P1 원장 증분 코드 | **완료**(09-09) — 1.1~1.8 전부, 신규 테스트 133건·ruff·pyright 통과, G1 서버 드라이런 통과. 전체 회귀 1,051 pass(기존 실패 1건은 main 과 동일) |
| P2 갭 메우기 | **완료**(09-09 19:11) — KRX 13일·키움 13일(10,508콜, KRX 대조 100%)·KIS(신규 33,513행)·DART 15일 전부 게이트 통과·유니버스 3,483. G2 6항 통과(#6 은 신규 상장 누락 발견 → 규칙 확장, 09-10 체인부터 반영) |
| P3 원장 크론·관찰 | **가동**(09-09 18:10 등록, 18:50 키움 단계 포함으로 변경 — `QL_SKIP_KW` 해제) — **관찰 1/5 통과**(09-10: 두 체인 rc 0, 건전성 19/20, crit 0). 관찰 중 확인할 것: 06:00 체인 76분(G3 #3 45분 초과, 토큰 충돌은 없음 → 기준 재정의 필요) · G3 #4 키움 10,500 은 유니버스 2,602 기준이라 갱신 필요. **09-10 검수** → `reviews/2026-09-10-intake-audit-summary.md` (소스별 A~D 4건). 핫픽스 3건 배포 `52d0f48`(09-10 15:00) — 관찰 2일차(09-11)부터 새 코드로 돈다 |
| P4 stage 일일 전량 | 미착수 |
| P5 equity 일일·소비자 | 미착수 |
| P6 운영 정착 | 미착수 |
| 원장 최신일(실측 09-09) | KRX 08-20 · 키움 시계열 08-20(ka10008 08-24 오염) · KIS credit 08-18 · DART 09-01 · WISE 09-09 |

---

## 0. 현황 (실측 2026-09-09)

### 0-1. 원장이 어디까지 차 있나

| 원장 | 최신 | 갭 | 메우는 데 필요한 콜 | 상태 |
|---|---|---|---:|---|
| KRX 7 엔드포인트 | 20260820 | 13거래일 | 91 (37초) | 정지. 코드는 `--to` 만 주면 됨 [A §1-1] |
| 키움 ka10014·20068·10060 | 20260820 | 13거래일 | 유니버스(2,602 ∪ 2,563)×3 | 정지. **증분 모드 없음**(DEFECT-A-02) [A §1-2] |
| 키움 ka10008 | 20260824 **(장중 수집 오염)** | 11거래일 + 08-24 재수집 | 유니버스 ×1 | 08-24 행 99%가 전일 복사본 [A §3-2] |
| 키움 ka10099 마스터 | 20260909 | 0 | 2/일 | **정상 가동** (daily_wise 06:00) |
| KIS credit (3,175종목) | deal_date 20260818 (T+2 확정) | 14거래일 | 3,175 (20분) | 정지. 재수집 시 **행 중복 증식**(DEFECT-A-03) [A §1-4] |
| KIS flow·short·loan·master | 20260814 | — | — | 폐지 652종목 전용 축. **일일 대상 아님** [A §2-3] |
| DART 공시목록 | rcept_dt 20260901 | 5영업일 ≈ 3,000~4,000건 | 410~890 | 정지. 스윕 → 상세 자동 연결 **없음** [B §1-4] |
| DART 재무·부속·DS005·지분 | 접수일 08-28~09-01 | **08-26 이후** 접수분의 상세 | 이벤트 corp 재호출 | corp 축 `ok` 영구 종결이라 신규 사건 **영영 미수집** [B §1-4, §4-4] |
| 문서 ZIP | 20260831001536 | 09-01 이후 | 평시 3~37/일 | 코드는 차집합 증분 지원. 파싱 증분은 없음 [B §2] |
| WISE | fetched_date 20260909 | 0 | 15,600 req/일 | **정상 가동**, 9일 무결손 [B §5] |

### 0-2. 세 층의 갱신 능력

| 층 | 증분 경로 | 전량 비용(실측) | 일일 갱신에 쓸 방식 |
|---|---|---|---|
| stage 66테이블 | **없음** — 항상 테이블 전체 재생성 [C §2] | 스냅샷 5 DB 3.8분 + 빌드 22.5분(`stg_fin` 666초가 절반) | **매일 전량**. DART 수집이 재개되면 `stg_rcept_dt_map`·`stg_fin` 이 거의 매일 대상이라 "영향 테이블만" 도 21.5분(리뷰 B1) — 전량이 단순하고 스냅샷 혼재도 없다 |
| equity 28표 | **없음** — 28표 중 13표는 원리적으로 증분 불가 [D §6] | 470초 + catalog 90초 + contract 9초 | 매일 전량. 단 **날짜 상수 5개가 2026-08-20 에 고정돼 EG17 이 첫 표에서 FAIL** [D §3-4] |
| 소비자 | 워크벤치는 `fetch_equity_local.sh` rsync, 커널은 parquet 5표 직접 [D §5] | — | 서버 catalog 갱신을 재빌드와 한 단위로 묶는다(지금 10표 stale) |

### 0-3. 운영 전제 (실측)

- KRX 는 **T+1 08:00 KST 공표**(07:55 0행 → 08:00 944행). 당일치는 23:00 까지 0행 [E §2-0]. 저녁 배치로는 KRX 를 못 받는다.
- 서버 4코어·15 GiB·여유 271 GB. stage `stg_fin` RSS 7.16 GB, equity 최대 6.5 GB → **stage 와 equity 동시 실행 불가**(13.3 GB > 13 GB available) [E §1-4].
- **08:05~15:30 KST 는 CPU·디스크·API 가 비어 있다.** v3 `daily_all` 은 20:05→22:33(최장 23:48) [E §2-1].
- KIS·키움 앱키는 v3 와 물리적으로 하나. DART 는 우리 키 2개(80,000/일) + v3 키(폴백). 08-28 에 v3 키를 실제로 소진시켰다 — E §3-2 는 35,588콜, B §3-2 는 39,002콜(UTC/KST 집계 차, 어느 쪽이든 한도 40,000 의 89~98%) [E §3].
- quant-ledger 크론은 `daily_wise.sh` 하나. flock 은 `run_equity.sh` 에만 있고 수집·stage 러너엔 없다. 알림·로그 로테이션 **없음**. 원장 정지를 20일간 아무도 몰랐다 [E §4·§5].
- 휴장일 정본이 quant-ledger 에 없다. v3 `~/kael-system-v3/data/.kis_holidays.json` 이 매월 갱신된다 [A §3-4, E §7].

### 0-4. 이 플랜이 고치는 결함

| ID | 내용 | 위치 | 고치는 페이즈 |
|---|---|---|---|
| DEFECT-A-01 | 08:00 전 실행 시 거래일이 `holiday` 로 **영구 확정** | `backfill_krx.py:51-53, 83-84` | P1 |
| DEFECT-A-02 | 키움 `--to` 없음·`20260820` 하드코딩 3곳·`todo()` 가 증분 시 전 종목 스킵 | `backfill_kw.py:223-230, 278-292` | P1 (증분 러너 신설) |
| DEFECT-A-03 | KIS `row_hash` 에 `req_d1/d2` 포함 → 창이 바뀌면 같은 사실이 새 행 (이미 12.1% 중복) | `backfill_dart.py:454-476` | P1 (KIS 전용 저장) |
| DEFECT-B01 | 열린 분기 스윕 창이 구조적으로 영구 `mismatch` | `sweep_disclosure.py:270-284` | P1 (완료 판정을 `rcept_dt` 존재로) |
| DEFECT-B02 | 정기보고서 부속 6종이 11011 만 수집 | `daily_dart.sh:35-36` | P1 (앞으로 분) · 과거 반기·분기 판본은 §11 후속 |
| DEFECT-B03 | `daily_dart.sh` 가 2026 사업연도를 안 쏨 | `daily_dart.sh:32` | P1 (스크립트 폐기) |
| DEFECT-B04 | `daily_dart.sh` 에 stage 5 없음 | `daily_dart.sh:37-38` | P1 |
| DEFECT-B05 | `corps.txt` 가 동결된 krx.db 와의 교집합이라 신규 상장사 진입 불가 | `dart_universe.py:18-30` | P1 |
| 키움 유니버스 | `tickers.txt`(08-24 고정) — 09-04 상장 스카이랩스 영구 누락, 폐지 4종목 헛콜 | `data/jsonl/tickers.txt` | P1 |
| stage GC | 스냅샷 보관 정책 없음(37 GB 잔존, 일일 풀 스냅샷이면 2주 내 디스크 소진) | `snapshot.py` | P0(1회 정리) · P4(빌드 내장) |
| stage 락 | 설계된 `/tmp/quant_ledger_raw.lock` 이 코드에 없음 | `run_stage*.sh`, `daily_wise.sh` | P0 |
| equity 상수 | `backfill_end` 등 날짜 상수 5개 고정 → 새 거래일에 EG17 FAIL | `baseline_locked.json`, `rules_s02.py:88-92` | P5 |
| equity 정렬 | baseline 락 3건 불일치·catalog 10표 stale·rules_version 혼재 | 서버 | P0 |
| DART 키 | v3 프로덕션 키가 폴백 순서에 있음 | `api.py:148-149` | P0 |
| 알림 | 실패가 사람에게 도달하는 경로 없음 | — | P0 |

---

## 1. 결정 사항 — 승인 필요

플랜은 아래 권고를 전제로 썼다. 바꾸면 해당 페이즈의 작업이 바뀐다.

| # | 쟁점 | **권고** | 대안 | 근거 |
|---|---|---|---|---|
| R1 | 실행 시각 | **사용자 수정(09-09)**: **06:00 수집 체인**(키움 마스터·WISE → 키움 시계열 4 TR → KIS credit → DART·문서; KRX 만 빼고 전부) + **08:10 빌드 체인**(KRX → 키움 KRX 대조·머지 → 원장 건전성 → stage → equity). 근거: KRX 만 T+1 08:00 공표이고 나머지 플랫폼은 06:00 이면 전날 데이터가 있을 것으로 **추정** — WISE·DART(접수 마감 18:00)·키움 마스터는 실측 확정, **키움 시계열 4 TR 은 미측정이라 P0 프로브(Task 0.7)가 06:00 기준으로 확인**, KIS credit 은 T+2 확정이라 06:00 에 T-3 까지는 확실하고 T-2 는 창 겹침으로 다음 날 자동 보충. 06:00 체인은 v3 토큰 재발급(07:00) 전에 끝나야 한다 | 08:30 단일 체인(초안) · 19:00 당일 배치(KRX 미공표라 불가) | [E §2 A·§3-3], [A C-1·§2-3] |
| R2 | stage 갱신 | **매일 전량 재빌드**(스냅샷 5 DB 3.8분 + 22.5분, 스냅샷 keep=3). "영향 테이블만" 은 DART 수집 재개 후 21.5분으로 전량과 같아져 값이 없다(리뷰 B1). 문서층 프리패스 4표는 제외, `stg_doc_index` 는 포함 | 영향 테이블만(변경 판정 코드 + 스냅샷 혼재) · 파티션 증분 빌더(게이트 재설계) | [C D1·D3·§3-4] |
| R3 | equity 갱신 | **매일 전량 재빌드**(470초) + catalog + contract 를 한 단위로. 날짜 상수 5개를 **파생값**으로 바꾸는 규칙 판본 e1.15.0. EG5c 표본 날짜는 과거 고정, 최신 구간은 EG14 최소판으로 | 상수를 매일 사람이 커밋(자동화 불가) · baseline 자동갱신 구획 | [D ①②③] |
| R4 | DART | `daily_dart.sh` **폐기**, `dart_daily.py` 신설: 열린 분기 스윕 전량(410~890콜) + 당일 공시에 등장한 corp 만 상세 재호출(정기보고서→fin+부속 6종 × 해당 reprt, 주요사항→DS005 15종 전부 300~510콜, 지분→elestock·majorstock 100~300콜, 정정→같은 유닛) + 문서 ZIP. **일 ≈ 830~2,000콜, 반기 마감일 ≈ 21,000**(용량 80,000). 재호출은 `ingest_log` 행 삭제 방식(백필 코드 무수정). **v3 키 폴백 제거** | report_nm→엔드포인트 매핑표(유지 부담) · `--force-corps` 플래그 | [B §3-5·§8-1·8-2·8-5] |
| R5 | 키움 | **사용자 결정(09-09 18:50): 공유 앱키로 지금부터 콜한다 — 앱키 분리는 전제가 아니라 후속(선택)으로 강등, `QL_SKIP_KW` 해제.** `kw_daily.py` 신설(백필 `backfill_kw.py` 동결). 유니버스 = `ka10099` 최신 스냅샷 `upSizeName<>''`(WISE 와 동일 규칙, 09-09 실측 2,563) **∪ 직전 유니버스**(첫 실행은 `tickers.txt` 2,602 로 시드 — 마스터에는 있으나 `upSizeName` 이 빈 ≈40종목의 갭이 사라지지 않게, 리뷰 2차 #2). 사라진 종목은 **5거래일 유예 후, 그 종목의 `ka10008.max(dt)` 가 마지막 마스터 등장일 이상일 때만** 제외. 응답 중 **`dt <= D` 구간만** 머지(개장 전 당일 행 차단), 오염 게이트는 `dt=D` 에만. 앱키 실측(P0 Task 0.8: v3 12,701 + 10,410 = 23,113 > 검증 한도 20,000)은 기록으로 남기되 **사용자가 초과를 수용(09-09)** — 실제 상한은 미측정, 초과 시 오류가 나면 그때 분리 | `backfill_kw.py` 에 `--to` 추가(샤드 PK 증식) · v3 investor_flows 1페이지화(−5,068) | [A C-2·C-4·C-7], reviews/2026-09-09-p0-task08 |
| R6 | KIS | **credit 만** 일일(창 `D-40일 ~ 오늘(T)` — `d2=T` 여야 `deal_date ≤ T-2 = D-1` 까지 온다, 유니버스 크기만큼 콜). KIS 전용 저장 함수로 `(req_ticker, deal_date, payload)` 동일 행 삽입 차단(`req_*` 보존 원칙 유지). flow·short·loan·master 4테이블은 폐지축이라 제외. 창 상한은 `corp_ticker.last_dd` 가 아니라 명시 인자 | KIS 보류·주 1회 · `row_hash` 정의 변경 | [A C-3] |
| R7 | 휴장일 | v3 `.kis_holidays.json` 을 **복사본**으로 읽고(`data/calendar/kis_holidays.json`, 매일 rsync), 없거나 검증 실패면 **영업일 가정**. KRX 빈 응답은 휴장 확정 근거로 쓰지 않는다 | `holiday.py` 이식(연 40콜) | [A C-5], [E G] |
| R8 | 알림 | 텔레그램 **`CHAT_ID_LOG`**(사용자 지정 09-09, 초안은 CHAT_ID_DATA), `scripts/notify.sh`(v3 `gpu_alert.sh` 12줄 복제, 쿨다운 포함). 등급 = `COLLECT_PLAN.md §4-4` 3등급 | n8n · 없음 | [E D] |
| R9 | GC | 스냅샷 keep=3(**빌드 내장**) + 현행 MANIFEST 가 가리키는 세트 보호 — 문서층 동결 4표가 `snap_20260902T230100Z`(dart 6.6 GB)를 영구 고정하므로 상한은 ≈ 60 GB(17.7×3 + 6.6). 그 스냅샷의 프리패스 캐시(`_tmp/doc` 3.7 GB)는 지운다(재파싱은 3~4.5h 로 재현 가능, 의도) · equity `_pinned` keep=3 · `_tmp/doc` 빌드 후 삭제 · `_failed` 30일 · 로그 주간 gzip. `stage.baseline` 자동 실행 **금지**(G9 저하를 스스로 승인하는 경로) | — | [C D2·D5], [D ②], [E H] |
| R10 | 범위 밖 | 문서층 **파싱** 증분(프리패스 3~4.5h, `t_*_ms` 비결정 선행 수정 필요) · `share/` 리빌드 크론 · KIS 폐지축 4테이블 · 키움 우선주·ETF 확장 · DEFECT-B02 과거분 · `snapshot_id` 일변경으로 인한 `target_tape` 거부·`_asof` 3일 보관 | — | [C D6], [B §8-7], [E §6-3], [D ②] |

---

## 2. 페이즈 개요

| 페이즈 | 목표 | 주요 산출 | 통과 게이트 | 예상 |
|---|---|---|---|---|
| **P0 안전장치·정렬** | 자동화가 가능하고 실패가 보이는 상태 | flock·notify·캘린더·GC 1회·키 폴백 제거·equity 손정렬·키움 시각 프로브 | G0: 12항 | 2~3일 (프로브 3일 병행) |
| **P1 원장 증분 코드** | 소스별 증분 러너 + 건전성 판정 | `kw_daily.py`·`kis_daily.py`·`dart_daily.py`·KRX 가드·`universe.py`·`calendar.py`·`ledger_health.py`·`daily_ledger.sh` | G1: 테스트·린트·서버 드라이런 | 3~4일 |
| **P2 갭 메우기** | 원장 5개를 T-1 까지 | 1회 수동 실행 + 오염 재수집 | G2: 소스별 완료 SQL 전건 | 1일 |
| **P3 원장 크론 가동·관찰** | 5거래일 무개입 성공 | crontab·일일 요약 알림 | G3: 5/5 거래일 통과 | 1주 |
| **P4 stage 일일 전량 재빌드** | 원장 변경 → stage 자동 반영 | `stage_daily.sh`·스냅샷 GC 내장·`stage/health.py` | G4: C1~C5 5거래일 | 1주 |
| **P5 equity 일일 갱신·소비자** | equity 28표·catalog·contract 자동, 소비자가 새 데이터를 봄 | 규칙 e1.15.0·`_pinned` GC·`equity_daily.sh`·전달 규약 | G5: D §7 12항 5거래일 | 1~2주 |
| **P6 운영 정착** | 사람이 매일 5분만 보면 되는 상태 | 통합 건전성 리포트·로테이션·주간 점검·문서 갱신 | G6: 2주 무사고 | 2주 |

각 페이즈는 **자기 게이트를 통과한 뒤에만** 다음 페이즈를 시작한다. 게이트는 전부 서버에서 실행 가능한 명령·SQL 이고 기대치는 실측값이다.

---

## 3. Phase 0 — 안전장치·정렬

목표: 코드 한 줄 없이도 "지금 서버가 어떤 상태인지" 매일 알 수 있고, 자동화가 사람을 놀라게 하지 않는 상태.

### Task 0.1: 알림 채널 `scripts/notify.sh`

**Files:** Create `database/scripts/notify.sh` · Modify `database/scripts/daily_wise.sh`

- [x] **Step 1**: v3 `~/infra/gpu_alert.sh` 를 본떠 작성. `.env` 는 `QL_ENV`(없으면 `~/kael-system-v3/.env`)에서 `BOT_TOKEN`·`CHAT_ID_LOG` 만 export(사용자 지정 09-09). 인자 `<level> <title> <body>`; `level` 은 `crit|warn|info`. 같은 `title` 은 30분 쿨다운(`/tmp/ql_notify_<hash>`).
- [x] **Step 2**: 서버에서 `scripts/notify.sh info "notify test" "P0"` → 텔레그램 수신 확인.
- [x] **Step 3**: `daily_wise.sh` 끝에 종료코드 검사 추가 — `master_daily` 또는 `backfill_wise` rc≠0, 또는 로그에 `⚠⚠` 가 있으면 `notify.sh crit`. 정상이면 `info` 한 줄(요청 수·n_bad).
- [x] **Step 4**: 커밋 `ops(database): add telegram notify.sh and wire daily_wise exit check`

### Task 0.2: flock 도입

**Files:** Modify `database/scripts/daily_wise.sh`, `run_stage.sh`, `run_stage_all.sh`, `run_equity.sh`

- [x] **Step 1**: 설계(`STAGE_DESIGN.md:72`)대로 원장 락 `/tmp/quant_ledger_raw.lock` 을 정의한다. `daily_wise.sh` 본문을 `flock -n /tmp/quant_ledger_raw.lock` 아래로 옮기고 획득 실패 시 `notify.sh warn` 후 exit 3.
- [x] **Step 2**: `run_stage.sh`·`run_stage_all.sh`·`run_equity.sh` 는 `/tmp/quant_ledger_build.lock` 하나로 통일(stage·equity 공용 — RSS 합이 available 을 넘으므로 **빌드는 하나만**). **락은 최외곽 스크립트만 잡는다**: 자식이 같은 파일을 다시 열어 `flock -n` 하면 별개 open file description 이라 부모와 충돌해 exit 3 이 된다(리뷰 2차 #1, `run_equity.sh:10-11`). 규약 = 락을 잡은 스크립트가 `QL_RAW_LOCK_HELD=1` / `QL_BUILD_LOCK_HELD=1` 을 export 하고, 자식 러너는 그 변수가 있으면 획득을 **생략**한다. `run_equity.sh:10-11` 도 이 규약으로 고친다.
- [x] **Step 3**: 서버에서 두 셸을 동시에 띄워 두 번째가 exit 3 으로 즉시 빠지는지, 그리고 부모→자식(`equity_rebuild_all.sh` → `run_equity.sh`)은 통과하는지 둘 다 확인.
- [x] **Step 4**: 커밋

### Task 0.3: 휴장일 캘린더 복사본

**Files:** Create `database/scripts/sync_calendar.sh`

- [x] **Step 1**: `rsync ~/kael-system-v3/data/.kis_holidays.json ~/quant-ledger/data/calendar/kis_holidays.json` + 검증(v3 `holiday.py` 규칙과 동일: `len(holidays) >= 100`, 전건 해당 연도, 토·일 ≥ 90). 실패 시 이전 복사본 유지 + `notify warn`.
- [x] **Step 2**: 서버 실행 → `data/calendar/kis_holidays.json` 에 2026-09-24·25 가 있는지 확인.
- [x] **Step 3**: 커밋

### Task 0.4: DART v3 키 폴백 제거

**Files:** Modify `database/src/api.py:148-149` · Test `database/tests/test_api_dart_keys.py`

- [x] **Step 1**: 실패 테스트 — `_K={"DART_API_KEY":"x","DART_API_KEY_2":"y"}` 일 때 `dart_keys()` 가 `[("k2","y")]` 만 돌려줘야 한다(현재는 `("kael","x")` 포함).
- [x] **Step 2**: `api.py:148-149` 두 줄 삭제. `backfill_dart.py` 의 "1순위가 kael 이면 중단" 가드는 그대로 둔다(무해).
- [x] **Step 3**: 테스트 통과 · `ruff` · 커밋 `fix(database): never fall back to the v3 production DART key`

### Task 0.5: GC 1회 손정리 + `scripts/gc.sh`

**Files:** Create `database/scripts/gc.sh`

- [x] **Step 1**: `gc.sh`(`--dry-run` 기본, `--apply`): `data/stage/_tmp/doc/` 삭제 · `data/stage/_failed/`·`data/equity/_failed/` 30일 초과 삭제 · `logs/*.log` 7일 초과 gzip. **스냅샷 GC 는 여기 넣지 않는다** — Task 4.2 에서 빌드에 내장(한 곳에만).
- [x] **Step 2**: 스냅샷은 이번 한 번만 손으로: mtime 최신 3세트(`154207Z` 풀·`230100Z` dart·`015712Z` wise = 23.7 GB, 현행 MANIFEST 3종이 가리키는 것과 정확히 일치)만 남기고 4세트 삭제.
- [x] **Step 3**: 서버 `gc.sh --dry-run` 검토 → `--apply`. 기대: `data/snapshots` 37 → ≤ 24 GB, `_tmp/doc` 3.7 GB 회수.
- [x] **Step 4**: 커밋

### Task 0.6: equity 손정렬

**Files:** Modify `database/src/equity/baseline_locked.json`

- [x] **Step 1**: 서버 `data/equity/baseline.json` 의 상수 3건(`consensus_daily.v3_wise_match_min 0.93`, `v3_wise_value_tol_rel 0.01`, `corp_event.bonus_ratio_window_sessions 25`)이 09-07 결정인지 `EQUITY_HANDOFF.md` §8 에서 확인. 맞으면 락 파일에 반영, 아니면 서버를 락으로 되돌린다.
- [x] **Step 2**: `scripts/check_baseline_lock.py` rc=0 확인. (09-09: 서버가 09-07 확정값이었고 락이 뒤처진 것 — 락을 서버 상수로 갱신해 설치, 바이트 동일 확인)
- [x] **Step 3**: 서버에서 `equity_rebuild_all.sh`(470초) → `python -m equity catalog` → `contract`. 목적: `rules_version` 단일화 + catalog stale 해소. `_catalog_meta.snapshot_id` 가 현재 MANIFEST 지문과 일치해야 한다. **(09-09 실행: pass1 517s · pass2 499s, content_hash 28/28 동일, rules_version 28표 e1.14.0, 게이트 fail 0, catalog `2c38be1d58fb03be` EG5c n_diff 0, contract EGC-01~05·10 pass. `contract` 는 `--engine-src ~/quant-ledger/_engine` 를 줘야 한다 — 기본값 `~/backend/src` 는 서버에 없다 → Task 5.3 `equity_daily.sh` 에 반영)**
- [x] **Step 4**: 커밋(락 파일 `9c8a24e`) + `EQUITY_HANDOFF.md §8-5` 에 정렬 기록

### Task 0.7: 키움 확정 시각 프로브 (3거래일 이상)

**Files:** Create `database/src/probe_kw_timing.py` · 회수 `database/src/probe_krx_timing.py`(서버에만 있음)

- [x] **Step 1**: `probe_krx_timing.py` 방식으로 ka10008·ka10060·ka10014·ka20068 각 1종목(005930) 을 매시 1콜(일 96콜) 호출해 `data/evidence/kw_timing.db` 에 `(ts_kst, api, target_dt, n_rows, poss_stkcnt_changed)` 기록. **06:00 시점에 전일 `dt` 행이 있고 그 값이 08:00 KRX 공표 이후에도 바뀌지 않는지**가 관측 대상(R1 의 "06:00 추정" 을 확인하는 프로브). KIS credit 도 1종목 매시 1콜로 `deal_date=T-2` 행이 06:00 에 오는지 함께 잰다.
- [x] **Step 2**: 임시 크론 `5 * * * *` 등록(09-09 15:05 KST 부터, `src/probe_kw_timing.py`). 첫 관측(09-09 15:15 KST, 장중): 키움 4 TR 모두 T-1(0908) 행 존재, 당일(0909) 장중 행도 이미 응답에 섞임 → `dt<=D` 머지 규칙의 근거. KIS credit 은 `d2=오늘` 로 조회해도 max deal_date 가 0904(T-3) — 06:00 에 T-2 가 오는지가 판독 포인트. **3거래일** 뒤 판독: "T-1 데이터가 확정되는 최초 시각". 06:00 이전이면 R1 그대로, 늦으면 키움 단계만 그 시각으로 미룬다(`--not-before`). 판독 전에는 키움 증분을 돌리지 않는다.
- [ ] **Step 3**: 판독 결과를 `docs/reviews/2026-09-09-daily-findings-A-*.md` 말미에 추가. 프로브 크론 제거.

### Task 0.8: 키움·KIS 앱키 실사용량 실측 (콜 0)

- [x] **Step 1**: v3 로그(`~/logs/kael-v3/pipeline.log`, `daily_prices`·`investor_flows` 단계)로 v3 의 키움·KIS 일일 콜 수를 최근 5거래일 집계. (09-09 실측 `reviews/2026-09-09-p0-task08-appkey-usage.md`: v3 는 콜 로그를 안 남겨 종목당 콜 수 × 유니버스로 산출, `pipeline_runs` 소요 × 레이트리밋으로 교차검증 오차 0.04% — **키움 평균 12,701콜/일**(daily_prices 2콜 + investor_flows 3콜 × 2,534종목, 20:05~20:47 집중) · **KIS 11콜/일**)
- [x] **Step 2**: 키움 `v3 12,701 + 우리 10,410 = 23,113 > 20,000`(+15.6%) → **별도 앱키 발급이 R5 의 전제**. 시각은 이미 안 겹치지만 문제는 총량이다. 부수 실측: 앱키 1개에 활성 토큰 1개라 우리 06:00 발급과 v3 07:00 발급이 서로를 무효화해 **8005 가 매일 발생**(양쪽 1회 재시도로 조용히 복구 중) — 앱키를 나누면 이것도 사라진다. 분리가 늦어질 때의 대안: v3 `investor_flows` 가 3페이지를 긁고 5행만 쓰므로 1페이지로 줄이면 5,068콜 절감 → 18,043(v3 저장소 수정, 합의 필요). KIS 는 11 + 2,600 ≪ 204,235 라 무관.
- [x] **Step 3**: 결과를 이 문서 R5 옆에 기록. → **사용자 행동 필요: 키움 앱키 추가 발급**(DECISIONS_PENDING 결정 5 R5 후속). 발급 전에는 P3 의 키움 단계를 켜지 않는다.

### Task 0.9: 서버 배포 정렬

- [x] **Step 1**: 처분 판정(09-09, `reviews` 대신 조사 요약을 여기 기록): `src/equity_s23/` 는 e1.7.0 구본 사본(현행 `src/equity` 의 부분집합, 참조 0건) → **서버 삭제**. `rebuild_share.py` 는 `backend/ops/` 와 md5 동일 → 저장소 정본은 그쪽, 서버엔 `deploy.sh` 가 별도 라인으로 민다. `probe_krx_timing.py` 는 한 번도 안 돌았고(산출 DB 없음) 새 `probe_kw_timing.py` 가 대체 → 회수하지 않고 삭제. `export_csv.py`·`api.py.bak` 삭제. **`sync_v3_wise.py` 는 유지** — `rules_s17.py:458-467` 이 v3 미러 재개를 사람 승인 옵션으로 명시(플랜 초안의 '서버에서도 지운다' 는 철회).
- [x] **Step 2**: 배포 명령을 `scripts/deploy.sh` 로 고정(dry-run 기본, `--apply`; 토큰 캐시·`sync_v3_wise.py` exclude): `rsync -avz --delete --exclude='.venv' --exclude='__pycache__' --exclude='.k*_token.json' database/src/ kael-server:~/quant-ledger/src/` + `scripts/`. `--delete` 는 `--dry-run` 검토 후.
- [x] **Step 3**: 커밋 (09-09 `--apply` 실행, 재실행 dry-run 전송·삭제 0 = G0#12)

### 게이트 G0 — 전부 통과해야 P1 시작

| # | 검증 | 명령 | 기대 |
|---|---|---|---|
| 1 | 알림 도달 | `scripts/notify.sh info test x` | 텔레그램 수신, 30분 내 재전송 억제 |
| 2 | daily_wise 실패 감지 | 로그에 `⚠⚠` 줄을 주입한 픽스처 로그로 종료 검사 블록 실행 | `crit` 수신 |
| 3 | 락 | `daily_wise.sh` 이중 실행 | 두 번째 exit 3 + `warn` |
| 4 | 캘린더 | `data/calendar/kis_holidays.json` | 2026-09-24·25 포함, 검증 통과 |
| 5 | 키 | `pytest tests/test_api_dart_keys.py` | pass (서버 `kael` 0건 판정은 DART 콜이 다시 나가는 G2 에서) |
| 6 | GC | `du -sh data/snapshots` | ≤ 24 GB, `_tmp/doc` 0 |
| 7 | equity 락 | `check_baseline_lock.py` | rc 0 |
| 8 | equity catalog | `_catalog_meta.snapshot_id` == MANIFEST 지문 | 일치. `read_catalog().usable == True` |
| 9 | rules_version | `summary.tsv` 7열 | 28표 전부 동일 |
| 10 | 키움 프로브 | `kw_timing.db` | ≥ 3거래일, 확정 시각 판독문 작성 |
| 11 | 앱키 실측 | 문서 R5 옆 기록 | v3 일일 콜 수 + 결정(분리/시각분리) |
| 12 | 배포 | `deploy.sh --dry-run` | 서버 ↔ 저장소 `src/`·`scripts/` diff 0 |

---

## 4. Phase 1 — 원장 증분 코드

원칙: **백필 코드는 동결**(`backfill_kw.py`·`backfill_kis.py`·`backfill_dart.py` 무수정, 예외 = KRX 가드 1건). 증분은 새 파일. 모든 러너는 `--date D`(기본 = 캘린더상 직전 거래일)·`--dry-run`·`--limit N` 을 받고, 종료코드로 성패를 알린다(0 성공 / 2 게이트 실패 / 3 락·시각 제약). **`--dry-run` 의 정의는 전 러너 공통: API 콜은 실제로 하되(`--limit` 만큼) 원장·`daily_run.db` 에 쓰지 않고 계획·게이트 결과만 출력.** 토큰 8005(키움)·만료(KIS)를 만나면 **조용히 건너뛰지 않고** 1회 재발급·재시도, 그래도 실패면 rc 2 + `crit`(`master_daily.py:39-42` 의 조용한 스킵을 답습하지 않는다). 테스트는 `tests/test_daily_*.py`, 픽스처는 소형 sqlite.

### Task 1.1: 공용 모듈 `src/daily/calendar.py`·`universe.py`·`runlog.py`

**Files:** Create `database/src/daily/__init__.py`, `calendar.py`, `universe.py`, `runlog.py` · Test `tests/test_daily_calendar.py`, `test_daily_universe.py`

- [x] **Step 1** (calendar): `is_trading_day(d)`, `prev_trading_day(d, n=1)` — `data/calendar/kis_holidays.json` + 주말. 파일 없거나 검증 실패 → 경고 로그 + **영업일 가정**(`COLLECT_PLAN §4-1` 0단계). 테스트: 2026-09-24(추석) False, 2026-09-23 True, 파일 없을 때 평일 True.
- [x] **Step 2** (universe): `kiwoom_common(con_kw, snap_date=None) -> list[str]` = `backfill_wise.py:182-201` 의 규칙(`upSizeName<>''`)을 옮겨 오고 `backfill_wise.py` 는 이 함수를 import. `with_grace(prev, today, con_kw, days=5)`: 요청 유니버스 = `today ∪ prev(유예 중)`. 오늘 스냅샷에서 사라진 종목을 5거래일 동안 유지하되, **제외는 그 종목의 `ka10008.max(dt)` 가 마지막 마스터 등장일 이상일 때만**(거래정지 종목은 키움이 `rc=0`+0행을 주므로 시간 만료만으로 빼면 마지막 거래일이 영구 누락). **`prev` 가 없는 첫 실행은 `data/jsonl/tickers.txt`(2,602)로 시드**하고, `tickers.txt − kiwoom_common`(≈40종목, 마스터에 있으나 `upSizeName` 공백)을 로그로 남긴다 — 이들을 계속 수집할지는 P6 에서 결정하고, 그 전엔 유예 규칙으로 자연 처리. 상태는 `data/daily/universe_kw.json`. 테스트: 386380(09-04 상장)이 `kiwoom_common` 에 드는지 서버 실측으로 확인해 픽스처에 반영(안 들면 필터 규칙을 재검토).
- [x] **Step 3** (runlog): `data/raw/daily_run.db` 테이블 `run(run_id, date, source, started, ended, n_calls, n_rows, status, detail)`. 모든 러너가 시작·종료를 기록. 건전성 판정과 알림이 이 표를 읽는다.
- [x] **Step 4**: 테스트 통과 · `ruff`·`pyright` · 커밋

### Task 1.2: KRX — 휴장 오확정 차단 + 재수집 (DEFECT-A-01)

**Files:** Modify `database/src/backfill_krx.py:51-53, 83-84, 109-117` · Test `tests/test_daily_krx_guard.py`

- [x] **Step 1** 실패 테스트: 캘린더가 거래일이라 하는 날짜에 빈 응답 → `ingest_log.status='pending'`(휴장 아님), `done` 집합에 안 들어감. `--refetch D1,D2` 는 그 날짜의 `ingest_log` 7행을 지우고 다시 받는다.
- [x] **Step 2** 구현: `--calendar` 옵션(기본 `data/calendar/kis_holidays.json`). 빈 응답 처리 = 캘린더 휴장이면 `holiday`, 거래일이면 `pending`. `done` 은 `status IN ('ok','holiday')` 유지(pending 은 재시도). `--refetch` 추가.
- [x] **Step 3**: `daily_build.sh`(08:10) 의 KRX 단계는 `--from <D> --to <D>` 로 호출하되 **최근 10거래일 중 `pending` 인 날짜를 함께 포함**(어제 못 받은 날은 오늘 자동 재시도). 당일 `pending` 이면 10분 간격 최대 6회 재시도(08:10→09:10) 후 `crit`.
- [x] **Step 4**: 테스트 통과 · 커밋 `fix(database): KRX never confirms a trading day as holiday before publication`

### Task 1.3: 키움 증분 러너 `src/daily/kw_daily.py` (DEFECT-A-02, 오염 가드)

**Files:** Create `database/src/daily/kw_daily.py` · Test `tests/test_daily_kw.py`

- [x] **Step 1** 실패 테스트(소형 kiwoom.db 픽스처): (a) 4 TR × 유니버스 종목당 **1콜**, 응답(캡 50~372행 = 최근 수십 거래일)을 임시 테이블 `_kw_incoming_<tr>` 에 적재 (b) 오염 게이트 — **`dt=D` 행만** 대상으로 `ka10008.poss_stkcnt` 가 D-1 과 동일한 비율 > 30% 면 **머지하지 않고** rc 2 (c) 크로스소스 — `dt=D` 에서 `ka10008.close_pric`(abs) 와 KRX `TDD_CLSPRC` 100% 일치해야 머지 (d) 머지는 응답 중 **`dt <= D` 구간만** `INSERT OR REPLACE`(PK `(ticker, dt)` — 과거 정정은 자연 반영, `dt > D` 인 당일 개장 전 행은 버린다: `ka10008` 은 날짜 인자가 없어 호출 시점 최신 50영업일이 온다 [A §1-2]), `ingest_shard` 무접촉, `daily_run.db` 기록. 즉 갭이 며칠이든 **1회 실행 = 유니버스 × 4콜**.
- [x] **Step 2** 구현. **두 단계로 나뉜다(R1)**: `--fetch`(06:00 체인) = 콜 + `_kw_incoming_<tr>` 적재 + 오염 게이트 (b) 만 판정 → 대기. `--merge`(08:10 체인, KRX T-1 도착 후) = 크로스소스 (c) 판정 → (d) 머지. KRX 가 `pending` 이면 머지하지 않고 대기(incoming 은 다음 날 fetch 가 덮는다). 유량은 `api.kiwoom()`(콜당 0.25s) + TR 당 4.4/s, 4 TR 병렬(백필과 동일 실측). 유니버스는 Task 1.1 `kiwoom_common` + `with_grace`. 실행 하한 시각은 P0 프로브 판독값을 `--not-before HH:MM` 로 받아 그 전이면 rc 3.
- [x] **Step 3**: `--date 2026-08-21 --limit 20 --dry-run` 을 서버에서 실행해 콜·행 수·게이트 출력 확인(실제 80콜, 원장 무변경).
- [x] **Step 4**: 테스트 통과 · 커밋

### Task 1.4: KIS 신용잔고 증분 `src/daily/kis_daily.py` (DEFECT-A-03)

**Files:** Create `database/src/daily/kis_daily.py` · Test `tests/test_daily_kis.py`

- [x] **Step 1** 실패 테스트: 같은 `(req_ticker, deal_date)` 에 payload(요청 파라미터·`row_hash`·`collected_at` 제외 전 컬럼)가 동일한 행이 이미 있으면 삽입하지 않는다. 다르면 새 행(정정 보존). 픽스처: 동일 잔고를 `req_d2` 만 바꿔 두 번 → 1행.
- [x] **Step 2** 구현: `backfill_kis.py` 의 `SPEC["credit"]`·`api.kis()` 를 import 해 종목당 1콜(창 `d1 = 오늘-40일, d2 = 오늘(T)` — `d2=T` 여야 `deal_date ≤ T-2 = D-1` 이 온다 [A §2-3]; 응답 30행). 저장은 위 "사실 중복 차단" 저장 함수(새 코드, `backfill_dart.store` 미사용). 유니버스 = Task 1.1 의 요청 유니버스(첫 실행은 기존 3,175 와의 차집합을 로그). `kis_ingest_log` 대신 `daily_run.db`.
- [x] **Step 3**: 서버 `--limit 20 --dry-run` 실측 → 중복 증가 0 확인(A §6-3 B).
- [x] **Step 4**: 테스트 통과 · 커밋

### Task 1.5: DART 증분 `src/daily/dart_daily.py` (DEFECT-B01~B04)

**Files:** Create `database/src/daily/dart_daily.py` · Test `tests/test_daily_dart.py`

- [x] **Step 1** 실패 테스트(소형 dart.db 픽스처, `report_nm` 표본 20건):
  - `classify(report_nm) -> ("periodic", bsns_year, reprt_code) | ("major",) | ("holder",) | ("correction", ...) | None`. `(2026.06)` 라벨 → `(2026, "11012")`, 사업보고서 `(2025.12)` → `("2025","11011")`, `[기재정정]` 접두 처리.
  - `plan(D)`: `dart_disclosure WHERE rcept_dt=D AND stock_code<>''` 에서 corp 별 재호출 목록 생성 — periodic → `fin` + 부속 6종을 **해당 reprt_code 로**(B02 해소), major → DS005 15종 전부(stage 4+5, B04 해소), holder → `elestock`·`majorstock`. 정정은 원 유닛과 동일.
  - `unlock(units)`: 대상 유닛의 `ingest_log` 행 삭제(백필 코드의 `ok` 영구 종결 우회, `store()` 는 멱등이라 안전).
- [x] **Step 2** 구현. CLI: `--date D` · `--skip-sweep`(스윕 생략, 갭 메우기에서 날짜를 연속 돌릴 때) · `--sweep-from YYYYQn`(기본 = D 가 속한 분기) · `--max-docs N`(기본 3000). 순서(한 프로세스): ① `sweep_disclosure.py --from <sweep-from> --quota-window midnight`(열린 분기 전량, 410~890콜) ② `plan(D)` → `unlock` → `backfill_dart.py --corps <목록> --only <ep> --years <y> --reprt <rc> --quota-window midnight` 를 그룹별 subprocess ③ `backfill_docs.py --max-calls <max-docs>`(`sleep_to_kst_midnight` 진입 방지). 완료 판정은 **`ingest_log.status` 가 아니라 B §7-1~7-3 SQL**(DEFECT-B01 회피).
- [x] **Step 3**: 예산 가드 — 오늘 `dart_call_log` k2+k3 > 40,000 이면 중단 + `crit`. `key_id='kael'` 이 1건이라도 생기면 `crit`(P0 이후엔 불가능해야 함).
- [x] **Step 4**: 서버 `--date 2026-09-02 --dry-run --limit 5 --skip-sweep` 으로 계획·게이트 출력 검토(corp 5개분 실제 콜 ≤ 100, 원장 무변경) → 테스트 통과 · 커밋

### Task 1.6: DART 유니버스 갱신 (DEFECT-B05)

**Files:** Modify `database/src/dart_universe.py:18-30` · Test `tests/test_dart_universe.py`

- [x] **Step 1** 실패 테스트: 티커 집합 = KRX 전기간(`isu_base_info` MIN/MAX) **∪** `ka10099` 최신 스냅샷(현역). `ka10099` 에만 있는 신규 상장(예: 386380)이 `first_year=올해` 로 들어온다.
- [x] **Step 2** 구현 + `--kw data/raw/kiwoom.db` 옵션. `corps.txt` 재작성은 `daily_ledger.sh` 에서 **주 1회(월요일)** 실행.
- [x] **Step 3**: 테스트 통과 · 커밋

### Task 1.7: 원장 건전성 판정 `src/daily/ledger_health.py`

**Files:** Create `database/src/daily/ledger_health.py` · Test `tests/test_daily_health.py`

- [x] **Step 1**: A §6-1~6-4 · B §7-1~7-5 의 SQL 을 그대로 함수화. 출력 `logs/health/<D>.json` + 요약 문자열. 판정 3등급: **필수**(실패 = rc 2) / 경고 / 중단 신호(DEFECT-A-01·A-03 감시).
- [x] **Step 2** 기대치 — **유니버스가 요청 목록으로 바뀌므로 종목 단위 테이블은 절대 하한이 아니라 상대 게이트**(리뷰 B2): 키움 ka10008·10060·20068 `행수(dt=D) / 그날 요청 유니버스 ≥ 0.98` · KIS credit `행수(deal_date=D-1) / 요청 유니버스 ≥ 0.95 & tk = n`(실측 응답 2,523~2,530 ÷ 2,563 = 0.985 라 0.98 은 여유 13종목뿐, 리뷰 지적) · ka10014 는 20거래일 평균 대비 ≥ 0.80. 날짜축 테이블은 실측 절대 하한 유지: KRX stk ≥ 920 · ksq ≥ 1,780 · kospi = 51 · kosdaq = 40 · etf ≥ 1,120 · `stk = stk_base` · 마스터 2시장 ≥ 4,200 · DART `rcept_dt=D` ≥ 400(휴장 0) · 문서 `n_never_tried = 0` · WISE `covered×15 + none×2 == n_req`, n_bad 0. 오염 게이트: ka10008 stale ≤ 30% · KRX↔키움 종가·거래량 100%.
- [x] **Step 3**: 테스트(픽스처로 각 게이트 pass/fail 1건씩) · 커밋

### Task 1.8: 체인 두 개 — `scripts/daily_ledger.sh`(06:00) · `scripts/daily_build.sh`(08:10)

**Files:** Create `database/scripts/daily_ledger.sh`, `database/scripts/daily_build.sh` · Modify `daily_wise.sh`(체인의 한 단계로 호출되도록 락 획득 생략 규약 적용)

- [x] **Step 1** `daily_ledger.sh`(06:00 KST, `flock -n /tmp/quant_ledger_raw.lock`): `sync_calendar.sh` → D = 직전 거래일 판정 → 키움 마스터 + WISE(기존 `daily_wise.sh` 본문, 매일) → (D 가 거래일이 아니면 여기서 info 후 종료) → 키움 4 TR `--fetch`(`--not-before` 프로브값) → KIS credit(`d2=T`) → DART 스윕·상세·문서 → 부분 요약 `notify`. 어느 단계든 rc≠0 이면 **그 단계에서 멈추고 crit**. 월요일엔 `dart_universe.py` 선행. **07:00(v3 토큰 재발급) 전에 끝나야 한다** — 예산 5 + 10 + 16 + 5 ≈ 36분.
- [x] **Step 2** `daily_build.sh`(08:10 KST, raw 락 + build 락): KRX `--from D --to D` + 최근 10거래일 `pending` 재수집(10분 간격 최대 6회, ≤ 09:10) → 키움 `--merge`(KRX 대조) → `ledger_health.py` → (필수 게이트 통과 시) `stage_daily.sh` → `equity_daily.sh` → `daily_report.py`. 원장 게이트 실패면 stage·equity 는 돌리지 않는다(어제 판 유지).
- [x] **Step 3**: 각 단계 소요를 `daily_run.db` 에 남겨 예산과 대조 · 커밋

### 게이트 G1

| # | 검증 | 명령 | 기대 |
|---|---|---|---|
| 1 | 단위 테스트 | `pytest tests/test_daily_*.py tests/test_dart_universe.py tests/test_api_dart_keys.py -q` | 전건 pass |
| 2 | 린트·타입 | `ruff check src/daily tests` · `pyright src/daily` | 0 |
| 3 | 기존 회귀 | `pytest tests -q` | 기존 + 신규 전건 pass. **(09-09: 1,051 pass · 1 fail `test_equity_s07_contract.py::test_FX_N_close_변조_사본은_EGC01_만_FAIL` — main 판 락 파일로 격리 재실행해도 실패하는 기존 실패, 이 브랜치와 무관. PR 본문에 기록)** |
| 4 | 서버 드라이런 | `daily_ledger.sh --date 20260820 --limit 20 --dry-run` 후 `daily_build.sh --date 20260820 --limit 20 --dry-run --no-build` | **(09-09 통과)** 수집 체인 rc 0(195초, 키움 80콜 stale 5%·KIS 20콜·DART 94콜), 빌드 체인 키움 merge 20/20 대조 pass·원장 무변경, 건전성 14/20 pass — 남은 fail 1 = `kiwoom.master`(08-20 엔 마스터 스냅샷이 없음, 09-01 개시) · skip 5 = 유니버스 상태 파일 미생성(dry-run)·전날 리포트 없음. DART 하위 도구는 dry-run 개념이 없어 제한 콜 결과가 `ingest_log`(+54)·스윕 신규 공시(+23)로 남는다 — 정상 데이터 |
| 5 | 오염 가드 | 픽스처로 stale 99% 주입 | rc 2, 머지 안 됨, crit 수신 |
| 6 | 휴장 가드 | `--date 2026-09-24` | 전 단계 skip, info 1건, `ingest_log` 에 `holiday` 미기록 |
| 7 | 키 | 드라이런 후 `dart_call_log` | `kael` 0건 |

---

## 5. Phase 2 — 갭 메우기 (1회, 수동 — KRX 단계는 08:00 이후)

순서는 의존 순서다. 각 단계는 다음 단계 전에 게이트 SQL 을 통과해야 한다.

### Task 2.1: KRX 13거래일

- [x] `python src/backfill_krx.py --from 2026-08-21 --to 2026-09-08` (09-09 16:1x KST 실행: 91콜·75초, ok 91 / pending 0)
- [x] 게이트: A §6-1 A·B 를 13일 전부 통과(stk 942~944 · ksq 1,821~1,823 · kospi 51 · kosdaq 40 · etf 1,161~1,168), §6-1 C **0행**

### Task 2.2: 키움 — 1회 실행, 오염 재수집 포함

- [x] **사용자 결정(09-09 18:50 KST): "지금 키움 콜해도 됨"** — 토요일 예약을 취소하고 즉시 실행. `kw_daily.py --date 20260908 --fetch` 뒤 `--merge` **1회씩**(v3 12,701 + 우리 10,300 = 오늘 23,000콜, 검증 한도 20,000 초과를 사용자가 수용)(응답 캡 50~372행이 13거래일을 덮으므로 유니버스 × 4 = ≈ 10,300콜, 10분; 리뷰 B3). KRX 13일치(Task 2.1)가 먼저 들어와 있어야 `--merge` 의 대조가 성립한다. `dt=20260824`·`20260821` 은 `INSERT OR REPLACE` 로 덮인다(C-6 (a)).
- [x] 게이트: (09-09 19:11 실행) fetch 10,508콜·1,550,779행, stale 267/2,623 = 10.2%, merge KRX 대조 2,623/2,623 종가·거래량 일치, 머지 1,548,156행, 당일(09-09) 행 2,623 폐기. **`dt=20260824` stale 99.0% → 10.6%**, `dt>0908` 행 0, `ledger_health --date 20260908` OK 18/20(경고 = 당일 DART 예산, skip = 중복 기준선).

### Task 2.3: KIS credit

- [x] `kis_daily.py --date 20260908` 1회 (09-09 16:2x KST: 2,627콜 · 신규 33,513행 · 중복 차단 43,506행 · 종목 2,627/2,627)
- [x] 게이트: A §6-3 A — **판정일은 D-2**(실측: 09-09 조회 시 max deal_date 09-04 = T-3. 플랜의 'T-2' 는 틀렸다 → 러너·건전성 판정을 D-2 로 수정, `94743a7`) deal_date 09-04 2,506/2,627 = 0.954 ≥ 0.95 pass · B payload 동일 중복쌍 **0**

### Task 2.4: DART — 08-26 부터 (상세 축이 먼저 끊겼다)

- [x] 부속 6종은 접수일 08-31, DS005 08-29, 지분 08-28, fin 08-28 까지만 반영돼 있다[B §4-4]. (09-09 실행: 08-26~09-08 10일 중 9일 게이트 전부 통과, 08-27 은 후속 배치에서 재실행. 오늘 DART 콜 k2 6,786 + k3 4,057 = 10,843 ≪ 80,000, kael 0) `plan(D)` 는 `rcept_dt=D` 로 corp 을 뽑으므로 **`--date 2026-08-26` 부터** `<T-1>` 까지 거래일 순으로 실행. 첫 실행만 스윕 포함, 이후는 `--skip-sweep`(스윕 1회가 열린 분기 전량을 가져온다).
- [ ] 예산: 08-26~09-01 은 이미 원장에 있는 공시라 상세 재호출만, 09-02~T-1 은 스윕 후 동일. 상단 추정 = 10거래일 × (DS005 510 + 지분 300 + 정기 240) + 스윕 890 ≈ 11,400 → **상한 15,000콜**, 하루 k2+k3 40,000 가드.
- [x] 게이트: B §7-1 ①(09-02 이후 각 날짜 ≥ 400) · §7-2(**08-26~T-1 각 날짜**에 `n_fin_ok = n_new_periodic`, `n_corp_recalled = n_corp_with_major`) · §7-3(`n_never_tried = 0`) · §7-5(`kael` 0) — `dart_daily` 가 날짜별로 판정: 9/10 `all gates passed`, 08-27 은 재실행 대기
- 진행 기록(09-09): 08-26 rc 0. 08-27 실행 중 **`backfill_docs.py` 가 DART 014(문서 없음) 확정 3,130건을 매 실행 재시도해 3,000콜·18분을 태우는 결함** 발견 → 014 기본 제외·`--retry-014` 플래그로 수정(`tests/test_backfill_docs_plan.py`), 낭비 중이던 서브프로세스 중단(08-27 은 docs 단계만 미완 → 뒤 날짜의 docs 차집합이 덮는다; 마지막에 08-27 재실행). 08-28 부터 새 코드로 진행. **범위 확장(09-09 사용자 질의)**: 공시목록은 09-01 까지 빈 날이 없고(08-18~26 일별 410~1,015건 실측) 상세 축만 08-28~31 에 끊겼으므로 08-26 시작이 맞지만, 08-20~25 접수분의 커버를 실측하니 반기보고서 18/18 corp 재무 있음·주요사항 74 corp 중 67 재수집 기록 있음(나머지 4 = DS005 범위 밖 유형 전건 no_data, 1 = 당시 유니버스 밖, 1 = 재수집 기록 없음) → 의심을 없애려 **08-20·21·24·25 + 08-27(문서 단계 재실행)** 을 본 루프 뒤 이어 돌린다(`logs/p2/dart_gap_extra.log`). **→ 09-09 18:24 완료: 5일 전부 `all gates passed`.** 오늘 DART 총 k2 8,897 + k3 4,057 = 12,954콜(상한 15,000 안), kael 0.

### Task 2.5: 유니버스

- [x] `dart_universe.py` (09-09 실행: 3,478 → **3,483** corp, 현역 2,713 → 2,718, 386380 스카이랩스 = 01586923 매핑. 이전 파일 `corps.txt.bak_p2_20260909`)
- [x] `ledger_health.py --date 20260908 --skip kiwoom` → OK 11/14 (skip 3: kiwoom 제외·중복 기준선 첫 기록 / warn 1: `dart.budget` 은 갭 메우기 당일 10,843콜이라 정상)

### 게이트 G2

| # | 검증 | 기대 |
|---|---|---|
| 1 | 원장 5개 최신일 | **(09-09 18:30)** KRX 09-08 ✓ · KIS credit 09-04(= D-2, 실측 규칙) ✓ · DART rcept_dt 09-09 ✓ · WISE 09-09 ✓ · **키움 시계열 08-24 → 09-12(토) 크론 후 판정** |
| 2 | `ledger_health.py --date <T-1>` | **✓** `--date 20260908 --skip kiwoom` OK 11/14(경고 1 = 갭 메우기 당일 DART 예산, skip 3 = 키움 제외·중복 기준선 첫 기록) |
| 3 | 오염 | **✓** `ka10008` 08-24 stale 10.6%, `dt > 0908` 행 0 |
| 4 | KIS 중복 | **✓** payload 동일 중복쌍 0 (KIS 실행 후 실측) |
| 5 | 예산·키 | **✓** 갭 메우기 DART 총 12,954콜 < 15,000, `kael` 0 |
| 6 | 키움 유니버스 | **△→✓(규칙 수정)** 요청 2,627 = tickers.txt 2,602 ∪ 보통주 2,563(시드 전용 64 유예). **386380 미포함 발견** — 신규 상장은 규모구분(`upSizeName`)이 몇 주 뒤에야 붙어 빠진다(스카이랩스·해치텍·니어스랩 등 87종목). 규칙을 `upSizeName<>'' OR (marketName∈{거래소,코스닥} AND 코드 6자리=0)` 으로 확장 → 09-09 스냅샷 기준 2,650. 09-10 06:00 체인부터 포함(ka10008 캡 50일이 신규 종목 전 구간을 덮는다) |

---

## 6. Phase 3 — 원장 크론 가동·관찰 (5거래일)

### Task 3.1: crontab 등록

- [x] (09-09 18:10 KST 등록) `0 21 * * * QL_SKIP_KW=1 daily_ledger.sh`(KST 06:00, `daily_wise.sh` 줄 교체 — daily_wise 는 체인 첫 단계로 호출) · `10 23 * * * QL_SKIP_KW=1 daily_build.sh --no-build`(KST 08:10). **키움 단계는 `QL_SKIP_KW=1` 로 제외** — 앱키 분리(또는 v3 페이지 축소) 확정 후 변수를 빼고 프로브 판독값을 `QL_KW_NOT_BEFORE` 로 준다. 이전 crontab 은 `logs/crontab_backup_p3_20260909.txt`.
- [ ] `daily_dart.sh` 는 서버에서 삭제(저장소는 `docs/archive/` 로 이동).

### Task 3.2: 관찰 5거래일

- [ ] 매일 06:40 전 수집 요약, 09:15 전 빌드(원장 게이트) 요약 텔레그램 수신. 내용: 소스별 행수·콜 수·소요·게이트 결과.
- [ ] 실패 시 `crit` 이 왔고, 원인·조치를 `logs/health/<D>.json` 옆 `<D>.note` 에 남긴다.

#### 관찰 기록 (거래일마다 한 줄)

| 일차 | 실행일(D) | 06:00 체인 | 08:10 체인 | 건전성 | 소요·예산 | 비고 |
|---|---|---|---|---|---|---|
| 2/5 | 09-11 (D=09-10) | rc 0 — WISE 19,416행(커버 816·무커버 1,794, 유니버스 2,610: 규모구분 갱신 +47)·키움 3 TR 7,965콜·KIS 09-08분 도착(2,529, 0.9525)·DART 1,028콜(3분기 창 excess=6 기록, 449페이지) | rc 1 — KRX 7/7·**외국인 보유 첫 08:10 실행 ok**(2,655콜 14분, 토큰 8005 흔적 0)·머지 2,651 대조 100%, `new=10,155 changed=685,981`(ka10014 `ovr_shrts_qty` 창 상대값 685,979 + ka10008 2 = 삼성전자·SK하이닉스 09-09 잠정값 → 1회 갱신 완료)·건전성 19/20, 실패 1 = `wise.raw` 절대 밴드 오탐 → 커버리지 유도로 수정 배포(`da53269`) | 오탐 1(수정) | 06:00 체인 79분(KIS·키움 06:48 종료) · 08:10 체인 15분 | 프로브: 투자자 순매수 18:56 이후 불변 · **대차 당일 행 20:05 확정** · **외국인 보유 당일 행 21:05 확정**(다음 날 07:05 불변) · 공매도 거래량 불변(누적 필드만 자정 창 이동). WISE 세 종목(036010·190510·342870)은 새 판정으로도 none = 진성 무커버, 317330 은 복귀 |
| 1/5 | 09-10 (D=09-09) | rc 0 — WISE 4,309행·키움 10,620콜/1,563,958행(stale 10.0%)·KIS 2,655콜/신규 2,817행(deal_date 09-07, 2,529/2,655=0.9525)·DART 1,036콜/공시 419건 | rc 0 — KRX 7/7 ok(08:10 즉시, pending 0)·키움 머지 2,650 대조 100%·건전성 **19/20 pass**(1건은 `kis.credit.dup_growth` 첫날 기준선 skip) | ok | 06:00 체인 **76분**(06:00→07:17: WISE 5·키움 16·KIS 27·DART 30) — G3 #3 의 45분 초과. 단 KIS·키움은 06:47 전 종료라 07:00 v3 토큰 재발급과 겹치지 않음 · 키움 10,620 > #4 의 10,500(유니버스 2,655 로 커짐, v3 안전 상한 20,000 은 여유) · DART 1,036 · kael 0 | 알림 3건(wise·ledger·build 완료) CHAT_ID_LOG 도달, crit 0. 발견: ① DART Q3 창이 `mismatch`(적재 44,162 > total_count 44,156 — DART 측 삭제 공시 6건 추정)라 매일 442콜 전량 재스윕 ② `kis.credit.rows` 0.9525 vs 하한 0.95 여유 8종목(warn) |

### 게이트 G3

| # | 검증 | 기대 |
|---|---|---|
| 1 | 5거래일 연속 `daily_run.db` status | 전 소스 `ok`, 사람 개입 0회 |
| 2 | 원장 최신일 | 매일 06:40 에 키움 incoming·KIS(T-2 또는 T-3)·DART = T-1, 09:15 에 KRX·키움 머지 = T-1 |
| 3 | 소요 | 06:00 체인: **KIS·키움 단계가 07:00 전 종료**(v3 토큰 재발급과 겹치지 않게 — DART 는 자체 키라 무관) + 전체 ≤ 90분 · 08:10 체인 원장 단계 ≤ 20분. *(09-10 개정, 사용자 승인: 종전 "전체 ≤ 45분" 은 첫 실행 76분 — WISE 5·키움 16·KIS 27·DART 30 — 으로 실측과 맞지 않았고 45분의 근거는 토큰 충돌 회피뿐이었다)* |
| 4 | 예산 | DART 일 ≤ 2,500콜(평시 830~2,000, 리뷰 B6; 직전 분기 재스윕 30일간은 +≈600) · 키움 ≤ 유니버스×4 + 100(09-10: 2,655×4 = 10,620; 종전 10,500 은 유니버스 2,602 기준) · 공유 앱키 총량은 v3 저녁분과 합쳐 확인된 상한 안(09-09·09-10 v3 로그 오류 0) · `kael` 0 |
| 5 | 비거래일 1회 이상(주말 포함) | 체인이 skip 하고 info 만 보냄 |
| 6 | 오탐 | `crit` 0건 또는 전건 실제 사고 |

---

## 7. Phase 4 — stage 일일 전량 재빌드

### Task 4.1: `scripts/stage_daily.sh`

**Files:** Create `database/scripts/stage_daily.sh`

- [ ] **Step 1**: `daily_build.sh`(08:10) 의 **자식으로 실행**(raw 락을 `QL_RAW_LOCK_HELD=1` 로 물려받아 스냅샷 중 `wisereport.db`(delete 저널) 쓰기가 끼어들지 않게, 설계 `STAGE_DESIGN.md:72`) + 자기 `flock -n /tmp/quant_ledger_build.lock`(자식 `run_stage_all.sh` 는 `QL_BUILD_LOCK_HELD=1` 로 생략) 아래에서 ① 스냅샷 5 DB 1세트(`snapshot.py`, 17.7 GB·3.8분 — `stg_price_daily` 의 G9 가 kiwoom 원장을 직접 조인하므로 `{sources} ∪ {cross_check.db}` 전부 필요, 부분 스냅샷 금지) ② `run_stage_all.sh <snap>`(62표 ORDER; 문서층 프리패스 4표는 캐시가 없어 자동 skip — 의도된 동결, `stg_doc_index` 는 `doc_store` 소스라 매일 재빌드되며 `stg_doc_meta/section` 과 접수번호 집합이 어긋나는 것은 알려진 상태로 문서화) ③ `stage/health.py` ④ 스냅샷 GC(Task 4.2) ⑤ notify.
- [ ] **Step 2**: 원장 게이트(`ledger_health.py`)가 실패한 날은 돌리지 않는다(어제 판 유지). `daily_build.sh` 가 원장 단계 성공 직후 호출.
- [ ] **Step 3**: 커밋

### Task 4.2: 스냅샷 GC 를 빌드에 내장

**Files:** Modify `database/src/stage/snapshot.py` · Test `tests/test_stage_snapshot_gc.py`

- [ ] `gc(root, keep=3, protect=현재 66 MANIFEST 가 가리키는 snapshot_id)` — 보호 세트 + mtime 최신 keep 세트를 남기고 삭제. `stage_daily.sh` 성공 종료 시 호출. 테스트: 5세트 중 보호 1 + 최신 3 남김.

### Task 4.3: stage 건전성 `src/stage/health.py` (C1~C5)

- [ ] MANIFEST 66개 읽기 전용. C1 62표 전부 오늘 스냅샷 · C2 fail 0 & `_failed` 오늘 0 · C3 append_only 행수 비감소 · **C4 재현성(비용 0)**: 판정 단위는 파일 mtime 이 아니라 **테이블**이다(5원장을 매일 쓰면 `_meta.src_mtime` 은 항상 바뀌어 공집합, 리뷰 2차 #3) — 직전 BuildRecord 대비 `Σn_src`·`Σn_dedup`·`Σn_reject` 가 전부 동일한 테이블(v3 동결 4표·KIS 폐지축 4표·정기보고서 없는 날의 `stg_fin` 등 소스 테이블이 안 자란 것)은 `content_hash` 도 동일해야 한다(`stg_doc_parse_log` 제외) · C5 소요 예산(스냅샷 + 빌드 ≤ 30분).

### Task 4.4: G9 정책 · baseline 금지

- [ ] 관찰 기간엔 **fail-closed 유지**. `stg_price_daily` 가 G9 로 폐기되면 `crit` + 불일치 행을 `logs/health/<D>.g9.tsv` 로 남긴다. 5거래일 실측 후 불일치가 0 이면 유지, 아니면 완화안(직전 값 − ε)을 사용자 결정으로 올린다.
- [ ] `python -m stage.baseline` 은 체인 어디서도 호출하지 않는다(사람 승인 전용, R9).

### 게이트 G4 (5거래일)

| # | 검증 | 기대 |
|---|---|---|
| 1 | C1 | 62표 전부 오늘 스냅샷 id(문서층 4표 제외) |
| 2 | C2 | fail 0, `_failed` 0 |
| 3 | C3 | append_only 감소 0 |
| 4 | C4 | `Σn_src·n_dedup·n_reject` 불변 테이블(매일 ≥ 8표: v3 4 + KIS 폐지축 4)은 hash 동일 (선례 60/60) |
| 5 | C5 | 스냅샷 3.8분 + 빌드 22.5분 ≤ 30분 |
| 6 | G9 | 5거래일 폐기 0 (또는 폐기 사유 기록·결정) |
| 7 | 디스크 | `data/snapshots` ≤ 60 GB(keep=3 × 17.7 + 보호 6.6), `_failed` 30일 |
| 8 | 락 | stage 실행 중 equity 수동 실행 → exit 3 |

---

## 8. Phase 5 — equity 일일 갱신·소비자 전달

### Task 5.1: 규칙 e1.15.0 — 날짜 상수 파생화

**Files:** Modify `database/src/equity/rules_s02.py:52-93`, `sql/security.sql:69`, `sql/security_span.sql:13`, `baseline_locked.json` · Test `tests/test_equity_s02_calendar.py`

- [ ] **Step 1** 실패 테스트: `backfill_end` 를 상수가 아니라 **stage `stg_price_daily` 의 max(date)** 에서 유도. EG17 술어 = `max(trading_calendar.date) == stage max` **AND** `max(date) >= 직전 빌드 max(date)`(원천이 잘려 조용히 짧아지는 것을 막는 대체 술어). `security.backfill_end`·`security_span` 도 같은 유도값.
- [ ] **Step 2**: `adj_factor.asof_for_jump_check` 도 유도값. `asof_sample_dates[4]`·`contract_probe_dates[6]` 의 최신일은 **과거 고정일**(2026-08-20)로 바꿔 EG5c 표본을 안정시키고, 최신 구간은 새 게이트 **EG14 최소판**(`price_daily`·`universe_daily` 의 D 파티션 행수 > 0 **AND** ≥ 0.95 × D-1 파티션 행수 — 실측 유니버스 ≈ 3,924 가 하루 0.7 신규/0.5 소멸이라 5% 여유면 충분)로 본다.
- [ ] **Step 3**: `RULES_VERSION = "e1.15.0"`, 락 파일 갱신, `EQUITY_GATES.md` 에 EG17 대체 술어·EG14 등재. 서버 전량 재빌드 1회(rules_version 단일화).
- [ ] **Step 4**: 테스트 · 커밋 · PR(equity 규칙 변경은 PR 로)

### Task 5.2: `_pinned` GC

- [ ] `inputs.py` 에 `gc_pinned(keep=3)`: 어떤 현행 equity BuildRecord.inputs 도 가리키지 않고 최신 3판 밖인 `v=` 삭제. `inputs.py:144-145` 의 금지 주석은 "manifest.commit 을 부르지 않는다" 로 한정해 유지. 테스트 포함.

### Task 5.3: `scripts/equity_daily.sh`

- [ ] `flock -n /tmp/quant_ledger_build.lock`(자식 `equity_rebuild_all.sh`→`run_equity.sh` 는 `QL_BUILD_LOCK_HELD=1` 로 획득 생략) → `equity_rebuild_all.sh`(470초) → `python -m equity catalog`(EG11·EG5c) → `contract` → `gc_pinned` → D §7 12항 판정(`src/equity/health.py`, 아래 G5 는 그중 요약 10항) → notify. **EG5c `n_diff_total ≠ 0` 이면 `--rebase-asof` 를 자동으로 하지 않는다** — `warn` + diff 키 10건을 알림에 첨부, 사람이 판단.
- [ ] stage 체인 성공 직후에만 실행(실패한 날은 어제 판 유지 = EG0 안전망).

### Task 5.4: 소비자 전달 규약

- [ ] 서버 산출이 정본. 워크벤치 사용자는 필요할 때 `fetch_equity_local.sh <dest> minimal`(2.1 GB)로 당긴다(매일 자동 push 안 함). 스크립트가 `dataset_profile`·`factor_readiness` 포함을 **검증**하고 없으면 실패.
- [ ] 알려진 제약을 규약에 명시: 매일 `snapshot_id` 가 바뀌므로 **전날 만든 `target_tape` 는 새 데이터셋에서 거부**되고(`_adapter.py:129`), `_asof/` 는 3일치만 남는다(`ASOF_KEEP`). 백테스트 재현이 필요하면 그날의 `fetch` 사본을 보관한다.
- [ ] backend PR(공용 영역·별도): `equity_duckdb` 어댑터가 루트에 `dataset_profile` 이 없으면 **기동 실패**(현재는 랙 0 으로 조용히 폴백 → 확정 look-ahead) [D §4-3].
- [ ] `TECH_DEBT.md §4` 본문을 "코드는 8014655 로 해결, 남은 것은 폴백 차단" 으로 갱신.

### Task 5.5: 09-10 검수 후속 (근거 `reviews/2026-09-10-intake-audit-summary.md`, 결정 6)

- [ ] **KIS 재수집 판본 = 최초 관측판**(결정 6-2): `sql/credit_daily.sql` 에 (ticker, date) 당 `observed_date` 최소 행을 고르는 판본 CTE, EG3 metric `n_versions_folded`. 없으면 격자 유일성(`gates.py:249`)에서 빌드 폐기 — 2026년 505군/29종목이 이미 있다.
- [ ] **액면병합 2건 확인**(H1): 첫 재빌드 뒤 001290·273060 의 `adj_factor` 0.2 존재. `ledger_health` 에 `PARVAL`/`LIST_SHRS` 일변동 warn 게이트 추가(09-09 전수 6건이 기준).
- [ ] **KIS 잔고율 라벨**(M3): `stg_credit_daily` 컬럼 설명에 "공표일(T+2) 상장주식수 기준", equity 잔고율 검산은 T+2 주식수로. 잔고 항등식 게이트는 `adj_factor` 사건일 제외(M4).
- [ ] **WISE 커버 이력**(사용자 요청 09-10): equity 파생 — 종목·날짜 격자에 커버 여부(stage `fetched_date` 행 존재), 커버 시작일·중단일·연속일, 월말 추정치 시계열의 최초 non-null 달(09-01 이전 월 단위). `opinion_daily` 확장 또는 별도 표. stage 는 파생 금지(STAGE_DESIGN §1)라 두지 않는다.
- [ ] **정정 링크 확인**(M7): `disclosure_version` 이 `[기재정정]`(정정본만 축에 있음)·`[첨부정정]`(원본만) 두 경우를 모두 잇는지.

### 게이트 G5 (5거래일)

| # | 검증 | 기대 |
|---|---|---|
| 1 | 완주 | `TOTAL` 470±60s, rc 전부 0, `EG*=fail` 0 (`EG5a=skip(inputs_changed)` 정상) |
| 2 | 행수 | `price_daily`·`price_adj_daily`·`universe_daily` = 전일 + 그날 유니버스(≈ 3,924), 셋이 동일 |
| 3 | EG14 | D 파티션 행수 > 0 AND ≥ 0.95 × D-1 파티션 |
| 4 | 판본 | 28표 `rules_version` 단일 |
| 5 | catalog | `snapshot_id` 일치, EG11 pass, EG5c `n_diff_total` = 0 (≠0 이면 사람 판정 기록) |
| 6 | contract | EGC 6항 pass |
| 7 | 대장 | `dataset_profile` 79행·`factor_readiness` ready 54 유지, `first_usable_date` 후퇴 0 |
| 8 | 디스크 | `data/equity` 일 증가 ≤ 300 MB(`_pinned` GC 작동) |
| 9 | 소비자 | `fetch_equity_local.sh` 후 로컬 워크벤치에서 `financial.*` 필드 available |

---

## 9. Phase 6 — 운영 정착 (2주)

### Task 6.1: 통합 건전성 리포트

- [ ] `scripts/daily_report.py`: `ledger_health` + `stage/health` + `equity/health` + 예산·디스크·락 상태를 한 메시지로. 등급표(`COLLECT_PLAN §4-4`): 즉시(수집 실패·게이트 폐기·`kael` 사용·디스크 < 50 GB) / 일일 요약 / 무음.
- [ ] 09-05 대시보드(1회성 아티팩트)의 66/66 표를 이 리포트의 stage 절로 대체.

### Task 6.2: 로그 로테이션·백업

- [ ] `gc.sh` 를 일요일 체인 끝에. `daily_*_MMDD.log` 는 `%Y%m%d` 로 바꿔 연도 충돌 제거. `data/raw/*.db` 는 `snapshots/` keep=3 이 사실상 일일 백업이므로 별도 백업은 `~/backups/quant-ledger/` 월 1회.

### Task 6.3: 주간 점검 체크리스트 (일요일)

- [ ] `equity_gate_all.sh`(175초, baseline 재판정) · `dart_universe.py` · KIS 중복쌍 수 · 스냅샷·`_pinned` 용량 · 키별 DART 주간 콜 · 프로브 재확인(분기 1회).

### Task 6.4: 문서 갱신

- [ ] `COLLECT_PLAN.md §4`(일일 증분)를 이 플랜의 최종 시간표·게이트로 재작성(19:00 안 폐기 명시). `DART_DESIGN.md` 운영 절의 `daily_dart.sh` 언급 정리. `START_HERE.md` 에 "일일 체인" 절 추가. `TECH_DEBT.md` 갱신(§4 해소, 신규: 문서층 파싱 증분·`t_*_ms`·EG13/EG19 미구현·DEFECT-B02 과거분). `database/README.md` scripts 행 갱신. `DART_CENSUS.md`·`DART_DESIGN.md` 에서 부속 6종 11011 한정이 의도였는지 확인해 기록.

### 게이트 G6

| # | 검증 | 기대 |
|---|---|---|
| 1 | 2주(10거래일) 무개입 | 원장·stage·equity 전 체인 `ok`, 사람 조치 ≤ 1회 |
| 2 | 알림 | 매일 1건 요약, `crit` 은 실제 사고만 |
| 3 | 디스크 | 2주간 순증 ≤ 20 GB |
| 4 | 문서 | 위 문서 갱신 PR 병합 |

---

## 10. 최종 시간표 (KST · 서버 crontab 은 UTC = KST − 9)

> **09-10 개정(결정 7)**: 키움 외국인 보유(ka10008)는 06:00 이 아니라 **08:10 체인**(KRX 직후, 머지 직전)에서 받는다. 근거 — 프로브 실측으로 T-1 행이 06:05~07:05 사이에 한 번 정정된다(삼성전자 2,736,410,506 → 2,736,456,466주, SK하이닉스 +30,660주; 무작위 30종목·대형주 10종목은 불변). 다른 세 TR 의 T-1 행은 00:05 부터 18:05 까지 19회 판독 불변이라 06:00 유지. 장 마감 직후(18:10) 당일 행은 투자자·공매도·가격은 확정값이지만 외국인 보유는 전일 복사본(변동 0), 대차는 전부 0 이라 저녁 수집은 채택하지 않는다. DART 는 접수가 ~18시까지 이어져(09-09 접수 525건 중 124건 = 24% 가 16:40 이후) 다음 날 06:47 스윕이 맞다.

| KST | 작업 | 락 | 예산(실측 근거) |
|---|---|---|---|
| **06:00** | `daily_ledger.sh` — 캘린더 → 키움 마스터 2콜 + WISE 15,600 req(4.7분) → 키움 4 TR `--fetch`(≈10,300콜, 10분, 오염 게이트만) → KIS credit(≈2,600콜, 16분, `d2=T`) → DART 스윕·상세·문서(830~2,000콜, ≈5분) → 수집 요약 | raw | ≈ 36분 → 06:36 (**07:00 전 종료**) |
| 07:00 | (v3 KIS·키움 토큰 재발급) | — | 우리 06:00 체인은 끝나 있어야 함 |
| 07:45 | (v3 DART 증분) | — | 키 분리됨 |
| 08:00 | KRX T-1 공표 (실측 07:55~08:00) | — | |
| **08:10** | `daily_build.sh` — KRX(7콜 + pending 재수집, 재시도 ≤ 09:10) → 키움 `--merge`(KRX 종가·거래량 100% 대조 후 `dt <= D` 머지) → `ledger_health` | raw | ≈ 5분 → 08:15 |
| ≈ 08:15 | `stage_daily.sh`(자식, raw 락 유지) — 스냅샷 5 DB(3.8분) → 62표 전량(22.5분) → health → GC | raw+build | ≈ 27분 → 08:42 |
| ≈ 08:42 | `equity_daily.sh` — 28표(470s) → catalog(90s) → contract(9s) → `_pinned` GC → health | build | ≈ 10분 → 08:52 |
| ≈ 08:55 | `daily_report.py` → 텔레그램 요약 | — | |
| 20:05~23:48 | (v3 `daily_all`) | — | 우리 체인과 겹치지 않음 |

06:00 체인 36분 + 08:10 체인 ≈ 45분. 08:05~15:30 유휴 창 안. stage·equity 는 같은 `build` 락으로 직렬(RSS 합 > 13 GB 방지). 락은 최외곽만 잡고 자식은 `QL_*_LOCK_HELD` 로 생략한다(Task 0.2). KRX 가 09:10 까지도 `pending` 이면 그날은 빌드 체인이 멈추고(키움 incoming 은 대기) stage·equity 는 전날 판을 유지한다. 키움 시계열의 06:00 확정은 **추정**이며 P0 프로브가 확인한다 — 늦으면 키움 fetch 만 그 시각으로 옮기고 나머지 시간표는 유지.

---

## 11. 범위 밖 · 후속

- 문서층 **파싱** 증분: `doc_store` 신규 행만 프리패스하는 경로 신설(캐시 하드링크 + `repair` 확장) + `stg_doc_parse_log.t_*_ms` `payload_exclude`. 그 전까지 `stg_doc_meta/section/correction/parse_log` 는 09-04 판 동결, `stg_doc_index` 만 매일. 별도 플랜.
- DEFECT-B02 과거분: 2016~2025 정기보고서 부속 6종의 반기·분기(11012·11013·11014) 판본 백필(예산 별도 산정).
- `share/` 리빌드 크론(설계서엔 "완료", 실제 없음, `share/latest` 08-27 정지).
- KIS flow·short·loan·master 의 살아있는 유니버스 확장(현재 폐지축 전용) · 기존 `kis_credit_balance` 중복 1,084,443행 정리.
- 키움 우선주·ETF·ETN 수집(유니버스 1,710 차이).
- stage G5 `src_bytes/src_mtime` 비교·G6 payload 비율 미구현, equity EG13·EG19 미구현, stage baseline 락 파일(equity 처럼 저장소 사본 + 대조).
- `snapshot_id` 일변경으로 인한 `target_tape` 거부 완화(내용 기반 지문, TECH_DEBT 1) · `ASOF_KEEP` 상향.

## 12. 근거

- `docs/reviews/2026-09-09-daily-findings-A-krx-kiwoom-kis.md` — 갭·콜·오염·완료 SQL
- `docs/reviews/2026-09-09-daily-findings-B-dart-docs-wise.md` — DART 증분 경로·예산·결함 B01~B05
- `docs/reviews/2026-09-09-daily-findings-C-stage.md` — 빌드 단위·의존 지도·게이트·C1~C5
- `docs/reviews/2026-09-09-daily-findings-D-equity.md` — pin·재빌드·날짜 상수·소비자
- `docs/reviews/2026-09-09-daily-findings-E-ops-timing.md` — 자원·시간 창·키·락·알림·배포
