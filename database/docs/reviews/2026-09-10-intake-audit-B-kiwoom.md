# 키움 원장 검수 — 2026-09-09 적재분 (검수일 09-10)

- 대상: `~/quant-ledger/data/raw/kiwoom.db` 의 `dt=20260909` 행 (fetch run_id=20 · merge run_id=23)
- 대조: `~/quant-ledger/data/raw/krx.db` `krx_stk_bydd_trd` + `krx_ksq_bydd_trd` (`bas_dd_req=20260909`, 합 2,765행, `ISU_CD` = 6자리 단축코드로 키움 `ticker` 와 직접 조인 가능)
- 접근 방식: 전부 `mode=ro` URI. 쓰기·API 호출·프로세스 조작 없음.

---

## 1. 검사 결과 표

| # | 항목 | 판정 | 수치·근거 |
|---|---|---|---|
| 1 | TR 별 `dt=20260909` 행수 · 직전 5거래일 비교 | 통과 | ka10008/10060/20068 각 **2,650** (09-04·07·08 도 2,650, 08-28~09-03 은 2,649). ka10014 **2,246** (직전 5세션 2,201~2,275 범위 내) |
| 1b | 본 테이블에 `dt > 20260909` 행 잔존 | 통과 | 4개 TR 모두 `MAX(dt)=20260909`. merge 가 `dropped_future=2651` 로 ka10008 의 `dt=20260910` 행을 전량 제거 (incoming 에는 2,651행 남아 있음 = 설계대로) |
| 2 | 요청 2,655 vs 적재 2,650 = 5종목 | **원인 규명 완료** | 5종목 = 신규상장 1 (`0197V0`) + 폐지 4 (`082640`·`096610`·`269620`·`471050`). 상세는 §2 발견 F-1·F-2 |
| 3 | ka10008 stale 262건의 성격 | 통과 | 전 컬럼(`close_pric`·`trde_qty`·`poss_stkcnt`·`wght`) 동일 = **108건, 전부 `trde_qty=0`**. 거래가 있으면서 전 컬럼 동일 = **0건** → 08-24 형 오염 아님. `poss_stkcnt` 만 동일 = 268건(그 중 거래 있음 158건, 정상) |
| 3b | ka10008 `close_pric`·`trde_qty` KRX 전수 대조 | 통과 | 2,650/2,650 종가 일치, 2,650/2,650 거래량 일치. 불일치 0. KRX 에 없는 키움 티커 0 |
| 3c | `poss_stkcnt > LIST_SHRS` · `wght` 범위 | 통과 | 초과 0건. `wght`·`limit_exh_rt` 0~100 밖 0건. `frgnr_limit <> LIST_SHRS` 30건은 전부 방송·통신·신문 외국인지분한도 업종(정상) |
| 4 | ka10060 투자자 합계 정합 | 통과 | `ind_invsr+frgnr_invsr+orgn+etc_corp+natfor` 잔차 분포 = 0(1,971) / −1(352) / +1(326) / +2(1). **범위 −1~+2, 반올림 잔차** |
| 4b | `orgn` = 세부 8개 합 | 통과 | 일치 2,302/2,650, 나머지도 잔차 −2~+2 (반올림) |
| 4c | `acc_trde_prica` 단위 확정 | **주의(원천 특성)** | KRX `ACC_TRDVOL` 과 **2,650/2,650 완전 일치**. `ACC_TRDVAL` 과 일치한 118건은 전부 무거래(`val=0`) 우연. → 컬럼명은 "거래대금"이나 실제 내용은 **거래량(주)**. stage(`rules_kiwoom.py:58`)가 이미 `volume_shr` 로 매핑 중 — 하류 오류 없음 |
| 4d | 투자자 순매수 단위 | 확정 = **백만원** | `MAX(|ind|,|frgnr|,|orgn|) > ACC_TRDVAL/1e6` 위반 **0건**, `> ACC_TRDVOL/1000` 위반 1,158건. 예: 298040 효성중공업 종가 2,931,000원·거래량 29,659주·거래대금 87,048백만원, `ind_invsr=-18,455` (천주 해석 시 거래량의 622배 → 불가) |
| 4e | `cur_prc` vs KRX 종가 | 통과 | 2,650/2,650 |
| 5 | ka20068 항등식 | 통과 | `irds = cntrcnt − rpy` **2,650/2,650**. `rmnd(D) = rmnd(D−1) + irds` **2,650/2,650**. 위반 0 |
| 5b | 음수 잔고 · `rmnd > LIST_SHRS` | 통과 | `rmnd`·`remn_amt`·`cntrcnt`·`rpy` 음수 0건. `rmnd > LIST_SHRS` 0건 |
| 5c | `remn_amt` 단위 | 확정 = **백만원** | `remn_amt ≈ rmnd × 종가 / 1e6` 오차 ≤ 1.0 이 2,650/2,650 (최대 편차 0.5 = 반올림). 예: 011200 HMM `rmnd`=15,919,928 × 20,850 / 1e6 = 331,930.5 vs `remn_amt`=331,930 |
| 6 | ka10014 미수록 404종목의 성격 | 통과(누락 아님) | 유니버스 2,650 − 수록 2,246 = **404**. incoming `_kw_incoming_ka10014` 의 `dt=20260909` 고유 티커 = **2,246** (본 테이블과 동일, merge 손실 0). 404종목의 incoming 최신 `dt` 는 20260908 이하 → **원천이 0행 반환 = 그날 공매도 없음**. 수록 행 중 `shrts_qty=0` 은 0건 |
| 6b | 09-08 有 / 09-09 無 | 통과 | 100종목 (역방향 신규 71종목). 예: 000040 KR모터스(거래량 52,935), 036420 콘텐트리중앙(11,103) — 전부 KRX 시세는 있고 공매도만 없음 |
| 6c | `shrts_qty > trde_qty` | 통과 | 위반 0건 |
| 6d | `trde_wght` 검산 | 통과 | `100×shrts_qty/trde_qty` 와 ±0.05 내 일치 2,246/2,246 |
| 6e | `shrts_trde_prica` 단위 | 확정 = **천원** | `prica×1000/shrts_qty ≈ shrts_avg_pric` 이 2,084/2,246 (1% 허용). 원(0/2,246)·백만원(0/2,246) 은 전량 불일치. 예: 298040 `14,696,546천원 / 5,018주 = 2,928,766원` = `shrts_avg_pric` 정확 일치 |
| 6f | `ovr_shrts_qty` 정체 | 확정 = **누적 공매도량** | `ovr(D) − ovr(D−1) = shrts_qty(D)` 가 **2,175/2,175** 완전 일치. `shrts_qty`·`trde_qty` 와는 0건 일치. `equity/rules_s09.py:39` 의 "샤드 요청창 시작부터의 누적" 기술과 일치 |
| 6g | ka10014 종가·거래량 KRX 대조 | 통과 | 종가 2,246/2,246, 거래량 2,246/2,246 |
| 7 | 값 형식 (`''`·NULL·부호만·비숫자) | 통과 | 4개 TR `dt=20260909` 전 숫자 컬럼에서 **전부 0건**. 부호 접두(`+1181`/`-5200`)는 존재하나 `CAST` 가 정상 파싱(`CAST('+1181' AS INTEGER)=1181`) |
| 8 | `collected_at` 분포 | **미흡** | 4개 TR·전 행이 **단일 값 `2026-09-09T21:04:51`**(UTC) = 09-10 06:04:51 KST. 실제 fetch 는 21:04:49~21:20:33 UTC 에 걸쳐 실행. 행 단위 수집 시각이 아니라 배치 상수 → §2 발견 F-5 |
| 9 | 마스터 `snap_date=20260910` | 통과 | 4,309행(코스피 2,486 · 코스닥 1,823), `collected_at` 2026-09-09T21:00:01Z = 09-10 06:00 KST. 09-09 대비 **추가 1건(`0197V0`), 삭제 0건** |
| 9b | 보통주 규칙 커버리지 | 통과 | 규칙 적중 2,651. KRX 09-09 종목 중 규칙 밖 115건은 전부 우선주(6번째 자리 5=80, K=20, 7=9, L=4, 9=1) + 뮤추얼펀드 1. **마스터에 없는 KRX 보통주 0건**. `upSizeName<>''` 이면서 6번째 자리≠'0' 인 행 0건(규칙 두 조건이 충돌하지 않음) |
| 9c | state / orderWarning 분포 | 기록 | state: 증거금100% 1,102 · 증거금40%\|담보대출\|신용가능 895 · 증거금30%\|… 265 · 관리종목 172 · 증거금100%\|거래정지 34 등. orderWarning: 0(2,628) · 5(14) · 2(7) · 4(1) · 3(1) |
| 9d | 거래정지 34종목 수집 여부 | 통과 | ka10008 34/34, ka10060 34/34, ka10014 1/34(공매도만 없음) |
| 10 | incoming 4개 현황 | 설계대로 | in_ka10008 132,176행 / 2,651티커 / dt 20260701~20260910 (`dt>D` 2,651행) · in_ka10014 904,562 / 2,591 / 20080811~20260909 · in_ka10060 263,610 / 2,650 / 20260415~20260909 · in_ka20068 263,610 / 2,650 / 20260415~20260909. merge 는 incoming 을 비우지 않음 |

