# DART 수집기 확정 설계

작성 2026-08-24 · 근거: 검토 A(엔드포인트 12콜) / B(콜산정 8콜) / C(스키마 0콜) / D(운영 0콜) 종합
**본 종합 작업의 DART 실사용 콜: 0 / 0.** 로컬 `dart.db` 조회와 문서 대조만 수행했다.

표기 규약: **실측** = 우리가 호출·조회해 확인 / **명세** = DART 문서 주장 / **미확인** = 근거 없음.

---

## 0. 네 축 상충 판정 (먼저)

| # | 상충 | A/B/C/D 입장 | **판정** | 이유 |
|---|---|---|---|---|
| J1 | 정정공시 처리 | A: `rcept_no` 를 키에서 빼고 **DELETE→INSERT** (DEFECT-A02). C: `rcept_no` 를 자연키에 **포함**(빈티지 보존) | **C 채택. 전 테이블 통일.** `rcept_no` 자연키 포함, 원장은 append-only | 원장에서 구 빈티지를 지우면 "그때 공시된 값"이 영구 소실된다. PIT 백테스트는 as-reported 와 restated 를 **둘 다** 필요로 한다. A02 가 지적한 이중집계는 스키마 문제가 아니라 소비 문제 → **원장 직접 집계 금지 + 빈티지 선택 뷰 강제**(§1.9)로 막는다. 반대로 A안의 DELETE→INSERT 는 되돌릴 수 없는 손실이다 |
| J2 | `ord` 를 키에 넣는가 | A: DS002 3종 전부 필수(DEFECT-A01 완전중복행 실측). C: `dart_fin_raw` 는 `ord`+`account_detail` 필수 | **양쪽 다 채택** | 근거가 각각 실측이고 서로 배타적이지 않다. `ord` 추가 비용 0 |
| J3 | `account_detail` | C 만 제기. A·B·D 무언급 | **채택. 자연키 필수** | 그룹 대조로 **1,202행(33.1%) 유실** 확인. 재검산 §1.3 |
| J4 | 다중 corp_code 묶기 | A: 금지(DEFECT-A03 silent loss). B: 1콜 1corp 전제로 산정 | **금지 확정.** 코드 레벨 assert | `status=000` 인데 3개 중 1개만 반환된 실측이 있다. 콜 절감분(최대 100배)보다 silent loss 가 비싸다 |
| J5 | `irdsSttus` 수집 축 | A: 누적 이력형 → 최신연도 1콜로 과거가 따라옴(예산 절감 여지). B: 게이팅 전건 26,220콜 | **A 채택, 단 프로브 40콜 선행**(§6-a). 기본값 corp당 1콜 = **3,341** | 삼성중공업 FY2021 응답에 2016·2018·2021 이 전부 포함된 실측. 단일 종목 근거이므로 20종목 × 2연도로 "최신연도 ⊇ 과거연도" 를 확인한 뒤 확정. 실패 시 26,220 으로 복귀(밴드로 명시) |
| J6 | 연간/분기 소급 하한 | B: 연간 FY2015 · 분기 FY2016(005930 단일 종목) | **연간 FY2015 / 분기 FY2016 채택** + 프로브 30콜로 분기 하한 재확인 | 연간은 삼성전자·현대차 교차 실측. 분기는 단일 종목이라 근거가 얇지만, 하한을 1년 낮추는 비용이 6,547콜(0.34일)이라 프로브 30콜이 훨씬 싸다 |
| J7 | `stockTotqySttus` 하한 | A: FY2015 는 000 이나 **전 필드 `-`**(실질 무데이터). B: FY2015~ 26,220 | **FY2015 부터 수집(26,220 유지)** | 빈 행이 오나 013 이 오나 콜 비용은 동일하다. 1년치 여유 1,984콜 = 0.1일. 표본으로 하한을 확정하는 것보다 여유를 사는 게 싸고 안전하다 |
| J8 | `020` 대응 | D: 자체 카운터 기반 판별, 소진이면 재시도 0회 + 그날 STOP, 저사용 시 프로브 1콜 | **D 그대로 채택** | 프로브 비용 1콜 vs 오판 비용 19,500콜. 비대칭이 명백 |
| J9 | 예산 창 | D: 롤링 24h (리셋 시각 미확인을 회피) | **채택** | 임의 캘린더일은 그 자체가 24h 구간이다. 롤링 24h ≤ B 는 KST일 ≤ B 와 UTC일 ≤ B 를 함의한다. 리셋 시각을 알아낼 필요가 사라진다 |
| J10 | `ticker` 의 지위 | C: `corp_code` 가 식별자, `ticker` 는 속성 | **채택** | 과거 종목코드 재사용 배제 불가(미확인). `corp_code` 는 API 호출 키이자 불변 |

---

## 1. 확정 스키마

전문은 아래가 정본이다. 기존 `data/raw/schema_v2.sql` 은 DS002 3종·majorstock·콜 원장이 빠져 있으므로 **이 문서로 대체**한다.

### 1.0 마이그레이션

현행 `dart.db` 의 `dart_fin_raw` 에는 `account_detail` 이 없어 **SCE 1,202행이 이미 유실된 상태**다(§1.3). 컬럼 추가로는 복구되지 않는다(응답 원본이 없다).

```
mv dart.db dart_e2e_backup.db      # E2E 근거 보존
sqlite3 dart.db < docs/DART_DESIGN.sql
# corp_code 118,747행만 백업에서 복사(재수집 1콜이면 되므로 선택)
```
E2E 재무 2,640행은 **폐기 후 재수집**한다. 재수집 비용은 41콜(status 000·013 로그 기준)로 무시 가능하다.
기존 `dart_call_log`(E2E용, ts 없음)는 `dart_call_log_e2e` 로 이름만 바꿔 보존하고, 새 `dart_call_log` 를 콜 원장으로 쓴다.

### 1.1 dart_corp_code — 기업 식별자 원장

```sql
CREATE TABLE dart_corp_code (
  corp_code    TEXT NOT NULL PRIMARY KEY,  -- DART 고유번호 8자리
  corp_name    TEXT NOT NULL,
  stock_code   TEXT,                       -- 비상장 '' -> NULL 정규화
  modify_date  TEXT,
  collected_at TEXT NOT NULL
);
CREATE INDEX ix_corpcode_stock ON dart_corp_code(stock_code) WHERE stock_code IS NOT NULL;
```
**PK 근거**: `corp_code` 만 유일하다 — `stock_code` 는 114,762/118,747 = 96.6% 가 공백, `corp_name` 은 중복 5,518건(실측).

### 1.2 dart_company — 기업개황 (`acc_mt` 가 목적)

```sql
CREATE TABLE dart_company (
  corp_code    TEXT NOT NULL PRIMARY KEY REFERENCES dart_corp_code(corp_code),
  ticker       TEXT,
  corp_name    TEXT,
  acc_mt       TEXT NOT NULL,   -- 결산월 '01'~'12'. 사업연도 종료일의 유일한 근거
  est_dt       TEXT,
  collected_at TEXT NOT NULL
);
```
**PK 근거**: `company.json` 은 `corp_code` 1건당 1행을 반환한다(엔드포인트 인자가 `corp_code` 뿐).
**필수인 이유**: 3월 결산 법인은 사업연도 종료일이 9개월 다르다(대신정보통신 실측). `acc_mt` 없이는 `period_end` 를 만들 수 없고, `period_end` 없이는 `013` 의 영구/잠정 판별(§3)이 불가능하다. → **1단계에서 전건 선행 수집**.

### 1.3 dart_fin_raw — 재무제표 원장

```sql
CREATE TABLE dart_fin_raw (
  fin_id        INTEGER PRIMARY KEY,  -- rowid. 자연키는 UNIQUE 로 강제
  corp_code     TEXT NOT NULL,
  ticker        TEXT,                 -- 수집시점 스냅샷. 식별자로 쓰지 말 것
  bsns_year     TEXT NOT NULL,
  reprt_code    TEXT NOT NULL,        -- 11011 사업 / 11013 1Q / 11012 반기 / 11014 3Q
  fs_div_used   TEXT NOT NULL,        -- 'CFS' | 'OFS'. CFS 가 013 이면 OFS 폴백
  rcept_no      TEXT NOT NULL,        -- 접수번호 = 빈티지 식별자
  sj_div        TEXT NOT NULL,        -- BS/IS/CIS/CF/SCE
  sj_nm         TEXT,
  account_id    TEXT NOT NULL,        -- 표준태그 없으면 '-표준계정코드 미사용-'
  account_nm    TEXT,
  account_detail TEXT NOT NULL DEFAULT '-',  -- SCE 자본열 member. 이것만으로 1,202행이 갈린다
  ord           TEXT NOT NULL,
  thstrm_nm     TEXT,
  thstrm_amount TEXT,                 -- as-reported 원문 문자열. 캐스팅은 뷰에서
  frmtrm_nm     TEXT,
  frmtrm_amount TEXT,
  bfefrmtrm_nm  TEXT,
  bfefrmtrm_amount TEXT,
  currency      TEXT NOT NULL,        -- ISO 코드. 금액 단위는 이 컬럼이 규정한다
  available_at  TEXT GENERATED ALWAYS AS (substr(rcept_no,1,8)) STORED,
  collected_at  TEXT NOT NULL,
  UNIQUE (corp_code, bsns_year, reprt_code, fs_div_used, rcept_no,
          sj_div, account_id, ord, account_detail)
);
CREATE INDEX ix_fin_pit    ON dart_fin_raw(available_at, corp_code, account_id);
CREATE INDEX ix_fin_series ON dart_fin_raw(corp_code, account_id, bsns_year, reprt_code);
CREATE INDEX ix_fin_cross  ON dart_fin_raw(account_id, bsns_year, reprt_code, corp_code);
CREATE INDEX ix_fin_ticker ON dart_fin_raw(ticker, bsns_year);
CREATE INDEX ix_fin_vint   ON dart_fin_raw(corp_code, bsns_year, reprt_code, rcept_no);
```

