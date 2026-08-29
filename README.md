# Quant_study

이벤트 드리븐 백테스트 엔진을 직접 만드는 학습 리포. 설계 아티팩트(`2026-08-17/`의
학습 노트)에서 고정한 전략 I/O 계약을 루트의 `backtest_engine` 패키지로 구현한다.

## 구조

```
src/backtest_engine/
├─ types/          # 프로토콜 계약: Requirements, Event, Context, Decision, Action, serde
├─ capability.py   # DEFINED ≠ IMPLEMENTED — 실행 전 요구사항 협상과 거절
├─ engine/         # reference engine: EventQueue, Router, OrderManager, BrokerSim, slippage, Portfolio, Metrics
├─ ports/          # 헥사고날 포트: BarSource, CorporateActionSource, UniverseSource, SlippageModel
├─ adapters/       # 포트 구현: CSV(csv_bars), sqlite(sqlite_bars), KRX 원장 parquet(krx_parquet)
└─ data/           # 소스 무관 정제(cleaning)·자본변동 검출(corporate_actions), DataFeed
examples/          # 골든크로스 예제 전략 + CSV / KRX parquet 데모
scripts/           # 테스트 픽스처 재생성, 다종목 벤치마크 등 유틸
tests/             # 골든(손계산)·계약·상태 전이·직렬화·단위 테스트
tests/fixtures/    # KRX 원장 슬라이스 (종목 5개, 605KB) — 어댑터 스모크용
2026-08-17/        # 설계 아티팩트와 Zipline 관찰용 앱 (기준 동작 비교용)
```

## 설계 아티팩트 요약