---

## 2. 발견

### F-1 (high) 폐지 직전 종목의 마지막 거래 구간이 원장에 영구 결측

- **무엇이**: `ka10008_foreign_holdings` / `ka10060_investor_flows` / `ka20068_lending_balance` / `ka10014_short_selling` 의 `dt` 축. 폐지 4종목이 KRX 에는 시세가 있는 구간에서 키움 원장에는 행이 없다.
- **몇 건**: 3종목 × 최대 7세션 (`ka10008` 기준). `ka10060`·`ka20068` 은 `MAX(dt)=20260820` 이라 결측이 3세션 더 길다.

| 종목 | 키움 `ka10008` MAX(dt) | KRX 마지막 시세일 | 결측 세션 | 결측 구간의 KRX 거래 |
|---|---|---|---|---|
| 096610 알에프세미 | 20260824 | 20260902 | **7** (08-25~09-02) | 정리매매. 종가 141→139→115→101→98→111→195, 일평균 거래량 4.0M주 |
| 471050 대신밸런스제17호스팩 | 20260824 | 20260831 | **5** (08-25~08-31) | 거래량 140,268 / 13,332 / 237,983 / 24,732 / 176,821주 |
| 082640 동양생명 | 20260824 | 20260828 | **4** (08-25~08-28) | 거래정지 유지(종가 8,250 고정, 거래량 0) |
| 269620 시스웍 | 20260824 | 20260824 | 0 | 결측 없음 |

