# Equity 층 워크플로우 (v1.2, 2026-09-05 — 문서층 P1 반영 · 소비자 계약 재기술 · 23슬라이스)

> stage(사실) 위에 equity(구조 정책)를 쌓아 **팩터 재료**와 **백테스트 PIT 패널**을 만드는 전 과정을 슬라이스·게이트로 고정한다.
> 근거: `EQUITY_KICKOFF.md` · `EQUITY_DESIGN.md` v1.2 · `EQUITY_GATES.md` v1.0(게이트 사양) · `EQUITY_FIELD_MAP.md` v1.0(소비자 필드 계약) · `STAGE_HANDOFF.md` · `DOC_DESIGN.md` v1.1(문서층, `origin/stage/doc-p1`) · `backend/`(워크벤치·엔진 코드).
> v1.1 → v1.2 변경 근거는 `reviews/2026-09-05-equity-*.md` 4건(제안서)과 §8 검수 기록. **결정 1~7 전부 사용자 확정(09-05 "권고사항으로 작업 모두 진행")**.

---

## 0. 정합성 — 이 층이 답해야 하는 질문

**"날짜 D에, 그 시점에 알 수 있던 정보만으로, 그날 실존한 전 종목의 상태를 줘."**

### 0-1. 소비자 — 워크벤치 5포트 + 엔진 커널 3포트, 어댑터는 하나 `[결정 5 재기술]`

v1.1 은 "엔진에 재무·컨센서스 포트가 없다" 고 결론지었으나, 병합된 `backend/src/strategy_workbench` 에는 이미 equity 소비 계약이 있다(코드 확인 09-05):

| 포트 | 파일 | equity 가 답할 것 |
|---|---|---|
| `EquityDataPort` | `application/equity_workspace/ports/outgoing/equity_data.py` | `snapshot()`·`list_fields()`(`DatasetFieldProfile`: field_id·basis·`recommended_lag_sessions`·coverage)·`load_universe()`·`load_panel()`(세션별 as-of 컷오프, `lag_overrides`) |
| `RawObservationPort` | `…/portfolio_design/ports/outgoing/raw_observations.py` | (as_of, security_id) 별 raw 필드 값 + `available_date`, `universe_member`, `sector_id`, 워밍업 세션. **값 합성 금지, 미지원은 status** |
| `FactorObservationPort`·`FactorMetadataPort` | `…/equity_workspace/ports/outgoing/factor_*.py` | 팩터 그래프 평가용 관측·메타 |
| `BacktestDataPort` | `…/backtest_data.py` | 백테스트 데이터셋 |
| 커널 `BarSource`·`UniverseSource`·`CorporateActionSource` | `backend/src/backtest_engine/ports/` | 원주가 Bar · span → `Membership` · `CorporateActionEvent(ts=효력일)` |

현재 조립은 `bootstrap/_container.py::build_container(equity_adapter="mock")` 이고 다른 값은 거절된다. 계약 테스트 `backend/tests/contract/test_raw_observation_port.py` 는 `ADAPTERS=[mock]` 로 매개변수화돼 있다. **equity 층의 완료 = `equity_duckdb` 어댑터가 이 `ADAPTERS` 에 들어가 전량 green 이고 `equity_adapter="duckdb"` 로 컨테이너가 뜨는 것**이다.

팩터 재료의 형태는 `backend/FACTORS.md`(레지스트리 50 팩터)가 요구하는 **field_id 42종**이며, equity 컬럼과의 대응·판정은 `EQUITY_FIELD_MAP.md` 에 있다(지원 17 · 부분 15 · 미지원 9 · 미확인 1).

### 0-2. 세 층의 책임

| 층 | 하는 일 | 하지 않는 일 |
|---|---|---|
| stage | 캐스팅·단위·결측 원인·중복 접기·공개일 **사실**. 문서층 P1(`stg_doc_meta`·`section`·`correction`·`parse_log`) 포함 | 조인·집계·정책 |
| **equity** | **구조 정책**: 크로스소스 조인 · 격자 · 판본 선택(PIT) · 조정계수 · 유니버스 사실+상태 · 정정 링크 · 공개시점·준비도 카탈로그 · **어댑터(5+3포트)** | 통계 정책 · 정책 **적용** 판정 · 임계·판정 결과를 팩트에 굽기 · 팩터 값 |
| 팩터층(워크벤치 `domain.factor`) | 팩터 그래프 평가·정규화·`universe_policy` 적용 | 원장·stage 직접 읽기 |

원칙 6 유지 + **정본 우선순위 KRX**(사용자 확정 09-03) + basis 어휘(stage 4종 + `convention`).

### 0-3. 목적 ↔ 편향 ↔ 장치 ↔ 게이트 (요약 — 게이트 술어는 `EQUITY_GATES.md`)

