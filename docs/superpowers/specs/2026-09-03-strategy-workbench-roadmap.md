# Strategy Workbench 전체 구현 로드맵

> 작성: 2026-09-03
>
> 상태: M5 완료 — Single backtest/전문 결과 화면 수직 슬라이스 완결
>
> 체크리스트: 150개 중 77개 완료, 73개 남음
>
> 다음 체크: YAML-first initiative Phase 1.5(backtest correctness gate) 완료 후 M6-1 `domain.experiment`
> SearchSpec/ParameterSpace/Constraint 추가
>
> 진행 중 initiative: YAML-first authoring 전환 — PR 단위 상태는
> [docs/planning/strategy-workbench-yaml-ui/PLAN.md](../../planning/strategy-workbench-yaml-ui/PLAN.md)만
> 추적한다 (16절 참고).
> 진행 규칙: 구현·테스트·문서가 모두 끝난 항목만 `[x]`. 각 M 완료 시 이 머리말과 완료 기록을 갱신한다.

## 1. 결론

재료는 충분하다. 추가 기능 아이디어보다 먼저 고정해야 할 것은 사실의 owner, import 방향,
재현 가능한 실행 단위다. 제품은 아래 한 문장으로 설계한다.

> 전문 트레이더가 verbose YAML/JSON source로 `StrategySpec`을 작성하고, backend가 이를
> PIT factor plan과 target tape로 컴파일한 뒤 Persistent Rust Engine으로 실행하며, 모든
> 후보를 원시 지표·데이터 판본·실험 이력과 함께 비교하는 전문가용 연구 도구.
>
> (2026-09-04 개정: Quick Builder/Advanced Graph no-code 편집기는 legacy route로 유지되며
> 삭제 조건은 [Strategy Authoring Contract ADR](./2026-09-04-strategy-authoring-contract-adr.md) D9.)

백엔드와 UX를 별도 단계로 만들지 않는다. 각 마일스톤은 항상
`domain contract → application/API → 화면 → 사용자 시나리오 테스트`까지 닫는 수직 슬라이스다.

## 2. 설계 입력과 현재 고정점

- 실행 커널: `backend/src/backtest_engine`의 `StrategyRequirements → StrategyEvent/Context →
  StrategyDecision → Action → Order/Fill/Portfolio` 계약.
- 고성능 상태 owner: `core="rust"` Persistent Rust Engine. 주문·그룹·포트폴리오·queue·accounting
  mutable state는 Rust가 소유한다.
- 현재 지표: `PerformanceMetrics`의 total return, CAGR, volatility, Sharpe, Sortino, MDD,
  Calmar, turnover. 전문 후보 비교에 필요한 지표는 아직 부족하다.
- 데이터 계획: Equity ERD v0.1의 14개 테이블과 PIT `available_date` 원칙. 최신 인계 문서는
  `workspace/dongmin/docs/EQUITY_KICKOFF.md`의 v0.2 개정 사항(`security_span`, `coverage_gap`,
  consensus vintage, `disclosure_version`, broker opinion)을 우선한다.
- 팩터 재료: `workspace/dongmin/docs/FACTORS.md`의 50개 팩터 후보. 이 문서는 재료 대장이며
  실행 시맨틱 SoT는 이후 backend Factor Registry로 승격한다.
- 구조 참고: Library.michelo 리팩토링 보드와 `.claude/rules/package-boundary.md`의
  노드별 facade/`DEPENDS_ON`/DAG gate.
- frontend 참고: `michelo.frontend`의 FSD, API SDK, 상태 owner, shared boundary, UI/test 규칙.

## 3. 시스템 한 컷

```text
Frontend
  YAML/JSON source editor ─ compile ─ StrategySpec ── generated OpenAPI SDK
  JSON/Form/Graph/Diff (read-only projection) ┘       │
  (legacy Quick/Advanced: migration 기간 별도 route)  │
                                                    ▼
Backend inbound adapter                         HTTP + SSE
                                                    │
                                                    ▼
Application use cases
  design strategy ─ run backtest ─ search parameters ─ compare results
       │                 │                  │
       └────────── outbound ports ──────────┘
            │              │                 │
            ▼              ▼                 ▼
     EquityDataPort   BacktestExecutor   ExperimentRepository
       │       │              │             │          │
       ▼       ▼              ▼             ▼          ▼
  Mock Equity  DuckDB    TargetTapeStrategy  SQLite   Artifact files
  (현재 기준)  (교체)     → Persistent Rust  → PostgreSQL/Object storage
```

핵심 절단면은 `TargetTape`다. Factor Engine은 세션별 종목 점수와 목표 비중을 계산하고,
Backtest Engine은 그 목표를 언제 어떤 주문으로 바꾸고 체결·회계할지만 담당한다. factor 식을
Rust event loop에 넣지 않고, 주문/포트폴리오 상태를 Python factor layer로 끌어올리지 않는다.

## 4. 목표 저장소 구조