- **예시 종목 3개**: `096610` 알에프세미(코스닥), `471050` 대신밸런스제17호스팩(코스닥), `082640` 동양생명(거래소)
- **에러 위치**: 데이터 계보. 백필(`src/backfill_kw.py`)이 `dt=20260824` 에서 멈췄고, 08-25~09-08 구간은 09-09·09-10 일일 증분이 API 응답 이력(`ka10008` cap=50영업일)으로 사후 충전했다. 이때 키움은 이미 폐지된 종목에 **0행**을 돌려주므로(`_kw_incoming_*` 에 이 4종목 행 0건 — `0197V0` 제외) 충전이 불가능하다.
- **추정 원인**: 수집기 버그가 아니라 **백필 종료 시점과 일일 증분 개시 시점 사이의 15세션 공백** + 키움 API 의 폐지종목 조회 불가 특성이 겹친 결과.
- **하류 영향**: `stg_foreign_daily`·`stg_flow_daily_kiwoom`·`stg_lending_daily`·`stg_short_daily_kiwoom` 이 이 구간에서 3종목을 잃는다. equity 층에서 폐지 직전 정리매매 구간(096610 은 종가가 2,965→141 로 −95%)이 통째로 빠지면 **생존편향으로 수익률이 과대추정**된다. 다만 KRX 원장에는 가격·거래량이 남아 있으므로 가격축은 복구 가능하고, 외국인·투자자·대차·공매도 축만 영구 결측이다.
- **재현 질의**: §4 Q-F1

### F-2 (low) `dt=20260909` 행이 없는 5종목은 전부 정상 사유

- **무엇이**: 요청 유니버스 2,655 − 적재 2,650 = 5.
- **내역**:
  - `0197V0` 엔에이치스팩34호 — `regDay=20260910`, 마스터 `snap_date=20260910` 에서 **신규 추가**. `_kw_incoming_ka10008` 에 `dt=20260910` 1행만 존재하고 merge 가 `dropped_future` 로 정상 제거. 09-09 에는 상장 전이므로 데이터가 없는 것이 옳다.
  - `082640`·`096610`·`269620`·`471050` — `data/daily/universe_kw.json` 의 grace 4종목. 전부 상장폐지되어 키움이 0행을 반환. F-1 참조.
- **판정**: merge·fetch 버그 아님. `dropped_future=2651` = 2,650(정상 종목의 `dt=20260910` 행) + 1(`0197V0`) 로 산술이 정확히 맞는다.

### F-3 (mid) 마스터에서 사라진 종목을 grace 에 넣는 코드 경로가 없다

- **무엇이**: `src/daily/universe.py:80-104` `requested()`.
- **상황**: 상태 파일 `data/daily/universe_kw.json` 이 이미 존재하는 정상 운영 상태(`first_run=False`).
- **인풋**:
  1. 종목 X 가 `snap_date=N` 마스터에 있고, 그날 정리매매 마지막 거래를 한다.
  2. 다음날 `snap_date=N+1` 마스터에서 X 가 사라진다.
  3. `N+1` 06:00 fetch 가 `requested()` 를 호출한다 (수집 대상은 `D=N`).
- **에러 위치**: `src/daily/universe.py:85-88` — `grace[t] = {...}` 대입이 **`if first_run:` 블록 안에만 존재**한다. `grep -n "grace\[" src/daily/universe.py` → 87행 단 하나. `prev_today − today` 를 grace 에 추가하는 분기가 없어, X 는 `today` 에도 grace 에도 없으므로 **그 즉시 요청 유니버스에서 탈락**한다. 함수 docstring(`universe.py:5-8`)이 약속한 "마스터에서 사라진 종목은 `grace_days` 거래일 유예" 는 첫 실행 시드(`data/jsonl/tickers.txt`) 경로로만 우연히 성립하고 있다.
- **위험성**: X 의 마지막 거래일(`D=N`) 데이터가 수집되지 않는다 → F-1 과 동일한 형태의 폐지 직전 결측이 앞으로도 반복된다. 현재 grace 에 들어 있는 4종목은 09-09 첫 실행 때 시드에서 채워진 것이라 이 버그를 가리고 있을 뿐, 만료되면 다시 노출된다.
- **완화 요인(실측)**: 09-01~09-10 사이 마스터 이탈 3건의 이탈 지연은 2~4세션이었다 (082640: KRX 마지막 08-28 → 이탈 snap 20260904 / 471050: 08-31 → 20260905 / 096610: 09-02 → 20260909). 지연이 1세션 이상이면 우연히 손실이 없다. 즉 **결정성이 없는 방식으로 위험이 상시 존재**한다.
- **하류 영향**: F-1 과 동일 (생존편향).