| 목적 · 위험 | 장치 | 게이트 |
|---|---|---|
| 생존편향 | `security_span`·폐지 전부 `delist_date`(KRX 우선) · ETF 포함 · 어댑터는 span 단위 질의(사전 제외 금지) | EG1·EG3·EG15·EG16 |
| 정리매매·거래정지 잔류 | `halt_state`(지정 공시→해제 공시 ∨ 다음 거래)·`liquidation_window`·`admin_state`(KOSDAQ 소속부, KOSPI 신호 창) | EG3 · 정리매매 recall |
| look-ahead(공개시점) | 전 행 `available_date`·컬럼군별 세션 랙(`dataset_profile`)·뷰가 적용·파생 컬럼 `<col>_available_date` | EG2·EG13·EG19·EG-C |
| look-ahead(정정) | `disclosure_version`(grain `rcept_no`, 확정 링크 `orig_rcept_no`, `first_correction_dt`·`n_corrections` 팩트 — 판정은 뷰) | EG6(E-G6a/b/c)·E-G7 |
| look-ahead(컨센서스) | `available_date`=최초 관측 · `obs_month` 축 금지 · `src` 구간 | EG6·EG-C ⑨ |
| 조정 오류 | 두 축 계수 + 공개일 · 곱셈(base=asof `v_adj_price` · 전방 `v_adj_price_fwd`) · `price.adj_close` 필드 분리 | EG3·EG8·EG20 |
| 레짐 편향 | 격자 + `fill_kind`(근거율) | EG9·EG14·EG17 |
| 우선주 시총 | isin8(KR7) · `v_firm_mktcap` | EG3·EG4 |
| **목적 자체(54 재료)** | **`factor_readiness`(54행)** | **EG10** |
| 재현성 | 입력 build 고정·`_pinned/`·`content_hash`·`_asof/` 표본 | EG0·EG5·EG11·EG18 |

---

## 1. 공통 워크플로우 — 슬라이스 하나가 지나는 길

슬라이스 = **테이블 1~3개**, 브랜치 `equity/<slice>` 하나, PR 하나(stage 는 62테이블을 PR 21건으로 나눴다).

```
설계 확정(DESIGN §4 의 grain·입력·available·EG1 등식·파티션 + GATES 술어 ID)
  → TDD: 손계산 픽스처(FX-*) 먼저 · 게이트 부정 픽스처(FX-N-*)
  → 로컬 빌드: stage 절단본(scripts/slice_stage_fixture.py) 위에서 EG 전량
  → 서버 실측: 읽기 전용 스크립트 scp → .venv/bin/python (stage 는 MANIFEST 경유, 맨 glob 금지)
  → 게이트 전량 통과(EQUITY_GATES §7-1 순서: EG0 → EG7 → EG1 → … → EG5) → MANIFEST 원자 교체
  → 문서 §기록(5항 필수: 입력 build_id · 행수 · 게이트 판정 · baseline diff · 시간·RSS) → PR self-merge → 브랜치 삭제 → rsync
```

- **산출** `~/quant-ledger/data/equity/<table>/`(stage 골격) · `baseline.json` · `fixtures/` · `_pinned/<stg_x>/{v=<build>/, MANIFEST.json}`(하드링크 + BuildRecord 복사) · `_asof/<view>/<build>/`(as-of 표본 결과) · `equity.duckdb`(뷰 카탈로그, 임시 파일 → `os.replace`).
- **코드** `workspace/dongmin/src/equity/`(`model`·`rules_*`·`inputs`·`build`·`gates`·`catalog`·`baseline`·`__main__`, `.sql` 파일 빌드) — 재사용은 `stage/manifest.py`·`stage/baseline.write`·`GateStatus/GateResult` 만, `build`·`gates` 는 복제 후 수정(원장 ATTACH·fanout 전제 제거). 상세는 `reviews/2026-09-05-equity-code-proposal.md`.
- **어댑터** 커널 3포트 `backend/src/backtest_engine/adapters/equity_duckdb.py`(pyarrow, 새 의존성 없음) · 워크벤치 5포트 `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/`(duckdb 필요 → **결정 7**).
- **판본** `BuildRecord.inputs`(09-03 반영) · stage 문서층 4테이블도 `_pinned/` 고정 · `content_hash` 는 tmp 경로에서(`v=` 하이브 컬럼 함정).
- **금지** 원장 SQLite 직접 읽기 · 상수 본문·코드 하드코딩 · 미수집→0 · `_current` 로 과거 필터 · `obs_month`·`bsns_year` 날짜 축 · latest 판본 · 게이트 술어를 산출식으로 재계산(항진명제) · `_pinned/` 에 `manifest.commit()` 호출(GC).

