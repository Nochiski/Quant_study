# Strategy Workbench Backend

## M5 Single backtest + analytics

`domain.analytics`의 `metric-registry-v1`이 기존 8개 성과 지표와 MDD 기간/회복,
benchmark/excess return, 거래·노출·비용을 합친 21개 정의와 공식을 소유한다.
`application.backtest_run`은 immutable `BacktestRunSpec`을 TargetTape로 컴파일하고 교체 가능한
data/executor/artifact port만 호출한다. 기본 조립은 Equity mock → `TargetTapeStrategy` →
Persistent Rust Engine → atomic local JSON artifact이며 Python reference core도 같은 계약으로 남긴다.

- `POST /api/v1/backtests`: Rust/Python core, 초기 자본, benchmark, metric scope와 함께 실행 시작.
  `strategy_source`로 저장 revision(`saved_revision`, spec_hash 대조) 또는 inline draft를 지정하고
  manifest의 `strategy_provenance`에 출처를 기록 (기존 `strategy` inline도 유지). 실제
  `backtest.run.invalid`·`portfolio.*` 422와 saved-reference 404/409는 OpenAPI/generated SDK의
  discriminated error 계약으로 함께 제공한다.
- `GET /api/v1/backtests/{run_id}`: 상태·진행률·artifact hash 조회
- `GET /api/v1/backtests/{run_id}/events`: SSE progress stream
- `GET /api/v1/backtests/{run_id}/result`: versioned metrics, 차트 series, raw artifact, manifest 조회
- `POST /api/v1/backtests/{run_id}/cancel`: 협력적 취소 요청

실제 Equity DB가 오면 `BacktestDataPort` 구현만 교체한다. domain/application과 engine executor는
OHLCV·universe membership·corporate action의 중립 계약을 유지한다. 로컬 실행 artifact는
`backend/.local/backtest-runs`에 저장되며 git에서 제외된다.

## M4 Portfolio pipeline + TargetTape

`domain.portfolio`가 PIT eligibility, 합성 score/rank/regime, long-only/long-short 선택,
equal/factor-score/rank/risk weight, exposure cap·neutralization, turnover/liquidity와 리밸런싱
달력을 소유한다. `application.portfolio_design`은 교체 가능한 observation/engine port만 알고,
결과를 snapshot/spec/tape hash와 T 종가→T+1 실행일이 고정된 immutable `TargetTape`로 반환한다.

- `POST /api/v1/portfolio/preview`: 세션별 후보, 편입·제외 이유, 목표 비중과 engine compatibility
- `POST /api/v1/strategies/debug/trace`: saved revision 또는 inline draft의 date/security/factor/node를
  제한해 raw·node·TargetTape projection과 `spec_hash/snapshot_id/registry_version/plan_hash`를 반환.
  trace와 executable factor output은 한 번의 node-cache 계산에서 파생되며 페이지·행 cap과
  cancellable raw capability/evaluator/TargetTape materialization·streaming hash의 client-disconnect
  협력적 취소를 적용. 기존 `RawObservationPort.load_raw_observations(query)`는 preview/backtest
  호환 계약으로 유지하고 trace는 별도 `CancellableRawObservationPort`를 계산 전에 협상한다.
  비세션 날짜와 snapshot에 없는 종목, 모든 StrategySpec numeric leaf의 non-finite 값과 유한
  피연산자의 산술 overflow는 빈 성공값·NaN tape 대신 coded 422로 실패한다. 404/409/422/499
  envelope, 요청 cap과 `trace.capability.unsupported` 진단은 OpenAPI/generated SDK에 명시된다.
  Raw port의 모든 숫자와 opening book은 계산 전 finite 계약을 통과해야 하고, starting holding은
  domain compiler가 TargetTape와 공유하는 실제 첫 signal frame에 존재할 때만 적용된다.
- `equity_mock`: 실제 Equity DB가 오기 전 portfolio observation port를 구현하는 deterministic adapter
- `engine_portfolio`: `StrategyRequirements`를 사전 협상하고 `SetPortfolioTarget(REPLACE)`로 변환

## M3 Factor research surface

`domain.factor`가 50개 factor ID, 표현식 타입, validation, PIT execution plan, hash/cache
fingerprint, evaluator와 전문 진단 지표의 단일 소유자다. `application.factor_research`는 다음 HTTP
use case를 제공하며, 미확정 Equity DB는 같은 outgoing port를 구현한 `equity_mock`으로 대체한다.

- `GET /api/v1/factors/catalog`: 50개 versioned 정의 검색·카테고리·구현 상태 필터
- `POST /api/v1/factors/validate`: cycle/type/unit/min-history/reference/missing-policy 검증
- `POST /api/v1/factors/explain`: topological PIT plan과 재현성 hash 설명
- `POST /api/v1/factors/preview`: IC, Rank IC, quantile spread, coverage, turnover, decay

사람이 검토하는 전체 ID/Equity field mapping은 [FACTORS.md](./FACTORS.md)에 있다. 실행 의미의
SoT는 항상 registry 코드이며 문서 정합성은 테스트로 고정한다.

