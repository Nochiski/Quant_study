# 로드맵 4단계: 주문 생명주기 확장 설계 (2026-08-29)

v1 엔진(`2026-08-24-backtest-engine-v1-design.md`)이 "시장가 + 다음 세션 시가 전량
체결 + DAY"로 고정해 둔 주문 모델을 설계 노트의 스키마 전체로 넓힌다. 스키마는
이미 `types/`에 정의돼 있으므로(DEFINED) 이 작업의 산출물은 handler·테스트를 붙여
Capability를 IMPLEMENTED로 승격하는 것이다.

전체 로드맵 순서와 4단계 이후 항목은 `2026-08-29-roadmap-overview.md`.

## 범위

네 하위 단계로 나누고 이 순서로 구현·머지한다. 각 하위 단계는 독립적으로 테스트가
통과하고 Capability 표가 정직한 상태여야 한다.

| 단계 | 내용 | 승격되는 Capability |
|---|---|---|
| 4a | `SetPositionTarget`(Weight/Quantity/Notional), `AdjustPosition`(QuantityDelta/NotionalDelta), `SetPortfolioTarget`의 Quantity/Notional 타깃 | Action: SET_POSITION_TARGET, ADJUST_POSITION |
| 4b | `SubmitOrder`로 LIMIT/STOP/STOP_LIMIT 주문, TIF DAY/GTC, OHLC 기반 트리거·체결 규칙 | Action: SUBMIT_ORDER · Feature: LIMIT_ORDER, STOP_ORDER |
| 4c | `CancelOrder`/`ReplaceOrder`, 전략에 FILL/ORDER_UPDATE 이벤트 전달, `ctx.open_orders()` | Action: CANCEL_ORDER, REPLACE_ORDER · Event: FILL, ORDER_UPDATE |
| 4d | 거래량 참여율 기반 부분체결, 슬리피지 모델 포트, TIF IOC/FOK | Feature: PARTIAL_FILL · ExecutionPolicy.max_participation |

**포함하지 않는 것**: 공매도·MARGIN(5단계), Basket(5단계), VWAP/TWAP 실행 스타일
(장중 데이터 없음 — UNSUPPORTED로 표시 유지), TIMER 이벤트, `MonthEndSession`.

## 변하지 않는 불변조건 (v1에서 승계)

1. 주문 생성만으로 현금·보유 수량이 바뀌지 않는다. 상태는 Fill로만 바뀐다.
2. T 종가 판단은 T+1 이후 세션에서만 체결된다. 같은 세션 bar로 판단·체결 불가.
3. Decision → Order → Fill → OrderUpdate는 append-only, id는 run 범위 일련번호.
4. 한 세션 안 처리 순서는 `MARKET → FILL → SESSION_CLOSE → ORDER` 우선순위 큐 하나.
5. 정직한 Capability: handler·테스트 없는 스키마는 NOT_IMPLEMENTED로 남긴다.

## 확정한 설계 결정

### 1. `OrderEvent`에 주문 종류·가격·TIF 필드 추가 (스키마 확장)

- **선택**: `OrderEvent`에 `order_type: OrderType`(MARKET/LIMIT/STOP/STOP_LIMIT),
  `limit_price: Decimal | None`, `stop_price: Decimal | None`,
  `time_in_force: TimeInForce` 를 추가한다. 기본값은 MARKET/None/None/DAY라 기존
  생성 코드와 직렬화 fixture가 그대로 돈다. `__post_init__`에서 종류별 필수 가격
  존재를 검증한다(LIMIT인데 limit_price None → ValueError).
- **대안**: `OrderEvent.request: OrderRequest`로 요청 객체를 통째로 품기.
- **이유**: 라우터가 만든 주문(SetPortfolioTarget 등)은 `OrderRequest`를 거치지
  않는다. 평면 필드가 EventStore·serde·Rust 포팅(Struct of primitives)에 단순하다.
  `source_action`이 원본 추적을 이미 담당한다.

### 2. 일봉 OHLC 기반 트리거·체결 규칙 (4b)

경로 정보가 없는 일봉에서 "그 세션에 체결됐는가, 얼마에"를 결정론적으로 정한다.
표준 규칙(시가에서 이미 조건 충족이면 시가, 아니면 조건 가격)을 쓰고 낙관적
가정(장중 최유리가)은 배제한다.