```text
backend/
├─ src/backtest_engine/              # 검증된 Python 실행 커널 API
├─ rust/backtest_core/               # Persistent Rust 구현
├─ src/strategy_workbench/
│  ├─ domain/
│  │  ├─ equity/                 # PIT query/result와 coverage 의미
│  │  ├─ factor/                 # FactorDefinition, typed expression DAG
│  │  ├─ strategy/               # StrategySpec와 pipeline policy
│  │  ├─ experiment/             # SearchSpec, split, trial identity/state
│  │  └─ analytics/              # MetricDefinition과 비교 의미
│  ├─ application/
│  │  ├─ equity_workspace/       # catalog/preview
│  │  ├─ strategy_design/        # validate/explain/revision
│  │  ├─ backtest_run/           # compile/run/result
│  │  ├─ experiment_search/      # plan/schedule/cancel
│  │  └─ result_compare/         # query/Pareto/pin selection
│  ├─ adapters/
│  │  ├─ inbound/http_api/       # FastAPI/OpenAPI/SSE, business logic 0
│  │  └─ outbound/
│  │     ├─ equity_mock/          # 현재 기준 구현
│  │     ├─ equity_duckdb/        # 실제 DB 수신 후 추가
│  │     ├─ factor_sql/           # DAG → PIT SQL/Arrow batch
│  │     ├─ backtest_engine/      # TargetTape → 현 Strategy/RunConfig
│  │     ├─ experiment_sqlite/    # local-first metadata
│  │     └─ artifact_files/       # immutable Parquet/JSON artifacts
│  └─ bootstrap/                  # concrete 조립의 유일한 위치
├─ tests/
│  ├─ architecture/
│  ├─ contract/
│  ├─ unit/
│  └─ integration/
└─ README.md

frontend/
├─ src/
│  ├─ app/                        # router/providers/composition
│  ├─ pages/                      # strategy-builder/results/run-detail
│  ├─ widgets/                    # factor-canvas/candidate-table/charts
│  ├─ features/                   # edit-strategy/configure-search/run-backtest/...
│  ├─ entities/                   # strategy/factor/experiment/metric/dataset
│  └─ shared/                     # generated SDK/UI primitive/token/lib
├─ e2e/
└─ README.md
```

기존 Python/Rust 커널, 테스트, 예제, 벤치마크, 스크립트, reference 자료는 M0에서
`backend/` 아래로 물리 이동했다. Strategy Workbench는 같은 폴더 안에서도 facade adapter 하나로만
커널을 소비하며 앱 코드의 커널 내부 deep import는 architecture gate로 차단한다.

## 5. SoT 대장

| 사실 | 유일한 owner | 식별/버전 | 파생·소비자 |
|---|---|---|---|
| raw equity 값·공개 시점·coverage | Equity DB view + `dataset_profile` | data snapshot/build ID | Equity adapter |
| Equity 조회 의미 | `EquityDataPort` | port version | mock/DuckDB adapter |
| raw PIT 관측(원천 필드·공개일·멤버십·섹터) | `RawObservationPort` (`application/portfolio_design`) | port version | mock/DuckDB adapter |
| 팩터 식·방향·단위·입력·결측 정책 | Factor Registry | `factor_id@version` | catalog, compiler, UI |
| 전략 의미 | immutable `StrategySpec` revision | schema version + canonical hash | source editor, projection view, compiler (legacy editor는 migration 기간) |
| 탐색 공간 | `SearchSpec` | schema version + hash | planner/optimizer |
| 해소된 한 후보 | `ResolvedStrategySpec` | base hash + params hash | factor compiler |
| 세션별 목표 비중 | `TargetTape` derived artifact | input fingerprint | engine adapter |
| 주문·체결·포트폴리오 mutable state | Persistent Rust Engine | engine build/git SHA | BacktestResult |
| 지표 공식·방향·단위 | Metric Registry | `metric_id@version` | analytics/API/UI |
| 실행 가정과 재현성 | immutable Run Manifest | run fingerprint | result/audit/export |
| experiment/trial lifecycle | Experiment Repository | monotonic event/revision | worker/SSE/UI |
| 최종 후보 선택 | user selection record | strategy revision + trial ID | compare UI |
| 미저장 draft·그래프 좌표 | frontend feature/local state | draft ID | editor only |

두 Equity port는 같은 셀에 같은 답을 해야 한다: `EquityDataPort`와 `RawObservationPort`가
같은 (security, date, field)에 대해 같은 값과 같은 `available_date`를 돌려주는 것이 어댑터
계약이며, `backend/tests/contract/test_raw_observation_port.py`가 이를 셀 단위로 강제한다.
`universe_member`와 `sector_id`에는 공개일이 없으므로 as_of vintage로 답할 책임도 어댑터에
있고 application은 검증하지 못한다.

두 위치를 동시에 고쳐야 같은 의미가 유지된다면 SoT 위반이다. generated OpenAPI/TypeScript,
execution plan, TargetTape, metric view, composite score, cache는 모두 파생물이다.

## 6. 책임 경계

| 영역 | 소유한다 | 소유하지 않는다 |
|---|---|---|
| Frontend | draft 편집, UI layout, 사용자 선택, 시각화 | 전략/팩터/지표 공식, 서버 state transition |
| HTTP adapter | wire validation, auth, pagination, SSE 변환 | 계산, 탐색, 실행 정책 |
| Application | 명령·조회 orchestration, transaction boundary, port 호출 | SQL, 파일 형식, React UX |
| Strategy domain | pipeline/AST 타입, semantic validation | DB 조회, 실행 mutable state |
| Factor domain/engine | expression type, DAG, normalization, neutralization | 주문·체결·후보 선택 |
| Equity adapter | PIT 조회와 외부 schema 매핑 | factor formula, imputation |
| Experiment planner | 후보 생성, constraint, seed, budget, lifecycle | metric formula, portfolio accounting |
| Backtest adapter | TargetTape와 현재 엔진 계약 변환 | factor 계산, 결과 랭킹 |
| Rust engine | order/fill/queue/portfolio/accounting state | factor/search/UI |
| Analytics | metric 계산·scope·version | composite winner 결정 |
| Repository/artifact store | immutable revision, audit, atomic commit | 의미 재해석 |

