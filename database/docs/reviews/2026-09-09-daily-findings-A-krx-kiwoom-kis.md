# findings A — KRX · 키움 · KIS 원장 수집기 조사

조사일 2026-09-09 · 읽기 전용 · 수집기 실행 0콜 · 저장소 수정 0건
대상 코드: `database/src/{backfill_krx,backfill_kw,backfill_kis,load_kiwoom_raw,master_daily,sustain,api,rt}.py`, `database/scripts/daily_wise.sh`
서버: `kael-server:~/quant-ledger` — **`src/` 8개 파일 전부 저장소 최신 main 과 바이트 동일**(`diff` 실측). 서버에만 있는 파일 1개: `src/probe_krx_timing.py`(저장소 미등록).

표기 규칙: **실측** = 서버 쿼리·로그·프로브 DB 에서 직접 잰 값 / **추정** = 코드에서 유추한 값 / **문서** = `docs/` 인용.

---

## 0. 한 줄 요약

1. **KRX 는 오늘 그대로 증분이 된다** — `--to` 인자가 있고 `ingest_log` 가 (엔드포인트, 날짜) 체크포인트다. 다만 **T+1 08:00 KST 이전에 돌리면 그날이 `holiday` 로 영구 확정**되는 결함이 있다(§3-1, DEFECT-A-01).
2. **키움은 증분 모드가 없다** — `backfill_kw.py` 에 `--to` 가 없고 `20260820` 이 3곳 하드코딩(`:223 :224 :230`)이며, `todo()` 의 재개 로직이 `--from` 을 늦추면 **전 종목을 스킵**한다. 코드 변경이 필수다(§1-2, DEFECT-A-02).
3. **KIS 는 증분을 돌리면 행이 증식한다** — `row_hash` 에 `req_d1`/`req_d2` 가 들어가 창이 바뀌면 같은 (종목, 날짜) 사실이 새 행이 된다. `kis_credit_balance` 에 **이미 1,084,443행(12.1%)이 그 중복**이다(실측, §1-4 DEFECT-A-03).
4. **오염 사고는 재현·정량화됐다** — ka10008 `dt=20260824`(수집 09:20 KST, 장중) 2,602행 중 **99.0%가 전일과 동일한 `poss_stkcnt`**. 정상일은 9.7%(실측, §3-2).
5. 갭은 **13거래일**(08-21 ~ 09-08). 이걸 다 채우는 데 **KRX 91콜(37초) + 키움 10,410콜(약 10분) + KIS credit 3,175콜(약 20분)** — 캡이 커서 1패스면 끝난다(§2).

---

## 1. 소스별 수집 단위와 증분 가능성

### 1-0. 전체 요약표

| 스크립트 | CLI 날짜 인자 | 수집 단위 | "최신일까지" 자동 | 체크포인트 | 재투입 멱등성 | 증분 가능? |
|---|---|---|---|---|---|---|
| `backfill_krx.py` | `--from` `--to` `--limit` | 1콜 = (엔드포인트, 1일, 전종목) | **없음** (`--to` 기본 `2026-08-20`) | `ingest_log` PK(endpoint, bas_dd) | **O** — `INSERT OR REPLACE`, PK(bas_dd_req, ISU_CD/IDX_NM) | **셸에서 `--to` 만 주면 가능** |
| `backfill_kw.py` | `--from` `--limit` (**`--to` 없음**) | 1콜 = (TR, 1종목, 캡 구간) 역방향 커서 | 없음 | `ingest_shard` PK(src_api, ticker, req_start, req_end) | **O** — `INSERT OR REPLACE`, PK(ticker, dt) | **불가 — 코드 수정 필요** |
| `backfill_kis.py` | 날짜 인자 **없음** (`--only` `--scope` `--limit` `--max-calls`) | span(100행/130일창) · asof(30행/42일스텝) · corp(1행) | 없음 (창은 `corp_ticker.last_dd` 상한) | `kis_ingest_log` PK(name, ticker, d1, d2) | **X** — `row_hash` 에 `req_d1/req_d2` 포함 → 창이 다르면 중복 행 | **불가 — 코드 수정 필요** |
| `master_daily.py` | 없음 (인자 없음) | 2콜 = (코스피·코스닥 전종목 스냅샷) | **항상 오늘**(UTC+9) | 없음 | **O(단 first-write-wins)** — `INSERT OR IGNORE`, PK(snap_date, code) | **이미 매일 돌고 있음** |
| `load_kiwoom_raw.py` | — | 일회성 JSONL→SQLite 이관 | — | — | **DROP TABLE 후 재생성** | **운영 금지** (§1-5) |
| `sustain.py` / `rt.py` | — | 유량 실측 도구 | — | — | — | 운영 대상 아님 |

### 1-1. `backfill_krx.py` — 유일하게 그대로 쓸 수 있다

```
:69   ap.add_argument("--from", dest="frm", default="2010-01-04")
:70   ap.add_argument("--to",   dest="to",  default="2026-08-20")
:83-84  done = {(r[0], r[1]) for r in con.execute(
          "SELECT endpoint, bas_dd FROM ingest_log WHERE status IN ('ok','holiday')")}
:94   if (path, d) in done: continue
:104-108  CREATE TABLE IF NOT EXISTS {tbl} (..., PRIMARY KEY ("bas_dd_req", "{key}"))
          INSERT OR REPLACE INTO {tbl} ...
:109-110  INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?)
```

- **멱등**: 테이블 PK 는 `(bas_dd_req, ISU_CD)`(시세·마스터·ETF) / `(bas_dd_req, IDX_NM)`(지수). `INSERT OR REPLACE` 라 같은 날짜를 다시 넣으면 덮어쓴다. 중복 0 (문서 `archive/SOURCE_AUDIT.md §8`: "KRX PK중복 0 · NULL 0 · 수집누락 0 · ingest_log 대조 4,094일 전건 일치").
- **강제 재수집 스위치는 없다** — `done` 스킵을 뚫으려면 `ingest_log` 행을 지워야 한다. `--force`/`--refresh` 없음.
- **휴장 판정**: `bdays()`(:60-64)가 주말만 거르고, 공휴일은 빈 `OutBlock_1` → `"holiday"`(:51-53). 가격(첫 엔드포인트)이 holiday 면 나머지 6개를 `note='skipped by price-probe'` 로 선기록(:113-117) — 하루 7콜이 1콜로 준다.
- **필요한 작업**: 없음. 셸에서 `--from 2026-08-21 --to $(TZ=Asia/Seoul date +%F)` 만 주면 된다. **단 §3-1 의 시각 가드가 반드시 앞에 붙어야 한다.**

### 1-2. `backfill_kw.py` — 증분 모드가 코드에 없다 · DEFECT-A-02

