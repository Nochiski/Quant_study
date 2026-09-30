# KIS 신용잔고 완료 판정 · 오전 원장 체인 격리 수정 플랜 (2026-09-30, 승인 대기)

> 계기: 09-30 "들어오는 데이터 상태" 점검. 모 플랜은 일일 증분 [`2026-09-11-daily-incremental-v2.md`](2026-09-11-daily-incremental-v2.md)
> (오전 06:00 원장 체인). 실행 모델은 v3 통합 플랜과 같다 — 오케스트레이터만 서버·배포·커밋, 게이트는 실측 숫자로만 통과.

## 0. 한눈에

| 태스크 | 고치는 것 | 파일 | 게이트 |
|---|---|---|---|
| **T-K1** 신용잔고 완료 판정 재정의 | 연휴 뒤 재실행 거짓 실패 + 분모 드리프트 | `src/daily/kis_daily.py` · `tests/test_daily_kis.py` | GK1·GK3 |
| **T-K2** 원장 건전성 검사 정렬 | `kis.credit.rows` 가 T-K1 과 같은 정의를 쓰게 | `src/daily/ledger_health.py` · `tests/test_daily_health.py` | GK1·GK2 |
| **T-K3** 오전 원장 체인 단계 격리 | 신용잔고 실패가 DART 를 막지 않게 | `scripts/daily_ledger.sh` · `tests/test_daily_ledger_sh.py`(신규) | GK1·GK4 |

**종료 게이트 G-K**: GK1~GK4 전부 통과.

## 1. 결함 (09-30 실측, 서버 `logs/daily_ledger_*.log` · `data/raw/kis.db`)

### DEFECT-K1: 연휴 뒤 같은 D 를 다시 돌면 신용잔고 판정이 거짓 실패한다

- **상황**: 09-24 오전 체인(D=09-23)에서 KIS 는 통과했지만 DART 가 rc=2(`periodic_followed unresolved=1`) → 체인 런로그 `failed`.
  추석 연휴(09-24~26)·주말이라 09-25~28 오전 체인도 D=09-23 을 다시 돈다(`scripts/daily_ledger.sh:61-63` — `ok` 가 아니면 재실행).
- **인풋**: `daily.kis_daily --date 20260923`, 판정일 09-21. 콜 2,654건 모두 성공, 응답은 이미 원장에 있는 사실과 같아 `new=0 dup_skipped=77,597`.
- **에러 위치**: `src/daily/kis_daily.py:318-352` `gate()` — `since`(이번 런 시작) 이후 **새로 적재된 행만** 센다(DEFECT-A06 대응).
  같은 사실은 `store_new_facts` 가 적재하지 않으므로 `n_rows(this run)=0` → 비율 0 → `GATE_FAILED` rc=2.
- **위험성**: 데이터는 정상인데 수집 실패로 판정된다(거짓 경보). 아래 K2 와 겹쳐 09-25·26·27·28 네 번 연속 오전 DART 단계를 막았다.
  A06 의 뜻("오늘 콜이 전멸했는데 옛 행으로 통과")은 **이번 런 응답에 판정일 행이 있었는가**로 재야 하는데 **적재 여부**로 재고 있다.

### DEFECT-K2: 신용잔고 실패가 오전 DART 수집을 막는다

- **상황**: 오전 체인은 `kiwoom fetch && kis credit && dart && dart company gap`(`scripts/daily_ledger.sh:77-80`).
- **인풋**: 09-25~28(K1) · 09-30(K3) 오전 체인.
- **에러 위치**: `scripts/daily_ledger.sh:73-80` — `&&` 연쇄라 앞 단계 rc≠0 이면 뒤 단계가 **실행되지 않는다**.
- **위험성**: 보조 소스(신용잔고 → v4 보조 버킷 `CRDT_CHG`) 하나가 핵심 소스(DART) 수집을 막는다. 최근 6회 오전 중 5회 DART 미실행.
  실제 손실은 이번엔 없었다 — 최근 5개 접수일(09-21~29) 모두 오전 런이 처음 잡은 공시 0건(저녁 런이 다 받음), 30일 재스윕이 뒤를 덮는다.
  그래도 `dart company gap`(신규 corp 회사정보)도 같이 빠졌고, 저녁 런이 실패한 날엔 오전 런이 유일한 보완 경로라 그날은 실손이 된다.

### DEFECT-K3: 신용잔고 판정 분모(요청 유니버스)가 원래 신용잔고가 없는 종목을 포함해 비율이 계속 내려간다

- **상황**: 요청 유니버스 = 키움 보통주 마스터 + 유예(`data/daily/universe_kw.json` `n_requested`). 신규 상장으로 2,651 → 2,655.
  신용잔고가 있는 종목은 ≈2,525 로 그대로(신규 상장·관리 종목 등은 신용 대상이 아니다).