### F-4 (mid) grace `missing_days` 가 거래일이 아니라 `requested()` 호출 횟수를 센다

- **무엇이**: `src/daily/universe.py:93-103` 의 유예 카운터. 상태 파일 실측값이 `missing_days: 3`, `last_seen: "20260909"`.
- **상황**: `data/daily/universe_kw.json` 을 **kw_daily 와 kis_daily 가 공유**한다 (`kw_daily.py:622-624`, `kis_daily.py:474-475` 둘 다 `data/daily/universe_kw.json`).
- **인풋**: `daily_run.db` 의 실행 로그가 그대로 증거다.
  1. run_id=2 `kis_credit` 2026-09-09T07:10Z — `requested=2627` (first_run, `missing_days=0`)
  2. run_id=17 `kiwoom_fetch` 2026-09-09T09:55Z — `universe=2627` (→ 1)
  3. run_id=20 `kiwoom_fetch` 2026-09-09T21:04Z — `universe=2655` (→ 2)
  4. run_id=21 `kis_credit` 2026-09-09T21:20Z — `requested=2655` (→ 3)
- **에러 위치**: `src/daily/universe.py:96` — `missing = prev_missing + (0 if first_run else 1)`. 호출 1회마다 +1 이다. 소비자가 2개(kw fetch, kis credit)이므로 하루 2 증가 → `GRACE_DAYS=5` 가 실효 **2.5일**로 줄어든다. 재실행·수동 실행이 섞이면 더 짧아진다.
- **추가**: `last_seen` 은 87행에서 한 번 쓰이고 이후 갱신되지 않는다. 실측값이 전 4종목 모두 `20260909`(= 파이프라인 첫 실행 스냅샷 날짜)인데, 실제 마스터 마지막 등장일은 082640=20260903 · 471050=20260904 · 096610=20260908 · 269620=20260901 이전으로 서로 다르다. 따라서 만료 판정 `if mx >= last_seen`(`universe.py:101`)은 "그 종목의 마지막 거래일 데이터를 받았는가" 가 아니라 "**파이프라인 첫 실행일 이후 데이터가 있는가**" 를 묻고 있다. 4종목 모두 `ka10008.MAX(dt)=20260824 < 20260909` 이므로 만료가 영원히 안 되고 grace 에 고착된다(요청 4콜×4TR=16콜/일 낭비, 데이터 이득 0).
- **하류 영향**: 직접적인 데이터 오류는 없다. 유예 창이 의도의 절반이라 F-3 의 안전망이 더 얇아지고, 폐지종목이 유니버스에 영구 잔류해 `ledger_health` 의 분모(`n_requested`)를 부풀린다.

### F-5 (mid) 매일 merge 가 전체 이력을 재기록하고 `collected_at` 을 덮어쓴다

- **무엇이**: `src/daily/kw_daily.py:549` `INSERT OR REPLACE INTO "{spec.table}"` (fetch 쪽도 `kw_daily.py:311` 동일).
- **몇 건**: 09-10 실행 1회로 `dt < 20260909` 인 과거 행이 재기록된 규모 —

| 테이블 | 재기록 행수 | 재기록 dt 범위 |
|---|---|---|
| `ka10014_short_selling` | 902,316 | 20080811 ~ 20260908 |
| `ka10060_investor_flows` | 260,960 | 20260415 ~ 20260908 |
| `ka20068_lending_balance` | 260,960 | 20260415 ~ 20260908 |
| `ka10008_foreign_holdings` | 126,875 | 20260701 ~ 20260908 |

- **에러 위치**: `kw_daily.py:304` 의 주석("`INSERT OR REPLACE` — PK 로 과거 정정이 자연 반영")대로 **의도된 설계**다. 문제는 부작용 두 가지다.
  1. 과거 행의 `collected_at` 이 매일 오늘 값으로 덮인다 → **최초 관측 시각이 소실**되어 "이 값을 언제 처음 봤는가"를 되물을 수 없다. 08-24 오염 같은 사건의 사후 추적이 불가능해진다.
  2. 원천이 과거 값을 조용히 정정하면 **차이 기록 없이** 원장이 바뀐다. merge 로그는 `merged=1561307` 처럼 행수만 남기고 변경된 값의 수를 세지 않는다.
- **위험성**: 재현성 손실. 어제 돌린 백테스트와 오늘 돌린 백테스트가 같은 과거 구간에서 다른 값을 쓸 수 있는데, 그 사실을 알 방법이 원장 안에 없다. `ka10014` 는 하루 90만 행(2008년치까지)을 다시 쓰므로 노출 면적이 가장 크다.
- **하류 영향**: stage 의 MANIFEST 해시가 조용히 바뀔 수 있다. equity 층의 `_pinned` 스냅샷과 원장이 불일치해도 감지되지 않는다.

### F-6 (mid) `collected_at` 이 행 단위 시각이 아니라 배치 상수

