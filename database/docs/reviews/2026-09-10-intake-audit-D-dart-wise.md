# DART·WISE 원장 검수 — 2026-09-10 적재분

- 검수 시각: 2026-09-10 (KST) / 서버 시계는 UTC (`dart.db` mtime `Sep 9 22:16` = 09-10 07:16 KST)
- 대상: DART 접수일 D=20260909 증분 · WISE 09-10 스냅샷(`fetched_date='2026-09-10'`)
- 접근: 전부 `mode=ro` 읽기 전용. API 호출·파일 쓰기·크론 조작 없음.
- **시각축 주의**: `dart_disclosure.collected_at`·`dart_call_log.ts`·`ws_run_log.run_at`·
  `ws_coverage.checked_at` 은 전부 naive **UTC**, `ws_raw.fetched_date` 는 **KST 날짜**다.
  그래서 오늘 06:47~07:16 KST 런의 흔적은 `2026-09-09T21:47 ~ T22:16` 에 있다.

---

## 1. 검사 결과 표

| # | 항목 | 판정 | 수치·근거 |
|---|---|---|---|
| 1 | rcept_dt=20260909 구성 | **정상** | 530행 / 고유 rcept_no 525 / `stock_code` 빈 값 106 / `corp_code` 결측 0 / `dup_seq` 전부 0. corp_cls Y 206·K 190·E 129·N 5. 상장사 행 424, 고유 419 |
| 1b | 로그 419·419·525·424 vs 원장 530 | **정상 (집계 정의 차이)** | 530 = 고유 525 + 내용개정 중복 5. 로그의 `공시 419건·상장사 행 419` 는 `plan()` 의 `SELECT DISTINCT rcept_no, corp_code, report_nm … stock_code<>''`(`src/daily/dart_daily.py:284-287`), 게이트의 `n_filings=525`·`n_listed_rows=424` 는 `_gate_filings` 의 `COUNT(DISTINCT rcept_no)`·`SUM(stock_code<>'')`(`:385-388`). 네 수치 모두 원장과 일치 |
| 1c | 스윕 뒤 추가 도착 여부 | **아님** | `collected_at` 분포: `2026-09-09T07` 401행(09-09 16:00 KST 최초 런) + `2026-09-09T22` 129행(오늘 런). 오늘 런 이후 추가 도착 0 |
| 2 | 09-10 접수 3건 | **설계대로** | 20260909000542 본느 `[첨부정정]주요사항보고서(유상증자결정)`, 20260909000543·000544 엠젠솔루션 `주요사항보고서(유상증자결정)`. 전부 `collected_at=2026-09-09T22:11:27`. 스윕 창은 `--from 2026Q3` → `20260701~20260930` 이라 미래 접수분 포함이 정상. `main()` 이 `calendar.prev_trading_day` 로 D 를 정하므로(`dart_daily.py:667-671`) 이 3건은 내일(D=20260910) 처리된다 |
| 3a | 지분공시 후속 축 | **결측 3건** | 대량보유 공시 65건 → `dart_majorstock` 매칭 63 (2건 결측). 임원소유 81건 → `dart_elestock` 매칭 80 (1건 결측). → 발견 M3 |
| 3b | 주요사항보고 후속 축 | **미구현 21건** | 주요사항보고서 39건 중 15개 `dart_*_decsn` 축 매칭 18건, 미매칭 21건. → 발견 M4 |
| 4 | correction 37건 | **축 대응 불일치** | 09-09 정정 접두 공시 91행(`[기재정정]` 89·`[첨부정정]` 2), 상장사 고유 70건. `[기재정정]` 은 축이 **정정본 rcept_no 만** 보유(원본은 축에서 사라짐), `[첨부정정]` 은 반대로 **원본 rcept_no 를 유지**한다. 원본·정정본이 축에 공존하는 사례는 없음(모호성 없음, 대신 결측 방향). → 발견 M5 |
| 5 | ingest_log 오늘 | **정상** | ts ≥ `2026-09-09T21:00` 인 행 595 = 유닛 594 + `disclosure` 1. status: `ok` 262 · `no_data` 332 · `mismatch` 1(disclosure). **error·pending 0건**. 해제(unlock) 579 → 신규 유닛 15개(직전 `ingest_log` 행 없음) 차이로 설명됨 |
| 6 | doc_store 대상 0건 | **규칙상 맞음** | `_DOC_TARGET_SQL`(`dart_daily.py:429-443`)·`backfill_docs.PRIORITIES`(`:46-52`) 대상은 주식분할/병합결정·사업·반기·분기보고서뿐. 09-09 접수분에 그런 report_nm 0건(있는 건 `감사보고서` 3건뿐 — 대상 규칙 밖). `backfill_docs` 도 P1 1,410·P2 52,332·P3 120,588 전부 "수집할 것 0" 으로 즉시 종료 |
| 7 | dart_call_log 오늘 | **정상** | k2 1,036 / k3 0 / **kael 0**. status `000` 704 · `013` 332. 에러·예외 0. endpoint: `list.json` 442, `majorstock/elestock` 각 87, DS005 15종 각 28. 로그의 `ours=1,036/40,000` 과 일치 |
| 8 | Q3 창 mismatch 6건 | **원인 규명 / 개별 특정 불가** | 원장 44,162 vs `total_count` 44,156. 나머지 66개 분기 창은 **전부 diff=0**. 원인은 DART 측 공시 삭제이고(아래 증거), 원장이 삭제를 기록하지 않아 개별 6건은 API 없이 특정 불가. 과제가 제안한 `req_page_no`/`collected_at` 기반 판별은 **무효**(근거: `backfill_dart.py:483` `INSERT OR IGNORE`, 해시에 `collected_at` 불포함 → 재스윕이 기존 행을 갱신하지 않는다). → 발견 H3 |
| 9 | ws_run_log / ws_coverage | **정상 (잔류 3행)** | `2026-09-09T21:04:48 full` n_stocks 2,563 · n_req 15,578 · n_ok 15,578 · **n_bad 0**. `ws_coverage` checked_at `2026-09-09` = covered 804 + none 1,759 = 2,563 ✓. 단 테이블 총행수는 2,566 — 미갱신 3행 잔류 → 발견 L2 |
| 10 | 커버리지 변동 | **오탐 3 / 진성 1** | 상실 4종목(0010V0·036010·190510·342870), 신규 1종목(029460). 807→804 설명 완료. 상실 4건 중 **3건은 false-none** → 발견 H1 |
| 11 | ws_raw 20260910 | **정상** | 15,578행 = 커버 804×15 + 무커버 1,759×2 ✓. ep 6종 × pkey 조합으로 15요청 구성. bytes=0 **0건**. sha256 이 09-09 와 100% 동일한 ep **없음**(c1010001 0/803, cF5001 1/4169, cF4002 25/803, cF5002 646/2409, cF3002 748/803, c1050001_data 3023/6578) → 갱신 정지 ep 없음. 32바이트 극소 응답 33행 → 발견 L1 |
| 12 | v3 복사분 | **정지 + 부분 스냅샷** | 4개 v3_* 테이블 전부 `copied_at` 최대 `2026-09-02T03:27`. **09-10 분 없음(8일 정지)**. `v3_consensus_annual` max sync_date `2026-09-02` 는 130행/50종목짜리 부분 복사(직전 `2026-09-01` 은 17,956행/2,553종목) → 발견 H2 |

