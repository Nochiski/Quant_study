# 일일 증분 파이프라인 감사 결함 수정 플랜 (2026-09-19)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 09-19 전수 감사(고유 결함 45건, 상 4·중 24·하 17)에서 코드·운영으로 닫을 수 있는 항목을 전부 닫고, 항목마다 검증 게이트를 통과시켜 서버에 배포한다.

**Architecture:** 기준 브랜치 `fix/pipeline-audit-2026-09-19` = `origin/feat/daily-v2-b`(d621000) = **서버 사본과 바이트 동일**. 갈래 6개가 파일 소유권을 나눠 병렬로 고친 뒤(브랜치 `fix/audit-{daily,stage,equity,ops,doc,workbench}`), 오케스트레이터가 기준 브랜치에 머지 → 전체 테스트 → `deploy.sh`(가드 신설) → 서버 수동 확정 빌드 1회 → 다음 자동 체인으로 실전 게이트.

**Tech Stack:** Python 3.11 · duckdb · sqlite3 · bash · pytest(`uv run --project backend pytest database/tests -q`, CI 와 동일) · ruff/pyright.

**참조:** 감사 원본 `scratchpad/audit/{A..F,SUMMARY}.md`(세션 scratchpad, 절대경로는 디스패치 프롬프트에) — 각 결함의 상황·인풋·에러 위치·증거·확인 방법은 거기 있다. 이 플랜은 "무엇을 어떻게 고치고 무엇으로 검증하나" 만 적는다.

---

## 0. 상태 블록 (정본 — 작업 단위마다 갱신)

| 단계 | 상태 | 비고 |
|---|---|---|
| P0 서버 즉시 조치(크론·프리패스 재생성) | 진행 중 | 프리패스 `snap_20260918T232444Z` 09-19 18:34 KST 시작 |
| 갈래 1 daily | 대기 | |
| 갈래 2 stage | 대기 | |
| 갈래 3 equity | 대기 | |
| 갈래 4 ops·docs | 대기 | |
| 갈래 5 doc 증분 | 대기 | |
| 갈래 6 workbench | 대기 | |
| 통합·테스트·배포 | 대기 | |
| 실전 게이트(다음 체인) | 대기 | |

## 1. 검증 게이트 (전 갈래 공통)

| 게이트 | 판정 | 실행 |
|---|---|---|
| **G-A 코드** | 변경 파일 `ruff check` 0 · `pyright --pythonversion 3.11` 0 신규 오류 · `uv run --project backend pytest database/tests -q` **전부 green**(기존 실패 1건 `test_equity_s07_contract.py::test_FX_N_close_변조_사본은_EGC01_만_FAIL` 는 로컬 환경 문제, main 과 동일 — 제외) | 갈래 안에서 관련 테스트, 통합 후 전체 |
| **G-B 배포** | `deploy.sh`(dry-run) 전송 목록 = 의도한 파일만 · 서버 `md5sum` = 브랜치 · 서버에서 `build_chain.sh morning --date 20260918 --dry-run` rc 0 · `python -c "import stage.health, equity.catalog, daily.kw_daily"` rc 0 | 오케스트레이터 |
| **G-C 실전** | 배포 직후 수동 `build_chain.sh morning --date 20260918` rc 0(stage health 6/6, equity 29/29) → 09-20(일) 08:10 체인이 **"이미 완료 — 건너뜀"** info 1건 · 09-21(월) 18:05/21:20 저녁 체인 rc 0 + 새 게이트 출력 존재 · 워치독 오탐 0 | 오케스트레이터(로그 확인) |
| **G-D 데이터** | 항목별 쿼리(각 Task 의 "게이트" 줄) | 갈래 + 오케스트레이터 |
| **G-E 리뷰** | 통합 브랜치에 `superpowers:code-reviewer` 1회, 상 4건은 결함 원문과 대조 | 오케스트레이터 |

## 2. 결정·가정 (사용자 확인 필요는 ★)

1. **E01 KIS 신용 lag** = `recommended_lag_sessions=3`(실입수 기준). `available_date` 는 그대로(백필 구간의 observed_date 가 2026-08 이라 관측 기준으로 바꾸면 과거 PIT 가 전부 깨진다).
2. **B02 G9 허용폭** = `volume_match_ratio` 기준값 − `5e-5`(≈380행/759만) 까지 통과. 기준값은 게이트와 같은 술어(20:00 조건)로 재측정.
3. **A02 원천 정정** = 사용자 결정 6-①(신규 행만 적재) 유지. 카운터 잡음(컬럼 추가·표기차)만 제거하고 진짜 정정 목록을 로그에 남긴다.
4. **A09 corp_action_candidates** = 기록형(PASS + items). 대조는 equity `adj_factor`·`corp_event` 가 이미 매일 한다(E 확인).
5. **워치독** evening_build 23:00→**23:30**, morning_build 09:45→**10:00**.
6. ★ **E03 v3 컨센서스 동결로 opinion_daily 종목 2,533→839** — 코드 변경 없음. 동결 유지 여부는 사용자 결정.
7. **연기(TECH_DEBT 등재만)**: A08 저녁 락 3h(별도 락 설계) · B09/C10 공유 경계 축소(마운트 설계·sudo) · B04 stage 표별 커밋 롤백(READY 마커로 대체) · E10/C06-2 EG5c 롤링 표본(EG-grid 로 우선 대체).
8. **문서층 따라잡기** = 오늘 전량 재파싱(P0) → 4표 수동 빌드 → 이후 증분(갈래 5).