- **무엇이**: 4개 TR 의 `collected_at`.
- **몇 건**: `dt=20260909` 전 행 (2,650×3 + 2,246 = 10,196행) + F-5 의 재기록 과거 행 전부가 **단일 값 `2026-09-09T21:04:51`**. `MIN=MAX`, `COUNT(DISTINCT)=1`.
- **에러 위치**: fetch 시작 시 한 번 만든 타임스탬프를 전 행에 붙인다. `daily_run.db` 의 run_id=20 은 `started=21:04:49Z`, `ended=21:20:33Z` 로 15분 44초에 걸쳐 실행됐다.
- **위험성**: 낮음(미관·기록). 부분 실패·재시도·rate-limit 백오프가 어느 티커에서 일어났는지 원장만으로 판별할 수 없다. 검수 항목 8의 "06:04~06:20 분포" 는 원장에서 확인 불가능하며 `daily_run.db` 로만 확인된다.

### F-7 (mid) 09-09 시점 유니버스가 2,627 로 축소돼 있었고 현재 DB 로 재현되지 않는다

- **무엇이**: `daily_run.db` run_id=2·17 의 `universe=2627` vs run_id=20·21 의 `universe=2655`.
- **상황**: 유니버스 = 마스터 규칙 적중 + grace(4). 따라서 09-09 실행 시점의 규칙 적중은 **2,623** 이었다는 뜻이다.
- **모순**: 현재 DB 의 마스터 규칙 적중은 20260901=2,652 / 20260907=2,651 / 20260908=2,651 / **20260909=2,650** / 20260910=2,651 이다. **2,623 을 내는 스냅샷이 존재하지 않는다.** 스냅샷은 시장별로도 완전하다(20260909: 코스피 2,486 + 코스닥 1,822, `collected_at` 단일값 2026-09-08T21:00:01Z = 09-09 06:00 KST — run_id=2 보다 10시간 앞섬).
- **관측된 귀결**: run_id=18(09-08 merge)은 `kw_rows=2623` 을 썼는데 현재 `ka10008` 의 `dt=20260908` 행수는 **2,650** 이다. 차이 27행은 09-10 merge 가 F-5 의 이력 재기록으로 사후에 채운 것이다. 즉 **09-09 하루 동안 27종목이 요청 유니버스에서 조용히 빠져 있었고**, 마침 `ka10008` 응답이 50영업일 이력을 함께 주는 덕에 하루 뒤 자동 복구됐다.
- **위험성**: `ka10008`(cap 50) · `ka10060`/`ka20068`(cap 100, 20260415~) 은 이력 폭이 넓어 복구됐지만, 유니버스 축소가 cap 을 넘는 기간 지속되면 복구되지 않는다. 원인이 규명되지 않아 재발 여부를 예측할 수 없다.
- **미확인**: §3 참조.

### F-8 (low) `shrts_trde_prica` 의 천원 반올림이 소량 공매도에서 평균가를 깨뜨린다

- **무엇이**: `ka10014_short_selling.shrts_trde_prica`(천원, 정수) ÷ `shrts_qty`.
- **몇 건**: `dt=20260909` 2,246행 중 **162행**에서 `prica×1000/qty` 가 `shrts_avg_pric` 대비 1% 초과 이탈. 그 162행의 `shrts_qty` 는 전부 ≤ 66주이고 107행은 ≤ 10주.
- **예시 종목 3개**: `254120`(shrts_qty 2, prica 3천원 → 계산 1,500원 vs `shrts_avg_pric` 1,040원, +44.2%), `340360`(qty 3, prica 4 → 1,333 vs 1,003, +32.9%), `464580`(qty 1, prica 2 → 2,000 vs 1,524, +31.2%).
- **추정 원인**: 수집기 버그가 아니라 **원천의 단위 절삭**. 천원 단위 정수라 1~2주 공매도는 유효숫자가 1자리도 안 남는다.
- **하류 영향**: `stg_short_daily_kiwoom.shrts_trde_prica_krw`(stage 가 ×1e3) 를 소량 공매도 종목의 평균 체결가 산출에 쓰면 최대 44% 틀린다. `shrts_avg_pric` 을 쓰면 정확하다. 금액 축 자체는 `shrts_qty` 가 작아 절대 오차가 미미하다.

### F-9 (low) 보통주 유니버스에 리츠 23 · 인프라투자금융 2 가 포함된다

- **무엇이**: `src/daily/universe.py:41-43` 의 규칙 `upSizeName<>'' OR (marketName IN ('거래소','코스닥') AND substr(code,6,1)='0')`.
- **몇 건**: `snap_date=20260910` 규칙 적중 2,651 의 `marketName` 분포 = 코스닥 1,820 · 거래소 806 · **리츠 23 · 인프라투자금융 2**. 25종목이 첫 번째 조건(`upSizeName<>''`)만으로 통과한다.
- **에러 위치**: `universe.py:5-8` docstring 은 "ETF·ETN 은 `marketName` 이 다르다" 만 언급하고 리츠·인프라투자금융은 다루지 않는다.
- **위험성**: 낮음. 리츠는 상장주식이라 포함이 타당할 수 있으나 **의도인지 부작용인지 문서에 없다**. 퀀트 유니버스 정의가 "보통주"인지 "상장 지분증권"인지에 따라 팩터 계산 모집단이 25종목만큼 달라진다.

