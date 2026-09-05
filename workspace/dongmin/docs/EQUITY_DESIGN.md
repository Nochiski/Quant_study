# Equity 층 설계 v1.1 (2026-09-03, 0단계 산출 — 적대적 검수 1회 반영)

> stage(사실) 위에 equity(구조 정책)를 쌓는 층의 **테이블·뷰·규칙·게이트 명세**. 워크플로우는 `EQUITY_WORKFLOW.md` v1.1, 인계 사실은 `STAGE_HANDOFF.md`, 실측 근거는 `STAGE_SPEC.md`·`STAGE_DESIGN.md` §10 과 이 문서 §10(0단계 프로브 P1~P11).
> 규칙 한 줄마다 근거를 병기한다. 근거 없는 수치는 적지 않는다. **stage 컬럼명은 `src/stage/rules_*.py` 의 `ColumnRule.name` 이 정본**이다. 결정 5개(§9)는 이 문서로 확정을 요청한다. v1 → v1.1 변경은 §12.

---

## 0. 범위와 한 문장

**"날짜 D에, 그 시점에 알 수 있던 정보만으로, 그날 실존한 전 종목의 상태를 줘."**

- 입력: 서버 `~/quant-ledger/data/stage/` 62테이블(읽기 전용, MANIFEST 경유). 원장 SQLite 는 읽지 않는다.
- 산출: `~/quant-ledger/data/equity/` parquet 테이블 26 + `equity.duckdb` 뷰 카탈로그. 소비자는 팩터층(뷰)과 백테스트 엔진(어댑터 3포트).
- 범위 밖: 팩터 값·통계 정규화·유니버스 정책 **적용**·일일 증분·문서층 L1·WICS 수집(`WICS_PROBE.md` 보류)·엔진 포트 확장.

---

## 1. 층 계약

허용 변환: stage 위에서 **조인 · 격자 · 판본 선택 · 파생(계수·정정 링크·정지 상태·무거래 연속일)** 을 한다. 하지 않는 것: 임계값·판정 결과를 팩트 행에 굽기, 통계 정규화, 최신 판본 선택, `_current` 테이블로 과거 필터.

원칙 6(ERD v0.1): ① 전 팩트 행 PIT 게이트 ② 원주가 불변 + 계수 분리 ③ corp/ticker 이축 ④ 결측은 결측 ⑤ 지식은 카탈로그 ⑥ 읽기 전용 SQL 계약 — **PIT 뷰 4종 + 보조 뷰 3종**(§5).

**정본 우선순위(사용자 확정 2026-09-03)**: KRX 가 주는 사실은 KRX 정본. 다른 소스는 KRX 부재 영역 정본(수급·공매도·대차·신용 = 키움/KIS, 재무·지분·배당 = DART, 컨센서스 = WISE), 폐지 종목 보완(`src`), 검산축(KIS 수정종가) 세 역할만. 폐지일도 KRX(listing 소멸) 우선, KIS 는 대조축(§4-1 `security`).

basis 어휘: stage 4종 `measured`·`derived`·`default`·`unknown`(HANDOFF §2) + **equity 신설 `convention`**(관행 랙, `dataset_profile` 전용).

---

## 2. 저장 · 판본 · 빌드 `[결정 1·2]`

| 항목 | 규칙 | 근거 |
|---|---|---|
| 포맷 | parquet, `data/equity/<table>/v=<build>/…` + `MANIFEST.json`(keep=3) + 파티션 `_meta.json` + `_reject/<reason>/` | stage §2 계승 |
| 파티션 클래스 | `date_axis` = `year(date)` / `receipt_axis` = **`substr(rcept_no,1,4)`**(stage 와 동일, `rcept_dt` 아님 — 접수번호가 불변 키) / `whole`. receipt_axis 테이블은 `rcept_no` 필수 컬럼 | STAGE_DESIGN §4 파티션 표 |
| 입력 고정 | `BuildRecord.inputs = {stg_x: build_id}`(09-03 반영, 테스트 194 통과). 고정 빌드의 `v=` 디렉토리는 `data/equity/_pinned/<stg_x>/v=<build>/` 하드링크 + 그 `BuildRecord` 1건을 `_pinned/<stg_x>/MANIFEST.json` 에 복사(맨 glob 금지 유지). 같은 파일시스템 실측(§10 P9) | manifest.py `commit()` rmtree · build_id 마이크로초 유일 |
| 재현성 | 같은 `inputs` → 파티션 `content_hash` 전량 동일(EG5a). `inputs` 변경 시 as-of 불변 검사(EG5c) | WORKFLOW §2 |
| 뷰 카탈로그 | `equity.duckdb` 는 데이터 없이 테이블 매크로만, **절대경로**로 빌드마다 **임시 파일 생성 후 `os.replace`**(read_only 리더와 충돌 회피). 기본값 인자 `name := expr`, 랙은 본문 스칼라 서브쿼리로 `dataset_profile` 조회 — P1d 실측 | §10 P1a~d |
| 빌드 실행 | 서버 테이블 직렬(`flock`), `memory_limit` 6GB·threads 3(stage 와 동일)·`temp_directory=data/equity/_tmp/spill`(여유 282GB). `fin_std`·격자는 연도 파티션 단위로 첫 슬라이스 실빌드 → RSS·스필·초 기록 후 전 구간 | RAM 15GB, stage `stg_fin` 스필 15~18GB |
| 커밋 입자 | 테이블. 섀도 버전 → 게이트 전량 → `MANIFEST.json` `os.replace`. 실패는 `_failed/<build>.json` | stage §2 |
| 상수 | `data/equity/baseline.json` `{table, metric, sql, value, measured_at}`. 코드·본문 하드코딩 금지. 첫 빌드 `skip(no_baseline)` + 측정치 `_meta.gates[].metrics` | stage §9 |

---

## 3. 공통 골격 컬럼

| 컬럼 | 규칙 |
|---|---|
| `ticker` TEXT(6) · `date` DATE · `corp_code` TEXT(8) | 정규 키. 티커 정수 캐스팅 금지(`0001A0` 실재) |
| `available_date` · `available_basis` | 전 팩트 행 필수(EG2). 파생 컬럼은 **구성 행 `available_date` 의 max**. 랙은 뷰 인자·`dataset_profile` |
| `observed_date` | stage 계승(재수집 판본 축) |
| `src` | 소스 상보 결합 테이블에만(`kiwoom` / `kis` / `v3` / `wise`). 결합 테이블은 PK 에 `src` 포함 |
| `fill_kind` STRUCT(kind, evidence) | 격자 테이블(3단계)만. kind ∈ {`measured`, `src_omitted`, `empty_response`, `not_collected`} · evidence ∈ {`shard_done`, `unit_ok`, `shard_empty`, `unit_empty`, `none`}. stage `miss_kind`(셀 결측 원인)는 그대로 동반 |
| `factor_ok` BOOL | `adj_factor` 만. false = 검증 안 된 계수(팩터층은 가로지르는 창을 결측 처리) |

---

## 4. 테이블 카탈로그 (26)

표기: **grain** = PK · **원천** = stage 테이블(컬럼, rules 실명) · **available** = 규칙(basis) · **파티션** · **EG1** = 행수 등식 · **규칙**. 게이트 조건은 §8.

### 4-1. 1단계 — 마스터 · 유니버스 · 캘린더 · 지수

**`corp`** — grain `corp_code` · whole
- 원천 `stg_corp_map`(`corp_code`·`ticker`·`corp_name_current`, 3,478) · `stg_company`(`acc_mt`·`induty_code_current`)
- 컬럼 `corp_code`·`corp_name`(현재값 라벨)·`fiscal_month` INT·**`fiscal_month_basis='current_snapshot'`**(결산월 변경 이력 없음 — `stg_company` 는 observed_date 1개, §10 P10)·`induty_code`(KSIC, 현재값)·`induty_class`(64/65/66 → `financial`)
- EG1 `= distinct corp_code(stg_corp_map)` = 3,478 · `acc_mt` NULL 0(P10) · `stg_fin.corp_code ⊆ corp_map`(P10: 미매핑 0)
- 규칙 `corp_cls` 는 싣지 않는다(현재값, 과거 필터 함정). `acc_mt` 도 현재값 — 결산월 변경 기업의 과거 `period_end` 오류는 4단계 EG7 격리로 잡는다(§4-4)

