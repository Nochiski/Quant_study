# WICS 주간 섹터 원장 → stage → equity 플랜 (2026-09-20)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WICS 섹터 구성(L1 10·L2 28)을 매주 토요일 원장 `data/raw/wiseindex.db` 에 쌓고, 같은 날 08:10 확정 빌드가 stage `stg_wics_components` → equity `sector_snapshot` + 매크로 `v_sector(as_of)` 로 실어 Kael-alpha 가 업종중립화·섹터캡에 쓸 수 있게 한다. 단계마다 검증 게이트(§2)를 통과해야 다음으로 간다.

**Architecture:** 원장(append-only 원문 zlib, 키 = dt·sec_cd·collected_at) → 스냅샷 6 DB(`LEDGER_FILES` 편입) → stage blob 언네스트 1표(§1 예외 c, `_load_blob_source` 에 `select_sql` 옵션 추가로 ws_raw 컬럼 계약을 일반화) → equity 1표 + as-of 매크로(스냅샷일 ≤ as_of 최신 행 + `days_since_snapshot`). 백필은 하지 않는다(사용자 09-20) — 섹터 축은 2026-09-18 부터만 존재한다.

**Tech Stack:** Python 3.11 · sqlite3 · duckdb · bash · pytest(`uv run --project backend pytest database/tests -q`, CI 와 동일; 로컬 빠른 확인은 `uv run --no-project --python 3.11 --with pytest --with duckdb --with requests python -m pytest <파일> -q`).

**참조:** `docs/WICS_PROBE.md` §6~§8(엔드포인트·응답 구조·첫 스냅샷 실측·결정), 수집기 `src/wics_snapshot.py`(브랜치 `feat/wics-ledger`, 09-18 스냅샷 서버 저장 완료). 배포 규약은 `scripts/deploy.sh`(`--allow-branch feat/wics-ledger`, 깨끗한 트리·테스트 통과 가드, `DEPLOYED.json`). 기준 브랜치 = `fix/pipeline-audit-2026-09-19`(09-19 감사 배포 rev, 서버 정본) 위.

---

## 0. 상태 블록 (정본 — 작업 단위마다 갱신)

| 단계 | 상태 | 비고 |
|---|---|---|
| T0 첫 스냅샷 | **완료 09-20 09:00 KST** | dt=20260918, 38콜 200, 2,460종목, L1 검산 10/10, KRX 대비 89.1% (`WICS_PROBE` §8) |
| T1 원장 편입·주간 잡 | **완료 09-20 09:26 KST** | 커밋 `959506b`·`6d1170e`, **G-W1** CI 환경 `database/tests` 1,291 passed(ruff·pyright 0) → `deploy.sh --apply --allow-branch feat/wics-ledger` rev 6d1170e. **G-W2** ① dry-run rc 0 ② `--date 20260918` 멱등 38 skip rc 0 ③ `wics.integrity` PASS(38/38·L1 10/10)·`wics.fresh` 0일·`wics.coverage` 0.8907 ④ `LEDGER_FILES` 6 DB 실재 ⑤ 백업 DBS 에 wiseindex. 크론 2줄은 T4 에서 등록(예정) |
| T2 stage `stg_wics_components` | **완료 09-20 09:51 KST** (② 는 월요일 저녁 빌드 대기) | 커밋 `0af6e3c`·`1ce4d73`, **G-W1** 1,294 passed → 배포 rev 1ce4d73. **G-W3 ①** 스냅샷 `snap_20260920T004352Z`(6 DB, wiseindex 220 KB) 단일 표 빌드 ok: **4,920행**(L2 2,460 + L1 2,460), reject 0, G0~G4·G6~G8 pass, G4 픽스처 6/6, G5 skip(no_baseline — 새 표라 회귀 기준 없음, 첫 실전 판 뒤 baseline 등재 여부 T5 에서 판단), `max_available_date` 2026-09-18. ② C1~C6 은 09-21(월) 21:20 잠정 빌드·09-22 08:10 확정 빌드에서 잰다(09-21 08:10 은 완료 가드로 건너뜀) |
| T3 equity `sector_snapshot` + `v_sector` | **완료 09-20 10:12 KST** (⑤ 는 자동 빌드 대기) | 커밋 `770ad9f`, **G-W1** 1,298 passed → 배포 rev 770ad9f. **G-W4** ① 단일 빌드 ok 2,460행, EG0·EG7·EG1·EG2·EG3·EG3_sector_snapshot·EG4(5/5) pass, EG5a skip(no_previous_build) ② `equity catalog` ok: 매크로 9(+`v_sector`)·표 30, EG11 pass, **EG5c pass n_diff 0**(재승인 불필요) ③ `v_sector('2026-09-18')` 2,460행 경과 0 · `'2026-09-22'` 2,460행 경과 4 · `'2026-09-17'` 0행, L1 분포 = 원장(G45 677 …) ④ **보류** — `dataset_profile` 등재는 `rules_s19.SOURCE_TABLES` 고정 목록 때문에 미룸(TECH_DEBT B-39, coverage_daily 와 같은 사정) ⑤ 30표 확정 빌드는 09-22(화) 08:10 에서 잰다(09-21 저녁 잠정 빌드가 첫 e1.17.0 판 — 29표 EG5a `rules_changed` skip 은 정상) |
| T4 배포·크론 | **완료 09-20 10:15 KST** | 배포는 T1~T3 마다 `deploy.sh --apply --allow-branch feat/wics-ledger`(최종 rev 770ad9f, tests 1,298 ok). 크론 2줄 등록(`0 18 * * 5` 토 03:00 · `0 1 * * 6` 토 10:00 재시도, 백업 `logs/crontab_backup_*`). README 크론표 가동. PR 은 감사 브랜치 PR 뒤 |
| T5 실전 게이트 | 진행 중 | 첫 관측: 09-21(월) 21:20 잠정 빌드(stage 63표 + equity 30표, C1~C6 · EG5a rules_changed) → 09-22(화) 08:10 확정 → 09-26(토) 03:00 첫 자동 스냅샷 → 10-03(토) 2회째 |