## 3. 갈래별 작업

파일 소유권: 한 파일은 한 갈래만 만진다. 다른 갈래 파일이 필요하면 "훅 지점" 을 프롬프트에 적어 오케스트레이터가 통합한다. 각 Task 는 (a) 실패 테스트 → (b) 최소 구현 → (c) 테스트 통과 → (d) 커밋(영문 메시지, `fix(daily): …` 꼴, 본문에 `DEFECT-A01` 참조) 순서다.

### 갈래 1 · daily (원장 수집)
소유: `database/src/daily/*.py`, `database/src/backfill_krx.py`, `database/src/master_daily.py`, `database/src/api.py`, `database/scripts/daily_ledger.sh`, `database/scripts/daily_build.sh`, `database/scripts/daily_evening.sh`, `database/scripts/sync_calendar.sh`, `database/tests/test_daily_*.py`

#### Task 1.1 — A01 키움 저녁 직행 결손 게이트 [상]
- Modify: `src/daily/kw_daily.py` (`fetch()` 511-527 · `FetchStatus` · `main()` 736-743)
- Test: `tests/test_daily_kw.py`
- 구현: `FetchStatus.COVERAGE_FAILED` 신설. `fetch()` 끝에서 `commit=True` 로 호출된 TR 마다 (a) `sum(n_error) > ERROR_MAX = max(5, ceil(0.005 × n_calls))` 또는 (b) `_kw_incoming_<tr>` 의 `COUNT(DISTINCT ticker) WHERE dt = date` / `len(tickers)` < `COMMIT_MIN_RATIO = 0.98` 이면 실패. `rc` 는 2. detail 에 `coverage=<n>/<universe> errors=<n>` 를 항상 싣는다(통과해도). 아침 경로(`GATE_TR` 오염 게이트)는 그대로.
- 게이트: 유니버스 100·incoming 97종목 픽스처 → `COVERAGE_FAILED`, 99종목 → OK. 서버 실전: 09-21 저녁 로그에 `coverage=26xx/2651 errors=0`.

#### Task 1.2 — D01·A03·B10 08:10 체인 "이미 완료" 가드 [중]
- Modify: `scripts/daily_build.sh` (D 산출 직후 `:57-60`)
- 구현: `DATE_ARG` 가 비었고 `QL_FORCE` 가 비었을 때 `data/deliver/history/${D}_morning.json` 이 있고 `health.stage=="ok" and health.equity=="ok"` 이면 `notify info "daily_build 건너뜀(D=$D 확정판 완료)"` 후 `exit 0`(런 로그 파일에도 한 줄). `daily_ledger.sh:56-62` 와 같은 꼴의 파이썬 원라이너.
- 게이트: 09-20(일) 08:10 로그 `logs/daily_build_20260920.log` 에 "건너뜀" 1줄, `data/snapshots/` 에 새 스냅샷 없음.

#### Task 1.3 — A04 유예 카운트를 거래일로 [중]
- Modify: `src/daily/calendar.py`(`Calendar.count_trading_days(a: date, b: date) -> int`, a 초과 b 이하), `src/daily/universe.py` (`requested()` 시그니처에 `cal: Calendar` 추가, `missing_days = cal.count_trading_days(last_seen, snap_date)`), 호출부 `kw_daily.py`·`kis_daily.py`
- Test: `tests/test_daily_universe.py`(금요일 이탈 → 다음 화요일 missing_days=2), `tests/test_daily_calendar.py`
- 게이트: 09-21 `data/daily/universe_kw.json` 의 472220 `missing_days` 가 3 이 아니라 2(9/17·9/18 만) → 제외 안 됨.

#### Task 1.4 — A05 KRX 재시도가 오류도 덮도록 [중]
- Modify: `src/backfill_krx.py`(`main()` 끝에 `rate`/`error`/`fatal` 이 있으면 `sys.exit(1)`, `fatal` 은 `ingest_log` 에 `status='fatal'` 기록), `scripts/daily_build.sh:42-56 krx_step`(재시도 조건을 `status IN ('pending','rate','error')` 로, `fatal` 은 즉시 `return 1` + 알림 본문에 "KRX 401")
- Test: `tests/test_daily_krx_guard.py`
- 게이트: 코드 경로 테스트만(실전 재현 불가).

