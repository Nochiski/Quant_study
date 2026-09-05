# Equity 층 워크플로우 (v1.1, 2026-09-03 — 5관점 검수 반영)

> stage(사실) 위에 equity(구조 정책)를 쌓아 **팩터 재료**와 **백테스트 PIT 패널**을 만드는 전 과정을 단계·게이트로 고정한다.
> 근거: `EQUITY_KICKOFF.md` · `STAGE_HANDOFF.md` · `STAGE_SPEC.md` §2~§5 · `STAGE_DESIGN.md` §2·§6·§7·§9 · ERD v0.1 아티팩트 · `src/backtest_engine/`(엔진 계약).
> v1 → v1.1 변경은 §7 검수 기록. **0단계 결정 5개는 권고안이다** — 승인 전이며, 결정에 매달린 절은 `[결정 N 전제]` 로 표시했다.

---

## 0. 정합성 — 이 층이 답해야 하는 질문

**"날짜 D에, 그 시점에 알 수 있던 정보만으로, 그날 실존한 전 종목의 상태를 줘."** (ERD v0.1)

### 0-1. 소비자 3 과 엔진 소비 경계 `[결정 5 전제]`

| 소비자 | 부르는 것 | equity 가 보장할 것 |
|---|---|---|
| 팩터 리서치 | 뷰 4종 + `dataset_profile` + `universe_policy` → 횡단면 패널 | 재료가 PIT·단위 명시·결측 3분류로 나간다. 윈저·z-score·중립화·정책 적용 판정은 팩터층 |
| 백테스트 엔진 | **어댑터 `adapters/equity_duckdb.py` 로 가격·유니버스·기업행위 3포트만**(`BarSource`·`UniverseSource`·`CorporateActionSource`) | 원주가 Bar + `CorporateActionEvent`(`ts` = 효력일) 조합. 재무·컨센서스는 엔진 포트가 없다(`ports/` 4종, `PriceField` 5종) — **팩터층이 뷰로 시그널 패널을 사전계산해 엔진 밖에서 주입**한다 |
| 라이브 시그널 | 같은 뷰, `:asof = 오늘` | 실시간 시세(원주가)와 같은 숫자 체계. 단, 계수의 `available_date` 가 라이브와 백테스트에서 같아야 한다(§3-2) |

엔진 사실(코드 확인): 세션 축은 Bar 합집합(`data/feed.py`), 체결은 다음 시가(`engine/pricing.py`), `CorporateActionType` 은 SPLIT·REVERSE_SPLIT·SHARE_COUNT_CHANGE 3종, `ratio` = 구주 1주당 신주 수, 검출 임계 1.5, `MonthEndSession` 미구현, `RunConfig` 에 asof·lag 없음, 새 어댑터는 `tests/test_bar_source_contract.py::BUILDERS` 등록이 계약이다.

### 0-2. 세 층의 책임 (SPEC §1 확장)

| 층 | 하는 일 | 하지 않는 일 |
|---|---|---|
| stage | 캐스팅·단위·결측 원인·중복 접기·공개일 **사실** | 조인·집계·정책 |
| **equity** | **구조 정책**: 크로스소스 조인 · 캘린더×유니버스 격자 · 판본 선택(PIT) · 조정계수 · 유니버스 **사실 + 정책표 보관** · 정정 링크 · 공개시점 카탈로그 | **통계 정책**(윈저·정규화·중립화) · 정책 **적용 판정** · 임계값을 팩트 행에 굽기 · 팩터 값 계산 |
| 팩터층 | 통계 정책 + 팩터 값 + `universe_policy` 적용 | 원장·stage 직접 읽기 |

원칙 6개(PIT 게이트 · 원주가 불변+조정계수 · corp/ticker 이축 · 결측은 결측 · 지식은 카탈로그 · 읽기 전용 뷰 계약 — PIT 뷰 4종 + 보조 뷰 3종)와 basis 어휘(stage 4종 measured / derived / default / unknown + equity 신설 convention)는 유지한다.

**정본 우선순위(2026-09-03 사용자 확정): KRX 가 주는 사실은 KRX 가 정본이다.** 가격·거래량·거래대금·시총·상장주식수·종목 유형·상장/폐지 구간·거래일·지수 전부. 다른 소스는 세 역할만 갖는다 — ① KRX 가 없는 영역의 정본(수급·공매도·대차·신용 = 키움/KIS, 재무·지분·배당 = DART, 컨센서스 = WISE) ② 폐지 종목 보완(`src` 딱지) ③ 검산축(KIS 수정종가). KRX 와 다른 소스가 어긋나면 KRX 값을 쓰고 불일치는 기록만 한다(키움 거래량 1,024행 등). KRX 의 한계(T+1 08:00 관측 상한 · 2026-08-20 백필 종료 · 2010-01-04 하한 · 업종분류 미수집 → DART `induty_code` 대용)는 그대로 equity 의 한계다.

### 0-3. 목적 ↔ 편향 ↔ 장치 ↔ 게이트 대응표

방향 = 안 막았을 때 백테스트 성과가 어느 쪽으로 틀리는가. 게이트 번호는 §2.