```
:296-300  p.add_argument("--tr", default="ka10014,ka20068,ka10060")
          p.add_argument("--from", dest="frm", default="20100101")
          p.add_argument("--limit", ...)          ← --to 없음
:223-224  con.execute("INSERT OR REPLACE INTO ingest_shard VALUES (...)",
            (api_id, tk, floor, "20260820", len(rows), t["cap"], st,
             reached, "20260820", stamp, nxt))    ← req_end · last_dt 하드코딩
:230      cursor = start_cursor or "20260820"     ← 커서 시작점 하드코딩
:278-292  floor = max(target_from, TRS[api_id]["floor"])
          seen = ... "SELECT ticker, status, next_cursor FROM ingest_shard
                      WHERE src_api=? AND req_start<=?", (api_id, floor)
          if st in ("done","empty","nodata"): continue
```

- **`--from 20260821` 로 돌리면 아무 일도 안 일어난다.** `floor = max('20260821', TR floor) = '20260821'` 이고, `todo()` 의 `req_start <= '20260821'` 조건에 기존 done 샤드(`req_start`=`20080623`/`20100101`/`20110725`/`20091101`)가 전부 걸려 **2,602종목이 전건 스킵**된다. 서버 `ingest_shard` 실측: ka10008/ka10060/ka20068 각 2,605 done, ka10014 2,545 done + 60 empty.
- **멱등성 자체는 있다** — 테이블 PK `(ticker, dt)`, `INSERT OR REPLACE`(:221). 같은 (종목, 날짜)를 다시 넣으면 덮어쓴다. 문제는 재투입을 **하게 만드는 경로가 없는 것**이다.
- **역방향 커서는 증분에 유리하다**: 1콜이 캡만큼 과거를 함께 준다(ka10014 372행 · ka10060 100 · ka20068 100 · ka10008 50). 13거래일 갭을 **1콜/종목**으로 메운다.
- **`ka10008` 은 날짜 파라미터가 아예 없다**(:60 `body=lambda tk,s,e: {"stk_cd":tk}`, 커서는 `next-key` 헤더 `A{tk}{e}`). 첫 콜은 무조건 "지금 시점 최신 50영업일"이 온다 → **§3-2 오염 경로의 원인**이자, 증분에서는 오히려 자동으로 최신을 잡는다.
- **필요한 최소 변경(추정)**: (1) `--to` 인자 추가하고 `"20260820"` 3곳을 그것으로 치환 (2) `todo()` 의 스킵 조건을 `req_end` 기준으로 바꾸거나 증분 전용 경로를 따로 두기 (3) 유니버스를 `tickers.txt` 고정 파일에서 `ka10099` 최신 스냅샷으로 교체(§5).

### 1-3. `backfill_kis.py` — 날짜 인자가 없고 창을 외부 DB 가 정한다

```
:219-225  --only(필수) --scope(delisted|all) --limit --max-calls   ← 날짜 인자 없음
:233-246  panel = f"{BASE}/data/build/equity_fin.db"
          cond = ("last_dd < (SELECT MAX(last_dd) FROM corp_ticker)"
                  if a.scope == "delisted" else "1=1")
          SELECT ticker, first_dd, last_dd FROM corp_ticker WHERE is_common=1 AND {cond}
            AND corp_name NOT LIKE '%기업인수목적%' AND corp_name NOT LIKE '%스팩%'
:189-215  windows(name, first, last)  — span: 130일 역방향 창 / asof: 42일 역방향 스텝
:258-267  done = kis_ingest_log status='ok'
          no_data = kis_call_log verdict='empty' 가 서로 다른 실행에서 2회 이상
```

- **수집 상한이 `data/build/equity_fin.db` 의 `corp_ticker.last_dd`** 다. 서버 실측: 파일 mtime **2026-08-26**, 3,672행 / `is_common` 3,478 / `MAX(last_dd)=20260820`. 이 파일을 `build_bridge.py` 로 다시 만들지 않으면 **KIS 는 08-20 을 절대 넘지 못한다.**
- **`--scope` 가 테이블마다 달랐다**(실측 `kis_ingest_log`): flow·short·loan·master 는 `delisted`(652종목 / loan 287), credit 만 `all`(3,175종목). 즉 **KIS 5테이블 중 살아있는 유니버스를 덮는 것은 `kis_credit_balance` 하나뿐**이다. 나머지 4개는 폐지종목 보완축이라 "일일 증분" 개념이 성립하지 않는다(대상이 이미 죽은 종목).

### 1-4. DEFECT-A-03 — KIS 는 재수집하면 같은 사실이 새 행으로 쌓인다

- **상황**: `backfill_kis.py` 가 `backfill_dart.store()` 를 재사용한다(`backfill_kis.py:25, 324`). `store()` 는 요청 파라미터를 `req_` 접두어로 컬럼에 넣고(`backfill_dart.py:454-455`) **그 컬럼까지 포함해 `row_hash` 를 만든다**(`:472, :476`). PK 는 `row_hash` 단독(`:460`), 적재는 `INSERT OR IGNORE`(`:483`).
- **인풋**:
  1. `--only credit --scope all` 실행 → `windows()` 가 42일 스텝 asof 창을 만들지만 응답은 30행뿐이라 **창이 겹친다**.
  2. 겹치는 구간의 같은 (종목, `deal_date`) 행이 `req_d2` 만 다른 채로 두 번 들어온다.
- **에러 위치**: `database/src/backfill_dart.py:454-455, 471-476` — `cols = keys + sorted(req) + ["dup_seq"]` 이고 `row_hash(cols, v)` 에 `req_d1`·`req_d2` 가 섞인다.
- **위험성**: **silent duplication.** 서버 실측 — `kis_credit_balance` 8,970,999행 중 `(req_ticker, deal_date)` 가 2건 이상인 쌍이 **517,648개, 행으로 1,084,443행(12.1%)**. 실제 행 대조:

  ```
  req_ticker deal_date  req_d1    req_d2    whol_loan_rmnd_stcn  row_hash
  000020     20100125   20100104  20100128  370576               ce6d9c5b41eb7444...
  000020     20100125   20100104  20100311  370576               36b57864b66b44d1...   ← 값 동일, req_d2 만 다름
  ```

  `kis_short_sale` 은 중복 **0** (span 창이 겹치지 않는다). 일일 증분을 새 창(`d2 = 오늘`)으로 돌리면 **asof 축 2테이블(`kis_investor_flow`·`kis_credit_balance`)은 매일 30일치를 통째로 다시 쌓는다.** 상위 stage 가 `(ticker, date)` 로 집계하면 잔고·순매수가 배수로 부풀 수 있다.
- **재현 test**: 없음.

### 1-5. `load_kiwoom_raw.py` — 서버에서 실행 금지

```
:5   BASE = "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/.../scratchpad"
:8   COLLECTED = "2026-08-21T15:47:00"   ← 수집 시각 상수
:9   REQ_START, REQ_END = "20250101", "20260820"
:29  con.execute(f"DROP TABLE IF EXISTS {s['tbl']}")
:39  con.execute("DROP TABLE IF EXISTS ingest_shard")
```
옛 맥 스크래치패드 경로가 박힌 **일회성 이관 스크립트**다. SPEC 에 ka10014·ka20068 둘뿐이고 `DROP TABLE` 로 시작한다. 증분과 무관하며, 서버에서 실행하면 원장 2테이블과 `ingest_shard` 전체가 사라진다. `rt.py:8, :50` · `sustain.py` 도 같은 옛 경로를 참조한다(운영 대상 아님).