- **인풋**: 09-30 오전 `kis credit` — 2,522 / 2,655 = 0.9499 < 0.95. 실제 빠진 것은 3종목(138610 콜 오류 `OPSQ1002`, 373170·412350 은 09-23 행 없음).
- **에러 위치**: `src/daily/kis_daily.py:338-339` `ratio = n_rows / n_requested` · `src/daily/ledger_health.py:330-337` 같은 식.
- **위험성**: 비율이 09-16 0.9536 → 09-29 0.9499 로 2주 새 0.4%p 내려왔다. 이제 콜 1건 실패로 rc=2 → K2 로 DART 까지 멈춘다.
  반대로 여유가 없으니 문턱을 낮추면 진짜 결손(콜 5% 전멸)을 놓친다 — 분모가 틀려서 문턱이 제 역할을 못 한다.

## 2. T-K1 — 신용잔고 완료 판정 재정의

### 2-1. 정의

- **기대 집합** `expected` = 원장에서 판정일 **직전 5개 `deal_date`** 에 한 번이라도 행이 있던 종목 ∩ 요청 유니버스.
  (신용잔고가 원래 없는 종목은 빠지고, 신용 대상에서 빠진 종목은 5세션 뒤 자연히 빠진다.)
- **받음** `hit` = **이번 런 응답**에 `deal_date = 판정일` 행이 있던 종목(적재됐든 같은 사실이라 건너뛰었든). A06 은 그대로 막힌다 — 콜이 전멸하면 `hit` 이 비어 있다.
- **비율** = |hit ∩ expected| / |expected| ≥ **0.98** 이고 원장의 판정일 행이 종목당 1행.
- **첫 수집(기대 집합이 빔)**: 옛 판정 그대로 — |hit| / |요청 유니버스| ≥ 0.95. detail 에 "기대 집합 없음 — 요청 유니버스로 판정".
- detail 에 기대·받음·비율·새로 생긴 종목 수·빠진 종목 앞 5개를 남긴다(`.claude/rules/error-messages.md`).
- `FAILURE_RATIO_MAX`(콜 실패 2% 초과 → GATE_FAILED), `DUP_GROWTH`, 토큰·유량 중단은 그대로.

### 2-2. 문턱 근거 (서버 원장 재계산, `logs/` 일회성 프로브)

| 기간 | 판정일 수 | 새 비율 최저 | 최고 | 옛 비율(09-16~29) |
|---|---|---|---|---|
| 08-10 ~ 09-23 | 32 | **0.9968** | 0.9996 | 0.9536 → 0.9499 (단조 하락) |

0.98 = 기대 ≈2,530 중 51종목 이상 빠지면 실패. 관측 최대 결손은 8종목. `FAILURE_RATIO_MAX` 2% 와 같은 선.

### 2-3. 테스트 (먼저 실패를 재현)

1. **연휴 재실행**: 원장에 판정일 사실이 이미 있고(옛 `collected_at`) 이번 응답이 같은 사실 → `OK`. (현행 코드에서 `GATE_FAILED` 재현)
2. **신용잔고 없는 종목**: 요청 100 · 이력 있는 90 전부 받음 · 10 은 응답 빈 목록 → 비율 1.0 `OK`. (현행 0.90 → 실패 재현)
3. **진짜 결손**: 기대 100 중 97 → 0.97 → `GATE_FAILED`.
4. **A06 유지**: 콜 전멸(기존 `test_gate_counts_only_rows_inserted_by_this_run` 을 새 어휘로) → `GATE_FAILED`.
5. **첫 수집**: 이력 없음 → 요청 유니버스 대비 0.95 (기존 테스트 기대값 유지).
6. **종목당 1행**: 기존 `test_gate_rejects_a_ticker_with_two_rows_on_the_target_date` 유지.

## 3. T-K2 — 원장 건전성 `kis.credit.rows` 정렬

- `ledger_health.check_kis` 가 `kis_daily` 의 기대 집합 함수를 import 해서 같은 정의로 판정한다(`dup_pairs` 와 같은 방식 — 정의는 한 곳).
- 건전성 검사는 사후 점검이라 "이번 런 응답"이 없다 → 받음 = 원장의 판정일 행. 요청 종목 목록이 상태 파일에 없으므로
  기대 집합에 요청 유니버스 교집합을 걸지 않는다(상장폐지 직후 몇 종목이 5세션 동안 결손으로 잡힐 수 있음 — 문턱 여유 51종목 안).
- 수준은 WARN 그대로. 값 = `{expected, got, ratio, distinct_ok, requested}`.
- 테스트: 이력 5세션 + 판정일 픽스처로 pass · 결손 3% fail · 이력 없음은 옛 판정.

## 4. T-K3 — 오전 원장 체인 단계 격리

- `scripts/daily_ledger.sh:73-80` 의 `&&` 연쇄를 풀어 **소스마다 독립 실행**한다: `kiwoom fetch` · `kis credit` · (`dart` → `dart company gap`).
  `dart company gap` 만 `dart` 성공에 묶는다(새 corp 목록이 스윕 결과에 달려 있다).
