# 층별 결함 수정 플랜 — DQ-7·DQ-8·DQ-9·DQ-10·DQ-11 (2026-09-26, 승인 대기)

> 모(母) 플랜: [`2026-09-24-v3-merge.md`](2026-09-24-v3-merge.md) §1-7 의 DQ-7~DQ-11. 09-26 저녁 결측 검토에서 나온
> 것들이며, 이 문서가 **수정 절차·검증 게이트의 정본**이다. 실행 모델은 모 플랜과 같다(오케스트레이터만
> 서버·배포·커밋, 구현 에이전트는 파일 집합 분리, 게이트는 실측 숫자로만 통과).

## 0. 한눈에

| 태스크 | 층 | 고치는 것 | 게이트 | 착수 |
|---|---|---|---|---|
| **T-A** capex 자산별 합 규칙 | equity `fin_std`(S12) | 유형자산 취득을 자산별로 나눠 적는 회사(FY2025 311사)에서 capex·FCF 가 NULL | GA1~GA6 | 승인 즉시. **M2 착수 전** 완료 권장(v4 퀄리티 입력) |
| **T-B** WISE 건전성 항등식·daily 모드 | 원장 수집 + `ledger_health` | 판정표 덮어쓰기 한 번에 전날 검사가 FAIL, daily 모드는 무커버 영구 스킵 | GB1~GB3 | T-A 와 병렬(파일 겹침 없음) |
| **T-C** 커버리지 유예 규칙 | 모델 층 `factor_inputs`(M2 T2.11) | 추정치가 사라진 종목을 즉시 빼지 않고 **유예 G 거래일** 뒤 제외, 그동안 플래그 | GC1~GC3 | M2 W1(코드는 M2 에서, **규칙은 여기서 확정**) |
| **T-D** 기록만 | stage 주석 · compat | DQ-10 라벨 신뢰 구간, DQ-11 비12월 결산은 모델 층 `freq` 판정 | 없음(문서) | T-A 커밋에 동승 |

**전체 종료 게이트 G-LF**: ① 전체 테스트 통과(기준 1,385 + 신규) ② 서버 `fin_std` 재빌드 EG 전부 pass ③ 09-23 그림자 재수출에서
`qual_fcf_assets` NULL 34 → **≤ 4** 이고 G-M2 지표(quality Spearman ≥ 0.97)가 유지 ④ 09-28 정규 저녁 수집 뒤 `ledger_health --date 20260928`
wise 5검사 pass ⑤ 모 플랜 §1-7 DQ-7~11 조치 열이 결과 숫자로 갱신.

---

## 1. T-A — capex 자산별 합 규칙 (DQ-8)

### 1-1. 사실(09-26 실측, `logs/probe_capex*.py`)

- DART 현금흐름표에서 유형자산 취득은 (a) 집계 한 줄 `ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`(2,218사)
  또는 (b) 자산별 줄(토지·건물·구축물·기계장치·차량운반구·비품·집기·건설중인자산·기타유형자산, `dart_PurchaseOf*` 9종)
  + 비표준 이름(시설장치·공구와기구·금형·건물부속설비 '…의 취득', `account_id='-표준계정코드 미사용-'`)으로 적힌다. **같은 회사가 둘 다 적는 경우는 0건**.
- 현행 `src/fin_map.py` `capex_ytd` 는 (a) 와 이름 3종만 pick → (b) 회사는 NULL. FY2025 연간 2,631사 중 capex NULL **311**(cf_operating 은 있음 310),
  그중 자산별 합 산출 가능 **211**(+비표준 이름 4). 09-23 유니버스 580 의 `qual_fcf_assets` NULL 34 중 **32** 가 이것.
- 검증 기준선: 집계 줄이 있는 유니버스 587종목은 v3(네이버) capex 와 ±2% **572(97.4%)** 일치 → 네이버도 "유형자산 취득(PPE)" 정의.
  자산별 합(9종+이름)은 30종목 중 ±2% 17·±10% 7·그 밖 6(036800·020000 은 네이버가 무형·기타를 더한 것으로 보임, 080160 은 네이버가 작음) —
  네이버는 참조일 뿐 정본이 아니다. **정본 = "집계 줄이 있었다면 그 값"** 이고, 자산별 줄은 PPE 를 분할한 것이므로 합이 곧 집계다.

### 1-2. 설계

- `fin_map.py` 에 `CAPEX_FALLBACK = dict(basis="ppe_parts", sj=["CF"], concept=[9종], nm=[비표준 이름 목록])` 추가(REVENUE_FALLBACK 와 같은 꼴, `require` 없음).
  이름 목록은 스테이지 실측에서 고른다: `시설장치의 취득`·`공구와기구의 취득`·`공구기구의 취득`·`금형의 취득`·`건물부속설비의 취득`·`기타유형자산의 취득`·`비품의 취득`·`건설중인자산의 취득`(비표준 id 로 적힌 것만 잡힘 — 표준 id 는 concept 행이 잡는다).
  **제외**: 사용권자산(리스, `AdditionsToRightofuseAssets`·`PurchaseOfFinanceLeaseAssets`), 무형자산, 투자부동산 — 집계 줄 정의(PPE)와 맞춘다.