#### Task 1.5 — A06 KIS 완료 게이트를 "이번 런이 넣은 행" 으로 [중]
- Modify: `src/daily/kis_daily.py`(`run()` 시작 시 `run_started_at` 기록, `gate()` 가 `collected_at >= run_started_at` 행만 센다; `failures` 비율 > 2% 면 `PARTIAL` 대신 `GATE_FAILED`(rc 2)), `src/daily/ledger_health.py`(REQUIRED `kis.credit.fresh`: `max(deal_date) >= prev_trading_day(D, 3)` — 3세션 뒤처지면 FAIL, 2세션은 WARN)
- Test: `tests/test_daily_kis.py`, `tests/test_daily_health.py`
- 게이트: 09-22 06:00 로그 `gate … n_rows(this run)=25xx`.

#### Task 1.6 — A02 정정 카운터 잡음 제거 [중]
- Modify: `src/daily/kw_daily.py:598-609 merge_tr`(`ensure_table` 이 새로 추가한 컬럼은 `differs` 에서 제외; 비교는 `TRY_CAST(REPLACE(x,'+','') AS REAL) IS NOT …` 로 숫자 정규화; 진짜 변경 상위 20건 `(ticker, dt, col, old, new)` 를 print)
- Test: `tests/test_daily_kw.py`(`"+0.00"` vs `"0"` → changed 0; 새 컬럼 → changed 0)
- 게이트: 09-22 08:10 머지 로그 `changed=` 가 표기차 없이 실제 정정만.

#### Task 1.7 — A07 휴장 캐시 다년 [중]
- Modify: `scripts/sync_calendar.sh`(원천의 `year` 로 `data/calendar/kis_holidays_<year>.json` 에 저장, 기존 `kis_holidays.json` 은 호환용으로 유지), `src/daily/calendar.py:load()`(디렉터리의 `kis_holidays*.json` 전부 합집합, 파일별로 "전건 해당 연도" 검증; 조회 연도 파일이 없으면 `weekend_only` 폴백 대신 **`detail` 에 명시 + 그 연도의 12/25·12/31·1/1 만이라도 하드코딩 폴백 금지** — 없으면 `KeyError` 로 체인이 멈추게), `prev_trading_day` 는 합집합으로 동작
- Test: `tests/test_daily_calendar.py`(2026+2027 두 파일 → 2026-12-31 휴장, 2027-01-01 휴장)
- 게이트: 서버 `data/calendar/kis_holidays_2026.json` 생성 확인.

#### Task 1.8 — A10 daily_wise 실패 시 런로그 [하]
- Modify: `scripts/daily_ledger.sh:79`(`status = ok` 조건에 `[ -z "$FAILED" ]` 추가)

#### Task 1.9 — A12·D11 토큰 캐시 권한·예외 본문 [하]
- Modify: `src/api.py:76-77, 118-119`(`os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` + 기존 파일 `os.chmod 0o600`), `:73-74`(`RuntimeError(f"KIS 토큰 발급 실패: keys={sorted(r)} msg={r.get('error_description','')[:80]}")`)
- 게이트: 서버 배포 후 첫 재발급 시 `stat -c %a src/.kis_token.json` = 600.

#### Task 1.10 — A09 corp_action_candidates 기록형 [하]
- Modify: `src/daily/ledger_health.py:208-213`(`Status.PASS` 고정, value 에 items, detail "후보 n건 기록 — adj_factor·corp_event 가 매일 대조")
- Test: `tests/test_daily_health.py`

#### Task 1.11 — D07 GC 만 실패한 rc 1 을 crit 으로 올리지 않기 [하]
- Modify: `scripts/daily_build.sh:87`(`build_morning` rc 1 이면 `FAILED_SOFT="build_morning(rc=1)"`, crit 대신 warn "확정 빌드 후처리 실패"; rc ≥ 2 만 crit)

### 갈래 2 · stage
소유: `database/src/stage/{health,manifest,build,baseline,gates,rules_krx,rules_dart}.py`, `database/scripts/run_stage_all.sh`, `database/docs/STAGE_DESIGN.md`, `database/docs/STAGE_SPEC.md`, `database/tests/test_stage_*.py`(doc 계열 제외)

#### Task 2.1 — B01 건전성 C6 신선도 [상]
- Modify: `src/stage/manifest.py`(`BuildRecord.max_available_date: str | None = None`, `max_observed_date` 동일 — 기본값 None 이라 옛 MANIFEST 호환), `src/stage/build.py`(커밋 직전 out 관계에서 `max(available_date)`·`max(observed_date)` 계산해 기록; 열이 없으면 None), 신규 `src/stage/freshness.py`(표별 허용 지연 일수 선언: 세션 표 0, `stg_credit_daily` 3, DART 이벤트·공시 7, 재무·주식수 120, 컨센서스 7, `FROZEN`(None)= `stg_short_daily_kis, stg_loan_daily_kis, stg_flow_split_daily, stg_delisted_master, stg_v3_*` 4표 — 사유 문자열 필수), `src/stage/health.py`(`_c6_fresh(pairs, date, allow)`: `max_available_date` 가 `D − allow(캘린더일)` 미만이면 FAIL, FROZEN 은 SKIP+사유 기록, 기록 없는 옛 판은 SKIP)
- Test: `tests/test_stage_health.py`(합성 MANIFEST 3표: 신선/지연/동결)
- 게이트: 서버 수동 빌드 후 `logs/health/stage_20260918_morning.json` 에 C6 pass, `skipped` 에 동결 8표 사유.