### 발견 없음 (명시)

아래는 위반을 찾지 못했다. 검사 범위와 표본 수는 §1 에 있다.

- `dt=20260909` 4개 TR 의 **숫자 컬럼 결측·비숫자·부호만** — 0건
- 본 테이블의 **`dt > 20260909` 잔존** — 0건
- **KRX 대조**: ka10008·ka10014 종가/거래량, ka10060 `cur_prc` — 전부 100% 일치, 불일치 0건
- **ka20068 항등식 2종** — 위반 0건, 음수 0건, `rmnd > LIST_SHRS` 0건
- **ka10008 stale 오염(08-24 형)** — 거래가 있는데 전 컬럼 동일한 행 0건
- **ka10014 merge 손실** — incoming 2,246티커 = 본 테이블 2,246티커, 손실 0
- **`shrts_qty > trde_qty`** — 0건
- **마스터 보통주 규칙이 놓친 KRX 보통주** — 0건
- **09-09 유니버스 종목의 최근 12세션 결측** — 386380 스카이랩스 8세션이 유일한데 `regDay=20260904`·KRX 첫 시세일 20260904 로 **신규상장이라 정상**

---

## 3. 확인하지 못한 것과 이유

1. **F-7 의 근본 원인** — 09-09 실행이 본 마스터 규칙 적중 2,623 을 현재 DB 로 재현할 수 없다. `ka10099_stock_master` 는 09-01 이 최초 스냅샷이고 그 이후 어떤 날도 2,623 이 아니다(2,650~2,652). 시장별 행수·`collected_at` 도 완전하다. 가능한 설명(당시 다른 DB 경로를 봤다 / 스냅샷이 사후 보정됐다 / 09-09 는 P2 셋업 중이라 DB 가 다른 상태였다)을 원장만으로는 가릴 수 없다. `logs/` 의 09-09 실행 stdout 을 봐야 하는데 검수 범위(읽기 전용 DB 질의) 밖으로 판단해 열지 않았다.
2. **09-09 에 유니버스에서 빠졌던 27종목의 식별** — F-5 의 `INSERT OR REPLACE` 가 `collected_at` 을 전부 덮어써 "09-09 merge 가 쓴 행"과 "09-10 merge 가 채운 행"을 원장에서 구분할 수 없다. 산술(2,650 − 2,623 = 27)로 존재만 확정했다.
3. **404 종목의 공매도 0 이 "실제 0" 인지 "금지·정지" 인지** — KRX 원장에 공매도 테이블이 없어 제3자 대조가 불가능하다. 키움 incoming 응답이 0행이라는 사실까지만 확인했다(수집 손실은 아님).
4. **`ovr_shrts_qty` 의 누적 기산일** — 증분(`ovr(D)−ovr(D−1) = shrts_qty(D)`)이 2,175/2,175 성립함은 확정했으나, 어느 날부터의 누적인지는 전 이력 스캔이 필요해 확인하지 않았다. `equity/rules_s09.py:39` 는 "샤드 요청창 시작부터"로 기술한다.
5. **`acc_trde_prica` 가 원천 명세상 거래량인지, `amt_qty_tp=1` 조합의 부작용인지** — API 문서 대조가 필요하고 API 호출은 금지 범위다. 데이터상 KRX `ACC_TRDVOL` 과 2,650/2,650 일치라는 사실만 기록한다.
6. **stage·equity 층으로의 실제 전파** — 본 검수는 `kiwoom.db` 원장에 한정했다. `stg_*` 테이블이 09-09 분을 이미 반영했는지, F-1 결측이 equity 유니버스에 어떻게 나타나는지는 확인하지 않았다.

---

## 4. 사용한 질의 (재현용)

전부 `cd ~/quant-ledger && timeout 900 sqlite3 "file:data/raw/kiwoom.db?mode=ro"` 로 실행. KRX 는 URI ATTACH 가 정상 동작한다.

```sql
-- 공통: KRX 09-09 통합 뷰 (ISU_CD 는 6자리 단축코드)
ATTACH DATABASE 'file:data/raw/krx.db?mode=ro' AS krx;
CREATE TEMP VIEW k AS
  SELECT ISU_CD cd, ISU_NM nm, CAST(TDD_CLSPRC AS INTEGER) cls,
         CAST(ACC_TRDVOL AS INTEGER) vol, CAST(ACC_TRDVAL AS INTEGER) val,
         CAST(LIST_SHRS AS INTEGER) shrs
    FROM krx.krx_stk_bydd_trd WHERE bas_dd_req='20260909'
  UNION ALL
  SELECT ISU_CD, ISU_NM, CAST(TDD_CLSPRC AS INTEGER), CAST(ACC_TRDVOL AS INTEGER),
         CAST(ACC_TRDVAL AS INTEGER), CAST(LIST_SHRS AS INTEGER)
    FROM krx.krx_ksq_bydd_trd WHERE bas_dd_req='20260909';
```

```sql
-- Q-1: TR 별 dt 분포 + 미래 행 (테이블마다 1회 풀스캔)
SELECT dt, COUNT(*) n FROM ka10008_foreign_holdings WHERE dt>='20260828' GROUP BY dt ORDER BY dt;
SELECT MAX(dt) FROM ka10008_foreign_holdings;   -- 4개 TR 모두 20260909
```