- `rules_s12.acct_rows()` 가 `capex_ytd` 에 tier `d_ppe_parts` 행을 **두 개**(kind concept · kind nm, 같은 tier 라벨, `agg='sum'`, basis `ppe_parts`) 붙인다.
  `.sql` 의 `best_tier` 가 `min(tier)` 라 집계 줄(a/b/c tier)이 하나라도 있으면 d 는 무시되고(이중계상 불가), 없을 때만 두 kind 의 hit 을 `sum` 한다(`val` CTE 는 tier 안에서 kind 를 가리지 않는다 — 09-26 확인, `fin_std.sql:159-190`).
- **`capex_basis` 컬럼 신설**(`standard` | `ppe_parts` | `unavailable`): `revenue_basis` 와 같은 자리(`fin_std.sql:328·414`)에 `max(basis) FILTER (WHERE metric='capex_ytd' AND v IS NOT NULL)`.
  이유: 합산 뒤에는 출처를 알 수 없고, 게이트(GA2)와 팩터층 기록이 이 열로 센다. 계약 변경이므로 `rules_s12.py` 필드 선언(`_fin` 블록, `:563` 근처)·게이트 어휘(`n_revenue_basis_outside_vocab` 의 capex 판)·`docs/EQUITY_FIELD_MAP.md`·`EQUITY_DESIGN.md §4-4`·`EQUITY_GATES.md` 동반 갱신.
- 부호는 종전대로 소비 측 abs()(compat `mappings.py:298-300`, 팩터층 동일). 합산 전 abs 는 하지 않는다(한 회사 안에서 부호는 일관, 실측).

### 1-3. 절차 (구현 에이전트 A · 파일 집합: `src/fin_map.py` · `src/equity/rules_s12.py` · `src/equity/sql/fin_std.sql` · `tests/test_equity_s12_fin.py` · `docs/EQUITY_*.md`)

1. **실패 테스트 먼저** — `tests/test_equity_s12_fin.py` 에 `make_stage_tree` 픽스처로 3사를 만든다:
   ① 자산별 표준 4줄(토지 100·기계 200·건설중 50·차량 0) + 비표준 `공구와기구의 취득` 30 → 기대 `capex_ytd = 380`, `capex_basis='ppe_parts'`
   ② 집계 줄 1,000 + 자산별 2줄 → 기대 1,000·`standard`(합 금지)
   ③ 사용권자산의 취득만 → 기대 NULL·`unavailable`.
   `uv run --project backend pytest database/tests/test_equity_s12_fin.py -q -k capex` → **FAIL** 확인.
2. `fin_map.py` CAPEX_FALLBACK 추가 → `rules_s12.acct_rows()` 확장(TIER 상수 `TIER_CAPEX_PARTS = "d_ppe_parts"`) → `fin_std.sql` `_acct` 블록을 `acct_values_sql()` 출력으로 교체(`test_sql_의_acct_블록은_생성기_문자열과_같다` 가 대조) → `capex_basis` 열·게이트 어휘 추가.
3. 위 테스트 PASS + 파일 전체(`test_equity_s12_fin.py` 절단본 빌드 EG 전부) PASS + `ruff check` 변경 파일. 커밋(오케스트레이터).
4. **서버**: `deploy.sh --apply --allow-branch feat/v3-merge` → `scripts/run_equity.sh fin_std`(m_ 판) → GA2·GA3·GA4 측정 스크립트 `logs/verify_dq8.sh`(아래) → 09-23 compat 재수출(`quant_20260923_v5.db`, `--consensus-asof 20260926`) + v3 엔진 → GA5·GA6.
5. 모 플랜 DQ-8 조치 열·§0 갱신, 메모리 갱신.

### 1-4. 게이트

| 게이트 | 판정 | 기준(실측 기준선) |
|---|---|---|
| **GA1** 단위 | 1-3 ①②③ 3건 + 기존 S12 테스트 전부 PASS | 회귀 0 |
| **GA2** 복구 규모 | 서버 `fin_std` FY2025 11011: `capex_ytd IS NULL AND cf_operating_ytd IS NOT NULL` 회사 수 | **310 → ≤ 100**, `capex_basis='ppe_parts'` 행 ≥ 205 |
| **GA3** 무회귀 | 재빌드 전후 `capex_ytd` 가 이미 있던 (corp_code, period_end, report_code, fs_div, vintage_kind) 행의 값 | **전건 동일**(0 diff), `capex_basis='standard'` |
| **GA4** 외부 참조 | ppe_parts 로 채워진 09-23 유니버스 종목(≈30) vs v3 네이버 capex | **±10% 이내 ≥ 80%**, 나머지는 건별 사유(네이버 정의 차이) 기록 — 정본이 아니므로 FAIL 조건이 아니라 기록 게이트 |
| **GA5** 하류 | 09-23 재수출 `score_history.qual_fcf_assets` NULL | **34 → ≤ 4** (나머지: 금융 1·연간행 없음 1·자산총계 없음 1) |
| **GA6** 동등성 유지 | `python -m model.compare` v3 vs 재수출: quality Spearman | **≥ 0.97**(현재 0.984; v3 는 네이버 capex 라 32종목이 움직임 — 값 기록) |

`logs/verify_dq8.sh` 초안: 재빌드 전 `fin_std` 현재 판을 `logs/fin_std_before.parquet` 로 복사 → 재빌드 → duckdb 로 GA2·GA3 → compat 재수출·엔진 → GA5 → compare → GA6. 서버 원오프, 커밋하지 않는다.

---

## 2. T-B — WISE 건전성 항등식·daily 모드 (DQ-9)

### 2-1. 사실