---

## 2. 게이트 — `EQUITY_GATES.md` v1.0 이 사양

EG0 입력 고정 · EG1 격자 등식(`− n_dedup − Σ n_reject` 일반형, 전 테이블) · EG2 PIT 불변식(`<col>_available_date` 포함) · EG3 키·불변식(독립 재계산) · EG4 골든 픽스처(FX 카탈로그 54 + 부정 10) · EG5 재현성(a 해시 · b 등식 · c as-of 불변 `_asof/`) · EG6 판본 선택(E-G6a/b/c 링크) · EG7 범위(격리형, `sec_type='other'` 는 격리 아님) · EG8 교차 소스(가격·거래량 점프·recall) · EG9 레짐 커버리지(커버율 ∧ evidence_rate ∧ 상관) · **EG10 팩터 준비도**(`factor_readiness` 54행) · EG11 뷰 결정성 · EG12 단위 접미사 · EG13 available 미래값 · EG14 파티션 경계 · EG15 폐지 전 가격 · EG16 가짜 재상장 · EG17 캘린더 · EG18 조인 팬아웃 · EG19 as-of 단조성 · EG20 원주가 불변 · EG-C 소비자 계약 ①~⑩(⑥ 는 커버 밖 팩터 `skip(no_coverage)`).

상수는 전부 `baseline.json`(`{table}.{metric}`), 첫 빌드 `skip(no_baseline)` + `_meta.gates[].metrics` 기록 → 사람 승인 → 2회차 정식. 실행 순서·실패 리포트·부정 픽스처 규약은 GATES §7.

---

## 3. 슬라이스 — 23 (+대기 2)

### 3-0. 규칙

- 슬라이스 = 테이블 1~3개. 통과 조건은 GATES 매트릭스의 술어 ID 로 적는다. 술어 ID 가 없는 테이블은 착수 금지.
- `universe_daily` 는 **존재·상태(S03)** 와 **시장 파생(S03B)** 으로 나눈다 — 시장 파생은 2단계 `price_daily` 정본을 읽어야 하므로(v1.1 의 1↔2 순환 제거).
- 어댑터 v0(S07)은 2단계 직후 — EG-C ①~⑤·⑩ 을 그 시점에 실행(v1.1 은 7단계라 순환).
- 재무 PIT 는 **4A(문서층 P1 만으로 지금 가능)** 와 **4C(P2 `stg_fin_asreported` 대기)** 로 나눈다. 4A 는 `stg_doc_meta.period_to`·`stg_doc_correction` 을 쓰므로 1단계 산출을 읽지 않는다.
- 고정 as-of 표본(baseline `asof_sample`: 날짜 5 × 종목 20)은 S02 에서 등재하고, 뷰를 내는 슬라이스(S06·S12·S17)는 그 표본으로 EG11·EG19 를 통과해야 한다.

### 3-1. 슬라이스 표