| 종류 | 매수 | 매도 |
|---|---|---|
| MARKET | `open` | `open` |
| LIMIT L | `open ≤ L` → open; `low ≤ L` → L; 아니면 미체결 | `open ≥ L` → open; `high ≥ L` → L; 아니면 미체결 |
| STOP S | `open ≥ S` → open; `high ≥ S` → S; 아니면 미발동 | `open ≤ S` → open; `low ≤ S` → S; 아니면 미발동 |
| STOP_LIMIT S/L | STOP 규칙으로 발동 → 발동 가격 p에서 `p ≤ L`이면 p에 체결, 아니면 LIMIT L 주문으로 전환해 대기 | 대칭 |

- 발동됐지만 체결되지 않은 STOP_LIMIT은 `OrderStatus.TRIGGERED` 업데이트를 남기고
  이후 세션부터 LIMIT으로 평가한다. `OrderStatus`에 `TRIGGERED` 값을 추가한다
  (스키마 확장, 결정 1과 같은 이유로 기록).
- 미체결 DAY 주문은 그 세션 MARKET 처리 끝에 `CANCELLED(detail="day order expired")`.
- GTC는 `OrderManager`에 남는다. 종목 bar가 없는 세션(거래정지)은 건너뛰고 유지한다.
  run 종료 시 남은 GTC는 `CANCELLED(detail="run ended")`로 기록해 미체결 주문이
  결과에서 조용히 사라지지 않게 한다.
- 매수 현금 한도(cash-cap) 규칙은 모든 종류에 동일 적용: 체결 가격 기준으로
  수수료 포함 살 수 있는 정수 수량까지만 체결, 나머지는 v1과 같이
  `PARTIALLY_FILLED` 기록 후 DAY면 소멸, GTC면 잔량 유지.
- 같은 세션 처리 순서는 v1 그대로: 매도 먼저, 그다음 매수, 각 그룹 안에서 order_id 오름차순.

### 3. `OrderManager`가 잔량을 소유한다 (4b·4d 공통)

- **선택**: `OrderManager`는 `OpenOrder(order: OrderEvent, remaining: Decimal,
  triggered: bool)` 가변 레코드를 갖고, `OrderEvent` 자체는 frozen 유지. `pop_all()`을
  없애고 `due(snapshot) -> tuple[OpenOrder, ...]`(bar가 있는 종목의 대기 주문)과
  `settle(order_id, filled_qty)`/`expire(...)`로 바꾼다.
- **대안**: 부분체결마다 잔량이 줄어든 새 `OrderEvent`를 발급.
- **이유**: 한 Order에 여러 Fill이 붙는 스키마(`FillEvent.order_id`)를 그대로 쓴다.
  id가 바뀌면 전략의 CancelOrder 대상이 흔들린다.

### 4. 4a 사이징: 참조 가격은 판단 세션 종가, 공매도 진입은 거절

- Quantity/Notional 타깃과 Delta는 기존 `floor_delta_shares(delta_notional, close)`
  경로에 합류한다. `QuantityTarget`/`QuantityDelta`는 notional 변환 없이 정수 검증만.
- 결과 포지션이 0 미만이 되는 목표·증감(`QuantityTarget(-10)`, 보유 5주에
  `QuantityDelta(-8)`)은 SHORT_SELLING 미구현이므로 `UnsupportedActionValue`로 거절
  한다. v1의 "매도는 보유 수량까지" 조용한 clamp는 `SetPortfolioTarget`의 비중 반올림
  오차 케이스에만 유지하고, 명시적 수량 지시는 clamp하지 않는다 — 전략이 요구한
  수량과 다른 주문이 소리 없이 나가는 것을 막는다.
- `SetPositionTarget`/`AdjustPosition`의 `ExecutionPolicy`는 v1 검증(`_check_execution`)을
  같이 통과한다. 4d 전까지 `max_participation`은 거절, 4d부터 허용.

### 5. 4c 전략 ↔ 주문 상태 인터페이스

- **`ctx.open_orders(instrument=None) -> tuple[OrderEvent, ...]`**를 `StrategyContext`
  프로토콜에 추가. 세션 종료 시점에 대기 중인 주문(잔량 > 0)을 돌려준다. 전략은
  여기서 얻은 `order_id`로 Cancel/Replace를 낸다. 읽기 전용(튜플·frozen).