**자연키 9열 근거 (열별 한 줄)**
| 열 | 근거 |
|---|---|
| `corp_code` | 불변 식별자. `ticker` 는 재사용 가능성 미확인이라 키 불가 |
| `bsns_year`·`reprt_code` | 호출 축 |
| `fs_div_used` | `rcept_no` 가 CFS/OFS 를 구분하지 못한다. CFS 가 013→000 으로 바뀐 해에 재수집하면 OFS 잔존행과 섞여 silent corrupt |
| `rcept_no` | 빈티지. J1 판정 |
| `sj_div` | 같은 `account_id` 가 다른 재무제표에 등장 |
| `account_id` | 표준태그 없으면 전부 `'-표준계정코드 미사용-'` → 단독 불가 |
| `ord` | 표준태그 결측 구간의 유일한 분별자. 누락 시 **216행(8.2%) 손실** 실측 |
| `account_detail` | SCE 자본열 member. 누락 시 **1,202행(33.1%) 손실** 실측 |

**1,202행 재검산 (본 종합에서 로컬 DB 재확인)**
```
status=000 인 fnltt 콜의 응답행 합계          3,814
  − 2016 프로브(00126380/2016/11013, 미적재)    187
  = 대조 대상                                 3,627
그룹 단위 매칭 적재행 합계                    2,425
  → 유실 1,202 (33.1%)
SCE 적재행 250 × 평균 자본열 5.81 = 1,452 = 250 + 1,202  (정확히 일치)
```
`schema_v2.sql` 주석의 **1,174 는 오류**다. 프로브(187행, 실제 적재 0)를 포함한 나이브 차분 3,814−2,640 이다. 정확값은 그룹 대조 기준 **1,202**.
비-SCE 유실은 0이다 — PK 충돌은 `ord` 결번을 만들 수 없다(충돌하려면 같은 `ord` 여야 하고 그러면 생존자가 남는다). 결번 155건은 DART 응답 자체의 속성이다.

**WITHOUT ROWID 를 쓰지 않는 이유**: 자연키가 ~120바이트다. 보조 인덱스 5개가 각각 이를 복제하면 전량 적재(추정 2,700만행)에서 인덱스만 수 GB 차이가 난다. rowid 테이블은 보조 인덱스에 8바이트만 얹는다.

### 1.4 dart_disclosure — 공시목록

> **폐기 (2026-08-26, DEFECT-P4-2).** 아래 DDL 은 응답 9필드 중 `corp_cls`·`rm` 을 버린다.
> `rm='정'` 은 정정공시 식별자라 PIT 판정에 직결되고, 원장의 무손실 계약을 깬다.
> 실제 적재는 `store()` 의 동적 컬럼에 맡긴다 — 응답 키 합집합으로 테이블을 만들고
> 새 필드는 `ALTER TABLE` 로 따라간다. `src/sweep_disclosure.py` 참조.
> 아래는 이력 보존용이며 **이 DDL 로 테이블을 만들지 마라.**

```sql
CREATE TABLE dart_disclosure (
  rcept_no     TEXT NOT NULL PRIMARY KEY,
  corp_code    TEXT NOT NULL,
  corp_name    TEXT,
  stock_code   TEXT,
  report_nm    TEXT,
  rcept_dt     TEXT NOT NULL,
  flr_nm       TEXT,
  window       TEXT,            -- 수집 창 'YYYYMMDD-YYYYMMDD'. 재개 근거
  collected_at TEXT NOT NULL
);
CREATE INDEX ix_disc_corp ON dart_disclosure(corp_code, rcept_dt);
CREATE INDEX ix_disc_dt   ON dart_disclosure(rcept_dt);
```
**PK 근거**: `rcept_no` 는 접수번호 자체가 공시 1건의 유일키(E2E 900/900 distinct 실측). 삼성물산이 2024-10-25 하루에 5건을 제출했으나 `rcept_no` 는 전부 상이(실측).

### 1.5 dart_dividend — 배당 (`alotMatter`)

```sql
CREATE TABLE dart_dividend (
  div_id    INTEGER PRIMARY KEY,
  corp_code  TEXT NOT NULL,
  bsns_year  TEXT NOT NULL,
  reprt_code TEXT NOT NULL,
  rcept_no   TEXT NOT NULL,
  ord        INTEGER NOT NULL,   -- 응답 list 배열 인덱스 0-base
  corp_cls   TEXT, corp_name TEXT,
  se         TEXT,               -- 항목명. 어휘가 연도별로 다르다 -> ENUM/CHECK 금지
  stock_knd  TEXT,               -- 배당수익률·주당배당금 계열에만 채워짐. 나머지 '-'
  thstrm     TEXT, frmtrm TEXT, lwfr TEXT,   -- 당기/전기/전전기. 전부 TEXT
  stlm_dt    TEXT,
  available_at TEXT GENERATED ALWAYS AS (substr(rcept_no,1,8)) STORED,
  collected_at TEXT NOT NULL,
  UNIQUE (corp_code, bsns_year, reprt_code, rcept_no, ord)
);
CREATE INDEX ix_div_pit ON dart_dividend(corp_code, bsns_year, se);
```
**PK 근거**: `se` 단독 불가(현금배당수익률이 보통주/우선주 2행, 실측). `(se, stock_knd)` 는 샘플상 유일하나 `se` 어휘가 FY2015 `주당순이익(원)` → FY2024 `(연결)주당순이익(원)` 로 바뀌는 실측이 있어 신뢰 불가 → `ord`.

### 1.6 dart_shares — 주식총수·자기주식·유통주식수 (`stockTotqySttus`)

```sql
CREATE TABLE dart_shares (
  shr_id     INTEGER PRIMARY KEY,
  corp_code  TEXT NOT NULL, bsns_year TEXT NOT NULL, reprt_code TEXT NOT NULL,
  rcept_no   TEXT NOT NULL, ord INTEGER NOT NULL,
  corp_cls   TEXT, corp_name TEXT,
  se         TEXT,   -- 보통주/우선주/합계/비고
  isu_stock_totqy        TEXT,  -- 발행할 주식 총수(수권)
  now_to_isu_stock_totqy TEXT,
  now_to_dcrs_stock_totqy TEXT,
  redc TEXT, profit_incnr TEXT, rdmstk_repy TEXT, etc TEXT,
  istc_totqy     TEXT,   -- 발행주식 총수
  tesstk_co      TEXT,   -- 자기주식수
  distb_stock_co TEXT,   -- 유통주식수
  stlm_dt TEXT,
  available_at TEXT GENERATED ALWAYS AS (substr(rcept_no,1,8)) STORED,
  collected_at TEXT NOT NULL,
  UNIQUE (corp_code, bsns_year, reprt_code, rcept_no, ord)
);
CREATE INDEX ix_shr_pit ON dart_shares(corp_code, bsns_year, se);
```
**PK 근거**: `se` 는 4값뿐이고 종류주식 다수 보유사에서 중복 가능성 미확인 → `ord`.
**전 컬럼 TEXT 필수**: `se='비고'` 행은 수치 행이 아니다 — `profit_incnr` 에 문자열 `"자사주소각"` 이 온다(실측). 집계는 `se IN ('보통주','우선주','합계')` 로 필터.

### 1.7 dart_capital — 증자·감자 (`irdsSttus`)

```sql
CREATE TABLE dart_capital (
  cap_id     INTEGER PRIMARY KEY,
  corp_code  TEXT NOT NULL, bsns_year TEXT NOT NULL, reprt_code TEXT NOT NULL,
  rcept_no   TEXT NOT NULL, ord INTEGER NOT NULL,
  corp_cls   TEXT, corp_name TEXT,
  isu_dcrs_de   TEXT,   -- '2021.07.26' 점 구분. 다른 필드의 YYYY-MM-DD 와 포맷이 다르다
  isu_dcrs_stle TEXT,
  isu_dcrs_stock_knd TEXT,
  isu_dcrs_qy   TEXT,
  isu_dcrs_mstvdv_fval_amount TEXT,
  isu_dcrs_mstvdv_amount      TEXT,
  stlm_dt TEXT,
  available_at TEXT GENERATED ALWAYS AS (substr(rcept_no,1,8)) STORED,
  collected_at TEXT NOT NULL,
  UNIQUE (corp_code, bsns_year, reprt_code, rcept_no, ord)
);
CREATE INDEX ix_cap_evt ON dart_capital(corp_code, isu_dcrs_de);
```
**PK 근거**: **자연키가 존재하지 않는다.** 이력 없는 회사는 전 필드가 `-` 인 **바이트 단위 동일 2행**을 반환한다(DEFECT-A01 실측). `ord` 없이는 INSERT OR REPLACE 가 2행→1행으로 조용히 축소한다.
**누적 이력형**: 하나의 사업보고서 응답에 과거 사건이 전부 들어온다(삼성중공업 FY2021 에 2016·2018·2021). 여러 `bsns_year` 로 수집하면 같은 사건이 중복 적재된다 → 소비는 반드시 `v_capital_events`(§1.9)를 거친다.

### 1.8 dart_majorstock — 5% 대량보유 (`majorstock`)

```sql
CREATE TABLE dart_majorstock (
  corp_code  TEXT NOT NULL,
  rcept_no   TEXT NOT NULL,
  rcept_dt   TEXT,      -- 실측 'YYYY-MM-DD'. 명세의 YYYYMMDD 는 오류
  corp_name  TEXT, report_tp TEXT, repror TEXT,
  stkqy TEXT, stkqy_irds TEXT,     -- 음수 문자열 '-348,407' 가능
  stkrt TEXT, stkrt_irds TEXT,     -- '-0.00' 가능
  ctr_stkqy TEXT, ctr_stkrt TEXT,
  report_resn TEXT,    -- 개행문자 \n 포함(실측). CSV 경유 금지, 키 사용 금지
  collected_at TEXT NOT NULL,
  PRIMARY KEY (corp_code, rcept_no)
);
CREATE INDEX ix_mjr_dt ON dart_majorstock(rcept_dt);
```
**PK 근거**: 보고서 1건 = 1행이고 `rcept_no` 가 유일하다(삼성물산 동일 일자 5건이 전부 상이한 접수번호, 실측). 연도 축이 없는 엔드포인트라 `bsns_year` 자체가 없다. `ord` 불필요.