## 7. 핵심 계약

### 7.1 StrategySpec

`StrategySpec`은 UI form JSON이 아니라 버전된 typed AST/DAG다.

```text
identity (revision envelope가 소유, spec_hash 제외)
  strategy_id, revision
schema_version (document top-level)
title, description (document)
data
  market, date range, universe, eligibility, dataset lag overrides
signal
  factor graph, transforms, composite, rank/threshold/regime
portfolio
  long/short sides, selection count/percentile, weighting, rebalance, buffers
risk
  gross/net exposure, per-name/sector caps, turnover/liquidity rules, stop policy
execution
  timing, order style, participation, fee/slippage/borrow/margin
parameters
  named typed references with bounds/choices/defaults/constraints
```

지원 expression 노드군:

- scalar: add/subtract/multiply/divide, clip, log, abs, sign, if/else.
- time-series: lag, return, delta, rolling mean/std/min/max, EWMA, volatility.
- cross-sectional: rank, percentile, z-score, winsorize, bucket/quantile.
- grouping: sector/market group rank, demean, neutralize, exposure cap.
- events/regime: disclosure age, revision, flow/short/credit/event flags, market regime.
- composition: saved factor/subgraph reference, parameter reference.

각 node는 input/output type, unit, minimum history, PIT field dependency, missing policy를 선언한다.
DAG cycle, unit mismatch, division risk, insufficient history, unavailable dataset은 실행 전 validation
issue로 반환한다. UI는 그 issue를 node와 field에 연결해 보여준다.

v1 authoring은 canonical field name과 raw value를 그대로 쓰는 verbose YAML/JSON source다. Form과
Graph는 현재 valid spec을 읽는 projection이며 새 편집 모델이 아니다. legacy Quick Builder는 허용된
subgraph를 form으로, Advanced Graph는 전체 DAG를 편집하지만 migration 기간에만 유지된다
(ADR D2, D5, D9).

### 7.2 SearchSpec과 trial identity

탐색 설정은 StrategySpec에 섞지 않는다.

```text
SearchSpec
  base_strategy_revision
  parameter spaces: int / float / bool / choice
  conditional constraints
  sampler: grid / seeded random / Latin hypercube / TPE / CMA-ES
  budget: max trials / wall time / parallelism
  objective: single / Pareto multi-objective
  validation protocol
  deterministic seed
```

trial fingerprint는 최소
`base spec hash + resolved params + data snapshot + factor registry revision + engine version +
metric registry revision + cost config + split + seed`를 포함한다. 같은 fingerprint는 재사용할 수
있지만 cache hit임을 기록한다. 실패·pruned·cancelled trial도 삭제하지 않는다.

### 7.3 Run Manifest

모든 single run과 trial은 다음을 immutable manifest로 남긴다.

- StrategySpec/SearchSpec revision과 canonical hash.
- resolved parameters.
- Equity snapshot/build ID와 실제 field/profile version.
- calendar, universe, factor registry, engine/core, metric registry version.
- benchmark, risk-free assumption, annualization, return convention.
- fee, tax, slippage, borrow, margin, execution delay/participation.
- IS/validation/OOS split, embargo/purge, seed.
- 시작/종료 시각, status, error code/detail, artifact hash.

### 7.4 Metric Registry

Metric 정의는 `metric_id`, version, family, label, direction, unit, precision, required series,
scope support, config schema, implementation을 한 곳에서 소유한다. UI column 목록도 API catalog에서
파생한다.

항상 보이는 핵심 후보 지표:

- 성과: total return, CAGR, monthly/annual returns.
- 위험조정: Sharpe, Sortino, Calmar, information ratio.
- 위험: annual volatility, MDD, drawdown duration/recovery, downside deviation, VaR/CVaR.
- 벤치마크: alpha, beta, tracking error, upside/downside capture.
- 거래: turnover, gross/net cost, slippage, trade count, win rate, profit factor, expectancy, payoff.
- 노출: gross/net exposure, leverage, cash, long/short attribution, max name/sector concentration.
- 팩터: IC, rank IC, ICIR, quantile spread, monotonicity, coverage, turnover, decay, correlation.
- 강건성: split별 성과, parameter sensitivity/plateau, cost stress, delay stress, trial count,
  probabilistic/deflated Sharpe.

`None`/N.A.와 0을 구분하고 공식 version을 저장한다. composite score는 사용자 정의 view이며
raw metrics를 숨기거나 “최고 전략”을 자동 확정하지 않는다.

## 8. Equity DB를 기다리지 않는 방식

현재 `MockEquityDataAdapter`가 실제 계약 개발의 기준이다.

- `snapshot()`: 데이터 판본.
- `list_fields()`: field id, dataset, 단위, 공개 기준, 권장 lag, 설명.
- `load_universe()`: 기간별 point-in-time 구성.
- `load_panel()`: 세션별 available cutoff를 적용한 연구 panel.
- fixture는 consensus revision, filing delay, next-session market-cap lag,
  `SOURCE_OMITTED_ZERO/MISSING/NOT_COLLECTED/COVERAGE_GAP`을 표현한다.

실제 DB가 오면 다음 순서만 허용한다.