```sql
-- Q-2: 요청 유니버스 − 적재 = 5종목
SELECT m.code, m.name, m.marketName, m.state, m.orderWarning, m.regDay
  FROM ka10099_stock_master m
 WHERE m.snap_date='20260910'
   AND (m.upSizeName<>'' OR (m.marketName IN ('거래소','코스닥') AND substr(m.code,6,1)='0'))
   AND m.code NOT IN (SELECT ticker FROM ka10008_foreign_holdings WHERE dt='20260909');
-- → 0197V0 (regDay=20260910, 신규상장) 1건.
-- 나머지 4건은 data/daily/universe_kw.json 의 grace 항목 (마스터 밖).
```

```sql
-- Q-3: ka10008 stale 성격 — 전 컬럼 동일 108건, 전부 무거래
SELECT COUNT(*) n_all_equal,
       SUM(CASE WHEN CAST(a.trde_qty AS INTEGER)=0 THEN 1 ELSE 0 END) qty0,
       SUM(CASE WHEN CAST(a.trde_qty AS INTEGER)>0 THEN 1 ELSE 0 END) qty_pos
  FROM (SELECT * FROM ka10008_foreign_holdings WHERE dt='20260909') a
  JOIN (SELECT * FROM ka10008_foreign_holdings WHERE dt='20260908') b USING(ticker)
 WHERE a.poss_stkcnt=b.poss_stkcnt AND a.wght=b.wght
   AND a.close_pric=b.close_pric AND a.trde_qty=b.trde_qty;   -- 108 / 108 / 0
```

```sql
-- Q-4a: acc_trde_prica 단위 확정 → 거래량(주)
SELECT COUNT(*) n,
  SUM(CASE WHEN CAST(a.acc_trde_prica AS INTEGER)=k.vol THEN 1 ELSE 0 END) eq_VOL,
  SUM(CASE WHEN CAST(a.acc_trde_prica AS INTEGER)=k.val THEN 1 ELSE 0 END) eq_VAL
  FROM ka10060_investor_flows a JOIN k ON k.cd=a.ticker WHERE a.dt='20260909';
-- 2650 / 2650 / 118  (118 은 전부 val=0 인 무거래 종목)

-- Q-4b: 순매수 단위 확정 → 백만원
SELECT SUM(CASE WHEN mx > k.vol/1000.0+1 THEN 1 ELSE 0 END) gt_vol_k,      -- 1158 위반
       SUM(CASE WHEN mx > k.val/1000000.0+1 THEN 1 ELSE 0 END) gt_val_mil  -- 0 위반
  FROM (SELECT ticker, MAX(ABS(CAST(ind_invsr AS INTEGER)),
                          ABS(CAST(frgnr_invsr AS INTEGER)),
                          ABS(CAST(orgn AS INTEGER))) mx
          FROM ka10060_investor_flows WHERE dt='20260909') a
  JOIN k ON k.cd=a.ticker;
```

```sql
-- Q-5: ka20068 항등식 + remn_amt 단위
SELECT COUNT(*) tot,
  SUM(CASE WHEN CAST(a.rmnd AS INTEGER)
              = CAST(b.rm AS INTEGER)+CAST(a.dbrt_trde_irds AS INTEGER) THEN 1 ELSE 0 END) ok
  FROM (SELECT * FROM ka20068_lending_balance WHERE dt='20260909') a
  JOIN (SELECT ticker, rmnd rm FROM ka20068_lending_balance WHERE dt='20260908') b USING(ticker);
-- 2650 / 2650

SELECT SUM(CASE WHEN ABS(CAST(a.remn_amt AS REAL)
                       - CAST(a.rmnd AS REAL)*k.cls/1000000.0)<=1.0 THEN 1 ELSE 0 END) within1
  FROM (SELECT * FROM ka20068_lending_balance WHERE dt='20260909') a
  JOIN k ON k.cd=a.ticker;   -- 2650 / 2650 → 백만원 확정
```

```sql
-- Q-6a: ka10014 미수록 404종목이 원천 0행인지 (merge 손실 아님)
SELECT COUNT(DISTINCT ticker) FROM _kw_incoming_ka10014 WHERE dt='20260909';   -- 2246
SELECT COUNT(*) FROM (SELECT DISTINCT ticker FROM _kw_incoming_ka10014 WHERE dt='20260909') i
 WHERE i.ticker NOT IN (SELECT ticker FROM ka10014_short_selling WHERE dt='20260909');  -- 0

-- Q-6b: ovr_shrts_qty = 누적 공매도량
SELECT COUNT(*) n,
  SUM(CASE WHEN CAST(a.ovr_shrts_qty AS INTEGER)-CAST(b.ovr_shrts_qty AS INTEGER)
              = CAST(a.shrts_qty AS INTEGER) THEN 1 ELSE 0 END) eq
  FROM (SELECT * FROM ka10014_short_selling WHERE dt='20260909') a
  JOIN (SELECT * FROM ka10014_short_selling WHERE dt='20260908') b USING(ticker);  -- 2175 / 2175

-- Q-6c: shrts_trde_prica 단위 = 천원
SELECT SUM(CASE WHEN ABS(CAST(shrts_trde_prica AS REAL)*1.0/q      - a)<=a*0.01+1 THEN 1 ELSE 0 END) unit_won,   -- 0
       SUM(CASE WHEN ABS(CAST(shrts_trde_prica AS REAL)*1000.0/q   - a)<=a*0.01+1 THEN 1 ELSE 0 END) unit_1000,  -- 2084
       SUM(CASE WHEN ABS(CAST(shrts_trde_prica AS REAL)*1000000./q - a)<=a*0.01+1 THEN 1 ELSE 0 END) unit_1e6    -- 0
  FROM (SELECT shrts_trde_prica, CAST(shrts_qty AS REAL) q, CAST(shrts_avg_pric AS REAL) a
          FROM ka10014_short_selling WHERE dt='20260909' AND CAST(shrts_qty AS INTEGER)>0);
```