---

## 2. 발견

### H1 (high) — WISE 커버 판정이 첫 연도만 보고 종목 전체를 버린다 (false-none)

- **무엇이**: `ws_coverage.status` 가 `none` 으로 뒤집히고, 그 결과 `ws_raw` 에 15개 ep 중 2개만 남는다.
- **몇 건**: 오늘 3건 (상실 4건 중 3건). 무커버 판정 전체 1,759종목 중 같은 원인이 몇 개인지는 재프로브 없이는 셀 수 없다.
- **상황**: `backfill_wise.py --mode full` 일일 실행. `c1050001_data`(pkey='')로 기간 목록을 받아 `ymms=[202612,202712,202812]` 를 만든 뒤, **`ymms[0]`(=202612) 하나의 `cF5001` 응답만으로** 커버 여부를 정한다.
- **인풋**:
  1. `cF5001?cmp_cd=036010&yymm=202612` → 09-10 응답: `eps=0 sales=0 tp=0`
  2. `is_covered()` → False → `covered=False`
  3. 이후 `cF5001/cF5002` 의 **202712·202812 요청이 통째로 건너뛰어진다**
- **에러 위치**: `src/backfill_wise.py:263-270`
  ```
  263  covered = True
  264  for ep, pk, url in jobs_for(cmp_cd, ymms)[1:]:
  265      if not covered and ep in ("cF5001", "cF5002"):
  266          continue        # 무커버 확정 후 잔여 연도 요청은 낭비다
  ...
  269      if ep == "cF5001" and pk == ymms[0] and v == "ok":
  270          covered = is_covered(body)
  ```
  `is_covered()` 자체(`:165-180`)는 EPS·매출·목표주가 3신호를 보지만, **호출되는 연도는 `ymms[0]` 하나뿐**이다.
- **예시 3건** (09-09 스냅샷 원문을 zlib 해제해 `chart1.select_item`/`chart2.select_item`/`chart1.target_price` 의 non-null 개수를 실측):

  | cmp_cd | 09-09 202612 | 09-09 **202712** | 09-09 202812 | 09-10 202612 | 09-10 202712 |
  |---|---|---|---|---|---|
  | 036010 | eps=4 sales=4 | **eps=4 sales=4** | eps=0 | eps=0 → none | *미요청* |
  | 190510 | eps=4 sales=7 | **eps=4 sales=7** | eps=0 | eps=0 → none | *미요청* |
  | 342870 | eps=3 sales=3 | **eps=3 sales=3** | eps=0 | eps=0 → none | *미요청* |
  | 0010V0 | eps=9 sales=9 | eps=0 | eps=0 | eps=0 → none | *미요청* (진성 무커버) |

  즉 세 종목은 **FY2027 추정치가 하루 전까지 실재했는데** FY2026 추정이 소멸한 것만 보고 무커버로 확정됐다.