1. v0.2 read-only view 이름/컬럼/판본 manifest를 adapter contract table로 작성.
2. `equity_duckdb` adapter를 추가하고 mock contract test를 그대로 parameterize.
3. mock과 DuckDB의 golden query 결과를 소형 hand-calculated fixture로 대조.
4. application/domain/frontend 변경 없이 composition root 설정만 `mock`→`duckdb`로 전환.
5. 미지원 field/capability는 명시적으로 catalog에서 unavailable 처리. mock fallback 금지.

물리 테이블 이름은 adapter 내부 사실이다. application은 `price_daily`, `fin_std` 같은 이름을
직접 SQL로 참조하지 않는다.

## 9. UX 정보 구조

### 9.1 Strategy Builder

왼쪽 outline(문서 섹션 탐색, 상단 중복 stepper 없음)과 중앙 editor, 오른쪽 항상 보이는
Validation/Estimate panel로 구성한다. 아래 번호는 outline 섹션이지 wizard 단계가 아니다.

1. 데이터/유니버스: 시장, 기간, 상장/관리/유동성/시총 필터, coverage와 available-date 설명.
2. 팩터: catalog 검색, factor card, 방향/단위/coverage, transform chain, 조합 weight.
3. 포트폴리오: long/short, top/bottom N 또는 percentile, equal/factor/risk weight, rebalance.
4. 위험/실행: 노출 cap, sector neutral, turnover buffer, 비용·슬리피지·지연·참여율.
5. 파라미터: 탐색 대상 토글, 범위/분포/step, constraint, 예상 trial/time/memory.

YAML/JSON/Form/Graph/Diff view 전환은 페이지 이동이 아니라 같은 source의 표현 전환이며 source와
undo history를 보존한다. 우측 panel은
backend `/validate`, `/explain`, `/estimate` 결과를 표시하며 error는 실행을 막고 warning은 사용자가
확인한 기록을 남긴다.

### 9.2 Experiment Monitor

- 전체 queued/running/completed/failed/pruned/cancelled 수와 ETA.
- trial별 parameter, status, 핵심 raw metrics, error reason.
- 중단/재시도는 idempotent command. 완료 trial은 experiment 취소 후에도 조회 가능.
- 숨은 실패 없이 denominator를 유지한다.

### 9.3 Candidate Explorer

- 열 고정 가능한 raw metric table: CAGR, Sharpe, Sortino, Calmar, MDD, volatility, turnover,
  costs, trades, exposure, IS/OOS/robustness.
- scatter/Pareto, heatmap, parallel coordinates, parameter response curve.
- 후보 클릭 시 equity/drawdown/monthly returns/rolling metrics/exposure/trades/factor diagnostics.
- manifest, data coverage, warnings, failed assumptions를 같은 detail 화면에서 확인.
- “선택/핀”은 사용자 action이고 선택 이유를 기록한다.

## 10. 상태 머신과 불변식

```text
Strategy: DRAFT(client only) -> SAVED REVISION(immutable) -> ARCHIVED

Experiment: QUEUED -> RUNNING -> COMPLETED
                    ├-------> FAILED
                    └-------> CANCELLING -> CANCELLED

Trial: QUEUED -> RUNNING -> COMPLETED | FAILED | PRUNED | CANCELLED
```

- revision은 update하지 않고 새 revision을 만든다.
- `COMPLETED` run만 완결 metric/artifact set을 가질 수 있다.
- artifact commit과 status 완료 전환은 원자적이다.
- trial status는 뒤로 가지 않는다. retry는 새 attempt record다.
- OOS는 잠금 ledger를 둔다. 한 번 본 OOS를 다시 “미사용 OOS”로 표시하지 않는다.
- 모든 연구 query는 각 세션 기준 `available_date <= cutoff`를 만족한다.
- historical universe, delisted/relisted span, coverage gap을 보존한다.
- seed와 순서가 같으면 candidate 생성과 결과 fingerprint가 같다.
- frontend가 계산한 metric은 공식 결과로 저장·표시하지 않는다.

## 11. 구현 마일스톤

각 M은 backend와 UX를 함께 끝낸다. API가 없는 임시 UI, 화면이 없는 백엔드 대량 구현을 오래
유지하지 않는다.

### M0 — 경계·mock·개발 골격

- [x] `backend/`와 `frontend/`를 분리하고 각 책임을 README에 기록.
- [x] backend를 domain/application/adapters/bootstrap 헥사고날 방향으로 배치.
- [x] 노드별 `facade/`와 `DEPENDS_ON` 규칙을 Claude rule로 지정.
- [x] 선언 밖 dependency, cycle, facade 우회 import를 잡는 architecture test 추가.
- [x] frontend에 FSD 6개 layer 폴더와 owner 설명 추가.
- [x] FSD/API-state/UI/test 규칙을 `michelo.frontend` 기준으로 차용·경로 지정.
- [x] `EquityDataPort`와 PIT query/result 타입 첫 버전 추가.
- [x] revision/lag/zero/missing/coverage gap을 가진 deterministic mock adapter 연결.
- [x] mock/architecture unit test 7개 통과.
- [x] backend 독립 package/test 설정과 root 커널 dependency 방식을 확정.
- [x] frontend React/TypeScript/Vite toolchain과 lint/boundaries plugin을 초기화.
- [x] CI에 backend architecture/test/lint와 frontend typecheck/lint/test job 추가.

완료 게이트: 새 코드의 물리 경로만 봐도 owner와 import 방향을 설명할 수 있고, 실제 DB 없이
frontend가 mock catalog/preview를 호출할 수 있다.

### M1 — StrategySpec 계약 + Builder shell

