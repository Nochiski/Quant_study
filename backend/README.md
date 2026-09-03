# Strategy Workbench Backend

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
   ├─ application/equity_workspace/        # 검색 catalog·universe/panel PIT preview
   │  ├─ ports/outgoing/equity_data.py     # EquityDataPort
   │  └─ facade/{ports,workspace}.py
   ├─ application/strategy_design/          # create/get/revise/validate/explain
   │  └─ ports/outgoing/strategy_repository.py
   ├─ adapters/inbound/http_api/            # FastAPI/OpenAPI wire adapter
   ├─ adapters/outbound/equity_mock/       # 결정적 in-memory Equity v0.2 mock
   │  └─ facade/provider.py
   ├─ adapters/outbound/strategy_memory/    # immutable revision 기준 adapter
   └─ bootstrap/                           # adapter 조립
      └─ facade/{container,http}.py
```

테스트 실행:

```powershell
cd backend
uv sync --extra parquet
uv run pytest -q
uv run ruff check src tests examples scripts
uv run pyright
uv run uvicorn strategy_workbench.bootstrap.facade.http:app --reload
uv run python scripts/export_openapi.py openapi.json
```

Equity mock HTTP 계약은 다음 세 경로로 분리한다.

- `GET /api/v1/equity/catalog`: 검색·dataset/unit/frequency 필터·pagination과 snapshot/field capability
- `POST /api/v1/equity/universe/preview`: 과거 시점별 구성 종목과 세션 coverage summary
- `POST /api/v1/equity/panel/preview`: row/column 제한, 비용 추정, PIT cell과 확인이 필요한 위험

권장 lag보다 짧은 override와 불완전 coverage는 backend가 구조화된 warning으로 판정한다.
확인 전에는 panel cell을 반환하지 않으므로 frontend가 같은 규칙을 복제하지 않는다.