#### Task 2.2 — B02 G9 기준값 술어 통일 + 허용폭 [중]
- Modify: `src/stage/rules_krx.py`(교차 조인 술어를 상수 `CROSS_JOIN_PREDICATE_SQL` 로 추출), `src/stage/baseline.py:38-39`(`_PRICE_JOIN` 이 그 상수를 쓴다; CLI `python -m stage.baseline --only stg_price_daily.volume_match_ratio --snapshot-id S` 추가 — 지정 지표만 재고 나머지는 보존, `measured_at`·`note` 갱신), `src/stage/gates.py:266-269`(`tol = baseline.get("volume_match_ratio_tol", 0.0)`; `vol_r < base − tol` 일 때만 FAIL), `docs/STAGE_SPEC.md §6`·`STAGE_DESIGN.md §9 G9`(결정 11 반영, 허용폭 5e-5 근거)
- Test: `tests/test_stage_baseline.py`(두 SQL 이 같은 술어), G9 tol 테스트
- 게이트(서버, 오케스트레이터): `--only` 재측정 → `baseline.json.stg_price_daily.volume_match_ratio` 가 20:00 술어 값으로 갱신되고 `volume_match_ratio_tol: 5e-5` 존재. `check_baseline_lock.py` 통과.

#### Task 2.3 — B03 C3 를 upsert 표에도 [중]
- Modify: `src/stage/health.py:84-85 _c3_monotonic`(append_only 가 아닌 표는 **닫힌 연도 파티션**(`partitions[].path` 의 `year=YYYY` < 현재 KST 연도)의 `n_rows` 가 직전 판보다 줄면 FAIL; `first_write_wins` 표는 전 파티션)
- Test: `tests/test_stage_health.py`

#### Task 2.4 — E08 rcept_dt·접수번호 접두 정합 [하]
- Modify: `src/stage/rules_dart.py:480`(`available_date = greatest(rcept_dt, CAST(substr(rcept_no,1,8) AS DATE))` — 보수적 방향; 기록 지표 `n_rcept_dt_before_no_prefix`·`n_rcept_dt_after_no_prefix` 를 G3 metrics 에), `docs/STAGE_SPEC.md` 해당 절
- Test: `tests/test_stage_*` 에 stg_disclosure 규칙 테스트가 있으면 확장, 없으면 신설(`test_stage_rules_dart.py`)
- 게이트: 서버 빌드 후 `stg_disclosure` 에 `available_date > today` 0건, 9/21 오타 행의 available_date = 9/18.

#### Task 2.5 — E09 주석 정정 [하]
- Modify: `src/stage/rules_dart.py:484` 주석("S1b 실측 충돌 0" → "일일 운영에서 같은 rcept_no 판본이 쌓인다(09-19 실측 2026 파티션 1,147건). 소비자는 GROUP BY 로 접는다")

#### Task 2.6 — B07·B08 stage 문서 [하]
- Modify: `docs/STAGE_DESIGN.md`(§9 G5 "src_mtime 비교" 미구현 명시, G9 결정 11), `docs/STAGE_SPEC.md`(같은날 폴드·허용폭)

### 갈래 3 · equity
소유: `database/src/equity/**`(`sql/`, `rules_s*.py`, `catalog.py`, `contract.py`, `gates.py`, `build.py`, `baseline*.json` 제외), `database/scripts/equity_gate_all.sh`, `database/scripts/equity_rebuild_all.sh`, `database/scripts/run_equity.sh`, `database/docs/EQUITY_*.md`, `database/tests/test_equity_*.py`

#### Task 3.1 — E01 KIS 신용잔고 권장 지연 [상]
- Modify: `src/equity/rules_s10.py:537-546`(`recommended_lag_sessions=3, recommended_lag_days=4`, `disclosure_basis="… 실입수 T+3 06:00 KST(09-19 실측 전 구간 +3일). 공표 T+2 이나 우리 체인은 T+3 아침에 받는다"`), `docs/EQUITY_FIELD_MAP.md` 해당 행
- Test: `tests/test_equity_s10_credit.py`(프로필 lag 값)
- 게이트: 서버 빌드 후 `dataset_profile` 의 `credit.margin_balance.recommended_lag_sessions = 3`.