- [x] backend `domain.strategy` 노드와 immutable StrategySpec v1 모델 추가.
- [x] Universe→Eligibility→Factor→Signal→Portfolio→Risk→Execution pipeline 타입 고정.
- [x] typed expression node union과 parameter reference 타입 고정.
- [x] canonical JSON serialization/hash와 revision identity 추가.
- [x] syntax/semantic/capability validation issue 모델 추가.
- [x] strategy create/get/revise/validate/explain application use case 추가.
- [x] in-memory strategy repository adapter와 contract test 추가.
- [x] HTTP adapter의 StrategySpec endpoint/OpenAPI 첫 절단면 추가.
- [x] generated TypeScript SDK를 `shared/api` 단일 gateway로 연결.
- [x] Strategy Builder page shell과 5단계 navigation 추가.
- [x] Quick/Advanced가 같은 draft object를 읽는 editor host 추가.
- [x] 저장 전/저장 후 revision, dirty state, validation panel UX 테스트 추가.

완료 게이트: 사용자가 빈 전략을 만들고 수정·검증·새 revision으로 저장하며, network payload와
frontend 타입이 backend schema에서 생성된다.

### M2 — Equity catalog/preview 수직 슬라이스

- [x] mock field catalog를 API로 노출하고 검색/필터/pagination 계약 추가.
- [x] 데이터 snapshot과 field별 unit/availability/lag/coverage capability 반환.
- [x] universe history preview API와 session coverage summary 추가.
- [x] panel preview API에 row/column limit와 cost estimate 추가.
- [x] `dataset` entity와 query keys/hooks 추가.
- [x] Builder 데이터/유니버스 화면 추가.
- [x] field tooltip에 내용일/공개일/권장 lag/근거 표시.
- [x] actual zero, missing, not-collected, coverage-gap 시각 구분 추가.
- [x] coverage 부족·lag override warning 확인 UX 추가.
- [x] mock MSW가 아닌 실제 backend mock adapter를 쓰는 통합 테스트 추가.
- [x] PIT revision이 공개일 전 UI preview에 나타나지 않는 E2E 추가.

완료 게이트: 실제 Equity DB 없이 데이터 선택 UX와 PIT 설명을 끝까지 검증할 수 있다.

### M3 — Factor Registry + 조합 editor

- [x] `domain.factor` 노드와 FactorDefinition/FactorRegistry SoT 추가.
- [x] `FACTORS.md` 50개 ID와 Equity field 요구사항 mapping 대장 작성.
- [x] 가격/재무/컨센서스/수급/공매도/신용/이벤트 mock factor subset 구현.
- [x] arithmetic/time-series/cross-sectional/group/conditional node 타입 추가.
- [x] DAG cycle/type/unit/min-history/missing-policy validator 구현.
- [x] winsorize/z-score/rank/neutralize/lag transform 구현.
- [x] parameter reference와 saved subgraph/factor reference 구현.
- [x] DAG→PIT execution plan compiler와 deterministic plan hash 추가.
- [x] factor matrix cache key에 data/factor/params/as-of fingerprint 포함.
- [x] IC/rank IC/quantile spread/coverage/turnover/decay 분석 구현.
- [x] factor catalog API와 validate/explain/preview API 추가.
- [x] `factor` entity와 factor browser/card UI 추가.
- [x] Quick transform chain/weight editor 추가.
- [x] Advanced typed node graph, port type, inline validation 추가.
- [x] Quick↔Advanced↔StrategySpec lossless property test 추가.

완료 게이트: 동일 spec/data snapshot이 동일 factor plan/value를 만들고, 두 UI 모드 사이 정보 손실이
없다.

### M4 — Portfolio pipeline + TargetTape

- [x] eligibility filter와 point-in-time universe 결합 구현.
- [x] composite score, rank, threshold, regime signal 구현.
- [x] top/bottom N·percentile, long-only/long-short 선택 구현.
- [x] equal/factor-score/rank/risk weight 구현.
- [x] gross/net/name/sector cap과 neutralization 구현.
- [x] turnover buffer, minimum trade/liquidity rule 구현.
- [x] every-N-session/weekly/month-end/quarterly rebalance calendar 구현.
- [x] pipeline 결과를 immutable TargetTape로 컴파일.
- [x] StrategyRequirements/SetPortfolioTarget로 변환하는 engine adapter 추가.
- [x] schedule/action/short/margin capability를 실행 전 협상.
- [x] Builder 포트폴리오·위험·실행 화면 추가.
- [x] 세션별 구성 종목/score/target/exclusion 이유 preview 추가.
- [x] T 종가 신호가 T+1 이전에 체결되지 않는 통합 E2E 추가.

완료 게이트: 사용자가 만든 mock factor 전략이 설명 가능한 TargetTape가 되고 기존 Python/Rust
engine에서 동일하게 실행 준비된다.

### M5 — Single backtest + 전문 결과 화면

- [x] `domain.analytics` MetricDefinition/MetricRegistry 추가.
- [x] BacktestRunSpec과 Run Manifest 모델 추가.
- [x] Equity research data를 engine Bar/Universe/CorporateAction port로 잇는 adapter 추가.
- [x] TargetTapeStrategy→`BacktestEngine(core="rust")` executor 구현.
- [x] Python reference executor를 패리티/debug 선택지로 유지.
- [x] raw snapshot/order/fill/cost/position artifact schema 고정.
- [x] current 8개 지표를 registry versioned implementation으로 흡수.
- [x] MDD duration/recovery, benchmark, trade, exposure, cost 지표 확장.
- [x] `None`과 0, scope(full/IS/validation/OOS/window) 직렬화 계약 추가.
- [x] local artifact store와 atomic run commit 구현.
- [x] run start/status/result/cancel API와 SSE progress 추가.
- [x] Run Detail에 equity/drawdown/monthly/rolling/exposure/trades 차트 추가.
- [x] raw metric table과 manifest/data warning drawer 추가.
- [x] Python/Rust result·metric golden parity 테스트 추가.