- `ledger_health.check_wise`(`src/daily/ledger_health.py:379-432`)는 `ws_coverage.checked_at` 이 그날인 행으로 covered·none 을 세어
  `covered×15 + none×4 == n_req` 와 `ws_raw` 기대치를 만든다. `ws_coverage` 는 `cmp_cd` PRIMARY KEY + INSERT OR REPLACE(`backfill_wise.py:66-69·353`) 라
  **최신 판정만** 남는다 → 09-26(토) 수동 full 뒤 `--date 20260923` 재판정이 covered 0·none 0 으로 FAIL(실측 `logs/health_probe/20260923.json`).
- `wise.run` 검사가 `mode == 'full'` 을 요구하므로 `--mode daily`(무커버 영구 스킵, `:256-259`)는 운영에서 쓸 수 없는 길이다. 크론 `daily_evening.sh:87-89` 는 full.

### 2-2. 설계

- 항등식의 입력을 **호출 원장 `ws_call_log`** 로 바꾼다(append-only, 이미 하루 단위 사실): 그날(KST) `covered = COUNT(DISTINCT cmp_cd WHERE ep='cF3002')`,
  `n_stocks_day = COUNT(DISTINCT cmp_cd WHERE ep='c1050001_data')`, `none = n_stocks_day − covered`. `ws_coverage` 는 더 이상 검사 입력이 아니다
  (표는 그대로 둔다 — 수집기의 최신 판정 캐시). `wise.cov_rate`·`wise.raw` 도 같은 수에서 유도.
- `--mode daily` 를 제거한다(choices `("full",)`, 기본 full). 크론 호출(`--mode full`)은 그대로 유효. 무커버 재프로브는 full 이 매일 하므로 별도 장치 불필요.
- **하지 않는 것**: `ws_coverage` 이력화(스키마 변경·마이그레이션 불필요해짐), 수집기 판정 논리 변경(정확했다).

### 2-3. 절차 (구현 에이전트 B · 파일 집합: `src/daily/ledger_health.py` · `tests/test_daily_health.py` · `src/backfill_wise.py`(main 만) · `tests/test_backfill_wise.py`)

1. **실패 테스트 먼저** — `tests/test_daily_health.py::_wise()` 픽스처에 `ws_call_log`(ts UTC, cmp_cd, ep, pkey, status, bytes, ms)를 항등식대로 채우는 인자를 추가하고,
   새 테스트 `test_wise_identity_survives_coverage_overwrite`: `ws_coverage.checked_at` 을 **다음날**로 덮어써도 D 의 `wise.req_identity`·`wise.raw` 가 PASS.
   현행 코드에서 FAIL 확인. `tests/test_backfill_wise.py::test_daily_mode_is_gone`: `--mode daily` 가 argparse 오류.
2. `check_wise` 를 2-2 대로 고친다(`ws_call_log` 없으면 종전 `ws_coverage` 폴백 유지 — 전환기 리포트 호환). `backfill_wise.main` 에서 daily 분기 삭제.
3. `tests/test_daily_health.py`·`test_backfill_wise.py` 전부 PASS, ruff. 커밋.
4. **서버**: 배포 → `cd src && python -m daily.ledger_health --date 20260923 --skip krx,kiwoom,kis,dart,wics --out logs/health_probe`(읽기 전용) → GB2.
5. 09-28(월) 정규 저녁 수집·09-29 아침 빌드 뒤 health 리포트 확인 → GB3.

### 2-4. 게이트

| 게이트 | 판정 | 기준 |
|---|---|---|
| **GB1** 단위 | 새 테스트 2건 + `test_daily_health.py`·`test_backfill_wise.py` 전부 PASS | 회귀 0 |
| **GB2** 과거일 재판정 | 서버 `--date 20260923` wise 검사 | `req_identity`·`raw` **FAIL → PASS**(covered 803·none 1,804·n_req 19,283 항등) |
| **GB3** 정규 운행 | 09-29 아침 `logs/health/20260928.json` | wise 5검사 pass, 크론 로그에 `--mode full` 정상 |

---

## 3. T-C — 커버리지 유예 규칙 (DQ-7, M2 T2.11 규칙 확정)

### 3-1. 사실(`logs/probe_grace.py`, 수집일 19일 09-01~09-26, 커버 ≈803)

| 지표 | 값 |
|---|---|
| 커버→무커버 에피소드 | 39 |
| 그중 복귀 | **2**(3일·5일 뒤), 진행 중 37 |
| 일평균 이탈 / 진입 | 2.2 / 1.2 종목 (0.3% / 0.15%) |
| 09-23 유니버스 중 현재 무커버 14종목의 경과 수집일 | 1일 2 · 2일 3 · 3일 2 · 6일 1 · 9일 2 · 11일 2 · 17일 2 |
| 유예 G=5 이면 | 유지 7 · 탈락 7 |
| 유예 G=10 이면 | 유지 10 · 탈락 4 |

복귀는 드물고(5%), 복귀할 때는 5수집일 안에 돌아왔다. 즉 **유예는 "복귀 대기"보다 "급작스런 이탈 완충 + 사람이 볼 시간"** 의 의미다.

### 3-2. 규칙 (결정 D-14 후보 — 사용자 확정 필요)

- 종목별 `coverage_state` ∈ {`fresh`, `grace`, `lapsed`}: `fresh` = 당해 12월기 op·ni 추정치 행의 `fetched_date` 가 판 기준 **마지막 수집일 D\***;
  `grace` = 마지막 fresh 가 D\* 로부터 **G 거래일 이내**; `lapsed` = 그 밖(유니버스 제외).