**`security`** — grain `ticker` · whole
- 원천 `stg_listing_daily`(`isin`·`name`·`list_date`·`market`·`secugrp`·`stkcert_tp`·`sect_tp`) · `stg_etf_price_daily`(ETF 존재) · `stg_delisted_master`(`lstg_abol_dt`, 652, `_current`)
- 컬럼 `ticker`·`corp_code`(via corp_ticker)·`isin`·`name_current`·`sec_type`·`list_date`·`list_date_basis`·`delist_date_krx`·`delist_date_kis`·`delist_conflict`·`delist_date`·`delist_date_basis`
- `sec_type` 매핑(전 이력 어휘 실측 P11): `secugrp='주권'` ∧ `stkcert_tp='보통주'` → `common`(3,341) / `구형우선주`·`신형우선주`·`종류주권` → `preferred`(120+60+14) / `부동산투자회사` → `reit`(34) / `선박투자회사` → `ship_fund`(49) / `투자회사`·`사회간접자본투융자회사` → `fund`(12+2) / `외국주권` → `foreign`(24) / `주식예탁증권`·`주식예탁증서` → `dr`(14+6) / `sect_tp='SPAC(소속부없음)'` ∨ `name LIKE '%기업인수목적%'` → `spac`(KOSPI 4·KOSDAQ 372, common 보다 우선) / ETF 원천 → `etf`(1,416) / 그 외 → `other` + EG7 격리
- `list_date`: KRX `list_date`(measured). ETF 는 NULL(`unknown`)
- **`delist_date` = KRX 우선**: `delist_date_krx` = span 마지막 존재일 + 1거래일(`derived`) — **단 마지막 존재일 = 백필 종료일(baseline `backfill_end`=2026-08-20)이면 NULL(`unknown`, coverage_gap)**. `delist_date_kis` = `lstg_abol_dt`(652). 둘 다 있고 다르면 `delist_conflict=true`(건수 baseline, `_reject` 아님). `delist_date` = krx, 없으면 kis
- EG1 `= distinct ticker(stg_listing_daily ∪ stg_etf_price_daily)` = 3,672 + 1,416(P8: 서로소)

**`security_span`** — grain (`ticker`, `span_seq`) · whole
- 원천 (`stg_listing_daily` ∪ `stg_etf_price_daily`) 존재일 × `trading_calendar`. span = 캘린더 기준 연속 존재 구간(최대 run)
- 컬럼 `ticker`·`span_seq`·`first_date`·`last_date`·`n_days`·`end_reason`(`delisted`·`coverage_gap`·`data_gap`(예약 — listing 완결성 4,094/4,094 실측 P11 이라 현재 0건))
- 규칙 `last_date ≤ backfill_end`. 재상장 실측 2종 036220·101970(P8) — baseline `respan_count=2`
- EG1 비중첩 ∧ `Σ n_days = count(ticker,date)(listing ∪ etf)` ∧ `count(distinct date)(listing) = trading_calendar 거래일 수`(P11: 4,094 = 4,094) ∧ 구간 2개 이상 티커 수 = baseline

**`corp_ticker`** — grain `ticker` · whole
- 원천 `stg_listing_daily.isin` · `stg_corp_map`(`corp_code`↔`ticker`)
- 규칙 `isin8 = substr(isin,1,8)`, **`KR7` 계열만 그룹핑** — 전 이력 3,438 그룹 전부 보통주 정확히 1(P11). 비KR7(KR8 16·HK0 15·KYG 8·USU 1 = 40 티커)은 단독 corp(과합병 방지, SPEC §2-17 HK000005 5개사 사례). `common_ticker` = 같은 isin8 의 common. DART 매핑 없는 티커(ETF·일부 외국주권)는 `corp_code` NULL
- 컬럼 `ticker`·`corp_code`·`isin8`·`is_common`·`common_ticker`·`link_basis`(`isin8`·`corp_map`·`none`)
- EG1 `= security 행수` · EG3 KR7 isin8 그룹당 `is_common` 정확히 1

**`trading_calendar`** — grain `date` · whole
- 원천 `stg_index_daily` distinct date(4,094일, 2010-01-04~2026-08-20) ∪ gap 축(`stg_flow_split_daily`·`stg_credit_daily` date > backfill_end)
- 컬럼 `date`·`prev_td`·`next_td`·`calendar_source`(`krx_index`·`gap_axis`). 행 = 거래일뿐(is_trading_day 컬럼 없음 — 항진명제)
- EG1 `= 4,094 + gap 거래일 수`(baseline)

**`index_daily`** — grain (`index_class`, `index_name`, `date`) · date_axis
- 원천 `stg_index_daily` 1:1(KOSPI 51 · KOSDAQ 40 — KRX 업종지수 포함, 업종 상대성과의 정본 축). 대표지수 실명 `코스피`·`코스닥`·`코스피 200`·`코스닥 150`
- available = `date`(default, KRX T+1 08:00 관측 상한 → profile)
- EG1 `= 347,821`

**`universe_daily`** — grain (`date`, `ticker`) · date_axis
- 원천 `security_span` × `trading_calendar` · `stg_listing_daily`(`market`·`list_shrs`·`sect_tp`) · `stg_etf_price_daily` · `stg_price_daily`(`volume_shr`·`value_krw`·`mktcap_krw`) · `stg_master_daily`(`is_admin_issue`·`is_trade_halt`·`is_liquidation`, 2026-09-01~) · `stg_disclosure`(신호, 아래 표)
- 컬럼 `status`·`market`·`sec_type`·`mktcap_krw`·`adv20_krw`·`listing_age_days`·`no_trade_run`·`halt_state`·`admin_state`·`admin_state_basis`·`liquidation_window`·`signal_halt`·`signal_halt_release`·`signal_admin`·`signal_liquidation`·`signal_delist`·`admin_flag`(09-01~, 이전 NULL)·`available_date`·`available_basis`
- **상태 규칙(PIT, 전부 D 이전 정보만)**:
  - `halt_state` = 지정 공시 `rcept_dt` 부터 [해제 공시 `rcept_dt` ∨ 다음 `volume>0` 거래일) 까지 true. 실측(P6·P12): 지정 8,415 · 해제 2,287 · 지정+해제 동일일 1,214 · 지정 후 첫 거래까지 중앙값 4일, p90 562일, 재거래 없음 930건 → 거래 재개가 암묵 해제 축
  - `status` = `coverage_gap`(gap 구간) / `delisted`(`delist_date ≤ D`) / `suspended`(`halt_state` ∨ (`price_kind='reference'` ∧ `no_trade_run ≥ k`, k 는 baseline)) / `listed`
  - `admin_state`: KOSDAQ = `sect_tp ∈ {관리종목(소속부없음), 투자주의환기종목(소속부없음)}` 일별(PIT, basis measured, 117+43 실측) / KOSPI = `sect_tp` 공란(P12) → 지정 신호 후 365거래일 창 플래그, basis `derived`, **해제 공시 실측 3건뿐이라 상태 종료 불가 — §11 한계** / 2026-09-01~ 는 `is_admin_issue`
  - `liquidation_window` = `signal_liquidation`(정리매매 개시 공시) 부터 `delist_date` 까지. 실측 349건 vs MS-04 추정 약 1,100건 → 커버 약 32%, 미달분은 `no_trade_run` 근사만(§11)
  - `no_trade_run` = 직전 무거래 연속 거래일(derived). 정리매매 재개일 FN 은 `liquidation_window` 가 보완