| ID | 이름 | 테이블/산출 | 입력 | 병렬 | 선행 |
|---|---|---|---|---|---|
| **S00** | 계약 문서·환경 | `EQUITY_FIELD_MAP.md`·DESIGN v1.2·GATES v1.0·FACTORS 정정·`check_field_map.py` · 로컬 duckdb 환경(`uv run --with duckdb`) · 서버 `data/equity/` 생성 | 제안서 4건 | — | — |
| **S01** | 법인·종목 식별 | `corp`·`security`·`corp_ticker` | `stg_corp_map`·`stg_company`·`stg_listing_daily`·`stg_etf_price_daily`·`stg_delisted_master` | — | S00 |
| **S02** | 캘린더·구간·지수 | `security_span`·`trading_calendar`·`index_daily` + `asof_sample`·`backfill_end` 등재 | S01 + `stg_index_daily`(gap 축 없음, P16) | — | S01 |
| **S03** | 유니버스(존재·상태) | `universe_daily` v1(`status`·`market`·`sec_type`·`halt_state`·`admin_state`·`liquidation_window`·`signal_*`) · `universe_policy` 스키마+`all` | S02 + `stg_listing_daily`·`stg_master_daily`·`stg_disclosure` | ∥ 4A | S02 |
| **S04** | 가격 정본 | `price_daily` | S02·S03 + `stg_price_daily`·`stg_etf_price_daily`·`stg_listing_daily` | ∥ S05 | S03 |
| **S05** | 기업행위 | `corp_event` | `stg_event_*`·`stg_capital`·`stg_disclosure` 락일·배당결정 · KRX 주식수 변화 | ∥ S04 | S03 |
| **S06** | 조정계수·가격 뷰 | `adj_factor`·`v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap`(+ S21 후속 전방 조정 `v_adj_price_fwd`·`v_adj_volume_fwd`) | S04·S05 | — | S04·S05 |
| **S06-2** | 조정계수 v3(KRX 기준가) | `price_daily` +`change_krw`·`base_price_krw` · `adj_factor` 원천 `krx_base_price`(사건 교체·신규 `unknown_krx`·재발견 제외·`unknown_price_only` 기록, ETF·구간 첫날 제외) · 어댑터 `unknown_krx` 방향 매핑 · 규칙 판본 e1.3.0 · EG8 재측정 | S04·S06 + stage `change_krw` + equity `security`·`security_span` | ∥ S03C | S06 (설계 확정 09-05, **구현 09-05** — DESIGN §10 P27 · GATES §9 S06-2, 서버 실측 대기) |
| **S03B** | 유니버스(시장 파생) | `universe_daily` v2(`mktcap_krw`·`adv20_krw`·`listing_age_days`·`no_trade_run`·`suspended` 완성) | S04·S03 | ∥ S06 | S04 |
| **S03C** | 유니버스(무거래 이유) | `universe_daily` v4(`no_trade_reason`, status 규칙 = 신호 없는 무거래에만 k) · `universe_policy` liquid 술어 +1(13행, s03c-v4) | S03B·S06 + equity `adj_factor` | ∥ S06-2 | S03B·S06 (**구현 완료 09-05** — 로컬 실측 DESIGN §10 P28, 서버 미실행) |
| **S07** | **엔진 어댑터 v0** | `backtest_engine/adapters/equity_duckdb.py`(3포트, pyarrow) + `backend/tests/test_bar_source_contract.py::BUILDERS` 등록(런타임 어댑터 레지스트리는 없다 — 호출자가 직접 생성) · `equity contract`(`src/equity/contract.py`, EG-C ①②③④⑤⑩ → `_contract_meta.json`) · `baseline_seed_s07.json` | S03B·S06 | — | S06·S03B |
| **S08** | 수급 격자 | `flow_daily`(13주체 + KIS 대응표) | S03B + 키움·KIS flow·foreign·로그 | ∥ | S03B |
| **S09** | 공매도·대차 격자 | `short_daily` | S03B + short kiwoom/kis·lending·loan_kis | ∥ | S03B |
| **S10** | 신용 격자 | `credit_daily` | S03B + `stg_credit_daily` | ∥ | S03B |
| **4A = S11** | 공시 판본(확정 링크) | `disclosure_version`(grain `rcept_no`, `orig_rcept_no`·`candidate_status`·`date_check`·`prior_corr_count`·`first_correction_dt`·`n_corrections`·`legal_deadline`·`delay_days`) + 모집단 5단 사다리 baseline | `stg_disclosure`·`stg_doc_correction`·`stg_doc_index`·`stg_doc_meta` | ∥ S03 | S00 · **doc-p1 main 병합** (**구현 완료 09-06** — 로컬 실측 DESIGN §10 P29, 서버 미실행) |
| **4A = S12** | 재무 PIT | `fin_std`(`period_end` = `stg_doc_meta.period_to` 정본, `vintage_kind='api_restated'`, 파생 블록마다 `*_available_date`·`*_n_rows`) · `v_fin_latest`(**후속**) | `stg_fin`·**`stg_disclosure`**(`stg_rcept_dt_map` 부재)·`stg_doc_meta`·S11·`corp` | ∥ | S11 (**테이블 구현 완료 09-06** — 로컬 실측 DESIGN §10 P30, 뷰·서버 미실행) |
| **4C = S14** | 재무 원본 판본 | `fin_std.vintage_kind ∈ {original, corrected}` · `v_fin_latest(vintage := 'pit')` · D9 교차 | + `stg_fin_asreported` | **대기** | S12 · **문서층 P2 완료** |
| **S15** | 지분·감사 | `holder_daily`·`ownership_snapshot`·`audit_opinion` | `stg_holder_*`·`stg_hyslr`·`stg_audit`·S01 | ∥ | S01 |
| **S16** | 주식수·자사주·배당 | `shares_outstanding`·`treasury_stock`·`dividend_event` | `stg_shares`·`stg_tesstk`·`stg_dividend` + `shares_outstanding` 만 `corp_ticker`(S01)·`price_daily`(S04) **고정만**(KRX 검산 기록형, DESIGN §4-2) | ∥ | S01 · **S04**(09-06 구현, GATES §9) |
| **4B′ = S16B** | 4-B 2011~2014 확장 | 위 6테이블 `src` 축 추가(서식표) | + `stg_doc_form_cell` | **대기** | S15·S16 · **문서층 P3** |
| **S17** | 컨센서스 | `consensus_daily`(v3 unpivot 8 + wise, `src`, `target_period` = `YYYYMM`) · 뷰 `v_consensus` | `stg_consensus_monthly`·`stg_v3_revision_daily` + equity `security_span`(EG9 커버율 분모) | ∥ | S02 (**구현 완료 09-06** — 로컬 실측 DESIGN §10 P36, 서버 미실행) |
| **S18** | 의견·목표가 | `opinion_daily`·`opinion_broker_daily` | `stg_analyst_summary/broker`·`stg_v3_analyst_opinions`·`stg_wise_coverage` | ∥ | S01 |
| **S17** | 컨센서스 | `consensus_daily`(v3 unpivot + wise, `src`) | `stg_consensus_*`·`stg_v3_*`·S01 | ∥ | S01 |
| **S18** | 의견·목표가 | `opinion_daily`(`src` wise ∪ v3, 최초 관측 선택)·`opinion_broker_daily`(+ 파생 `prev_opinion_date`) | `stg_analyst_summary/broker`·`stg_v3_analyst_opinions`·`stg_wise_coverage` + equity `security`·`security_span`(EG9 커버 모집단, GATES §9 S18) | ∥ | S01 · **구현 완료 09-06** — 로컬 실측 DESIGN §10 P37, 서버 미실행 |
| **S19** | 공개시점 대장 | `dataset_profile`(field_id · `recommended_lag_sessions` · `supported_cell_kinds` · `point_in_time` · `coverage_*` · `coverage_by_mktcap_quintile`) | 전 슬라이스 `_meta` | — | S08~S18 |
| **S20** | **팩터 준비도** | `factor_readiness`(54행: id·required_columns·status ready/blocked·reason·owner·first_usable_date) + **EG10** | S19 + `FACTORS.md` + `backend/FACTORS.md` + `EQUITY_FIELD_MAP.md` | — | S19 |
| **S21** | **워크벤치 어댑터** | `adapters/outbound/equity_duckdb`(5포트) · contract suite `ADAPTERS` 매개변수화 · `build_container(equity_adapter="duckdb")`. **축소판 구현(09-05, MVP-B)**: 필드 3(`price.close`·`price.market_cap`·`price.adj_close`) 위에 5포트 전부(컨테이너·파이프라인이 요구) · contract `ADAPTERS=[mock, equity_duckdb]` green · `build_container(equity_adapter="duckdb", equity_root=…)` · `scripts/run_mvp_backtest.py` 로 절단본 백테스트 1회 완주(DESIGN §7·§10 P25). 남은 것(S19·S20 뒤 본판): 나머지 필드 39 · `dataset_profile` 랙 · `sector_id` · 서버 실측 | S07·S03B(축소) / S19·S20(본판) | — | S07(축소) · S20(본판) |
| **S22** | 마무리·인계 | `baseline.json` 고정 · `EQUITY_HANDOFF.md` · 재현성 2회 · MVP-B 백테스트 재현 | 전부 | — | S21 |