- **G 는 레지스트리 파라미터 `coverage_grace_days`, 기본 5 거래일**(= 주간 리포트 한 사이클, 복귀 실측 3·5일 포함). 10 은 "두 사이클" 대안으로 기록.
- `grace` 동안: 점수는 마지막 fresh 행(추정·재무)으로 종전대로 계산, 다만 **표시**에 `추정치 소멸 D+n` 플래그(일간 엑셀 `점수` 시트 열, 주간 후보 시트 `이탈 예정` 표기), 팩터 카드에 사유.
  복귀(`fresh` 재진입)하면 플래그 해제, 카운터 리셋. `lapsed` 는 판 메타 `n_lapsed_dropped` 로 기록.
- 재무 입력(cF3002/cF4002)은 커버가 끊기면 수집이 멈추므로 grace 동안 마지막 행을 쓴다(연간 스냅샷이라 G 안에서는 낡지 않음). 비12월 결산 판정은 `freq`(DQ-11).
- compat 그림자에는 적용하지 않는다(v3 미러, G-M2 동등성).

### 3-3. 게이트(M2 T2.11 에서 실행)

| 게이트 | 판정 | 기준 |
|---|---|---|
| **GC1** 단위 | 픽스처 4상태(fresh · grace n=1 · grace n=G · lapsed n=G+1) + 복귀 리셋 | 상태·플래그·`n_lapsed_dropped` 기대값 일치 |
| **GC2** 09-23 재현 | 09-23 판(D\*=09-23) 에 G=5 적용 | 14종목 중 grace 7·lapsed 7, 6종목 NULL 재무행 = 0(전부 lapsed 로 나감) |
| **GC3** 안정성 | 5거래일 연속 판에서 일일 멤버십 변동 | ≤ 1%(실측 0.45%), grace→fresh 복귀가 있으면 플래그 해제 확인 |

---

## 4. T-D — 기록만 (DQ-10 · DQ-11)

- `src/stage/rules_wise.py` `stg_fin_wise` 규칙 주석: "2026-09-26 이전 `fs_basis` 는 요청 파라미터(IFRSL) 를 따른 라벨이라 신뢰 불가, 값은 주재무제표(실측 141종목 값 동일). 09-26 이전 84종목 cF3002 는 NULL(복구 불가)".
- compat `mappings.py` `_FINANCIAL_SUMMARY_SQL` 주석: "period_type 은 v3 미러(mm=12). 비12월 결산 12종목은 quarter 로 들어간다 — 모델 층은 `freq` 로 판정(DQ-11)".
- T-A 커밋에 같이 태운다(문서·주석만, 테스트 없음).

---

## 5. 순서·병렬·롤백

- **W0(승인 직후)**: A ∥ B 동시 착수(파일 겹침 없음). T-D 는 A 가 맡는다.
- **W1**: A 커밋 → 서버 `fin_std` 재빌드(GA2·GA3) → compat 재수출(GA5·GA6). B 커밋 → 배포 → GB2. 두 배포는 한 번에(같은 브랜치).
- **W2(09-29 아침)**: GB3 확인, 모 플랜·메모리 갱신, G-LF 판정.
- **T-C** 는 이 문서 §3-2 를 모 플랜 T2.11 행에 링크하고 M2 W1 에서 코드화.
- 롤백: `fin_std` 는 판(v=) 단위라 이전 m_ 판으로 MANIFEST current 를 되돌리면 끝(값은 이미 검증 스크립트가 before 사본 보관). health 변경은 코드만(데이터 무변경).

## 6. 사용자 결정 필요

1. `capex_basis` 열 신설(계약 변경, 문서 3종 갱신) — 권장 **예**.
2. 유예 G 기본 **5 거래일**(대안 10) · 플래그 표기 방식(§3-2).
3. `--mode daily` 제거(건전성이 full 을 요구하므로 죽은 길) — 권장 **예**.

---

## 7. 실행 결과 (2026-09-26 20:10 KST, 배포 rev 17bc918d)

| 게이트 | 결과 | 비고 |
|---|---|---|
| GA1 | **PASS** | `test_equity_s12_fin.py` 39(+4 capex) · 관련 808 · 전체 1,391+ |
| GA2 | 복구 **203**(ppe_parts), 잔여 107 → 기준(≤100·≥205)에 각 7·2 미달, **잔여 전수 분류로 종결** | 잔여 107 = 집계 줄은 있으나 값 공란 8 · 유형자산+투자부동산 **합산 줄** 3 · PPE 줄 없이 리스·무형·소프트웨어 취득만 82 · '취득' 줄 자체 없음 14. 규칙(PPE 정의)으로 더 채울 것은 합산 줄 3(FY2025 전체 7사, 유니버스 024110·030200·000370·046890)뿐 → 후속 F-A1 |
| GA3 | **PASS** | 기존 capex 80,391행 값 전건 동일·basis 전부 standard, revenue·net_income·cf_operating 회귀 0 |
| GA4 | **PASS 81%**(±10% 25/31) | 그 밖 6 중 2 는 0 vs 0·1 vs 1(분모 효과), 4 는 네이버 정의 차이(006730·036800 무형 포함, 020000, 080160) |
| GA5 | **PASS** | `qual_fcf_assets` NULL 34 → **4**(030200·024110 합산 줄, 241560 비KRW 격리, 146320 영업현금흐름 이름 변형) |
| GA6 | **PASS** | v3 vs v5: composite 0.993 · quality 0.9855 · valuation 0.987 · v2 total 0.9825, verdict pass |
| GB1 | **PASS** | `test_daily_health.py`·`test_backfill_wise.py` 33, 전체 1,391 |
| GB2 | **PASS** | `--date 20260923` 재판정 OK(covered 805·none 1,802·19,283 항등, source=call_log) — 종전 FAIL |
| GB3 | 대기 | 09-28 정규 저녁 수집 → 09-29 아침 `logs/health/20260928.json` |
| EG5a | 절차 추가 | 규칙 변경엔 `model.RULES_VERSION` 인상이 필요(플랜 누락) → **e1.17.0 → e1.18.0**(17bc918d), 이후 `skip(rules_changed)` |