- **추정 원인**: 수집기 버그. `is_covered()` 의 docstring 이 스스로 *"오판 비용이 비대칭(false-none = 영구 손실)"* 이라고 적어 두었는데, 프로브 연도를 1개로 좁힌 최적화(`:265-266`)가 그 원칙을 깬다. 원천 특성 측면의 방아쇠는 연말이 가까워질수록 소형주의 당해 연도(FY2026) 컨센서스가 먼저 만료된다는 점이라, **10~12월로 갈수록 오탐이 계속 늘어난다**.
- **하류 영향**: 해당 종목의 `cF3002`(손익 추정)·`cF4002`(지표 추정)·`c1010001`(추정기관수·투자의견)·`c1050001_data` flag=2/4 가 전부 미수집 → equity 층 컨센서스 팩터(추정 EPS·목표주가·ROE_E·추정 리비전)가 그 종목·그 날에 결측. 매일 반복되므로 시계열에 종목 단위 구멍이 생기고, 팩터 유니버스가 조용히 줄어든다.
- **보조 관측**: 반대 방향 증거도 있다 — 029460 은 09-09 에 202612 `eps=0` 으로 none 이었다가 09-10 에 `eps=1`(202612·202712·202812 전부 1)로 covered 로 복귀했다. 09-09 시점에 202712 를 안 찍어 본 탓에 그날도 오탐이었을 가능성이 높다.

### H2 (high) — v3 복사 파이프라인이 09-02 이후 정지, 게다가 마지막 스냅샷이 부분본

- **무엇이**: `v3_consensus_annual` · `v3_consensus_revision_daily` · `v3_consensus_revision_compare` · `v3_analyst_opinions` 전부.
- **몇 건**: 4개 테이블 전부 8일 정지. 그리고 `v3_consensus_annual` 의 최신 파티션 1개(130행)가 부분본.
- **상황**: 일일 크론 `daily_ledger` 는 `daily_wise`(= `backfill_wise.py`)만 돌린다. 로그(`logs/daily_ledger_20260910.log`)에 `sync_v3_wise` 실행 흔적이 없다.
- **인풋 / 수치**:

  | 테이블 | 최신 키 | 그 파티션 행수 | 직전 파티션 | copied_at |
  |---|---|---|---|---|
  | `v3_consensus_annual` | sync_date `2026-09-02` | **130행 / 50종목** | `2026-09-01` 17,956행 / 2,553종목 | 2026-09-02T03:27:42 |
  | `v3_consensus_revision_daily` | base_date `2026-08-31` | 625행 | `2026-08-28` 631행 | 2026-09-02T03:27:42 |
  | `v3_consensus_revision_compare` | sync_date `2026-09-02` | 978행 (전체) | — | 2026-09-02T03:27:43 |
  | `v3_analyst_opinions` | snapshot_date `2026-09-01` | 전체 254,925행 | — | 2026-09-02T03:27:43 |

- **에러 위치**: 누락된 단계. `backend/ops/sync_v3_wise.py` 가 `logs/daily_ledger_20260910.log` 의 어느 단계에도 없다(단계는 `daily_wise` → `kiwoom fetch` → `kis credit` → `dart` 4개뿐).
- **위험성**:
  1. **결측** — 컨센서스 리비전 DB 를 따로 만든 이유가 "v3 는 2027E·2028E 이력을 매일 덮어쓴다" 인데, 복사가 멈춘 8일치 리비전 이력은 v3 쪽에서 이미 덮여 **복구 불가**다.
  2. **silent corrupt** — `MAX(sync_date)` 로 최신 스냅샷을 고르는 하류 질의는 2,553종목이 아니라 **50종목**을 최신으로 본다. 커버리지가 98% 사라진 채로 정상처럼 응답한다.
  - eps/per NULL 비율(정상 파티션 `2026-09-01` 기준): eps NULL 5,963/17,956(33.2%), per NULL 10,153/17,956(56.5%) — 이 자체는 적자·추정부재 종목이라 원천 특성으로 보이며 결함은 아니다.

### H3 (high) — Q3 창은 DART 측 삭제 때문에 영구 `mismatch`, 그리고 10-01 이후 재스윕에서 빠진다