---

## 1. 결정·범위

- **주기·시각**: 매주 **토요일 03:00 KST**(크론 `0 18 * * 5` UTC). 금요일 잠정 빌드(≈22:40) 뒤 서버 유휴, 03:30 주간 백업이 바로 뒤라 그 주 백업에 포함되고, 08:10 금요일 확정 빌드가 같은 스냅샷을 stage·equity 에 싣는다. **dt = 직전 거래일(금요일)** — 휴장일 dt 는 CNT=0 이므로(§8 실측) `daily.calendar.prev_trading_day` 로 유도한다. 전부 빈 응답이면(구성 미공표) **10:00 KST 재시도 크론**(`0 1 * * 6`, 멱등)이 한 번 더 돈다.
- **원장**: `data/raw/wiseindex.db`. `wisereport.db` 와 분리 — 소스 도메인(기업 리포트 스크래퍼 vs 지수 구성 API)·런 로그·건전성·백업 단위가 다르다(WICS_PROBE §7-2).
- **콜**: 스냅샷당 L2 28 + L1 10 = **38콜**, 1콜/초. 비공식 API — 3회 연속 실패 즉시 중단.
- **백필 없음**(사용자 09-20): 2026-09-18 이전 섹터 축은 없다. 파일럿 13회분(L2 26개 기준)도 넣지 않는다.
- **범위 밖**: 라이선스 판단(사용자), KRX 업종분류(MS-09), Kael-alpha 소비 코드, `E04 실질 유통비율` 의 유동주식수 교체(후속 TECH_DEBT).

---

## 2. 검증 게이트