### 1.9 운영 테이블

```sql
-- 수집 원장. 데이터 INSERT 와 같은 트랜잭션에서 기록한다.
CREATE TABLE ingest_log (
  endpoint   TEXT NOT NULL,
  corp_code  TEXT NOT NULL DEFAULT '',
  bsns_year  TEXT NOT NULL DEFAULT '',
  reprt_code TEXT NOT NULL DEFAULT '',
  fs_div     TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL,   -- DART 원문 코드 또는 내부 상태. 가공 금지
  message    TEXT,
  n_rows_api INTEGER NOT NULL DEFAULT 0,
  n_rows_db  INTEGER NOT NULL DEFAULT 0,   -- api<>db 면 키 충돌 경보
  n_attempt  INTEGER NOT NULL DEFAULT 1,
  next_retry_at TEXT,          -- no_data_pending 재시도 하한 시각
  collected_at  TEXT NOT NULL,
  PRIMARY KEY (endpoint, corp_code, bsns_year, reprt_code, fs_div)
) WITHOUT ROWID;
CREATE INDEX ix_ingest_status ON ingest_log(status, endpoint);

-- 콜 원장. 롤링 24h 예산의 근거. 송신 직전에 별도 커넥션으로 즉시 커밋한다.
CREATE TABLE dart_call_log (
  call_id   INTEGER PRIMARY KEY,
  ts        REAL NOT NULL,          -- epoch 초(UTC). 송신 직전 시각
  source    TEXT NOT NULL DEFAULT 'dongmin',
  endpoint  TEXT NOT NULL,
  params    TEXT,                   -- JSON. crtfc_key 는 절대 넣지 않는다
  http_code INTEGER,
  status    TEXT,                   -- 본문 status 원문. 예외면 'err_net'
  n_rows    INTEGER,
  elapsed_ms INTEGER
);
CREATE INDEX ix_call_ts ON dart_call_log(ts);

-- 캘린더일 요약(가독·감사용). 게이트는 롤링 24h 가 담당한다.
CREATE TABLE api_budget (
  date TEXT NOT NULL, source TEXT NOT NULL,
  n_calls INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
  PRIMARY KEY (date, source)
) WITHOUT ROWID;
```
**`ingest_log` PK 근거**: 유닛 = (엔드포인트, 호출 축) 이 재개의 최소 단위다. `endpoint` 를 반드시 유지해야 하는 이유 — DS002 3종은 **같은 사업보고서에서 파생되어 `rcept_no` 가 동일**하다(FY2024 세 엔드포인트 모두 `20250311001085` 실측). `endpoint` 를 빼면 세 수집이 서로를 덮는다.
**`dart_call_log` 를 rowid 테이블로 두는 이유**: 초당 수 건의 append 전용이고 조회는 `ts` 범위뿐이다.

### 1.10 뷰 — 원장 직접 집계 금지 규약

```sql
-- 재무: PIT (모든 빈티지 보존, 시점 t 선택은 소비 측에서)
CREATE VIEW v_fin_pit AS
SELECT f.*, c.acc_mt,
       date(f.bsns_year||'-'||c.acc_mt||'-01','+1 month','-1 day') AS period_end,
       julianday(substr(f.rcept_no,1,4)||'-'||substr(f.rcept_no,5,2)||'-'||substr(f.rcept_no,7,2))
         - julianday(date(f.bsns_year||'-'||c.acc_mt||'-01','+1 month','-1 day')) AS lag_days
FROM dart_fin_raw f LEFT JOIN dart_company c ON c.corp_code = f.corp_code;

-- 재무: 최신 빈티지(restated 기준 분석용)
CREATE VIEW v_fin_latest AS
SELECT f.* FROM dart_fin_raw f
JOIN (SELECT corp_code,bsns_year,reprt_code,fs_div_used,MAX(rcept_no) AS rcept_no
      FROM dart_fin_raw GROUP BY 1,2,3,4) m
  USING (corp_code,bsns_year,reprt_code,fs_div_used,rcept_no);

CREATE VIEW v_dividend_latest AS
SELECT d.* FROM dart_dividend d
JOIN (SELECT corp_code,bsns_year,reprt_code,MAX(rcept_no) AS rcept_no
      FROM dart_dividend GROUP BY 1,2,3) m USING (corp_code,bsns_year,reprt_code,rcept_no);

CREATE VIEW v_shares_latest AS
SELECT s.* FROM dart_shares s
JOIN (SELECT corp_code,bsns_year,reprt_code,MAX(rcept_no) AS rcept_no
      FROM dart_shares GROUP BY 1,2,3) m USING (corp_code,bsns_year,reprt_code,rcept_no)
WHERE s.se IN ('보통주','우선주','합계');

-- 증자·감자: 누적 이력형이라 사건 단위로 중복 제거한다
CREATE VIEW v_capital_events AS
SELECT corp_code, isu_dcrs_de, isu_dcrs_stle, isu_dcrs_stock_knd, isu_dcrs_qy,
       isu_dcrs_mstvdv_fval_amount, isu_dcrs_mstvdv_amount,
       MIN(available_at) AS first_seen_at, MAX(rcept_no) AS last_rcept_no
FROM dart_capital
WHERE isu_dcrs_de <> '-'
GROUP BY 1,2,3,4,5,6,7;
```
`MAX(rcept_no)` 가 최신 빈티지인 근거: `rcept_no` 앞 8자리가 접수일이므로 사전순 최대 = 최신(실측).
**PIT 선택 규칙**: 시점 t 의 값 = `available_at <= t` 인 행 중 `(corp_code,bsns_year,reprt_code)` 별 `MAX(rcept_no)`. 원장이나 `*_latest` 뷰를 백테스트에 직접 쓰면 look-ahead 다.

---

## 2. 확정 콜 예산·일정

> **2026-08-26 개정.** 프로브 157콜 + 3축 병렬 감사 결과를 반영했다. 근거는
> `docs/BACKFILL_REVIEW.md`. 이전 판(206,859콜 · 10.61일)은 ① 모수가 3,341 이었고
> ② `irdsSttus` 를 누적 이력형으로 가정했고 ③ 엔드포인트 4종이 표에서 빠져 있었다.

가용 한도: k2 **19,800** + kael(카엘 프로덕션, 2순위) 19,500. 실행 계획은 **k2 단독
19,500콜/일** 기준으로 잡는다 — kael 은 예비이고, 그 키가 나가야 하는 날은 이미
비정상이다.

**모수** (실측, `data/corps.txt`):
- 대상 기업 **3,478사** — KRX 전기간 티커 3,672 ∩ DART 상장사 3,988
- 게이팅 corp-year **26,811** (연간 FY2015~2025) / **24,763** (분기 FY2016~2025)
- 게이팅은 KRX 상장구간 ∩ 대상 FY + 상장 직전연도 1년 유예. 없으면 41,736 으로
  **31% 부풀고** 폐지 종목이 올해까지 전건 013 으로 낭비된다
- CFS→OFS 폴백배수 f = **1.10** (실측 4/40)

| 단계 | 항목 | 산식 | 콜 | 일 | 근거 |
|---|---|---|---:|---:|---|
| P0 | 착수 전 프로브 | §6 | **완료** | — | 157콜 실사용. §6-a·b·c·e + P4 창 실측 |
| P1a | `corpCode.xml` | 1 | **완료** | — | `dart_universe.py` |
| P1b | `company` (acc_mt) | 3,478 × 1 | **3,478** | 0.18 | 013 판별의 전제. 최우선 |
| P2 | 연간재무 FY2015~2025 | 26,811 × 1.10 | **29,492** | 1.51 | |
| P3a | 배당 `alotMatter` | 26,811 × 1 | **26,811** | 1.37 | 하한 FY2015 확정(§6-b) |
| P3b | 주식총수 `stockTotqySttus` | 26,811 × 1 | **26,811** | 1.37 | 하한 FY2015 확정(§6-b) |
| P3c | 증자·감자 `irdsSttus` | 26,811 × 1 | **26,811** | 1.37 | **누적 아님**(DEFECT-A04). corp축 3,341 → corp-year |
| P3d | 대량보유 `majorstock` | 3,478 × 1 | **완료** | — | 3,478콜 수집됨 |
| P3e | 임원·주요주주 `elestock` | 3,478 × 1 | **완료** | — | 3,478콜 수집됨. 롤링 2년이라 재수집 불가 |
| **P3f** | 자사주 `tesstkAcqsDspsSttus` | 26,811 × 1 | **26,811** | 1.37 | **신규.** I03·I04·I05·E04. 소각량은 여기뿐 |
| **P3g** | 최대주주 `hyslrSttus` | 26,811 × 1 | **26,811** | 1.37 | **신규.** E03·E04 |
| **P3h** | 감사의견 `accnutAdtor…` | 26,811 ÷ 3 | **8,937** | 0.46 | **신규.** E08. 1콜에 3개 연도가 온다 |
| **P3i** | 주요사항보고 DS005 7종 | 3,478 × 7 | **24,346** | 1.25 | **신규.** E05·E06·E07 |
| P4 | 공시 스윕 2010-01~2026-08 | Σ⌈total_count/100⌉ | **34,406** | 1.76 | **실측** 3,436,971건 / 67창. page 1 이 `total_count` 를 이미 주므로 창 메타 콜 불필요 |
| P5 | 분기재무 FY2016~2025 | 24,763 × 3 × 1.10 | **81,718** | 4.19 | 하한 FY2016 확정(§6-c, 30/30 013) |
| | **합계** | | **316,432** | **16.23** | |
| | **기수집 차감** | elestock + majorstock | **−6,956** | −0.36 | |
| | **실행 필요** | | **309,476** | **15.87** | |