### 1-6. `master_daily.py` + `daily_wise.sh` — 유일하게 돌고 있는 일일 경로

```
master_daily.py:28-29  snap = (utcnow + 9h).strftime("%Y%m%d")
              :49-51   PRIMARY KEY (snap_date, "code")
              :58      INSERT OR IGNORE INTO ...
              :43-45   cont-yn=='Y' 면 경고만 찍고 첫 페이지만 적재
scripts/daily_wise.sh:11  .venv/bin/python src/master_daily.py
                     :16  .venv/bin/python src/backfill_wise.py --mode full
크론(실측): 0 21 * * *  /bin/bash ~/quant-ledger/scripts/daily_wise.sh   (UTC 21:00 = KST 06:00, 매일)
```
- quant-ledger 크론은 **이 한 줄뿐**이다(서버 `crontab -l` 실측; 나머지는 전부 kael-system-v3·unitelegram·n8n).
- 실측 로그 `logs/daily_wise_0909.log`: `06:00:01 → 06:04:47 KST`, 코스피 2,486행 · 코스닥 1,822행, WISE 15,617요청 / 4.7분 / 54.8req/s.
- **멱등은 first-write-wins** — `INSERT OR IGNORE` 라 같은 `snap_date` 로 재실행해도 값이 안 바뀐다. 아침 스냅샷을 저녁에 덮어쓸 수 없다.
- **휴장 가드 없음** — 실측으로 20260905(토)·20260906(일) 스냅샷이 존재한다. 상태 스냅샷이므로 치명적이진 않으나 주말 행은 전일 복사본이다.

---

## 2. 테이블별 갭 (서버 실측 2026-09-09)

기준 거래일: **08-21, 08-24~28, 08-31, 09-01~04, 09-07, 09-08 = 13거래일**
(근거: `kael-system-v3/data/quant.db.daily_prices` 의 `trade_date` 실측 — 08-21 2,532종목 … 09-08 2,533종목, 결번 없음. `data/.kis_holidays.json` 실측 2026-09 휴장일은 09-24·09-25 추석 + 주말뿐이라 이 구간에 공휴일 없음. **09-09(오늘)은 거래일이며 아직 어느 소스에도 확정치가 없다.**)

### 2-1. KRX (7 엔드포인트)

| 테이블 / 엔드포인트 | 행수 | 기간 | MAX | `ingest_log` ok/holiday | 최근 20거래일 일별 행수 |
|---|---:|---|---|---|---|
| `krx_stk_bydd_trd` (`sto/stk_bydd_trd`) | 3,771,174 | 20100104~ | **20260820** | 4,094 / 245 | 942~943 |
| `krx_ksq_bydd_trd` (`sto/ksq_bydd_trd`) | 5,430,342 | 〃 | **20260820** | 4,094 / 245 | 1,819~1,822 |
| `krx_stk_isu_base_info` | 3,771,174 | 〃 | **20260820** | 4,094 / 245 | 942~943 |
| `krx_ksq_isu_base_info` | 5,430,342 | 〃 | **20260820** | 4,094 / 245 | 1,819~1,822 |
| `krx_kospi_dd_trd` (`idx/kospi_dd_trd`) | 195,711 | 〃 | **20260820** | 4,094 / 245 | **51 (고정)** |
| `krx_kosdaq_dd_trd` (`idx/kosdaq_dd_trd`) | 152,110 | 〃 | **20260820** | 4,094 / 245 | **40 (고정)** |
| `krx_etf_bydd_trd` (`etp/etf_bydd_trd`) | 1,688,735 | 〃 | **20260820** | 4,094 / 245 | 1,150~1,163 |

- 전 엔드포인트 `ingest_log` 4,339행 동일, bad(ok·holiday 아닌 것) **0**.
- **갭 = 13거래일 × 7엔드포인트 = 91콜.** 근거: 1콜 = (엔드포인트, 1일). 휴장 단축 없음(구간에 공휴일 0).
- **소요(추정)**: 실측 2.44콜/s → **37초**.

### 2-2. 키움 (5 TR)

| 테이블 | 행수 | 기간 | MAX(dt) | 종목수 | 결측 거래일 | 콜 단위(캡) | 필요 콜 |
|---|---:|---|---|---:|---:|---|---:|
| `ka10014_short_selling` | 3,971,630 | 20080623~ | **20260820** | 2,542* | 13 | 372행/콜 | **2,602** |
| `ka20068_lending_balance` | 6,988,296 | 20110725~ | **20260820** | 2,602 | 13 | 100행/콜 | **2,602** |
| `ka10060_investor_flows` | 7,621,338 | 20091222~ | **20260820** | 2,602 | 13 | 100행/콜 | **2,602** |
| `ka10008_foreign_holdings` | 7,682,844 | 20091015~ | **20260824** (오염) | 2,602 | 11 (+08-24 재수집) | 50행/콜 | **2,602** |
| `ka10099_stock_master` | 38,771 | 20260901~ | **20260909** (최신) | 4,308/일 | 0 | 시장당 1콜 | **2** |

\* `ka10014` 는 `ingest_shard` 에 done 2,545 + empty 60(스팩 57 포함, 문서 `SOURCE_AUDIT §1`).

- **`ka10008` 의 20260824·20260821 행은 08-24 09:20 KST(장중) 수집분이다** — §3-2 참조. `20260824` 는 오염, `20260821` 은 정상 범위.
- **갭 합계 = 4 TR × 2,602 + 2 = 10,410콜.** 근거: 캡(50~372행)이 13거래일보다 크므로 **종목당 1콜이면 갭 전체가 메워진다**. 정상 운영 시 일일 콜도 같은 10,410 (문서 `COLLECT_PLAN §4-1` 은 2,763종목 기준 11,054 로 적었다).
- **소요(추정)**: 4 TR 병렬 · TR 당 실측 4.44콜/s → 2,602/4.44 ≈ **9.8분**.

### 2-3. KIS (5 테이블)

| 테이블 | 행수 | 날짜 컬럼 | 범위 | MAX | 종목수 | `--scope` |
|---|---:|---|---|---|---:|---|
| `kis_investor_flow` | 1,001,370 | `stck_bsop_date` | 20091119~ | **20260814** | 652 | delisted |
| `kis_short_sale` | 939,610 | `stck_bsop_date` | 20100104~ | **20260814** | 652 | delisted |
| `kis_loan_trans` | 493,445 | `bsop_date` | 20140102~ | **20260814** | 287 | delisted |
| `kis_stock_info` | 652 | (corp축, 날짜 없음) | — | 수집 08-26 | 652 | delisted |
| `kis_credit_balance` | 8,970,999 | `deal_date` | 20070716~ | **20260818** | **3,175** | **all** |