전략 생성·팩터 계산·실험 관리 API가 들어갈 백엔드 애플리케이션이다. 기존
`backend/src/backtest_engine`과 `backend/rust/backtest_core`는 이 애플리케이션이 사용하는 실행 커널이며,
백엔드의 도메인·유스케이스·저장소 책임과 섞지 않는다.

## 의존성 방향

```text
adapters/inbound  ─┐
                   ├─> application ─> domain
adapters/outbound ─┘          │
                              └─> application/*/ports/outgoing

bootstrap ─> application + adapters
```

- `domain/`: 프레임워크·DB·HTTP·백테스트 엔진을 모르는 순수 정책과 값 타입.
- `application/`: 사용자 유스케이스와 필요한 outbound port. concrete adapter를 모른다.
- `adapters/inbound/`: FastAPI/OpenAPI 같은 입력 변환. business rule은 두지 않는다.
- `adapters/outbound/`: Equity DB, mock, 실행 엔진, 결과 저장소 구현.
- `bootstrap/`: concrete 구현을 고르는 유일한 composition root.
- 각 책임 노드는 `facade/<책임>.py`로만 외부 심볼을 노출하고
  `facade/__init__.py`에 허용 의존성 `DEPENDS_ON`을 선언한다.

현재 Equity DB 계약이 확정되지 않았으므로 `equity_mock`이 기준 adapter다. 실제 DB가 오면
같은 application port를 구현하는 `equity_duckdb` adapter를 추가하며, domain/application은
수정하지 않는다. mock으로 조용히 fallback하지 않고 bootstrap에서 adapter를 명시적으로 고른다.

## 현재 골격

```text
backend/
├─ src/backtest_engine/                    # Python reference/public engine
├─ rust/backtest_core/                     # Persistent Rust core
├─ tests/                                  # engine + workbench + architecture
├─ examples/ · scripts/ · benchmarks/
├─ reference/                              # 2026-08-17 Zipline 관찰 아카이브
└─ src/strategy_workbench/
   ├─ domain/equity/                       # PIT 데이터 값 타입과 query/result
   │  └─ facade/research_data.py
   ├─ domain/strategy/                     # StrategySpec v1, hash, validation, explanation
   ├─ domain/portfolio/                    # TargetTape compiler와 portfolio policy
   ├─ domain/analytics/                    # 21개 versioned metric 정의·공식
   ├─ domain/backtest/                     # run/manifest/raw artifact 계약
   ├─ application/equity_workspace/        # 검색 catalog·universe/panel PIT preview
   │  ├─ ports/outgoing/equity_data.py     # EquityDataPort
   │  └─ facade/{ports,workspace}.py
   ├─ application/strategy_design/          # create/get/revise/validate/explain
   ├─ application/portfolio_design/         # portfolio preview use case와 outgoing ports
   ├─ application/backtest_run/             # compile/run/status/result/cancel orchestration
   │  └─ ports/outgoing/strategy_repository.py
   ├─ adapters/inbound/http_api/            # FastAPI/OpenAPI wire adapter
   ├─ adapters/outbound/equity_mock/       # 결정적 in-memory Equity v0.2 mock
   │  └─ facade/provider.py
   ├─ adapters/outbound/strategy_memory/    # contract reference/test adapter
   ├─ adapters/outbound/engine_portfolio/   # capability 협상과 target action 변환
   ├─ adapters/outbound/backtest_engine/    # TargetTape → Rust/Python engine
   ├─ adapters/outbound/artifact_local/     # atomic local result commit
   ├─ adapters/outbound/strategy_sqlite/    # durable immutable strategy revisions
   └─ bootstrap/                           # adapter 조립
      └─ facade/{container,http}.py
```

테스트 실행:

```powershell
cd backend
uv sync --extra parquet
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release
uv run pytest -q
uv run ruff check src tests examples scripts
uv run pyright
uv run server
uv run python scripts/export_openapi.py openapi.json
```

`uv run server`의 전략 revision 저장소는 기본적으로 `.local/strategy-revisions.sqlite3`이며
프로세스를 재시작해도 원문·hash·provenance를 복원한다. 배포별 저장 위치는
`STRATEGY_WORKBENCH_DB_PATH` 환경 변수로 지정할 수 있다. 기본 Workbench run은 `rust` core를
선택하므로 실제 백테스트와 browser E2E 전에는 위 확장을 설치한다. 확장이 없을 때는 성능·실행
의미를 숨기는 Python fallback 대신 `CoreUnavailable`로 실패한다.

Equity mock HTTP 계약은 다음 세 경로로 분리한다.

- `GET /api/v1/equity/catalog`: 검색·dataset/unit/frequency 필터·pagination과 snapshot/field capability
- `POST /api/v1/equity/universe/preview`: 과거 시점별 구성 종목과 세션 coverage summary
- `POST /api/v1/equity/panel/preview`: row/column 제한, 비용 추정, PIT cell과 확인이 필요한 위험

권장 lag보다 짧은 override와 불완전 coverage는 backend가 구조화된 warning으로 판정한다.
확인 전에는 panel cell을 반환하지 않으므로 frontend가 같은 규칙을 복제하지 않는다.