| 게이트 | 언제 | 통과 기준 | 측정 |
|---|---|---|---|
| **G-W1 단위** | 각 태스크 커밋 전 | 새 테스트 + 기존 `database/tests` 전부 green(로컬 pyarrow 환경 기존 실패 1건 `test_equity_s07_contract` 제외), `uvx ruff check` 0, pyright 0 | pytest 요약 줄, 커밋 메시지에 테스트 수 |
| **G-W2 원장** | T1 뒤 | ① `wics_weekly.sh --dry-run` rc 0 ② `wics_weekly.sh --date 20260918` 이 38/38 `skip_exists`(멱등) ③ `ledger_health --date 20260918` 에 `wics.integrity` PASS(38/38·L1 10/10)·`wics.fresh` PASS·`wics.coverage` ≥ 0.85 ④ 스냅샷 6 DB(`stage.snapshot.make_snapshot` 결과에 `wiseindex`) ⑤ `backup_raw.sh --dry-run` 목록에 wiseindex | 서버 로그 `logs/wics_weekly_*.log`, `logs/health/20260918.json` |
| **G-W3 stage** | T2 뒤 | ① 서버 단일 표 빌드 `run_stage_all.sh <snap> --basis morning stg_wics_components` ok, 행수 = Σ CNT(2026-09-18: L2 2,460 + L1 2,460 = **4,920**), reject 0, G1~G9 pass, G4 fixture(005930 → `G4530 반도체와반도체장비`) ② 다음 확정 빌드 건전성 **C1~C6 6/6**(신선도 14일) ③ 평일 저녁·아침 빌드에서 C6 오탐 0(dt 가 7일 묵어도 PASS) | `logs/stage_all/summary.tsv`, `logs/health/stage_<D>_morning.json` |
| **G-W4 equity** | T3 뒤 | ① 서버 `sector_snapshot` 빌드 ok, EG0·EG1(격자 = L2 행 distinct(ticker, dt))·EG2·EG3_sector_snapshot(L2 중복 0·L1만 있는 종목 0·어휘 폐쇄) pass ② `equity catalog` ok — EG5a `rules_changed` skip 1회는 정상, **EG5c n_diff 0**(as-of 뷰 무변경) ③ `v_sector(DATE '2026-09-18')` 2,460행 `days_since_snapshot` 0, `v_sector(DATE '2026-09-22')` 2,460행 3(거래일이 아니라 캘린더일) ④ `factor_readiness` 에 `sector.*` 4필드 ready, `first_usable_date` 2026-09-18 ⑤ 확정 빌드 전체 30표 rc 0 | `logs/equity/rebuild_*/sector_snapshot.log`, `_catalog_meta.json` |
| **G-W5 실전** | T4 뒤 2주 | 토요일 **2회 연속**(09-26·10-03) 03:00 잡 rc 0·알림 info·재시도 미발동, 08:10 확정 빌드에 새 스냅샷 반영(`sector_snapshot` max snapshot_date = 금요일), 03:30 백업 세트에 `wiseindex.db`, 평일 C6 오탐 0, 수동 개입 0 | 워치독·일일 리포트·`logs/backup_raw.log` |

게이트에 걸리면 다음 태스크로 넘어가지 않고 원인을 고친 뒤 같은 게이트를 다시 잰다. 재시도·완화는 이 문서 §0 에 사유와 함께 적는다.

---

## 3. 파일 구조

| 파일 | 역할 | 태스크 |
|---|---|---|
| `src/wics_snapshot.py` (수정) | `have_ok` 를 `n_rows > 0` 로(빈 응답은 재시도 대상), 요약에 `n_empty` | T1 |
| `scripts/wics_weekly.sh` (신규) | raw 락 → dt=직전 거래일 → 수집 → 전부 빈 응답이면 rc 4(재시도 크론용) → 알림 | T1 |
| `src/stage/rules.py` (수정) | `LEDGER_FILES["wiseindex"] = "wiseindex.db"`, `_MODULES` 에 `rules_wics` | T1·T2 |
| `scripts/backup_raw.sh` (수정) | `DBS` 에 `wiseindex` | T1 |
| `src/daily/ledger_health.py` (수정) | `Paths.wiseindex`, `check_wics`, `--skip wics` | T1 |
| `src/stage/model.py` (수정) | `BlobSource.select_sql: str | None` — 기본은 ws_raw 계약 그대로 | T2 |
| `src/stage/build.py` (수정) | `_load_blob_source` 가 `select_sql` 이 있으면 그 SELECT 로 RawBlob 6튜플을 만든다 | T2 |
| `src/stage/parsers.py` (수정) | `parse_wics_components` + `PARSERS` 등재 | T2 |
| `src/stage/rules_wics.py` (신규) | `stg_wics_components` TableRule | T2 |
| `src/stage/freshness.py` (수정) | `ALLOW_DAYS["stg_wics_components"] = 14` | T2 |
| `scripts/run_stage_all.sh` (수정) | `ORDER` 에 `stg_wics_components`(`stg_wise_coverage` 뒤) | T2 |
| `src/equity/rules_s25.py`·`sql/sector_snapshot.sql` (신규) | S25 `sector_snapshot` + EG3_sector_snapshot + 필드 프로파일 4 | T3 |
| `src/equity/views.py`·`catalog.py` (수정) | `v_sector(as_of)` 매크로·시그니처·입력·의존 등재 | T3 |
| `src/equity/model.py` (수정) | `RULES_VERSION = "e1.17.0"` | T3 |
| `scripts/equity_order.txt` (수정) | `sector_snapshot`(`security_span` 뒤, 상류는 stage 뿐) | T3 |
| `tests/test_wics_snapshot.py`·`test_daily_health.py`·`test_stage_wics.py`(신규)·`test_equity_s25_sector.py`(신규) | 게이트 G-W1 | T1~T3 |
| `README.md` 크론표 · `docs/STAGE_DESIGN.md` 표 목록 · `docs/STAGE_SPEC.md` §2-22 · `docs/EQUITY_DESIGN.md` §4-1 · `docs/EQUITY_GATES.md` · `docs/EQUITY_FIELD_MAP.md` | 문서 | T1~T3 |

