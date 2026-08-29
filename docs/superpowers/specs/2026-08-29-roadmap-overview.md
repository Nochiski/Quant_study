# 남은 로드맵 개요 (2026-08-29)

v1(1–3단계)·Zipline 대조·시장 데이터 포트가 끝난 시점에서 남은 작업을 순서·범위·
검증 골자로 고정한다. 각 단계는 착수 시점에 별도 상세 스펙(`docs/superpowers/specs/`)을
쓰고, 상세 스펙에는 반드시 **테스트·검증 계획** 섹션을 둔다. 여기 적은 검증 골자는
그 섹션의 최소 요구다.

## 진행 상태 (2026-08-29)

| 단계 | 브랜치 | 상태 |
|---|---|---|
| 4 | `feat/order-lifecycle` | 구현 완료, 리뷰 중 |
| D | `feat/data-followups` | 구현 완료, 리뷰 중 |
| 5 | `feat/basket-short` | 구현 완료, 리뷰 중 |
| 6a·6b·6c·6d | `feat/rust-core` | 구현 완료 (6d: 다종목 벤치마크 결과 큐 이전 대신 값 타입 인덱싱 — 100종목 15.1s→4.2s) |

## 순서와 의존

```
4  주문 생명주기 확장 (4a→4b→4c→4d)   ← 상세: 2026-08-29-order-lifecycle-step4-design.md
D  데이터 측 후속 (D1 자본변동 포트, D2 유니버스 포트, D3 외부 DB 어댑터)   ← 4와 독립
5  Basket · 공매도 · MARGIN            ← 4 필요 (Cancel/Replace, 부분체결, 잔량 소유)
6  Rust 포팅 + Python/Zipline diff     ← 4·5로 코어가 안정된 뒤
```

D는 4와 독립이라 4 하위 단계 사이에 끼워 넣을 수 있다. 5·6은 순서 고정.

## D. 데이터 측 후속

포트 설계 스펙(`2026-08-29-market-data-ports-design.md`)의 "다음 단계"를 그대로 잇는다.

- **D1 자본변동 이벤트 포트**: 등락률이 가격제한폭을 넘는 세션을 `CorporateActionEvent`로
  노출하는 `CorporateActionSource` 포트 + KRX 원장 어댑터(원장에 액면분할·병합
  컬럼이 없으면 등락률 휴리스틱으로 감지하고 `detail`에 근거 기록). 엔진은 이벤트를
  전략에 전달만 하고(EventKind.CORPORATE_ACTION 승격), 가격 조정(adjusted price)은 하지
  않는다 — 조정은 원장 재빌드 책임.
  - 검증: 슬라이스 fixture에서 알려진 분할 종목 1개의 감지 세션 골든; 휴리스틱 오탐
    (상한가 연속) 케이스가 제외되는지; 부분 성공 금지 규칙 승계.
- **D2 유니버스 포트**: 종목마스터 기반 `UniverseSource.members(session) -> frozenset[
  InstrumentId]`. 세션별 구성 변경(상장·상폐)을 look-ahead 없이 돌려준다. 전략의
  `requirements()`가 유니버스를 선언하면 `DataFeed`가 그 종목만 로드.
  - 검증: 상폐 종목이 상폐 세션 이후 구성에서 빠지는지(골든), 미래 상장 종목이
    과거 세션에 안 보이는지(look-ahead 테스트), 종목 0개 세션 처리.
- **D3 외부 DB 어댑터**: `BarSource` 구현 하나 추가로 끝나야 한다는 포트 설계의
  가설 검증. DB 종류는 착수 시 결정(현재 후보 없음 — 후보가 생길 때 스펙).
  - 검증: `tests/test_ports.py`의 공통 계약 테스트를 어댑터 파라미터화로 재사용;
    엔진·전략 코드 diff 0 확인.

## 5. Basket · 공매도 · MARGIN

- **Basket**: `BasketAction(legs, group_policy)`. `BEST_EFFORT`는 leg 독립 실행,
  `ALL_OR_NONE`은 4d의 FOK 의미를 그룹으로 확장(어느 leg든 전량 불가면 전체 미체결),
  `PROPORTIONAL`은 가장 낮은 체결 비율에 맞춰 나머지 leg를 비례 축소 후 잔량 취소.
  같은 세션 안에서 leg 간 현금 의존(매도 대금으로 매수)은 4b의 "매도 먼저" 규칙을
  그룹 단위로 적용.
- **공매도·MARGIN**: 음수 포지션 허용, 차입 회계(공매도 대금 예치·이자율 설정),
  `Portfolio` 회계 확장, 음수 비중 타깃·`QuantityTarget(-n)` 허용. 4a의 "음수 결과
  거절" 분기를 Capability 조건부로 바꾼다.
- 검증 골자:
  - Basket 골든: 페어(A 매수/B 매도) × 정책 3종 × {양쪽 체결, 한쪽 유동성 부족} 손계산.
  - 회계 골든: 공매도 진입·환매 후 equity 손계산, 이자 누적; "Fill과 Bar만으로 equity
    재계산" 불변조건을 음수 포지션에서도 검사.
  - Zipline 대조: 공매도 buy-hold(음수 비중 단일 종목) 세션 equity 대조 — Zipline은
    공매도 회계를 기본 지원하므로 기준으로 쓸 수 있다.

## 6. Rust 포팅 + diff

- 범위: 이벤트 큐, BrokerSim, Portfolio 회계, OrderManager를 `backtest_engine._core`
  (maturin, PyO3)로 옮긴다. Python 호출 경계는 설계 노트대로 "시점별 Snapshot +
  Action 배치". 전략·라우터·Capability·포트/어댑터는 Python에 남는다.
- 검증 골자:
  - **동일성 diff**: 같은 입력(fixture 슬라이스 + 골든크로스 + 4d 슬리피지 설정)에
    Python 엔진과 Rust 코어를 돌려 EventStore(orders/fills/order_updates/snapshots)를
    레코드 단위로 비교, 오차 0(Decimal 수량) / 부동소수 equity는 1e-9 이내.
  - 기존 골든·상태 전이 테스트를 엔진 구현 파라미터화(`engine=python|rust`)로 양쪽에 실행.
  - Zipline 대조 하네스를 Rust 코어로도 실행해 기존 오차 상한 유지.
  - 성능 기준: 674MB 원장 전 종목 × 1,619세션 실행 시간을 Python 대비 기록
    (목표치는 착수 시 측정 후 확정).