- **무엇이**: `ingest_log`(name='disclosure', 20260701~20260930).status = `mismatch`, `dart_disclosure` 의 Q3 고유 rcept_no 44,162 vs `total_count` 44,156.
- **몇 건**: 초과 6건. 다른 66개 분기 창은 diff 0 (합계 diff = 6, 양수 창 1개).
- **상황·인풋**: `sweep_disclosure.py:271-276`
  ```
  271  n_db   = stored_rows(con, bgn, end, distinct=True)
  272  if tc != n_recv or tc != n_db:
  275      mark(con, bgn, end, "mismatch", n_recv, tc, tp, st)
  ```
  `n_db` 는 **그 창에서 여태 한 번이라도 적재된** 고유 rcept_no 다. DART 가 공시를 목록에서 지우면 `total_count` 는 줄지만 원장은 줄지 않는다 → 등식이 영구히 깨진다.
- **삭제가 실제로 일어났다는 증거** (질의 QQ/RR):
  - 09-09T07 스윕이 Q3 창 페이지 001~400 에 **400/400 페이지 전부** 새 행을 만들었다(그 구간 798행, 그중 208페이지는 정확히 1행씩).
  - 그 1행짜리들은 **같은 rcept_no 가 직전 페이지로 한 칸 내려온 것**이다: `20260701900681` 0007→0006, `20260701000675` 0009→0008, `20260701000606` 0008→0007, … `20260701000039` 0002→0001.
  - 페이지가 100건 고정이므로, 한 레코드가 페이지 경계를 한 칸 앞으로 넘는 현상이 **모든 페이지에서 동시에** 일어나려면 창 앞쪽(1페이지 안)에서 레코드가 사라져 이후 전 위치가 당겨져야 한다. 신규 도착으로는 이 패턴이 나오지 않는다(DART 는 지각 등록분을 정렬해 끼워 넣지 않고 **꼬리 페이지에 붙인다** — 오늘 page 0442 에 rcept_dt 20260825·20260903 인 10건이 새로 등장한 것이 그 증거다).
- **개별 6건을 특정하지 못하는 이유**: `store()` 는 `INSERT OR IGNORE`(`backfill_dart.py:483`)이고 row_hash 는 `내용 + req_* + dup_seq`(`:455`)로만 만든다. `collected_at` 은 해시에 없고 **기존 행은 재스윕에서 갱신되지 않는다**. 실측으로도 오늘 44,156건을 다시 받았는데 새 행은 171행뿐이고, 나머지는 `collected_at` 이 08-30 인 채 그대로다. 따라서 "오늘 다시 안 온 행" 이라는 표식이 원장에 존재하지 않는다.
- **위험성 (두 갈래)**:
  1. **게이트 무력화** — 열린 창은 삭제가 한 번이라도 있으면 분기가 끝나도 `ok` 가 될 수 없다. `sweep_disclosure.py:213` 이 `mismatch` 창을 매번 page 1 부터 전량 재수집하므로 **매일 442콜을 영구히 태우고**, "대조 실패" 경보가 상시화돼 진짜 페이징 누락과 구분되지 않는다.
  2. **데이터 결측(10-01 이후)** — `dart_daily.sweep_from_for()`(`:474-478`)가 D 가 속한 분기만 `--from` 으로 주고, `sweep_disclosure.py:308` 의 `--to` 기본값은 `q_end(today)` 다. 10-01 부터 스윕 계획은 `[20261001~20261231]` 뿐이라 **Q3 창은 다시 스윕되지 않는다**. 오늘 관측된 것처럼 Q3 목록에는 8월 접수분(`20260825000126` 등)이 지금도 새로 나타나는데, 10-01 이후 나타나는 것들은 영구 결측이 된다.

### M1 (mid) — `dart_disclosure` 는 같은 rcept_no 의 내용 개정마다 행을 추가한다

- **무엇이**: `dart_disclosure` 전체. Q3 창 기준 45,683행 / 고유 44,162 → 1,521행 초과.
- **몇 건**: Q3 창에서 2행인 rcept_no 1,505건, 3행인 것 8건. 원인 분해: `rm` 값 변경 461건, `req_page_no` 변경 978건, `report_nm` 변경 9건.
- **예시 3건**:
  - `20260909000291` 금양 `주요사항보고서(회사분할결정)` — 두 행 모두 page `0440`·`dup_seq=0`, **`rm` 만 `''`(09-09T07:41) → `'정'`(09-09T22:11)** 로 다름.
  - `20260909000299`, `20260909000312` — 동일 패턴(page 0439, rm 변경).
- **에러 위치**: `src/backfill_dart.py:455` `cols = keys + sorted(req) + ["dup_seq"]` → `row_hash` 가 응답 내용 전체(=`rm` 포함)와 `req_page_no` 를 다 물고, `:483` 이 `INSERT OR IGNORE` 라 개정본이 새 행으로 쌓인다.
- **추정 원인**: 설계 의도(원문 보존)와 하류 계약의 충돌. `sweep_disclosure` 자신은 대조를 `DISTINCT rcept_no` 로 해서 피해가지만(`:129-136` 주석), 원장 소비자는 그 규약을 모른다.
- **하류 영향**: stage 문서층·equity 가 `SELECT … FROM dart_disclosure WHERE rcept_dt=…` 를 그대로 쓰면 공시 1건이 2건으로 세어진다(09-09 은 525건이 530행). 특히 `rm='정'` 행과 `rm=''` 행 중 무엇이 최신인지 알려주는 컬럼이 없다 — `collected_at` 이 유일한 단서인데 이건 "그 행이 처음 적재된 시각" 이라 개정 순서를 보장하지 않는다.