---

## 4. 태스크

### T1. 원장 편입·주간 잡

**Files:** `src/wics_snapshot.py`, `scripts/wics_weekly.sh`, `src/stage/rules.py`, `scripts/backup_raw.sh`, `src/daily/ledger_health.py`, `tests/test_wics_snapshot.py`, `tests/test_daily_health.py`, `README.md`

- [ ] 1. `tests/test_wics_snapshot.py` 에 실패 테스트: 200 이지만 `list` 가 빈 코드는 `have_ok` 가 False → 재실행 시 다시 부른다(`status == "empty"`), `report()` 가 `empty=N` 을 낸다.
- [ ] 2. `src/wics_snapshot.py`: `have_ok` 를 `n_rows > 0` 조건으로, `run()` 반환에 `status: "empty"`(CNT=0) 분리, `main()` 이 전부 empty 면 rc 4.
- [ ] 3. 테스트 통과 확인 → 커밋 `wics: empty responses are retried, rc 4 when all empty`.
- [ ] 4. `src/stage/rules.py` `LEDGER_FILES` 에 `"wiseindex": "wiseindex.db"`. `tests/test_stage_snapshot*.py` 의 DB 수 단언이 있으면 6 으로. `scripts/backup_raw.sh` `DBS` 에 `wiseindex`.
- [ ] 5. `tests/test_daily_health.py` 에 실패 테스트 3건: `wics.integrity`(최신 dt 38/38 200 + L1 10/10 → PASS, L1 불일치 → FAIL), `wics.fresh`(D − 최신 dt ≤ 9일 PASS, 초과 WARN), `wics.coverage`(KRX D 종목 대비 ≥ 0.85 PASS, 미만 WARN), 원장 파일 없으면 SKIP(사유).
- [ ] 6. `src/daily/ledger_health.py`: `Paths.wiseindex`(기본 `data/raw/wiseindex.db`), `check_wics(con, krx, d)`, `run()` 에서 `wics` 소스 추가, `--skip` 어휘에 `wics`.
- [ ] 7. `scripts/wics_weekly.sh` 신규(`daily_ledger.sh` 골격: raw 락·kst()·step()·notify): `--date`·`--dry-run`, dt 산출은 `calendar.prev_trading_day(오늘 KST)`, `python -m wics_snapshot --dt D --codes all`, rc 0 → info "WICS 주간 스냅샷 dt=D 38/38 …", rc 4 → warn "전부 빈 응답 — 10:00 재시도", 그 외 → crit. 로그 `logs/wics_weekly_<D>.log`.
- [ ] 8. README 크론표에 두 줄(토 03:00·10:00 재시도) 예정 상태로. `bash -n`, ruff, pyright.
- [ ] 9. 전체 테스트 → 커밋 `feat(wics): weekly ledger job, LEDGER_FILES/backup/health wiring`. **G-W1.**
- [ ] 10. 서버(배포 전 검증, `logs/` 사본으로): `wics_weekly.sh --dry-run`, `--date 20260918`(멱등 38 skip), `ledger_health --date 20260918`(wics 3항목) → **G-W2** 기록.

