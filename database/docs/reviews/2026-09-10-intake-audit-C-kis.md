# KIS 신용잔고 검수 — 2026-09-10 적재분

- 대상: `data/raw/kis.db` · `kis_credit_balance` (서버 `kael-server:~/quant-ledger`, 전부 read-only 조회)
- 대조: `data/raw/krx.db` (`krx_stk_bydd_trd`·`krx_ksq_bydd_trd`) · `data/raw/kiwoom.db` (`ka10099_stock_master` snap 20260910)
- 오늘 실행: `daily_run.db` `run_id=21` · `2026-09-09T21:20:35Z ~ 21:47:14Z` (= KST 09-10 06:20~06:47)
  `calls=2655 new_rows=2817 dup_skipped=74856 tickers=2655/2655 window=20260801~20260910 dup_pairs 0->0 failures=0`
  게이트 `deal_date=20260907 n_rows=2529 n_tickers=2529 requested=2655 ratio=0.9525`

## 1. 검사 결과 표

| # | 항목 | 판정 | 수치·근거 |
|---|------|------|-----------|
| 1 | 오늘 신규 행의 `deal_date` 분포와 정정 여부 | **정상 (정정 0건)** | 신규 2,817행 = `20260907` 2,529행 + `20260819~20260904` 288행. 288행은 **23종목 × 최대 13일**의 최초 도착이며 같은 `(req_ticker, deal_date)` 의 기존 행은 **0건** → 정정 아님. 23종목 = 외국주권 21(`900070`·`900100`·`900110`·`900120`·`900140`·`900250`·`900260`·`900270`·`900290`·`900300`·`900310`·`900340`·`950130`·`950140`·`950160`·`950170`·`950190`·`950200`·`950210`·`950220`·`950250`) + `0220W0` + `417030`. 원인: 전날 09-09 16:00 KST 수동 캐치업이 seed `data/jsonl/tickers.txt`(2,602종목, 위 23종목 전부 미포함)로 돌았고, 오늘 크론이 키움 마스터 유니버스(2,655)로 처음 돌면서 갭을 메웠다 |
| 2 | `deal_date` 별 행수·고유 종목·중복 쌍 (08-20~09-07) | **중복 0** | 08-20 2531 / 08-21 2531 / 08-24 2532 / 08-25 2532 / 08-26 2533 / 08-27 2531 / 08-28 2530 / 08-31 2530 / 09-01 2528 / 09-02 2529 / 09-03 2531 / 09-04 2529 / 09-07 2529. 모든 날짜에서 `행수 = 고유 req_ticker 수`(중복 0). 원장 전체 중복의 최대 `deal_date` = `20260818` — **08-19 이후 구간에는 중복이 없다** |
| 3 | 09-07 결측 126종목 | **원천 특성 118 + 조사 필요 8** | 유니버스 재구성 결과 정확히 2,655종목(키움 마스터 보통주 2,651 + 유예 4 `082640`·`096610`·`269620`·`471050`). 결측 126 = 최근 20세션 내내 미도착 **118** + 이전엔 오다가 09-07 만 빠진 **8**. 118 분류: 스팩 64 · 증거금100%(신용 불가) 26 · 거래정지 8 · 2026-08 이후 신규상장 7 · 관리종목 6 · 투자주의환기 4 · 투자경고 1 · 투자위험 1 · 마스터 없음 1. 8종목의 마지막 `deal_date`: `148780` 09-03 · `328380` 09-03 · `096610` 08-31 · `321370` 08-31 · `415640` 08-28 · `082640` 08-26 · `269620` 08-20 · `294630` 08-12 |
| 4 | 잔고 항등식 `rmnd(D)=rmnd(D−1)+new(D)−rdmp(D)` (08-25~09-07) | **loan 위반 14.16% · stln 정상** | loan 3,581/25,296 = **14.16%** (99%가 음수 잔차, 잔차 중앙 150주, 잔고 대비 상대오차 중앙 0.03% · q99 27%). stln 1/25,296 = **0.004%**(`466100` 09-03). 금액 항등식은 11,138/25,296 = **44.03%**. 결측 때문이 아니다 — `new=rdmp=0` 인데 잔고가 바뀐 건 3,581건 중 **24건**뿐. 시차 때문도 아니다 — 플로우를 1·2세션 밀면 위반률이 77.06%·77.46%로 **악화**(lag 0 이 정답) |
| 5 | 값 범위 (`deal_date` 09-01~09-07, 12,646행) | **발견 없음** | 주수·금액 음수 0 · `rmnd_rate` 0~100 밖 0 · `\|gvrt\|>1000` 0 · 숫자 컬럼 `''`/NULL 0 (`whol_loan_*` 4컬럼·`whol_stln_rmnd_stcn`·`stck_prpr`·`stck_hgpr`·`acml_vol`·`stlm_date` 검사) |
| 6-a | `whol_loan_rmnd_rate` ≈ `rmnd_stcn / KRX LIST_SHRS × 100` | **정의 확인 · 분모가 조회시점** | 정의는 **상장주식수 기준, 소수 2자리 절사**로 확정 (`rate ≥ 2.0` 414종목에서 내재분모/LIST_SHRS = q05 1.0011 · q50 1.0035 · q95 1.0067 = 절사 오차 범위). 다만 09-07~09-09 사이에 상장주식수가 바뀐 15종목 중 **판별 가능한 4종목 전부가 09-09(조회시점) 주식수와 일치**하고 **09-07 주식수와 맞는 종목은 0** (`041190` 4.17 vs 09-07 기준 3.973 / 09-09 기준 4.182, `099190` 2.39 vs 2.443 / 2.398, `273060`, `363260`) |
| 6-b | `new_amt / new_stcn × 10,000` 이 당일 저가~고가 안인가 | **불성립** | 범위 안 169 / 밖 1,472 (**10.3%만 범위 내**). 내재단가/종가 비율 q10 0.801 · **q50 0.878** · q90 0.929. 총계로도 `Σnew_amt×10⁴ / Σ(new_stcn×종가)` = **0.8551**, 상환 0.8519, 잔고 0.9351. 증거금률 그룹(40%/30%/20%/100%)별 차이 없음(전부 q50 0.85~0.89). 가격창을 1·2·3세션 밀어도 개선 없음(16.8%→16.3%→15.1%). 자릿수(만원)는 맞으나 `amt = stcn × 체결가` 는 성립하지 않는다 |
| 6-c | `stck_prpr` == KRX `TDD_CLSPRC` | **불일치 4건** | 2,529 전건 조인(KRX `ISU_CD` **6자리**, kis-only 0). 불일치 = `001290` 상상인증권 5050 vs 1010 · `044480` 빌리언스 4100 vs 820 · `273060` 와이즈버즈 4320 vs 864 · `363260` 모비데이즈 4580 vs 916. **정확히 5배**이고 4종목 모두 09-08~09-09 액면병합(예: `001290` LIST_SHRS 108,337,120 → 21,667,424) |
| 6-d | `acml_vol` == KRX `ACC_TRDVOL` | **전건 일치** | 불일치 0/2,529 |
| 6-부수 | 고가·저가 대조 | **KRX 관례** | 불일치 107건, **107건 전부 KRX `ACC_TRDVOL`=0 이고 KIS `acml_vol`=0** — KRX 가 무거래일에 O/H/L 을 0 으로 주는 관례. KIS 는 기준가를 채운다 |
| 7 | `stlm_date − deal_date` = 2거래일 | **예외 0** | 08-18~09-07 전 행. 09-01→09-03 · 09-02→09-04 · 09-03→09-07 · 09-04→09-08 · 09-07→09-09. `deal_date > 20260907` 행 0 (09-08 잔고는 결제일 = 오늘이라 06:20 시점 미공표 — 설계대로) |
| 8 | `kis_ingest_log` 오늘 ts 행 | **행 없음** | `kis_ingest_log` 최신 `ts` = `2026-08-27T20:55:31`, `kis_call_log` 도 동일. 일일 증분은 `daily_run.db.run` 에만 기록한다. 창은 원장 `req_d1/req_d2` 로 확인 — 오늘 신규 2,817행 전부 `20260801 / 20260910` 단일 조합으로 설계와 일치. **종목별 status·n_rows 는 확인 불가** |
| 9 | 중복 차단이 정정을 삼키는가 | **삼키지 않음** | `src/daily/kis_daily.py:store_new_facts` 는 `req_*`·`row_hash`·`dup_seq`·`collected_at` 을 뺀 payload 로만 대조하고 값이 하나라도 다르면 새 행을 넣는다. 오늘 `dup_skipped=74856` 은 전부 payload 동일 재수집(창 25거래일 × 2,655콜 ≈ 74,340과 정합). **오늘 실제 정정 발생 0건** |