구현 중 발견·반영: 이름 목록을 `kind='nm'` 으로 두면 표준 태그 줄(예 `dart_PurchaseOfConstructionInProgress` = '건설중인자산의 취득')이 concept 줄과 nm 줄에 **둘 다** 걸려 같은 tier 에서 두 번 더해진다 → 새 kind `nm_nonstd`(`NOT account_std` 행만) 로 차단, 테스트 ①이 경계를 찍는다. `tests/test_equity_s19_profile.py` 선언 행수 83→84.

**후속(작음, 별도 승인)**
- **F-A1** 유형자산+투자부동산 합산 줄(7사, 유니버스 4): tier `e_ppe_combined`·basis `ppe_incl_invprop` 추가 여부 — 투자부동산이 섞이므로 정의가 다르다는 표시가 필요. KT(030200)가 여기 속한다.
- **F-A2** `cf_operating_ytd` 이름 목록에 공백 변형 '영업활동으로 인한 순현금흐름'(146320) — 이름 매칭에 공백 정규화(NFKC·공백 제거)를 넣는 편이 근본적.
- **F-A3** PPE 취득 줄이 없고 리스·무형만 있는 82사: capex 를 NULL 로 둘지 0 으로 볼지는 팩터층 결정(현재 NULL = FCF 결측). 서비스업 편향 여부 확인 뒤 결정.
- T-C(유예 규칙)는 M2 T2.11 에서 코드화.

---

## 8. 후속 F-A1~A4 실행 결과 (2026-09-26 22:30 KST, 배포 rev 1fafee3c, RULES_VERSION e1.20.0)

사용자 지시(09-26 21:00) "F-A1 F-A2 F-A3 다 진행해" → 구현 중 서버 재빌드가 결함 셋을 더 드러내 **F-A4** 로 함께 닫았다.

| 항목 | 규칙 | 결과 |
|---|---|---|
| F-A1 합산 줄 | tier `e_ppe_combined`(`유형자산및투자부동산의취득`·`투자부동산및유형자산의취득`, basis `ppe_incl_invprop`) | FY2025 3행. KT(030200) capex 35,965억·기업은행(024110) 1,861억 채워짐 |
| F-A2 이름 공백 | 모든 `nm`/`nm_nonstd` 매칭을 공백 제거 뒤 비교, `영업활동으로인한순현금흐름` 추가 | `cf_operating_ytd` NULL→값 501행, FY2025 결측 4→2. 부수로 매출원가 43·EPS 51·현금흐름 각 200~500행 등 다른 계정도 채워짐(값 변경 0) |
| F-A3 capex 0 | 현금흐름표는 있는데 유형자산 취득 줄(값 있는)이 없으면 0, basis `none_in_cf` | FY2025 104행. 현금흐름표 없는 그룹은 종전대로 NULL |
| **F-A4** (서버 실측 정정) | ① 자산별 합은 **크기(|값|)의 합**(`agg=sum_abs`) — 같은 회사가 표준 태그 줄은 양수, 비표준 줄은 음수로 적는다(00402989 FY2016) ② 비표준 이름 = 자산류 24종 × 접미어 2 = 48 완전일치 토큰(태그 없는 회사는 토지·건물·기계장치… 전부 이름으로 적는다, 00159971 FY2015) ③ 공백 정규화가 만든 pick 모호 2건 → **공백 없는 원문 우선**, 공백 변형은 exact 히트가 없을 때만 | 정규화 이전 판(110658) 대비 **값→NULL 0** · 표준 capex 80,391행 변경 0 · ppe_parts 변경 1,402행 전부 증가·감소 0 · 표본 00402989 83.13억·00159971 337.78억(손계산 일치) · 00364795 −29.812·00926522 0.083 복원 |
| GF3 | 09-23 유니버스 `qual_fcf_assets` NULL | **4 → 1**(241560 비KRW 격리만) |
| GF4 | v3 동등성(v7) | composite 0.993 · quality 0.9855 유지, verdict pass |
| EG5a | 규칙 변경마다 `RULES_VERSION` 인상 | e1.18.0 → e1.19.0 → **e1.20.0** |

FY2025 `capex_basis`: standard 2,320 · ppe_parts 203 · none_in_cf 104 · ppe_incl_invprop 3 · unavailable 1. capex NULL 이면서 영업현금흐름은 있는 회사 **310 → 1**.

주의(운영): equity 판 보관 한도(`--keep`) 때문에 T-A 이전 판(m_20260924T040605)이 지워져 회귀 기준을 정규화 이전 판(110658)으로 바꿨다 — 표준 capex 는 T-A 검증(GA3)에서 이미 p0 대비 0 diff 였으므로 추이적으로 성립. 검증 스크립트 `logs/verify_fa.sh`·`verify_fa2.sh`·`verify_fa2_reg.py`, 로그 `logs/verify_fa*.log`.