| 목적 · 위험 | 편향 (실측) | 방향 | equity 장치 | 게이트 |
|---|---|---|---|---|
| 생존편향 | 폐지 직전 20영업일 −95~−99% 소실 → 소형·저PBR −2.9%p/년 (MS-02) | 과대 | `security_span` · 폐지 909(=KIS 652 + 스팩 176 + 우선주 81) 전부 span 종료 · `coverage_gap` | EG1 · EG4 · **폐지 909 span 확보 게이트(§3-1)** |
| 정리매매·거래정지 잔류 | 정리매매 68건/년(최대 +182.61%) 약 1,100건이 유니버스에 잔류, 저변동성 롱사이드가 "변동성 0" 으로 설명 (MS-04·05) | 과대 | `universe_daily.no_trade_run`(직전 무거래 연속일, derived) + 공시 축 거래정지 신호(1회 측정 후 채택) · 관리종목은 2026-09-01~ 만, 이전 NULL | EG3 · §3-1 판단 |
| look-ahead (공개시점) | KRX T+1 08:00(관측 상한, I3 미프로브) · 수급·DART 등 lag_known=false | 과대 | 전 팩트 `available_date`+`basis` · `dataset_profile` **컬럼군별** 랙 · 뷰가 컬럼군별로 `≤ :asof − lag` 적용 · lag_known=false 는 권장 랙 ≥ 1 | EG2 · EG-C |
| look-ahead (정정 공시) | DART 는 (corp,year,reprt)당 **최신 판본 하나만** 준다(SPEC §2-18) — 원본 판본은 원리적 부재 | **결측 치환**: 정정 지연 종목이 D 시점에 비랜덤 결측(부실 집중) → 과대 | `disclosure_version`(원본↔정정, `correction_seq`) · `fin_std.has_correction`·`restated_unknown` · `rcept_dt ≤ D` | EG6 · EG4(결측 재현) · **PIT 결측률 교차표(baseline)** |
| look-ahead (컨센서스) | WISE 는 13개월 이력 재작성, 자체 수집은 2026-09-01~ · v3 과거분 2026-04-03~09-02 | 과대 | `available_date`: wise = min(fetched_date), v3 = collected_date · **`obs_month` 를 날짜 축으로 쓰는 것 금지** | EG6 · EG-C |
| 조정 오류 | `base_price` 계수 20%만 정수비, 계수는 가격·주식수 **두 축**(SPEC §3-1·3-2), 인적분할 000070 +10.70%p 룩백 유입, 207940 미검출 | 양방향 | 원주가 불변 + `adj_factor(price_factor, share_factor, factor_source, factor_ok, available_date)` · `factor_ok=false` 이벤트를 가로지르는 룩백은 팩터층이 결측 처리 | EG3(계수 항등) · EG8 · EG4 |
| 레짐 편향 | 키움 공매도 0일 행 생략 → complete-case 시 2020-06-30 커버 36.7% (SPEC §5-3) | 위기 구간 대형주 편중 | 격자 + `fill_kind` 3분류 · 판정 근거 `evidence_rate` 동반 | EG9 |
| 우선주 시총 누락 | 003540 55.9%, P/B 분모 최대 35.9% 과소 (MS-03) · HK0000 ISIN 8종목 과합병(SPEC §2-17) | 밸류 롱사이드 오류 | `corp_ticker`(isin8, KR7 만) · `v_firm_mktcap` | EG3 · EG4 |
| 컨센서스 선택편향 | v3 커버 787 종목(약 30%, 소형주 배제) | 리비전 팩터 대형주 편중 | `dataset_profile.coverage_*` · 시총 분위수별 커버율 기록 | §5 기록 |
| 총수익률 부재 | 배당락일 원천 없음(CA-05) → PR 만 가능: 저변동성 −4.35%p·밸류 +3.01%p·모멘텀 −1.10%p (PR-04) | 팩터별 부호 상이 | 배당 계수 **생성 안 함**, `dividend_event` 는 재료로만 · §6 한계 명시 | — |
| 재현성 | 같은 입력 다른 출력 / stage 재빌드가 과거 as-of 를 재작성(§2-18) | 비결정 | 입력 stage build 고정 + `_pinned/` 존속 · `content_hash` · **as-of 불변 검사** | EG0 · EG5 |
| 유니버스 정책 오염 | 파일럿 `halted` 26,384셀 잔류, 임계 정의 부재 (D6) | 과대 | 층 플래그를 굽지 않고 판정 입력값 + `universe_policy` 정책표 | EG3 · EG-C |

---

## 1. 공통 워크플로우 — 슬라이스 하나가 지나는 길

```
설계 확정(§3 표의 grain·입력·available 규칙·EG1 등식·게이트 조건 채움)
  → TDD: 손계산 픽스처 먼저 (fixtures/<table>.json)
  → 로컬 빌드: stage 슬라이스 픽스처(서버 stage 의 소형 절단본) 위에서 게이트 통과
  → 서버 실측: 읽기 전용 스크립트 scp → .venv/bin/python (stage 는 MANIFEST 경유, 맨 glob 금지)
  → 게이트 전량 통과 → MANIFEST 원자 교체 (실패 = 새 버전 폐기, 구 버전 무손)
  → 문서 §기록: 실측 수치 + baseline diff + findings(4요소 양식)
  → PR self-merge → 브랜치 삭제 → 배포 rsync
```

- **산출 위치** `~/quant-ledger/data/equity/<table>/` — stage 와 같은 골격(`MANIFEST.json` · `v=<build>/…` · `_meta.json` · `_reject/`) + `data/equity/baseline.json` + `data/equity/fixtures/`. stage 산출은 **읽기 전용**.
- **코드** `workspace/dongmin/src/equity/`. 재사용은 **`stage/manifest.py`·`stage/baseline.py` 의 파일 규약만** — `gates.py`·`build.py`·`rules.py` 는 원장 ATTACH·fanout 축에 묶여 있어 equity 전용 신규. 브랜치 `equity/<slice>`.
- **판본 규약** `[결정 2 전제]` MANIFEST `builds[]` 에 `inputs: {stg_x: build_id}` 를 추가한다. `BuildRecord` 에 `inputs`(기본 `{}`) 필드를 추가했다(09-03, stage 테스트 194 통과). `snapshot_id` 는 필수 필드 그대로 두고 equity 는 `""` 를 명시적으로 넘긴다. 고정한 stage 빌드의 `BuildRecord` 1건은 `_pinned/<stg_x>/MANIFEST.json` 에 복사한다(맨 glob 금지 유지). **고정한 stage build 는 keep=3 GC 로 삭제되므로** `data/equity/_pinned/<stg_table>/v=<build>/` 에 하드링크로 존속시킨다(같은 파일시스템, 비용 0). 이것이 stage 의 VACUUM 스냅샷을 대체한다.
- **파티션 클래스** 테이블마다 선언(§3-0 표): 일별 팩트 = `date_axis`(연도), DART 유래 = `receipt_axis`, 차원·카탈로그 = `whole`. EG5 의 `content_hash` 는 파티션 단위.
- **규모·메모리** 격자 기준면 ≈ 9.2M 셀(주권) / 10.9M(ETF 포함) × 팩트 테이블 5 → 코어만 약 4,800만 행. 서버 RAM 15GB, stage 는 `stg_fin` 단독 스필 15~18GB 를 겪었다(KICKOFF §4). 서버 빌드는 **테이블 직렬**(`flock`), 4단계는 계정 선별 후 피벗, `memory_limit` 는 baseline 에 기록.
- **뷰 카탈로그** `[결정 1 전제]` `equity.duckdb` 는 데이터 없이 뷰·테이블 매크로만 담고 빌드마다 `MANIFEST.current_build` 경로로 재생성. 절대경로 이식성·read_only 매크로 호출·1.5.5 포맷 호환은 **0단계 실측 항목**(§3-0).
- **금지** 원장 SQLite 직접 읽기 · stage 밖 소스 · 임계 상수 본문·코드 하드코딩(전부 `baseline.json`·`universe_policy`) · 미수집을 0 으로 적재 · `_current` 테이블로 과거 필터 · **`obs_month`·`bsns_year` 를 날짜 축으로 사용** · latest 판본 선택.

---

## 2. 게이트 체계 — EG0~EG9 + EG-C `[결정 2 전제]`

stage G0~G9 골격(폐기형/격리형 · `skip(사유)` · 상수는 baseline 에서만)을 계승하되 **내용은 조인·집계 층에 맞게 재정의**한다. 술어는 duckdb SQL 로 pass/fail 이 나와야 하고, 상수는 `baseline.json` 의 `{table, metric, sql, value, measured_at}` 를 참조한다. 본문에 숫자를 적지 않는다.