- **flow·short·loan·master 는 "갭" 개념이 성립하지 않는다** — 대상이 폐지 652종목이라 최신 거래일 데이터가 존재하지 않는다. 최근 tail 실측: `kis_short_sale` 20260814 = **1종목 1행**, 20260807 = 3종목. 즉 이 4테이블은 백필 산출물이고 일일 증분 대상이 아니다.
- **`kis_credit_balance` 만 살아있는 유니버스를 덮는다** — 일별 2,523~2,530종목(실측 20거래일).
- **KIS 신용잔고는 T+2 확정이다(실측)**: `deal_date=20260818 ↔ stlm_date=20260820`, `20260814 ↔ 20260819`, `20260813 ↔ 20260818`. `req_d2=20260820` 로 조회했을 때 돌아온 최대 `deal_date` 가 **20260818**. → 조회일 D 에는 `deal_date <= D − 2세션` 만 받을 수 있다.
- **갭 = `deal_date` 08-19 ~ 09-07 = 14거래일.** (09-08 은 결제일이 09-10 이라 오늘은 못 받는다.)
- **필요 콜 = 3,175콜** (asof 30행/콜 → 종목당 1콜이 30영업일을 덮는다). **소요(추정)**: 실측 2.71콜/s → **약 20분**.
- 단 **§1-4 의 중복 증식**이 그대로 발동한다 — 3,175콜이 30일치를 다시 실어오고, 그중 이미 있는 16일치가 새 `row_hash` 로 재적재된다.

---

## 3. 데이터 가용 시각과 오염 위험

### 3-1. KRX — T+1 08:00 KST 실측 · DEFECT-A-01

증거: 서버 `data/evidence/krx_timing.db`(154행, `src/probe_krx_timing.py` 가 매시 `sto/stk_bydd_trd` 당일·전일 2콜).

| 기준일 | offset_d=1 최초 비영행 시각 | 행수 |
|---|---|---:|
| 20260824 | **2026-08-25 08:00** | 942 |
| 20260825 | **2026-08-26 08:00** | 944 |
| 20260826 | 2026-08-27 14:21 (프로브 공백) | 944 |
| 20260827 | **2026-08-28 08:00** | 944 |

- **08-28 은 5분 간격 프로브**: 07:05·07:10·…·07:55 전부 **0행**, **08:00 에 944행**. → 확정 공표는 **07:55 ~ 08:00 KST 사이**.
- **당일치(offset_d=0)는 어느 시각에도 0행이다** — 08-24 16:04~23:00, 08-25/26 00:00~23:00 전건 0. KRX OPEN API 는 **당일 데이터를 주지 않는다**.
- `ingest_log.collected_at` 은 백필 1회분(2026-08-23 15:00~18:00 UTC)뿐이라 시각 분포로는 아무것도 못 잰다 — 위 프로브 DB 가 유일한 실측 근거다.

> ### DEFECT-A-01: 08:00 이전 실행이 그날을 영구 휴장으로 확정한다
> - **상황**: `backfill_krx.py` 를 T+1 08:00 KST 이전(또는 당일)에 `--to` 로 그 날짜를 포함해 실행.
> - **인풋**: 1. `python src/backfill_krx.py --from 2026-09-09 --to 2026-09-09` 을 09-09 07:00 KST 에 실행 → 2. `sto/stk_bydd_trd?basDd=20260909` 가 빈 `OutBlock_1` 반환.
> - **에러 위치**: `database/src/backfill_krx.py:51-53` — `rows = r.json().get("OutBlock_1"); if not rows: return [], "holiday"`. 이어 `:109-110` 이 `status='holiday'` 로 `ingest_log` 확정, `:113-117` 이 나머지 6엔드포인트도 `holiday`("skipped by price-probe")로 선기록. 다음 실행에서 `:83-84` 의 `done`(status IN ('ok','holiday'))에 걸려 **영구 스킵**.
> - **위험성**: **silent data loss.** 거래일이 휴장으로 굳고, 재수집 스위치가 없어 `ingest_log` 행을 손으로 지우기 전에는 복구되지 않는다. 상위 stage 는 `ingest_log` 를 완결성 증거로 읽으므로 "수집 완료 · 휴장"으로 통과한다. 미래일자·주말도 같은 경로다(`bdays()` 는 주말만 거르고 미래일자는 안 거른다).
> - **재현 test**: 없음.

### 3-2. 키움 — 장중 수집 오염 실측 (ka10008, 2026-08-24)

`ka10008_foreign_holdings` 의 `collected_at` 분포(실측, 서버 TZ=UTC):

| dt | collected_at (UTC) | = KST | 행수 |
|---|---|---|---:|
| 20260819·20·21·24 | 2026-08-24T00:19:33 | 08-24 09:19 | 각 3 |
| 20260819·20·21·24 | **2026-08-24T00:20:54** | **08-24 09:20** | 각 2,598 |
| 20260819·20·21·24 | 2026-08-24T10:41:33 | 08-24 19:41 | 각 1 |

**장 개장(09:00) 20분 뒤에 그날 데이터를 받았다.** 결과:

| 대조 | 전일과 `poss_stkcnt` 동일 | `chg_qty = 0` |
|---|---:|---:|
| **20260824 vs 20260821** (오염일) | **2,577 / 2,602 = 99.0%** | **2,578 / 2,602 = 99.1%** |
| 20260820 vs 20260819 (정상일) | 252 / 2,602 = 9.7% | 254 / 2,602 = 9.8% |

→ `dt=20260824` 행은 사실상 **전일 잔고의 복사본**이다. 외국인 보유주식수 변동(`chg_qty`)이 99% 0 이므로 그대로 stage 로 올라가면 그날 외국인 순매수가 0 으로 잠긴다.

**코드에는 방어가 하나도 없다(실측):**
- `stex_tp` 는 **저장소 전체에 0건**(`grep -rn "stex_tp"` — `.py`·`.md` 전부). 프롬프트가 언급한 거래소 구분 가드는 존재하지 않는다.
- `backfill_kw.py` 에 시각 가드·거래일 판정·"확정 전이면 skip" 로직 없음. `worker()`(:200-272)는 응답을 받는 즉시 `INSERT OR REPLACE` 한다.
- `ka10008` 은 특히 위험하다 — 날짜 파라미터가 없어 첫 콜이 **무조건 호출 시점 최신 50영업일**을 준다(`:60`). 즉 언제 돌리든 "지금"이 섞인다.
- 유일하게 작동하는 방어는 **크로스소스 대조**다. 실측 20260820 KRX(stk+ksq) ↔ 키움 `ka10008`: **2,602종목 매칭, 종가 일치 2,602/2,602, 거래량 일치 2,602/2,602**(`abs(close_pric)` 적용). 문서 `SOURCE_AUDIT §2-2` 와 일치(정규장 기준, `venue_scope='KRX'`).
- **키움 확정 시각은 미측정.** KRX 처럼 시각 프로브 DB 가 없다. `ka10099` 는 06:00 KST 에 이미 그날 신규 상장을 담고 있으나(§5), 시세·수급 TR 의 확정 시각은 관측된 적이 없다.

### 3-3. KIS