완료 게이트: UI에서 single run을 실행하고 raw metrics와 모든 재현 가정을 확인할 수 있으며
Python/Rust 결과가 같다.

### M6 — Parameter Search + 실험 진행 UX

- [ ] `domain.experiment` SearchSpec/ParameterSpace/Constraint 추가.
- [ ] path 기반 parameter binding과 ResolvedStrategySpec 생성 구현.
- [ ] invalid combination을 실행 전 제거하는 constraint evaluator 구현.
- [ ] trial count/time/memory estimate 구현.
- [ ] deterministic grid sampler 구현.
- [ ] seeded random과 Latin hypercube sampler 구현.
- [ ] sampler plugin port를 두고 TPE/CMA-ES는 후속 adapter로 격리.
- [ ] fingerprint duplicate/cache-hit 판정 구현.
- [ ] SQLite experiment repository와 migration 추가.
- [ ] experiment/trial/attempt 상태 전이·audit event 구현.
- [ ] bounded process worker, parallelism, cancellation, crash recovery 구현.
- [ ] failed/pruned/cancelled trial과 error reason 보존.
- [ ] experiment create/status/trials/results/cancel/retry API 추가.
- [ ] Builder 파라미터 범위/constraint/budget 화면 추가.
- [ ] Experiment Monitor 진행률·ETA·상태별 count·실패 상세 추가.
- [ ] cancel/restart/idempotency 통합 테스트 추가.

완료 게이트: 사용자는 실행 전 비용을 알고 탐색을 시작·중단·재개할 수 있고, 모든 trial이
감사 가능한 denominator에 남는다.

### M7 — 강건성·OOS·과적합 방지

- [ ] train/validation/locked-OOS SplitSpec 추가.
- [ ] rolling/anchored walk-forward와 window metrics 구현.
- [ ] label horizon 기반 purge/embargo 구현.
- [ ] OOS 열람 ledger와 재사용 상태 표시 구현.
- [ ] parameter sensitivity와 plateau/stability score 구현.
- [ ] fee/slippage/borrow/execution-delay stress grid 구현.
- [ ] return/trade bootstrap 또는 Monte Carlo adapter 구현.
- [ ] probabilistic Sharpe와 deflated Sharpe 구현.
- [ ] trial count와 selection bias metadata를 결과에 포함.
- [ ] single-objective ranking과 Pareto frontier view 구현.
- [ ] robustness report API 추가.
- [ ] Candidate Explorer heatmap/scatter/parallel coordinate 추가.
- [ ] IS/validation/OOS와 stress 결과를 숨기지 않는 detail UX 추가.
- [ ] OOS를 다시 미사용으로 표시할 수 없는 state/E2E 테스트 추가.

완료 게이트: 최고 in-sample 점수 하나가 아니라 성과·위험·비용·안정성 trade-off로 후보를
선택할 수 있다.

### M8 — 전문가용 표현력과 작업 흐름

- [ ] long/short leg별 서로 다른 factor graph와 universe 지원.
- [ ] regime switch와 conditional portfolio branch 지원.
- [ ] event-driven eligibility/signal node 지원.
- [ ] custom formula editor에 autocomplete/type/unit feedback 추가.
- [ ] reusable factor/subgraph library와 version pin 추가.
- [ ] strategy clone/diff/revision history UX 추가.
- [ ] candidate pin/compare/note/selection record 추가.
- [ ] experiment/result CSV·Parquet·JSON manifest export 추가.
- [ ] StrategySpec readable report와 Python example export 추가.
- [ ] 사용자 preset/template은 spec revision reference로 구현.
- [ ] keyboard navigation, screen reader label, focus management audit.
- [ ] 대규모 table virtualization과 chart downsampling 구현.
- [ ] draft autosave/recovery와 server revision conflict UX 구현.
- [ ] 임의 Python plugin은 sandbox/reproducibility 별도 spec 전까지 제외.

완료 게이트: 전략 정의는 verbose source로 작성하되 parameter search와 실험 실행은 source를 다시
편집하지 않고 UI에서 수행할 수 있고, 전문 사용자는 typed graph와 식으로 제약 없이 확장하며 결과를
재현할 수 있다 (2026-09-04 ADR D8로 조정).

### M9 — 실제 Equity DuckDB adapter 전환

- [ ] Equity v0.2 view contract와 mock field mapping 확정.
- [ ] data snapshot/manifest 판독과 read-only connection 구현.
- [ ] security/security_span/corp_ticker identity mapping 구현.
- [ ] universe_asof와 coverage_gap mapping 구현.
- [ ] price/adjustment/corporate-action mapping 구현.
- [ ] financial/disclosure revision PIT mapping 구현.
- [ ] consensus first-seen vintage와 broker opinion mapping 구현.
- [ ] flow/short/credit missing-kind mapping 구현.
- [ ] dataset_profile column-scope lag/capability mapping 구현.
- [ ] parameterized query, row limit, timeout, cancellation 구현.
- [ ] mock/DuckDB shared port contract suite 통과.
- [ ] hand-calculated PIT/leakage golden suite 통과.
- [ ] schema mismatch/unsupported field fail-closed UX 추가.
- [ ] composition root 설정으로 mock↔DuckDB 명시 전환.

