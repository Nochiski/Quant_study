# findings B — DART(공시·재무·문서 ZIP) · WISE(컨센서스) 원장 수집기 조사

조사일 2026-09-09 (KST). 저장소 워크트리 `/Users/claudeoscarmonet/orca/workspaces/Quant_study/데이터베이스`, 서버 `kael-server:~/quant-ledger`.
읽기 전용으로만 조사했다. **API 키 값은 이 문서 어디에도 없다 (key_id 만).**

> 표기 — **실측**: 서버 쿼리/로그/파일시스템에서 직접 잰 값. **추정**: 코드·문서에서 유추한 값.

**서버 = 저장소 (실측)**: DART·WISE 관련 10개 파일의 md5 가 서버와 저장소에서 전건 일치.
`api.py`·`backfill_dart.py`·`backfill_docs.py`·`backfill_wise.py`·`sweep_disclosure.py`·`dart_universe.py`·`dart_refetch.py`·`master_daily.py`·`daily_dart.sh`·`daily_wise.sh` — 코드 드리프트 없음.
서버에만 있고 저장소에 없는 파일: `src/sync_v3_wise.py`(v3 미러), `src/export_csv.py`, `src/rebuild_share.py`, `src/probe_krx_timing.py`, `src/equity_s23/`.

---

## 1. DART 수집 단위와 증분 경로

### 1-1. 수집 축(axis) 3종 — 이게 증분 설계의 뼈대다

`database/src/backfill_dart.py:141-205` (SPEC). 축이 곧 "무엇을 키로 1콜을 쏘는가" 이고, 축마다 재수집 규칙이 다르다.

| axis | 요청 파라미터 | 1콜 커버 | 소속 엔드포인트 |
|---|---|---|---|
| `corp` | `corp_code` | 그 회사 전체 (연도축 없음) | `company`, `elestock`, `majorstock` |
| `corp_year` | `corp_code`+`bsns_year`+`reprt_code`(+`fs_div`) | 그 회사·그 기수 1건 | `fin`, `dividend`, `shares`, `capital`, `tesstk`, `hyslr`, `audit` |
| `corp_range` | `corp_code`+`bgn_de=19990101`+`end_de=20991231` (상수) | 그 회사 **전 이력** 1콜 | DS005 15종 (stage 4·5) |
| `window` | `bgn_de`~`end_de`+`page_no` (분기 창) | 그 분기 100건/페이지 | `list.json` (`sweep_disclosure.py`) |

### 1-2. stage 1~5 정의 (`backfill_dart.py:128-139`)

| stage | 엔드포인트 | axis | daily_dart.sh 에 포함? |
|---|---|---|---|
| 1 | `company` (결산월 acc_mt) | corp | ✖ |
| 2 | `fin` (`fnlttSinglAcntAll`) | corp_year | ○ (`--reprt 11012,11013,11014 --years 2016..2025`) |
| 3 | `dividend`·`shares`·`capital`·`tesstk`·`hyslr`·`audit`·`elestock`·`majorstock` | corp_year ×6, corp ×2 | ○ (`--reprt` 기본 = **11011 만**) |
| 4 | `tsstkAqDecsn`·`piicDecsn`·`cvbdIsDecsn`·`ctrcvsBgrq`·`dfOcr`·`dsRsOcr`·`bnkMngtPcbg` | corp_range | ○ |
| 5 | `fricDecsn`·`pifricDecsn`·`crDecsn`·`cmpMgDecsn`·`cmpDvDecsn`·`cmpDvmgDecsn`·`stkExtrDecsn`·`tsstkDpDecsn` | corp_range | **✖ (누락)** |

### 1-3. 멱등·재시작 — ingest_log 와 store()

- **원장 적재 PK = `row_hash`** (`store()`, `backfill_dart.py:424-491`). 응답 필드 + `req_*` 요청 파라미터 + `dup_seq`(응답 내 동일내용 출현순번)의 SHA256(32자). `INSERT OR IGNORE` 라 `collected_at` 은 **최초 관측 시각**으로 고정된다. 재수집해도 같은 값이면 0행 증가 → **완전 멱등**.
- **정정공시**: `rcept_no` 가 자연키·해시에 들어가므로 원본·정정본이 **둘 다 별도 행으로 남는다**. 덮어쓰기 아님. 해석(어느 판본이 유효한지)은 stage 층 몫.
- **유닛 종결 대장 = `ingest_log`**, PK `(name, corp_code, bsns_year, reprt_code, fs_div)` (`backfill_dart.py:566-572`). corp/corp_range 축은 `bsns_year=''`·`reprt_code=''` 로 저장.
- **`--quota-window`**: `rolling`(기본, 24h 롤백) / `midnight`(KST 자정 리셋). `daily_dart.sh` 는 전 단계 `midnight`. `budget_used()` 는 `dart_call_log` 를 그 창으로 COUNT 하는 파생값이라 **프로세스 재시작에 안전**하고, `pick_key()` 가 창 전환 시 `blocked` 집합을 비운다(`backfill_dart.py:271-290`).
- **재시작**: `daily_dart.sh` 는 매일 00:01 KST 에 전일 프로세스를 `kill` 하고 새로 띄운다. 근거가 스크립트 주석에 있다 — `budget_used` 는 자동 리셋되지만 프로세스 메모리의 `blocked` 는 재기동으로만 비워진다.

### 1-4. 재수집(신선도) 규칙 — **여기가 일일 증분의 핵심 결함이다**

`backfill_dart.py:674-697` 의 종결 판정:

```python
if stt == "ok":
    done.add(key); continue                     # ← 축 무관. 영구 종결
if stt != "no_data": continue                   # 그 외는 미종결 → 다시 쏜다
if not by:                                      # corp·corp_range 축
    if (ts or "") > cut_ts: done.add(key)       # RECHECK_DAYS=30 이내면 종결
    else: pending += 1
...
if today >= fiscal_end(am, by, rc) + grace: done.add(key)   # corp_year: 영구
else: pending += 1                                          # 잠정 → 다시 쏜다
```

| 축 | `ok` 유닛 | `no_data` 유닛 |
|---|---|---|
| corp (`elestock`·`majorstock`·`company`) | **영구 종결 — 다시 안 쏜다** | 30일 지나면 1회 재확인 |
| corp_range (DS005 15종) | **영구 종결 — 다시 안 쏜다** | 30일 지나면 1회 재확인 |
| corp_year (`fin` 등 7종) | **영구 종결 — 다시 안 쏜다** | `fiscal_end+GRACE`(135/180일) 전이면 매 실행 재시도 |

**결론 (조사 항목 1의 답)**
- `sweep_disclosure.py` 는 **`dart_disclosure` 테이블만 채운다**. stage 2~4 로 넘어가는 자동 연결이 **코드에 없다**. stage 2~4 는 `corps.txt × years × reprt` 를 완전 독립적으로 순회하고 `ingest_log` 로만 스킵을 판단한다. → **별도 트리거가 필요하다.**
- 새 정기보고서가 나면: corp_year 유닛이 `no_data`(잠정)로 남아 있는 경우에만 다음 실행이 자동으로 다시 쏜다. 이미 `ok` 면 정정본이 나와도 **영원히 다시 안 쏜다** (DART 재무 API 는 최신 판본만 주므로 **정정 반영이 원장에 안 들어온다**).
- 새 주요사항보고서(DS005)가 나면: 그 corp 이 이미 `ok` 면 **영원히 못 잡는다**. DS005 는 1콜에 전 이력이 오므로 "그 corp 을 다시 부르면" 되는데, 부르지 않는다.
- 이 사실은 이미 문서화돼 있다 — `database/docs/COLLECT_PLAN.md:221` (§4-1 단계 10-1):
  > "corp축 ok 는 백필에서 영구 종결이므로(재수집 없음 — backfill_dart.py:637), 신선도는 일일 증분이 담당한다. **당일 `list.json` 공시에 해당 유형이 있는 corp 만 그날 재호출** — 이벤트 없으면 0콜"
  즉 "스윕 → 상세" 연결은 **설계돼 있으나 미구현**이다.

