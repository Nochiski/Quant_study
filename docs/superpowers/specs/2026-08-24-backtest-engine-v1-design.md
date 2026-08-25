# 백테스트 엔진 v1 설계 (2026-08-24)

설계 아티팩트(학습 노트, `2026-08-17/` 빌드 페이지)의 로드맵 1–3단계를 구현하는
Python reference engine의 설계 결정 기록. 스키마·용어는 아티팩트를 따르고,
여기에는 구현하면서 확정한 사항만 남긴다.

## 범위

- **1단계**: 프로토콜 타입 전체(9개 Action, StrategyEvent 합 타입, Requirements,
  Order/Fill)와 JSON 직렬화 round-trip 테스트.
- **2단계**: Capability Registry — Action/Feature/Event/Schedule 네 축으로
  IMPLEMENTED/NOT_IMPLEMENTED/UNSUPPORTED를 선언하고, `prepare_strategy()`가
  데이터 루프 전에 전체 위반 목록과 함께 거절.
- **3단계**: `NoAction`·`SetPortfolioTarget(WeightTarget)`·`LiquidatePosition`과
  Fill 기반 회계를 갖춘 최소 엔진 + 손계산 골든 테스트 + 005930 실데이터 데모.

## 확정한 설계 결정

### 1. 엔진 코어는 범용 이벤트 큐 (사용자 선택)

- **선택**: 모든 흐름이 `(ts, priority, seq)` 정렬 heap 큐를 통과.
  우선순위 `MARKET(10) → FILL(20) → SESSION_CLOSE(30) → ORDER(40)`,
  `seq`는 동순위 FIFO 보장용 단조 증가 일련번호 (재현성 전제).
- **대안**: 단순 시간순 bar 루프 (일봉만 있는 현재는 더 짧음).
- **이유**: 최종 아키텍처(장중 이벤트, Rust 포팅)와 같은 골격을 처음부터 유지.
  한 세션의 처리 순서를 우선순위 테이블 하나로 고정해 look-ahead가 구조적으로
  불가능하다: T 종가 판단 → ORDER(T) → 다음 MARKET(T+1)에서 시가 체결.

### 2. FillEvent에 side 추가 (아티팩트 스키마 확장)

- 아티팩트의 FillEvent에는 방향이 없지만, "fills와 bars만으로 equity curve를
  재계산할 수 있어야 한다"는 불변조건을 만족하려면 Fill 자체에 방향이 필요.
  `side: Side`를 추가하고 quantity는 항상 양수로 유지.

### 3. v1 체결 모델 (BrokerSim)

- 시장가 + 다음 세션 시가 전량 체결, 유동성 무제한, 슬리피지 0 기록.
- 매수는 현금 한도 초과 시 체결 수량이 줄어든다(cash-cap). 이는 유동성 부분체결
  (PARTIAL_FILL, 미구현)이 아니라 MARGIN 미구현 상태의 회계 제약이며,
  `OrderUpdateEvent(PARTIALLY_FILLED)`로 기록된다.
- 같은 세션에서 매도 주문을 먼저 처리해 매수 가용 현금을 확정한다 (결정론 규칙).
- 모든 주문은 DAY: 해당 세션에 체결 못 하면 취소 기록 후 소멸.

### 4. 데이터 정제는 명시적 정책으로

- 실제 PyKRX 수정주가에 `close > high`인 행이 존재(005930에서 6행 관찰).
- Bar의 OHLC 불변조건은 엄격하게 유지하고, CSV 로더에 `OhlcPolicy.STRICT`(기본,
  FORMAT_ERROR 거절) / `CLAMP`(high/low를 몸통 포함으로 확장, 보정 행 수를
  `LoadResult.repaired_rows`로 보고)를 둔다. silent 보정 금지.

### 5. 오류 정책

- 예상된 도메인 실패(파일 없음, 형식 오류, 체결 거절)는 Result 값 타입
  (`LoadResult`, `ExecutionOutcome`)으로 반환.
- 계약 위반(미선언 데이터 접근, 미선언 Action 반환, 시간 역행, 음수 현금)은
  진단 컨텍스트를 담은 typed 예외로 즉시 중단.

### 6. 식별자와 재현성

- decision/order/fill id는 uuid가 아니라 run 범위 일련번호(`D-000001` 등) —
  같은 입력이면 같은 id가 나와 결과 diff가 가능하다.
- 실행 중 모든 이벤트는 append-only EventStore에 남는다:
  Run → Decision → Action → Order → Fill → Snapshot 역추적 경로.

## 검증

- 골든 테스트: 4세션 수동 시나리오에서 cash/position/equity/fee를 손계산과 대조.
- 계약 테스트: 미선언 조회·미선언 Action·미구현 요구가 항상 거절되는지.
- 상태 전이 테스트: 주문 생성으로는 자산 불변, Fill 적용에만 변경.
- 직렬화 테스트: 전체 Action 종류 JSON round-trip.
- 실데이터 스모크: `examples/run_demo.py` (005930, 2015–2020, 1,247세션).

## 다음 단계 (로드맵)

4. 주문 생명주기 확장: 지정가·스톱, 유동성 부분체결, 취소·정정, 슬리피지 모델.
5. Basket과 공매도 → Capability 승격.
6. Rust 포팅(maturin, `backtest_engine._core`)과 Python/Zipline 결과 diff.
