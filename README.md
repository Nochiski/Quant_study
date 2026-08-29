# Quant_study

이벤트 드리븐 백테스트 엔진을 직접 만드는 학습 리포. 설계 아티팩트(`2026-08-17/`의
학습 노트)에서 고정한 전략 I/O 계약을 루트의 `backtest_engine` 패키지로 구현한다.

## 구조

```
src/backtest_engine/
├─ types/          # 프로토콜 계약: Requirements, Event, Context, Decision, Action, serde
├─ capability.py   # DEFINED ≠ IMPLEMENTED — 실행 전 요구사항 협상과 거절
├─ engine/         # reference engine: EventQueue, Router, BrokerSim, Portfolio, Metrics
├─ ports/          # 헥사고날 포트: BarSource 프로토콜, BarQuery, LoadResult (도메인 소유)
├─ adapters/       # 포트 구현: CSV(csv_bars), KRX 원장 parquet(krx_parquet)
└─ data/           # 소스 무관 정제(cleaning), DataFeed (엔진 입력)
examples/          # 골든크로스 예제 전략 + CSV / KRX parquet 데모
scripts/           # 테스트 픽스처 재생성 등 유틸
tests/             # 골든(손계산)·계약·상태 전이·직렬화·단위 테스트
tests/fixtures/    # KRX 원장 슬라이스 (종목 5개, 605KB) — 어댑터 스모크용
2026-08-17/        # 설계 아티팩트와 Zipline 관찰용 앱 (기준 동작 비교용)
```

## 핵심 설계

- 전략은 `requirements()`와 `on_event(ctx, event) -> StrategyDecision` 두 메서드만 구현한다.
  판단만 반환하고, 주문 수량·체결·회계는 엔진 책임이다.
- 엔진 내부는 `(ts, priority, seq)`로 정렬되는 이벤트 큐 하나로 흐른다:
  `MARKET(대기 주문 체결) → FILL(포트폴리오 반영) → SESSION_CLOSE(평가·전략 호출) → ORDER(주문 등록)`.
  T 종가 판단은 T+1 시가에 체결된다 (look-ahead 차단).
- 현금과 보유 수량은 FillEvent 적용 시점에만 변한다. 모든 이벤트는 append-only
  EventStore에 남아 Run → Decision → Action → Order → Fill → Snapshot으로 역추적된다.
- 미구현 Action/Feature/Schedule은 데이터 루프 전에 `CapabilityNotImplemented`로
  전체 위반 목록과 함께 거절된다.
- 시장 데이터는 헥사고날 경계로 분리된다: 엔진·전략은 `ports.BarSource`가 돌려주는
  `LoadResult`와 `DataFeed`만 보고, 파일 형식·벤더 SDK·DB는 `adapters/`에만 존재한다.
  새 채널은 `load_bars(BarQuery) -> LoadResult` 하나를 구현하면 붙는다.
  정제(OHLC 정책·거래정지 제거·시간 역행 거절)는 `data/cleaning.py` 한 곳에서 하고,
  손댄 행 수는 항상 `repaired_rows`/`dropped_rows`로 보고한다 (silent 보정 금지).

v1 구현 범위(로드맵 3단계): `NoAction` · `SetPortfolioTarget(WeightTarget)` ·
`LiquidatePosition`, MARKET 이벤트, `EverySession` 일정. 나머지 스키마는 정의만
되어 있고 `reference_engine_capabilities()`에 NOT_IMPLEMENTED로 명시된다.

## 실행

```bash
uv sync --extra parquet             # 의존성 설치 (Python 3.11+, pyarrow 포함)
uv run pytest                       # 테스트
uv run ruff check src tests examples
uv run pyright src tests examples
uv run python examples/run_demo.py      # PyKRX CSV(005930)로 골든크로스 백테스트
uv run python examples/run_krx_demo.py  # KRX 원장 parquet 슬라이스로 동일 전략 실행
uv run python examples/run_krx_demo.py <원장 디렉토리>   # quant-data 빌드 전체 대상
```

## 검증: Zipline 대조

엔진 회계는 Zipline과의 세션 단위 equity 대조로 검증됐다 (buy-hold 오차 0,
골든크로스 최대 3e-16). 실행 방법과 리포트는 `tests/manual/README.md` 참고.