## 2. 발견

### HIGH-1: `whol_loan_rmnd_rate`·`whol_stln_rmnd_rate` 의 분모가 `deal_date` 가 아니라 **조회 시점** 상장주식수다

- **무엇이**: `kis_credit_balance.whol_loan_rmnd_rate` (대주 `whol_stln_rmnd_rate` 도 동일 산식)
- **몇 건**: 오늘 적재된 `deal_date=20260907` **2,529행 전부**가 조회시점(09-09~09-10) 기준으로 계산된 값이다. 검증 가능한 표본은 09-07~09-09 사이 상장주식수가 바뀐 15종목이고, 그 중 rate 해상도(2자리)로 판별 가능한 4종목이 **전부** 09-09 주식수와 일치했다. 09-07 주식수와 일치한 종목은 **0**
- **예시 종목**:
  - `041190` 우리기술투자 — rate 4.17. 09-07 LIST_SHRS 84,000,000 기준 3.973, 09-08 이후 79,800,000 기준 4.182
  - `099190` 아이센스 — rate 2.39. 09-07 28,757,309 기준 2.443, 09-09 29,301,512 기준 2.398
  - `363260` 모비데이즈 — rate 0.58. 09-07 32,163,769 기준 0.118, 09-08 병합 후 6,432,753 기준 0.589 (**5배 오차**)