## 9. 예정 — T-E 스테이지 계정명 정규화 열 (2026-09-29 화, 사용자 09-27 확인)

`stg_fin` 에 원문 `account_nm` 은 그대로 두고 **`account_nm_norm`**(공백 제거) 열을 추가한다. 실측(09-27): 고유 계정명 72,488 → 공백 제거 55,489, 변형 군 11,765(관련 행 1,400만/1,540만), 특수 공백·NFKC 차이 0 → "공백 제거"만으로 충분.
절차: ① stage 규칙(`rules_dart.py` stg_fin)에 파생 열 + 스테이지 규칙 버전 인상(`test_stage_model` 2.3.0 고정 갱신) ② `fin_std.sql` 이 그 열을 입력으로 선언하고 자체 `regexp_replace` 제거(`nm_exact` 는 `account_nm = account_nm_norm` 그대로) ③ 절단본 픽스처 `stg_fin` 에 열 추가 ④ 저녁 정규 판에서 값 변경 0 확인(`verify_fa2_reg.py` 방식, 기준 = 09-28 판). 09-29 아침 GB3·5일째 그림자 통과 뒤 착수.

## 10. T-F 정정 안내 페이지 서식 드리프트 (2026-09-28 승인, 코드 오늘 · 서버 09-29)

**사실(09-28 실측, `logs/probe_corr*.py`)**: DART 정정 표지가 2025년부터 표 형식으로 바뀌어 최초제출일이 앵커 다음 줄로 내려가고 "3. 정정사유" 가 항목별 열이 됐다. `stg_doc_correction` 의 최초제출일 파싱 실패 2023 0.7% · 2024 2.4% → **2025 96.6% · 2026 99.1%**(정정대상·정정사유도 같이 NULL, 항목 표는 정상). 소비처 S11 `disclosure_version` 은 기간 라벨로 연결해 `candidate_status` 는 99.9% unique 그대로지만, 교차검증 `date_check='exact'` 가 2024 1,212 → 2025 44 → 2026 8 로 꺼졌다. 사다리 지표 `n_correction_filed_parsed` 는 기록형이라 20개월간 경보 없음.

**조치**: ① 파서(`parsers_doc.correction_page`)를 줄 배치에 무관하게(같은 줄이 비면 다음 줄, 정정사유는 항목 열에서 합성) 고치고 `PARSER_VERSION` p1.4 → p1.5 ② 프리패스에 접수번호 목록 재파싱 경로(전량 4.5h 대신 2,400건만) ③ S11 게이트에 최근 90일 파싱 성공률(≥50건에서 <0.5 FAIL, 기록형 비율) ④ B-40(1·3분기 판정 3개월 가정) TECH_DEBT 등재.

**게이트 GT-F**: (1) 파서 단위 테스트(2025 배치 2종·옛 배치 불변) (2) 서버: 미파싱 2,392건 재파싱 뒤 2025~2026 파싱 성공률 **≥ 95%**, 2024 이전 행 전건 불변 (3) `disclosure_version` 재빌드 뒤 2025~2026 `date_check` exact ≥ 90%(no_zip 제외), `candidate_status` 분포 불변 (4) 전체 테스트 통과. 서버 절차는 09-29 아침 GB3 뒤 T-E 와 함께.

**T-F 코드 완료(09-28, 커밋 아래) — 서버 절차(09-29 아침, GB3 뒤)**
```bash
cd /home/kael/quant-ledger; SNAP=<09-29 아침 확정 빌드의 snapshot-id>
# 1) 재파싱 대상 = 캐시에서 정정 표지가 있는 접수 전부(≈17,600) — 미파싱 2,392 만 골라도 되지만 parser p1.5 일관성을 위해 전부
.venv/bin/python -c "import json,pathlib,sys; r=pathlib.Path('data/stage/_tmp/doc/%s/stg_doc_correction'%sys.argv[1]); print('\n'.join(sorted({json.loads(l)['rcept_no'] for f in r.glob('year=*.jsonl') for l in f.open(encoding='utf-8') if l.strip()})))" $SNAP > /tmp/corr_$SNAP.txt
# 2) 표적 재파싱(≈30분): PYTHONPATH=src .venv/bin/python -m stage.doc_prepass --snapshot-id $SNAP --repair /tmp/corr_$SNAP.txt --workers 3
# 3) 문서층 4표: scripts/run_stage_all.sh $SNAP stg_doc_meta stg_doc_section stg_doc_correction stg_doc_parse_log
# 4) S11: scripts/run_equity.sh disclosure_version
```
GT-F(2) 의 "2024 이전 행 불변" 은 `filed_date`·`target_raw` 기준이다 — `reason_raw` 는 옛 서식에서도 비어 있던 행이 항목 열로 채워질 수 있다(NULL→값만 허용). 대조 사본 `logs/tf_before_corr.parquet`(17,618행, 판 m_20260924T035856). 후속: 서버 재파싱 뒤 절단본 픽스처 `tests/fixtures/stage_slice` 의 정정 6건(p1.4)을 다시 떠서 S11 최근 창 단언 3개·`DATE_CHECK` 갱신.

---

## 11. GPT 자문 채택분 T-G · T-H · T-I (2026-09-28 승인 "ㅇㅇ")