### T2. stage `stg_wics_components`

**Files:** `src/stage/model.py`, `src/stage/build.py`, `src/stage/parsers.py`, `src/stage/rules_wics.py`, `src/stage/rules.py`, `src/stage/freshness.py`, `scripts/run_stage_all.sh`, `tests/test_stage_wics.py`, `docs/STAGE_DESIGN.md`, `docs/STAGE_SPEC.md`

- [ ] 1. `tests/test_stage_wics.py` 실패 테스트: 가짜 `wiseindex.db`(`wics_raw` 에 L1 `G45` 1 blob + L2 `G4530`·`G4535` 2 blob, 종목 겹침) → 빌드 ok, 행수 = Σ CNT, 키 (date, req_sec_cd, ticker) 유일, L1 행과 L2 행이 같은 종목에 공존, `idx_nm` 이 L2 라벨, `float_mktcap_krw = MKT_VAL × 1e6`, G8 파싱 등식, `max_available_date = dt`.
- [ ] 2. `src/stage/model.py` `BlobSource` 에 `select_sql: str | None = None`(RawBlob 6컬럼 별칭 `cmp_cd, ep, pkey, fetched_date, body, fetched_at` 를 내는 SELECT). `src/stage/build.py` `_load_blob_source`: `select_sql` 이 있으면 그것을 쓰고 `eps` 필터는 SELECT 안에서 처리.
- [ ] 3. `src/stage/parsers.py` `parse_wics_components`: blob 마다 `list` 를 풀어 컬럼 `req_sec_cd`(=cmp_cd 입력), `dt`(=pkey), `cmp_cd`, `cmp_kor`, `idx_cd`, `idx_nm`, `sec_cd`, `sec_nm`, `mkt_val`, `all_mkt_val`, `wgt`, `s_wgt`, `apt_shr_cnt`, `top60`, `cal_wgt`, `collected_at`(=fetched_at). 메트릭 `n_blobs`·`n_empty`·`n_failed`·`sum_cnt`(G8: Σ info.CNT == 행수).
- [ ] 4. `src/stage/rules_wics.py`: `TableRule(name="stg_wics_components", sources=(SourceRef("wiseindex","wics_raw","wics_raw"),), blob_source=BlobSource("wiseindex","wics_raw",("GetIndexComponets",),"parse_wics_components", required_columns=("dt","sec_cd","collected_at","http_status","n_rows","body"), select_sql="SELECT sec_cd AS cmp_cd, \'GetIndexComponets\' AS ep, dt AS pkey, dt AS fetched_date, body, collected_at AS fetched_at FROM wics_raw WHERE http_status = 200"), natural_key=("date","req_sec_cd","ticker"), partition_class="date_axis", partition_expr="substr(dt,1,4)", partition_src="dt", observed_src="collected_at", available=AvailableRule("column", column="date"), lag_known=False, write_mode="append_only", key_unique=True, ...)` — 컬럼 규칙: `ticker`(cmp_cd, 6자리 key), `date`(dt YYYYMMDD→DATE key), `req_sec_cd` key, `idx_cd`·`idx_nm`·`sec_cd`·`sec_nm` 텍스트, `float_mktcap_krw`(mkt_val 백만원 ×1e6), `all_float_mktcap_krw`, `wgt_pct`·`s_wgt_pct`, `float_shares_shr`(apt_shr_cnt), `top60`·`cal_wgt` 원문, `cmp_kor_current`. `rules.py` `_MODULES` 에 추가.
- [ ] 5. `src/stage/freshness.py` `ALLOW_DAYS["stg_wics_components"] = 14`(주 1회 + 연휴). `scripts/run_stage_all.sh` `ORDER` 에 추가.
- [ ] 6. 테스트 통과 → 기존 stage 전체 테스트 → 커밋. **G-W1.**
- [ ] 7. 서버 fixture `data/stage/fixtures/stg_wics_components.json`(005930/2026-09-18/G4530 → `idx_nm`='WICS 반도체와반도체장비', `float_mktcap_krw` 실측값) 작성. baseline 항목 추가 방법 확인(`python -m stage.baseline` 이 표별 metric 을 재계산하는지 — 새 표만 추가하는 경로가 없으면 표 없는 metric 은 `None` 으로 회귀 판정 skip 인지 확인해 기록).
- [ ] 8. 서버 단일 표 수동 빌드(스냅샷 새로 뜸, 빌드 락) → 다음 확정 빌드에서 C1~C6 → **G-W3** 기록.

