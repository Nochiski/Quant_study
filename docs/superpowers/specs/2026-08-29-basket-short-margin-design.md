# 로드맵 5단계: 공매도·MARGIN·Basket 설계 (2026-08-29)

4단계(주문 생명주기)가 남긴 마지막 `NOT_IMPLEMENTED` — `SHORT_SELLING`, `MARGIN`,
`PROPORTIONAL_BASKET`, `BASKET` — 를 승격한다. `feat/data-followups` 위에 스택된다.

| 하위 | 내용 | 승격 |
|---|---|---|
| 5a | 음수 포지션 회계, 공매도 대금·차입 비용, 라우터의 음수 목표 허용 | Feature: SHORT_SELLING |
| 5b | 총노출 한도 내 음수 현금(신용) 허용, 이자 비용, 매수 여력 규칙 | Feature: MARGIN |
| 5c | `BasketAction` — BEST_EFFORT / ALL_OR_NONE / PROPORTIONAL 그룹 체결 | Action: BASKET · Feature: PROPORTIONAL_BASKET |

**포함하지 않는 것**: 대차 가능 수량·공매도 금지 종목(원장에 없음), 증거금률의 종목별 차등,
반대매매(margin call) 자동 청산, 배당 지급 의무(공매도 중 배당), 세금.

## 변하지 않는 불변조건

- 주문 생성은 현금·포지션을 바꾸지 않는다. 상태는 Fill·CorporateActionApplied로만 바뀐다.
- Snapshot은 Bar + Fill + CorporateActionApplied + **비용 발생(CostAccrued)** 으로 재계산 가능하다.
- 미선언 기능은 라우터에서 `UndeclaredFeatureUsed`로 거절한다 (4단계 규칙 승계).

## 확정한 설계 결정

### 1. 5a 공매도 회계: 음수 수량, 매도 대금은 현금에 반영

- **선택**: `Portfolio`가 음수 수량을 허용한다(`allow_short=True`일 때). 공매도 체결은 일반
  매도와 같은 회계(현금 += 대금 − 수수료, 수량 −= q)이며, 0을 지나 음수가 되는 순간부터
  평균단가는 공매도 진입가로 새로 잡는다(롱 → 숏 전환 시 롱 부분은 실현 손익).
  차입 비용은 매 세션 종료(mark) 시 `|숏 평가액| × borrow_bps_annual / annualization_days`를
  현금에서 차감하고 `CostAccrued(kind=SHORT_BORROW, instrument, ts, amount)`로 기록한다.
- **대안**: 공매도 대금을 별도 예치 계정에 묶고 현금과 분리.
- **이유**: Zipline·대부분 리서치 엔진이 대금을 현금에 넣는 단순 모델을 쓰고, 예치 계정은
  MARGIN 한도 계산에서 노출로 잡히므로 결과 equity는 같다. 분리 회계는 5단계 밖.
- 라우터: `SHORT_SELLING` 선언 시 음수 `WeightTarget`·`QuantityTarget`·`NotionalTarget`,
  결과 포지션이 음수가 되는 `QuantityDelta`/`NotionalDelta`, 보유 초과 `SubmitOrder(SELL)`를
  허용한다. 미선언이면 4단계 그대로 거절. `LiquidatePosition`은 숏 포지션이면 매수로 청산한다.
- 브로커: 매도는 여전히 현금 캡이 없다. 공매도 진입도 MARGIN 한도(결정 2)에는 걸린다.

### 2. 5b MARGIN: 총노출 한도 하나로 매수 여력을 정한다

- **선택**: `RunConfig`에 `max_gross_leverage: float = 1.0`, `margin_interest_bps_annual`,
  `short_borrow_bps_annual` 추가(기본 0). 세션 처리 중 매수 여력 =
  `max_gross_leverage × equity − gross_exposure`(둘 다 그 세션에서 앞서 처리된 체결 반영).
  MARGIN 미선언(레버리지 1.0)이면 여력은 `cash`와 같아 4단계 결과가 그대로다.
  현금이 음수인 세션 종료 시 `|cash| × margin_interest / annualization_days`를 차감하고
  `CostAccrued(kind=MARGIN_INTEREST)`로 기록한다.