- `adv20_krw` = `[D−19, D]` 거래일 값 그대로 저장(랙은 뷰가 D 를 옮겨 적용)
- available: 행 = `date`(default). 컬럼군별 랙은 profile(`core`·`mktcap,adv20,listing_age`·`admin_flag`·`signal_*`)
- ETF 는 행에 포함(`sec_type='etf'`) — 정책 판정 컬럼 없음(`universe_policy` + 팩터층)
- EG1 `= Σ_span n_days(≤ backfill_end) + gap 거래일 × backfill_end 활성 티커 수` · EG3 `halt_state` 열린 채 폐지·gap 없이 끝난 구간 0

**공시 축 신호(`stg_disclosure.report_nm`, 콜 0, 실측 P6·P12)** — 전부 `has_ticker`, `available_date = rcept_dt`(measured), report_nm 은 DESIGN §5 정규화 후 대괄호 접두어(`[기재정정]` 등) 제거 뒤 매칭:

| 신호 | report_nm 술어 | 건수(2010~) |
|---|---|---|
| `signal_halt` | `%매매거래정지%` ∧ NOT `%해제%` | 8,415 (전건 11,970 − 해제 계열 3,555) |
| `signal_halt_release` | `%매매거래정지해제%`(정리매매 개시 제외) · `%매매거래정지및정지해제%` 는 당일 양쪽 | 2,287 · 1,214 |
| `signal_liquidation` | `주권매매거래정지해제%정리매매 개시%` | 349 |
| `signal_admin` | `%관리종목%` ∧ NOT `%해제%` | 1,218 (해제 3) |
| `signal_delist` | `%상장폐지%` | 3,952 |
| 락일(→ `corp_event` 효력일 교차) | `배당락`·`배당락(주식배당)`·`권리락(무상증자)`·`권리락(유상증자)`·`권리락` | 257 · 200 · 618 · 455 · 452 |
| 배당 결정(→ `corp_event`) | `현금ᆞ현물배당결정`·`…주주명부폐쇄(기준일)결정`·`주식배당결정` | 19,275 · 3,260(+중간 411) · 548 |

**`universe_policy`** — grain (`policy`, `rule_seq`) · whole
- 컬럼 `policy`(`all`·`investable`·`liquid`)·`rule_seq`·`predicate`(SQL 술어)·`threshold_kind`(`quantile`·`absolute`·`flag`)·`threshold_value`·`basis`·`measured_at`·`version`
- 초기값은 서버 실측(연도별 `mktcap`·`adv20` 분위수) 후 등재. equity 는 표를 **보관**, 적용은 팩터층(`v_universe(:d, :policy)` 는 편의 뷰). `baseline.json` 은 게이트 상수 전용이라 역할이 다르다(§12 EQD-10 판단)

### 4-2. 2단계 — 가격 · 기업행위 · 조정계수

**`price_daily`** — grain (`ticker`, `date`) · date_axis
- 원천 `stg_price_daily`(9,201,516) ∪ `stg_etf_price_daily`(1,688,735) — 서로소 · `stg_listing_daily`(`par_value_krw`·`list_shrs`) · `stg_credit_daily.close_krw`(수정종가, 검산축)
- 컬럼 `open`·`high`·`low`·`close`(원주가, O/H/L NULL 유지)·`volume_shr`·`value_krw`·`mktcap_krw`·`shares_out`·`par_value_krw`·`price_kind`(`traded` / `reference` = `volume=0`)·**`krx_kis_close_ratio`**(실수: KIS 수정종가 ÷ KRX 원주가, 판정은 EG8 baseline)·`available_date = date`(default; 시총·주식수 익일은 profile column_scope)
- EG1 `= 10,890,251`

**`corp_event`** — grain `event_id` · receipt_axis(`rcept_no` 필수, 신호 유래는 해당 공시 rcept_no)
- 원천 DS005 15종(`stg_event_*`) · `stg_capital`(비집계) · `stg_disclosure` 락일·배당결정 신호 · KRX `list_shrs`·`par_value_krw` 변화(효력일 교차)
- 컬럼 `event_id`·`ticker`·`corp_code`·`event_type`(`split`·`reverse_split`·`bonus`·`rights`·`capred`·`spinoff`·`merger`·`stock_dividend`·`cash_dividend`·`cb_issue`·`treasury_buy`·`treasury_sell`·`other`)·`announce_date`(rcept_dt)·`effective_date`·`effective_basis`(`disclosure_body`·`krx_shares_change`·`krx_notice`·`unconfirmed`)·`ratio`·`amount_krw`·`rcept_no`·`available_date = announce_date`(measured)
- 규칙 효력일 = 공시 본문 없이 확정 못 하면 KRX `list_shrs`·`par_value_krw` 변화일과 락일 공시(rcept_dt = 락일 관행, 2단계 실측 후 basis 확정)로 교차. 인적분할·시그니처 불일치는 `effective_basis='unconfirmed'`
- EG1 `= Σ 선언 원천 행 − dedup(ticker, event_type, effective_date; 건수 `_meta`)` · EG2 `available_date ≥ announce_date`(effective 축 아님) · EG8 탐지 recall: 기준가≠전일종가 5,214건(SPEC §3-1) 매칭율 ≥ baseline

**`adj_factor`** — grain (`ticker`, `effective_date`, `event_id`) · date_axis
- 두 축(SPEC §3-2): `price_factor`·`share_factor`. 시총 불변 이벤트(split·bonus·stock_dividend)는 `price × share = 1`(EG3). rights·capred·spinoff·merger 는 두 축 상이 + `factor_source`(`disclosure_ratio`·`krx_shares_change`·`base_price`)·`factor_ok`
- `available_date = min(원인 공시 rcept_dt, effective_date + 1거래일)`(basis `measured` / `derived`). **EG2 예외: `available_date ≥ announce_date` 축**(내용일 = effective_date 가 아님)
- **누적계수 정규화(§5)**: `cum_*_factor(d, asof)` = d 이후 asof 까지 효력 발생한 계수의 곱, base = asof(asof 이후 행은 1). `adj_price = close × cum_price_factor`, `adj_volume = volume × cum_share_factor` — **둘 다 곱셈**. 삼성전자 50:1: 분할 전 행 `cum_price=1/50`·`cum_share=50`
- 배당 계수는 만들지 않는다(§11). `cash_dividend` 이벤트는 `corp_event` 보존
- EG1 `= corp_event 중 계수 대상 유형 행수` · EG8 분할·무상증자 이벤트일 수정수익률 점프 ≤ baseline ∧ **조정 거래량 점프(M03 절댓값) ≤ baseline** · `krx_kis_close_ratio` 대 누적계수 일치율 ≥ baseline

### 4-3. 3단계 — 격자 · 결측 3분류

공통: 격자 = `trading_calendar`(거래일) × `universe_daily`(`status ∈ {listed, suspended}` ∧ **`sec_type ∉ {etf}`** — ETF 는 수급·공매도·신용 원천이 없어 격자 밖) · date_axis · **캘린더 하한 이전 원장 행은 `_reject/pre_calendar`**(P11: short 58,211행·674종목(2008-06-23~) · flow 7,609·1,269(2009-12-22~) · foreign 67,994·1,269(2009-10-15~) · credit 24,497·1,750(2007-07-16~) · lending 0) · 유니버스 밖 티커는 `_reject/off_grid`.

`fill_kind` 판정(어휘 실측 P3·P12 — 두 축의 status 어휘가 다르다):

| 축 | 빈 셀 조건 | kind | evidence |
|---|---|---|---|
| 키움 `stg_shards_kiwoom` | (src_api, ticker) 샤드 존재 ∧ `req_start ≤ d ≤ req_end` ∧ `status='done'` ∧ **KRX 가격 행 존재일** | `src_omitted`(0) | `shard_done` |
| 키움 | 샤드 `status='empty'` | `empty_response`(NULL) | `shard_empty` |
| KIS `stg_units_kis` | (dataset, ticker) 유닛 `window_from ≤ d ≤ window_to` ∧ **`status='ok'`** | `src_omitted`(0) | `unit_ok` |
| KIS | 유닛 `status='empty'` | `empty_response`(NULL) | `unit_empty` |
| — | 어느 로그도 없음 | `not_collected`(NULL) | `none` |