병렬 최대 폭: S03B 뒤 6갈래(S08·S09·S10·S11+S12·S15+S16·S17+S18). 서버 빌드는 `flock` 직렬(RAM 15GB), 병렬은 픽스처·TDD·로컬 빌드까지.

### 3-2. 순서 그래프

```
S00 ─ S01 ─ S02 ─ S03 ─┬─ S04 ─┬─ S06 ─┬─ S03B ─ S07(어댑터 v0) ─┬─ S08·S09·S10 ──┐
                       └─ S05 ─┘       └────────┘                 ├─ S15·S16 ──────┤
S00 ─ S11(4A) ─ S12(4A) ────────────────────────────────────────── ├─ S17·S18 ──────┼─ S19 ─ S20 ─ S21 ─ S22
stage P2 ─► S14(4C) ── 대기 ───────────────────────────────────────┤
stage P3 ─► S16B(4B′) ─ 대기 ──────────────────────────────────────┘
```

### 3-3. 1단계(S01~S03) 상세 — 파일·테스트 단위

T0 인프라(`equity/model.py`·`inputs.py`(resolve·pin·create_views·declared_columns_missing)·`build.py`·`gates.py`·`baseline.py`·`__main__.py` + 테스트 5파일) → T1 `trading_calendar` → T2 `corp` → T3 `security` → T4 `security_span` → T5 `corp_ticker` → T6 `index_daily` → T7 `universe_daily` v1 → T8 `universe_policy` → T9 `catalog.py`+`v_universe` → T11 서버 실측 1회전. 각 작업의 파일·함수·테스트명·완료 조건은 `reviews/2026-09-05-equity-code-proposal.md` §5 표(T0.1~T11)와 `reviews/2026-09-05-equity-workflow-proposal.md` §3-2 를 그대로 쓴다. 픽스처 ID 는 GATES §4(FX-1-001~).