운영 여유(재시도·013 재확인·주말 전원 이슈) 포함 **18일**.

**실행 순서** — `STAGES` 가 이 순서를 강제한다.

| stage | 항목 | 선행조건 |
|---|---|---|
| 1 | `company` | 없음. **`acc_mt` 는 모든 013 판별의 전제라 최우선** |
| 2 | `fin` (P2 + P5) | P1b 필수 |
| 3 | 나머지 + DS005 + 공시 스윕 | P1b |

순서 근거: ① `acc_mt` 없이는 013 의 영구/잠정을 가를 수 없다 — 3월 결산 법인은
`period_end` 가 9개월 어긋나 12월 결산 폴백도 안전하지 않다 ② 재무가 단위 콜당
가치가 가장 높다 ③ 공시 스윕은 창 단위라 중단·재개가 가장 싸므로 버퍼 역할
④ 분기재무는 최대 물량(26%)이라 마지막.

**이전 판 대비 증감**

| 항목 | 이전 | 확정 | 차이 | 이유 |
|---|---:|---:|---:|---|
| 모수 | 3,341 | 3,478 | +137 | 로컬 krx.db 축약본 → 서버 정본 |
| P3c `irdsSttus` | 3,341 | 26,811 | **+23,470** | 누적 이력형 가정이 실측으로 기각(DEFECT-A04) |
| P4 공시 스윕 | 35,454 | 34,406 | −1,048 | 67창 `total_count` 전건 실측 + 메타 콜 불필요 |
| 누락 4종 | 0 | 86,905 | **+86,905** | `SPEC` 에 있는데 예산표에 없던 것 + DS005 |
| **계** | **206,859** | **316,432** | **+109,573** | |

---

## 3. 오류코드 처리표 (13코드 + 네트워크)

분기는 **HTTP 200 본문 `status` 기준**이다(E2E 에서 013 이 예외 없이 HTTP 200 으로 통과, 실측). HTTP 레벨 실패는 별도 축.

| status | 의미 | 재시도 | 유닛 처리(`ingest_log.status`) | 런 처리 | 사람 호출 |
|---|---|---|---|---|---|
| `000` | 정상 | — | `ok` (n_rows=0 이면 `ok_empty`) | 계속 | — |
| `010` | 미등록 키 | **0회** | 기록 안 함 | **전면 ABORT** | **즉시** |
| `011` | 사용중지 키 | **0회** | 기록 안 함 | **전면 ABORT** | **즉시** (약관 위반 제재 신호) |
| `012` | 접근불가 IP | **0회** | 기록 안 함 | **전면 ABORT** | **즉시** |
| `013` | 조회 데이터 없음 | 조건부(§3-1) | `no_data` 또는 `no_data_pending` | 계속 | — |
| `014` | 파일 없음 | **0회** | `no_file` (묘비 — 영구 skip) | 계속 | — |
| `020` | 요청제한 초과 | **판별 프로브 1회만** | 기록 안 함(반드시 미완으로 남긴다) | **그날 STOP** | 조건부(§3-2) |
| `021` | 회사 개수 초과(최대 100) | **0회** | `bad_request` | **해당 엔드포인트 ABORT** | **즉시** — 1콜 1corp 규약상 뜨면 100% 파라미터 생성 버그 |
| `100` | 필드 부적절 | **0회** | `bad_request` (파라미터 원문 전량 기록) | 임계 초과 시 ABORT | 임계 초과 시 |
| `101` | 부적절한 접근 | **0회** | 기록 안 함 | **전면 ABORT** | **즉시** (남용 판정 신호) |
| `800` | 시스템 점검 | 60s→300s→900s **프로브 3회** | 기록 안 함 | 지속 시 그날 STOP | 2일 연속이면 |
| `900` | 미정의 오류 | 2s→8s **2회** | `err_900` | 비율 임계 초과 시 ABORT | 임계 초과 시 |
| `901` | 개인정보 보유기간 만료 | **0회** | 기록 안 함 | **전면 ABORT** | **즉시** (계정 재등록 필요) |
| (네트워크 예외/타임아웃) | — | 1s→4s **2회** | `err_net` | 비율 임계 초과 시 ABORT | 임계 초과 시 |

임계: 최근 500콜 중 `err_900`+`err_net`+`bad_request` 가 25건(5%) 초과 → ABORT + 알림. **개별 관용, 총량 불관용.**
E2E 에서 실제로 관측된 status 는 `000`(28) / `013`(13) 뿐이다. 나머지 11개는 **전부 미관측** — 위 처리는 명세 + 설계 판단이다.

### 3-1. `013` 의 영구/잠정 판별 — 이 표에서 유일하게 사소하지 않은 항목

과거 회계연도의 013 은 안정적이지만, **아직 공시되지 않은 최근 기수의 013 은 나중에 000 이 된다.** 구분하지 못하면 최근 2년치가 통째로 `no_data` 로 굳는다.

```python
period_end = fiscal_end(company.acc_mt, bsns_year, reprt_code)   # 3월결산 보정 필수(실측)
if today >= period_end + timedelta(days=161):   # 결산일→접수일 실측 중앙값
    unit_status = "no_data"          # 영구. 재시도 안 함
else:
    unit_status = "no_data_pending"  # next_retry_at = period_end+161d, 일일 증분이 다시 집는다
```
161일 근거: `rcept_no` 앞 8자리로 잰 실측 시차 — 최소 69 / **중앙 161** / 최대 1,509(현대차 FY2017 의 2022년 정정본). 최대치를 쓰면 4년을 못 굳히므로 중앙값 + 일일 증분 재시도 조합을 쓴다.

### 3-2. `020` — 즉시 중단인가 백오프인가

**판정: 우리 자체 카운터로 판별한다. '우리가 소진'이면 재시도 0회 + 그날 STOP.**

1. 한도가 일 단위라면 같은 날의 백오프는 산술적 순손실이다. 0.5s 페이스로 10분만 재시도해도 1,200콜을 태우고 성공은 0이다. 게다가 **실패 요청이 한도에 계상되는지가 미확인**이라, 계상된다면 다음 날 예산까지 갉는다. 불확실성은 백오프를 더 위험하게 만들지 덜 위험하게 만들지 않는다.
2. 그러나 `020` 의 공식 설명이 "**일반적으로는** 20,000건 이상의 요청" 이다. 이 단서는 미공개 버스트 한도나 **키를 공유하는 카엘의 초과사용** 을 배제하지 않는다. 그 경우 하루를 통째로 버리면 19,500콜 손실이다.
3. 우리는 우리 콜 수를 정확히 안다 → 두 경우를 가른다.

```python
def on_020(sent_24h, cap):
    if sent_24h >= 0.8 * cap:
        mark_day_stop("quota_ours"); return STOP          # 재시도 0, 사람 호출 없음
    sleep(60)
    probe = call(cheapest_unit)                            # 판별 프로브 딱 1콜
    if probe.status == "000":
        rate_limiter.halve()
        alert("020@low-usage: 버스트 한도 추정, 페이스 절반"); return CONTINUE
    mark_day_stop("quota_foreign"); alert("020@low-usage: 외부 소진 의심"); return STOP
```
비대칭: 프로브 1콜 vs 오판 19,500콜. `sent_24h ≥ 0.8·cap` 구간의 백오프는 얻을 게 없으므로 0회.
**`800` 은 다르다** — 시간 제한형 장애이고 한도와 무관하므로 백오프가 의미를 갖는다. 다만 점검 중 콜 계상 여부가 미확인이라 **프로브 3콜로 상한**을 두고 그날 런을 종료해 30분 캐치업 틱에 넘긴다(24시간을 버리지 않는다).

---

## 4. 재개·예산 설계 (의사코드)

### 4-1. 롤링 24시간 게이트 (리셋 시각 미확인을 회피)

리셋 후보는 ① KST 자정 ② UTC 자정 ③ 롤링 24h 셋인데, **어느 것인지 알아낼 필요가 없다.**
임의의 캘린더 일은 그 자체가 하나의 24시간 구간이다. 따라서 "모든 슬라이딩 24h 창에서 콜 ≤ B" 를 강제하면 KST일 ≤ B 와 UTC일 ≤ B 가 자동으로 따라온다. 롤링 24h 는 셋 중 가장 강한 제약이고 나머지 둘을 함의한다.

```python
class Rolling24h:
    def __init__(self, db, cap=19_500):
        rows = db.execute("SELECT ts FROM dart_call_log WHERE ts > ? ORDER BY ts",
                          (time.time() - 86400,)).fetchall()
        self.q = deque(r[0] for r in rows)     # 재기동 시 DB 에서 시딩. 최대 ~19,500개
        self.cap = cap
    def allow(self):
        now = time.time()
        while self.q and self.q[0] <= now - 86400:
            self.q.popleft()
        return len(self.q) < self.cap
    def charge(self, ts):
        self.q.append(ts)                       # '송신 직전'에 계상한다(응답이 아니라)
```
자기동기화 성질: 어제 18:45~21:30 에 상한을 태웠다면 오늘 18:45 부터 정확히 어제 속도로 슬롯이 풀린다. 창이 매일 뒤로 밀리는 드리프트가 없다.

### 4-2. 콜 래퍼 — 계상은 송신 직전, 별도 커밋