- **추정 원인**: 원천 특성. KIS `FHPST04760000` 은 과거 결제일 행을 돌려줄 때 비율을 저장값이 아니라 **현재 상장주식수로 재계산**한다. 수집기 버그가 아니다 — 같은 콜의 주수(`rmnd_stcn`)는 원본 그대로다
- **하류 영향**: stage `stg_credit_daily` 는 이 컬럼을 `_pct("whol_loan_rmnd_rate", 4)` 로만 싣고 `price_basis_close='adjusted_asof_collect'` 같은 **기준 라벨을 붙이지 않는다**. equity `credit_daily` 의 측정축 `whol_loan_rmnd_rate_pct` 는 그대로 소비층에 나간다. `deal_date` 시점에 알 수 없는 미래 주식수가 섞이므로 **look-ahead bias**다. 자체 계산(`rmnd_stcn / price_daily.shares_out`)으로 대체하거나, `rate` 축에 `rate_basis='asof_collect'` 라벨을 달아야 한다

### HIGH-2: 같은 행 안에서 `stck_prpr` 은 수정주가, `stck_oprc/hgpr/lwpr` 은 원주가다

- **무엇이**: `kis_credit_balance.stck_prpr` vs `stck_oprc`·`stck_hgpr`·`stck_lwpr`
- **몇 건**: 오늘 적재된 09-07분에서 **4종목**. `deal_date=20260907` 에서 종가가 당일 저가~고가 밖인 행이 정확히 이 4건
- **예시 종목**:
  - `001290` 상상인증권 — `oprc=hgpr=lwpr=1010`, `prpr=5050`, `acml_vol=0`
  - `363260` 모비데이즈 — `oprc=hgpr=lwpr=916`, `prpr=4580`
  - `044480` 빌리언스 — `oprc=hgpr=lwpr=820`, `prpr=4100` (`273060` 와이즈버즈도 동일 형태)
- **추정 원인**: 원천 특성. 4종목 모두 09-08~09-09 에 5:1 액면병합을 했고, KIS 가 `stck_prpr` 만 병합 후 기준으로 소급 환산한다. `stg_credit_daily` 주석(`price_basis_close='adjusted_asof_collect'` / `price_basis_ohl='raw'`)이 이미 이 사실을 기록하고 있다
- **하류 영향**: stage 는 컬럼 단위 라벨로 격리했으나 equity `credit_daily` 는 `close_krw` 를 쓰므로, 이 4종목의 09-07 행은 **거래도 없었는데(`acml_vol=0`) 5배 가격**을 들고 간다. 병합 전 구간 전체가 같은 문제이며(`001290` 은 07-31~08-18 전 행이 5배), 그 값으로 계산한 수익률·시가총액·`amt/price` 파생은 전부 틀린다

### HIGH-3: payload 까지 달라 접히지 않는 `(req_ticker, deal_date)` 중복이 원장에 536군 남아 있다 — 오늘 기여분은 0