키움 샤드 = {`done` 2,605 / `empty` 60}, 티커당 전 구간 1개(`collected_at` 08-23~24 2일뿐 — 로그 자체의 결손인지 미수집인지 구분 불가, `rules_kiwoom.py:258`). `n_rows ≥ cap` 행 다수(ka10014 2,089/2,605) — `cap` 의미 미확정, 절단 판정에 쓰지 않는다. KIS 유닛 `dataset ∈ {credit(3,175), flow(652), loan(287), master(652), short(652)}` · status {`ok`, `empty`} — 폐지 652 종목의 flow/short 는 KIS 축으로만 판정.

**`flow_daily`** — 원천 `stg_flow_daily_kiwoom`(13주체 `_krw`, 정본) + `stg_flow_split_daily`(KIS 13주체 `*_ntby_tr_pbmn_krw`, 폐지 커버) + `stg_foreign_daily`(`wght_pct`·`limit_exh_rt_pct`·`poss_stkcnt_shr`). 컬럼 = **키움 13주체 전부** + `foreign_wght_pct`·`limit_exh_rt_pct`·`foreign_poss_shr` + `src`. KIS 행은 아래 대응표로만 채우고 대응 없는 주체는 NULL:

| 키움 컬럼 | KIS 컬럼 | 비고 |
|---|---|---|
| `ind_invsr` 개인 | `prsn` | |
| `frgnr_invsr` 외국인 | `frgn` | |
| `orgn` 기관계 | `orgn` | 합계 컬럼 — 항등식에서 제외 |
| `fnnc_invt` 금융투자 | `scrt` | 명세 기반 대응, 검증축 없음(겹침 0) |
| `insrnc` 보험 | `insu` | |
| `invtrt` 투신 | `ivtr` | |
| `bank` 은행 | `bank` | |
| `penfnd_etc` 연기금등 | `fund` | 명세 기반 |
| `samo_fund` 사모펀드 | `pe_fund` | |
| `etc_corp` 기타법인 | `etc_corp` | |
| `etc_fnnc`·`natn`·`natfor` | 없음 | NULL |
| 없음 | `mrbn`·`etc_orgt`·`etc` | 버림(원장 보존) |

EG3 12주체 항등(Σ 주체 순매수 = 0 ± baseline, 파일럿 검산 ±6백만원)은 **`src='kiwoom'` 행에서 `orgn` 제외** 12컬럼으로만.
**`short_daily`** — 원천 `stg_short_daily_kiwoom` + `stg_short_daily_kis`(공매도) · `stg_lending_daily`(키움 대차 `rmnd` — **단위 미측정**, HANDOFF §4) + `stg_loan_daily_kis`(KIS 대차 `rmnd_stcn_shr`). 컬럼 `short_volume_shr`·`short_value_krw`·`short_ratio_pct`·`lending_balance_kis_shr`·`lending_balance_kiwoom_raw`(단위 미확정 → profile) ·`src`.
**`credit_daily`** — 원천 `stg_credit_daily` 단독(융자 `whol_loan_rmnd_stcn_shr`·대주 `whol_stln_rmnd_stcn_shr`·`stlm_date`). available = `date`(default) · `stlm_date − date` 분포는 profile 랙 근거.
EG1(3테이블 공통) `격자 행수 = Σ_d 활성 주권 티커 수` · `원장 행 = 격자 매핑 + _reject/pre_calendar + _reject/off_grid` · `flow` 컬럼 = 키움 13주체 컬럼 수.

### 4-4. 4단계 — 재무 PIT

**`fin_std`** — grain (`corp_code`, `period_end`, `report_code`, `fs_div`) · receipt_axis(`rcept_no` 컬럼 포함)
- 원천 `stg_fin`(자연키 8: corp_code·bsns_year·reprt_code·fs_div·sj_div·account_id·account_detail·ord, `account_std`·`is_krw`) · `stg_rcept_dt_map` · `corp.fiscal_month`
- `period_end`: `bsns_year` 는 **회계연도 종료 연도**(P2: 3·6·9·11·8·10월 결산 중앙값 78~92일). 후보 2개(`bsns_year`·`bsns_year+1`) × `acc_mt` 말일 중 `0 ≤ rcept_dt − period_end ≤ 200`(baseline) 인 쪽. 둘 다 아니면 `period_end_basis='unknown'` + EG7 격리(P2: 01·04·05월 결산 소수 기업 지연 250~300일 — 결산월 변경 의심, 격리로 잡는다). 분기: 11013 = 1Q(종료월−9), 11012 = 2Q(−6), 11014 = 3Q(−3), 11011 = 연간
- 손익 = `thstrm_amount` 그대로(분기보고서 전부 **3개월**, SPEC §2-7; `is_months` 동반). `*_q4_derived = 11011 − Σ(1Q,2Q,3Q)`; CF `_ytd` 보존 + `*_q` = 전분기 누계 차감. **파생 컬럼의 `available_date = max(구성 행 rcept_dt)`**, `derived_n_rows` 동반, 구성 행 하나라도 없으면 NULL(부분합 금지)
- 표준 계정: `fin_map.py` 21항목 + 추가 3(`depreciation`·`borrowings`·`interest_expense`, `account_std` 코드 실재 확인 후 — 없으면 V05·V07·Q07·Q08 미착수)
- `revenue_basis`(`REVENUE_FALLBACK` 계승) · `is_krw` 만 · `account_std=false` 조인 금지
- `available_date = rcept_dt`(참조표 → **basis `derived`**, build.py:359 규약) · `rcept_no` · `has_correction`·`correction_seq_max`(disclosure_version) · `restated_unknown = true`(전 행 — DART 최신 판본만, SPEC §2-18)
- EG1 `= distinct(corp_code, bsns_year, reprt_code, fs_div) with ≥1 account_std 행` · EG7 `rcept_dt − period_end` ∉ [0, baseline_p99] 격리

**`disclosure_version`** — grain (`group_key`, `correction_seq`) · receipt_axis
- 원천 `stg_disclosure`(정기보고서 3종: report_nm 정규화 → **`^\[[^\]]*\]` 접두어 제거**(`[기재정정]` 11,942·`[첨부추가]` 2,666·`[첨부정정]` 969 등) → 종류 추출; **`유동화전문회사`·`회계법인사업보고서`·`제출기한연장신고서`·`해외…국내신고` 는 제외**; 기간 라벨 `(YYYY.MM)` 없는 586건은 `group_key_basis='no_label'` 격리 — P12) · `stg_doc_index.zip_ok`
- 컬럼 `group_key`·**`bsns_year`·`reprt_code`(라벨 + `corp.fiscal_month` 역산 — `fin_std` 와 4키 등가조인)**·`rcept_no`·`rcept_dt`·`is_correction`·`correction_seq`(0 = 원본)·`corrected_at`·`link_basis='grouped'`(L1 후 `parsed`)
- EG1 `= 정기보고서 3종 접수 행수(제외 종류 제외)` · EG6 표본 오판율 ≤ baseline · `fin_std ⋈ disclosure_version` 무매칭률: 비12월 105사 = 12월사 ± baseline

### 4-5. 4-B단계 — DART 보조 PIT

전부 receipt_axis(`rcept_no` 포함) · `available_date = rcept_dt`(참조표, **derived**) · grain = **stage 자연키에서 유도** · `corp_ticker` 로 ticker 부여. `row_kind='aggregate'` 제외는 **`stg_shares`·`stg_tesstk`·`stg_hyslr` 3테이블만**(row_kind 부착 테이블, `rules_dart.py:136`).