### M2 (mid) — 축 테이블의 `rcept_dt` 포맷이 `dart_disclosure` 와 다르다

- **무엇이**: `dart_majorstock.rcept_dt`·`dart_elestock.rcept_dt` 는 `2026-09-09`(하이픈), `dart_disclosure.rcept_dt` 는 `20260909`.
- **몇 건**: 두 테이블 전량. `WHERE rcept_dt='20260909'` 는 **0행**을 돌려준다(실제 09-09 축 행은 존재).
- **예시 3건**: `20260909000011`·`20260909000018`·`20260909000020` — 전부 `dart_majorstock.rcept_dt='2026-09-09'`.
- **추정 원인**: 원천 특성. DART 의 `majorstock.json`·`elestock.json` 응답 필드가 하이픈 형식이고, `store()` 는 값을 가공하지 않는다(원장 원칙). 결함이 아니라 **미문서화된 계약 차이**다.
- **하류 영향**: `dart_disclosure d JOIN dart_majorstock m ON d.rcept_dt=m.rcept_dt` 형태의 질의가 예외 없이 조용히 0행을 낸다. 날짜 범위 필터도 문자열 비교라 `BETWEEN '20260901' AND '20260930'` 이 하이픈 값에는 걸리지 않는다. `rcept_no` 조인은 안전.

### M3 (mid) — 지분공시 3건이 후속 축에 없다

- **무엇이**: `dart_majorstock` 2건, `dart_elestock` 1건.
- **몇 건**: 09-09 기준 major 65건 중 2건, ele 81건 중 1건. 09-01~09-09 누적으로 major 8건·ele 2건 비슷한 격차가 보인다(일자별 공시 고유건수 vs 축 고유건수: major 09-01 74/73, 09-04 68/66, 09-07 44/43, 09-09 65/63).
- **예시 3건**:
  - `20260909000036` 스모트로닉(corp 00301422) `[기재정정]주식등의대량보유상황보고서(약식)`
  - `20260909000270` 태웅(corp 00186799) `주식등의대량보유상황보고서(일반)`
  - `20260909000161` TS트릴리온(corp 01353024) `임원ㆍ주요주주특정증권등소유상황보고서`
- **추정 원인**: 수집기 버그 아님 — 세 corp 모두 오늘 콜이 `status=000` 으로 정상 응답했다(`majorstock.json` corp 00301422 n_rows=19, 00186799 n_rows=2; `elestock.json` 01353024 n_rows=12). 축에는 같은 corp 의 **다른 rcept_no** 가 들어와 있다(태웅: `20250103000383`·`20260909000308`; TS트릴리온: `20260909000171`). 즉 DART 의 지분공시 상세 API 가 공시목록의 모든 rcept_no 를 1:1 로 돌려주지 않는다(정정본·약식·보고자 단위 최신본만 반환하는 원천 특성으로 보인다).
- **하류 영향**: 공시목록을 기준으로 "지분 변동 사건" 을 세는 하류는 축에 없는 rcept_no 를 결측으로 본다. 대량보유 비율(`stkrt`) 시계열 자체는 다른 rcept_no 로 커버되므로 **값의 손실보다 사건-키 정합성 문제**다. `rcept_no` 를 조인 키로 쓰는 파이프라인(예: 문서층 rcept_dt 맵)에서 조용히 행이 빠진다.

### M4 (mid) — 주요사항보고서 39건 중 21건은 대응 엔드포인트 자체가 없다

- **무엇이**: `dart_daily.DS005_ENDPOINTS`(`:58-62`)가 DS005 중 15종만 구현. 나머지 유형은 축 테이블이 없다.
- **몇 건**: 09-09 상장사 주요사항보고서 39건 중 축 매칭 18건, 미매칭 21건(54%).
- **예시 3건**:
  - `20260909000019` 이노테나 `주요사항보고서(자기주식취득신탁계약체결결정)` — DART `tsstkAqTrctrCnsDecsn` 미구현
  - `20260909000116` 대선조선 `주요사항보고서(소송등의제기)` — `lwstLg` 미구현
  - `20260909000286` 광무 `주요사항보고서(타법인주식및출자증권양수결정)` — `otcprStkInvscrInhDecsn` 미구현
  - (그 외: 자기전환사채 만기전취득/매도, 자기주식취득신탁 해지, 유형자산 양수, 타법인주식 양도)