### 3-4. 4A 상세 (S11·S12) — 문서층 P1 기반

- `disclosure_version`: 모집단 = `stg_disclosure` 정기보고서 3종(연장신고·비표준 종류 제외) 전건 → `stg_doc_correction` LEFT JOIN(`rcept_no`) → 후보 술어(같은 corp·kind·기간 라벨·접두 ∉ {기재정정, 첨부정정}·`rcept_no <`) → `candidate_status ∈ {unique, none, multi_resolved, multi_unresolved}` · `date_check ∈ {exact, off_1d, off_2_7d, mismatch, unparsed, no_page, no_zip, n/a}`(`no_page`/`no_zip` 은 `stg_doc_index.zip_ok` 로 가른다). `kind` 는 정정 문서 자신의 `report_nm` 에서만(자유 텍스트 `target_raw` 미사용). **모집단 사다리 5단(20,579 / 24,285 / 181,106 화해)을 baseline 에 등재하기 전에는 E-G6a·E-G7 임계를 걸지 않는다.** 실측 기준값: DOC C340 unique 339/340 · date exact+1d 276/291(94.8%) · 서버 filed_date 조인 14,026/15,225(92.1%, 같은 축) · `rm` 플래그 도달 99.9%.
- `fin_std`: `period_end`·`report_code` 는 `stg_doc_meta(period_from, period_to, doc_acode)` 정본(main·파싱 ok 168,938 전건 채움), `corp.fiscal_month` 후보 규칙은 검산으로 강등 · 1Q/3Q 는 `period_from→period_to` 개월 수로 판정 · `has_correction` 정적 컬럼 폐기 → `first_correction_dt`·`n_corrections` 팩트 + 뷰가 asof 로 판정(DEFECT-E01) · `stg_doc_section` 은 팩트에 쓰지 않는다(`fin` 섹션 = 2015-03 이후 서식만, `fin_legacy` 별도).

### 3-5. MVP 경로 (MVP-B, 권고)

S00·S01·S02·S03·S04·S05(축소: split·bonus·capred 만)·S06·S03B·S07 + S21 축소(`RawObservationPort` 만, 필드 `price.close`·`price.adj_close`·`price.market_cap`) → 워크벤치 파이프라인으로 `price.momentum_12_1` 월간 리밸런싱 백테스트 1회(2011-01~2026-08-20). 한계(명시): 팩터 7개(M·R 군) · PR 수익률 · 섹터 중립화 불가 · 유니버스 `all` 만 · rights/spinoff 불연속은 실현손익 · 2026-08-21 이후 불가. 되돌리기 비싼 결정은 `security_id`·`universe_id` 어휘뿐(FIELD_MAP §1).