| 테이블 | grain | 원천 | 컬럼(핵심) | EG1 |
|---|---|---|---|---|
| `holder_daily` | (`rcept_no`, `repror`, `src`) | `stg_holder_elestock`(32,668) ∪ `stg_holder_majorstock`(22,209) | 보고자·구분·지분 전/후 | = 두 stage 행수 합(1:1) |
| `ownership_snapshot` | (`corp_code`, `bsns_year`, `reprt_code`, `nm`) 비집계 | `stg_hyslr`(228,226) | 최대주주·특수관계인 지분율 | = 비집계 행수 |
| `shares_outstanding` | (`corp_code`, `bsns_year`, `reprt_code`, `se`) 비집계 | `stg_shares`(97,194) | 발행·자기·유통 주식수 | = 비집계 행수 |
| `treasury_stock` | (`corp_code`, `bsns_year`, `reprt_code`, `acqs_mth1`, `acqs_mth2`, `acqs_mth3`, `stock_knd`) 비집계 | `stg_tesstk`(328,704) | 취득·처분·소각 수량 | = 비집계 행수 |
| `audit_opinion` | (`corp_code`, `bsns_year`, `reprt_code`, `bsns_year_label`) | `stg_audit`(93,037) | `adt_opinion`·`adt_opinion_class` | = 93,037 (1:1) |
| `dividend_event` | (`corp_code`, `bsns_year`, `reprt_code`, `stock_knd`) | `stg_dividend`(384,232, `se` 라벨 wide) | `dps_krw`·`cash_total_krw`·`yield_pct`·`payout_pct` — **기준일·락일 없음** | = distinct(요청축, stock_knd) |

지분 롤링 2년 창 경계는 profile `coverage_from`.

### 4-6. 5단계 — 컨센서스 PIT

**`consensus_daily`** — grain (`ticker`, `obs_month`, `target_period`, `metric`, **`src`**) · date_axis(**물리 파티션 키 = obs_month 연도, PIT 필터 축 아님**)
- 원천 `stg_consensus_monthly`(`consensus`·`consensus_min`·`consensus_max`·`unit`·`obs_label`·`obs_date`·`metric`, fetched_date ≥ 2026-09-01) · `stg_v3_revision_daily`(wide 8: `revenue`·`op`·`ni`·`eps`·`per`·`bps`·`pbr`·`roe_pct`, 키 (ticker, `date`, target_period), 2026-04-03~09-02) · `stg_v3_consensus_annual`
- 구간 2행 규칙: `src='v3'` available = `collected_date`(measured; NULL → `date`(base_date 원장명) default + `coverage_degraded`) / `src='wise'` available = **min(fetched_date)**(measured). **`obs_month` 를 날짜 축으로 쓰는 것 금지**(EG-C ⑨ 는 wise 구간에서만 유효 — v3 는 접은 뒤 obs_month ≈ available)
- v3 → metric unpivot 대응표: `revenue`→`revenue` · `op`→`op` · `ni`→`ni` · `eps`→`eps` · `per`·`bps`·`pbr`·`roe_pct` → 동명(WISE metric 어휘와 대조는 5단계 첫 작업). v3 는 `est_min`·`est_max` 없음(NULL). obs_month 변환 = 월 내 min(`date`) 행(월중 리비전은 다음 관측으로 밀림 — 정보 소실, look-ahead 아님)
- 컬럼 `est_mean`·`est_min`·`est_max`·`unit`(EPS 원·매출 억원)·`src`. `n_analyst` 는 원천 없음 → 없음(커버리지는 `opinion_daily.analyst_count`)
- `target_period` 롤(연도 변경)에서 G05 는 같은 `target_period` 끼리만 비교 — 뷰 `v_consensus` 가 `target_period` 를 그대로 노출하고 팩터층 규칙
- EG1 `= distinct(ticker, obs_month, target_period, metric, src)` · EG6 max 선택 0 · EG8 v3⋈WISE 겹침 구간(09-01~02) 일치율 ≥ baseline

**`opinion_daily`** — grain (`ticker`, `obs_date`, `src`) · date_axis · 원천 `stg_analyst_summary`(ticker × fetched_date: `opinion_score`·`target_price_krw`·`analyst_count`, 810종목·2일) ∪ `stg_v3_analyst_opinions`. available = fetched_date(measured). EG1 `= summary 행수 + v3 distinct`.
**`opinion_broker_daily`** — grain (`ticker`, `fetched_date`, `broker`, **`opinion_date`**) · date_axis · 원천 `stg_analyst_broker`(521종목·3일). 컬럼 + `prev_opinion_date`(간격 없이 `change_pct` 해석 금지). EG1 `= 9,717`(1:1).
커버: WISE `covered` 804 / `none` 1,762(P7) — 커버 밖은 결측. 5단계는 데이터가 얇아(G05~G08 사용 가능 2027~) 실행 순서상 마지막(6 직전).

### 4-7. 6단계 — `dataset_profile` · whole

grain (`table_name`, `column_scope`). 컬럼 `source_stage_tables` TEXT[] · `content_date_means`·`publish_when`·`basis`·`recommended_lag_days`·`evidence`·`coverage_from`·`coverage_to`·`coverage_basis`·`universe_coverage_pct`·**`coverage_by_mktcap_quintile` DOUBLE[5]**. EG2 = `stage._meta.lag_known=false` 인 테이블 T 중 `source_stage_tables` 에 T 를 포함하며 `recommended_lag_days ≥ 1` 인 profile 행이 없는 T = 0(SQL 술어). 초기 행(테이블 × 컬럼군 단위):

| table · scope | publish_when | basis | lag | coverage_from | 근거 |
|---|---|---|---|---|---|
| price_daily · ohlcv | D 15:30 시장 공개(관행) · 사본 T+1 08:00 상한(측정) | convention | 0 | 2010-01-04 | SPEC §2-20; 체결이 D+1 09:00 시가라 08:00 < 09:00 |
| price_daily · mktcap,shares | 익일 | convention | 1 | 2010-01-04 | KICKOFF §1 |
| index_daily | T+1 08:00 상한 | measured | 0 | 2010-01-04 | SPEC §2-20 |
| universe_daily · core(status,market,sec_type,kosdaq_sect) | 익일(listing 스냅샷) | convention | 1 | 2010-01-04 | listing lag_known=false |
| universe_daily · mktcap,adv20,listing_age | 익일 | convention | 1 | 2010-01-04 | |
| universe_daily · admin_flag | snap_date | measured | 1 | 2026-09-01 | master_daily coverage_from |
| universe_daily · signal_*,halt_state,liquidation_window | rcept_dt | measured | 1 | 2010-01-04 | |
| flow_daily · kiwoom | 익영업일 | convention | 1 | 2009-12-22 → 격자 2010-01-04 | ka10060 |
| flow_daily · foreign | 익영업일 | convention | 1 | 2009-10-15 → 2010-01-04 | ka10008 |
| short_daily · short | 익영업일 | convention | 1 | 2008-06-23 → 2010-01-04 | ka10014 |
| short_daily · lending | 익영업일 | convention | 1 | 2011-07-25 | ka20068 |
| credit_daily | 익영업일 | convention | 1 | 2007-07-16 → 2010-01-04 | `stlm_date − date` 분포 |
| fin_std · 전 컬럼 | rcept_dt(시각 없음) | derived | 1 | FY2015 | SPEC §2-19 |
| 4-B 6테이블 | rcept_dt | derived | 1 | FY2013~2015 · 지분 2024-08 | |
| consensus_daily · wise | fetched_date | measured | 1 | 2026-09-01 | |
| consensus_daily · v3 | collected_date | measured | 1 | 2026-04-03 | |
| opinion_* | fetched_date | measured | 1 | 2026-09-01 | |

---

## 5. 뷰 · 매크로 시그니처 (`equity.duckdb`, DuckDB 매크로 문법 — 타입 선언 없음, 기본값 `:=`, 랙은 본문 서브쿼리)