- 순서는 그대로 — KIS 가 07:00(v3 토큰 재발급) 전에 끝나야 한다(09-29 실측: KIS 06:13→06:41, DART 06:41→07:15).
- `FAILED` 는 실패 단계를 **모두** 모은다("kis credit(rc=2), dart(rc=2)"). 하나라도 있으면 지금처럼 crit 알림 · 런로그 `failed` · exit 2
  (→ 같은 D 는 다음 날 다시 돈다. T-K1 뒤로는 재실행이 거짓 실패하지 않는다).
- 테스트 가능하게 루트만 환경변수로 뺀다: `ROOT="${QL_LEDGER_ROOT:-/home/kael/quant-ledger}"`(운영 기본값 불변).
- **신규 테스트** `tests/test_daily_ledger_sh.py`: 임시 루트에 대역 `.venv/bin/python`·`scripts/*.sh` 를 두고
  ① KIS rc=2 → DART·company gap 이 실행되고 exit 2, crit 제목에 `kis credit(rc=2)` ② 전부 rc=0 → exit 0·info 알림
  ③ DART rc=2 → company gap 미실행, KIS 는 실행 ④ 실패 두 개 → `FAILED` 에 둘 다.

## 5. 검증 게이트

| 게이트 | 기준 | 방법 |
|---|---|---|
| **GK1** | `uv run --project backend pytest database/tests -q` 전부 통과(기존 + 신규) · ruff · pyright 0 | 로컬 |
| **GK2** | 배포 뒤 서버 `ledger_health --date 20260929` 재계산: `kis.credit.rows` **pass**, 기대 2,530 · 받음 2,522 · 비율 0.9968 | 서버 읽기 전용 |
| **GK3** | 서버 원장으로 새 판정 재생(08-10~09-23 판정일 32개) 전부 pass, 최저 0.9968 | 일회성 스크립트 `logs/verify_kis_gate.py` |
| **GK4** | 10-01(목) 06:00 실운영: `kis credit` rc=0 **또는** 실패해도 `dart`·`dart company gap` 실행 로그 존재 · 런로그 판정이 단계 결과와 일치 | 로그 확인 |

## 6. 실행 순서 · 일정

1. T-K1·T-K2(파이썬, 파일 겹침 없음 → 한 에이전트 또는 직접) → T-K3(셸 + 테스트).
2. GK1 → 커밋 → `scripts/deploy.sh --apply --allow-branch feat/v3-merge` (18:05 저녁 수집 전, 원장 락이 비어 있을 때).
3. GK2·GK3 즉시, GK4 는 10-01 아침.
4. 크론 변경 없음(스크립트 내용만 바뀐다).

## 7. 범위 밖 (기록만)

- 저녁 체인(`daily_evening.sh`)은 이미 소스별 독립 실행(09-23 키움 rc=1 · DART rc=2 · WISE rc=0 이 각각 돌았다) — 변경 없음.
- 09-23 저녁 키움 `JSONDecodeError`(응답이 JSON 아님) · DART `periodic_followed unresolved=1` — 일회성, 09-28 저녁부터 rc=0.
- DART `rcept_dt` 가 `rcept_no` 날짜보다 늦은 23행(장 마감 뒤 제출 → 다음 영업일 게재) — 가용 시점이 표기보다 이르므로 look-ahead 아님.

## 8. 실행 결과 (2026-09-30 13:10 KST, 커밋 204c5d87, 배포 rev 204c5d87)

- **GK1 통과** — `database/tests` 1,681 passed · ruff · pyright 0. 재현 테스트 3개(연휴 재실행 · 신용잔고 없는 종목 · 3% 결손)는
  수정 전 코드에서 서버 로그와 같은 이유로 실패하는 것을 먼저 확인했다(`n_rows(this run)=0 … dup_skipped=4`).
  셸 테스트 4개(`tests/test_daily_ledger_sh.py`)는 옛 스크립트가 루트를 고정해 수정 전 재현은 불가 — 새 동작만 검증.
- **GK2 통과** — 서버 `ledger_health --date 20260929 --out logs/verify_kis_gk2`(운영 리포트 비덮어쓰기):
  `kis.credit.rows` pass `{expected 2530, got 2522, ratio 0.9968, distinct_ok True, requested 2655}`.
- **GK3 비율 판정 통과, 종목당 1행 하위 검사는 재생에 부적합** — `logs/verify_kis_gate.py`: 판정일 32개 비율 최저 0.9968 · 최고 0.9996 ·
  판정 1회 3.8초. 다만 원장 전체 기준 "종목당 1행" 이 오래된 판정일 31개에서 위반으로 나왔다. 원인: KIS 가 과거 `deal_date` 행의
  `stck_prpr` 를 뒤늦게 바꿔 보내고(예: 0099X0 의 09-22 행 2,180 → 2,300, 09-28·09-29 수집), 원장은 값이 다른 행을 정정으로
  보존한다(DEFECT-A-03 설계). 그래서 날짜가 오래될수록 행이 쌓인다(08-10 은 +49). 운영 판정은 영향 없음 — 수집기는 이번 런
  적재분(`since`)으로, 건전성 검사는 막 들어온 D−2 로 보며 옛 코드도 같은 논리였다(09-23 은 2,522 = 2,522).
- **GK4 대기** — 10-01(목) 06:00 실운영 로그.