- **`kis_credit_balance`: T+2 확정(실측)** — §2-3. `stlm_date` 가 조회 기준일이고 `deal_date` 는 그보다 2세션 앞이다.
- **flow / short / loan 의 확정 시각은 미측정** — `--scope delisted` 로만 돌려서 최신 거래일 응답을 관측한 적이 없다.
- **`kis_stock_info`(master)**: 시점 개념 없음. 조회 시점의 마스터 67필드 스냅샷(`lstg_abol_dt` 등).
- **토큰**: 파일 캐시 23h(`api.py:56`), 만료 시 캐시 삭제 후 재발급(`backfill_kis.py:154-163`).

### 3-4. 휴장일 처리 — 소스별로 완전히 다르다

| 소스 | 캘린더 소스 | 휴장일 동작 | 빈 응답 처리 |
|---|---|---|---|
| KRX | **없음** — `bdays()`(`backfill_krx.py:60-64`)가 주말만 거름 | 빈 `OutBlock_1` | `status='holiday'` **영구 확정** (DEFECT-A-01) |
| 키움 | **없음** | 역방향 커서라 응답에 없는 날은 자동 스킵 | `ended='empty'`(첫 청크) / `'exhausted'`(이후) → shard `empty` = 영구 스킵 |
| KIS | **없음** — `windows()` 가 캘린더일 기준 130일/42일 창 | 창 안에 거래일이 있으면 무관 | `empty` 1회는 재방문, 서로 다른 실행에서 2회면 `no_data` 확정(`:258-267`) |
| `master_daily` | **없음** | 가드 없음 → 주말·휴장일에도 스냅샷 생성 (실측 20260905 토·20260906 일) | `rc != 0` 또는 0행이면 그 시장 적재 안 함(`:39-42`) |

- **quant-ledger 안에 휴장일 정본이 없다.** 유일한 캘린더 구현은 kael-system-v3 쪽이다:
  `~/kael-system-v3/backend/clients/kis/holiday.py` — KIS `chk-holiday`(`BASS_DT` 커서 페이지네이션, 최대 40페이지), 캐시 `data/.kis_holidays.json`, 검증 규칙 `len(holidays) >= 100` + 전건 해당 연도 + 토·일 90개 이상(빈 응답이 정상 캐시를 덮은 2026-06-05 사고 재발 방지).
  실측 캐시(fetched 2026-09-01): 2026-09 휴장 = **20260905·06·12·13·19·20(주말) + 20260924·25(추석) + 20260926·27(주말)**.
- 문서 `COLLECT_PLAN §4-1` 은 0단계로 "KIS 캐시 → API 캘린더 판정, 둘 다 실패면 **영업일 가정하고 진행**(빈 응답을 휴장으로 확정하는 순환 차단)" 을 이미 처방했으나 **구현은 없다**.

---

## 4. 유량 한도 · 예산

### 4-1. 소스별 한도와 코드 스로틀

| 소스 | 코드 스로틀 | 공식/문서 한도 | **서버 실측** |
|---|---|---|---|
| KRX | `RATE = 3.0`/s (`backfill_krx.py:20`, 주석 "초당 제한 미확인 → 보수적으로") · 429/503 시 1.0s 백오프 x4 | 문서 `COLLECT_PLAN §5-2` "자체상한 9,000/일". `archive/A1_krx_endpoints.md` 에 한도 기재 없음 | **2.44콜/s · 28,881콜 / 11,837초(3.29h) · `rate` 0 · `error` 0** (`logs/KRX_DONE.txt`) |
| 키움 | `RATE = 4.4`/s **per TR**(`backfill_kw.py:28`) · `BACKOFF 0.5s`(429 복구 실측 419~759ms) · `api.kiwoom()` 은 콜당 `sleep(0.25)`(`api.py:120`) · `Circuit`(연속 3회 30·60·…600s, 12회 중단) | 문서: **일 20,000콜이 유일한 검증 안전 한도**(`COLLECT_PLAN §0-1`). API ID 별 5콜/s(`backfill_kw.py:3-5`) | **4.44콜/s (`logs/progress.txt`, ka10008 68콜/15초, 429=0)** |
| KIS | `PACE = 0.12`s(`backfill_kis.py:29`, 주석 "실측 초당 5콜, 네트워크 왕복이 병목이라 유량 제한(20/s)에 안 닿는다") · `RETRY_BASE 3.0` 지수백오프 · `QUOTA_RETRY 2`·`QUOTA_STREAK 3` | 코드 주석: "실측 2026-08-26 하루 33,379콜 무오류" | **일 204,235콜 무오류**(2026-08-27 UTC, `kis_call_log` verdict 는 ok/empty 뿐 — **quota 0건**). 08-26 은 153,447콜. 속도 **2.71콜/s**(08-27) · 2.39콜/s(08-26) |

> **KIS 일일 한도는 문서·코드가 적은 33,379 의 6배 이상이다** — 실측 204,235콜/일을 무오류로 통과했다. 코드 주석(`backfill_kis.py:41`)을 갱신할 근거.

### 4-2. 앱키 공유 — 키움만 진짜 제약이다

`api.py:6-13` 이 `~/kael-system-v3/.env` 를 직접 읽는다. 즉 **키움 앱키가 카엘 운영 시스템과 공유 중**이고, 문서(`COLLECT_PLAN §0-2`, `FINAL_SUMMARY §Y3`)는 "카엘이 검증 한도 20,000콜을 전량 소진한다"고 적었다(이번 조사에서 카엘 실사용량은 재측정하지 않았다 — **문서 인용**).

- 현재 quant-ledger 의 키움 사용량은 **2콜/일**(`master_daily`)뿐이다.
- 키움 일일 증분 10,410콜을 얹으면 카엘분과 합쳐 30,410콜/일 → 검증 범위 밖.
- KRX·KIS·DART 는 별도 채널이라 무관.

### 4-3. 1일치 증분 예산 (전부 추정, 위 실측 속도 기준)

| 소스 | 콜 | 속도(실측) | 소요 | 비고 |
|---|---:|---|---|---|
| KRX 7엔드포인트 | **7** | 2.44/s | 3초 | 재확인 5영업일 포함 시 35콜 / 15초 |
| 키움 4 TR × 2,602종목 | **10,408** | 4.44/s × 4병렬 | **9.8분** | 캡이 커서 며칠 밀려도 콜 수 불변 |
| 키움 `ka10099` | **2** | — | 1초 | 이미 크론에 있음 |
| KIS `credit` × 3,175 | **3,175** | 2.71/s | **19.5분** | 중복 증식 문제 해결 전엔 보류 권고 |
| **합계(평시)** | **약 13,592** | | **약 30분** | 병렬이면 20분 |
| 13거래일 갭 일괄 | KRX 91 + 키움 10,410 + KIS 3,175 = **13,676** | | **약 31분** | 캡 덕분에 1일치와 거의 같다 |

---

## 5. 유니버스 갱신

### 5-1. 소스별 유니버스 출처