```sql
-- ① 유니버스: 정책 미적용('all')이 기본. 컬럼군별 랙은 profile 을 본문에서 읽는다
v_universe(d, policy := 'all', lag_override := NULL)
  → ticker, status, market, sec_type, mktcap_krw, adv20_krw, listing_age_days, no_trade_run, halt_state, admin_state, admin_state_basis, liquidation_window, signal_*, admin_flag
-- ② as-of 누적계수: effective_date ≤ asof ∧ available_date ≤ asof − lag. base = asof, asof 이후 행은 1
v_cum_adj(asof, lag_override := NULL) → ticker, date, cum_price_factor, cum_share_factor, any_factor_ok_false
v_adj_price(asof, lag_override := NULL)  → ticker, date, adj_close = close × cum_price_factor, adj_open/high/low 동일
v_adj_volume(asof, lag_override := NULL) → ticker, date, adj_volume = volume_shr × cum_share_factor
-- ③ 그 시점 최신 재무. fs_div 우선순위 CFS → OFS(fs_div_used 반환). ttm_x = 최근 4개 3개월 값 합, 4개 중 하나라도 available > asof − lag 면 NULL(연율화 금지)
v_fin_latest(asof, lag_override := NULL) → corp_code, period_end, report_code, fs_div_used, 표준계정…, *_q4_derived, ttm_*, has_correction, days_since_rcept
-- ④ 컨센서스 as-of: available_date ≤ asof − lag, 관측점별 최초 관측, target_period 그대로 노출
v_consensus(asof, lag_override := NULL) → ticker, obs_month, target_period, metric, est_mean, est_min, est_max, unit, src
-- 보조
v_firm_mktcap(d) → corp_code, firm_mktcap_krw = Σ 종류주 mktcap (003540 우선주 55.9% 사례)
```

엔진 어댑터(`adapters/equity_duckdb.py`)는 ①·`price_daily`·`adj_factor`/`corp_event` 만 읽는다(§7). `:asof` 를 세션마다 바꾸는 호출은 팩터층 패널 생성에서만.

---

## 6. 팩터 ID × 계정 매트릭스 (`fin_std` 표준 계정, FACTORS 정본 54)

| 팩터 | 필요 계정 | fin_map 항목 | 상태 |
|---|---|---|---|
| V01 PBR · Q01 ROE | `total_equity`·`equity_owners` | 있음 | ○ |
| V02 PER · Q02 ROA · Q04 발생액 · G04 EPS | `net_income`·`net_income_owners`·`eps_basic`·`total_asset`·`cf_operating_ytd` | 있음 | ○ (G04 는 `eps_basic`) |
| V03 PSR · Q03 영업이익률 · G01·G02 | `revenue`(+`revenue_basis`)·`op_profit` | 있음 | ○ |
| V04 PCR · Q05 FCF | `cf_operating_ytd`·`capex_ytd` | 있음 | ○ |
| Q06 부채비율 | `total_liab` | 있음 | ○ |
| V07 순현금 | `cash`·**`borrowings`** | 추가 필요 | △ |
| V05 EV/EBITDA | `op_profit`·**`depreciation`**·**`borrowings`**·`cash` | 추가 2 | △ |
| Q07 이자보상배율 | `op_profit`·**`interest_expense`** | 추가 필요 | △ |
| Q08 NOA | `total_asset`·`cash`·`total_liab`·**`borrowings`** | 추가 필요 | △ |
| G03 자산성장 | `total_asset` | 있음 | ○ |
| I01·I02 배당 · I03~I05 자사주 · E01~E04·E08 지분·감사 | 4-B 테이블 | 4-B | ○ |
| F09 신용잔고율 · G09·G10 · R04 베타 | `credit_daily` · `opinion_*` · `index_daily` | 3·5·1단계 | ○ |

추가 3계정은 4단계 착수 시 `stg_fin.account_id` 어휘에서 실재 확인 → 없으면 V05·V07·Q07·Q08 미착수로 인계.

---

## 7. 엔진 매핑 `[결정 5]`

엔진 사실(코드): `CorporateActionType ∈ {SPLIT, REVERSE_SPLIT, SHARE_COUNT_CHANGE}`, `CorporateActionEvent(ts, instrument, action_type, ratio, detail)` — `effective_date` 필드 없음, 적용 = `snapshot.ts ≥ action.ts` 인 첫 Bar 세션(loop.py), 수량 `floor(qty × ratio)`·평단 `/ratio`(portfolio.py) · `ratio` = 구주 1주당 신주 수 · `Membership(first, last)` · `LoadStatus ∈ {OK, NO_DATA, FORMAT_ERROR}` · `merge_results` 는 하나라도 NO_DATA 면 전체 실패(계약 테스트 `test_missing_instrument_fails_whole_query`) · `HistoryStore` 는 원주가만 저장.

| equity | 엔진 | 규칙 |
|---|---|---|
| `price_daily` | `BarSource` | 어댑터는 **종목별 질의 구간을 `security_span ∩ [start, end]` 로 좁혀** 로드(사전 제외 금지 — 생존편향). `price_kind='reference'`·`volume=0` 행은 Bar 로 내보내지 않고 `dropped_rows`. `open` NULL 을 close 로 채우지 않음 |
| `security_span`·`universe_daily` | `UniverseSource` | span → `Membership` 1:1(재상장 2구간 지원 확인). `suspended` 는 구간 유지(Bar 부재는 span 안이라 NO_DATA 아님). `coverage_gap` 은 구간을 끊지 않고 `UniverseQuery.end > backfill_end` 를 거절. `venue='XKRX'` 고정, `market` 은 필터 컬럼 |
| `adj_factor`/`corp_event` | `CorporateActionSource` | **`ts = effective_date 00:00`**. `available_date > asof − lag` 인 계수는 방출하지 않음. `split`·`bonus`·`stock_dividend`(share_factor > 1) → SPLIT, ratio = `share_factor` / `reverse_split`·`capred` → REVERSE_SPLIT, ratio = `share_factor`(< 1) / rights·spinoff·merger·`factor_ok=false` → SHARE_COUNT_CHANGE(알림 전용). 임계 1.5 미만도 전달(엔진 검출기는 equity 어댑터가 대체하므로 이중조정 없음) |
| 재무·컨센서스 | 포트 없음 | 팩터층이 `v_fin_latest`·`v_consensus` 로 시그널 패널 생성 → 엔진 밖 주입 |
| 어댑터 생성자 | `(root, asof, lag_overrides)` | 결과 메타에 기록 |

**엔진 측 한계(§11)**: `HistoryStore` 가 원주가만 갖고 SHARE_COUNT_CHANGE 는 포지션을 안 건드리므로, 엔진 안에서 계산되는 룩백 신호는 분할·감자·인적분할을 가로지르면 틀리고, rights/spinoff/merger 보유 포지션의 가격 불연속은 실현손익으로 기록된다. 룩백 신호는 팩터층(`v_adj_price`)에서 만든다.

---

## 8. 게이트 조건 · 픽스처 (테이블별 요약)

**EG0(입력 고정·stage 컬럼명 실재)·EG5(재현성)·EG7(범위 격리)·EG-C 는 전 단계 공통 필수**(WORKFLOW §2). 아래는 단계별 특이 조건.