### T3. equity `sector_snapshot` + `v_sector`

**Files:** `src/equity/rules_s25.py`, `src/equity/sql/sector_snapshot.sql`, `src/equity/views.py`, `src/equity/catalog.py`, `src/equity/model.py`, `scripts/equity_order.txt`, `tests/test_equity_s25_sector.py`, `docs/EQUITY_DESIGN.md`, `docs/EQUITY_GATES.md`, `docs/EQUITY_FIELD_MAP.md`

- [ ] 1. `tests/test_equity_s25_sector.py` 실패 테스트(S24 테스트 골격): stage 픽스처 → 빌드 전 게이트 pass, grain (ticker, snapshot_date) 유일, L2 행만 격자(L1 행은 검산축), `wics_l1_cd` ∈ 10코드, `v_sector(as_of)` 가 스냅샷일 ≤ as_of 최신 행 + `days_since_snapshot`, 스냅샷 전 날짜는 0행.
- [ ] 2. `sql/sector_snapshot.sql`: `SELECT ticker, date AS snapshot_date, sec_cd AS wics_l1_cd, sec_nm AS wics_l1_nm, req_sec_cd AS wics_l2_cd, idx_nm AS wics_l2_nm, float_shares_shr, float_mktcap_krw, wgt_pct AS wgt_in_l2_pct, date AS available_date, \'convention\' AS available_basis FROM stg_wics_components WHERE length(req_sec_cd) = 5`.
- [ ] 3. `rules_s25.py`: `EquityTable(name="sector_snapshot", grain=("ticker","snapshot_date"), inputs=("stg_wics_components",), partition_class="date_axis", partition_key_expr="year(snapshot_date)", available_basis=("convention",), content_date_column="snapshot_date", eg1: 좌변 out 행수 = 우변 stage L2 행 distinct(ticker,date), extra_gates=EG3_sector_snapshot{n_l2_dup, n_l1_only(L1 행에 있고 L2 행에 없는 (ticker,date)), n_l1_outside_vocab}, field_profiles: sector.wics_l1(code), sector.wics_l2(code), sector.float_shares(주), sector.float_mktcap(원) — frequency weekly, recommended_lag_days 1, point_in_time True(스냅샷일 기준))`.
- [ ] 4. `views.py` `v_sector(as_of)`: `SELECT s.*, datediff(\'day\', s.snapshot_date, as_of) AS days_since_snapshot FROM sector_snapshot s QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) = 1 WHERE snapshot_date <= as_of`; `MACRO_SIGNATURES/INPUTS/DEPENDS` 등재(ASOF_VIEWS 에는 넣지 않는다 — EG5c 표본 무변경). `catalog.py` 매크로 목록 반영.
- [ ] 5. `scripts/equity_order.txt` 에 `sector_snapshot`, `model.RULES_VERSION = "e1.17.0"`, `factor_readiness`(S20) 가 `sector.*` 를 ready 로 내도록 필드 등재 확인.
- [ ] 6. 테스트 통과 → equity 전체 테스트 → 문서 3건 → 커밋. **G-W1.**
- [ ] 7. 서버: 수동 `run_equity.sh sector_snapshot` + `equity catalog`(EG5a skip·EG5c 0 확인) + `v_sector` 두 날짜 질의 → 확정 빌드 30표 → **G-W4** 기록.

### T4. 배포·크론 (G-B)