### 1-5. `sweep_disclosure.py` 의 열린 창 처리 (실측 결함 1건)

- 창 = 분기(최장 92일). 2010Q1~2026Q3 = 67창. 재개 단위 = (창, 페이지), 상태는 별도 테이블이 아니라 원장(`req_bgn_de`/`req_end_de`/`req_page_no`)에서 직접 읽는다.
- `end >= today` 인 **진행 중 창은 매 실행 page 1 부터 전량 재수집**(`sweep_disclosure.py:206-215`) — 정렬이 `rcept_dt` 단조가 아니라서 꼬리만 받을 수 없다는 실측 근거가 주석에 있다.
- 종료 시 `total_count == Σ수신 == 적재(DISTINCT rcept_no)` 3자 대조. 어긋나면 `mismatch` 로 기록하고 **완료로 적지 않는다.**

> ### DEFECT-B01: 열린 분기 창이 구조적으로 영구 `mismatch` 가 된다
> - **상황**: `dart_disclosure` 에 같은 창을 2회 이상 스윕한 이력이 있는 상태. 서버 실측 — 2026Q3 창을 08-30·08-31·09-01(UTC) 3회 스윕.
> - **인풋**: `python src/sweep_disclosure.py --quota-window midnight` (daily_dart.sh 4단계).
> - **에러 위치**: `database/src/sweep_disclosure.py:270-284` — `n_db = stored_rows(..., distinct=True)` 는 **그 창의 전 스윕 이력 합집합**의 DISTINCT `rcept_no` 를 세는데, `tc`(total_count)는 **이번 런 마지막 페이지 응답값**이다. 두 수는 원리상 같을 수 없다(과거 스윕이 잡았지만 지금 DART 목록에서 빠진 접수가 1건이라도 있으면 `n_db > tc`).
> - **실측**: `ingest_log` 의 `20260701-20260930` = `mismatch`, `{"total_count": 40953, "total_page": 410}`, 수신 40,953 = tc 는 맞는데 적재(distinct) **40,956** (+3). 로그: `✖ 20260701-20260930 대조 실패 … 완료로 적지 않는다`.
> - **위험성**: 기능적 손실은 없다(열린 창은 어차피 page 1 재수집이 정답). 그러나 ① **"오늘 스윕이 정상 완료됐다"를 `ingest_log.status` 로 판정할 수 없다** — 정상 운영과 진짜 페이징 유실이 같은 `mismatch` 로 보인다. ② `sweep_window` 가 `"bad"` 를 반환해 요약에 "미완 1" 로 찍히므로 알림 설계 시 오탐이 상시 발생한다. 일일 증분의 완료 게이트를 여기에 걸면 **매일 실패로 보인다.**
> - **재현 test**: 없음.

---

## 2. 문서 ZIP (`data/raw/documents/`)

### 2-1. `backfill_docs.py` 의 대상 선정·증분 방식

- 대상은 **`dart_disclosure` 에서 report_nm LIKE 로 뽑는다** (`backfill_docs.py:46-52`, `build_plan()` 128-141):

| 우선순위 | 조건 | 목적 |
|---|---|---|
| P1 분할·병합 | `report_nm LIKE '%주식분할결정%' OR '%주식병합결정%'`, `stock_code<>''` | 조정계수 정답지 |
| P2 사업보고서 | `LIKE '%사업보고서%' AND NOT LIKE '%연장신고%'` | 연간 축 |
| P3 반기·분기 | `LIKE '%반기보고서%' OR '%분기보고서%'`, 연장신고 제외 | 분기 해상도 |

- **증분 방식 = `doc_store.zip_ok=1` 차집합**. `build_plan()` 이 매 실행 전량 재계산하고 이미 받은 것을 뺀다. 별도 워터마크 없음 → **`dart_disclosure` 가 갱신되면 그 다음 실행이 자동으로 신규분만 받는다.** (즉 문서층은 스윕 → 문서 연결이 이미 성립한다. 끊긴 곳은 스윕 → stage 2~4 뿐이다.)
- 키 순서 **k3 → k2** (backfill_dart 의 k2→k3 과 반대로 두어 동시 실행 경합 회피, `backfill_docs.py:155-159`). `kael` 은 아예 안 쓴다.
- 전 키 소진 시 **KST 자정 00:02 까지 sleep 후 계속** (`sleep_to_kst_midnight()`, 81-86). 크론 없이 프로세스 하나로 며칠짜리 계획을 완주하도록 설계됨.
- 실패도 `doc_store` 에 `zip_ok=0` 으로 남긴다 → 다음 실행에서 재시도 대상.

### 2-2. 서버 실측 (2026-09-09)

| 항목 | 실측값 |
|---|---|
| `data/raw/documents/` 총량 | **27 GB** (`du -sh`), `SUM(doc_store.bytes)` = **25.94 GiB** |
| ZIP 파일 수 | **171,179** (`find … -name '*.zip' \| wc -l`) |
| `doc_store` 행수 | 174,309 (`zip_ok=1` 171,179 · `zip_ok=0` 3,130) |
| `http_status` 분포 | `000` 171,179 · **`014`(DART 파일없음) 3,130** — 전부 정정공시, 2015~2019 집중 |
| 연도 샤드 | 2010~2026, 17개. 2026 = 9,952 파일 · **2.4 GB** |
| 최신 접수번호 | **`20260831001536`** (파일 mtime 2026-09-01 16:53 UTC) |
| 수집 구간 | `fetched_at` 2026-09-01T04:55:33 ~ **2026-09-02T22:34:03** (UTC) = 09-01 13:55 ~ **09-03 07:34 KST** |
| 현재 미수집 대상 | **2건** (`dart_disclosure` 기준 차집합 — 원장 자체가 09-01 에서 멈춰 있어서 작다) |

### 2-3. 문서층 P1 후속의 "일일 증분 크론" 요구사항

- `database/docs/DOC_DESIGN.md:17`:
  > "**일일 증분** — stage 와 동일하게 1회 풀 빌드 후 별도 설계. 접수번호 키·ZIP 불변이라 **`doc_store` 신규 행만 파싱하면 되는 구조**."