| # | 게이트 | 유형 | 술어 | 첫 빌드 |
|---|---|---|---|---|
| EG0 | 입력 고정 | 폐기형 | `inputs` 의 stage build 가 `_pinned/` 에 존재 · 선언 컬럼 실재(parquet 스키마 대조, **stage 컬럼명** 기준 — 예 `close_krw`, `stck_prpr` 아님) · 입력 `_meta.gates` fail 0. 맨 glob 검사는 CI lint 로 분리 | 실행 |
| EG1 | 격자 등식 | 폐기형 | **§2-1 표의 테이블별 등식**. 표에 등식이 없는 테이블은 착수 금지 | 실행 |
| EG2 | PIT 불변식 | 폐기형 | 전 팩트 행 (`available_date` NOT NULL) ∨ (`basis='unknown'`) · `available_date ≥ 내용일`(`corp_event` 는 `announce_date` 축, `effective_date` 아님) · `lag_known=false` 원천 테이블은 `dataset_profile` 행 필수 ∧ `recommended_lag_days ≥ 1` · basis ∈ {measured, derived, convention, default, unknown} | 실행 |
| EG3 | 키·불변식 | 폐기형 | PK 유일 · `corp_ticker` corp 당 보통주 1(HK 예외 제외) · `security_span` 비중첩 · 시총 불변 이벤트(split·bonus·stock dividend)는 `price_factor × share_factor = 1` · `v_firm_mktcap` = Σ 종류주 시총(003540 포함) · Σ 투자자 12주체 순매수 = 0 ± 허용오차(baseline) · 티커 6문자 TEXT · `induty_code` 공란 0 | 실행 |
| EG4 | 골든 픽스처 | 폐기형 | `data/equity/fixtures/<table>.json` 강제. 최소 집합은 §3 각 단계 픽스처 행. 기대값의 출처가 look-ahead 테이블(`stg_v3_revision_compare`, `stg_consensus_matrix` lookback 컬럼)이면 무효 | 실행 |
| EG5 | 회귀·재현성 | 폐기형 | (a) `inputs` 불변이면 파티션 `content_hash` 전량 동일 · (b) `inputs` 변경 시 EG1 등식이 새 입력으로 성립 · (c) **as-of 불변**: 고정 asof 표본(baseline: 날짜 × 종목)에서 `v_fin_latest`·`v_consensus`·`v_universe` 결과 차이가 전부 "새 `rcept_dt`/`fetched_date` > asof" 로 설명, 설명 불가 행 = 0. `rcept_dt ≤ asof` 값 변경 건수는 baseline 등재 | `skip(no_baseline)` |
| EG6 | 판본 선택 | 폐기형 | 같은 자연키 다판본에서 선언 규칙으로 하나만: 컨센서스 wise = min(fetched_date) · v3 = collected_date · 재무 `rcept_dt ≤ D` · 마스터 first_write_wins. "max 선택" 행 0 · `is_latest` 부재 · 정정 그룹 링크 표본 오판율 ≤ baseline | 실행 (오판율은 `skip(no_baseline)`) |
| EG7 | 범위·부호 | **격리형** | 가격·계수 > 0 · 비율 범위 · 부호 교차. 범위 밖은 NULL + `miss_kind='out_of_range'` + reject. 격리 비율 임계는 stage 초기값(0.1%)을 첫 빌드 기본으로 쓰고 baseline 으로 이관 | 실행 |
| EG8 | 교차 소스 | 폐기형 | `krx_kis_close_ratio` 대 누적계수 일치율 ≥ baseline(KIS `close_krw` 수정종가 ÷ KRX 원주가 — 수집 시점 앵커라 재수집 시 baseline 재측정) · 분할일 수정수익률 점프 절댓값 ≤ baseline · 키움 flow ⋈ KIS flow_split 겹침 0 · v3 ⋈ WISE 겹침 구간 일치율 ≥ baseline · 이벤트 탐지 recall: 기준가≠전일종가 5,214건 중 `corp_event` 매칭율 ≥ baseline | `skip(no_baseline)` · ETF 는 `sec_type` 축으로 `skip(no_cross_source)` |
| EG9 | 레짐 커버리지 | 폐기형 | 격자 테이블만. 측정일 3개(baseline 상수) 커버율 ≥ baseline **∧** `src_omitted` 판정 셀의 로그 근거율 `evidence_rate` ≥ baseline(근거 없는 구간은 `not_collected` 강등) · 월별 커버율 ↔ 시장 월수익률 상관 절댓값 ≤ baseline · "미수집→0" 행 0 · 컨센서스 월별 커버 종목수 급락·급증(직전 중앙값 대비, baseline) | `skip(no_baseline)` |
| EG-C | 소비자 계약 | 폐기형(테스트) | ① `tests/test_bar_source_contract.py::BUILDERS` 에 `equity_duckdb` 등록 후 전 케이스 통과 ② `v_universe(:d,'all', lag=profile)` 티커 집합 = `stg_listing_daily`(d − lag) 집합 ③ 재상장 2종 Membership 2구간, `coverage_gap` 은 구간을 끊지 않고 `UniverseQuery.end` 를 08-20 으로 잘라 거절 ④ 분할 픽스처 포함 run 의 누적수익률 = 조정가 손계산(수량·현금 이중조정 0) ⑤ 거래정지 종목 섞인 다종목 BarQuery 가 OK + `dropped_rows > 0`(어댑터는 종목별 질의 구간을 `security_span ∩ [start,end]` 로 좁힌다 — 사전 제외 금지) · ⑩ 폐지 909 중 무작위 20종목 포함 전 구간 BarQuery 가 OK ∧ 반환 종목 집합 = 요청 집합 ⑥ 샘플 팩터 4개(V01·F01·M01·G05)가 profile 랙 적용 상태에서 손계산과 일치(허용오차 baseline) ⑦ `:asof` 를 과거로 두면 그 뒤 공시·이벤트·판본이 안 보임 ⑧ profile 랙을 바꾸면 컬럼군별로 뷰 결과가 바뀜 ⑨ `obs_month` 로 계산한 G05 는 `v_consensus` 로 재현되지 않음 | 실행 |

첫 빌드에서 `skip(no_baseline)` 인 게이트는 측정치를 `_meta.gates[].metrics` 에 남기고, 사람이 승인해 baseline 에 넣은 뒤 2회차 빌드가 정식 통과다.

### 2-1. EG1 등식 — 테이블별