- **무엇이**: `kis_credit_balance` 전 컬럼(`req_*` 제외) 기준 중복
- **몇 건**: 전체 중복 초과행 **567,331행** 중, `req_d1/req_d2` 만 다른 순수 재수집(= 26컬럼 투영에서 접히는 것)을 뺀 **536군**이 payload까지 다르다. 2026년만 505군·29종목이고, 이 505군은 전부 **2026-09-09 07:00 UTC 수동 캐치업**이 만들었다. **오늘 06:20 크론이 만든 것은 0군**
- **예시 종목**: `001290` 상상인증권 (`deal_date=20260818` 에 `prpr=969`(08-26 수집)와 `prpr=4845`(09-09 수집) 두 행 공존, 07-31~08-18 전 구간 동일), `363260` 모비데이즈, `273060` 와이즈버즈
- **추정 원인**: 수집기 설계 + 원천 특성의 조합. `row_hash` 에 `req_d1/req_d2` 가 섞인 것은 이미 문서화된 DEFECT-A-03 이고, 여기에 HIGH-2 의 소급 환산이 겹쳐 "재수집하면 값이 바뀌는" 컬럼이 생겼다. `store_new_facts` 는 설계대로 값이 다르면 새 행을 남긴다(정정 보존)
- **하류 영향**: `STG_CREDIT_DAILY` 는 `key_unique=False`·`write_mode="append_only"` 이므로 이 536군은 **접히지 않고 `(ticker, date)` 중복으로 남는다**(rules_kis.py 주석의 "수정종가 재수집 판본이 생기면 접히지 않는다"가 실현된 상태). equity 격자가 어느 판본을 쓸지 결정하는 규칙이 없으면 빌드마다 값이 달라질 수 있다. 판본 선택 규칙(예: `collected_at` 최신 = 소급 반영판)을 명시해야 한다

### MID-1: 액면병합·감자 종목에서 잔고 항등식이 크게 깨진다

- **무엇이**: `whol_loan_rmnd_stcn` vs `whol_loan_new_stcn`·`whol_loan_rdmp_stcn`
- **몇 건**: 08-25~09-07 loan 위반 3,581건 중 잔차 절대값 10,000주 초과가 **95건 · 53종목**. 나머지는 잔차 중앙 150주(잔고 대비 0.03%)로 원천의 기타 증감 수준
- **예시 종목**:
  - `006050` 국영지앤엠 — 08-25 잔고 171,418 → 51,980 (신규 0, 상환 49). 08-27 에 10:1 병합(LIST_SHRS 34,895,243 → 3,489,524)
  - `008040` 사조동아원 — 08-28 잔고 334,051 → 134,702 (신규 0, 상환 350). 09-02 에 10:1 병합
  - `011370` 서한 — 08-28 잔고 168,006 → 58,023 (신규 0, 상환 42). 09-02 에 5:1 병합. (`023760` 한국캐피탈 08-27 2:1, `363260` 모비데이즈 09-08 5:1 도 같은 형태)
- **추정 원인**: 원천 특성. 병합·감자가 확정되면 원천이 **잔고 주수만 새 기준으로 재기준화**하고 그 이전 날짜의 신규·상환 주수는 옛 기준 그대로 둔다. 이벤트 며칠 **전** 날짜에서 항등식이 깨지는 것이 이 해석과 맞는다
- **하류 영향**: equity `credit_daily` 는 이미 `balance_over_shares` 격리 사유를 갖고 있어 잔고>상장주식수 행은 걸러지지만, **잔고가 상장주식수보다 작으면서 기준만 뒤섞인 행은 걸리지 않는다**. 신용잔고 증감을 플로우로 재구성하는 파생은 이 종목·구간에서 틀린다. 병합비율 확정 후 재수집으로는 고쳐지지 않는다(HIGH-3 의 판본 문제로 넘어간다)

### MID-2: 신용거래 불가로 지정된 5종목이 거래는 되는데 신용잔고 행만 사라진다