완료 게이트: domain/application/frontend 수정 없이 adapter만 바꿔 실데이터를 사용하고, mock에서
검증한 모든 PIT 불변식이 유지된다.

### M10 — 성능·운영·릴리스

- [ ] 100/300/전체 유니버스 single-run benchmark 기준선 추가.
- [ ] 10/100/1,000 trial throughput·peak RSS·artifact size 측정.
- [ ] factor query/transform/TargetTape/engine/metric 구간별 profile 추가.
- [ ] Arrow/Parquet batch와 content-addressed factor cache 최적화.
- [ ] worker quota, max combinations, timeout, disk budget guard 추가.
- [ ] SQLite→PostgreSQL, file→object store adapter contract 검증.
- [ ] API auth/authorization과 project ownership 추가.
- [ ] audit log, structured log, trace ID, metrics/health endpoint 추가.
- [ ] schema/repository migration과 backward compatibility policy 추가.
- [ ] 백업/복원, crash recovery, orphan job reconciliation 검증.
- [ ] threat model: formula DoS, path traversal, artifact injection, secret masking.
- [ ] accessibility/visual regression/browser compatibility E2E 추가.
- [ ] example strategy 5종과 사용자 온보딩/용어 도움말 작성.
- [ ] release checklist와 rollback 절차 작성.
- [ ] 전체 acceptance suite와 재현성 export/import 검증.

완료 게이트: 전문 사용자가 장시간 탐색을 안전하게 실행하고, 다른 환경에서 manifest로 결과를
재현하며, 장애 후에도 실험 이력을 잃지 않는다.

## 12. 전역 검증 게이트

Backend:

```powershell
cd backend
uv sync --extra parquet
uv run pytest -q
uv run ruff check src tests examples scripts
uv run pyright
uv run cargo fmt --manifest-path rust/backtest_core/Cargo.toml -- --check
uv run cargo clippy --manifest-path rust/backtest_core/Cargo.toml --all-targets -- -D warnings
uv run cargo test --manifest-path rust/backtest_core/Cargo.toml
```

Frontend toolchain 확정 후:

```text
npm run typecheck
npm run lint
npm run test
npm run test:e2e -- <affected specs>
```

Contract:

- OpenAPI 생성 후 working tree가 깨끗해야 한다.
- frontend generated SDK 외 수기 StrategySpec/metric DTO가 없어야 한다.
- backend dependency DAG/facade-only와 frontend FSD boundary가 CI에서 강제돼야 한다.
- mock과 실제 adapter가 같은 contract suite를 통과해야 한다.
- 같은 fingerprint는 repeated run에서 동일 결과와 manifest를 내야 한다.

## 13. 의도적으로 미룬 결정

- FastAPI/Pydantic, React/Vite/TanStack Query 같은 구체 dependency는 각 toolchain M에서 기존
  저장소 의존성을 먼저 확인하고 추가한다. 계약과 폴더 방향은 프레임워크보다 먼저 고정한다.
- optimizer는 grid/random/LHS를 먼저 완결한다. TPE/CMA-ES는 sampler port 뒤에서 추가한다.
- 실험 metadata는 local-first SQLite, artifact는 파일로 시작한다. PostgreSQL/object storage는
  같은 port contract를 통과한 뒤 운영 adapter로 승격한다.
- arbitrary Python factor/plugin은 표현력은 크지만 재현성·보안·자원 통제가 별도 문제다.
  typed DAG로 먼저 최대 범위를 제공하고 plugin sandbox는 독립 설계한다.
- 커널의 물리 이동은 M0에서 완료했다. 이후 커널 경로와 앱 adapter 경계는 독립적으로 유지한다.

## 14. 완료 정의

전체 로드맵은 화면이 뜨거나 API가 존재하는 것만으로 끝나지 않는다.

- 한 전략 의미가 StrategySpec 한 벌에만 존재한다.
- frontend/backend의 타입은 backend schema에서 파생된다.
- 실제 Equity DB 교체가 adapter 변경으로 끝난다.
- factor 계산, 실험 계획, 실행 상태, metric 계산의 owner가 겹치지 않는다.
- 모든 후보는 raw metrics, 실패 trial, 비용, split, data/engine/registry version과 함께 남는다.
- source ↔ StrategySpec ↔ projection round-trip이 lossless이고 전문 표현력을 막지 않는다.
- Persistent Rust Engine이 execution state를 계속 단독 소유한다.
- import 방향과 public surface 위반이 CI에서 실패한다.
- mock, Python reference, Rust, 실제 Equity adapter에 대한 계약/패리티/재현성 검증이 통과한다.

## 15. 완료 기록

- 2026-09-03 — M0 첫 절단면: backend/frontend 폴더, backend facade/`DEPENDS_ON` 규칙,
  frontend FSD/API-state/UI/test 규칙, EquityDataPort, deterministic PIT mock, architecture/contract
  테스트 7개 추가.
- 2026-09-03 — 기존 Python/Rust 커널, 테스트, 예제, 스크립트, 벤치마크와 reference 자료를
  `backend/` 아래로 물리 통합. 루트는 backend/frontend/docs와 저장소 운영 파일만 소유하도록 정리.