**정정(S21 실측, 09-05)**: ① "`RawObservationPort` 만" 은 성립하지 않는다 — `build_container` 와 `BacktestRunService` 가 `EquityDataPort`(snapshot·list_fields·load_universe·load_panel)·`FactorMetadataPort`(그래프 컴파일)·`FactorObservationPort`(타입 계약)·`BacktestDataPort`(bar·구간·사건 피드)를 함께 요구하므로 5포트를 같은 패널 코어 위에 구현했다(필드는 3 그대로). ② "유니버스 `all` 만" 도 철회 — S03B 정책표가 있어 `krx.common-stock`(계약 기본값)·`krx.investable` 이 정책 driven 으로 풀린다. ③ 레지스트리 그래프는 `price.close` 를 요구하므로(#64) `scripts/run_mvp_backtest.py` 가 FieldNode 만 `price.adj_close` 로 바꿔 돌린다(`--price-field price.close` 로 원주가 비교 run). ④ 커널이 bar 없는 세션(정지)의 비중 목표를 수량화하지 못해 워크벤치 `TargetTapeStrategy` 에 보유 유지/건너뛰기 규칙을 넣었다(DESIGN §7 엔진 측 한계). ⑤ 워밍업 273세션(252+21)은 2011-01-03 앞 캘린더(2010-01-04~, 247세션)보다 길어 어댑터가 잘라내고 경고를 남긴다 — 첫 유효 스코어는 2011-02 부터. 실행·수치는 DESIGN §10 P25.

---

## 4. 완료 정의 (DoD)

**슬라이스 공통**: MANIFEST `current_build` 존재 ∧ `gates` fail 0(skip 은 사유 필수) ∧ `inputs` 전부 `_pinned/` 실존 ∧ `fixtures/<table>.json` 존재·테스트 green ∧ 같은 `inputs` 재빌드 `content_hash` 동일 ∧ DESIGN §기록 5항 ∧ 새 상수는 `baseline.json` 에만(grep 0).

| 단계 | DoD (술어 ID 는 GATES) |
|---|---|
| S00 | `check_field_map.py` 집합 차 0 ∧ GAP 21건 전부 슬라이스 배정 ∧ 결정 5·6·7 확정 표기 |
| 1단계 S01~S03 | EG1 7식 ∧ 폐지 전부 `delist_date`(EG3-P10) ∧ KR7 isin8 그룹당 보통주 1 ∧ span 비중첩·Σ n_days 등식 ∧ 캘린더 = 4,094 ∧ `induty_code` 공란 0 ∧ `halt_state` 열린 구간 0 ∧ `asof_sample` 등재 |
| 2단계 S04~S06·S03B | `price_daily` = 10,890,251 ∧ 시총 불변 `price×share=1` 위반 0 ∧ `v_firm_mktcap` 독립 재계산 일치 ∧ 분할일 가격·거래량 점프 ≤ baseline ∧ EG20 원주가 불변 ∧ EG11 뷰 결정성 |
| S07 | `test_bar_source_contract.py::BUILDERS['equity_duckdb']` 등록 후 그 파일 전량 green ∧ `test_adapters_equity.py` green ∧ `equity contract` EGC-01·02·03·04·05·10 pass(절단본 체인 → 서버) ∧ 폐지 표본(`security.delist_sample_n`) BarQuery OK·반환 = 요청 |
| 3단계 S08~S10 | 격자 등식 ∧ 미수집→0 행 0(로그 축 독립 재판정) ∧ evidence_rate ≥ baseline ∧ 커버율↔시장수익률 상관 ≤ baseline ∧ 12주체 합 항등(kiwoom) ∧ 겹침 0 ∧ pre_calendar 격리 건수 = 실측 |
| 4A S11·S12 | 사다리 5단 baseline 등재 ∧ E-G6a ≥ 임계·E-G6b 기록·E-G7 ≥ 임계 ∧ `period_end` 문서 정본 커버 ≥ baseline ∧ `available ≥ period_end` 위반 0 ∧ 참조표 미스 0 ∧ 파생 `<col>_available_date` 위반 0 ∧ PIT 결측률 3축 교차표 baseline 등재 |
| S14(대기) | `vintage_kind` 3종 적재 ∧ D9(정정 없는 보고서 API=원본 100%) 재현 ∧ EG5c 재기준(`_asof/` 갱신 승인) |
| 4B S15·S16 | 6테이블 EG1 ∧ `row_kind` 제거 건수 기록(3테이블) ∧ 롤링 창 경계 profile |
| 5단계 S17·S18 | 최초 관측 선택 독립 재계산 일치 ∧ v3⋈WISE 겹침 일치율 ≥ baseline ∧ EG-C ⑨(wise 구간) ∧ 시총 분위별 커버율 기록 |
| 6단계 S19·S20 | EG2 profile 전수(SQL) ∧ 세션 랙 ≥ 1(lag_known=false) ∧ **EG10: `factor_readiness` 54행, blocked 는 reason·owner 필수, ready 는 `first_usable_date` NOT NULL, ready ≥ baseline(초기 36)** |
| 7단계 S21·S22 | 워크벤치 contract suite `ADAPTERS` 에 `equity_duckdb` 포함 전량 green ∧ `EquityDataPort`·`RawObservationPort` 가 같은 셀에 같은 값·공개일 ∧ 미지원 필드 `unavailable` 명시 ∧ `build_container(equity_adapter="duckdb")` 부팅 ∧ EG5a·c ∧ EG-C ①~⑩ ∧ MVP-B 재현(`tape_hash` 동일) |

**전체 DoD**: 위 전부 ∧ 27테이블(26 + `factor_readiness`) `gates` fail 0 ∧ `EQUITY_HANDOFF.md`(읽기 계약·테이블 메모·`factor_readiness` 참조·PR 고지·한계 GAP-06/07/08/15/18).

---

## 5. 위험 대장 (요약 — 전체 15건은 `reviews/2026-09-05-equity-workflow-proposal.md` §5)

| 위험 | 징후 | 완화 | 롤백 |
|---|---|---|---|
| `price.close` 조정 여부 충돌 | 분할 구간 모멘텀이 게이트를 전부 통과한 채 틀림 | `price.adj_close` 필드 분리 + FX 삼성전자 2018-05-04 + EG8 거래량 항 · 레지스트리 개정(결정 6) | 어댑터 필드 매핑만 교체 |
| 문서층 코드 미병합(`stage/doc-p1`) | S11 입력 재현 불가 | 착수 조건 = main 병합 · 데이터는 `_pinned/` 고정 | 핀 유지 |
| 정정 모집단 술어 3종 불일치 | E-G6a/E-G7 임계가 장식 | 사다리 5단 baseline 선등재 | 임계 skip |
| 서버 RAM 15GB | 4단계·격자 빌드 스왑(09-05 문서층 사고 재발) | 연도 파티션 루프 · threads 3 · `temp_directory` · 큰 집계 스트리밍 | tmp 폐기, MANIFEST 불변 |
| stage 재빌드로 입력 이동 | EG5 fail | `_pinned/` + `inputs` | 이전 build 재빌드 |
| KOSPI 관리종목 비대칭 | 시장별 상태 지속일 차 | `admin_state_basis` 필수 · `factor_readiness` E07 caveat | — |
| 워크벤치 계약 변경 | contract suite 실패 | `check_field_map.py` CI · 같은 PR 갱신 | — |
| duckdb 의존성 미승인 | S21 착수 불가 | 결정 7 · 대안 pyarrow(패널 질의 성능 미확인) | — |

---

## 6. 기록 규약 · 범위 밖

- 슬라이스 완료마다 DESIGN §기록 5항. 결함은 `.claude/rules/pr-review.md` 4요소. baseline diff 는 커밋 메시지.
- 범위 밖: 팩터 값·통계 정규화·정책 적용 · TR(배당락 원천 없음) · 거래비용·호가·상하한·공매도 금지 규칙표(엔진 저장소 이슈) · 관리종목 과거 상태 복원 · 코넥스 · 지수 구성 PIT · 문서층 P2~P4 자체 · 일일 증분.

---

## 7. 결정 (5 확정 · 6·7 신설)

| # | 결정 | 상태 |
|---|---|---|
| 1~4 | 산출 형식 · 판본·게이트 · 팩터 ID 54 · 유니버스 정책표 | 확정(09-05 "작업 진행") |
| 5 | **엔진 커널 3포트 + 워크벤치 5포트, 단일 어댑터 `equity_duckdb`**(v1.1 의 "팩터층 주입" 폐기) | 확정(재기술) |
| **6** | `price.close` = 원주가 · `price.adj_close` = 조정가(전방 조정 `v_adj_price_fwd`, S21 후속 09-05) 두 필드 제공. 레지스트리의 수익률·모멘텀·변동성 팩터가 `adj_close` 를 쓰도록 워크벤치 이슈 발행 | **확정(09-05)** |
| **7** | 워크벤치 어댑터는 duckdb 필요 → `backend` optional-dependency `equity = ["duckdb>=1.5"]` 추가(코드 규칙 "새 라이브러리 금지" 예외, S21 에서 반영). 커널 어댑터(S07)는 pyarrow 로 새 의존성 0 | **확정(09-05) · 반영(S21 축소 — `backend/pyproject.toml`·`uv.lock` duckdb 1.5.5·CI `uv sync --extra parquet --extra equity`)** |

---

## 8. 검수 기록 (v1.1 → v1.2, 2026-09-05)

Opus 제안서 4건(`reviews/`): 문서층 통합 · 게이트 명세 · 목적 추적·워크플로우 · 구현 골격. 오케스트레이터 검증(코드·서버) 후 반영.

| 출처 | 채택 | 기각·조정 |
|---|---|---|
| 워크플로우 | 결정 5 재기술(워크벤치 5포트 실재, `_container.py` 확인) · S03/S03B 분리 · S07 앞당김 · 4·4B·3 분해(23슬라이스) · `factor_readiness`+EG10 · `EQUITY_FIELD_MAP.md` · `krx_kis_close_ratio` 컬럼 삭제(게이트 metric 으로만) · coverage_gap 행 생성 폐기 · as-of 표본 S02 등재 · profile 세션 랙·cell kind · `delay_days` · FACTORS §0 2열 | "정정 링크 92%" 는 같은 축(date exact) 실측이라 C340 94.8% 와 정합 — 위험 아님으로 정정 |
| 문서층 통합 | `disclosure_version` grain `rcept_no`·링크 흡수 · `has_correction` → 팩트+뷰 판정 · `period_end` 문서 정본 · `stg_doc_section` 미사용 · 4A/4C/4B′ · `vintage_kind` · items 값 미사용 · stage 요청 8건 | — |
| 게이트 | GATES v1.0 채택(EG7→EG1 순서, 항진명제 제거, 부정 픽스처, EG10~EG20) | A13(엔진 별도 저장소) 기각 — 같은 모노레포 · C17(consensus 4테이블) 기각 — 실재 |
| 구현 골격 | 모듈 배치·`.sql` 빌드·`content_hash` tmp·카탈로그 재생성·T0~T11 | 어댑터 배치: 커널 = pyarrow(A안), 워크벤치 = duckdb(결정 7) |