- **무엇이**: `kis_credit_balance` 의 종목 커버리지
- **몇 건**: 09-07 결측 126종목 중 **5종목**이 09-07 KRX 에 정상 거래(가격·거래량 있음)인데 신용잔고 행이 없다
- **예시 종목**: `148780` 비큐AI (마지막 09-03, 09-07 KRX 종가 3,765 · 거래량 78,477) · `294630` 서남 (마지막 08-12, 09-07 종가 3,050 · 거래량 2,725,288) · `321370` 센서뷰 (마지막 08-31, 09-07 종가 1,116 · 거래량 1,198,703). `328380` 솔트웨어·`415640` KB발해인프라도 동일. 09-10 키움 마스터에서 **5종목 전부 `증거금100%`**
- **추정 원인**: 원천 특성. 증거금100%(신용거래 불가) 지정 시 KIS 가 해당 종목 응답을 빈 배열로 준다. 마지막 행의 잔고가 0 이 아닌 채로 끊기는 경우가 있다(`148780` 08-21 잔고 5,143주 → 08-24 0 → 08-26 251 → 09-03 0 로 요동 후 소멸)
- **하류 영향**: `credit_daily` 격자에서 이 종목·구간이 `empty_response` 로 남는다. 상류가 값을 지우고 사라지므로, 하류가 **전방 채움(ffill)을 하면 이미 소멸한 잔고를 계속 들고 간다.** 게이트 분모는 요청 유니버스(2,655)라 이 이탈이 비율(0.9525)에는 잡히지만 종목 단위 경보는 없다. 원장에 "지정 사유로 사라짐"을 남길 방법이 없다(MID: 특정 종목에 국한)

### MID-3: `*_amt` 6컬럼은 `주수 × 체결가` 가 아니다 — 만원 해석으로도 15% 어긋난다

- **무엇이**: `whol_loan_new_amt`·`whol_loan_rdmp_amt`·`whol_loan_rmnd_amt` 및 대주 3컬럼
- **몇 건**: 09-07 신규가 있는 1,641종목 중 **1,472종목(89.7%)** 에서 `new_amt × 10,000 / new_stcn` 이 당일 저가~고가 밖. 총계 비율 0.8551(신규)·0.8519(상환)·0.9351(잔고)
- **예시 종목**: `000660` SK하이닉스 (386,025주 · 56,747,535만원 → 내재단가 1,470,048원, 당일 1,733,000~1,783,000) · `009150` 삼성전기 (127,566주 · 15,705,947만원 → 1,231,202원, 1,410,000~1,464,000) · `038060` 루멘스 (08-27 잔고가 신규 6,870주로만 생겼는데 `rmnd_amt = new_amt = 301만원` → 438원, 당일 831~889)
- **추정 원인**: 원천 정의 미상. 증거금률 그룹별 차이가 없어 "융자금 = 매수대금 × (1−증거금률)" 가설은 배제되고, 가격창 시차(1·2·3세션)로도 설명되지 않는다. KIS 공식 예제(`daily_credit_balance.py`)에도 컬럼 단위 명세가 없다 — 거기 명시된 것은 `※ 상환수량은 "매도상환수량+현금상환수량"의 합계` 하나뿐이다
- **하류 영향**: stage `stg_credit_daily` 는 이 6컬럼을 이미 `_unit_unknown` = `amt_basis='unknown'` 으로 싣고 equity `field_id` 로 내보내지 않는다 — **현재 소비 경로에서는 차단돼 있다.** 다만 세션 메모의 `_amt = 만원, 취득금액 기준` 해석은 데이터와 맞지 않으므로 그대로 문서에 남기면 안 된다. 단가를 `amt/stcn` 으로 복원하면 약 12~15% 낮게 나온다

### LOW-1: 오늘 실행이 `kis_ingest_log`·`kis_call_log` 에 아무것도 남기지 않았다

- **무엇이**: `kis_ingest_log`(최신 `ts` = `2026-08-27T20:55:31`) · `kis_call_log`(동일)
- **몇 건**: 오늘 2,655콜 전부 미기록. 기록은 `daily_run.db.run` 의 요약 1행(`run_id=21`)뿐
- **추정 원인**: 수집기 설계. `src/daily/kis_daily.py` 는 `daily.runlog` 만 쓴다(백필 전용 로그 테이블을 재사용하지 않는 선택)
- **하류 영향**: 원장 자체에는 없다. 다만 **종목별 status·n_rows·verdict 를 사후에 확인할 수 없어** MID-2 같은 "빈 응답인가 실패인가"를 원장 부재로만 추론해야 한다. 검수·재현 비용이 올라간다

### LOW-2: `req_name` 은 전 행 `'credit'`, `dup_seq` 는 전 행 `'0'`