#### Task 3.2 — F02 미파싱 정정 라벨 `not_parsed` [중]
- Modify: `src/equity/sql/disclosure_version.sql:189-192`(`WHEN NOT v.has_corr_row THEN CASE WHEN NOT coalesce(v.zip_ok,FALSE) THEN 'no_zip' WHEN NOT v.has_doc_meta THEN 'not_parsed' ELSE 'no_page' END` — `has_doc_meta` 는 이미 입력으로 고정된 `stg_doc_meta` 에 `rcept_no` 존재 여부), `src/equity/rules_s11.py:54`(`DATE_CHECK_UNMEASURED` 에 `not_parsed`), `:227-232`(material_mismatch 술어에 `not_parsed` 포함), `docs/EQUITY_DESIGN.md:144`·`EQUITY_GATES.md` 어휘
- Test: `tests/test_equity_s11_disclosure.py`
- 게이트: 서버 빌드 후 `date_check='not_parsed'` 가 9/1 이후 정정에 찍히고 `no_page` 신규 0.

#### Task 3.3 — E07 저녁 잠정 행에 폐지 종목 제외 [하]
- Modify: `src/equity/sql/price_daily.sql:95-107`(`kw` 저녁 행을 `stg_listing_daily` 최신일(T-1) 상장 종목으로 제한 — `price_daily` 입력에 `stg_listing_daily` 가 이미 있는지 `rules_s04.py inputs` 확인, 없으면 추가), `rules_s04.py` EG3 기록 지표 `n_evening_rows_not_listed`
- Test: `tests/test_equity_s04_price.py`

#### Task 3.4 — C04 catalog·contract duckdb 자원 제한 [중]
- Modify: `src/equity/catalog.py:115, 208, 289`, `src/equity/contract.py:519`(공통 `_connect(path=None, read_only=False)` 가 `SET threads=3; SET memory_limit='8GB'` — `build.py:164-165` 와 동일 값)
- Test: `tests/test_equity_catalog.py`(연결 후 `SELECT current_setting('threads')` = 3)

#### Task 3.5 — C06 격자 표 최신 구간 게이트 `EG21` [중]
- Modify: `src/equity/gates.py`(공용 `eg21_recent_grid(ctx)`: 표의 `date` 축에서 `D − lag` 이하 최신 세션 K=3 개의 행수가 직전 20세션 중앙값 × `recent_grid_row_ratio_min`(기본 0.8) 미만이면 FAIL; lag·ratio 는 baseline 상수, 저녁 basis 의 T 세션은 제외), 등록: `rules_s08.py`(flow_daily), `rules_s09.py`(short_daily), `rules_s10.py`(credit_daily, lag 3), `rules_s17.py`(consensus_daily), `rules_s18.py`(opinion_daily), `rules_s15.py`(holder_daily 가 세션 격자면), `docs/EQUITY_GATES.md`
- Test: `tests/test_equity_gates.py`(합성 격자: 최신 세션 반쪽 → FAIL)
- 게이트: 서버 빌드 29표 EG21 pass 또는 skip(사유), fail 0.

#### Task 3.6 — C08 rebase-asof 승인 기록 [중]
- Modify: `src/equity/catalog.py:324-327`(`--rebase-asof` 에 `--reason "…"` 필수; 승인 시 `data/equity/_asof/_approvals/<utcstamp>.json` 에 `{reason, approver: os.environ.get("USER"), previous_snapshot_id, snapshot_id, diff_by_kind(뷰별), columns_added/removed}` 영구 기록(GC 대상 아님); 뷰의 컬럼 집합이 달라졌으면 `diff_by_kind` 에 `schema_changed: true` 를 넣고 detail 에 명시)
- Test: `tests/test_equity_catalog.py`
- 게이트: 다음 EG5c FAIL 시 재승인 절차에 `--reason` 없으면 거부.

#### Task 3.7 — C09 ORDER 단일화 [하]
- Create: `scripts/equity_order.txt`(29표 한 줄 하나) · Modify: `scripts/equity_rebuild_all.sh:39`, `scripts/equity_gate_all.sh:12`(`ORDER=$(tr '\n' ' ' < scripts/equity_order.txt)`)

#### Task 3.8 — E02·E06 KIS 축 커버리지 선언 [중]
- Modify: `src/equity/rules_s09.py`(FieldProfile 추가: `short.short_sale_volume_kis`(`short_volume_kis_shr`), `short.borrowed_quantity_kis`(`lending_balance_kis_shr`) — `requires_confirmation=True`, evidence 에 "일일 수집 범위 밖(플랜 R10) — coverage_to 로 종료일이 드러난다"), `src/equity/rules_s08.py`(`flow.*_kis` 동일), `src/equity/rules_s01.py`(security `delist_date_kis` 주석: KIS 마스터 2026-08-26 단일 조회), `docs/EQUITY_FIELD_MAP.md`
- Test: `tests/test_equity_*` 프로필 수 갱신
- 게이트: `dataset_profile` 에 `*_kis` 필드 `coverage_to = 2026-08-14`.