| 소스 | 유니버스 | 갱신 방식 | 서버 실측 |
|---|---|---|---|
| KRX | **없음(날짜축)** | 1콜 = 그날 전종목 → **자동** | 신규·폐지 모두 그날 응답에 반영 |
| 키움 백필 | `data/jsonl/tickers.txt` | **고정 파일** | 2,602줄, mtime **2026-08-24 00:20** — 갱신 경로 없음 |
| 키움 마스터 | `ka10099` 2콜 | **매일 자동** | `ka10099_stock_master` 20260901~20260909, 일 4,308행(코스피 2,486 · 코스닥 1,822) |
| KIS | `data/build/equity_fin.db` `corp_ticker` | **고정 스냅샷** | mtime 2026-08-26, 3,672행 / `is_common` 3,478 / `MAX(last_dd)=20260820` |
| DART | `data/corps.txt` | 고정 | 3,478줄, mtime 2026-08-25 |
| WISE | `ka10099` 최신 스냅샷 | **매일 자동** | `daily_wise_0909.log`: "유니버스 = 키움 마스터 20260909 (2,563종목)" |

### 5-2. 실측 churn (2026-09-01 → 09-09, 6거래일)

| 구분 | 코드 | 이름 | 시장 | regDay / 사유 |
|---|---|---|---|---|
| 신규 | `386380` | 스카이랩스 | 코스닥 | **regDay 20260904** |
| 신규 | `0238P0` | TIGER 미국S&P500미국채혼합50 | ETF | 20260908 |
| 신규 | `610111` `610112` | 메리츠 일본국채 ETN 2종 | ETN | 20260904 |
| 소멸 | `082640` | 동양생명 | 거래소 | 거래정지 |
| 소멸 | `096610` | 알에프세미 | 코스닥 | 관리종목 |
| 소멸 | `471050` | 대신밸런스제17호스팩 | 코스닥 | 관리종목 |

→ **거래일당 약 0.7 신규 / 0.5 소멸.**

### 5-3. 신규 상장 첫날이 빠지는 경로 — 실재한다

- **`ka10099` 는 상장일 당일 아침에 이미 잡는다(좋은 경로)**: `386380` 스카이랩스 `regDay=20260904` 가 **`snap_date=20260904` 스냅샷(수집 2026-09-03T21:00:01 UTC = 09-04 06:00 KST)에 이미 존재**한다(실측). 즉 06:00 KST 마스터 스냅샷은 그날 상장 종목을 개장 전에 포착한다.
- **`tickers.txt` 는 못 잡는다(나쁜 경로)**: `tickers.txt`(08-24 고정) ↔ `ka10099` 20260909 스냅샷 차집합 실측 —
  - `tickers.txt` 에만 있는 코드 **4개**: `082640`(동양생명, 거래정지) · `096610` · `269620` · `471050` → 폐지·정지 종목을 계속 두들긴다(키움은 `rc=0` + 0행을 주므로 로그상 정상, 문서 `SOURCE_AUDIT §0`).
  - `ka10099` 에만 있는 코드 **1,710개** — 대부분 우선주·ETF·ETN(키움 백필 유니버스가 보통주 2,602 뿐). 여기에 **09-04 상장 `386380` 이 포함**된다. 현재 코드로 키움 증분을 돌리면 **스카이랩스는 영원히 수집되지 않는다.**
- **KIS `corp_ticker` 도 못 잡는다**: 08-26 스냅샷 고정. `build_bridge.py` 재실행 없이는 신규 상장이 안 들어오고, `last_dd` 상한 때문에 **08-20 이후 창 자체가 생성되지 않는다**.
- **KRX 는 문제없다**: 날짜축이라 상장일 당일 응답에 자동 포함.

---

## 6. "하루가 끝났다" 판정 쿼리

기대치는 전부 **서버 실측 최근 20거래일(20260722~20260820) 분포**로 뒷받침한다.

### 6-0. 실측 기준선

| 테이블 | 20거래일 min | max | 평균 | 안정성 |
|---|---:|---:|---:|---|
| `krx_stk_bydd_trd` | 942 | 943 | 942.6 | 매우 안정 |
| `krx_ksq_bydd_trd` | 1,819 | 1,822 | 1,820.5 | 매우 안정 |
| `krx_kospi_dd_trd` | 51 | 51 | 51 | **고정** |
| `krx_kosdaq_dd_trd` | 40 | 40 | 40 | **고정** |
| `krx_etf_bydd_trd` | 1,150 | 1,163 | 1,158 | 안정(증가 추세) |
| `ka10008_foreign_holdings` | 2,598 | 2,602 | 2,600.3 | 매우 안정 |
| `ka10060_investor_flows` | 2,598 | 2,602 | 2,600.3 | 매우 안정 |
| `ka20068_lending_balance` | 2,598 | 2,602 | 2,600.3 | 매우 안정 |
| `ka10014_short_selling` | **2,081** | **2,334** | 2,249.1 | **변동 12%** — 절대 임계 금지 |
| `ka10099_stock_master` | 4,307 | 4,309 | 4,308 | 안정 |
| `kis_credit_balance` | 2,523 | 2,530 | 2,525.9 | 매우 안정 |

→ `ka10014` 만 절대 임계가 위험하다. 나머지는 하한 고정치를 써도 된다.

### 6-1. KRX — D 일자 수집 완료

```sql
-- A. 원장 계약: 7 엔드포인트가 모두 ok 이고, 하나라도 holiday 면 7개 전부 holiday 여야 한다
SELECT :D AS bas_dd,
       COUNT(*)                                   AS n_endpoints,      -- 기대 7
       SUM(status='ok')                           AS n_ok,
       SUM(status='holiday')                      AS n_holiday,
       SUM(status NOT IN ('ok','holiday'))        AS n_bad,            -- 기대 0
       MIN(collected_at)                          AS first_seen
FROM ingest_log WHERE bas_dd = :D;
-- 판정: n_endpoints=7 AND n_bad=0 AND (n_ok=7 OR n_holiday=7)

-- B. 행수 게이트 (실측 20거래일 기준, 하한은 min의 0.98배)
SELECT (SELECT COUNT(*) FROM krx_stk_bydd_trd       WHERE bas_dd_req=:D) AS stk,     -- >= 920
       (SELECT COUNT(*) FROM krx_ksq_bydd_trd       WHERE bas_dd_req=:D) AS ksq,     -- >= 1780
       (SELECT COUNT(*) FROM krx_stk_isu_base_info  WHERE bas_dd_req=:D) AS stk_base,-- = stk
       (SELECT COUNT(*) FROM krx_ksq_isu_base_info  WHERE bas_dd_req=:D) AS ksq_base,-- = ksq
       (SELECT COUNT(*) FROM krx_kospi_dd_trd       WHERE bas_dd_req=:D) AS kospi,   -- = 51
       (SELECT COUNT(*) FROM krx_kosdaq_dd_trd      WHERE bas_dd_req=:D) AS kosdaq,  -- = 40
       (SELECT COUNT(*) FROM krx_etf_bydd_trd       WHERE bas_dd_req=:D) AS etf;     -- >= 1120
-- 강한 불변식(실측 전건 성립): stk = stk_base AND ksq = ksq_base

-- C. holiday 오확정 탐지 — DEFECT-A-01 감시. 캘린더가 거래일이라 말하는데 holiday 로 기록된 날
SELECT bas_dd FROM ingest_log
WHERE status='holiday' AND bas_dd IN (:거래일_목록)   -- 캘린더 소스는 KIS chk-holiday
GROUP BY bas_dd;
-- 판정: 0행이어야 한다. 1행이라도 나오면 그 날짜의 ingest_log 7행을 지우고 재수집.
```