```python
def guarded_call(endpoint, params):
    if not budget.allow():
        raise BudgetExhausted(endpoint, params)
    pacer.wait()                                  # 최소 간격 0.2s (api.dart 내장)
    ts = time.time()
    budget.charge(ts)
    call_id = calldb.execute(                     # ★ 데이터 트랜잭션과 분리된 커넥션.
        "INSERT INTO dart_call_log(ts,source,endpoint,params) VALUES(?,?,?,?)",
        (ts, 'dongmin', endpoint, json_masked(params))).lastrowid
    calldb.commit()                               # 크래시해도 계상은 남는다(보수적 방향)
    try:
        j = api.dart(endpoint, **params)          # 키는 api.dart 안에서만 다룬다
        st = j.get('status') if isinstance(j, dict) else '000'
        n  = len(j.get('list', [])) if isinstance(j, dict) else 1
    except Exception as e:
        calldb.execute("UPDATE dart_call_log SET status='err_net',elapsed_ms=? WHERE call_id=?",
                       (int((time.time()-ts)*1000), call_id)); calldb.commit()
        raise
    calldb.execute("UPDATE dart_call_log SET status=?,n_rows=?,elapsed_ms=? WHERE call_id=?",
                   (st, n, int((time.time()-ts)*1000), call_id)); calldb.commit()
    return j, st, n
```
`json_masked` 는 `crtfc_key` 를 제거한다. **`logging` 레벨은 WARNING 이상으로 고정**한다 — 카엘은 `logging.basicConfig(level=INFO)` + httpx 조합으로 요청 URL 전체(=인증키)를 `dart.log` 에 남기고 있고, 그 로그는 로테이션도 없다(실측). 우리는 requests 를 쓰지만 같은 사고를 만들지 않는다.

### 4-3. 유닛 재개

```python
UNIT = (endpoint, corp_code, bsns_year, reprt_code, fs_div)
TERMINAL = ('ok', 'ok_empty', 'no_data', 'no_file', 'bad_request')

def pending_units(plan):
    done = {row[:5] for row in db.execute(
        "SELECT endpoint,corp_code,bsns_year,reprt_code,fs_div FROM ingest_log "
        "WHERE status IN (?,?,?,?,?)", TERMINAL)}
    retry_ok = lambda u: db_next_retry_at(u) is None or now() >= db_next_retry_at(u)
    return [u for u in plan if u not in done and retry_ok(u)]
```
`plan` 은 결정적으로 생성한다: 게이팅(KRX 상장구간 ∩ 대상 FY, 상장 직전연도 1년 유예) → 정렬 → 순차. 난수·집합 순회 순서에 의존하지 않는다(재개 시 같은 순서가 나와야 한다).

### 4-4. 유닛 실행 — 데이터와 로그는 한 트랜잭션

```python
def run_unit(u):
    endpoint, corp, year, reprt, fs = u
    assert ',' not in corp, "다중 corp_code 금지 (DEFECT-A03: status=000 인데 일부만 반환)"
    j, st, n_api = guarded_call(endpoint, params_of(u))

    if st in ('010','011','012','101','901'):  raise FatalAbort(st)
    if st == '021':                            raise EndpointAbort(st)   # 규약상 발생 불가
    if st == '020':                            return on_020(len(budget.q), budget.cap)
    if st == '800':                            return backoff_800()
    if st == '900':                            return retry_or_mark(u, 'err_900')
    if st == '013':
        if endpoint == 'fnlttSinglAcntAll.json' and fs == 'CFS':
            return run_unit((endpoint, corp, year, reprt, 'OFS'))        # 폴백. 실측 10%
        return mark(u, classify_013(u))                                   # §3-1
    if st == '100':                            return mark(u, 'bad_request', msg=params_of(u))

    rows = normalize(j['list'], u)             # ord = enumerate 인덱스, 전 필드 TEXT
    with db:                                   # ★ 데이터 + ingest_log 원자적
        db.executemany(INSERT_SQL[endpoint], rows)
        n_db = db.total_changes_delta()
        db.execute(UPSERT_INGEST, (…, 'ok' if n_api else 'ok_empty', n_api, n_db, now()))
    if n_db != n_api:
        alert(f"키 충돌 의심 {u}: api={n_api} db={n_db}")   # 1,202행 사고의 조기경보
```
`n_rows_api ≠ n_rows_db` 알림이 이 설계에서 가장 값싼 안전장치다. SCE 유실도, `irdsSttus` 중복행 붕괴도 이 한 줄이면 첫 유닛에서 잡혔다.

### 4-5. 일일 예산 마감

```python
def day_loop():
    for u in pending_units(plan):
        try:
            run_unit(u)
        except BudgetExhausted:
            log("budget window full — 30분 뒤 캐치업 틱이 이어받는다"); break
        except FatalAbort as e:
            alert_human(e); sys.exit(2)
    db.execute("INSERT INTO api_budget(date,source,n_calls,updated_at) VALUES(?,?,?,?) "
               "ON CONFLICT(date,source) DO UPDATE SET n_calls=excluded.n_calls,"
               "updated_at=excluded.updated_at",
               (kst_today(), 'dongmin', calls_today(), now()))
```
`BudgetExhausted` 는 예외가 아니라 정상 종료다. 롤링 창이 자연히 열리므로 30분 틱이 그대로 재개한다.

---

## 5. cron 항목

전제(전부 실측): 실행 머신은 맥, 시스템 TZ `Asia/Seoul` → **cron 시각이 곧 KST 다**(BSD cron 은 `CRON_TZ` 미지원이므로 쓰지 않는다). AC 전원에서 `sleep 0`, 배터리에서 `sleep 1` → 백필 중 슬립 방지 필요. `flock(1)` 은 맥에 없다 → `fcntl.flock` 을 러너 진입점에서 건다. 카엘 DART cron 은 KST 07:45 / 18:15 이고 락 공유가 불가능하다(다른 머신) → **예산 헤드룸 500콜로만 방어**한다.

```crontab
# ── DART 수집기 (KST. 시스템 TZ=Asia/Seoul) ─────────────────────
DART_HOME=/Users/claudeoscarmonet/Desktop/Quant_study/workspace/dongmin
PY=/usr/bin/python3

# 백필 캐치업 틱: 30분마다. 예산 창이 열려 있으면 이어서 돌고, 차 있으면 즉시 종료.
# fcntl.flock 으로 중복 실행을 막으므로 틱이 겹쳐도 안전하다.
*/30 * * * * /usr/bin/caffeinate -i $PY $DART_HOME/src/dart_runner.py backfill >> $DART_HOME/logs/dart_backfill.log 2>&1

# 일일 증분: 카엘 창(07:45 / 18:15)을 피해 08:05 / 19:05.
#   - 신규 공시 스윕(전일~당일) + no_data_pending 재방문
 5 8,19 * * * /usr/bin/caffeinate -i $PY $DART_HOME/src/dart_runner.py incremental >> $DART_HOME/logs/dart_incr.log 2>&1

# 무결성·요약: 예산 소비가 0인 로컬 작업.
#   - PRAGMA quick_check, n_rows_api<>n_rows_db 유닛 집계, api_budget 마감
15 4 * * * $PY $DART_HOME/src/dart_runner.py audit >> $DART_HOME/logs/dart_audit.log 2>&1

# 로그 로테이션: 카엘 서버에는 없어서 dart.log 가 단조 증가 중이다(실측). 같은 실수를 안 한다.
30 4 * * 0 /usr/bin/find $DART_HOME/logs -name '*.log' -size +50M -exec /usr/bin/gzip -f {} \;
```
`caffeinate -i` 는 유휴 슬립만 막는다(뚜껑 닫힘은 못 막는다) — 백필 기간에는 AC 연결 + 뚜껑 열림을 운영 조건으로 둔다.
러너 진입점:
```python
import fcntl
_lk = open('/tmp/dongmin_dart.lock', 'w')
try: fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: sys.exit(0)      # 이미 돌고 있다. 조용히 종료
```

---

## 6. 착수 전 확인할 것

| # | 미확인 항목 | 확인 방법 | 비용 | 무엇이 걸려 있나 |
|---|---|---|---:|---|
| a | `irdsSttus` 누적성 — 최신연도 응답이 과거연도 응답을 포함하는가 | 20종목 × (최신 FY, 임의 과거 FY) 응답 집합 비교 | **40콜** | P3c 예산 **3,341 ↔ 26,220** (22,879콜 = 1.2일) |
| b | DS002 3종 소급 하한 | `alotMatter`·`stockTotqySttus`·`irdsSttus` × FY2014/FY2015 × 2종목 | **12콜** | 각 항목 ±1년 = ±1,984콜 |
| c | 분기재무 하한 FY2016 (현재 005930 단일 근거) | 무작위 30종목 × FY2015 `11013` | **30콜** | P5 ±6,547콜(0.34일) |
| ~~d~~ | ~~`majorstock` 페이징 유무~~ | **해소** — 수집분 실측(§6-1) | ~~3콜~~ **0** | P3d **3,341 확정** |
| e | `list.json` 3개월 창의 정확한 경계(92일? 캘린더 3개월?) | 경계 전후 2콜 | **2콜** | P4 창 개수 67 ↔ 70 |
| | **소계** | | **84콜** (여유 포함 **120**) | |

### 6-1. 페이징 — 실측 결과와 `list.json` 구현 요구

`store()` 는 `j.get("list")` 만 꺼내고 응답 최상위 메타는 보지 않는다. 즉 응답이 잘려 와도
**사후에 알 방법이 없다.** 현재 SPEC 10종에 대해 실측으로 확인했다:

| 확인 | 결과 |
|---|---|
| `elestock` 응답 최상위 필드 | `status`·`message` **뿐** — `total_count`/`total_page` 자체가 없다 |
| `elestock` 단일 응답 최대 행수 | **3,399행**(005930) 이 한 번에 온다 |
| 정확히 100행인 기업 | `elestock` **0** / `majorstock` **0** |
| 100행 이상인 기업 | `elestock` 21 / `majorstock` 0 (최대 91행) |

100의 배수에서 끊긴 흔적이 없고 3,399행이 단일 응답으로 오므로, **현재 10종은 페이징을
쓰지 않는다.** §6-d 프로브(3콜)는 이것으로 해소한다.