- `database/docs/DOC_LAYER_KICKOFF.md` 는 P1 완료(PR #54, `stg_doc_meta`·`stg_doc_section`·`stg_doc_correction`·`stg_doc_parse_log`)를 기록하고, 마지막 줄에 "크론은 daily_wise 06:00 KST 하나뿐" 을 명시.
- `DOC_DESIGN.md:420` 의 P1 미결 5건 중 일일 증분 관련: 규칙 5c 정규식 성능(117ms/멤버) · `stg_doc_parse_log.t_*_ms` 가 payload 라 `content_hash` 가 실행마다 달라짐(→ **재현성 게이트가 일일 증분에서 매번 깨진다**, `payload_exclude` 로 빼야 함) · lenient 65건 D10 등급 · **카엘 AI 머신 분산은 P2 프리패스부터**.
- 즉 요구사항은 두 층이다 — ① ZIP **수집** 증분(= `backfill_docs.py` 를 매일 짧게 도는 것, 이미 코드가 지원) ② 문서 **파싱** 증분(`doc_store` 신규 행만 → stage 재빌드, 설계 미완).

---

## 3. 예산 · 키

### 3-1. 등록 키 (값 비공개, key_id 만)

| key_id | .env 변수 | 우선순위 | 사전 상한(CAPS) | 비고 |
|---|---|---|---|---|
| `k2` | `DART_API_KEY_2` | 1 | **없음**(`CAP_OURS=None`) | 020 이 올 때까지 쓴다 |
| `k3` | `DART_API_KEY_3` | 2 | 없음 | |
| `kael` | `DART_API_KEY` | **최후** | **39,000** | **kael-system-v3 프로덕션 키** |

`api.py:136-150` — `_2`~`_5` 를 번호순으로 붙이고 `DART_API_KEY` 를 **항상 마지막**에 둔다. 서버 `.env` 실측 = 3개(`DART_API_KEY`, `_2`, `_3`). `_4`·`_5` 자리는 비어 있다 → **키 추가는 .env 에 넣기만 하면 자동 인식**.

한도 상수 (`backfill_dart.py:56-63`): `QUOTA_LIMIT = 40_000` (2026-08-26 실측 — k2 가 40,000번째까지 정상, 40,001번째에 020). 리셋 = **KST 자정** 실측 확정. 우리 키 총 용량 = **80,000콜/일**.

### 3-2. 최근 사용량 — `dart_call_log` 키별·일별 (KST, **실측**)

> `dart_call_log` 의 MIN(ts) = 2026-08-25T10:07:51 · MAX(ts) = **2026-09-02T22:34:03** · 총 623,538행. 08-25 이전 기록은 없다.

| KST 날짜 | k2 | k3 | kael | 합계 |
|---|---:|---:|---:|---:|
| 08-25 | 7,001 | — | — | 7,001 |
| 08-26 | 25,000 | — | 19,500 | 44,500 |
| 08-27 | 40,003 | 40,003 | 25,902 | 105,908 |
| 08-28 | 40,004 | 40,004 | 39,002 | 119,010 |
| 08-29 | 66,405 | — | — | 66,405 |
| 08-30 | 28,921 | — | — | 28,921 |
| 08-31 | 16,664 | — | — | 16,664 |
| 09-01 | 40,001 | 39,990 | — | 79,991 |
| 09-02 | 40,001 | 40,000 | — | 80,001 |
| 09-03 | 35,137 | 40,000 | — | 75,137 |
| **09-04 ~ 09-09** | **0** | **0** | **0** | **0 — 6일 연속 무호출** |

키별 누계: k2 339,137 · k3 199,997 · kael 84,404. (08-29 의 k2 66,405 는 `midnight` 창 판정이 UTC/KST 경계에서 두 자연일에 걸친 결과로 보인다 — **추정**.)

### 3-3. 최근 실행의 콜 구성 (엔드포인트별, **실측**)

| KST 날짜 | 구성 |
|---|---|
| 09-01 | `document.xml` 35,669 · stage5 DS005 8종 × 3,478 = 27,824 · stage3 6종 × 2,682 = 16,092 · `list.json` 405 |
| 09-02 | `document.xml` 63,505 · stage3 6종 × 2,681 = 16,086 · `list.json` 410 |
| 09-03 | `document.xml` 75,137 (backfill_docs 완주) |

전체 누계: `list.json` **35,657콜**(2010Q1~2026Q3 전기간 스윕) · `document.xml` **174,311콜**.

### 3-4. 현행 daily_dart.sh 의 고정비 — **16,496콜/일 (실측 09-02)**

| 단계 | 실측 콜 | 원인 |
|---|---:|---|
| stage 2 (`fin`, 2016~2025 × 11012/13/14) | **0** | 전 유닛 종결 |
| stage 3 (6 corp_year 엔드포인트) | **16,086** | `bsns_year=2026`·`reprt_code=11011` 의 `no_data` **2,681유닛 × 6** 을 매일 재확인. `fiscal_end(12월,2026,11011)+180일 = 2027-06-29` 까지 계속 |
| stage 3 (`elestock`·`majorstock`) | 0 | 전건 `ok`(2,306/2,514) 영구종결 + `no_data` 30일 이내 |
| stage 4 (DS005 7종) | 0 | 전건 `ok` 종결 + `no_data` 30일 이내 |
| 공시 스윕 | **410** | 열린 창(2026Q3) 410페이지 전량 재수집 |

즉 **daily_dart.sh 를 그대로 재등록하면 하루 16,500콜을 쓰면서 실제 신규 데이터는 `dart_disclosure` 뿐이다** (stage 3 의 16,086콜은 2027년 6월까지 전건 013 이 확정적).

### 3-5. 일일 증분에 필요한 콜 수 — **추정** (실측 기반 산식)

| 구성 | 평시(콜/일) | 반기·사업보고서 마감일 | 근거 |
|---|---:|---:|---|
| ① `list.json` 열린 분기 전량 재스윕 | **410** (분기 초) ~ **890** (분기 말) | 동일 | 실측 09-02 410콜 / 2026Q2 창 886페이지 |
| ①' `list.json` 당일 창만 (`bgn=end=D`) | **5~23** | ~50 | 실측 일 500~2,200건 ÷ 100. **단 창 id 가 표준 분기창과 달라 원장 중복 적재**(`sweep_disclosure.py:390-393` 경고) |
| ② 신규 정기보고서 상세 (fin 1~2콜 + stage3 6콜 ≈ 8콜/건) | **16 ~ 240** (일 2~30건) | **~18,200** (08-14 실측 2,273건) | 실측 일별 정기보고서 건수 |
| ③ 주요사항보고서 corp 재호출 (DS005 15종 전부) | **300 ~ 510** (일 20~34 corp) | 동일 | 실측 일 26~39건 / 16~34 corp |
| ③' 위, report_nm→엔드포인트 매핑 시 | **20 ~ 40** | 동일 | 추정 |
| ④ 지분공시 corp 재호출 (`elestock`+`majorstock`) | **100 ~ 300** | 동일 | 추정 (일별 대량보유/임원소유 공시 corp 수 미측정) |
| ⑤ 문서 ZIP (정기보고서 + 분할·병합) | **3 ~ 37** | **~2,280** | 실측 분할병합 1~7건/일 |
| **합계 (①' + ②+③'+④+⑤)** | **≈ 150 ~ 640** | **≈ 20,600** | |
| **합계 (① 전량스윕 유지 시)** | **≈ 550 ~ 1,500** | **≈ 21,000** | |

우리 키 용량 80,000/일 대비 **평시 1% 미만, 최악(3월 사업보고서 마감일)에도 여유** — 다만 3월 마감일 실측치는 없다(2026-03 은 백필 전이라 `dart_call_log` 에 없음).

### 3-6. kael-system-v3 와의 키 충돌

| 항목 | 실측 |
|---|---|
| v3 크론 | `45 22 * * *` (07:45 KST) · `15 9 * * *` (18:15 KST) — `backend.dart.filings --mode incremental`, `flock` 으로 자기 중복만 방지 |
| v3 가 쓰는 키 | `DART_API_KEY` = 우리의 `kael` (순서상 최후) |
| v3 일 사용량 | **약 60~90콜/일** (실측, `~/logs/kael-v3/dart.log` 요약행: `{"window":"20260907~20260908","pages":10,"filings":504,"docs":59}` 형태. pages=list.json 콜, docs=document.xml 콜. 2회 실행 합계 최대 83) |
| 설계 가정 | 300콜/일 (`CAPS["kael"]=39_000` = 40,000 − 카엘몫 − 마진) → **실측이 가정의 1/4, 여유 충분** |

**충돌 위험 평가**
- 우리 키 k2·k3 로 80,000콜을 다 태우기 전에는 `kael` 이 아예 나가지 않는다(`pick_key()` 순차 폴백). 일일 증분은 평시 640콜이므로 **kael 키에 도달할 일이 사실상 없다.**
- 진짜 위험은 **역방향**: 우리가 실수로 `kael` 을 쓰면 v3 의 22:45/09:15 UTC 실행이 020 을 맞는다. `backfill_dart.py` 에는 1순위가 kael 이면 즉시 중단하는 가드가 있고(`main()`), `backfill_docs.py` 는 kael 을 아예 제외한다.
- **부수 발견(보안)**: `~/logs/kael-v3/dart.log` 는 `crtfc_key` 를 **URL 평문으로 로깅**한다(httpx INFO). 이 파일은 로테이션이 없고 현재 584KB·3,223 호출. `api.py:160-164` 의 주석이 지목한 바로 그 사고다. 우리 조사·플랜 산출물에 이 로그를 인용할 때 URL 전문을 붙이면 키가 유출된다.

---

## 4. 갭 실측 — 08-20 이후 무엇이 안 들어왔나

### 4-1. `dart_disclosure` 일별 접수 건수 (실측)

| rcept_dt | 행 수 | DISTINCT rcept_no | (상장사만) |
|---|---:|---:|---:|
| 20260810 | 847 | 842 | |
| 20260811 | 909 | 907 | |
| 20260812 | 1,156 | 1,153 | |
| 20260813 | 2,084 | 2,069 | |
| **20260814 (반기 마감)** | **4,706** | **4,606** | |
| 20260818 | 438 | 410 | 344 |
| 20260819 | 483 | 469 | 294 |
| 20260820 | 573 | 553 | 362 |
| 20260821 | 877 | 841 | 542 |
| 20260824 | 486 | 452 | 376 |
| 20260825 | 649 | 618 | 380 |
| 20260826 | 1,062 | 1,013 | 449 |
| 20260827 | 1,342 | 1,281 | 450 |
| 20260828 | 2,253 | 2,148 | 775 |
| 20260831 | 1,780 | 1,730 | 658 |
| **20260901** | **531** | **530** | **436** |
| **20260902 ~ 20260909** | **0** | **0** | **0** |

원장 전체: 3,444,518행 / 3,443,898 distinct, `rcept_dt` 20100104 ~ **20260901**.

**미수집 = 09-02(수)·09-03(목)·09-04(금)·09-07(월)·09-08(화) 5영업일 + 09-09(오늘 진행중).**
v3 로그의 같은 기간 실측 filings: 09-02 461 · 09-03 633 · 09-06→09-07 454 · 09-07→09-08 504. 우리 원장 기준으로는 **일 500~900건 × 5일 ≈ 3,000~4,000건 미수집**(추정).

### 4-2. stage 2~4 대상 테이블 최신도 (실측)

| 테이블 | 행수 | MAX(collected_at) | 최신 `req_bsns_year` × `reprt_code` (행수 / MAX 접수일) |
|---|---:|---|---|
| `dart_company` | 3,478 | 2026-08-26T02:30 | (corp 축) |
| `dart_fin_raw` | 15,375,024 | **2026-08-30T13:42** | 2026×11012 416,527 / **20260828** · 2026×11013 391,992 / 20260825 · 2025×11011 487,635 / 20260825 |
| `dart_dividend` | 384,232 | 2026-08-31T17:07 | **11011 만** · 2026 525행 / 20260831 |
| `dart_shares` | 97,194 | 2026-08-31T17:08 | **11011 만** · 2026 131행 / 20260831 |
| `dart_capital` | 283,479 | 2026-08-31T17:08 | **11011 만** · 2026 234행 / 20260831 |
| `dart_tesstk` | 328,704 | 2026-08-31T17:08 | **11011 만** · 2026 154행 / 20260831 |
| `dart_hyslr` | 228,226 | 2026-08-31T17:08 | **11011 만** · 2026 181행 / 20260831 |
| `dart_audit` | 93,037 | 2026-08-31T17:08 | **11011 만** · 2026 210행 / 20260831 |
| `dart_elestock` | 32,516 | 2026-08-28T22:07 | (corp 축) |
| `dart_majorstock` | 22,110 | 2026-08-28T22:07 | (corp 축) |
| DS005 stage4 7종 | — | 2026-08-29T08:53 | (corp_range 축) |
| DS005 stage5 8종 | — | 2026-09-01T04:55 | (corp_range 축) |

> ### DEFECT-B02: 정기보고서 부속 6종이 **사업보고서(11011)만** 수집돼 있다
> - **상황**: 백필·daily_dart.sh 모두 `--reprt` 기본값 `11011` 로 stage 3 을 돌렸다.
> - **인풋**: `src/backfill_dart.py --corps data/corps.txt --stage 3 --quota-window midnight` (`scripts/daily_dart.sh:35-36`, `--reprt` 미지정 → `backfill_dart.py:548` 기본값 `11011`).
> - **에러 위치**: `scripts/daily_dart.sh:35-36` — stage 2 는 `--reprt 11012,11013,11014` 를 명시하는데 stage 3 은 명시하지 않는다.
> - **실측**: `SELECT group_concat(DISTINCT req_reprt_code)` 이 6개 테이블 전부 **`11011` 단일값**.
> - **위험성**: `alotMatter`(배당)·`stockTotqySttus`(주식총수)·`irdsSttus`(증자·감자)·`tesstkAcqsDspsSttus`(자기주식)·`hyslrSttus`(최대주주)·`accnutAdtorNmNdAdtOpinion`(감사의견)의 **반기·분기 판본이 통째로 없다**. 주식총수·자기주식은 분기 해상도가 있어야 시총·유통주식수 팩터의 시점 오차가 줄고, 최대주주·감사의견은 반기 변동이 실재한다. 일일 증분이 11011 만 잡으면 이 공백이 영구화된다. 다만 **의도된 제한인지 미확인** — `DART_CENSUS.md`/`DART_DESIGN.md` 에 11011 한정 근거가 있는지 플랜 작성자가 확인할 것.

> ### DEFECT-B03: `daily_dart.sh` 의 stage 2 가 **올해(2026) 사업연도를 아예 안 쏜다**
> - **상황**: 매일 00:01 KST 크론.
> - **인풋**: `scripts/daily_dart.sh:32-34` — `Y2=$(python3 -c "print(','.join(str(y) for y in range(2016,2026)))")` → **2016~2025**. `--reprt 11012,11013,11014`.
> - **에러 위치**: `scripts/daily_dart.sh:32` — `range(2016,2026)` 의 끝값이 배타적이라 2026 이 빠진다. `backfill_dart.py:614-618` 의 기본값은 `range(2015, 올해+1)` 로 올해를 포함하도록 고쳐져 있는데(DEFECT-B02 경로 주석), 크론 스크립트가 그 수정을 반영하지 않았다.
> - **실측**: `dart_fin_raw` 의 `bsns_year=2026` 데이터(11012 416,527행 · 11013 391,992행)는 `collected_at` 이 전부 **2026-08-30T13:42** = `logs/dart_2026.log` 의 **1회성 수동 실행**(`--reprt 11013,11012`, 6,639콜) 산출물이다. 크론은 한 번도 2026 을 쏜 적이 없다.
> - **위험성**: ① 08-30 이후 접수된 2026 반기보고서(늦은 제출·정정)가 원장에 없다. ② 2026 3분기(11014, 11월 접수)·2027 1분기가 **아무도 안 쏘는 상태**가 된다. ③ 2026×11012 의 `no_data` 85유닛·11013 84유닛이 잠정 상태로 남아 있는데 재시도할 실행 경로가 없다.

> ### DEFECT-B04: `daily_dart.sh` 에 **stage 5(DS005 조정계수 8종)가 없다**
> - **상황**: stage 5 는 2026-09-01 04:55 에 `logs/dart_stage5.log` 수동 1회 실행으로만 채워졌다.
> - **인풋**: `scripts/daily_dart.sh:33-39` 는 stage 2·3·4·sweep 만 호출한다.
> - **에러 위치**: `scripts/daily_dart.sh:37-38`.
> - **위험성**: `fricDecsn`(무상증자)·`crDecsn`(감자)·`cmpMgDecsn`(합병)·`cmpDvDecsn`(분할)·`stkExtrDecsn`(주식교환)·`tsstkDpDecsn`(자기주식 처분) 은 **조정계수(adj_factor)의 원인·조건을 대는 층**이다(`backfill_dart.py:134-139` 주석, CA-01 대체). 여기가 정지하면 09-01 이후의 기업행위가 stage/equity 의 `adj_factor` 검산축에서 빠진다. corp_range `ok` 는 영구종결이므로 **크론에 넣어도 재수집되지 않는다** — 1-4 의 문제와 겹친다.

### 4-3. `daily_dart.sh` 가 마지막으로 돈 날짜 (실측)

`logs/daily_dart_*.log` = 0827, 0828, 0829, 0830, 0831, 0901, **0902** (총 7개). 그 뒤는 없다.
`daily_dart_0902.log` 내용: `════ [09-02 00:01:01 KST] 데일리 재기동 ════` … `──── 공시 스윕 종료 rc=0 02:46:42 KST ────`, `키별 소진 (KST 오늘) k2 16,496 / k3 26,852`.
→ **마지막 실행 = 2026-09-02 00:01~02:46 KST.** 그 이후 crontab 에서 제거됨(현재 crontab 62줄에 `daily_dart` 없음, `daily_wise` 만 61-62줄에 존재).

`dart.db` 마지막 쓰기는 09-02 22:34 UTC(= 09-03 07:34 KST) — `backfill_docs.py` 완주 시각이다.

### 4-4. 9월 반기 시즌 이후 결손 요약

| 대상 | 마지막 반영 | 결손 |
|---|---|---|
| `dart_disclosure` (공시목록) | 접수일 20260901 | **5영업일** (09-02~09-08) |
| 문서 ZIP | 접수번호 20260831001536 | 09-01~09-08 접수분 전부. 평시 정기보고서 2~30건/일 + 분할병합 1~7건/일 → **추정 30~200건** |
| `dart_fin_raw` 2026 반기 | 접수일 20260828 (수집 08-30) | 08-29 이후 접수된 반기·정정 |
| stage 3 6종 (11011) | 접수일 20260831 | 09-01 이후 접수분 + **반기·분기 판본 전부(DEFECT-B02)** |
| DS005 stage4 | 08-29 | 08-29 이후 신규 사건 (ok corp 은 영구 미수집, §1-4) |
| DS005 stage5 | 09-01 | 09-01 이후 신규 사건 (동일) |
| `elestock`/`majorstock` | 08-28 | 08-28 이후 신규 지분공시 (ok corp 은 영구 미수집) |

---

## 5. WISE (컨센서스)

### 5-1. `daily_wise.sh` — quant-ledger 의 유일한 크론 (실측)

crontab 61-62행:
```
# quant-ledger WISE 데일리 — KST 06:00 (= UTC 21:00 전일). 2026-09-02 18:00에서 변경
0 21 * * * /bin/bash ~/quant-ledger/scripts/daily_wise.sh
```
① `master_daily.py` (키움 ka10099 코스피/코스닥 2콜 → `kiwoom.db:ka10099_stock_master` 일별 스냅샷)
② `backfill_wise.py --mode full` — **항상 full** (무커버 재프로브가 +3,700요청·+1분뿐이라 신규 커버리지 개시를 다음 날에 잡는 쪽이 이득, 2026-09-01 결정)
③ v3 미러 `sync_v3_wise.py` 는 **2026-09-02 중단** — 직접 수집이 같은 값을 커버, 과거분(~09-02)은 동결 보관

### 5-2. `--mode full` 이 매일 하는 일 (`backfill_wise.py`)

유니버스 = 키움 마스터 최신 스냅샷의 `upSizeName<>''`(보통주). 종목당 요청 세트:

| 커버리지 | 요청 수 | 내용 |
|---|---:|---|
| 무커버 (`none`) | **2** | `c1050001_data`(기간목록) + `cF5001` 첫 연도 → 전값 None 이면 나머지 스킵 |
| 커버 (`covered`) | **15** | 위 2 + `cF5002`×3·`cF5001`×2 (총 6) + `c1050001_data` flag2 T2Y/T2Q (2) + flag4 T4:yymm ×3 (3) + `c1010001` 정적페이지 (1) + `cF3002`·`cF4002` (2) |

검산: 807×15 + 1,756×2 = **15,617** = 09-09 실측 요청 수. **정확히 일치.**
원장 `ws_raw` PK `(cmp_cd, ep, pkey, fetched_date)`, 본문은 zlib 압축 원문. 스레드 10, 지터 0.05~0.25s. `validate()` 가 HTTP 200 인데 내용이 깨진 "조용한 실패"를 `bad/*` 로 걸러 **저장하지 않는다**.

### 5-3. `ws_run_log` · `ws_coverage` · `ws_raw` 최근 실측

| run_at (UTC) | mode | n_stocks | n_req | n_ok | n_bad | bad_summary |
|---|---|---:|---:|---:|---:|---|
| 2026-09-08T21:04:47 | full | 2,563 | 15,617 | 15,617 | 0 | {} |
| 2026-09-07T21:04:37 | full | 2,564 | 15,580 | 15,580 | 0 | {} |
| 2026-09-06T21:04:38 | full | 2,564 | 15,580 | 15,580 | 0 | {} |
| 2026-09-05T21:04:33 | full | 2,564 | 15,580 | 15,580 | 0 | {} |
| 2026-09-04T21:04:36 | full | 2,564 | 15,580 | 15,580 | 0 | {} |
| 2026-09-03T21:04:34 | full | 2,565 | 15,556 | 15,556 | 0 | {} |
| 2026-09-02T21:04:33 | full | 2,566 | 15,545 | 15,544 | 1 | {"invalid_stock": 1} |
| 2026-09-02T03:27:42 | full | 2,566 | 15,584 | 15,583 | 1 | {"invalid_stock": 1} |
| 2026-09-01T04:40:48 | daily | 98 | 1,470 | 1,470 | 0 | {} |

- `ws_coverage`: **covered 808 · none 1,758** (checked_at 최신 2026-09-08T21:04:47). 커버율 31.5%.
- `ws_raw` 일자별: 09-09 15,617 / 2,563종목 · 09-08 15,580 / 2,564 · … · 09-01 15,662 / 2,566. **09-01 시작, 9일 연속 무결손.**
- `ws_call_log` 상태 분포: 09-03~09-09 전건 `ok`. `bad/invalid_stock` 은 09-02 에 2건뿐(거래정지 종목).
- 09-09 로그 실측: `06:00:01 → 06:04:47 KST`, **4.7분**, 54.8 req/s, 커버 807·무커버 1,756.

### 5-4. 실패 시 감지 방법 (현행)

| 층 | 신호 | 현황 |
|---|---|---|
| 본문 검증 | `validate()` → `ws_call_log.status='bad/*'`, `ws_run_log.n_bad`·`bad_summary` | 있음. `struct_changed`/`marker_missing` 이면 "구조 개편 의심" 경고 출력 |
| 실행 자체 | `ws_run_log` 에 그날 행이 생겼는가 | 있음 (단 **알림 없음** — 로그 파일만) |
| 키움 마스터 실패 | `master_daily.py` 가 `return_code≠0` 시 그 시장을 적재하지 않고 메시지 출력 | 있음. 그 뒤 `backfill_wise` 는 `MAX(snap_date)` 를 쓰므로 조용히 전일 스냅샷으로 돈다 |
| **알림** | **없다** — 크론 출력은 `logs/daily_wise_MMDD.log` 로만 간다 | **미비** |

### 5-5. v3 미러(`v3_*`)의 동결 상태 (실측)

`wisereport.db` 내 4개 테이블. `sync_v3_wise.py` 는 09-02 이후 실행 안 됨.

| 테이블 | 행수 | 데이터 축 범위 | `copied_at` 범위 |
|---|---:|---|---|
| `v3_consensus_revision_daily` | 63,175 | `base_date` 2026-04-03 ~ **2026-08-31** · `collected_date` 2026-04-10 ~ 2026-09-01 | 09-01T03:31 ~ 09-02T03:27 |
| `v3_analyst_opinions` | 254,925 | `snapshot_date` 2026-04-04 ~ **2026-09-01** | 09-01T03:31 ~ 09-02T03:27 |
| `v3_consensus_annual` | 18,086 | `sync_date` 09-01(17,956) · 09-02(130) | 동일 |
| `v3_consensus_revision_compare` | 978 | `sync_date` 2판본 | 동일 |

`STAGE_HANDOFF.md:86-89` 가 stage 사본을 **"동결 사본"** 으로 명시(`stg_v3_revision_daily` 63,175 / `stg_v3_analyst_opinions` 254,925 / `stg_v3_consensus_annual` 18,086 / `stg_v3_revision_compare` 978 — 행수 전건 일치).
`EQUITY_HANDOFF.md:555`: "이 넷의 유일한 재료(v3 일별)는 **08-31 에 동결**됐다".
→ **`base_date` 상한 2026-08-31 이 동결선.** 09-01 부터는 우리 직접 수집(`ws_raw`)이 유일 소스이고 두 축은 09-01 에서 **1일 겹친다**. 겹침 구간의 값 일치는 `EQUITY_DESIGN.md` P37 에서 5키 × 5축 전건 일치로 실측 확인됨.
서버에 09-08 자 백업 2개(`wisereport.db.bak_v3_catchup_20260908T044946Z`, `…bak_post_catchup_20260908T050951Z`)가 있다 — 09-08 에 v3 관련 캐치업 작업이 있었던 흔적. **그 작업 내용은 이번 조사 범위 밖(미확인).**

---

## 6. 유니버스

### 6-1. `data/corps.txt` — 백필 대상 corp_code

| 항목 | 실측 |
|---|---|
| 파일 | `~/quant-ledger/data/corps.txt`, 66,082 bytes, **mtime 2026-08-25 23:41** |
| 행수 | **3,478** (= `dart_corp_map` 3,478 = `dart_company` 3,478) |
| 포맷 | `corp_code\tfirst_year\tlast_year` (예: `00119195\t2015\t2026`) |
| `last_year=2026` (현역) | **2,713**. 나머지 765 는 폐지·상장구간 종료 |

**갱신 경로 (`src/dart_universe.py`)**
1. `api.dart("corpCode.xml")` 로 DART 전체 corpCode ZIP 을 매 실행 재다운로드(1콜) → `stock_code` 있는 상장사 맵 구축.
2. `krx.db` 의 `krx_stk_isu_base_info`·`krx_ksq_isu_base_info` 전기간 티커 × (MIN/MAX `bas_dd_req`) 와 **교집합**.
3. `dart_corp_map` 갱신(`INSERT OR REPLACE`) + `corps.txt` 재작성. 연도 게이팅 `FLOOR=2015`, `CEIL=datetime.now().year`, 상장 직전연도 1년 유예.

**갱신 주기 = 없다. 수동 실행뿐이고 08-25 이후 한 번도 안 돌았다 (실측).**

> ### DEFECT-B05: 신규 상장사가 DART 유니버스에 들어올 경로가 막혀 있다
> - **상황**: `dart_universe.py` 를 지금 다시 돌려도 결과가 08-25 와 거의 같다.
> - **인풋**: `.venv/bin/python src/dart_universe.py` (기본 `--krx data/raw/krx.db`).
> - **에러 위치**: `database/src/dart_universe.py:18-30` (`krx_tickers()`) — 유니버스가 `krx.db` 티커와의 **교집합**인데, 서버 `krx.db` 의 `MAX(bas_dd_req)` 가 `krx_stk_isu_base_info`·`krx_ksq_isu_base_info` **둘 다 20260820** 에 동결돼 있다(KRX 수집기 `--to` 하드코딩).
> - **위험성**: 08-21 이후 신규 상장한 종목은 DART corpCode 에 있어도 `corps.txt` 에 들어가지 못한다 → 그 회사의 재무·배당·주식총수·기업행위가 **전건 미수집**. 폐지 종목의 `last_year` 도 갱신되지 않아 헛콜이 계속 나간다. 참고로 WISE 수집기는 이 함정을 이미 피했다 — `backfill_wise.py:182-201` 이 유니버스를 **키움 일별 마스터**(매일 갱신, 09-09 스냅샷 4,308종목·보통주 2,563)에서 읽고 KRX 를 폴백으로만 쓴다. **같은 전환이 `dart_universe.py` 에도 필요하다.**

### 6-2. 키움 마스터(WISE 유니버스 소스)는 정상

`ka10099_stock_master` snap_date 20260901~**20260909** 전 영업일·주말 포함 매일 4,307~4,309행. `daily_wise.sh` 가 매일 06:00 KST 에 갱신 중.

---

## 7. "하루가 끝났다" 판정 쿼리

D = 판정 대상 KST 날짜(`YYYYMMDD`). 기대치는 전부 위 실측에 근거한다.

### 7-1. DART 공시목록 (`list.json` 스윕)

```sql
-- ① D일 접수분이 원장에 들어왔는가 (필수)
SELECT :D AS d,
       COUNT(DISTINCT rcept_no)                          AS n_filings,
       SUM(CASE WHEN stock_code <> '' THEN 1 ELSE 0 END) AS n_listed_rows
FROM   dart_disclosure
WHERE  rcept_dt = :D;
-- 기대: 영업일 n_filings >= 400 (실측 08-18~09-01 범위 410~2,148, 중앙값 ~840).
--       n_listed_rows >= 290 (실측 294~775).
--       휴장일은 0 이 정상 — trading_calendar 로 먼저 영업일 판정할 것.

-- ② 스윕이 실제로 그날 돌았는가 (ingest_log.status 는 쓰지 말 것 — DEFECT-B01)
SELECT COUNT(*) AS n_page_calls, MAX(ts) AS last_call
FROM   dart_call_log
WHERE  endpoint = 'list.json'
  AND  ts > strftime('%Y-%m-%dT%H:%M:%S','now','+9 hours','start of day','-9 hours');
-- 기대: 열린 분기 전량 재수집이면 400~890 (실측 09-01 405 · 09-02 410, 2026Q2 창 886페이지).
--       당일 창만 쏘는 설계로 바꾸면 5~23.

-- ③ 추세 이탈 게이트 (COLLECT_PLAN §4-3 게이트 C 준용)
SELECT (SELECT COUNT(DISTINCT rcept_no) FROM dart_disclosure WHERE rcept_dt = :D) * 1.0
     / NULLIF((SELECT AVG(c) FROM (SELECT COUNT(DISTINCT rcept_no) c FROM dart_disclosure
               WHERE rcept_dt < :D AND rcept_dt >= :D_minus_30 GROUP BY rcept_dt)), 0) AS ratio;
-- 기대: ratio >= 0.5 (반기 마감 등 계절성이 4배까지 튀므로 하한만 본다)
```

### 7-2. DART 상세(stage 2~4) — "신규 공시가 상세로 이어졌는가"

```sql
-- D일 접수된 정기보고서 중, 그 (corp, bsns_year, reprt_code) 유닛이 ok 인 건수
WITH new_periodic AS (
  SELECT DISTINCT corp_code, rcept_no, report_nm
  FROM   dart_disclosure
  WHERE  rcept_dt = :D AND stock_code <> ''
    AND  report_nm NOT LIKE '%연장신고%'
    AND (report_nm LIKE '%사업보고서%' OR report_nm LIKE '%반기보고서%'
         OR report_nm LIKE '%분기보고서%')
)
SELECT COUNT(*) AS n_new_periodic,
       SUM(CASE WHEN i.status = 'ok' THEN 1 ELSE 0 END) AS n_fin_ok
FROM   new_periodic n
LEFT JOIN ingest_log i
       ON i.name = 'fin' AND i.corp_code = n.corp_code
      AND i.bsns_year = :BSNS_YEAR AND i.reprt_code = :REPRT;
-- 기대: 평시 n_new_periodic 2~30 (실측 08-18~09-01), 반기 마감일 2,273 (실측 20260814).
--       n_fin_ok = n_new_periodic 이어야 "따라갔다".
-- ※ (bsns_year, reprt_code) 는 report_nm 의 "(2026.06)" 라벨에서 파싱해야 한다 — 그 매핑 코드가 현재 없다.

-- D일 주요사항보고서를 낸 corp 중 그날 DS005 를 다시 부른 corp 수
SELECT COUNT(DISTINCT d.corp_code) AS n_corp_with_major,
       COUNT(DISTINCT c.corp_code) AS n_corp_recalled
FROM   dart_disclosure d
LEFT JOIN dart_call_log c
       ON c.corp_code = d.corp_code
      AND c.endpoint IN ('tsstkAqDecsn.json','piicDecsn.json','cvbdIsDecsn.json',
                         'ctrcvsBgrq.json','dfOcr.json','dsRsOcr.json','bnkMngtPcbg.json',
                         'fricDecsn.json','pifricDecsn.json','crDecsn.json','cmpMgDecsn.json',
                         'cmpDvDecsn.json','cmpDvmgDecsn.json','stkExtrDecsn.json','tsstkDpDecsn.json')
      AND date(c.ts,'+9 hours') = date(:D_ISO)
WHERE  d.rcept_dt = :D AND d.stock_code <> '' AND d.report_nm LIKE '%주요사항보고서%';
-- 기대: n_corp_with_major 16~34/일 (실측 08-18~09-01), n_corp_recalled 가 같아야 한다.
--       현재 코드로는 n_corp_recalled = 0 이 나온다 (§1-4 미구현).
```

### 7-3. 문서 ZIP

```sql
WITH tgt AS (
  SELECT DISTINCT rcept_no FROM dart_disclosure
  WHERE  rcept_dt = :D AND stock_code <> ''
    AND (report_nm LIKE '%주식분할결정%' OR report_nm LIKE '%주식병합결정%'
         OR (report_nm NOT LIKE '%연장신고%' AND
             (report_nm LIKE '%사업보고서%' OR report_nm LIKE '%반기보고서%'
              OR report_nm LIKE '%분기보고서%'))))
SELECT COUNT(*)                                            AS n_target,
       SUM(CASE WHEN s.zip_ok = 1 THEN 1 ELSE 0 END)       AS n_ok,
       SUM(CASE WHEN s.rcept_no IS NULL THEN 1 ELSE 0 END) AS n_never_tried,
       SUM(CASE WHEN s.zip_ok = 0 THEN 1 ELSE 0 END)       AS n_failed
FROM   tgt t LEFT JOIN doc_store s USING (rcept_no);
-- 기대: n_never_tried = 0. n_failed 는 http_status='014'(정정공시 파일없음)만 허용.
--       평시 n_target 3~37, 반기 마감일 ~2,280 (실측).
-- 전기간 현재값(09-09): n_target 174,311 · n_ok 171,179 · n_failed 3,130(전부 014) · n_never_tried 2.
```
파일시스템 대조(선택): `ls data/raw/documents/2026 | wc -l` 이 `SELECT COUNT(*) FROM doc_store WHERE zip_ok=1 AND rcept_no LIKE '2026%'` 와 일치해야 한다 — 09-09 실측 **9,952 = 9,952**.

### 7-4. WISE

```sql
-- ① 그날 런이 있었고 실패가 없었는가
SELECT run_at, mode, n_stocks, n_req, n_ok, n_bad, bad_summary
FROM   ws_run_log WHERE date(run_at, '+9 hours') = date(:D_ISO)
ORDER BY run_at DESC LIMIT 1;
-- 기대: 행 1개. mode='full'. n_bad = 0. n_ok = n_req. n_stocks 2,560~2,570 (실측 2,563~2,566).

-- ② 요청 수가 커버리지 구성과 정확히 맞는가 (조용한 부분실패 탐지)
SELECT (SELECT COUNT(*) FROM ws_coverage WHERE status='covered') * 15
     + (SELECT COUNT(*) FROM ws_coverage WHERE status='none')    *  2 AS expected_req,
       (SELECT n_req FROM ws_run_log ORDER BY run_at DESC LIMIT 1)    AS actual_req;
-- 기대: 두 값 일치. 09-09 실측 807*15 + 1756*2 = 15,617 = 실제 15,617.

-- ③ 원장 스냅샷이 그날 축에 들어왔는가
SELECT fetched_date, COUNT(*) AS n_rows, COUNT(DISTINCT cmp_cd) AS n_stocks
FROM   ws_raw WHERE fetched_date = date(:D_ISO);
-- 기대: n_rows 15,500~15,700 · n_stocks 2,560~2,570 (실측 9일 연속 이 밴드).

-- ④ 커버리지 붕괴 조기경보
SELECT SUM(status='covered')*1.0/COUNT(*) AS cov_rate FROM ws_coverage;
-- 기대: 0.30~0.33 (실측 808/2,566 = 0.315). 0.25 미만이면 WISE 페이지 개편 의심.

-- kiwoom.db (유니버스 소스)
SELECT MAX(snap_date), COUNT(*) FROM ka10099_stock_master
WHERE snap_date = (SELECT MAX(snap_date) FROM ka10099_stock_master);
-- 기대: MAX(snap_date) = :D, COUNT 4,300~4,320 (실측 4,307~4,309).
```

### 7-5. 예산 마감 확인 (공통)

```sql
SELECT key_id, COUNT(*) AS n_calls, MIN(ts), MAX(ts)
FROM   dart_call_log
WHERE  ts > strftime('%Y-%m-%dT%H:%M:%S','now','+9 hours','start of day','-9 hours')
GROUP BY key_id;
-- 기대(일일 증분 설계 후): k2+k3 합계가 우리 용량 80,000 의 5% 미만.
--   kael 이 0 이 아니면 즉시 조사 — v3 프로덕션 키를 건드렸다는 뜻이다.
-- 참고 실측: 현행 daily_dart.sh 그대로면 k2 ≈ 16,500 (stage3 013 재확인 16,086 + 스윕 410).
```

---

## 8. 플랜 작성자에게 — 결정이 필요한 쟁점

### 8-1. `daily_dart.sh` 를 그대로 재등록할 것인가 → **권고: 아니다**

| 근거 | 실측 |
|---|---|
| 하루 16,496콜 중 **16,086콜(97.5%)이 2027-06 까지 확정적으로 013 인 재확인** | stage3 6종 × `bsns_year=2026`·`11011` no_data 2,681유닛 |
| 스테이지 2 가 2026 을 안 쏜다 (DEFECT-B03) | `range(2016,2026)` |
| 스테이지 3 이 11011 만 쏜다 (DEFECT-B02) | 6개 테이블 전건 `req_reprt_code='11011'` |
| 스테이지 5 가 빠져 있다 (DEFECT-B04) | 스크립트에 stage 5 호출 없음 |
| corp/corp_range `ok` 는 영구종결이라 신규 이벤트를 못 잡는다 | `backfill_dart.py:680-681` |
| 전일 프로세스 `kill` 로직이 남아 있다 | `daily_dart.sh:15-24` — 백필 시대 유물. 증분 전용 크론에는 불필요·위험 |

**선택지** — (A) 그대로 재등록(갭은 메워지나 낭비 + 4결함 유지) / (B) 갭 메우기용 1~2회 수동 실행 후 새 증분 스크립트를 크론에 / (C) 새 스크립트만 만들고 갭도 그것으로 메운다.

### 8-2. 스윕 → 상세 자동 연결을 만들 것인가 → **필요하다. 설계는 이미 있다**

`COLLECT_PLAN.md:221` 이 정답을 적어 뒀다: **"당일 `list.json` 공시에 해당 유형이 있는 corp 만 그날 재호출 — 이벤트 없으면 0콜"**. 구현만 없다. 결정할 것:

1. **`report_nm` → 엔드포인트 매핑표를 만들 것인가**, 아니면 **해당 corp 에 DS005 15종 전부 재호출**할 것인가?
   - 매핑표: 일 20~40콜. 정확하지만 어휘 표 유지 부담 + 새 유형이 조용히 빠진다.
   - 전량 재호출: 일 300~510콜(용량의 0.6%). 누락 위험 없음. → **전량 재호출 권고**.
2. **`ingest_log` 의 `ok` 종결을 어떻게 우회할 것인가?**
   - (a) 증분 러너가 대상 corp 의 `ingest_log` 행을 지우고 `backfill_dart.py --only <ep> --corps <corp>` 호출 — 코드 수정 0.
   - (b) `--force-corps` 플래그 신설 — 수술적이나 코드 수정.
   - (c) `RECHECK_DAYS` 를 corp/corp_range `ok` 에도 적용 — 간단하나 30일마다 3,478×15 = 52,170콜의 무차별 재확인.
   - 어느 쪽이든 `store()` 가 멱등이라 재호출 자체는 안전하다.
3. **정정공시(corp_year `ok` 재호출)를 어디까지 따라갈 것인가?** DART 재무 API 는 최신 판본만 준다. `[기재정정]` 이 일 48~155건(실측) 나오는데 그 corp·기수의 `fin` 을 다시 쏘면 정정 반영 판본이 새 `rcept_no` 행으로 들어온다. 안 쏘면 **PIT 재무가 원본 판본에 영원히 고정된다**. `report_nm` → (bsns_year, reprt_code) 파싱 코드가 없다는 점이 걸림돌.

### 8-3. `list.json` 스윕 창을 좁힐 것인가

- 현행(열린 분기 전량): 410~890콜/런, 분기 경계 정합 + `total_count` 3자 대조 성립.
- 당일 창(`bgn=end=D`): 5~23콜. 그러나 `sweep_disclosure.py:390-393` 이 **"분기 경계 밖 범위는 창 id 가 달라져 원장에 중복 적재된다 — 검증용으로만 쓸 것"** 이라고 명시.
- 예산상 410콜은 용량의 0.5% 라 전량 유지가 안전하지만 분기 말(886페이지)엔 런타임이 길다.
- **결정 필요**: 전량 유지 / 당일 창 + 주 1회 분기 전량 대조 / 창 id 정규화 코드 수정.

### 8-4. DEFECT-B01(열린 창 영구 mismatch) 을 고칠 것인가

완료 게이트를 `ingest_log.status='ok'` 에 걸면 매일 실패로 판정된다.
(a) 완료 판정을 `dart_disclosure.rcept_dt = D` 행 존재로 대체(7-1 ①) — 코드 수정 0. **권고**. / (b) `stored_rows(distinct=True)` 를 "이번 런에서 본 rcept_no" 로 한정. / (c) 열린 창은 `n_db >= tc` 로 완화.

### 8-5. v3 키 충돌 회피 시각 — **실질 위험은 0, 다만 시각은 정리할 가치가 있다**

- v3 는 `DART_API_KEY`(우리 `kael`, 최후 순위)만 쓰고 **일 60~90콜**(실측). 우리 증분이 평시 640콜이면 k2 하나로 끝나 kael 은 안 나간다. → **키 충돌 위험 사실상 0.**
- 다만 동시 실행 시각은 겹치지 않는 편이 낫다. v3 크론 = **07:45 / 18:15 KST**. `DART_DESIGN.md:623` 원설계는 이를 피해 **08:05 / 19:05** 를 제안했다.
- `COLLECT_PLAN.md:21` 은 우리 일일 증분을 **19:00~19:35 KST** 로 잡았다(장 마감 +3.5h, 카엘 시작 전 25분 여유). DART 접수는 18:00 마감이라 19:00 시작이면 그날 접수분이 확정돼 있다. `daily_wise.sh`(06:00)와도 안 겹친다.
- **결정 필요**: 19:00 KST 단일 실행(당일 완결) vs 00:01 KST(현행 시각, 전일분 확실하나 하루 늦음) vs 둘 다.

### 8-6. `dart_universe.py` 의 KRX 의존 (DEFECT-B05)

신규 상장사가 유니버스에 못 들어온다. `backfill_wise.py:182-201` 이 이미 키움 일별 마스터로 갈아탄 전례가 있다. 단 `dart_universe.py` 는 **폐지 종목의 상장구간(first/last year)** 도 필요한데 키움 마스터는 당일 스냅샷뿐 → **KRX 전기간(과거) + 키움 마스터(현재)의 합집합**이 필요하다. KRX 원장 재개(08-20 동결 해제)와 묶어 결정.

### 8-7. 문서층 일일 증분 — 수집과 파싱을 분리할 것

- **수집**: `backfill_docs.py` 는 `doc_store` 차집합 방식이라 **지금 그대로 매일 짧게 돌리면 된다**(평시 3~37콜, 수 초). 단 `sleep_to_kst_midnight()` 이 있어 예산 소진 시 프로세스가 최대 24h 남는다 → `--max-calls` 지정 또는 그 대기 우회 필요.
- **파싱**: `DOC_DESIGN.md:17` 이 "`doc_store` 신규 행만 파싱" 을 명시했으나 설계 미완. 미결 중 **`stg_doc_parse_log.t_*_ms` 가 payload 에 있어 `content_hash` 가 실행마다 달라지는 문제**(`DOC_DESIGN.md:420`)는 일일 증분에서 **재현성 게이트를 매번 깨뜨린다** — 증분 크론 전에 `payload_exclude` 처리가 선행돼야 한다.

### 8-8. 알림이 없다

DART·문서·WISE 어느 쪽도 실패 시 사람에게 도달하는 경로가 없다(로그 파일뿐). `COLLECT_PLAN.md §4-4` 가 3등급 알림 체계를 설계했으나 미구현. **09-02 에 `daily_dart.sh` 가 crontab 에서 빠진 뒤 7일간 아무도 몰랐다**는 것이 그 증거다. 일일 증분 플랜에 **완료 판정 쿼리(7절) + 미달 시 알림**을 반드시 포함할 것.