```sql
-- Q-7: 값 형식 (ka10008 예. 나머지 3 TR 도 같은 형태)
SELECT COUNT(*) n,
  SUM(CASE WHEN close_pric='' OR close_pric IS NULL THEN 1 ELSE 0 END) empty,
  SUM(CASE WHEN close_pric IN ('+','-') THEN 1 ELSE 0 END) signonly,
  SUM(CASE WHEN trim(close_pric,'+-0123456789.')<>''
             OR trim(poss_stkcnt,'+-0123456789.')<>''
             OR trim(wght,'+-0123456789.')<>'' THEN 1 ELSE 0 END) nonnum
  FROM ka10008_foreign_holdings WHERE dt='20260909';   -- 2650 / 0 / 0 / 0
```

```sql
-- Q-F1: 폐지 4종목의 결측 (F-1)
SELECT ticker, MAX(dt) FROM ka10008_foreign_holdings
 WHERE ticker IN ('082640','096610','269620','471050') GROUP BY ticker;   -- 전부 20260824
SELECT bas_dd_req, ISU_CD, ISU_NM, TDD_CLSPRC, ACC_TRDVOL FROM krx.krx_ksq_bydd_trd
 WHERE ISU_CD IN ('096610','471050') AND bas_dd_req>'20260824' ORDER BY ISU_CD, bas_dd_req;
SELECT COUNT(*) FROM _kw_incoming_ka10008
 WHERE ticker IN ('082640','096610','269620','471050');   -- 0 → API 가 0행 반환
```

```sql
-- Q-F5: 과거 이력 재기록 규모 (collected_at 이 오늘 값인 과거 행)
SELECT COUNT(*) n, MIN(dt), MAX(dt) FROM ka10014_short_selling
 WHERE collected_at='2026-09-09T21:04:51' AND dt<'20260909';   -- 902316, 20080811~20260908
```

```sql
-- Q-F7: 스냅샷별 규칙 적중 추이 (2,623 이 재현되지 않음)
SELECT snap_date, COUNT(*) rows_all,
  SUM(CASE WHEN upSizeName<>'' OR (marketName IN ('거래소','코스닥')
           AND substr(code,6,1)='0') THEN 1 ELSE 0 END) rule_hit
  FROM ka10099_stock_master WHERE snap_date>='20260901' GROUP BY snap_date ORDER BY snap_date;
```

```sql
-- Q-9: 규칙이 놓치는 종목 = 전부 우선주·뮤추얼펀드
SELECT CASE WHEN m.code IS NULL THEN '마스터에없음'
            WHEN m.upSizeName<>'' THEN '규모구분있음(모순)'
            WHEN m.marketName NOT IN ('거래소','코스닥') THEN 'marketName='||m.marketName
            WHEN substr(m.code,6,1)<>'0' THEN '6번째자리='||substr(m.code,6,1)
            ELSE '기타' END reason, COUNT(*) n
  FROM k LEFT JOIN (SELECT * FROM ka10099_stock_master WHERE snap_date='20260910') m ON m.code=k.cd
 WHERE k.cd NOT IN (SELECT ticker FROM ka10008_foreign_holdings WHERE dt='20260909')
 GROUP BY reason ORDER BY n DESC;
-- 6번째자리=5 (80) / K (20) / 7 (9) / L (4) / 9 (1) / marketName=뮤추얼펀드 (1). '마스터에없음' 0
```

```sql
-- Q-10: incoming 현황 (설계상 다음 fetch 까지 잔존)
SELECT '10008' t, COUNT(*), MIN(dt), MAX(dt), COUNT(DISTINCT ticker) FROM _kw_incoming_ka10008
UNION ALL SELECT '10014', COUNT(*), MIN(dt), MAX(dt), COUNT(DISTINCT ticker) FROM _kw_incoming_ka10014
UNION ALL SELECT '10060', COUNT(*), MIN(dt), MAX(dt), COUNT(DISTINCT ticker) FROM _kw_incoming_ka10060
UNION ALL SELECT '20068', COUNT(*), MIN(dt), MAX(dt), COUNT(DISTINCT ticker) FROM _kw_incoming_ka20068;
```