**단 `list.json`(P4 공시 스윕, 35,454콜)은 다르다.** 예산 산식 자체가 `Σ분기 total_count/100`
이므로 페이징이 있는 엔드포인트다. 구현 시 아래를 **필수 조건**으로 한다:

1. `page_no` 를 1부터 `total_page` 까지 순회한다. 첫 페이지만 받고 끝내면 100건 초과분이
   **조용히** 사라진다 — 응답에 실패 신호가 없으므로 `status=000` 으로 완료 처리된다
2. 마지막 페이지 수신 후 `Σ len(list)` 와 `total_count` 를 대조한다. 불일치면 그 유닛을
   완료로 기록하지 않는다 (DEFECT-A03 의 silent 부분반환과 같은 실패 모드다)
3. `total_count` 를 `ingest_log` 에 남긴다. 지금은 `n_rows`(수신 행수)만 있어서
   "받은 것 = 저장한 것" 은 검증되지만 "받은 것 = API 가 가진 전부" 는 검증되지 않는다

**확인하지 않기로 한 것 (비용이 이득을 초과)**
- **`020` 리셋 시각 / 실패 콜 계상 여부**: 확인하려면 하루치(20,000콜)를 태워야 한다. 롤링 24h(§4-1)가 세 후보를 모두 함의하므로 **알 필요가 없다**. 실패 콜은 보수적으로 전부 계상한다.
- **`800` 응답이 JSON 인지 HTML 인지**: 점검 시간대에만 관측 가능. 비용 0이지만 시점을 못 고른다 → 파서에 `json.JSONDecodeError` 방어를 넣고 `err_net` 으로 떨어뜨린다.
- **분당 한도 존재 여부**: 공식 오류코드표에 항목이 없다. 카엘의 `_MIN_INTERVAL=0.7`("분당 100회" 주장)은 **민간전승으로 취급**하되, 우리 페이스 0.2s 는 유지하고 `020@low-usage` 가 뜨면 자동 반감(§3-2)으로 흡수한다.
- **1999~2009 소급**: KRX 마스터가 2010-01-04부터라 2009년 이전 폐지 종목이 모수 3,341 에 없다. 모수를 모르는 채 후방외삽한 14,003콜은 거의 확실히 과대다. **범위 밖으로 명시**하고 이번 설계에서 제외한다.

---

## 7. 남은 리스크

### 7-1. 해소한다 (착수 전 또는 구현으로)

| 리스크 | 해소 수단 |
|---|---|
| SCE 1,202행(33.1%) silent 유실 | `account_detail` 자연키 포함 + `n_rows_api<>n_rows_db` 알림 |
| `ord` 누락 216행(8.2%) 유실, `irdsSttus` 완전중복행 붕괴 | 전 테이블 `ord` 자연키 포함 |
| 다중 corp_code 의 silent 부분반환 | 1콜 1corp 강제(`assert ',' not in corp_code`) |
| 정정공시 이중집계 / 고아행 | 빈티지 append-only + `*_latest` 뷰 강제. 원장 직접 집계 금지 |
| 3월 결산 법인의 `period_end` 오산 | `acc_mt` 를 P1b 에서 전건 선행 수집 |
| 최근 기수 013 이 영구 `no_data` 로 굳음 | `period_end + 161일` 규칙 + `no_data_pending` + 일일 증분 |
| 한도 리셋 시각 미확인 | 롤링 24h 게이트(세 후보를 모두 함의) |
| 인증키 로그 노출 | `logging` WARNING 고정 + `params` 마스킹 + 로그 로테이션 |
| 프로세스 크래시 시 예산 오계상 | 콜 원장을 **송신 직전 · 별도 커넥션 즉시 커밋** (보수적 방향으로만 틀린다) |
| `irdsSttus` 예산 22,879콜 불확실 | 프로브 40콜(§6-a) |
| `list.json` 페이징 미처리 시 100건 초과분 silent 유실 | `page_no` 순회 + `Σlen(list)` vs `total_count` 대조(§6-1). 불일치 시 완료 미기록 |

### 7-2. 감수한다 (근거를 붙여서)

| 리스크 | 감수 이유 | 잔여 노출 |
|---|---|---|
| f=1.10 (CFS→OFS 폴백배수)의 표본이 10 | 95% CI [0.3%, 44.5%]. 정밀화하려면 수백 콜을 표본에 태워야 한다 | 예산 밴드 26,220~45,939 (P2 기준 최대 +1.1일) |
| 분기 하한 FY2016 이 005930 단일 근거 | 프로브 30콜로 부분 해소하되, 전 종목 검증은 하지 않는다 | ±6,547콜(0.34일) |
| 카엘 사용량 ~300콜/일 이 변동 | 타인 코드라 통제 불가. 마진 200콜 + `020@low-usage` 판별로 흡수 | 최악의 경우 그날 STOP 1회 |
| 실패·타임아웃 콜의 한도 계상 여부 미확인 | 전부 계상하는 보수적 가정. 계상되지 않는다면 예산을 조금 낭비할 뿐 | 예산 과소사용 (안전 방향) |
| 맥 슬립·전원 이탈로 야간 백필 중단 | `caffeinate -i` + 30분 캐치업 틱으로 자동 재개. 무인 서버가 아니다 | 일정 지연(데이터 손실 없음) |
| 1999~2009 소급 불가 | 모수(폐지 종목) 자체가 없다 | 커버리지 2010~ 로 명시 |
| 상장폐지 종목의 최신 `irdsSttus` 부재 | 누적 이력형 최적화가 폐지 종목에는 안 먹힌다 → 마지막 상장연도로 호출 | 폐지 종목 일부 이력 결측 가능 |
| `ticker`→`corp_code` 과거 재사용 가능성 | 현재 스냅샷은 1:1(실측)이나 과거는 미확인. `corp_code` 를 식별자로 써서 회피 | `ticker` 조인 시점 데이터에 한정 |

---

## 부록. 확정된 결함 (구현 시 회귀 테스트 대상)

**DEFECT-A01: `irdsSttus` 는 자연키가 없다**
- 상황: 증자·감자 이력이 없는 회사의 정기보고서 수집
- 인풋: `api.dart('irdsSttus.json', corp_code='00126380', bsns_year='2024', reprt_code='11011')`
- 에러 위치: 응답 `list` 자체 — 전 필드 `-` 인 **바이트 단위 동일 2행**. `(rcept_no, isu_dcrs_de, isu_dcrs_stle, isu_dcrs_stock_knd)` 어떤 조합으로도 구분 불가
- 위험성: PK 에 `ord` 가 없으면 INSERT OR REPLACE 가 2행→1행으로 조용히 축소(silent data loss). 실데이터도 위험 — 삼성중공업 FY2021 은 같은 날·같은 형태 2행이 주식종류로만 갈린다

**DEFECT-A04: `irdsSttus` 는 누적 이력이 아니라 롤링 윈도우다 — 과거 증자·감자가 빠진다**
- **상황**: P3c 예산을 "최신연도 1콜이면 전 이력"(누적 이력형) 가정 위에 3,341콜로 잡았다. §6-a 프로브 46콜로 그 가정을 검정했다
- **인풋**: 2015년 이전 상장 보통주 20사 × `irdsSttus.json` `reprt_code='11011'` × `bsns_year` ∈ {2025, 2019}. 판정 키는 `(isu_dcrs_de, isu_dcrs_stle, isu_dcrs_stock_knd, isu_dcrs_qy)`, 전 필드 `-` 인 빈 행은 제외
- **에러 위치**: 계획 가정 자체 — `docs/DART_DESIGN.md` §2 P3c 행. `src/backfill_dart.py:41-42` `SPEC["capital"]` 은 `axis="corp_year"` 로 맞게 구현돼 있으나, 예산표만 corp 축(3,341)으로 잡혀 있었다
- **실측**: 20사 중 **과거 포함 8 · 미포함 12**. 응답이 요청 연도 기준 최근 몇 년치만 담는다:

  | corp_code | FY2019 응답 | FY2022 응답 | FY2025 응답 |
  |---|---|---|---|
  | 00101044 | 2015.05~2019.08 (31행) | 2020.05~2022.12 (6행) | 2023.04~2024.08 (5행) |
  | 00102113 | 2007.06~2019.10 (13행) | 2007.06~2022.05 (36행) | 2007.06~2022.05 (36행) |

  00101044 는 세 응답의 일자 범위가 **전혀 겹치지 않는다.** 00102113 은 2022년 이후 증자가 없어 FY2022 와 FY2025 가 동일할 뿐이다
- **위험성**: **데이터 손실.** 최신연도만 받으면 그 윈도우 밖 증자·감자가 통째로 빠진다. 주식수 변동 이력이 끊기면 희석 보정과 주당 지표(EPS·BPS)가 과거 구간에서 틀린다. 예산 영향은 **P3c 3,341 → 26,220콜 (+22,879 = +1.2일)**

**DEFECT-A05: 같은 증자 사건의 `isu_dcrs_stle`(형태)이 보고서 시점마다 다르다**
- **상황**: §6-a 프로브 중 발견. 동일 기업의 같은 일자·같은 수량 사건이 요청 연도에 따라 다른 형태로 분류되어 온다
- **인풋**: `irdsSttus.json` `corp_code='00102113'` `reprt_code='11011'`, `bsns_year` 를 `2019` 와 `2025` 로
- **에러 위치**: 응답 `list` 의 `isu_dcrs_stle` 필드. 세 건이 확인됐다:

  | 일자 | 수량 | FY2019 응답 | FY2025 응답 |
  |---|---:|---|---|
  | 2019.08.28 | 120,000 | 유상증자(주주배정) | **주식매수선택권행사** |
  | 2019.10.29 | 30,000 | 유상증자(주주배정) | **주식매수선택권행사** |
  | 2019.10.29 | 70,000 | 유상증자(주주배정) | **주식매수선택권행사** |