### 6-2. 키움 — D 일자 수집 완료

```sql
-- A. 행수 (하한은 실측 min 기준)
SELECT :D AS dt,
  (SELECT COUNT(*) FROM ka10008_foreign_holdings WHERE dt=:D) AS foreign,   -- >= 2590
  (SELECT COUNT(*) FROM ka10060_investor_flows   WHERE dt=:D) AS flows,     -- >= 2590
  (SELECT COUNT(*) FROM ka20068_lending_balance  WHERE dt=:D) AS lending,   -- >= 2590
  (SELECT COUNT(*) FROM ka10014_short_selling    WHERE dt=:D) AS shorts;    -- 절대치 금지 -> 아래 B

-- B. ka10014 는 추세 게이트 (문서 COLLECT_PLAN §4-3 게이트 C)
WITH base AS (SELECT dt, COUNT(*) n FROM ka10014_short_selling
              WHERE dt < :D GROUP BY dt ORDER BY dt DESC LIMIT 20)
SELECT (SELECT COUNT(*) FROM ka10014_short_selling WHERE dt=:D) * 1.0
     / (SELECT AVG(n) FROM base) AS ratio;          -- >= 0.80

-- C. 장중 오염 탐지 (§3-2 재발 방지). 정상일 9.7% / 오염일 99.0% 로 갈린다
WITH a AS (SELECT ticker, poss_stkcnt p FROM ka10008_foreign_holdings WHERE dt=:D),
     b AS (SELECT ticker, poss_stkcnt p FROM ka10008_foreign_holdings WHERE dt=:D_prev)
SELECT COUNT(*) n, ROUND(100.0*SUM(a.p=b.p)/COUNT(*),1) pct_stale
FROM a JOIN b USING(ticker);
-- 판정: pct_stale <= 30 (실측 정상 9.7% · 오염 99.0%). 초과 시 그 날짜를 폐기하고 재수집.

-- D. 크로스소스 대조 — KRX 종가·거래량 <-> 키움 (실측 20260820 2,602/2,602 완전일치)
ATTACH 'file:data/raw/kiwoom.db?mode=ro' AS kw;
WITH k AS (SELECT ISU_CD c, TDD_CLSPRC p, ACC_TRDVOL v FROM krx_stk_bydd_trd WHERE bas_dd_req=:D
           UNION ALL
           SELECT ISU_CD,  TDD_CLSPRC,  ACC_TRDVOL  FROM krx_ksq_bydd_trd WHERE bas_dd_req=:D)
SELECT COUNT(*)                                                   AS matched,
       SUM(CAST(k.p AS INTEGER) = ABS(CAST(f.close_pric AS INTEGER))) AS same_close,
       SUM(CAST(k.v AS INTEGER) =     CAST(f.trde_qty  AS INTEGER))   AS same_vol
FROM k JOIN kw.ka10008_foreign_holdings f ON f.ticker = k.c AND f.dt = :D;
-- 판정: same_close = same_vol = matched (실측 전건 일치). abs() 는 필수 —
--       키움 가격의 +/- 는 방향 표시자다(SOURCE_AUDIT §2-3).

-- E. 마스터 스냅샷 (소멸성 축 — 그날 못 받으면 복구 불가)
SELECT snap_date, COUNT(*) n, COUNT(DISTINCT mrkt_tp) mkts
FROM ka10099_stock_master WHERE snap_date=:D GROUP BY snap_date;
-- 판정: mkts=2 AND n >= 4200 (실측 4,307~4,309)
```

### 6-3. KIS — D 일자 수집 완료

```sql
-- A. 신용잔고. D 를 조회하면 deal_date 는 D-2세션까지만 온다(실측)
SELECT deal_date, COUNT(*) n, COUNT(DISTINCT req_ticker) tk
FROM kis_credit_balance WHERE deal_date = :D_minus2 GROUP BY deal_date;
-- 판정: n >= 2480 AND tk = n  (실측 20거래일 2,523~2,530, tk=n 전건)

-- B. 중복 증식 감시 (DEFECT-A-03). 증분을 돌린 직후 반드시 본다
SELECT COUNT(*) AS dup_pairs FROM (
  SELECT req_ticker, deal_date FROM kis_credit_balance
  WHERE deal_date >= :D_minus40 GROUP BY 1,2 HAVING COUNT(*) > 1);
-- 현재 baseline: 전체 517,648쌍 / 1,084,443행(12.1%). 증분 후 증가분이 0 이어야 한다.

-- C. 유닛 완결성
SELECT name, status, COUNT(*) FROM kis_ingest_log
WHERE ts >= :run_start GROUP BY 1,2;
-- 판정: status='ok' 외의 행이 나오면 kis_call_log 의 verdict/code 를 본다
--       (quota 는 실측 0건 — 나오면 진짜 한도다)
```

### 6-4. 배치 전체 종결 판정 (제안)

```
필수(실패 = 배치 실패)
  KRX  §6-1 A + B 전건 통과
  키움 §6-2 A(3테이블 >= 2590) + C(pct_stale <= 30) + D(크로스소스 전건 일치) + E(마스터 2시장)
경고(로그만)
  키움 §6-2 B (ka10014 ratio >= 0.80)
  KIS  §6-3 A
중단 신호
  §6-1 C 가 1행이라도 반환  -> DEFECT-A-01 발동. 즉시 알림 + 그 날짜 ingest_log 삭제 후 재수집
  §6-3 B 증가분 > 0         -> DEFECT-A-03 발동. KIS 증분 중단
```

---

## 7. 플랜 작성자에게 — 결정이 필요한 쟁점

코드·실측으로 확정되지 않아 사람이 골라야 하는 것들.

### C-1. 실행 시각 — KRX 08:00 KST 가 하한을 못 박는다
- KRX 는 **T+1 07:55~08:00 KST 확정**(실측). 그 전에 돌리면 그날이 영구 휴장으로 굳는다(DEFECT-A-01).
- 문서 `COLLECT_PLAN §4-1` 의 "19:00 KST 시작" 은 **KRX 를 당일 받는다는 전제**였고, 그 전제는 프로브로 반증됐다(당일치는 23:00 까지도 0행). 문서 자체가 `[2026-09-02 결정 ⑨]` 로 재검토를 지시하고 있다.
- 후보:
  - (a) **T+1 06:00 KST 로 통합** — 이미 `daily_wise.sh` 가 도는 시각. 단 **KRX 는 아직 안 나온다**(06:00 < 08:00). KRX 만 D-1 이 아니라 D-2 를 받게 된다.
  - (b) **T+1 08:10 KST** — KRX 확정 직후. `daily_wise` 와 2시간 10분 간격. 키움 10,410콜 10분 + KIS 20분 → 08:40 종료.
  - (c) **06:00(마스터·WISE) + 08:10(KRX·키움·KIS) 2단 분리** — 소멸성 축(ka10099)은 지금 시각 유지, 시계열은 KRX 확정 뒤.