- **FILL / ORDER_UPDATE 이벤트 전달**: 전략이 `requirements().events`에 선언한
  경우에만, 큐에서 해당 이벤트가 처리되는 시점(FILL 우선순위, SESSION_CLOSE 이전)에
  `on_event(ctx, FillEvent | OrderUpdateEvent)`를 호출한다. 이 호출이 돌려준
  Decision도 일반 경로로 라우팅돼 ORDER(T)로 큐에 들어가고 T+1에 체결된다. 선언하지
  않은 전략은 호출되지 않는다(기존 동작 유지).
- **`CancelOrder`**: 대기 주문이면 `OrderManager`에서 제거하고 `CANCELLED(detail=
  "cancelled by strategy decision_id=...")`. 존재하지 않거나 이미 종료된 id는
  `UnknownOrderId` 예외로 run을 중단한다 — 전략은 같은 세션의 `open_orders()`를 본
  뒤 판단하므로 모르는 id는 전략 버그다(조용히 무시하면 청산 실패가 감춰진다).
- **`ReplaceOrder`**: 원 주문 `REPLACED(detail="replaced_by=O-…")` 기록 후 제거, 새
  `OrderEvent`를 새 id로 발급(`source_action=ReplaceOrder`라 원 id 추적 가능). 잔량이
  부분체결로 줄어 있었다면 새 주문 수량은 `replacement.core.quantity` 그대로 —
  전략이 명시한 값을 엔진이 재해석하지 않는다.
- `LiquidatePosition(cancel_open_orders=True)`는 기존대로 `cancel_for_instrument`.

### 6. 4d 체결 모델: 유동성 캡과 슬리피지 포트

- **유동성**: 체결 수량 = `min(remaining, floor(bar.volume × participation))`.
  `participation`은 액션의 `ExecutionPolicy.max_participation`이 있으면 그 값, 없으면
  `BrokerSim(max_participation=None)` 기본값(None = 무제한, v1 동작). `SubmitOrder`는
  정책을 갖지 않으므로 브로커 기본값을 쓴다. 거래량 0인 bar에서는 체결 0.
- **슬리피지 포트**: `ports/execution.py`에
  `SlippageModel.slip(order, bar, base_price, quantity) -> float`(주당 금액, 항상 ≥ 0)
  프로토콜. 구현은 `engine/slippage.py`: `NoSlippage`(기본), `FixedBpsSlippage`,
  `VolumeShareSlippage(volume_limit, price_impact)`(Zipline 기본 모델과 같은 식 —
  대조 하네스용). 체결가 = base ± slip(매수 +, 매도 −). LIMIT 주문은 체결가가 지정가를
  넘지 못하도록 clip한다(시장가는 clip 없음). `BacktestEngine(config, capabilities,
  slippage=..., max_participation=...)` 생성자 주입 — `RunConfig`(types)는 엔진 구현
  타입을 모르게 유지.
- **IOC / FOK**: 일봉에서 "즉시" = 그 세션 한 번. IOC는 가능한 만큼 체결 후 잔량
  `CANCELLED`. FOK는 유동성·현금으로 전량 체결 불가면 Fill 없이 `CANCELLED(detail=
  "fok not fillable ...")`. 4d 전에는 둘 다 `UnsupportedActionValue`로 거절.
- **부분체결 상태 기록**: Fill 후 잔량 > 0이면 `PARTIALLY_FILLED`, DAY/IOC면 이어서
  `CANCELLED`, GTC면 다음 세션에 잔량으로 재시도.

### 7. Capability 승격은 하위 단계 머지 단위로

`reference_engine_capabilities()`의 사유 문자열("roadmap step 4")을 하위 단계별로
갱신하고, 승격은 해당 단계의 골든·상태 전이 테스트가 통과한 커밋에서만 한다.
`tests/test_capability.py`가 표와 실제 라우터 handler 집합을 대조해 불일치를 잡는다.

## 컴포넌트별 변경 요약