| 테이블 | 행수 등식 (좌변 = equity 산출) |
|---|---|
| `corp` | = distinct corp_code(`stg_corp_map`) |
| `security` | = distinct ticker(`stg_listing_daily` ∪ `stg_etf_price_daily`) |
| `security_span` | 비중첩 ∧ Σ span 거래일 = count(ticker,date)(`stg_listing_daily` ∪ `stg_etf_price_daily`) · 재상장 구간 수 = baseline |
| `corp_ticker` | = `security` 행수 (`link_basis` ∈ {isin8, corp_map, none} — ETF·미매핑은 corp_code NULL) |
| `trading_calendar` | = distinct date(`stg_index_daily`) + gap 거래일(KIS·키움 date 축, ≤ 백필일) |
| `index_daily` | = `stg_index_daily` 행수 (1:1) |
| `universe_daily` | = Σ span 거래일(≤ 08-20) + gap 거래일 × gap 직전 활성 티커 수 |
| `price_daily` | = `stg_price_daily` + `stg_etf_price_daily` (서로소) |
| `corp_event` | = Σ 선언 원천 행(DS005 15종 · `stg_capital` 비집계행 · 공시 락일·배당결정 신호) − dedup(동일 ticker·event_type·effective_date, 건수 `_meta` 기록) |
| `adj_factor` | = `corp_event` 중 계수 대상 유형 행수 |
| `flow_daily`·`short_daily`·`credit_daily` | 격자 행수 = Σ_d 활성 주권 티커 수(ETF 제외) · 원장 행 = 격자 매핑 + `_reject/pre_calendar`(2010-01-04 이전: short 58,211·flow 7,609·foreign 67,994·credit 24,497) + `_reject/off_grid` · **키움 13주체 컬럼 전부 계승**(flow) |
| `fin_std` | = count distinct (corp_code, bsns_year, reprt_code, fs_div) with ≥1 `account_std=true` 행 |
| `disclosure_version` | = 정기보고서 접수 행수(원본 + 정정, group 181,106 기준) |
| `dividend_event` | = distinct (corp, bsns_year, reprt_code, stock_knd) 비집계 |
| `holder_daily` | = `stg_holder_elestock` + `stg_holder_majorstock` (1:1, grain = stage 자연키 + `src`) |
| `ownership_snapshot`·`shares_outstanding`·`treasury_stock` | = distinct(요청축 + 종류축) 중 `row_kind<>'aggregate'` (원천 `stg_hyslr`·`stg_shares`·`stg_tesstk` — `row_kind` 는 이 3테이블에만 있다) |
| `audit_opinion` | = `stg_audit` 행수 (grain = corp·bsns_year·reprt_code·bsns_year_label, 1:1) |
| `consensus_daily` | = distinct (ticker, obs_month, target_period, metric, **src**) — v3 wide 8지표 unpivot + wise |
| `opinion_daily`·`opinion_broker_daily` | = `stg_analyst_summary` + v3 opinions (grain + `src`) · `stg_analyst_broker` (1:1, grain 에 `opinion_date` 포함) |
| `dataset_profile`·`universe_policy` | 선언표 — 행수는 EG2 커버 조건으로 대체 |

---

## 3. 단계별 워크플로우

각 단계 표는 (목적 · 입력 · 산출 · 작업 · 판단 · 픽스처 · 통과 조건) 골격이다. **통과 조건이 비어 있으면 착수 금지**. 기록 항목은 §5.

### 3-0. 0단계 — 설계 확정 (코드 0)