원본은 [백테스트 엔진 설계 노트](https://claude.ai/code/artifact/d1124798-840e-4282-a945-8317e4b7066d?org=20cfa914-bd88-4167-936b-209dd561dfb7)다.
Zipline 사용법 자체보다 커스텀 엔진을 만들기 전에 전략 I/O 계약, 책임 경계,
Capability 협상과 향후 Rust 포팅 경계를 고정한 구현 전 설계 기준이다.

```text
Requirements → Capability 검증 → StrategyEvent + 읽기 전용 Context
             → StrategyDecision(Action 묶음) → Validator / Router / Risk
             → Order → BrokerSim → Fill → Portfolio / Snapshot
```

- 전략은 `requirements()`로 필요한 데이터·이벤트·Action·기능을 먼저 선언하고,
  `on_event(ctx, event) -> StrategyDecision`에서 판단만 반환한다. 설정과 전략 고유
  State는 소유할 수 있지만 Context나 Portfolio를 직접 변경하지 않는다.
- 엔진은 시계열 정렬, 선언된 데이터의 준비, Action 검증과 수량 변환, 주문 생명주기,
  체결·비용·슬리피지, 포트폴리오 회계와 성과 기록을 소유한다.
- `NoAction`, 목표 비중/수량, 증감, 긴급 청산, 직접 주문, 취소·정정, Basket은 의미가
  다른 합 타입으로 표현한다. 타입이 정의된 것(`DEFINED`)과 실행 코드가 있는 것
  (`IMPLEMENTED`)은 별개이며, 미구현 요구는 첫 Bar 전에 구체적으로 거절한다.
- 동일 종목의 Bar는 시간순이어야 하고, 주문 생성만으로 현금·보유 수량이 바뀌어서는
  안 된다. 거래 상태는 Fill로만 바뀌며 Snapshot은 Bar와 Fill에서 재계산 가능해야 한다.
- 일봉 종가로 만든 판단은 같은 종가에 체결하지 않고 다음 시가에 실행한다. Decision,
  Action, Order, Fill, Snapshot은 append-only로 남겨 결과를 역추적한다.
- 전략 연구는 Python에 유지한다. 성능·타입 안정성이 필요한 이벤트 루프, 체결과 회계는
  Python 기준 구현과 골든/Zipline 대조를 먼저 고정한 뒤 Rust + PyO3로 옮기는 경로를
  택한다. Python 호출은 종목별 Bar가 아니라 시점별 Snapshot과 Action 배치 단위로 한다.
- 검증은 단위, 손계산 골든, Zipline 대조, 회귀, 계약, 상태 전이 테스트로 나눈다.

## 현재 브랜치 구현 범위

- 로드맵 3단계(`NoAction`, `SetPortfolioTarget`, `LiquidatePosition`)에 더해 4단계
  주문 생명주기를 구현했다 (`docs/superpowers/specs/2026-08-29-order-lifecycle-step4-design.md`):
  - 4a `SetPositionTarget`(Weight/Quantity/Notional), `AdjustPosition`
  - 4b `SubmitOrder`로 MARKET/LIMIT/STOP/STOP_LIMIT, DAY/GTC — 일봉 OHLC 기반 체결 규칙
  - 4c `CancelOrder`/`ReplaceOrder`, `ctx.open_orders()`, 전략에 FILL/ORDER_UPDATE 전달
  - 4d 거래량 참여율 부분체결, IOC/FOK, 슬리피지 포트(`NoSlippage`/`FixedBps`/`VolumeShare`)
  남은 `NOT_IMPLEMENTED`는 5단계(Basket, 공매도, MARGIN)뿐이며
  `reference_engine_capabilities()`에 명시된다. LIMIT/STOP·IOC/FOK·`max_participation`은
  해당 `EngineFeature`를 `requirements()`에 선언한 전략만 쓸 수 있다.
- 엔진 내부는 `(ts, priority, seq)`로 정렬되는 이벤트 큐 하나로 흐른다:
  `MARKET(대기 주문 체결) → FILL(포트폴리오 반영) → NOTIFY(전략 알림) → SESSION_CLOSE(평가·전략 호출) → ORDER(주문 등록)`.
  T 종가 판단은 T+1 세션부터 체결된다. 주문 생성은 현금·보유를 바꾸지 않고,
  미체결 주문은 DAY 만료·run 종료 시 반드시 `CANCELLED`로 기록된다.
- 시장 데이터는 헥사고날 경계로 분리된다. 엔진·전략은 `ports.BarSource`가 돌려주는
  `LoadResult`와 `DataFeed`만 보고, 파일 형식·벤더 SDK·DB는 `adapters/`에만 존재한다.
  새 채널은 `load_bars(BarQuery) -> LoadResult` 하나를 구현하면 붙는다.
- 정제(OHLC 정책·거래정지 제거·시간 역행 거절)는 `data/cleaning.py` 한 곳에서 하고,
  손댄 행 수는 항상 `repaired_rows`/`dropped_rows`로 보고한다 (silent 보정 금지).
- 5단계(`docs/superpowers/specs/2026-08-29-basket-short-margin-design.md`): `SHORT_SELLING`
  선언 시 음수 포지션(방향 전환 시 평균단가 재설정)과 세션 종료 차입 비용, `MARGIN` 선언 시
  매수 여력 = `max_gross_leverage × equity − 총노출`과 음수 현금 이자(둘 다 `CostAccrued`로
  기록, equity < 0이면 `EquityWipedOut`), `BasketAction`은 leg를 함께 견적해 BEST_EFFORT /
  ALL_OR_NONE / PROPORTIONAL로 판정한다. 이제 Action·Feature 축에 `NOT_IMPLEMENTED`가 없고
  TIMER 이벤트·`MonthEndSession`만 남는다.
- 6a Rust 코어(`docs/superpowers/specs/2026-08-29-rust-core-design.md`): `rust/backtest_core`
  (PyO3)가 체결 가격 규칙·수량 변환·포트폴리오 회계를 제공하고 `BacktestEngine(core="rust")`로
  켠다. Python 구현이 진실 원천이며 `tests/test_core_parity.py`가 두 코어의 결과를 레코드
  단위로 고정한다(확장 없으면 skip). 6b(견적 산술·매수 여력)·6c(세션 MARKET 처리 계획)까지
  옮겼고 주문 생명주기 11시나리오가 레코드 단위로 비트 동일하다. 6d는 다종목 벤치마크
  (`scripts/bench_universe.py`, 100종목·1,231세션·주문 23k)로 병목을 먼저 쟀다 — 큐가 아니라
  스냅샷·포트폴리오의 선형 종목 조회였고, dict 인덱스·스냅샷 메모로 python 15.1s→4.2s,
  rust 8.8s→3.3s. Rust 세션 루프 이전은 측정 결과로 닫았다.
- 데이터 후속(D, `docs/superpowers/specs/2026-08-29-data-followups-design.md`): 원장의
  상장주식수 변화로 액면분할·병합을 검출해 `run(corporate_actions=)`로 넘기면 엔진이 사건
  세션 시작에 보유 수량·평균단가를 조정하고(단주는 시가 현금 정산) 대기 주문을 취소한다.
  종목마스터 일별 스냅샷으로 만든 `UniverseResult`를 `run(universe=)`로 주면 전략이
  `ctx.universe()`로 그 세션의 상장 종목만 본다(look-ahead 차단). 새 데이터 채널은
  `tests/test_bar_source_contract.py`의 빌더 하나로 계약 전체를 통과해야 한다 (sqlite로 검증).

## 실행

```bash
uv sync --extra parquet             # 의존성 설치 (Python 3.11+, pyarrow 포함)
uv run pytest                       # 테스트
uv run ruff check src tests examples
uv run pyright src tests examples
uv run python examples/run_demo.py      # PyKRX CSV(005930)로 골든크로스 백테스트
uv run python examples/run_krx_demo.py  # KRX 원장 parquet 슬라이스로 동일 전략 실행
uv run python examples/run_krx_demo.py <원장 디렉토리>   # quant-data 빌드 전체 대상
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release  # Rust 코어(선택)
uv run python examples/run_krx_demo.py --core rust      # Rust 코어로 같은 데모
```

## 검증: Zipline 대조

엔진 회계는 Zipline과의 세션 단위 equity 대조로 검증됐다 — buy-hold, 골든크로스, 슬리피지
(VolumeShare + 참여율 캡·GTC 이월), 공매도 buy-hold 네 시나리오 모두 최대 상대 오차 0
(1,619세션, KRX 원장 슬라이스에서 생성한 CSV). 실행 방법과 리포트는 `tests/manual/README.md` 참고.