- **추정 원인**: 의도된 범위 제한으로 보인다 — `dart_daily.py:52-56` 주석이 "report_nm → 엔드포인트 매핑표를 두지 않고 전량 재호출" 이라고만 적었을 뿐 **15종이 DS005 전부라는 주장은 없다**. 다만 15종이 어디까지인지 문서화가 없어 "새 유형이 조용히 빠진다" 는 그 주석의 우려가 그대로 실현돼 있다.
- **하류 영향**: 자기주식 신탁계약(체결/해지)은 유통주식수·수급 팩터에, 타법인주식·유형자산 양수도는 기업행위 이벤트에 직접 걸린다. equity 층 `corp_actions` 가 이 유형들을 DART 축에서 못 받으므로 KRX 마스터 역산에만 의존하게 된다.

### M5 (mid) — 정정 공시와 원 공시의 축 대응 규칙이 유형마다 반대다

- **무엇이**: `dart_*_decsn`.rcept_no vs `dart_disclosure`.rcept_no.
- **몇 건**: 09-09 상장사 정정 공시 70건. 그중 주요사항보고서 19건이 이 규칙에 걸린다.
- **예시 3건**:
  - `[기재정정]` — 금양: 원본 `20260909000291`(주요사항보고서(회사분할결정), 오늘 `rm` 이 `'정'` 으로 바뀜)은 `dart_cmp_dv_decsn` 에 **없고**, 정정본 `20260909000470` 만 있다.
  - `[기재정정]` — 쎄노텍: 원본 `20260909000132`(자기주식취득결정, `rm='정'`)는 `dart_tsstk_aq_decsn` 에 **없고**, 정정본 `20260909000412` 만 있다.
  - `[첨부정정]` — 우성: 정정본 `20260909000530`(회사합병결정)이 `dart_cmp_mg_decsn` 에 **없고**, 원본 `20260904000355` 가 남아 있다.
- **추정 원인**: 원천 특성. DART 의 DS005 상세 API 는 `[기재정정]` 이면 **정정본 rcept_no 로 레코드를 갈아치우고**, `[첨부정정]` 이면 본문이 안 바뀌었으므로 원본 rcept_no 를 유지한다. 수집기는 응답을 그대로 적을 뿐이다.
- **하류 영향**: "공시목록 → 상세 축" 을 `rcept_no` 로 조인하는 파이프라인이 정정 건에서 항상 한쪽을 놓친다. 원본 기준 조인은 `[기재정정]` 건을 결측 처리하고, 정정본 기준 조인은 `[첨부정정]` 건을 결측 처리한다. 원장 안에 "이 rcept_no 를 무엇이 대체했는가" 를 적은 컬럼이 없어(`rm='정'` 은 정정이 있었다는 사실만 알려준다) 하류에서 복원할 수 없다.

### L1 (low) — WISE flag=4 매트릭스가 32바이트 빈 본문으로 온다

- **무엇이**: `ws_raw`(ep=`c1050001_data`, pkey=`T4:YYYYMM`), `bytes=32`.
- **몇 건**: 09-10 스냅샷 33행 / 11종목.
- **예시 3건**: `088260`(T4:202606·202706·202806), `293940`(T4:202703·202803·202903), `330590`(T4:202606·202706·202806).
- **추정 원인**: 원천 특성. 전부 **12월 결산이 아닌 종목**(리츠 등, 결산월 03·05·06·09)이고, WISE 의 flag=4 변동 매트릭스가 비12월 결산 `yymm` 에 빈 응답을 준다. `validate()`(`backfill_wise.py:136-161`)는 `{` 로 시작하고 알려진 최상위 키가 있으면 통과시키므로 걸리지 않는다.
- **하류 영향**: 해당 11종목의 컨센서스 리비전(투자의견 점수 610100 포함)이 값 없이 저장된다. 무해하지만 "저장됨 = 값 있음" 으로 가정하는 하류는 빈 dict 를 파싱하게 된다.

### L2 (low) — `ws_coverage` 에 유니버스에서 빠진 종목 3건이 잔류

- **무엇이**: `ws_coverage`(총 2,566행) vs 오늘 확인 2,563행.
- **몇 건**: 3건. `082640`(covered, checked_at `2026-09-02T21:02`), `471050`(none, `2026-09-03T21:04`), `096610`(none, `2026-09-07T21:02`).
- **추정 원인**: `ws_coverage` 는 `INSERT OR REPLACE` 로만 갱신되고(`backfill_wise.py:325-326`) 유니버스에서 사라진 종목의 행을 지우지 않는다. 082640 은 코드 주석(`:139-141`)이 "거래정지 종목" 으로 지목한 바로 그 종목이다.
- **하류 영향**: `SELECT COUNT(*) FROM ws_coverage WHERE status='covered'` 같은 집계가 상장폐지·정지 종목을 계속 센다. `--mode daily` 로 돌릴 때 `skip` 집합(`:227`)에도 계속 들어간다.