- **위험성**: **중복 적재.** 원장 PK 는 행 내용 해시(`row_hash`)라 형태가 다르면 별개 행으로 들어간다. corp-year 전건 수집(DEFECT-A04 대응)을 하면 같은 사건이 서로 다른 연도 응답에서 **두 번** 잡히고, 증자 수량이 합산되면 희석이 과대계산된다. 통합층에서 `(corp_code, isu_dcrs_de, isu_dcrs_stock_knd, isu_dcrs_qy)` 로 중복 제거하되 `isu_dcrs_stle` 은 키에서 빼야 한다 — 다만 그 경우 "같은 날 같은 수량의 서로 다른 사건" 을 구분할 수 없으므로, 어느 응답의 형태를 정본으로 볼지 규칙이 필요하다 (최신 응답 우선이 무난하나 미확정)
**DEFECT-A03: 다중 corp_code 는 조용히 일부를 버린다**
- 상황: 콜 절감을 위해 corp_code 를 콤마로 묶어 DS002 호출
- 인풋: `irdsSttus.json`, `corp_code='00100601,<2개 더>'`, `bsns_year='2024'`, `reprt_code='11011'`
- 에러 위치: 응답 — `status=000` 인데 distinct `corp_code` 가 1개. 누락에 대한 신호가 013 도 021 도 아닌 **무신호**
- 위험성: silent data loss. 100개를 보내 40개만 와도 `status=000` 이라 수집기가 완료로 기록하고 넘어간다. 재수집 트리거가 영구히 사라진다

**DEFECT-C01: `account_detail` 누락 시 SCE 33.1% 유실**
- 상황: `fnlttSinglAcntAll` 응답을 `(corp,year,reprt,fs,sj,account_id,ord)` 키로 적재
- 인풋: 005930 FY2017 등 20개 그룹 전부
- 에러 위치: `dart_fin_raw` UNIQUE 제약 — DART 는 SCE 한 줄을 자본 열 개수만큼 반복 반환하고 `account_detail` 만 다르다
- 위험성: `REPLACE` 로 마지막 1건만 생존. 3,627행 중 **1,202행(33.1%)** 소실. 유실/SCE행 비율이 8.0·8.0·7.0 으로 정수(=자본 열 수)라는 점과 250×5.81=1,452 총량 일치로 이중 검산됨

**DEFECT-B01: 계획서 콜 산정의 기저 모수가 틀렸다**
- 상황: 계획서 R4-3~R4-8 예산 수립
- 인풋: 계획서 값 23,868 / 143,208 / 19,890 / 3,978 / 45,648
- 에러 위치: 전부 `1,989`(또는 2,853) 배수. 본문 "FY2013~2025 = 13년" 과 산식(×12)이 자기모순이고, 분기 143,208 은 CFS+OFS 전건 2회 호출을 가정(실측 폴백률 10%의 9배)
- 위험성: 분기 79% 과대 / 증자·감자 6.6배 과소. 일정 산정이 통째로 어긋난다

**DEFECT-B02: `corp_code` 없는 `list.json` 은 검색기간 3개월 제한**
- 상황: 공시 스윕 창 설계
- 인풋: `list.json`, `bgn_de='20180101'`, `end_de='20181231'`, corp_code 없음
- 에러 위치: 응답 `status=100` — "corp_code가 없는 경우 검색기간은 3개월만 가능합니다"
- 위험성: 계획서·FINAL_PLAN 어디에도 없는 하드 제약. 연 단위 창으로 설계하면 P4 전체가 첫 콜에서 실패한다

**DEFECT-C02: 분기 보고서에서 손익은 3개월인데 현금흐름은 연초누계다 — 한 행에 두 기간이 섞인다**
- 상황: `fnlttSinglAcntAll` 분기·반기 응답을 스테이지로 펼친 뒤(`build_stage.py`) 통합층 `financials` 한 행으로 접는 경로. 연간(`11011`)은 둘 다 12개월이라 드러나지 않고 분기에서만 터진다
- 인풋:
  1. `corp_code='00126380'`(삼성전자) `bsns_year='2025'` `fs_div='CFS'` 를 `11013`·`11012`·`11014`·`11011` 네 번 호출
  2. `build_stage.py` → `build_panel.py` → `financials.revenue`, `financials.cf_operating` 조회
- 에러 위치: `src/build_panel.py:41-64` `resolve()` — `sj_div` 와 무관하게 `fin_fact.amount` 만 읽는다. 그런데 원장의 `thstrm_amount` 의미가 재무제표별로 다르다:
  · IS·CIS — 당분기 **3개월**. 누계는 별도 필드 `thstrm_add_amount`(스테이지 `amount_cum`)로 온다
  · CF — **연초누계**. `thstrm_add_amount` 가 아예 오지 않아 `amount_cum` 이 183행 전건 NULL 이다
  · BS·SCE — 시점 잔액(기간 개념 없음). 정상
- 위험성: **silent unit mismatch.** 통합층 한 행 안에서 `revenue` 는 3개월, `cf_operating` 은 3·6·9개월로 제각각인데 컬럼명·타입·NULL 여부 어디에도 신호가 없다. 실측(삼성전자 FY2025 CFS, 억원):

  | 보고서 | `revenue`(3개월) | `cf_operating`(누계) |
  |---|---:|---:|
  | 1분기 | 791,405 | 165,808 |
  | 반기 | 745,663 | 339,410 |
  | 3분기 | 860,617 | 565,154 |
  | 사업 | 3,336,059 | 853,151 |

  누계 판정 근거: CF 를 3개월로 보면 1Q+2Q+3Q = 1,069,720 억원이 되어 연간 853,151 억원을 초과해 모순이다. 손익 쪽은 반대로 검산이 맞는다 — 1Q 791,405 + 2Q 745,663 = 1,537,068 억원 = 반기 `amount_cum` (원 단위까지 일치)
- 영향 팩터: **V04 PCR · Q04 발생액 · Q05 FCF수익률** (`docs/FACTORS.md` §1·§2). 특히 Q04 `(순이익 − 영업활동현금흐름) / 자산총계` 는 3분기에 **3개월 순이익에서 9개월 현금흐름을 빼는** 계산이 되어 부호까지 뒤집힌다. 연간만 쓰면 안전하나 분기 리밸런싱에서는 전량 오염
- **해소 (2026-08-26)**: 누계인 채로 **컬럼명에 기간을 싣는 쪽**으로 확정. CF 4컬럼에 `_ytd` 접미어(`cf_operating_ytd` 등), 손익 기간은 `financials.is_months`(분기 3 · 사업 12). 3개월 환산은 팩터 계산 층에서 한다 — CF 3개월 값은 **두 보고서를 결합해야** 나오는 파생이고(직전 누계가 같은 보고서 안에 없다), 재작성 오염을 사실 층에 들이지 않기 위해서다
- **표본 확대 검증**: `Revenue` 태그가 있는 8사 전건에서 `반기 amount_cum == 1Q amount + 2Q amount` 가 원 단위까지 일치. 삼성전자 단독 근거가 표본 전체로 일반화된다

**DEFECT-C03: 분기 보고서의 전기 비교값을 통째로 버린다 — 잘못된 필드를 읽는다**
- 상황: 분기·반기 보고서(`11012`·`11013`·`11014`)를 스테이지로 펼치는 경로. 사업보고서(`11011`)는 해당 없음
- 인풋: 10사 × FY2025 × 4보고서(8,833행)를 `build_stage.py` → `build_panel.py`
- 에러 위치: `src/build_stage.py:79-81` `TERMS` — `term_rank=1`(전기)을 `frmtrm_amount` 에서만 읽는다. 그런데 분기 보고서에서 그 필드는 재무상태표에만 오고, 손익·현금흐름·자본변동표에는 오지 않는다. 전기 값은 `frmtrm_q_amount`(전년 동기)에 들어 있다. 10사 분기 보고서 6,489행 실측 채움률:

  | `sj_div` | 행 | `frmtrm_amount` (지금 읽음) | `frmtrm_q_amount` (실제 값) |
  |---|---:|---:|---:|
  | BS | 1,314 | **1,308** | 0 |
  | IS | 110 | 0 | **110** |
  | CIS | 982 | 0 | **981** |
  | CF | 1,425 | 0 | **1,401** |
  | SCE | 2,658 | 36 | **2,636** |

- 위험성: **분기 YoY 성장률이 원천적으로 만들어지지 않는다** (`docs/FACTORS.md` §3 성장). 손익·현금흐름의 전기 값이 100% 결측이라 전년 동기 대비 증감을 계산할 재료가 없다. 더 나쁜 건 신호가 없다는 점 — `fin_reject` 에 `empty_term` 으로 31건 남지만, 그 사유가 "그 기에 금액 없음(정상)"으로 라벨링되어 있어 결손이 아니라 응답 구조로 읽힌다. 실제로는 값이 옆 필드에 멀쩡히 있다:
  ```
  삼성생명 FY2025 3Q 매출액  frmtrm_amount = (빈칸)  ← 읽는 필드
                          frmtrm_q_amount = 79조987억  ← 실제 전년 동기
  ```
- **해소 (2026-08-26) — 소멸**: `sj_div` 로 분기하는 대신 **당기(`term_rank=0`)만 펼치도록** 축소했다. 백필이 FY2015~2025 연간 + FY2016~2025 분기를 전부 받으므로 전년 동기는 **그해 보고서의 당기값**으로 확보된다 — YoY 에 전기 축이 필요 없다.
  전기 축의 고유 용도는 재작성 탐지 하나뿐인데 그건 상시 파이프라인이 아니라 감사 작업이다(DEFECT-S02). 원장이 `frmtrm_q_amount`·`bfefrmtrm_amount` 를 보존하므로 필요할 때 다시 펼치면 되고 정보 손실은 0이다.
  부수 효과: 팩트 15,762 → 8,693행, 패널 108 → 40행, 전항목 결측 유령 행 18 → 0