#### Task 3.9 — C03 equity 부분 실패 롤백 [중]
- Modify: `src/equity/build.py`(`manifest` 가 stage.manifest 재사용이면 stage 갈래와 겹치므로 **equity 쪽에 `rollback(table_root, to_build_id)` 를 `src/equity/inputs.py` 또는 새 `src/equity/rollback.py` 에** 둔다: MANIFEST `current_build` 를 지정 판으로 되돌리고 `v=` 디렉터리는 남긴다), `src/equity/__main__.py`(`equity rollback --pass <PASS>`: `logs/equity/rebuild_<PASS>/summary.tsv` 의 rc 0 표를 이번 판 직전 판으로 되돌림), `scripts/equity_rebuild_all.sh:61`(`QL_EQUITY_CONTINUE` 아닌 경로에서 첫 실패 시 `python -m equity rollback --pass "$PASS"` 호출 후 exit), `docs/EQUITY_WORKFLOW.md`
- Test: 신규 `tests/test_equity_rollback.py`(임시 MANIFEST 두 판 → 롤백 후 current = 이전)

#### Task 3.10 — C05 contract 단계 [중]
- Modify: `src/equity/contract.py`(현재 데이터·e1.15.0 컬럼에서 통과하도록 필요한 수정만; `basis` 열이 있는 표에서 EGC-01 은 `basis='krx'` 행만 대조), `docs/EQUITY_WORKFLOW.md`(체인 편입·`_engine` 갱신 규약)
- 훅 지점(갈래 4): `build_chain.sh` catalog 뒤 `contract_step() { $PY -m equity contract --engine-src "$QL_HOME/_engine"; }`(실패는 기록형 warn), `deploy.sh` 가 `backend/src/backtest_engine/` → 서버 `_engine/backtest_engine/` 동기화
- 게이트: 서버 `python -m equity contract --engine-src ~/quant-ledger/_engine` rc 0, `_contract_meta.json.builds` 가 `m_` 판.

#### Task 3.11 — F01 fin_std 추정 폴백 가시화 게이트 [상 — 게이트 부분]
- Modify: `src/equity/rules_s12.py`(EG3 기록 지표 `n_period_end_inferred_recent`(available_date > D−30 인 행 중 `period_end_basis='inferred'`) + `ratio_period_end_inferred_recent`; 폐기형 임계 `fin_std_inferred_recent_ratio_max` 기본 0.2 를 baseline 상수로), `docs/EQUITY_GATES.md`
- Test: `tests/test_equity_*`(합성: inferred 50% → FAIL)
- 배포 순서: 문서층 따라잡기(P0) **뒤에** 서버 반영. 통합 시 오케스트레이터가 확인.

### 갈래 4 · ops · docs
소유: `database/scripts/{build_chain,build_evening,build_morning,watchdog,notify,gc,deploy,rotate_logs,backup_raw}.sh`, `database/scripts/daily_report.py`, `database/README.md`, `database/docs/{TECH_DEBT,DECISIONS_PENDING}.md`, `database/docs/plans/2026-09-11-daily-incremental-v2.md`

#### Task 4.1 — D02·D03·D06 워치독 시각·제목 [중]
- Modify: `scripts/watchdog.sh:6-7, 16-18`(23:30 / 10:00 제목·주석, 근거 = stage 실측 43~66분 + equity 11분), `:74`(`evening_ledger` 도 `evening_build` 와 같이 UTC→KST 변환), `README.md` 크론표
- 서버 크론(오케스트레이터 P0): `0 14 * * 1-5` → `30 14 * * 1-5`, `45 0 * * *` → `0 1 * * *`
- Test: `bash -n`; 실전 09-21 23:30 워치독 info.

#### Task 4.2 — D04 알림 실패 기록 [중]
- Modify: `scripts/notify.sh:44-49`(실패 시 `logs/notify_failed.log` 에 `<utc> <level> <title> curl_rc resp` 1줄 append — stderr 와 무관하게 항상), `scripts/daily_report.py`(`notify_failed.log` 최근 24h 건수를 리포트 첫 줄에; 0 이 아니면 제목에 `⚠ 알림 실패 n`), `scripts/watchdog.sh`(같은 건수를 본문에)
- 서버 크론(오케스트레이터 P0): 세 체인 줄에 `>> logs/cron_daily_ledger.log 2>&1` 류 리다이렉트 추가, `rotate_logs.sh` 대상에 포함
- Test: `notify.sh` 를 `BOT_TOKEN=bad` 로 호출 → `notify_failed.log` 1줄(로컬 dry).

#### Task 4.3 — D12 gc.sh 락 + 프리패스 캐시 보존 [하 → 갈래 5 선행]
- Modify: `scripts/gc.sh`(`/tmp/quant_ledger_build.lock` `flock -n` 실패면 warn 후 exit 0; `_tmp/doc` 은 전부 삭제가 아니라 **`stage.snapshot.current_snapshot_ids` 에 있는 스냅샷 + 가장 최근 1개** 를 남기고 나머지만 삭제), 주석 갱신
- Test: `bash -n`, 로컬 임시 디렉터리 dry-run 출력 확인
- 게이트: 09-20 04:30 gc 로그에 `_tmp/doc: keep snap_20260918T232444Z`.