- 2026-09-03 — M0 완료: backend 독립 uv package, React/TypeScript/Vite, 생성 OpenAPI SDK,
  FSD boundaries lint, backend/frontend CI gate를 연결.
- 2026-09-03 — M1 완료: immutable StrategySpec v1 typed DAG와 parameter contract, canonical hash,
  validation/explanation, in-memory revision repository, FastAPI endpoint, Quick/Advanced 공유 draft,
  dirty/validation/revision UX와 MSW wire 테스트를 추가.
- 2026-09-03 — M2 완료: 검색·필터·pagination field catalog, snapshot/field capability,
  universe coverage와 제한·비용이 있는 PIT panel preview API를 추가. Builder 데이터 화면에서
  필드 근거와 lag를 선택하고 coverage/lag 위험을 명시적으로 확인하며, 실제 0·원천 생략 0·결측·
  미수집·coverage gap을 구분한다. 실제 backend mock HTTP 통합 테스트와 공개일 전 revision이
  노출되지 않는 UI→generated SDK→실제 FastAPI mock adapter E2E를 고정했다.
- 2026-09-03 — M3 완료: 50개 versioned Factor Registry와 7개 카테고리별 실행 가능한 mock
  graph를 추가했다. arithmetic/time-series/cross-sectional/group/conditional 표현식, cycle·type·
  unit·history·missing 검증, PIT plan/hash/cache key, IC·Rank IC·quantile spread·coverage·turnover·
  decay 분석을 backend SoT로 고정했다. Factor catalog/validate/explain/preview API와 Quick 팩터
  탐색·가중치·5종 transform·진단, Advanced typed port/inline validation을 연결하고 두 편집 모드의
  StrategySpec 무손실 속성을 테스트했다. Equity DB가 확정되기 전에는 같은 application port를
  deterministic mock adapter가 구현한다.
- 2026-09-03 — M4 완료: `domain.portfolio`가 PIT eligibility, 합성 점수·순위·레짐,
  long-only/long-short 선택, equal/factor/rank/risk 비중, gross/net/name/sector 제약,
  neutralization, 유동성·회전율 규칙과 리밸런싱 달력을 소유한다. 결과는 snapshot/spec hash와
  T 종가→T+1 실행일을 담은 immutable `TargetTape`로 컴파일된다. 미확정 Equity DB는 portfolio
  observation port의 deterministic mock으로 연결하고, engine adapter가 schedule/action/short/margin
  요구사항을 실행 전에 협상해 `SetPortfolioTarget(REPLACE, next_open)`으로 변환한다. Builder에
  포트폴리오·리스크·실행 화면과 후보 score/target/exclusion preview를 추가하고 backend HTTP 및
  frontend MSW E2E로 T+1 경계를 고정했다.
- 2026-09-03 — M5 완료: versioned `MetricRegistry`와 immutable `BacktestRunSpec`/manifest,
  raw snapshot·position·order·fill·cost·trade artifact 계약을 추가했다. Equity mock의 OHLCV·universe·
  corporate-action port가 `TargetTapeStrategy`를 Persistent Rust Engine 또는 Python reference core로
  실행하고, 기존 8개 지표와 MDD 기간/회복·benchmark·trade·exposure·cost를 포함한 21개 지표를
  같은 registry에서 산출한다. local store는 staging directory rename으로 JSON artifact를 원자
  commit하며 `None`/0과 Full·IS·Validation·OOS·Window scope를 보존한다. start/status/result/cancel와
  SSE progress API, Builder 6단계 run console, equity/drawdown/monthly/rolling Sharpe/exposure 차트,
  거래 원장·raw metric table·manifest/data warning drawer를 연결했고 Python/Rust golden parity를
  통합 테스트로 고정했다.

체크 수는 이 문서의 완료/미완료 체크박스 기준으로 갱신한다. 설명 안의 예시 checkbox는 두지
않아 수치가 실제 구현 단위와 일치하게 유지한다.

## 16. YAML-first authoring initiative와 M6~M10의 선후 관계

2026-09-04 [Strategy Authoring Contract ADR](./2026-09-04-strategy-authoring-contract-adr.md)로
authoring 방식을 verbose YAML/JSON source로 전환했다. 이 initiative의 Phase/PR 범위는
[WORKFLOW.md](../../planning/strategy-workbench-yaml-ui/WORKFLOW.md), PR 진행 상태는
[PLAN.md](../../planning/strategy-workbench-yaml-ui/PLAN.md)가 소유한다. 이 로드맵은 PR 단위
상태를 복제하지 않는다.

- 이 로드맵은 product milestone SoT로 남는다. M6~M10의 순서와 완료 게이트는 유지한다.
- initiative Phase 1.5(backtest correctness gate)는 M6 parameter search보다 먼저 끝나야 한다.
  현재 portfolio preview/backtest가 FactorGraph 대신 factor ID 기반 synthetic 값을 쓰는 결함을
  제거한다.
- M6 parameter search UI는 initiative Phase 3(YAML MVP) 이후 YAML route 위에 연결한다.
- M8 항목 중 revision history/diff, autosave/recovery, revision conflict, keyboard navigation은
  initiative P1-08, P3-06, P3-07, P4-08, P6-02, P6-03이 먼저 제공하며, 해당 PR merge 시 M8
  체크박스를 갱신한다. custom formula editor(표현식 DSL)는 initiative v1 non-goal이며 M8에 남는다.
- Quick/Advanced 편집기 삭제는 ADR D9 조건이 모두 충족될 때만 initiative P6-06에서 수행한다.