- **키움 확정 시각이 미측정이라 (b)/(c)의 08:10 이 키움에도 안전한지 모른다.** KRX 처럼 `probe_krx_timing.py` 방식의 키움 프로브(ka10008 종목 1개 x 매시)를 며칠 돌려 확정 시각을 재는 것을 권고. **이 프로브 없이 08:10 을 고르면 §3-2 사고를 시각만 바꿔 반복할 위험이 있다.**

### C-2. 키움 증분 모드 — 코드 변경 범위를 정해야 한다
- 최소안: `backfill_kw.py` 에 `--to` 추가 + `"20260820"` 3곳(`:223 :224 :230`) 치환 + `todo()` 스킵 조건 재정의.
- 문제는 `ingest_shard` PK 가 `(src_api, ticker, req_start, req_end)` 라 **매일 다른 `req_end` 로 돌리면 종목당 샤드 행이 하루 1개씩 늘어난다**(2,602 x 4 TR x 일 = 연 270만 행). 별도 `ingest_daily` 테이블을 두는 안 vs 샤드 행을 그냥 쌓는 안 — 선택 필요.
- 대안: 증분 전용 스크립트를 신설하고 `backfill_kw.py` 는 백필 전용으로 동결. (기존 재개 로직을 안 건드리는 쪽이 안전하나 코드 중복)

### C-3. KIS 를 일일 증분에 넣을 것인가
- 현재 KIS 5테이블 중 **살아있는 유니버스를 덮는 것은 `kis_credit_balance` 하나뿐**이다(나머지 4개는 폐지종목 보완축).
- 넣으면 **DEFECT-A-03 이 매일 발동**한다 — asof 30일 창이 겹쳐 하루 3,175콜이 최대 30일치를 중복 적재. 고치려면 `store()` 의 `row_hash` 에서 `req_d1`/`req_d2` 를 빼거나 KIS 전용 store 를 만들어야 하는데, **`req_*` 보존은 `backfill_kis.py:13-17` 이 명시한 설계 원칙**(`FID_ORG_ADJ_PRC` 가 응답 의미를 바꾼 전례)이라 정면 충돌한다.
- 선택지: (a) KIS 증분 보류, 신용잔고는 주 1회 배치 (b) `row_hash` 에서 날짜 창 파라미터만 제외(의미를 바꾸는 파라미터는 유지) (c) `(req_ticker, deal_date)` UNIQUE 인덱스 신설 + `INSERT OR REPLACE`.
- 또한 **`corp_ticker`(equity_fin.db, 08-26 고정)를 매일 갱신할 것인가**도 결정 사항 — 안 하면 KIS 는 08-20 을 못 넘는다.

### C-4. 유니버스 소스를 `tickers.txt` 에서 `ka10099` 로 옮길 것인가
- 현재 키움 증분을 그대로 돌리면 **09-04 상장 스카이랩스(386380)가 영구 누락**되고, **폐지 4종목(082640 등)을 매일 헛되이 두들긴다**(키움은 폐지에 `rc=0`+0행 → 로그상 정상).
- `ka10099` 최신 스냅샷은 상장일 06:00 KST 에 이미 신규를 담는다(실측). WISE 수집기는 이미 그렇게 쓴다(`daily_wise_0909.log`: "유니버스 = 키움 마스터 20260909 (2,563종목)").
- 결정 필요: **보통주 필터 규칙**. `ka10099` 4,308행에는 우선주·ETF·ETN 이 섞여 있고(`kind` A 3,933 / Q 375), 백필 유니버스 2,602 와 맞추려면 필터가 필요하다. WISE 는 2,563 을 뽑는데 그 필터가 `backfill_wise.py` 안에 있다 — 재사용할지 새로 정할지.
- 부수 결정: 폐지 종목을 증분에서 뺄 때 **마지막 거래일 데이터를 반드시 받았는지** 확인하는 절차가 필요하다(빼는 순간 영구 누락).

### C-5. 휴장일 판정을 어디서 가져올 것인가
- quant-ledger 에 캘린더가 없다. 후보:
  - (a) kael-system-v3 `data/.kis_holidays.json` 캐시를 읽기 전용으로 참조 — 콜 0, 단 타 시스템 파일에 의존.
  - (b) `holiday.py` 를 quant-ledger 로 이식 + 자체 캐시 — 연 1~40콜.
  - (c) KRX 응답으로 사후 판정 — **DEFECT-A-01 때문에 단독으로는 위험**(빈 응답을 휴장으로 확정하는 순환).
- 문서 `COLLECT_PLAN §4-1` 0단계 처방("둘 다 실패면 영업일 가정하고 진행")은 (a)/(b) 를 전제한다.

### C-6. 08-24 오염 행을 어떻게 처리할 것인가
- `ka10008_foreign_holdings` `dt=20260824` 2,602행이 99% 전일 복사본이다.
- 선택지: (a) 재수집해 덮어쓴다(`INSERT OR REPLACE` PK (ticker,dt) 라 그대로 덮인다 — 콜 2,602) (b) 삭제 후 재수집 (c) 그대로 두고 stage 에서 격리.
- **`dt=20260821` 행도 같은 실행(08-24 09:20 KST)에서 왔다.** 08-21 은 정상 범위로 보이지만(전일 동일 비율 미측정) 같이 재수집하는 쪽이 안전하다.

### C-7. 키움 앱키 분리
- 문서가 3곳에서 "일일 운영에서도 앱키 분리가 필요하다 — 백필만의 문제가 아니다" 라고 못 박았다(`COLLECT_PLAN §4-1` 말미, `§0-2`, `FINAL_SUMMARY §153`).
- 키움 증분 10,410콜/일은 이 결정 없이는 카엘 운영과 한도를 다툰다. **이번 조사에서 카엘의 실제 키움 일일 사용량은 재측정하지 않았다** — 플랜 착수 전 실측 권고(카엘 로그로 콜 0 측정 가능).

### C-8. 미측정으로 남은 것 (콜을 써야 알 수 있다)
| 항목 | 왜 모르나 | 재는 법 |
|---|---|---|
| 키움 시세·수급 TR 확정 시각 | 프로브 DB 없음. 백필은 전부 과거 데이터 | `probe_krx_timing.py` 방식 키움 판 (1종목 x 매시 = 일 24콜 x 며칠) |
| KIS flow·short·loan 확정 시각 | `--scope delisted` 로만 돌려 최신일 관측 0 | 살아있는 종목 1개로 매시 프로브 |
| KRX 초당·일일 한도 | 코드 주석 "미확인". 2.44콜/s 에서 429 를 못 봤을 뿐 | 상한 탐색(권장 안 함 — 현재 속도로 충분) |
| 카엘의 키움 일일 실사용량 | 문서 인용값(20,000)만 있음 | kael-system-v3 로그 집계(콜 0) |