#### Task 4.4 — B05·D05·C01 deploy.sh 가드 + 기록 [중]
- Modify: `scripts/deploy.sh`(`--apply` 전 검사: ① `git status --porcelain` 비어 있음 ② HEAD 가 `origin/main` 을 포함하거나 `--allow-branch <name>` 명시 ③ `uv run --project backend pytest database/tests -q` 통과(`--skip-tests` 로 생략 가능, 생략 사실을 기록) ④ 서버에 `~/quant-ledger/DEPLOYED.json` `{rev, branch, at_utc, by, tests: ok|skipped}` 기록 ⑤ `backend/src/backtest_engine/` → `_engine/backtest_engine/` 동기화(C05) ⑥ dry-run 출력 첫 줄에 되돌아갈 파일 수)
- Test: `bash -n`; 더러운 트리에서 `--apply` → 거부 메시지.

#### Task 4.5 — B04·B07·D09·C05 build_chain.sh [중]
- Modify: `scripts/build_chain.sh`(① dry-run 문구 실측값(62표·43~66분·29표·9~11분·keep=3) ② `contract_step` 을 catalog 뒤에, 실패는 `FAILED_SOFT` 로 warn(갈래 3 Task 3.10 훅) ③ 체인 성공 시 `data/stage/_READY.json`·`data/equity/_READY.json` `{date, basis, generated_at_utc, stage_builds|equity_builds}` 원자 기록 — 상목 SFTP 가 완료 신호로 읽는다(실패 시 갱신 안 함) ④ `gc_step` 주석 keep=3 ⑤ stage_step 앞에 `[ -x scripts/doc_prepass_daily.sh ] && step "doc prepass" bash scripts/doc_prepass_daily.sh "$SNAP"`(실패는 기록형 — 갈래 5 훅))
- Test: `bash -n`, `--dry-run` 출력 대조

#### Task 4.6 — D08·D10·B08 문서 정합 [하]
- Modify: `README.md`(크론 전체표 = 서버 실제 + 이번 변경, 상태표 "08:10 확정 빌드 포함", 백업 보존 "주 1회 최신 1세트"), `docs/DECISIONS_PENDING.md:633-644`(결정 9: 실측 스냅샷 18.5GB/판·keep 3·백업 1세트 로 **적용됨** 표기), `docs/plans/2026-09-11-daily-incremental-v2.md` 상태블록(09-17 전면 가동·21:20/21:45/23:30·완료 실측 22:41), `docs/TECH_DEBT.md`(연기 항목 B-26~: A08 저녁 락, B09/C10 공유 경계, B04 stage 롤백, E03 결정 대기, C06-2 EG5c 롤링 표본, E10)

### 갈래 5 · 문서층 증분
소유: `database/src/stage/{doc_prepass,parsers_doc,rules_doc,doc_checks}.py`, `database/src/stage/build.py` 의 `_load_file_source` 만(갈래 2 와 겹침 — **함수 하나만** 만지고 커밋 메시지에 명시), 신규 `database/scripts/doc_prepass_daily.sh`, `database/docs/DOC_DESIGN.md`, `database/tests/test_stage_doc_*.py`

#### Task 5.1 — F03 캐시 무결성 가드 [하 → 5.2 선행]
- Modify: `src/stage/doc_prepass.py`(`input_hash_for(db: Path) -> str` 공개 — `run()` 의 계산식 그대로), `src/stage/build.py:413-431 _load_file_source`(`summary["input_hash"] != input_hash_for(snap.dart_db)` 면 `RuntimeError("doc prepass cache is for a different document set …")`)
- Test: `tests/test_stage_doc_build.py`(다른 스냅샷 id 로 복사한 캐시 → 실패)

#### Task 5.2 — 증분 프리패스 `--base-snapshot` [상 F01 근본]
- Modify: `src/stage/doc_prepass.py`(`incremental(db, docs_dir, cache_root, snapshot_id, base_snapshot_id, workers)`: ① base 캐시 → 새 캐시로 `os.link` 하드링크(샤드 파일 단위; `summary.json` 은 복사 안 함) ② `cur = doc_store(zip_ok=1)` vs `base` 의 접수번호 집합 → `added`·`removed` ③ `added` 는 `_run_shard` 로 파싱해 해당 샤드 파일에 `_replace_rows(drop=removed∩샤드, add=…)` — **하드링크된 파일은 쓰기 전에 복사본으로 끊는다**(base 캐시 오염 금지) ④ `summarize_from_cache(..., input_hash=input_hash_for(db), n_missing=재계산)` 로 summary 기록(`repairs` 가 아니라 `increments: [{from, added, removed, at}]`) ⑤ CLI `--base-snapshot ID`), 캐시 포맷 불변(오늘 P0 전량 캐시와 호환)
- Test: `tests/test_stage_doc_prepass.py`(픽스처 3문서 base → 1추가·1삭제 증분 → summary tables 일치, base 캐시 파일 불변)
- 게이트(서버): `doc_prepass --snapshot-id <새> --base-snapshot snap_20260918T232444Z` 가 분 단위로 끝나고 `summary.status=ok`, 이어서 4표 빌드 G0~G8 pass.