- **대안**: 종목별 증거금률 테이블.
- **이유**: 원장에 증거금 정보가 없고, 리서치 목적의 레버리지 상한 한 개가 결과를 설명하기 쉽다.
- `Portfolio.apply`의 `NegativeCashError`는 MARGIN 허용 시 `equity < 0`(자본 잠식)일 때만 난다.
  브로커가 여력을 넘는 매수를 먼저 잘라내므로 정상 경로에서는 발생하지 않는다.

### 3. 5c Basket: 그룹 단위 체결 판정은 브로커의 견적(quote) 재사용

- **선택**: `BrokerSim.execute`를 `quote(open_order, bar, buying_power) -> Quote(price, quantity,
  status)`와 `fill(quote, fill_id)`로 나눈다. 단일 주문 경로는 quote→fill을 그대로 잇는다.
  라우터는 `BasketAction`의 leg를 각각 `OrderEvent`로 만들고 `group_id`·`group_policy`를 붙인다
  (`OrderEvent`에 `group_id: str | None` 추가, 정책은 `OrderManager`의 `BasketGroup`이 보관).
  MARKET 처리에서 같은 그룹의 leg는 **함께** 견적한다(매도 leg 먼저, 매수 여력 공유):
  - BEST_EFFORT: 견적대로 각자 체결(단일 주문과 동일).
  - ALL_OR_NONE: 어느 leg든 `quantity < remaining`이면 그룹 전체 체결 없음 → 전 leg
    `CANCELLED(detail="basket all_or_none …")`.
  - PROPORTIONAL: `scale = min(quantity_i / remaining_i)`; 각 leg `floor(remaining_i × scale)`
    체결, 잔량은 `CANCELLED(detail="basket proportional remainder …")`. `PROPORTIONAL_BASKET`
    선언 필요.
- **대안**: 그룹을 세션 단위로 재시도(GTC 바스켓).
- **이유**: 페어 전략이 원하는 것은 "한쪽만 체결되는 사고 방지"이지 세션 이월이 아니다.
  바스켓 leg는 모두 그 세션 한 번(DAY 의미)이며, 이월이 필요하면 전략이 다음 세션에 다시 낸다.
- 같은 그룹 안에 같은 종목이 두 번 오면 `ValueError`(수량 상쇄 의미가 불명확).

### 4. 비용 기록: `CostAccrued`는 Fill이 아니다

- `types/events.py`에 `CostAccrued(ts, kind: CostKind(SHORT_BORROW | MARGIN_INTEREST),
  instrument | None, amount)`. EventStore `RecordKind.COST`. 성과 지표는 equity 기준이라
  자동 반영된다. 회계 재계산 불변조건에 항목으로 추가.

## 컴포넌트별 변경 요약

| 파일 | 5a | 5b | 5c |
|---|---|---|---|
| `types/results.py` | `short_borrow_bps_annual` | `max_gross_leverage`, `margin_interest_bps_annual` | |
| `types/events.py` | `CostAccrued`, `CostKind` | | `OrderEvent.group_id` |
| `engine/portfolio.py` | 음수 수량, 롱↔숏 전환 평균단가, `accrue()` | 자본 잠식 검사 | |
| `engine/broker.py` | | 매수 여력 인자 | `quote`/`fill` 분리 |
| `engine/orders.py` | | | `BasketGroup` |
| `engine/router.py` | 음수 목표 허용(선언 시) | | `BasketAction` leg 라우팅 |
| `engine/loop.py` | 세션 종료 비용 발생 | 여력 계산 | 그룹 견적·정책 판정 |
| `capability.py` | SHORT_SELLING | MARGIN | BASKET, PROPORTIONAL_BASKET |
| `types/serde.py` | | | `group_id` round-trip |