| 파일 | 4a | 4b | 4c | 4d |
|---|---|---|---|---|
| `types/events.py` | | OrderEvent 필드, OrderStatus.TRIGGERED | | |
| `types/orders.py` | | `OrderType` enum | | |
| `types/strategy.py` | | | `open_orders()` | |
| `sizing.py` | Quantity/Notional 변환 | | | |
| `engine/router.py` | 두 액션 handler | SubmitOrder → OrderEvent | Cancel/Replace | max_participation 허용 |
| `engine/orders.py` | | OpenOrder 잔량·GTC | cancel(order_id) | 잔량 갱신 |
| `engine/broker.py` | | 종류별 트리거·가격 규칙 | | 유동성·슬리피지·IOC/FOK |
| `engine/loop.py` | | DAY 만료·GTC 유지·run 종료 취소 | 전략에 FILL/ORDER_UPDATE 전달 | |
| `engine/context.py` | | | open_orders | |
| `ports/execution.py`, `engine/slippage.py` | | | | 신규 |
| `capability.py` | 승격 | 승격 | 승격 | 승격 |
| `types/serde.py` | | 새 필드 round-trip | | |

## 테스트·검증 계획

원칙: 모든 체결 가격·수량은 손계산 골든으로 고정하고, 상태 전이는 허용 전이표로
검사한다. 회귀 기준은 기존 `tests/test_engine_golden.py`와 Zipline 대조
(`tests/manual/`)가 4단계 전후로 동일 수치를 내는 것이다.

### 공통 (모든 하위 단계)

- **회귀**: 기존 테스트 전부 통과. `examples/run_demo.py`·`run_krx_demo.py` 결과
  수치(최종 equity, 체결 수) 변화 0 — MARKET/DAY 경로는 손대지 않았다는 증거.
- **Capability 계약** (`test_capability.py`): 승격된 항목이 `prepare_strategy()`를
  통과하고, 아직 NOT_IMPLEMENTED인 항목은 첫 bar 전에 거절되는지. 표에 IMPLEMENTED로
  적힌 ActionKind마다 라우터에 handler가 있는지 자동 대조.
- **직렬화** (`test_serde.py`): 새 필드·enum 값 round-trip.
- **품질 게이트**: `ruff check`, `pyright` 0 오류.

### 4a — 사이징

- 단위 (`test_sizing.py`): Quantity/Notional 타깃·델타 → 주문 수량 손계산 6케이스
  (증가·감소·변화 없음·정수 내림·notional 0·보유 0에서 감소).
- 라우터 (`test_router.py`): 결과 포지션 음수 → `UnsupportedActionValue`;
  `SetPortfolioTarget`에 `QuantityTarget`·`NotionalTarget` 혼합; `TargetScope.REPLACE`와
  Quantity 타깃 조합.
- 골든 (`test_engine_golden.py`): 3세션 fixture에서 `AdjustPosition(+10) → (-4)`
  → 최종 보유 6주·현금 손계산.

### 4b — 주문 종류·TIF

- 브로커 단위 (`tests/test_broker.py`, 신규): 결정 2의 표를 케이스별로 고정한
  파라미터 테스트 — 종류 × 방향 × {시가 충족, 장중 충족, 미충족} = 24케이스 +
  STOP_LIMIT 발동-후-미체결 전환 2케이스. 각 케이스의 기대 (체결 여부, 가격)은
  손계산 값을 리터럴로 적는다.
- 상태 전이 (`tests/test_order_lifecycle.py`, 신규): 허용 전이표
  `NEW → {PARTIALLY_FILLED, FILLED, CANCELLED, TRIGGERED, REPLACED}`,
  `TRIGGERED → {FILLED, PARTIALLY_FILLED, CANCELLED}`, `PARTIALLY_FILLED → {FILLED,
  CANCELLED}`; 종료 상태에서 나가는 전이 없음. EventStore의 order_updates를 order_id별로
  재생해 검사.
- 골든: (i) GTC 지정가 매수가 3세션 뒤 저가 터치로 L에 체결, (ii) DAY 지정가 미체결 →
  당일 CANCELLED, (iii) 거래정지 세션을 건너뛴 GTC 유지, (iv) run 종료 시 GTC
  CANCELLED("run ended"), (v) STOP 매도로 손절 → 시가 갭 하락 시 open 체결.
- 불변조건 검사: 모든 골든에서 "주문 생성 세션의 cash·position 불변", "Fill ts >
  Order ts".

### 4c — 취소·정정·이벤트 전달

- 컨텍스트 (`test_context.py`): `open_orders()`가 세션 종료 시점 잔량 > 0 주문만,
  instrument 필터, 반환값 불변성.