| 단계 | EG1 | EG2 | EG3 | EG4 픽스처 | EG6/8/9 · 추가 |
|---|---|---|---|---|---|
| 1 | §4-1 7식 + listing 완결성 | `universe_daily`·`index_daily` available | KR7 isin8 그룹당 보통주 1 · span 비중첩 · `induty_code` 공란 0 · `halt_state` 열린 구간 0 · **폐지 909 전부 `delist_date` 보유** · `delist_conflict` ≤ baseline | 036220·101970 span 2 · 003540 1:N · 900050·900060 단독 · `0001A0` · coverage_gap · 우선주 폐지 1 · 무거래 연속 1 · 정지 지정→거래 재개 1 · 정지+해제 동일일 1 · 정리매매 개시 1 · KOSDAQ 관리 소속부 1 | EG7 `sec_type='other'` 격리 · `corp_ticker` 매핑률 ≥ baseline · **정리매매·폐지 recall**: 폐지 909 중 `signal_liquidation ∨ signal_delist` 가 폐지일 −30거래일 내 존재 비율 ≥ baseline · EG-C ②③⑩ |
| 2 | 3식 | `corp_event` announce 축 · `adj_factor` available ≥ announce | 시총 불변 `price×share=1` · `v_firm_mktcap` 합산 | 삼성전자 2018-05-04 50:1(`cum_price` 1/50·`cum_share` 50·`adj_volume(2018-05-03)` 손계산) · 무상증자 1.2:1 · 감자 1 · 207940 `factor_ok=false` · ETF 1 · reference 1 · KRX 관측만 계수 1(available = 효력일 익일) · 공시일≠효력일 이벤트 1 | EG7 · EG8 4항(가격·거래량 점프, ratio 일치율, recall) · EG-C ④⑤ |
| 3 | 격자·컬럼 수·pre_calendar | profile 행 | 12주체 항등(kiwoom, orgn 제외) | 2020-06-30 `src_omitted`(shard_done) · KIS `unit_ok` 셀 · 로그 없음 `not_collected` · `shard_empty` 셀 · 폐지 종목 KIS 만 있는 날 · 12주체 합 · KIS→키움 주체 대응 1행 | EG7 · EG8 겹침 0 · EG9 4항 |
| 4 | 2식 | rcept_dt ≥ period_end · 참조표 미스 0 · 파생 컬럼 available = max(구성) | PK · 조인 무매칭률 비대칭 | 삼성전자 FY2025 1Q+2Q=반기 누계 · 3월 결산 1사 period_end · 두산에너빌리티 FY2020 결측 · 정정 2회 그룹 · `[기재정정]` 접두어 그룹 · 은행 revenue_basis · 접수지연 2,875일 · q4_derived 판본 혼합 1 | EG6 전수·오판율 · EG7 `rcept_dt − period_end` 격리 · PIT 결측률 교차표 |
| 4B | 6식 | rcept_dt | PK(stage 자연키) | 자사주 집계행 · 비적정 의견 · 5% 보고 · 배당 se wide · 롤링 창 | EG6 · EG7 |
| 5 | 3식 | 구간별 basis | PK(+src, +opinion_date) | 판본 3 → min · 두 fetched_date 손계산 · unit · v3→wise 경계 · v3 NULL · unpivot 1 | EG6 · EG8 · EG9 급락·급증 · EG-C ⑨ |
| 6 | — | lag ≥ 1 · coverage_from 전수 · source_stage_tables 전수 | — | — | EG9 분위별 커버 경고 · EG-C ⑥⑧ |
| 7 | — | — | — | — | EG5a·c · EG-C ①~⑩ · 전 테이블 gates fail 0 |

---

## 9. 결정 기록 (권고안 → 확정 요청)

| # | 선택 | 대안 | 이유 · 실측 근거 |
|---|---|---|---|
| 1 산출 형식 | parquet 정본 + `equity.duckdb` 매크로 카탈로그(절대경로, 임시 파일 → `os.replace`) | KICKOFF 권장 duckdb 단일 파일 | 입력 불변 parquet → build 고정 = 스냅샷 · 단일 작성자 락 회피 · 파티션 해시 재현성 · 사용자 09-01 방향. P1a~d: 매크로·read_only·기본값 인자·본문 서브쿼리 성립, 상대경로 불가 |
| 2 판본·게이트 | stage 골격 계승 + `inputs` + `_pinned/`(디렉토리 하드링크 + BuildRecord 복사) · 게이트 내용 재정의 | stage 게이트 그대로 | keep=3 GC 가 고정 입력을 지움(manifest.py) · 조인층은 1:1 등식 없음 · 같은 파일시스템 실측 |
| 3 팩터 ID | `FACTORS.md` 정본 **54**(=50 + F09·G09·G10·R04, 정정 3건, §12 대응표) | CATALOG 평면 F## | F06 충돌 해소 · 계산식·시작연도 보유 |
| 4 유니버스 | 사실 컬럼 + 공시 축 상태(`halt_state`·`admin_state`·`liquidation_window`) + `universe_policy`(equity 보관, 팩터층 적용), 임계 = 분위수 | 파일럿 `in_micro/wide/core` | D6 재발 방지 · 정지 지정 8,415·해제 2,287·정리매매 349·관리 1,218 공시 실측으로 과거 PIT 상태 가능 |
| 5 엔진 경계 | 어댑터 3포트(`ts` = 효력일, span 단위 질의), 재무·컨센서스는 팩터층 주입 | `FundamentalSource` 포트 신설 | 엔진 `ports/` 4종·`PriceField` 5종·`CorporateActionEvent` 필드(코드) |

---

## 10. 0단계 실측 기록 (2026-09-03, 서버 stage, 콜 0)

| # | 항목 | 결과 |
|---|---|---|
| P1 | duckdb 1.5.5 매크로 | (a) in-memory 테이블 매크로 + `read_parquet` 바인딩 OK (b) 파일 DB → `read_only=True` 재오픈 → 호출 OK (c) 상대경로는 CREATE 시 `No files found` → 절대경로 필수 (d) 기본값 인자 `b := 1`·본문 스칼라 서브쿼리 read_only 호출 OK |
| P2 | `bsns_year` 관례 | 비12월 전 corp × 11011: `rcept_dt − (bsns_year, acc_mt 말일)` 중앙값 03월 90 · 06월 92 · 09월 90 · 11월 78 · 08월 85 · 10월 90(n≥10, 음수 0) → **종료 연도**. 02·05·04·01월(n 14·10·7·6) 중앙값 192~304일 → 후보 규칙 + 격리. 12월 24,031건 중앙값 87 · p95 273 · 음수 68 · 60일 미만 126. acc_mt 분포 12:3,373 · 03:32 · 06:29 · 09:9 · 02:7 · 04:6 |
| P3 | 키움 샤드 | 4 API × 2,605행/2,602티커, `status ∈ {done, empty(60, ka10014)}`, 요청창 티커당 1개 전 구간. listing 3,672 중 샤드 없는 티커 **1,070**. `n_rows ≥ cap` ka10014 2,089·ka20068 2,578·ka10008 2,594·ka10060 2,578 |
| P4 | credit 2010 이전 | 24,497행 · 1,750종목 · 2007-07-16~2009-12-30 (전체 8,404,204 · 3,175종목) |
| P5 | analyst | summary 1,612행 · fetched_date 2 · 810종목 / broker 9,717 · 3일 · 521종목 |
| P6 | 공시 신호 | 매매거래정지(전건) 11,970 · 정리매매 개시 349 · 관리종목 1,221 · 상장폐지 3,952 · 배당결정 19,275(+기재정정 1,993) · 기준일결정 3,260+411 · 주식배당 548 · 배당락 257·200 · 권리락 618·455·452 |
| P7 | WISE 커버 | `covered` 804 / `none` 1,762 (2,566). consensus_monthly distinct ticker 2,566(빈 행 포함) |
| P8 | 마스터·지수 | 거래일 4,094 · 재상장 2(036220: ~2016-05-04/2024-03-13~, 101970: ~2015-03-16/2025-03-28~) · ETF 1,416 티커, listing 에 0 · KOSPI SPAC 4·KOSDAQ 372 · 지수 KOSPI 51·KOSDAQ 40 · `stg_delisted_master` 78컬럼(`lstg_abol_dt`·`scts/kosdaq_mket_lstg_dt`·`idx_bztp_*_name_current`) |
| P9 | 파일시스템 | stage·equity 부모 동일 디바이스(`/dev/mapper/ubuntu--vg-ubuntu--lv`), 여유 282GB |
| P10 | 결산월·매핑 | `stg_company` 3,478행·observed_date 1(변경 이력 없음)·`acc_mt` NULL 0 · `stg_fin.corp_code` ∉ corp_map 0 · listing vs price (ticker,date) 집합 차 0/0 |
| P11 | 어휘·완결성 | listing distinct date 4,094(홀 0) · secugrp×stkcert_tp 전 이력: 주권/보통주 3,341 · 구형우선주 120 · 신형우선주 60 · 선박투자회사 49 · 부동산투자회사 34 · 외국주권 24 · 주식예탁증권 14 · 종류주권 14 · 투자회사 12 · 주식예탁증서 6 · 사회간접자본 2 · KR7 isin8 3,438 그룹 전부 보통주 1(3,632 티커) · 비KR7 KR8 16·HK0 15·KYG 8·USU 1 · pre-2010: short 58,211/674 · flow 7,609/1,269 · foreign 67,994/1,269 · lending 0 · KIS 유닛 `dataset` {credit 3,175 · flow 652 · loan 287 · master 652 · short 652}, status {ok, empty} |
| P13 | 문서 원본 재무표 | `document.xml` ZIP 은 보고서 본문 XML(부방 2024.12 원본 3파일·정정 2파일) — 재무상태표 18회·자산총계·당기순이익·매출액 값 텍스트로 존재. 원본·정정 ZIP 둘 다 로컬 보유 |
| P12 | 정지·관리·정정 라벨 | 정지 지정(비해제) 8,415 · 해제 2,287 · 지정및해제 동일일 1,214 · 관리종목 해제 공시 3 · KOSPI `sect_tp` 공란 942(소속부 없음) · 정기보고서 접두어 `[기재정정]` 11,942/6,763/3,287 · `[첨부추가]` 2,666 · `[첨부정정]` 969 · 비표준 종류(유동화전문회사·회계법인·기한연장·해외신고) · 기간 라벨 없음 586 · 정지 지정 후 첫 거래 중앙값 4일·p90 562일·재거래 없음 930/7,996 |