| 항목 | 내용 |
|---|---|
| 목적 | `EQUITY_DESIGN.md` v1 + ERD v0.2 확정, 적대적 검수 1회 |
| 입력 | KICKOFF §1 표 · HANDOFF §4 · SPEC §2~5 · ERD v0.1 · 이 문서 §7 검수 기록 |
| 산출 | `EQUITY_DESIGN.md` v1(아래 테이블 목록 전부에 grain·입력·available 규칙·파티션 클래스·EG1 등식·게이트 조건) · ERD v0.2 · 결정 5개 기록(선택/대안/이유) · `manifest.py` 필드 변경 · `FACTORS.md` 정정 |
| 테이블 목록 | ERD 14(`corp`·`security`·`trading_calendar`·`price_daily`·`universe_daily`·`adj_factor`·`corp_event`·`flow_daily`·`short_daily`·`credit_daily`·`fin_std`·`consensus_daily`·`opinion_daily`·`dataset_profile`) + `security_span`·`corp_ticker`·`universe_policy`·`index_daily`·`disclosure_version`·`dividend_event`·`holder_daily`·`ownership_snapshot`·`shares_outstanding`·`treasury_stock`·`audit_opinion`·`opinion_broker_daily`. 뷰: `v_universe`·`v_cum_adj`·`v_fin_latest`·`v_consensus`·`v_firm_mktcap`·`v_adj_volume` |
| 판단 | 뷰 매크로 시그니처(랙 인자 포함) · `fin_std` 표준 계정 = **팩터 ID × 필요 계정 매트릭스**(V05 감가상각·차입금, V07 현금성자산, Q05 CAPEX, Q06 부채총계, Q07 이자비용, Q08 NOA, G04 가중평균주식수 — `stg_fin.account_std` 코드 집합에 실재하는지 확인, 없으면 해당 팩터 미착수 명시) · `corp_event` 유형 어휘 ↔ 엔진 `CorporateActionType` 매핑표(ratio = 신주/구주, 임계 1.5 미만 이벤트 전달 방침, 격리 플래그 표현) · `fill_kind` 어휘와 stage `miss_kind` 공존 규칙 |
| 실측 항목(콜 0) | **완료 2026-09-03 → `EQUITY_DESIGN.md` §10**: duckdb 매크로(in-memory·read_only OK, 상대경로 불가) · `bsns_year` = 종료 연도 · 샤드 status {done, empty}, 1,070 티커 미커버 · credit 2010 이전 24,497행 · analyst fetched_date 2~3일 · 공시 신호 매매거래정지 11,970·정리매매 개시 349·관리종목 1,221·배당락 257·권리락 1,073+ · WISE 커버 804 |
| 통과 조건 | 결정 5개가 DESIGN §결정 기록에 "확정"(선택/대안/이유 3요소) · 검수 findings open 0 · **§3 산출 열거와 DESIGN 테이블 목록의 집합 차 = 0** · 전 테이블에 EG1 등식·파티션 클래스·available 규칙 존재 · 팩터 ID × 계정 매트릭스 존재 · `FACTORS.md` 정정 3건(§9 신용잔고율 → 가능, 밸류 빈도 → 분기, 정본 수 54 확정 = 50 + F09 신용잔고율·G09 목표가 리비전·G10 커버리지·R04 베타, 구 평면 F## 대응표 §12 — 09-03 반영) · 1단계가 2~5 에 넘기는 계약 컬럼 표 고정 |

**결정 5개 (권고안 — 승인 대기)**

| # | 선택 | 대안 | 이유 |
|---|---|---|---|
| 1 산출 형식 | parquet 세트 정본 + `equity.duckdb` 뷰 카탈로그(데이터 없음) | KICKOFF 권장 duckdb 단일 파일 | 입력이 불변 parquet 이라 build 고정이 스냅샷 · 단일 작성자 락 회피 · 파티션 해시 재현성 · 09-01 사용자 방향(equity=parquet). 매크로 실측 후 확정 |
| 2 판본·게이트 | stage 골격 계승, 내용 재정의(§2) + `inputs`·`_pinned/` | stage 게이트 그대로 | 조인층은 1:1 등식·원장 ATTACH 가 없다. keep=3 GC 가 고정 입력을 지운다 |
| 3 팩터 ID | `FACTORS.md` 정본 54(=50 + F09·G09·G10·R04) + 정정 3건 + §12 대응표 | DATA_CATALOG 평면 F## | 계산식·원재료·시작연도 보유. F06 충돌(공매도 거래비중 vs 베타) 해소 |
| 4 유니버스 | 사실 컬럼 + `universe_policy` 정책표(equity 보관, 팩터층 적용), 임계 = 횡단면 분위수 | 파일럿식 `in_micro/wide/core` 플래그 | D6 재발 방지. 분위수는 16년 명목 드리프트 회피. 값은 서버 실측 후 |
| 5 엔진 경계 | 엔진 어댑터 = 가격·유니버스·CA 3포트, 재무·컨센서스는 팩터층 시그널 패널 주입 | 엔진에 `FundamentalSource` 포트 신설 | 엔진 `ports/` 4종·`PriceField` 5종뿐. 포트 신설은 엔진 저장소 별도 이슈 |

### 3-1. 1단계 — 종목마스터 · 유니버스 · 캘린더 · 지수 `[결정 4 전제]`

| 항목 | 내용 |
|---|---|
| 목적 | "그날 실존한 전 종목" 의 정답지 + 2~5 단계가 쓰는 계약 컬럼 |
| 입력 | `stg_listing_daily`(pit: `list_date`·`market`·`secugrp`·`sect_tp`·`stkcert_tp`·`isin`·`par_value_krw`·`list_shrs`) · `stg_etf_price_daily`(ETF 존재 구간) · `stg_master_daily`(coverage_from 2026-09-01) · `stg_delisted_master`(652, `_current`) · `stg_corp_map`(3,478) · `stg_company`(`acc_mt`·`induty_code`) · `stg_price_daily`(무거래 연속일) · `stg_index_daily`(`IDX_CLSS`·`IDX_NM`·`BAS_DD`) · gap 구간 date 축: `stg_flow_split_daily`·`stg_credit_daily` |
| 산출 | `corp`(corp_code·name·fiscal_month·`induty_code`·`induty_class`) · `security`(ticker·corp_code·name·`sec_type` 10종 common·preferred·etf·spac·reit·foreign·dr·fund·ship_fund·other — 원천 `stkcert_tp`·`secugrp`·`sect_tp`·name, ETF 는 `list_date` NULL basis=unknown) · `security_span`(ticker × 구간) · `corp_ticker`(isin8, KR7 만; `HK0000` 8종목은 단독 corp) · `trading_calendar`(date·prev_td·next_td·calendar_source) · `index_daily`(index_key × date · close_pt · `available_date`) · `universe_daily`(date × ticker: status(listed·suspended·delisted·coverage_gap)·market·sec_type·admin_flag·mktcap_krw·adv20_krw·listing_age_days·`no_trade_run`·`available_date`·`available_basis`) · `universe_policy`(policy·rule·threshold·basis·version) |
| 작업 | 코넥스 제외(수집 자체 없음, no-op) · 재상장 구간 분리 · `coverage_gap` = 2026-08-21~백필일, 캘린더는 다른 축 date 로 연장하되 span 은 08-20 에서 멈춤 · `corp_cls` 과거 필터 금지 · `build_bridge.py` 는 **개념만 계승**(prefix5 방식·원장 직접 읽기라 재사용 불가), isin8 신규 · ADV20 창 = [D−19, D] 거래일 값을 저장하고 랙은 뷰가 D 를 옮겨 적용 · 관리·정지 09-01 이후만, 이전 NULL |
| 판단 | 정책 3종 분위수 초기값(서버 실측: 연도별 시총·ADV20 분포) · `stg_disclosure.report_nm` 거래정지·정리매매 공시 추출 재현율(0단계 실측) → 되면 D 시점 신호 컬럼 채택, 안 되면 `no_trade_run` 만 · ETF·스팩·리츠는 유니버스 행으로 두고 정책으로 제외 |
| 픽스처 | 재상장 2종 span 2행 · 003540 corp_ticker 1:N · 900050·900060 HK 단독 · `0001A0` TEXT · 2026-08-21 이후 coverage_gap · 폐지 1 의 마지막 거래일 = span 종료 · 우선주 폐지 1(81 중) span 종료 · 무거래 연속일 픽스처 1 |
| 통과 조건 | EG0 · EG1(§2-1 의 corp·security·security_span·corp_ticker·trading_calendar·index_daily·universe_daily) · EG2(`universe_daily`·`index_daily` available/basis) · EG3(corp 당 보통주 1, `induty_code` 공란 0) · EG4 · **폐지 909 전부 span 종료 보유**(`stg_delisted_master` 652 + listing 소멸 257) = 위반 0 · `corp_ticker` 매핑률 ≥ baseline · EG-C ② |

### 3-2. 2단계 — 가격 정본 · 기업행위 · 조정계수 (1단계 의존)

| 항목 | 내용 |
|---|---|
| 목적 | 원주가 불변 + 계수 두 축 분리로 백테스트·라이브 패리티 |
| 입력 | `stg_price_daily`(9,201,516) · `stg_etf_price_daily`(1,688,735) · `stg_listing_daily`(`par_value_krw`·`list_shrs`) · `stg_credit_daily.close_krw`(수정종가, 검산축) · `stg_capital`·`stg_event_*`(DS005 15종) · 1단계 `security`·`trading_calendar` |
| 산출 | `price_daily`(원주가 O/H/L/C·volume·value_krw·mktcap_krw·shares_out·`price_kind`·`krx_kis_close_ratio`(실수, 판정은 EG8)·`available_date`) · `corp_event`(event_id·ticker·event_type·announce_date·effective_date·ratio·rcept_no·`available_date`) · `adj_factor`(event_id·ticker·effective_date·**price_factor·share_factor**·factor_source·factor_ok·`available_date`·`available_basis`) · 뷰 `v_cum_adj`·`v_adj_volume`(share_factor 역수) |
| 작업 | 무거래일 종가 보존(`volume=0` = 기준가) · O/H/L NULL 유지 · 계수는 SPEC §3-2 두 축: 시총 불변 이벤트는 price=1/share, 유증·유상감자·인적분할·합병은 두 축 상이 · 효력일은 KRX `list_shrs`·`par_value_krw` 변화로 교차 확정(엔진 검출기는 PARVAL 없음·임계 1.5 → **신규 구현**) · 계수 `available_date` = min(원인 공시 `rcept_dt`, 효력일 + KRX 관측 랙), KRX 관측만이면 basis=derived · 인적분할·시그니처 불일치는 `factor_ok=false`(전파창 상수 없음 — 팩터층이 가로지르는 창을 결측 처리) · ETF 는 `security.sec_type` 로 구분해 같은 테이블 |
| 판단 | 배당 계수는 **생성하지 않는다**(배당락일 원천 부재, §6) · OHLCV available = 당일(default) · 시총·주식수 익일은 `dataset_profile` column_scope · 엔진 어댑터 노출 정책: `price_kind='reference'`·`volume=0` 행은 Bar 로 내보내지 않고 `dropped_rows` 보고, `open` NULL 을 close 로 채우지 않음, 구간 전체 무거래 종목은 사전 제외 · `corp_event.event_type` → `CorporateActionType` 매핑표(0단계) |
| 픽스처 | 삼성전자 2018-05-04 50:1(원주가 2,650,000 → price 1/50·share 50, 분할일 수정수익률 점프 0) · 무상증자 1.2:1(엔진 임계 미만 — 계수는 있고 엔진 전달 방침 검증) · 감자 1 · 인적분할 **207940**(`factor_ok=false`) · ETF 1 · `price_kind=reference` 1 · 계수 available: KRX 관측만인 이벤트 1 |
| 통과 조건 | EG0 · EG1(`price_daily`·`corp_event`·`adj_factor`) · **EG2**(`corp_event` announce 축, `adj_factor` available ≥ announce_date) · EG3(시총 불변 이벤트 price×share=1, `v_firm_mktcap` 합산) · EG4 · EG7 · EG8(`krx_kis_close_ratio` 일치율 ≥ baseline, 분할일 점프 ≤ baseline, 탐지 recall ≥ baseline) · EG-C ④·⑤ |

### 3-3. 3단계 — 격자 · 결측 3분류 (1단계 의존)

| 항목 | 내용 |
|---|---|
| 목적 | 레짐 편향 제거 — "없음" 을 0 / 결측 / 미수집으로 가르되 **근거 없는 0 은 만들지 않는다** |
| 입력 | `stg_flow_daily_kiwoom`(13주체 `_krw`) + `stg_flow_split_daily`(KIS, 폐지 커버) · `stg_foreign_daily`(보유·`limit_exh`) · `stg_short_daily_kiwoom/kis` · `stg_lending_daily`(키움 대차) + **`stg_loan_daily_kis`(KIS 대차 — 신용 아님)** · `stg_credit_daily`(융자 `whol_loan_rmnd_stcn_shr`·대주 `whol_stln_rmnd_stcn_shr`) · 로그 `stg_shards_kiwoom`(status 실측 어휘 `done`/`empty` — `rules_kiwoom.py` 주석의 ok/truncated 는 오기)·`stg_units_kis`·`stg_calls_kis` · 1단계 `universe_daily`·`trading_calendar` |
| 산출 | `flow_daily`(**13주체 전 컬럼** + foreign_ratio_pct + limit_exh_rt, `src`) · `short_daily`(공매도 + 대차잔고: 키움 ∪ KIS, `src`) · `credit_daily`(융자·대주, available = 거래일 default, `stlm_date` 는 profile 랙 근거) · 공통 `fill_kind` STRUCT(kind·evidence) |
| 작업 | 격자 = `trading_calendar` × `universe_daily` 활성 → 원장 행 매핑 → 빈 셀 판정: 샤드/유닛 요청창 커버 ∧ **`status='done'`** ∧ KRX 가격 행 존재일 → `src_omitted`(0 + evidence) / 샤드 `empty` → `empty_response`(NULL) / 로그 부재 → `not_collected`(NULL). 샤드는 티커당 전 구간 1개라 창 내부 미수신은 판별 불가(DESIGN §4-3) · 폐지 종목은 KIS 유닛 축으로 판정 · 격자 밖 원장 행(캘린더 하한 2010-01-04 이전 credit 2007-07-16~, 유니버스 밖)은 `_reject/` 격리 + 건수 `_meta` · 단위 접미사 계승 · `limit_exh_rt` 단위 미측정 → profile 행 |
| 판단 | 키움 샤드는 2,602 티커만 덮는다(0단계 실측: listing 3,672 중 1,070 티커 샤드 없음 → KIS 유닛 축, 그것도 없으면 `not_collected`). EG9 커버율 미달 시 **임계를 낮추지 않고 한계로 기록** |
| 픽스처 | 2020-06-30 공매도 0 종목 1 → `src_omitted`(evidence 있음) · 로그 없는 셀 1 → `not_collected` · `truncated` 샤드 셀 1 → `not_collected` · 폐지 종목 KIS 만 있는 날 → `src='kis'` · 키움 `close_krw` abs+`_dir` 1 · 12주체 합 = 0 항등 1 |
| 통과 조건 | EG0 · EG1(격자·컬럼 수) · **EG2**(lag_known=false 원천 → profile 행 필수) · EG3(12주체 합 항등) · EG4 · EG7 · EG8(키움⋈KIS 겹침 0) · **EG9**(커버율 ∧ evidence_rate ∧ 상관 ∧ 미수집→0 행 0) |

### 3-4. 4단계 — 재무 PIT (1단계 의존)

| 항목 | 내용 |
|---|---|
| 목적 | "그 시점에 알려져 있던 최신 재무" — 공시일·정정·비12월 결산까지 PIT. 원본 판본은 없다(§2-18) |
| 입력 | `stg_fin`(15,375,024, 자연키 8컬럼, `account_std`) · `stg_rcept_dt_map` · `stg_disclosure`(`is_correction`·`report_nm`·`rcept_dt`) · `stg_doc_index`(`zip_ok`) · 1단계 `corp`(fiscal_month·induty_code)·`corp_ticker` |
| 산출 | `fin_std`(corp × period_end × report_code × fs_div: 매트릭스 계정 wide · `is_months` · CF `_ytd` · `revenue_basis` · `has_correction` · `restated_unknown` · `rcept_no` · `available_date=rcept_dt`(derived)) · `v_fin_latest` 뷰(TTM 포함, CFS 우선) · `disclosure_version`(group_key × `correction_seq`: rcept_no·corrected_at·`link_basis`) |
| 작업 | 계정 선정은 0단계 매트릭스 · `account_std=false` 조인 금지 · **손익 = `thstrm_amount` 그대로(분기보고서 전부 3개월, SPEC §2-7), Q4 = 연간 − Σ(1Q,2Q,3Q); CF = `_ytd` 보존 + 전분기 차감 파생** · `is_months` 계승(`build_panel.py`) · 비12월 105사 asof 규칙 3줄 이식(`finalize.py` 는 import 부작용으로 재사용 불가) + `bsns_year` 라벨 관례 실측 후 확정 · 정정 그룹 = (corp_code, 보고서 종류, `report_nm` 끝 기간 라벨) with **DESIGN §5 문자열 정규화 순서 고정** · 다중 정정은 `correction_seq` · 금융업 470사 `revenue_basis` · `is_krw` 만 |
| 판단 | `link_basis='grouped'` → L1 파싱 후 `parsed` 교체 · 오판율 표본 N(baseline)은 확보된 정정 ZIP 첫 장 정정신고 표와 대조 |
| 픽스처 | 삼성전자 FY2025 1Q+2Q = 반기 손익 누계(원 단위) · 비12월 1사 asof · **정정 지연 종목(두산에너빌리티 FY2020, 1,232일)의 D 시점 결측 재현** · 정정 2회 그룹 1(`correction_seq` 2) · 은행 1 revenue_basis · 접수지연 max 2,875일 사례의 available |
| 통과 조건 | EG0 · EG1(`fin_std`·`disclosure_version`) · EG2(available = rcept_dt ≥ period_end, 참조표 미스 0) · EG3 · EG4 · EG6(`rcept_dt ≤ D` 선택 전수, 그룹 오판율) · EG7 · **PIT 결측률 교차표(has_correction × 접수지연 분위수) baseline 등재** |

### 3-4B. 4-B단계 — DART 보조 PIT: 지분 · 감사 · 주식총수 · 자사주 · 배당 (1단계 의존, 4단계와 병렬)

| 항목 | 내용 |
|---|---|
| 목적 | FACTORS E01~E04·E08·I01~I05·V06 재료. 4단계와 같은 rcept_dt PIT 기계를 쓴다 |
| 입력 | `stg_holder_elestock`(32,668) · `stg_holder_majorstock`(22,209) · `stg_hyslr`(228,226, `row_kind`) · `stg_shares`(97,194, `row_kind`) · `stg_tesstk`(328,704, `row_kind`) · `stg_audit`(93,037, `adt_opinion_class`) · `stg_dividend`(384,232, `se` 라벨 × `stock_knd`) · `stg_rcept_dt_map` · 1단계 `corp_ticker` |
| 산출 | `holder_daily`(ticker × rcept_dt × 보고자: 지분 전·후, `available_date=rcept_dt`) · `ownership_snapshot`(corp × 기준: 최대주주 지분율) · `shares_outstanding`(corp × 기준: 발행·자기·유통) · `treasury_stock`(corp × 기준: 취득·처분·소각, 방법 3단) · `audit_opinion`(corp × 사업연도: 원문·class) · `dividend_event`(corp × 기준 × stock_knd: `dps_krw`·`cash_total_krw`·`yield_pct` — **기준일·배당락일 없음, 계수 재료 아님**) |
| 작업 | `row_kind='aggregate'` 제외 후 합산 · `se` 라벨 → 컬럼 wide(단위는 라벨) · 지분 롤링 2년 창 경계 기록 · available 전부 rcept_dt(참조표) |
| 픽스처 | 자사주 집계행 포함 원장 → 합산 1건 · 비적정 감사의견 1 · 5% 보고 1 · 배당 `se` 3종 wide 1 · 롤링 창 첫날 1 |
| 통과 조건 | EG0 · EG1(6테이블) · EG2 · EG3 · EG4 · EG6 · EG7 |

### 3-5. 5단계 — 컨센서스 PIT (1단계 의존)

| 항목 | 내용 |
|---|---|
| 목적 | 리비전 방향이 알파다 — 관측점별 최초 관측값을 지키고 두 구간(v3 / WISE)을 구분해 이어붙인다 |
| 입력 | `stg_consensus_monthly/annual/quarterly/matrix`(fetched_date **2026-09-01~**) · `stg_analyst_summary`(ticker × fetched_date) · `stg_analyst_broker`(cTB24) · `stg_v3_revision_daily`(2026-04-03~09-02, 787 종목, `coverage_degraded`) · `stg_v3_analyst_opinions` · `stg_v3_consensus_annual` · `stg_wise_coverage` · 1단계 `corp_ticker` |
| 산출 | `consensus_daily`(ticker × obs_month × target_period × metric: est_mean/min/max/n · `unit` · `src` · `available_date`) — **구간 2행 규칙**: `src='v3'` available = `collected_date`(NULL 은 `base_date` + `coverage_degraded`) / `src='wise'` available = min(fetched_date) ≥ 2026-09-01 · `opinion_daily`(원천 `stg_analyst_summary` ∪ v3) · `opinion_broker_daily`(직전 보고서일 컬럼 동반) |
| 작업 | v3 일별 (ticker, base_date, target_period) → obs_month 그레인 변환 규칙 명문화(월내 min collected_date) · latest 금지 · 단위 컬럼 · 2027E·2028E 보존 · v3 ↔ WISE 겹침 구간 값 대조 · broker `change_pct` 는 간격 없이 해석 금지 · 커버 밖은 결측 |
| 판단 | `stg_fin_wise` `val_q*` 슬롯·`lookback` 해석은 팩터층으로 이관(5단계 범위 밖) · `_daily` 명명 유지(0단계 실측: summary fetched_date 2일·810종목, broker 3일·521종목 — 일별 적립) · WISE 커버 804(`stg_wise_coverage` covered) |
| 픽스처 | 같은 (ticker, obs_month, target) 에 fetched_date 3판본 → min 선택 · **서로 다른 두 fetched_date 관측점에서 손계산한 리비전**(revision_compare·matrix lookback 유래 수치 금지) · EPS 원 vs 매출 억원 unit · v3→WISE 경계일 연속 · v3 collected_date NULL 행 |
| 통과 조건 | EG0 · EG1(`consensus_daily`·`opinion_*`) · EG2(구간별 basis) · EG4 · EG6(max 선택 0) · EG8(v3⋈WISE 일치율) · EG9(커버 종목수 급락·급증) · EG-C ⑨ |

### 3-6. 6단계 — 공개시점 대장 `dataset_profile`

| 항목 | 내용 |
|---|---|
| 목적 | 공개 시점·커버리지 지식을 테이블로 — 엔진·팩터층이 SQL 로 읽는 랙·시작일 |
| 입력 | HANDOFF `lag_known` · DESIGN §6 · SPEC §2-8·§2-19·§2-20(08:00 은 관측 상한, I3 미프로브) · 1~5 실측 |
| 산출 | `dataset_profile`(table × column_scope: content_date_means · publish_when · basis · recommended_lag_days · evidence · **coverage_from · coverage_to · coverage_basis(measured/source_floor) · universe_coverage_pct**) |
| 작업 | 전 테이블 × 컬럼군 행 · KRX 는 basis=measured(상한) + evidence 에 I3 미결 명시 · 시총·주식수 익일 column_scope · credit 랙 근거 = `stlm_date − deal_date` 분포 · 팩터 시작연도(2008 공매도 / 2010 가격·수급 / 2011 대차 / 2015 재무 / 2026-04 리비전) 를 coverage_from 으로 |
| 통과 조건 | EG2(lag_known=false 테이블 전부 행 보유 ∧ `recommended_lag_days ≥ 1`, basis=default 행에 evidence 필수, coverage_from 전수) · EG-C ⑥·⑧ |

### 3-7. 7단계 — 마무리 · 인계

| 항목 | 내용 |
|---|---|
| 산출 | `equity.duckdb` 뷰 카탈로그 · 엔진 어댑터 `adapters/equity_duckdb.py`(3포트, 생성자 `(asof, lag_overrides)` 를 결과 메타에 기록, `venue='XKRX'` 고정, `market` 은 필터 컬럼) · `data/equity/baseline.json` 고정 · `EQUITY_HANDOFF.md`(읽기 계약 · 테이블별 메모 · **팩터 ID × 컬럼 × 시작일 표** · PR 수익률 고지 · 알려진 한계) |
| 통과 조건 | EG5(a)(c) · **EG-C ①~⑩ 전량** · 전 테이블 MANIFEST `gates` fail 0 |
| 엔진 저장소 후속(별도 이슈) | `CorporateActionType` 확장(bonus·rights·capred·spinoff) · `CalendarSource` 포트(`MonthEndSession`) · `FundamentalSource` 포트(결정 5 대안) |

---

## 4. 순서 · 병렬

```
0 설계 ─ 1 마스터·유니버스·캘린더·지수 ─┬─ 2 가격·이벤트·계수 ──┐
                                        ├─ 3 격자·결측 3분류 ──┤
                                        ├─ 4 재무 PIT ─────────┼─ 6 profile ─ 7 마무리·인계
                                        ├─ 4B 지분·감사·배당 ──┤
                                        └─ 5 컨센서스 ─────────┘
```

- **1단계가 유일한 직렬 병목**이다. 2·3·4·4B·5 는 전부 1단계 계약 컬럼(`security`·`security_span`·`trading_calendar`·`universe_daily`·`corp`·`corp_ticker`)에 의존하므로 1단계 MANIFEST 확정 뒤 병렬. 서버 실측은 RAM 제약으로 직렬(`flock`), 픽스처·TDD 만 병렬.
- baseline 상수가 필요한 게이트(EG5·EG6 오판율·EG8·EG9)는 첫 빌드 `skip(no_baseline)` + 측정치 기록 → 사람 승인 → 2회차 정식.

---

## 5. 기록 규약 (게이트 아님)

단계 완료마다 `EQUITY_DESIGN.md` §기록에 남긴다. 숫자 없는 "완료" 금지.

| 단계 | 기록 항목 |
|---|---|
| 공통 | 입력 build_id · 산출 행수 · 게이트 판정 · baseline diff · 빌드 시간·RSS |
| 1 | `corp_ticker` 매핑률 · 폐지 909 분해별 span 종료 편차 · 정책 분위수 초기값과 근거 |
| 2 | 이벤트 유형별 건수 · `factor_ok=false` 건수 · 교차 불일치 상위 20 종목 사유 |
| 3 | 소스별 커버율 월별 시계열 · `fill_kind` 분포 · evidence_rate · 키움 샤드 미커버 티커 수 |
| 4 | 계정별 커버율(FY2015~) · 정정 타이밍 분포(7일·90일·1년) · PIT 결측률 교차표 |
| 4B | 롤링 창 경계 · 집계행 제거 건수 |
| 5 | 구간별(v3/wise) 커버 종목수 월별 · 시총 분위수별 커버율 · 리비전 팩터 사용 가능 시작 월 |
| 6 | basis 분포(measured 비율) · coverage_from 전수 |

결함은 `.claude/rules/pr-review.md` 4요소 양식. PR body 는 규칙 양식. baseline 갱신은 diff 를 커밋 메시지에.

---

## 6. 범위 밖 · 알려진 한계

- 팩터 값 계산 · 윈저 · z-score · 중립화 · `universe_policy` **적용** · `factor_ok=false` 창 결측 처리 — 팩터층.
- **총수익률(TR) 불가** — 배당락일 원천 없음(CA-05). 이 DB 의 수익률은 PR 이며 팩터별 편의 부호는 PR-04 실측. `stg_disclosure` 에서 배당락 공시가 추출되면(0단계 실측) 재검토.
- **재무 원본 판본 없음** — DART 는 최신 판본만(§2-18). PIT 규칙은 look-ahead 를 비랜덤 결측으로 바꾼다. 결측률 교차표를 인계.
- **공매도 금지 구간**(2011-07 이후 34.6%, SH-03) 규칙표 — 수작업 문헌값이라 팩터층 비용·제약 모형 몫. equity 는 만들지 않으며 인계 문서에 구간을 고지.
- **신용 2007-07-16~2009-12** — 캘린더 하한(KRX 2010-01-04) 밖이라 격리. 유니버스·가격이 없어 횡단면에 못 쓴다.
- 관리종목·거래정지 과거 시계열(2026-09-01 이전 NULL, 소급 금지) · 코넥스 · 지수 구성종목 PIT · 문서층 L1(정정 링크는 grouped 근사) · 일일 증분 · 엔진 포트·enum 확장(별도 저장소 이슈).

---

## 7. 검수 기록 (v1 → v1.1, 2026-09-03)

Opus 리뷰어 5명(엔진 소비자 · 팩터 재료 · PIT 적대 감사 · stage 계약 · 게이트 감사) 51건. 전건 근거 문서·코드로 재검증했고, 반영 방식은 아래. 기각·조정은 이유를 적었다.

| 출처 | 지적 | 검증 | 반영 |
|---|---|---|---|
| 엔진 | 재무·컨센서스 엔진 포트 없음 / 원주가 Bar+계수 결합 지점 없음 / `ratio` 방향·enum 3종·임계 1.5 / status→Membership·coverage_gap / reference·volume=0 행이 NO_DATA 유발 / EG-C 가 못 잡음 / asof·lag 필드 없음 / 캘린더 포트 없음 | 코드 확인 8/8 | 결정 5 신설, §0-1, §3-2 판단, EG-C ①~⑨, §3-7 후속 |
| 팩터 | 지분·감사·주식총수 stage 5테이블 미입력(E01~04·E08) / flow 13컬럼 소실 / corp_event 단일 수치·row_kind·조정 거래량 / 지수 테이블 없음 / induty_code 미전달 / fin_std 계정 순환 / 시작일 미기록 / FACTORS stale 3건(R01~03 존재 → 정본 50) | 문서 확인 8/8 | 4B단계 신설, `index_daily`·`dividend_event`·`v_adj_volume`, 0단계 계정 매트릭스, profile coverage 컬럼, FACTORS 정정. "변동성" 은 R01 중복이라 누락 4종에서 제외 |
| PIT | 정정 방어 = 결측 치환(§2-18) / 재무 랙 하한 없음 / 컬럼군별 랙 미배선·ADV20 창 / EG9 93% 측정일 없음·evidence 부재 / 정리매매 잔류·폐지 909·sec_type 원천 / 계수 available 없음·전파창 60 < 룩백 231·항진명제 / 배당 계수 근거 없음 / 컨센서스 구간·픽스처 look-ahead 유래 / 정정 그룹 정규화·다중 정정·오판율 / as-of 불변 미검사 | 문서 확인 10/10 | §0-3 방향 라벨, EG2 랙 ≥ 1, `universe_daily.available_date`, EG9 evidence_rate·상관, `no_trade_run`, 909 게이트, 계수 available·`factor_ok`, 배당 계수 삭제, 구간 2행, `correction_seq`, EG5(c). **조정**: sec_type 원천은 `stg_listing_daily.stkcert_tp·secugrp` 로 실재(코드 확인) — "없음" 이 아니라 원천 명시. 전파창은 상수 대신 `factor_ok` 플래그 + 팩터층 창 규칙 |
| stage | 분기화 방향 반대(§2-7) / fin_std 등식 grain / ETF 원천 없음 / 캘린더 08-20 종료 vs coverage_gap / keep=3 GC·BuildRecord frozen·gates 재사용 불가 / 병렬 주장 모순 / credit 2007~·`stg_loan_daily_kis`=대차 / 샤드 status 'done' 부재 / HK ISIN 8종목 / 재사용 3건 불성립 / `stck_prpr`→`close_krw` / skip 규약 / 규모 | 코드·문서 확인 10/10 + m1~m6 | §3-4 공식, §2-1 등식표, `stg_etf_price_daily` 입력, 캘린더 연장·등식 분리, `_pinned/`·manifest 변경, §4 그래프, 격리·대차 재배치, `status='ok'`, HK 단독 corp, 재사용 정정, EG0 stage 컬럼명, §2 skip 열, §1 규모 |
| 게이트 | EG2 가 1·2·3 에 없음 / 병렬 모순 / 계수 축 SPEC 불일치 / 항진명제·G9 중복 / 90% 하드코딩·날짜 아님 / 정책 소유 이중·'micro' 술어 거짓 / 결정 확정형 서술 / 테이블 수 산술 / EG1 미선언·EG5 자연어 / 기록 혼입·span 확보 게이트 승격 | 문서 확인 10/10 | 전 단계 EG2, EG3 재정의, 본문 상수 제거·baseline 참조, `v_universe(:d,'all')`, 결정 표(선택/대안/이유)+`[결정 N 전제]`, 집합 차 술어, §2-1, §5 분리·909 게이트. **조정**: 제안된 시총 연속성 항등식은 항등이 아니라(가격 변동) `price×share=1` 로 대체 |

미확인으로 남겨 0단계 실측 항목에 넣은 것: duckdb 매크로 4건 · `bsns_year` 라벨 관례 · 샤드 커버 · credit pre-2010 행수 · analyst 테이블 fetched_date 수 · 공시 축 거래정지·배당락 추출 가능성 · WISE 실제 커버 종목수.