근거: [`../research/2026-09-28-gpt-data-quality-consult.md`](../research/2026-09-28-gpt-data-quality-consult.md) §2. 공통 원칙: **분모는 "만들어진 행"이 아니라 "예상 대상"**, 경보에는 영향 건수·대표 사례·최초 발생 판·이유를 같이.

### T-G 전 컬럼 diff 게이트 (지금, 코드만)
- `scripts/equity_diff.py`: 같은 equity 표의 두 판(`--before <build_id> --after <build_id>`, 기본 = 직전 커밋 판 vs 현재 판)을 grain 키로 FULL OUTER JOIN 해 컬럼별로 **값→NULL · NULL→값 · 값→다른 값(부동소수는 컬럼별 허용오차) · 행 추가 · 행 삭제 · basis/available_date 변경**을 세고, 대표 사례 3건(키·전·후)을 붙여 JSON+표로 낸다. 운영 메타(빌드 시각 등)는 비교에서 뺀다.
- **예상 변경 선언** `--expect <yaml>`: 컬럼·조건(예 `capex_ytd: null_to_value only where capex_basis in (ppe_parts, none_in_cf)`)·허용 방향을 적으면 그 밖의 변경은 `unexplained` 로 집계. `--gate` 면 unexplained 값→NULL·행 삭제·available_date 변경 > 0 이면 rc 2.
- EG5a 가 `skip(rules_changed)` 인 빌드에서 **신판 2회 빌드 결정성**(같은 스냅샷으로 두 번 지어 content_hash 동일)을 `scripts/run_equity.sh` 뒤에 붙일 수 있게 `--determinism` 옵션(두 build_id 의 파티션 해시 비교).
- 테스트: 절단본 두 판(픽스처 빌드 → 규칙 한 줄 바꿔 재빌드)으로 각 변경 유형이 정확히 집계되는지, expect 파일이 unexplained 를 0 으로 만드는지.
- 게이트 GT-G: 09-26 의 fin_std 세 판(110658·130415·132656)에 대해 손으로 낸 숫자(값→NULL 0·표준 capex 변경 0·ppe_parts 증가 1,402)와 **동일 결과**.

### T-H 예상 대상 기준 커버리지 (지금 1단계 = fin_std, M2 에서 2단계 = factor_inputs)
- 1단계 `rules_s12.py` EG3_fin_std 에 기록형 지표 `coverage_by_group`: 그룹 = `fs_div` × 템플릿(`req` CTE 의 has_interest_revenue → banking · has_investment_income → insurance · 그 밖 standard) × 보고서 종류, 지표 = revenue·op_profit·net_income·total_asset·cf_operating_ytd·capex_ytd. 분모 = 그룹의 **fin_std 채택 행 + 격리 행**(격리는 `_reject` 에서, 예상 대상 = 표준계정이 있는 stg_fin 그룹 = EG1 우변). 분자 = 값 있음. 직전 판의 같은 표와 비교해 **≥20 대상 그룹에서 유효 비율이 50%p 이상 떨어지거나 0 이 되면 FAIL**, 5%p 이상·5건 이상이면 기록형 warn. 대표 사례 3건 동반.
- NULL 사유 1단계: `capex_basis` 어휘에 이미 있는 `unavailable` 을 `no_cf_statement`(현금흐름표 없음) 와 `unmapped`(현금흐름표는 있는데 대응 실패) 로 나눈다 — F-A3 의 0 규칙 분모를 그대로 쓴다. revenue_basis 도 `unavailable` 을 같은 두 갈래로.
- 2단계(M2 T2.2b): factor_inputs 의 12개 모델 입력(VOL60·E/P·배당·OPM·FCF/A·M_PULL_C·리비전 4·신용·외국인)마다 예상 대상(유니버스) 대비 유효 건수와 사유 분포를 판 메타에 기록, 전부 NULL 인 입력이 있으면 판 FAIL.
- 게이트 GT-H: 09-23 판 재현에서 DQ-6 상황(금융 템플릿 순이익 0%)을 넣으면 FAIL 하고, 현재 판은 PASS 하며 은행·보험 그룹의 순이익 유효 비율이 표에 나온다.

### T-I 골든·절단본 픽스처의 형태 다양성 (T-F 서버 재파싱 뒤, 09-29~30)
- 절단본 `tests/fixtures/stage_slice` 에 이번 결함 원문 각 1건 추가: 별도 결산 회사(252990) · 금융 템플릿(003540 이미 있음 + 은행 1) · 분할 capex(00159971 형) · 부호 혼재(00402989 형) · 2025 정정 표지 1건 · 비12월 결산 1건. 슬라이스 스크립트가 있으면 그것으로, 없으면 `scripts/` 에 추출 스크립트를 두고 재현 가능하게.
- 골든 27건에 위 유형의 기대값 6건 추가(EG4). S11 최근 창 단언·`DATE_CHECK` 갱신(T-F 보고 항목).
- 게이트 GT-I: 픽스처 재구성 뒤 전체 테스트 통과, EG4 33건.

순서: T-G ∥ T-H(에이전트 둘, 파일 겹침 없음: T-G 는 `scripts/equity_diff.py`·`tests/test_equity_diff.py`·`docs/EQUITY_GATES.md` §diff, T-H 는 `src/equity/rules_s12.py`·`src/equity/sql/fin_std.sql`(basis 어휘)·`src/fin_map.py`·`tests/test_equity_s12_fin.py`·`docs/EQUITY_GATES.md` §S12) → 커밋 → 09-29 배포 묶음에 포함.

