# Strategy Workbench Backend

전략 생성·팩터 계산·실험 관리 API가 들어갈 백엔드 애플리케이션이다. 기존
`src/backtest_engine`과 `rust/backtest_core`는 이 애플리케이션이 사용하는 실행 커널이며,
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
- `adapters/inbound/`: HTTP/SSE 같은 입력 변환. 향후 구현한다.
- `adapters/outbound/`: Equity DB, mock, 실행 엔진, 결과 저장소 구현.
- `bootstrap/`: concrete 구현을 고르는 유일한 composition root.
- 각 책임 노드는 `facade/<책임>.py`로만 외부 심볼을 노출하고
  `facade/__init__.py`에 허용 의존성 `DEPENDS_ON`을 선언한다.

현재 Equity DB 계약이 확정되지 않았으므로 `equity_mock`이 기준 adapter다. 실제 DB가 오면
같은 application port를 구현하는 `equity_duckdb` adapter를 추가하며, domain/application은
수정하지 않는다. mock으로 조용히 fallback하지 않고 bootstrap에서 adapter를 명시적으로 고른다.

## 현재 골격

```text
src/strategy_workbench/
├─ domain/equity/                         # PIT 데이터 값 타입과 query/result
│  └─ facade/research_data.py
├─ application/equity_workspace/          # 카탈로그·preview 유스케이스
│  ├─ ports/outgoing/equity_data.py       # EquityDataPort
│  └─ facade/{ports,workspace}.py
├─ adapters/outbound/equity_mock/         # 결정적 in-memory Equity v0.2 mock
│  └─ facade/provider.py
└─ bootstrap/                             # adapter 조립
   └─ facade/container.py
```

테스트 실행:

```powershell
$env:PYTHONPATH = (Resolve-Path backend/src)
uv run pytest backend/tests -q
uv run ruff check backend/src backend/tests
uv run pyright backend/src backend/tests
```