#### Task 5.3 — `scripts/doc_prepass_daily.sh <snap>` [중]
- Create: `scripts/doc_prepass_daily.sh`(① `data/stage/_tmp/doc/*/summary.json` 중 `status=ok` 이고 가장 최근 것을 base 로 ② 없으면 `echo "no base cache — skipped"` rc 0(체인 계속, 4표는 종전대로 skip) ③ 있으면 `--base-snapshot` 증분, rc 전파, 로그 `logs/doc_prepass/<snap>.log`), 실행 권한
- 훅 지점(갈래 4 Task 4.5 ⑤)
- Test: `bash -n`, 빈 캐시 디렉터리에서 rc 0.

#### Task 5.4 — DOC_DESIGN §7·플랜 문서 [하]
- Modify: `docs/DOC_DESIGN.md`(§7 미결에 증분 설계·gc 보존 규약·F03 가드 추가), `docs/plans/2026-09-04-doc-stage-p1.md` 후속 상태

### 갈래 6 · workbench 어댑터
소유: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py`, `backend/tests/**`(해당 어댑터 테스트만)

#### Task 6.1 — C07 잠정 행 구분 [중]
- Modify: `_adapter.py:918-923`(SELECT 에 `basis` 추가; `basis <> 'krx'` 행은 bars 에서 제외하고 `n_provisional` 로 따로 센다 — `n_invalid` 와 분리; 로그·통계 노출), `dataset_profile` 소비부가 `coverage_to` 를 읽는 곳이 있으면 "잠정 행 제외 시 coverage_to = 마지막 krx 세션" 으로 보정
- Test: `backend/tests/...`(evening 행 1 + krx 행 2 픽스처 → bars 2, n_provisional 1, n_invalid 0)
- 실행: `uv run --project backend pytest backend/tests/<해당> -q`

## 4. 통합·배포·실전 게이트 (오케스트레이터)

- [ ] 갈래 6개 완료 보고 수신 → 각 브랜치 `git log`·diff 검토, 소유권 밖 파일 변경 없는지 확인
- [ ] `fix/pipeline-audit-2026-09-19` 에 순서대로 머지: stage → equity → daily → doc → ops → workbench(충돌은 `build_chain.sh`·`build.py` 훅 지점만 예상)
- [ ] G-A: `uv run --project backend pytest database/tests -q` + backend 어댑터 테스트 + `ruff check` 변경 파일
- [ ] G-E: `superpowers:code-reviewer` 1회 → 지적 반영
- [ ] 서버 P0 확인: 프리패스 완료(`logs/doc_prepass/full_*.log` summary ok) → `run_stage_all.sh snap_20260918T232444Z --basis morning stg_doc_meta stg_doc_section stg_doc_correction stg_doc_parse_log` → 4표 G0~G8 pass
- [ ] G-B: `deploy.sh`(dry-run) 목록 확인 → `deploy.sh --apply --allow-branch fix/pipeline-audit-2026-09-19` → 서버 md5 대조 → dry-run 체인 → import 스모크
- [ ] 서버 baseline 재측정(Task 2.2 `--only`) → `check_baseline_lock.py`
- [ ] 크론 갱신(워치독 23:30/10:00, 리다이렉트 3줄) — 백업 `logs/crontab_backup_20260919_audit.txt`
- [ ] G-C-1: 수동 `build_chain.sh morning --date 20260918`(≈65분) → health C1~C6 pass, equity 29/29(EG21·fin_std 게이트 포함), contract pass, `_READY.json` 2개, `latest_morning.json` 갱신
- [ ] 상목 안내 발송(사용자) — 잠정판 재수령·9/6 zip 폐기 + `_READY.json`·`basis` 열 설명
- [ ] PR 생성(`fix/pipeline-audit-2026-09-19` → main, PR #105 포함·대체 명시, 본문 pr-review.md 형식)
- [ ] G-C-2: 09-20 08:10 "건너뜀" · 09-20 04:30 gc 캐시 보존 · 09-21 저녁 체인 coverage 출력·증분 프리패스 분 단위·워치독 오탐 0 · 09-22 08:10 확정판 정상 → 상태 블록 갱신, 메모리 갱신

## 5. P0 서버 즉시 조치 (오케스트레이터, 코드 배포 전)

- [x] 프리패스 전량 재생성 시작(09-19 18:34 KST, `snap_20260918T232444Z`, workers 3, 로그 `logs/doc_prepass/full_snap_20260918T232444Z.log`)
- [ ] 크론: 워치독 시각 2줄 + 수집 체인 리다이렉트 3줄(배포와 함께)
- [ ] 프리패스 완료 뒤 4표 수동 빌드(§4)

## 6. 사용자 몫

1. 상목 안내 2건 발송(문구는 오케스트레이터가 준비).
2. PR 머지(#105 또는 이 PR).
3. ★ E03 v3 컨센서스 동결 유지 여부.
4. (선택) 공유 경계 축소(C10/B09) 설계 착수 여부.