### L3 (low) — `corp_cls='E'` 인데 `stock_code` 가 채워진 23행이 "상장사" 로 계획에 들어간다

- **무엇이**: `dart_disclosure`(rcept_dt=20260909) 중 corp_cls E 129행 가운데 23행이 `stock_code<>''`.
- **추정 원인**: DART 의 `corp_cls` 는 현재 시점 값이라 상장구간과 어긋난다(기존에 기록된 함정). `plan()`·`_gate_filings` 는 `stock_code<>''` 만 보므로(`dart_daily.py:286`, `:387`) 이 23행을 상장사로 취급한다.
- **하류 영향**: 오늘은 콜 낭비 수준(기타법인 corp 에 DS005 15콜)이라 무해. 다만 `corp_cls` 와 `stock_code` 중 어느 쪽이 상장 판정의 정본인지 원장에 명시가 없다.

### L4 (정보, 결함 아님) — `rcept_dt` ≠ `rcept_no` 접두 8자리

- 전체 원장 125,709행(고유 125,668)에서 `substr(rcept_no,1,8) <> rcept_dt`. 최근 예: rcept_dt 20260910 ↔ 접두 20260909 3건, rcept_dt 20260909 ↔ 접두 20260908 18건, rcept_dt 20260908 ↔ 20260907 40건.
- 원천 특성이다 — DART 는 접수번호를 **접수 시각** 기준으로, `rcept_dt` 를 **공시 시각**(18시 이후 접수분은 익일) 기준으로 매긴다. `plan()`·게이트가 전부 `rcept_dt` 로 자르므로 처리 누락은 없다. 다만 `rcept_no` 접두를 날짜로 쓰는 하류 코드가 있으면 하루 어긋난다.

---

## 3. 확인하지 못한 것과 이유

1. **Q3 창 초과 6건의 개별 rcept_no** — 원장이 "이번 스윕에서 다시 왔는가" 를 기록하지 않는다(`INSERT OR IGNORE` + 해시에 `collected_at` 불포함). 특정하려면 `list.json` 을 다시 호출해 44,156건 집합과 원장 44,162건을 차집합해야 하는데, 이번 검수는 API 호출 금지다. 대신 **삭제가 원인이라는 것**은 페이지 시프트 시그니처로 확정했다(발견 H3).
2. **H1 의 FY2027 추정치가 지금도 살아 있는지** — 확인하려면 `cF5001?yymm=202712` 를 실제로 호출해야 한다. 09-09 스냅샷에서 3종목 모두 202712 에 EPS·매출 추정이 있었다는 것까지가 원장으로 증명 가능한 범위다.
3. **오탐 무커버의 전체 규모** — 무커버 1,759종목 각각에 대해 `ymms[1]` 을 프로브해야 알 수 있다. 오늘 새로 상실한 4건만 검사했다.
4. **`dart_majorstock`/`dart_elestock` 결측 3건이 DART 응답에 실제로 없었는지** — 콜은 `status=000` 이었고 응답 본문은 보존하지 않으므로(축 테이블만 적재), 응답에 없었는지 `store()` 가 흘렸는지는 원장으로 구분 불가. 다만 `store()` 는 응답 행 수를 로그에 남기고 축 행 수와 일치하므로 응답에 없었을 가능성이 높다.
5. **`dart_call_log` 의 요청 URL** — 키 파라미터 노출 위험 때문에 원문을 조회·출력하지 않았다. 스키마상 URL 컬럼 자체가 없다(`endpoint, corp_code, bsns_year, reprt_code, fs_div, status, n_rows, ts, key_id`).
6. **09-03~09-07 접수분의 후속 축 완결성** — DART 일일 증분은 09-09(KST 16:00)에 처음 돌았고 그때 D=20260908 만 처리했다. 그 이전 D 는 8월 말 전량 백필이 커버하지만, 09-03~09-07 접수분의 corp 축 후속은 "그 corp 가 09-08·09-09 에도 공시했는가" 에 우연히 의존한다. 일자별 대조(표 3a 근거)에서 큰 구멍은 안 보였으나, 전량 검증은 이번 범위 밖이다.

---

## 4. 사용한 질의 (핵심 SQL, 재현용)