---

## 11. 미결 · 한계

- **TR 수익률**: 배당락일 원천 없음. 12월 결산 현금배당은 `기준일 = 회계연도 말 마지막 거래일, 배당락 = 직전 거래일` 관행(2023 배당절차 개선 전) `convention` 후보 — 팩터층 판단. 기본 수익률은 PR.
- **재무 원본 판본 — 구조화 API 에는 없고 문서 원본에는 있다**(사용자 지적 09-03): `fnlttSinglAcntAll` 은 (corp, year, reprt) 당 최신 판본만 준다(SPEC §2-18). 그러나 원본 접수의 공시서류(`document.xml` ZIP, `stg_doc_index` 174,309 · 원본 153,223 / 정정 18,013 확보)에는 재무제표 표가 그대로 들어 있다 — 실측(P13): 부방 사업보고서(2024.12) 원본 `20250320001612` 와 기재정정 `20260814004301` ZIP 모두 재무상태표 18회·자산총계 값 존재. 따라서 as-reported 복원은 **문서층 L1 파싱**(범위 밖) 문제이지 원리적 불가가 아니다. 현 4단계는 최신 판본 + `restated_unknown=true` 로 가고, 후속 슬라이스 `fin_asreported`(정정 있는 그룹 20,759건의 원본 ZIP 만 파싱, 또는 DART XBRL 원본파일 API `fnlttXbrl` — 저장소에 사용 이력 없음, 미확인)로 원본 판본을 붙일 수 있다. 그때까지 PIT 는 look-ahead 를 비랜덤 결측으로 바꾸며, 결측률 교차표를 인계한다.
- **관리종목 상태**: KOSDAQ 은 소속부로 PIT, **KOSPI 는 지정 신호만(해제 공시 3건) → 365거래일 창 플래그, 편향 KOSDAQ 쪽으로 비대칭**. 2026-09-01~ 는 양시장 상태.
- **정리매매**: 개시 공시 349건 = MS-04 추정 약 1,100건의 약 32%. 나머지는 `no_trade_run` 근사.
- **키움 샤드 로그**: `collected_at` 2일뿐 · 1,070 티커 미커버 · `cap` 의미 미확정 → `src_omitted` 는 보수적으로만. 키움 대차 잔고 단위 미측정.
- **KIS 투자자 주체 대응**: 명세 기반, 겹침 0 이라 검증축 없음.
- **결산월 변경**: 이력 원천 없음 → `period_end` 후보 규칙 + 격리로만 방어.
- **credit 2007~2009 · short 2008~2009 · flow/foreign 2009**: 캘린더 밖 격리.
- **컨센서스 선택편향**: WISE 804·v3 787 → `coverage_by_mktcap_quintile` 경고.
- **업종 과거 PIT 없음**: `induty_code`·`idx_bztp_*`(현재값), WISE 페이지 헤더(2026-09~), WICS 보류.
- **엔진**: 캘린더 포트·`MonthEndSession`·enum 확장·룩백 신호는 엔진 저장소 이슈(§7 한계).

---

## 12. 검수 기록 (v1 → v1.1, 2026-09-03)

Opus 리뷰어 3명(stage 계약·실행성 / PIT·편향·엔진 / 일관성·측정가능성) 30건 + 부수 지적. 전건 코드·서버 실측(P9~P12)으로 재검증했다.

| 출처 | 지적 | 반영 |
|---|---|---|
| PIT | `suspended` 술어·해제 축 없음 / `v_cum_adj` available 미필터 / 계수 정규화·방향 미정의 / `fiscal_month` 불변 선언 / 파생 컬럼 available / CA `ts` 미정 / 무거래 종목 사전 제외 = 생존편향 / `disclosure_version`↔`fin_std` 조인 키·접두어 / 정리매매 recall·`delist_date` 순서 / profile SQL 평가 불가·ohlcv basis | §4-1 상태 규칙·신호 표(P12 실측), §4-2·§5 정규화(곱셈)·`v_adj_price`, `fiscal_month_basis`+EG7, `available=max(구성)`, §7 `ts`=효력일·span 질의, §4-4 4키 조인·접두어·제외 종류, §4-1 KRX 우선 폐지일·`liquidation_window`·recall 게이트, §4-7 `source_stage_tables`·분위별 커버·ohlcv convention |
| 일관성 | `delist_date` fallback 하한 없음 / 컨센서스 PK vs 구간 2행 / §4-6 EG1·파티션 누락 / §8 EG0·EG7 누락 / 신호 술어·건수 불일치 / ADV20 창·profile 행 / `fs_div`·TTM 미정의 / 어휘(basis·sec_type·54·FACTORS 산식) / 과잉 6건 | `backfill_end` 조건, PK+`src`, §4-6 EG1·파티션, §8 머리말, P12 분해 수치, 창 통일·행 분리, CFS 우선·TTM NULL 규칙, 어휘 정정(WORKFLOW·FACTORS 동시). **과잉 판단**: `is_trading_day`·`calendar_version` 삭제, `price_matches_krx` → 실수 비율 반영. `universe_policy`(baseline 과 역할 다름)·`index_daily` 전량(1:1, KRX 업종지수 정본 축)·5단계·`opinion_broker_daily`(KICKOFF 개정 #6, 일별 적립)는 유지 |
| stage | KIS 유닛 status `ok` / span 원천에 ETF 누락 / 4-B `row_kind` 범위·grain / receipt_axis `rcept_no`·basis derived / 키움·KIS 주체 집합 상이 / 컬럼명 오기 3(`ticker`·`wght_pct`·`limit_exh_rt_pct`·`poss_stkcnt_shr`·`date`) / 5단계 EG1·`n_analyst`·unpivot·broker grain / listing 완결성 / short 축 pre-2010 / 매크로 문법 | §4-3 축별 어휘표, §4-1 span 원천·격자 ETF 제외 명시, §4-5 grain·row_kind 3테이블, §2 파티션식·§4-4 basis, §4-3 대응표+EG3 스코프, 실명 교체, §4-6 보강, P11 완결성 4,094·`data_gap` 예약, `_reject/pre_calendar` 공통(P11 건수), §5 `:=`·서브쿼리(P1d). 부수: `_pinned/` MANIFEST 복사, 카탈로그 `os.replace`, 대차 단위 분리 컬럼, `corp_event` dedup 등식 |

검수가 확인한 문제 없음: `ratio` 방향·임계 1.5 방침·분기 `period_end` 산식·`corp_cls` 배제·isin8+HK 단독·credit 시점 키·무거래일 종가 보존·재상장 다구간·fin_std 랙 1일·fin_map 21항목 일치·SHARE_COUNT_CHANGE 알림 전용.