- **무엇이**: `kis_credit_balance.req_name` (9,007,329행 전부 `'credit'`) · `dup_seq` (전부 `'0'`)
- **추정 원인**: 설계대로다. `req_name` 은 종목명이 아니라 잡 이름(`REQ_NAME = "credit"`)이고, `dup_seq` 는 **한 번의 적재 안에서** 내용 중복 순번이라 콜 간 중복에는 0 이 찍힌다
- **하류 영향**: 없음. 다만 `dup_seq` 를 중복 표식으로 읽으면 안 된다 — HIGH-3 의 536군을 찾는 데 쓸 수 없다(전부 0)

### LOW-3: KRX 무거래일 O/H/L = 0 조인 함정

- **무엇이**: KRX `TDD_HGPRC`·`TDD_LWPRC` vs KIS `stck_hgpr`·`stck_lwpr`
- **몇 건**: 09-07 조인 2,529건 중 **107건** 불일치. 107건 전부 KRX `ACC_TRDVOL`=0 · KIS `acml_vol`=0
- **예시 종목**: `000040` KR모터스 (KIS 1,310 / KRX 0) · `001470` 삼부토건 (5,820 / 0) · `006380` 카프로 (3,660 / 0)
- **하류 영향**: 없음(KIS 쪽이 기준가로 채운 것이 오히려 유용). 다만 KRX 를 정본으로 O/H/L 을 채우는 파이프라인은 무거래일에 0 을 그대로 싣지 않도록 주의해야 한다

## 3. 확인하지 못한 것과 이유

1. **`*_amt` 의 원천 정의** — KIS 공식 예제 코드(`daily_credit_balance.py` / `chk_*.py`)에 컬럼 단위·산식 명세가 없다. HTS `[0476]` 화면 또는 금투협 원자료와 직접 대조해야 확정된다. 이 검수에서는 "만원 자릿수는 맞고 `stcn × 체결가` 는 아니다"까지만 실측으로 확정했다.
2. **loan 항등식의 소액 잔차(잔고 대비 0.03% 수준, 3,486건)의 정체** — 병합·감자로 설명되는 것은 95건뿐이다. 나머지는 원천의 기타 증감 항목(권리 변동, 계좌 이관 등)으로 보이나 원자료 없이는 확정 불가.
3. **오늘 2,655콜의 종목별 결과(status·n_rows)** — LOW-1 대로 로그가 없다. 결측 126종목이 "빈 응답"인지 "콜 실패"인지는 `failures=0` 과 `tickers=2655/2655` 라는 실행 요약, 그리고 최근 20세션 패턴으로만 추론했다.
4. **`082850` 우리바이오의 대형 잔고 감소** — 09-02~09-04 에 신규 0·상환 1,415~9,433주인데 잔고가 1,069,090 → 208,406 으로 줄었다. 이 구간 KRX `LIST_SHRS` 는 48,456,578 로 불변이라 병합·감자로 설명되지 않는다. 09-07 이후 이벤트일 가능성이 있으나 오늘 원장 범위 밖이라 확인 못 했다.
5. **`471050`(유예 종목, 키움 마스터에 없음)** — 원장 마지막 `deal_date` 가 `20251031` 이고 KRX 09-07 에도 없다. 상장폐지로 보이나 폐지일을 확인할 소스를 이 검수 범위에서 열지 않았다.

## 4. 사용한 질의 (재현용)