```sql
-- [1] D일 구성
SELECT COUNT(*) n_rows, COUNT(DISTINCT rcept_no) u,
       SUM(CASE WHEN stock_code IS NULL OR TRIM(stock_code)='' THEN 1 ELSE 0 END) no_stock,
       SUM(CASE WHEN corp_code IS NULL OR TRIM(corp_code)='' THEN 1 ELSE 0 END) no_corp
FROM dart_disclosure WHERE rcept_dt='20260909';
SELECT substr(collected_at,1,13) hh, COUNT(*) FROM dart_disclosure
WHERE rcept_dt='20260909' GROUP BY 1;                    -- 스윕 뒤 추가 도착 판정

-- [1b] 중복 rcept_no 의 원인 (같은 page, rm 만 다름)
SELECT rcept_no, COUNT(*) n, GROUP_CONCAT(DISTINCT req_page_no) pages
FROM dart_disclosure WHERE rcept_dt='20260909' GROUP BY 1 HAVING n>1;

-- [3] 후속 축 커버리지 (축의 rcept_dt 포맷이 달라 rcept_no 로만 조인해야 한다)
WITH pub AS (SELECT DISTINCT rcept_no FROM dart_disclosure
             WHERE rcept_dt='20260909' AND stock_code<>''
               AND report_nm LIKE '%주식등의대량보유상황보고서%')
SELECT (SELECT COUNT(*) FROM pub) n_pub,
       (SELECT COUNT(*) FROM pub WHERE rcept_no IN (SELECT rcept_no FROM dart_majorstock)) n_hit;

-- [5] ingest_log 오늘 (status 는 'ok'/'no_data'/'mismatch'; '013' 은 note 에 들어간다)
SELECT name, status, COUNT(*) FROM ingest_log WHERE ts>='2026-09-09T21:00' GROUP BY 1,2;

-- [7] 콜 예산
SELECT key_id, COUNT(*) FROM dart_call_log WHERE ts>'2026-09-09T21:00' GROUP BY 1;
SELECT status, COUNT(*) FROM dart_call_log WHERE ts>'2026-09-09T21:00' GROUP BY 1;

-- [8] 전 분기 창 대조 — Q3 만 diff>0 임을 보인다
WITH w AS (SELECT bsns_year bgn, reprt_code end, status,
                  CAST(json_extract(note,'$.total_count') AS INTEGER) tc
           FROM ingest_log WHERE name='disclosure')
SELECT w.bgn, w.end, w.status, w.tc,
       (SELECT COUNT(DISTINCT rcept_no) FROM dart_disclosure d
         WHERE d.req_bgn_de=w.bgn AND d.req_end_de=w.end) led
FROM w ORDER BY w.bgn DESC;

-- [8] 삭제 시그니처 — 09-09T07 스윕이 페이지 001~400 전부에 새 행을 만들었는가
SELECT COUNT(*) n, COUNT(DISTINCT req_page_no) npages FROM dart_disclosure
WHERE req_bgn_de='20260701' AND req_end_de='20260930'
  AND collected_at BETWEEN '2026-09-09T07' AND '2026-09-09T08'
  AND CAST(req_page_no AS INTEGER)<=400;                 -- → 798행 / 400페이지
-- 그 행들이 "한 칸 앞 페이지로 이동" 인지 확인
SELECT rcept_no, GROUP_CONCAT(req_page_no) pages FROM dart_disclosure
WHERE rcept_dt='20260701' GROUP BY 1 HAVING COUNT(DISTINCT req_page_no)>1 LIMIT 10;

-- [9~11] WISE (fetched_date 는 KST 'YYYY-MM-DD', checked_at 은 UTC)
SELECT ep, COUNT(*), COUNT(DISTINCT cmp_cd), MIN(CAST(bytes AS INTEGER))
FROM ws_raw WHERE fetched_date='2026-09-10' GROUP BY 1;
WITH a AS (SELECT cmp_cd, COUNT(*) k FROM ws_raw WHERE fetched_date='2026-09-09' GROUP BY 1),
     b AS (SELECT cmp_cd, COUNT(*) k FROM ws_raw WHERE fetched_date='2026-09-10' GROUP BY 1)
SELECT COALESCE(a.cmp_cd,b.cmp_cd), a.k, b.k FROM a LEFT JOIN b USING(cmp_cd)
WHERE COALESCE(a.k,0)<>COALESCE(b.k,0);                  -- 커버 상실/신규
SELECT a.ep, COUNT(*), SUM(a.sha256=b.sha256) FROM ws_raw a JOIN ws_raw b
  ON a.cmp_cd=b.cmp_cd AND a.ep=b.ep AND a.pkey=b.pkey
WHERE a.fetched_date='2026-09-10' AND b.fetched_date='2026-09-09' GROUP BY 1;
```

false-none 판정에 쓴 원문 검증(읽기 전용, 서버 python3):

```python
import sqlite3, zlib, json
con = sqlite3.connect("file:data/raw/wisereport.db?mode=ro", uri=True)
for cd, fd, pk, body in con.execute(
        "SELECT cmp_cd, fetched_date, pkey, body FROM ws_raw WHERE ep='cF5001' "
        "AND cmp_cd IN ('036010','190510','342870','0010V0') "
        "AND fetched_date IN ('2026-09-09','2026-09-10')"):
    j = json.loads(zlib.decompress(body))
    c1, c2 = json.loads(j["chart1"]), json.loads(j["chart2"])
    print(cd, fd, pk,
          "eps", sum(v is not None for v in c1.get("select_item", [])),
          "sales", sum(v is not None for v in c2.get("select_item", [])),
          "tp", sum(v is not None for v in c1.get("target_price", [])))
```