- 라우터: CancelOrder 정상 → `CANCELLED` 기록·대기열 제거; 모르는 id →
  `UnknownOrderId`; 이미 체결된 id → `UnknownOrderId`; ReplaceOrder → 원 주문
  `REPLACED`, 새 주문 id 증가·`source_action` 추적.
- 이벤트 전달 (`test_engine_golden.py`): FILL을 선언한 스텁 전략이 받은 이벤트
  시퀀스가 EventStore의 fills와 순서·개수 일치; 선언하지 않은 전략은 MARKET만 받음;
  FILL 이벤트 핸들러가 반환한 Decision이 다음 세션에 체결(look-ahead 없음).
- 골든: GTC 지정가를 2세션 뒤 취소 → Fill 없음, equity 변화 0.

### 4d — 부분체결·슬리피지

- 브로커 단위: 참여율 캡 (volume 1,000 × 10% → 100주), volume 0 → 체결 0, 캡과
  cash-cap 동시 적용 시 더 작은 값; LIMIT 체결가가 슬리피지 후에도 L을 넘지 않는 clip.
- 슬리피지 모델 단위 (`tests/test_slippage.py`, 신규): `FixedBpsSlippage`,
  `VolumeShareSlippage` 손계산 값; `slip ≥ 0` 계약.
- 상태 전이: GTC 부분체결 3세션에 걸쳐 완료 → `PARTIALLY_FILLED ×2 → FILLED`,
  Fill 합 = 주문 수량; IOC 잔량 `CANCELLED`; FOK 불충족 → Fill 0 + `CANCELLED`.
- **Zipline 대조 확장** (`tests/manual/`): `VolumeShareSlippage(0.025, 0.1)`를 양쪽에
  같은 파라미터로 두고 시장가 골든크로스 세션 equity 대조. 기준은 기존 하네스와
  같은 방식(오차 상한을 리포트에 기록). 지정가·스톱은 Zipline이 종가 기준으로
  발동을 판정해 규칙이 다르므로 대조 대상에서 제외하고 그 이유를 하네스 README에
  적는다.

### 완료 기준 (4단계 전체)

- 위 테스트 전부 통과, Capability 표에서 4단계 사유 문자열이 남아 있지 않음
  (남는 NOT_IMPLEMENTED는 5단계 사유만).
- README "현재 브랜치 구현 범위" 갱신, `.claude/rules/`에 규칙 변경이 있으면 동반 갱신.
- 발견된 결함은 `.claude/rules/pr-review.md`의 4요소 양식으로 PR body에 기록.

## 구현 결과와 스펙 차이 (2026-08-29 구현 완료)

구현하면서 확정·변경된 사항. 나머지는 스펙대로다.

- 전략 알림은 큐에 `NOTIFY(25)` 우선순위를 추가해 전달한다 (FILL 20 → NOTIFY 25 →
  SESSION_CLOSE 30). 한 세션의 체결이 전부 포트폴리오에 반영된 뒤 전략이 호출되도록
  하기 위해서다. 알림은 `StrategyNotify(event, snapshot)` 큐 이벤트로 흐른다.
- 라우터가 선언된 `EngineFeature`도 검사한다: LIMIT/STOP 종류 → `LIMIT_ORDER`/`STOP_ORDER`,
  IOC/FOK·`max_participation` → `PARTIAL_FILL`. 미선언이면 `UndeclaredFeatureUsed`.
  Capability 표가 IMPLEMENTED여도 전략이 선언하지 않은 기능은 쓸 수 없다.
- 취소·정정 결과는 `RoutingResult.updates: tuple[OrderUpdateEvent, ...]`로 라우터가
  직접 만든다 (기존 `cancelled: tuple[OrderEvent]` 대체).
- 4d의 IOC/FOK는 4b에서 "거절"이 아니라 `PARTIAL_FILL` 미선언 거절로 구현됐고, 4d에서
  기능 승격과 함께 열렸다.
- 거래량 0 bar에서 참여율 캡이 0이면 체결 없이 대기(`NOT_FILLED`)한다. DAY면 당일 만료.
- Zipline 슬리피지 대조는 보류 — 사유는 `tests/manual/README.md`.

## 다음 단계

`2026-08-29-roadmap-overview.md` 참고: 데이터 측 후속(D) → 5단계 Basket·공매도 →
6단계 Rust 포팅.