## 테스트·검증 계획

### 공통
- 회귀: 5단계 이전 테스트 전부 통과, KRX 데모 출력 불변(기본 레버리지 1.0·비용 0·미선언).
- Capability 계약: 세 기능·BASKET 승격, `test_reference_capabilities_are_honest`에서 남는
  NOT_IMPLEMENTED가 없음(TIMER·MonthEndSession은 Event/Schedule 축이라 별도).
- `ruff`·`pyright` 0 오류.

### 5a 공매도
- 포트폴리오 단위(`tests/test_portfolio.py`): 공매도 진입 10주 @100 → 현금 +1,000, 수량 −10,
  평균단가 100; 환매 4주 @90 → 실현 +40, 수량 −6; 롱 5 → 매도 8 → 숏 3에서 평균단가 = 매도가;
  마크 후 unrealized = (avg − mark) × |qty|; `allow_short=False`면 기존 `NegativePositionError`.
- 차입 비용 골든: 숏 10주 종가 100, 연 365bp(=0.0365), 252일 → 세션당 1,000×0.0365/252 손계산,
  `CostAccrued` 기록과 현금 차감 일치.
- 라우터: 선언 시 `WeightTarget(-0.3)`·`QuantityTarget(-10)`·보유 초과 매도 허용, 미선언 시
  4단계 거절 유지(기존 테스트 그대로), 숏 포지션 `LiquidatePosition` → BUY 주문.
- 엔진 골든: 숏 진입 → 가격 하락 → 청산 equity 손계산; "Bar+Fill+Cost로 재계산" 검사 함수로
  모든 세션 스냅샷 재구성 일치.

### 5b MARGIN
- 브로커 단위: 여력 = 2.0×equity − gross; 현금 100, equity 100, 롱 없음 → 200 매수 가능;
  여력 초과 주문은 `CASH_LIMITED`(사유 문자열에 leverage 명시).
- 엔진 골든: 레버리지 2배 매수 → 현금 음수 → 세션 종료 이자 `CostAccrued` 손계산;
  자본 잠식(equity < 0)이 되는 하락 시 `NegativeCashError`가 아니라 `EquityWipedOut` 예외로 중단.
- 미선언 시 `max_gross_leverage > 1` 설정은 `prepare_strategy`에서 무시되지 않고 라우터가
  `UndeclaredFeatureUsed`로 거절(설정과 선언의 불일치를 조용히 넘기지 않음).

### 5c Basket
- 브로커 `quote`/`fill` 분리 회귀: 4단계 `tests/test_broker.py` 전부 통과(단일 경로 불변).
- 그룹 골든(페어 A 매수 10 / B 매도 10, 참여율 캡으로 B는 6주만 가능):
  BEST_EFFORT → A 10, B 6; ALL_OR_NONE → 체결 0, 두 leg CANCELLED; PROPORTIONAL → scale 0.6,
  A 6, B 6, 잔량 CANCELLED. 같은 세션 매도 대금이 매수 여력에 반영되는지(B 매도 먼저).
- 라우터: leg 타입 4종 각각 `group_id` 부여, PROPORTIONAL 미선언 거절, 같은 종목 중복 leg
  `ValueError`, BASKET 미선언 거절.
- 상태 전이: 바스켓 leg는 `NEW → FILLED | PARTIALLY_FILLED→CANCELLED | CANCELLED`만.
- serde: `group_id` round-trip, 기본값 None 호환.

### Zipline 대조
- 공매도 buy-hold(비중 −1.0 단일 종목) 세션 equity 대조는 원본 CSV(`data/raw`)가 없어
  4d 슬리피지 대조와 같은 사유로 보류. 하네스 README에 시나리오 정의만 추가한다.

## 다음 단계

6단계 Rust 포팅 (`2026-08-29-roadmap-overview.md`).