**DEFECT-S02: `term_rank≥1` 은 재작성 값이라 그 시점에 알 수 없던 수치를 준다 — look-ahead**
- **상황**: 같은 대차대조일을 사업보고서와 분기보고서가 다르게 말하는 경우. 10사 중 2사에서 발생
- **인풋**: `corp_code='00126256'`(삼성생명) `bsns_year='2025'` `fs_div='CFS'` 를 4개 `reprt_code` 로 호출한 뒤 `term_rank=1`(제69기말 = 2024-12-31)을 비교
- **에러 위치**: 원장이 아니라 **소비 규약**. 원장·스테이지는 두 값을 다른 행으로 정직하게 보존한다. `term_rank=1` 을 "전년 실적"으로 읽는 쪽이 틀린다
- **실측** (억원):

  | 보고서 | 부채총계 | 자본총계 |
  |---|---:|---:|
  | 사업보고서 | 2,741,161 | **381,026** |
  | 분기보고서 3종 | 2,794,809 | **327,379** |

  차 53,647억 = **자본의 16.4%**. 2024년말 실제 공시는 32.7조이고 38.1조는 2025년 금감원 보험부채 산출지침 변경에 따른 **소급 재작성치**다. 이마트도 제14기말에서 993억 차이가 난다
- **위험성**: **look-ahead.** 2024년말 기준 백테스트가 자본을 16.4% 과대계상한 채 PBR·ROE 를 계산한다. 예외도 NULL 도 나지 않는다
- **해소**: 스테이지를 당기만 펼치도록 축소(DEFECT-C03). 뷰 4종 전부 `WHERE term_rank = 0` 을 걸어, 감사 목적으로 전기 축을 재확장하더라도 사람이 보는 층에는 새어 들지 않게 했다

**DEFECT-P4-1: `page_no > total_page` 가 빈 응답이 아니라 마지막 페이지를 그대로 돌려준다**
- **상황**: 공시 스윕에서 창 재개 지점을 페이지 번호로 추정하는 모든 로직
- **인풋**: `list.json` `bgn_de='20100105'` `end_de='20100105'` `page_count=100` `page_no=6` (해당 창의 `total_page=5`)
- **에러 위치**: DART 응답 자체 — `status=000`, 응답의 `page_no` 는 6 인데 내용은 page 5 의 69행 그대로다. `rcept_no` 69/69 가 page 5 와 일치(DB 대조 실측)
- **위험성**: **silent 중복 + 완료 오판.** 실패 신호가 없어 "다 받았다"로 읽힌다. 재개 지점을 계산으로 구하면 마지막 페이지를 무한 재수신하면서 진행이 멈춘 것을 모른다
- **대응**: `src/sweep_disclosure.py` 가 상한을 매 응답의 `total_page` 로 다시 읽고 `page >= total_page` 에서만 종료한다. `mismatch` 로 끝난 창은 page 1 부터 전량 재수집

**DEFECT-P4-2: §1.4 `dart_disclosure` 확정 스키마가 응답 필드 2개를 버린다**
- **상황**: §1.4 스키마 또는 `src/dart_collect.py` 로 만든 테이블에 공시목록을 적재
- **인풋**: `list.json` 응답 행 — 실측 **9필드** `corp_cls, corp_code, corp_name, flr_nm, rcept_dt, rcept_no, report_nm, rm, stock_code`
- **에러 위치**: `docs/DART_DESIGN.md` §1.4 및 `src/dart_collect.py:22-23` — 컬럼이 7개뿐이라 **`corp_cls`(시장구분 Y/K/N/E)와 `rm`(정정·공시 플래그, 실측 `'정'`·`'공'` 값 존재)이 빠진다**
- **위험성**: **원장 계약 위반.** 원장의 유일한 계약이 무손실 보존인데 스키마가 필드를 버린다. `rm='정'` 은 정정공시 식별자라 PIT 판정에 직결되고, 다시 받으려면 34,406콜을 통째로 재실행해야 한다. 또 그 스키마의 테이블이 이미 있으면 `store()` 가 `row_hash` 컬럼 부재로 죽는다
- **대응**: §1.4 를 폐기하고 `store()` 의 동적 컬럼에 맡긴다 — 응답 키 합집합으로 테이블을 만들므로 필드가 늘어도 자동으로 따라간다. `sweep_disclosure.py` 는 진입 시 기존 테이블이 `store()` 규약(`row_hash` PK)인지 확인하고 아니면 중단한다

**DEFECT-S01 (소스 결함): 삼성생명 분기 보고서의 `자산총계`·`부채총계` 가 1분기 값에 고정 — DART 원본 오류**
- 상황: 보험사 정기보고서 수집. 우리 파이프라인이 아니라 **DART 응답 자체**가 틀렸다
- 인풋: `fnlttSinglAcntAll.json` `corp_code='00126256'` `bsns_year='2025'` `fs_div='CFS'`, `reprt_code` 를 `11013`·`11012`·`11014` 로 각각
- 에러 위치: 응답 `list` 의 `account_id='ifrs-full_Assets'` 행 — `thstrm_nm` 은 "제 70 기 1분기말"·"반기말"·"3분기말"로 정확히 갱신되는데 `thstrm_amount` 는 세 보고서 전부 `318858553000000` 로 동일하다. `Liabilities` 도 같은 증상(`2873680`억 고정). 주변 계정은 정상 갱신된다 — 당기법인세자산 2,086 → 2,684 → 4,351억, 사용권자산 3,219 → 3,569 → 3,388억
- 검산: `EquityAndLiabilities`(자본과부채총계)는 제대로 갱신된다. 회계상 자산총계와 같아야 하는데 어긋난다 (억원)

  | 보고서 | `Assets` | `EquityAndLiabilities` | 차이 |
  |---|---:|---:|---:|
  | 1분기 | 3,188,585 | 3,188,585 | **0** |
  | 반기 | 3,188,585 | 3,191,246 | −2,660 |
  | 3분기 | 3,188,585 | 3,353,068 | −164,483 |

  1분기는 `부채 2,873,680 + 자본 314,905 = 3,188,585` 로 항등식이 원 단위까지 맞는다. 즉 매핑은 정상이고 반기·3분기 원본만 stale 하다
- 위험성: **silent corrupt.** 자산총계를 분모로 쓰는 팩터가 전부 오염된다 — ROA·발생액(Q04)·자산성장률(G03)·부채비율. 3분기 기준 오차 −4.9% 이고 **부호나 예외 없이 조용히 통과**한다. 10사 40행 중 2행(5%)에서 발생했으므로 특수 사례로 볼 수 없다
- 탐지법: `Assets` vs `EquityAndLiabilities` 대조. 10사 실측에서 38/40 이 원 단위 일치했고 어긋난 2행이 정확히 이 건이다. **수집 후 상시 검사로 둘 것**

**DEFECT-C04: 원장에 조회용 인덱스가 없어 어떤 질의든 전수 스캔이다 (설계상 의도 + 소비 규약 부재)**
- **상황**: 원장을 직접 조회하는 모든 경로. 백필 진행 중 커버리지 확인, 결함 추적, 스테이지 재생성 전 사전 점검 등
- **인풋**:
  1. `SELECT * FROM dart_fin_raw WHERE req_corp_code='00126380' AND req_bsns_year='2023'`
  2. `SELECT ... WHERE account_id='ifrs-full_Revenue'`
- **에러 위치**: `src/backfill_dart.py` `store()` — 테이블을 만들 때 `row_hash TEXT PRIMARY KEY` 만 걸고 보조 인덱스를 만들지 않는다. `sqlite_master` 실측: `dart_fin_raw` 의 인덱스는 `sqlite_autoindex_dart_fin_raw_1`(PK) 하나뿐이다. `EXPLAIN QUERY PLAN` 은 `SCAN dart_fin_raw` 를 반환한다 — 인덱스를 못 탄다
- **실측** (3,802,395행 시점):

  | 질의 | 결과 행 | 소요 |
  |---|---:|---:|
  | `req_corp_code='00126380'` | 2,195 | **0.81초** |
  | `account_id='ifrs-full_Revenue'` | 13,279 | **0.67초** |

  전량 백필 후 예상 규모(약 1,900만 행, 현재의 5배)에서는 질의당 3~4초가 된다.
  실제로 이 조사 중 8사 랜덤 표본 집계 질의가 **120초 타임아웃**으로 죽었다.
- **위험성**: **데이터 손상은 아니다.** 원장은 append 전용이고 인덱스가 없는 편이 쓰기에 유리하므로 이 상태 자체는 설계 의도에 부합한다(§1.9 가 `dart_call_log` 에 대해 같은 근거를 명시한다). 문제는 **소비 규약이 문서에 없다**는 것이다:
  · 백필 도중 커버리지·정합성을 확인하려면 원장을 직접 볼 수밖에 없는데, 그때마다 초 단위가 든다. 조사자가 질의를 좁히지 않으면 타임아웃으로 죽고, 그게 "데이터가 없다"로 오독될 수 있다
  · 스테이지(`build_stage.py`)는 `ix_fact_concept`·`ix_fact_corp` 를 만들지만, **스테이지를 거치지 않고 원장에 직접 붙는 코드를 막는 장치가 없다.** KRX 원장에서 같은 실수가 실제로 위험했다(`SOURCE_AUDIT.md` RISK-1·2 — 원장 직접 조회 시 조인 0행·사전순 정렬)
- **조치**: 인덱스를 추가하지 **않는다**. 대신 규약을 명시한다 —
  1. **분석·집계는 스테이지 이후 층에서만.** 원장 직접 조회는 무결성 점검과 결함 추적에 한정한다
  2. 점검 질의는 `req_corp_code` 등으로 **먼저 좁힌다**. 전수 집계가 필요하면 스테이지를 재생성해서 거기서 한다
  3. 스테이지 생성은 어차피 전수 스캔 1회이므로 인덱스 부재의 영향을 받지 않는다