- [ ] 1. `git fetch`; 기준 브랜치가 움직였으면 `feat/wics-ledger` 를 그 위로 리베이스. 전체 테스트(CI 명령) green.
- [ ] 2. `scripts/deploy.sh --apply --allow-branch feat/wics-ledger` → `DEPLOYED.json` rev 확인, 체크섬 동일.
- [ ] 3. 크론 2줄 등록(백업 파일 `logs/crontab_backup_<ts>.txt`): `0 18 * * 5 … scripts/wics_weekly.sh >> logs/cron_wics_weekly.log 2>&1` · `0 1 * * 6 … scripts/wics_weekly.sh --retry >> …`(재시도는 rc 4 였을 때만 실제 콜). README 크론표 "가동".
- [ ] 4. 플랜 §0·`docs/WICS_PROBE.md` §8 갱신, PR(기준 브랜치 대상 또는 그쪽 PR 뒤 main) 생성.

### T5. 실전 게이트 (G-W5)

- [ ] 09-26(토)·10-03(토) 결과를 §0 에 적고, 통과면 플랜 종료. 미통과면 원인·수정·재측정. 후속 TECH_DEBT: `E04 실질 유통비율` 분모를 `sector_snapshot.float_shares_shr` 로(현 DART 축), KRX 업종분류(MS-09) 별도 축, Kael-alpha `v_sector` 소비 규약.

---

## 4b. 조용한 실패 점검 (2026-09-20 11:45, 사용자 "조용한 실패가 있으면 안됨")

| 경로 | 전 | 후 |
|---|---|---|
| `wics_weekly.sh` 종료 코드 | `{ … } \| tee` 파이프 안에서 RC 가 서브셸에 갇혀 **항상 0 → 실패해도 info "완료"** | RC 를 파일로 꺼낸다. 실측: 휴장일 dt 로 rc 4 → warn, `--retry` 로 rc 4 → **crit** |
| 빈 응답 일부(예 33/38) | rc 0 → info | rc 4 → warn + 10:00 재시도(빈 코드만 재호출), 재시도 뒤에도 비면 crit |
| L1 검산 불일치(Σ L2 ≠ L1) | 출력만 | 전 코드를 받은 런이면 rc 5 → crit(부분 런은 원장 건전성 `wics.integrity` REQUIRED 가 DB 로 판정) |
| 크론 자체가 안 돎 | 알림 없음(9일 뒤 `wics.fresh` WARN, 14일 뒤 C6 crit) | **토 11:30 `watchdog.sh wics_weekly`** — 금요일 dt 38코드(행>0) 없으면 crit(크론 `30 2 * * 6`, 09-20 등록) |
| 아침 체인 | — | `wics.integrity` REQUIRED(38/38·L1 10/10) → 08:10 daily_build crit · stage C6 14일 · equity EG3_sector_snapshot |

배포 rev 00ba456(tests 1,299). 서버 실측 09-20 11:40: ① `watchdog wics_weekly` info(09-18 38/38) ② `--date 20260920` rc 4 warn ③ `--retry --date 20260920` rc 4 crit — 텔레그램 3건은 검증용.

## 5. 리스크·완화

| 리스크 | 완화 |
|---|---|
| 토 03:00 에 금요일 구성이 아직 안 나와 빈 응답 | rc 4 + 10:00 재시도(멱등). 그래도 비면 crit → 수동. 2주 실측 뒤 시각 조정 |
| blob 로더 일반화가 core(`build.py`)를 건드림 | `select_sql=None` 이면 기존 경로 그대로(회귀 0), stage 전체 테스트 + 서버 단일 표 빌드 게이트 |
| 일일 건전성이 주간 축을 매일 FAIL 로 봄 | `wics.fresh`·`coverage` 는 WARN, `integrity` 만 REQUIRED(최신 스냅샷 기준). C6 는 14일 |
| 기준 브랜치(감사) 이동·배포 순서 | 배포 직전 리베이스, `feat/wics-ledger` 는 기준 브랜치의 상위집합만 배포 |
| EG5c 재승인 오탐 | `v_sector` 는 ASOF_VIEWS 밖 — 표본 변경 없음. e1.17.0 첫 빌드의 EG5a skip 은 정상 |
| 비공식 API 차단 | 3회 연속 실패 중단·UA 고정·1콜/초. 주 38콜이라 노출 최소 |