```sql
-- (1) 오늘 신규 행의 deal_date 분포 · 정정 여부
SELECT deal_date, COUNT(*), COUNT(DISTINCT req_ticker)
FROM kis_credit_balance WHERE collected_at >= '2026-09-09T21:00' GROUP BY deal_date;

SELECT req_ticker, COUNT(*) FROM kis_credit_balance
WHERE collected_at >= '2026-09-09T21:00' AND deal_date < '20260907' GROUP BY req_ticker;

-- (2) deal_date 별 행수 · 고유 종목 · 중복
SELECT deal_date, COUNT(*) n_rows, COUNT(DISTINCT req_ticker) n_tick,
       COUNT(*) - COUNT(DISTINCT req_ticker) dup_extra
FROM kis_credit_balance WHERE deal_date BETWEEN '20260820' AND '20260907'
GROUP BY deal_date;

-- (2') 전체 중복 · payload 까지 접어도 남는 중복군(536)
SELECT SUM(c-1) FROM (SELECT req_ticker, deal_date, COUNT(*) c
                      FROM kis_credit_balance GROUP BY 1,2 HAVING c > 1);
SELECT COUNT(*) FROM (
  SELECT req_ticker, deal_date, COUNT(DISTINCT
    acml_vol||'|'||prdy_ctrt||'|'||prdy_vrss||'|'||prdy_vrss_sign||'|'||stck_hgpr||'|'||stck_lwpr||'|'||
    stck_oprc||'|'||stck_prpr||'|'||stlm_date||'|'||whol_loan_gvrt||'|'||whol_loan_new_amt||'|'||
    whol_loan_new_stcn||'|'||whol_loan_rdmp_amt||'|'||whol_loan_rdmp_stcn||'|'||whol_loan_rmnd_amt||'|'||
    whol_loan_rmnd_rate||'|'||whol_loan_rmnd_stcn||'|'||whol_stln_gvrt||'|'||whol_stln_new_amt||'|'||
    whol_stln_new_stcn||'|'||whol_stln_rdmp_amt||'|'||whol_stln_rdmp_stcn||'|'||whol_stln_rmnd_amt||'|'||
    whol_stln_rmnd_rate||'|'||whol_stln_rmnd_stcn) c
  FROM kis_credit_balance GROUP BY 1,2 HAVING c > 1);

-- (3) 요청 유니버스 재구성 (kiwoom.db) — universe.kiwoom_common 규칙 + 유예 4종목
SELECT code FROM ka10099_stock_master
WHERE snap_date = (SELECT MAX(snap_date) FROM ka10099_stock_master)
  AND (upSizeName <> '' OR (marketName IN ('거래소','코스닥') AND substr(code,6,1) = '0'));
-- 유예 종목은 data/daily/universe_kw.json 의 grace 키 (082640·096610·269620·471050)

-- (5) 값 범위 (09-01~09-07)
SELECT SUM(CAST(whol_loan_rmnd_stcn AS REAL) < 0),
       SUM(CAST(whol_loan_rmnd_rate AS REAL) < 0 OR CAST(whol_loan_rmnd_rate AS REAL) > 100),
       SUM(ABS(CAST(whol_loan_gvrt AS REAL)) > 1000),
       SUM(whol_loan_rmnd_stcn IS NULL OR whol_loan_rmnd_stcn = ''), COUNT(*)
FROM kis_credit_balance WHERE deal_date BETWEEN '20260901' AND '20260907';

-- (6) KRX 대조용 추출 (krx.db, 따로 ro 로 열어 로컬에서 조인)
SELECT ISU_CD, ISU_NM, MKT_NM, TDD_CLSPRC, TDD_HGPRC, TDD_LWPRC, ACC_TRDVOL, LIST_SHRS, MKTCAP
FROM krx_stk_bydd_trd WHERE bas_dd_req = 20260907
UNION ALL SELECT ISU_CD, ISU_NM, MKT_NM, TDD_CLSPRC, TDD_HGPRC, TDD_LWPRC, ACC_TRDVOL, LIST_SHRS, MKTCAP
FROM krx_ksq_bydd_trd WHERE bas_dd_req = 20260907;
-- 분모 시점 판별은 bas_dd_req = 20260909 도 같은 방식으로 뽑아 rate 와 대조

-- (7) stlm_date 간격
SELECT deal_date, stlm_date, COUNT(*) FROM kis_credit_balance
WHERE deal_date BETWEEN '20260818' AND '20260907' GROUP BY 1,2 ORDER BY 1,2;

-- (8) 로그
SELECT name, MAX(ts) FROM kis_ingest_log GROUP BY name;          -- credit 최신 2026-08-27
SELECT req_d1, req_d2, COUNT(*) FROM kis_credit_balance
WHERE collected_at >= '2026-09-09T21:00' GROUP BY 1,2;           -- 20260801 / 20260910 단일
-- daily_run.db: SELECT * FROM run WHERE source='kis_credit' ORDER BY run_id DESC;
```

항등식(4)·단위(6-a·6-b)·시차 가설 검정은 위 추출물을 로컬 파이썬으로 조인해 계산했다.
`deal_date BETWEEN 20260818 AND 20260907` 의 21컬럼을 CSV 로 뽑아 종목·날짜 사전을 만든 뒤,
직전 거래일과 짝지어 `rmnd(D) − (rmnd(D−1) + new(D) − rdmp(D))` 를 loan·stln 각각 계산했고,
lag 1·2 로 밀어 재계산해 시차 가설을 배제했다.