### T-E·T-G·T-H 코드 완료 (09-28 밤, 커밋 87450dfa·316a7b22·946dab0a·81d6c634)

| 태스크 | 산출 | 서버 반영 |
|---|---|---|
| T-E | `stg_fin.account_nm_norm` 파생 열(stage 2.3.0→2.4.0), `fin_std` 가 선언 입력으로 읽음. 절단본 재빌드 해시 동일 = 산출 불변 | **stage 먼저** `run_stage_all.sh <snap> stg_fin` → 그 뒤 equity. 순서 어기면 EG0 가 `stg_fin.account_nm_norm` 결측으로 FAIL |
| T-G | `scripts/equity_diff.py`(테스트 18) + expect 예시 3종(`docs/equity_diff/`) | 서버 `logs/equity_diff_expect_{th,tf}.json` 업로드 완료 |
| T-H | `EG8_fin_std` 커버리지 게이트 + basis 결측 사유 2분할(e1.21.0) | 첫 재빌드는 기록만(직전 판에 `coverage_by_group` 없음), 판정은 그다음 판부터 |

주의: stage 규칙 버전 인상 → 배포 뒤 **첫 판에서 stage health C4 가 전 표를 `rules_changed` 로 비교 생략**(두 번째 판부터 복귀). equity EG5a 는 매일 `skip(inputs_changed)` 이므로 값 불변 확인은 T-G diff 로 한다.

**09-29 실행 순서**(`logs/tuesday_runbook.sh <step>`): `gb3` → `shadow5` → 배포(`deploy.sh --apply --allow-branch feat/v3-merge`) → `stage_fin`(T-E) → `tf`(T-F 재파싱·문서층 4표) → `fin_std`(T-E·T-H 재빌드 + diff 게이트 + 2회 빌드 결정성) → `s11`(T-F, disclosure_version 재빌드 + diff) → `report`. 그 뒤 M1 종결 판정 → M2 W0 착수.

---

## 12. 09-29(화) 서버 실행 결과 — 배포 rev 593c6e9c (스테이지 2.4.0 · equity e1.21.0 · 파서 p1.5)

| 단계 | 결과 | 게이트 |
|---|---|---|
| 아침 확정 빌드(09-28) | 09:25 완료. 원장 건전성 26/26(WISE 항등식 source=call_log 19,261 일치), 스테이지 6/6 | **GB3 PASS** |
| 5일째 그림자(09-28) | composite 0.992 · 상위50 48 · momentum 0.997 · revision 0.987 · flow 0.995 · quality 0.985 · valuation 0.987 · v2 0.980 | G-M2 5/5 → **M1 완료** |
| 배포 | 테스트 1,447 통과, 바뀐 파일 12(src 8·scripts 4) | — |
| T-E `stg_fin` 재빌드(m_20260929T003116) | 15,387,792행 · 격리 0 · 규칙 2.4.0 · `account_nm_norm` 전 행 채움 · 고유명 72,489 → 55,490 · 21분 | PASS |
| T-F 재파싱(17,628건, p1.5) + 문서층 4표 | 캐시 ok(D0 0). 최초제출일 파싱 2023 99.6% · 2024 99.5% · **2025 99.8% · 2026 99.9%**(종전 3.4% · 0.9%). 2025 이전 행: 채움만(최초제출일 96 · 정정대상 134 · 정정사유 13,139), 소실 0, **정정대상 값 변경 4**(아래) | **GT-F(2) PASS** |
| T-E·T-H `fin_std` 재빌드(m_20260929T012907) | diff 게이트 rc 0: 변경은 basis 라벨 재명명 1,419건뿐(revenue_basis 717 · revenue_basis_prev 464 · capex_basis 238, 전부 설명됨), 숫자 컬럼 변경 0, 행 증감 0. 2회 빌드 해시 동일. EG8 첫 판 = 기록만 | **GT-G · GT-H PASS** |
| T-F `disclosure_version` 재빌드(b_20260929T013042) | diff 게이트 rc 0: 최초제출일 NULL→값 2,319 · date_check unparsed→판정 2,319 · 정정사유 NULL→값 15,369. 정확 일치(ZIP 있는 정정 대비) 2025 44 → 1,251(96.9%) · 2026 8 → 954(96.6%), 2024 96.5% 와 같은 수준. candidate_status 불변(unique 23,124 · none 94). 최근 90일 파싱률 363/364 = 99.7% | **GT-F(3) PASS** |

**작은 부작용 1건(후속, T-I 와 함께)**: 콜론이 다음 줄로 떨어진 옛 서식 4건(2013~2016)의 `target_raw` 가 `: 분기보고서` 처럼 콜론을 달고 나온다. `target_raw` 는 equity·모델 어디서도 안 쓴다(`disclosure_version.sql:14` 는 report_nm 을 쓴다). 수정 = `_next_value_line` 결과의 앞 콜론 제거 + 그 4건 `--repair`.

**운영 메모**: ① 확인 스크립트가 판을 이름순으로 고르면 `b_`(수동) < `m_`(아침) 이라 옛 판을 읽는다 — MANIFEST `current_build` 로 골라야 한다(오늘 1회 오판독, 재확인으로 정정). ② 오늘 저녁 빌드가 스테이지 2.4.0 첫 정규 판이라 C4 는 `rules_changed` 로 한 번 비교를 건너뛴다(예정된 비용). EG8_fin_std 는 오늘 판에 기록이 생겼으므로 저녁 판부터 판정한다.
