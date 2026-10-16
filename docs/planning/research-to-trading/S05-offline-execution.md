# S05: 영속 의도·가짜 브로커·오프라인 대사

이 단계는 응답 유실·프로세스 종료·중복 이벤트가 중복 주문이나 잘못된 보유·현금으로 이어지지
않는지 확인하는 작은 실행 기반이다. 네트워크, 실제 URL, 키, 계좌 연결, 자동 실행 스케줄,
HTTP/기존 bootstrap 연결은 없다. 이 구현을 실제 브로커에 연결할 준비가 끝났다는 뜻으로 읽으면
안 된다. S06의 계약 확인과 그 시점의 명시적 실행 지시가 별도로 필요하다.

## 책임과 의존성

| 노드 | 소유 책임 | 직접 의존 노드 |
|---|---|---|
| `domain/order_execution` | 값 검증, 누적 이벤트 전이, 대사, 목표 주식 수 차이 | 없음 |
| `application/offline_execution` | 자체 `BrokerPort`·`LedgerPort`, 최초 기준 잔고, 전송/복구/중지 | `domain/order_execution` |
| `adapters/outbound/execution_sqlite` | 원장/이벤트 기록, 원자 claim, 파일 실행자 잠금 | application, domain, `sqlite_store` |
| `adapters/outbound/fake_broker` | 오프라인 브로커 사실과 장애 fixture | application, domain |

노드 밖의 import는 각 `facade`를 사용한다. 유스케이스는 concrete adapter를 import하지 않는다.
기존 `domain/portfolio`·`backtest_engine`·`database/src/api.py`에는 실행 책임을 추가하지 않는다.
선언 owner는 `.claude/rules/strategy-workbench-sot.md`와 `backend-package-boundary.md`를 따른다.

## 범위와 값의 의미

- 입력은 **이미 결정된 목표 주식 수**다. 목표 비중을 주식 수로 변환하는 가격·세금·단주·거래소
  단위 정책은 미구현이다. 기존 컴파일러의 이전 목표 비중 기반 buffer를 실제 잔고 기반으로
  바꾸지 않는다.
- 수량·가격·체결대금·비용은 오프라인 예제용 정수 주식/정수 KRW다. `bool`, 실수, 음수 및
  `10**15` 초과 값은 거절하고 주문 금액+비용 예산에도 이 상한을 적용한다. 실 브로커의 소수
  금액·세금·수수료 반올림 규약을 대표하지 않는다. 거래소/신용/공매도/환율은 다루지 않는다.
- 한 주문은 매수 또는 매도이며 양의 지정가·수량과 비용 예산을 가진다. 실제 누적 비용은 예산과
  누적 체결대금을 넘을 수 없다. 매수는 지정가 이하, 매도는 이상에서만 체결된다. 잔고가 없는
  매도와 현재 현금/예약분을 넘는 매수는 전송 전에 막고 fake도 최종 거절한다.
- 계좌의 `cash_krw`는 예약현금을 포함한 전체 현금이다. 가용현금은 현금−예약현금이다. 매수
  미체결 수량×지정가+아직 쓰지 않은 비용 예산 전체를 예약한다. 매도는 미체결 주식 수를 예약한다.
  모든 종목의 미체결 주문 예약을 합산한다. 매도 미체결 대금을 새 매수 자금으로 쓰지 않는다.
- `plan`의 목표는 전체 목표 보유 지도다. 생략한 현재 보유 종목의 목표는 0이다. 차이는
  `목표 수량 − (현재 보유 + 매수 미체결 − 매도 미체결)`이며 조회만 하고 전송하지 않는다.
  `plan`은 원장을 갱신하지 않으므로 새 체결이 있으면 먼저 `recover()`를 명시적으로 호출한다.

## 원장과 전송 경계

전용 DB 파일 경로를 호출자가 명시한다. `execution_sqlite`가 판본 1, application_id
`0x4D484558`의 `execution_meta`(불변 최초 baseline/지속 중지), `order_intents`(의도+최신 누적
사실), `order_events`(관측 revision별 사실)을 소유한다. `sqlite_store`가 비어 있지 않은 외부
DB·다른 owner·미래 판본·DDL 불일치를 거절한다. 전략/연구/assistant DB와 같은 파일을 쓰면
실패하며 기본 런타임 경로·환경 변수는 없다.

1. `initialize()`는 주문과 예약이 없는 fake 최초 snapshot의 실제 보유·현금을 한 번 저장한다.
   기존 baseline은 덮어쓰지 않는다. 최초 수동 보유 주식도 baseline에 포함한다.
2. `submit`은 DB별 단일 실행자 잠금 안에서 같은 intent id의 내용 충돌과 기존 claim을 검사한다.
   재호출한 이미 claim된 의도는 다시 전송하지 않는다. 같은 신호를 같은 intent id로 공급하는
   책임은 호출자에게 있다. 서로 다른 id로 보낸 동일 신호를 자동으로 식별하지 않는다.
3. 브로커 누적 snapshot 한 번을 대사와 가용 잔고 판단에 함께 쓴다. 차이/지속 중지가 있으면
   신규 주문을 막는다. 의도를 `READY`로 커밋하고 원자적으로 `UNKNOWN`으로 claim·커밋한다.
4. 이 두 저장이 성공한 뒤에만 한 번 `broker.submit()`을 호출한다. 저장 실패는 전송을 금지한다.
   전송이 시작된 뒤 ACK/원장 기록 실패는 `UNKNOWN` 결과이며 자동 재발주하지 않는다.
5. `UNKNOWN` 커밋 직후 실제 전송 전에 프로세스가 종료되어도 재전송하지 않는다. 완전한 조회에
   주문이 없으면 미해결 차이로 중지한다. 이 보수적 원칙은 주문 누락을 허용하며 중복 주문을 막는다.
   이 slice에는 미해결 의도를 강제로 다시 READY로 돌리는 기능이 없다.

한 DB 파일의 단일 실행자는 POSIX `flock` 또는 Windows `msvcrt.locking`으로 유지한다. 같은
프로세스의 재진입도 거절한다. OS가 종료 시 잠금을 해제하므로 만료 시간을 보고 도는 주문의 권한을
다른 실행자에게 넘기지 않는다. POSIX 프로세스 종료 복구를 테스트했다. Windows 분기는 구현했으나
이 환경에서 검증하지 않았다. 미지원 OS는 잠금 없이 진행하지 않고 `LedgerError`로 실패한다.
SQLite/잠금 파일 경로를 alias나 별도 사본으로 나누거나 네트워크 파일 시스템에서 분산 잠금으로
사용하는 것은 지원하지 않는다. port의 저수준 쓰기 메서드는 유스케이스 안에서 호출하는 계약이다.

## 누적 사실·복구·중지

`OrderState`는 로컬 `READY`, `UNKNOWN`, 브로커 `OPEN`, `PARTIAL`, `FILLED`, `CANCELLED`,
`REJECTED`다. 주문 의도와 브로커 사실을 구분하고 주문별 누적 체결수량·체결대금·비용을 저장한다.
`OrderFact`의 revision은 fake가 부여하는 양의 정수이며 같은 revision의 내용은 불변이다.

- 중복 사실은 한 번만 반영한다. 이전 revision은 최신 상태를 되돌리지 않는다. 이미 관측한 이전
  revision의 다른 내용도 이벤트 기록으로 충돌을 잡는다.
- 누적 수량/대금/비용 감소, 지정가를 어기는 증분 체결, 종료 뒤 새 사실, 같은 revision의 내용
  충돌은 거절한다. 정정·체결 취소·종료 주문 재개는 지원하지 않는다.
- `recover`는 저장한 id의 누적 사실을 갱신한 후, 최초 기준 현금/보유에 **최신 누적 체결을 한 번**
  더해 현재 broker snapshot과 비교한다. 현재 잔고에 체결을 다시 더하지 않는다. 현금·수량·예약
  총액·주문 내용이 모두 일치해야 성공한다. 미추적 주문, 주문 조회 누락, 수동 잔고 변경도 차이다.
- 대사 차이는 지속 중지를 켠다. `stop()`은 신규 전송을 멈추고 기존 주문을 취소하지 않는다.
  `cancel(id)`는 알려진 주문에 명시적으로 취소를 요청한다. 부분체결은 유지하고 미체결 예약만
  해제한다. 취소 ACK 유실도 조회로 복구하며 주문을 다시 내지 않는다.
- 일치한 `recover`는 지속 중지를 자동 해제하지 않는다. `resume()`이 같은 대사를 통과할 때만
  해제한다. 손상된 JSON/값은 adapter에서 `LedgerError`로 바꾸어 실행 결과 `ERROR`로 차단한다.

fake의 snapshot은 기준 이후 **모든 주문의 최신 누적 사실(종료 포함)**과 같은 시점의 잔고를
완전하게 반환한다. fake에는 계좌/세션/날짜별 조회 범위와 페이지가 없다. intent id 조회 가능성,
revision 순서, 완전성은 실제 공급자의 보장이 아니다. 조회와 전송 사이 외부 계좌 변동을 잠글 수는
없다. fake는 전송 시 자금/주식을 다시 검사하고 다음 대사가 추가 차이를 잡는다. 실제 공급자에는
식별/조회 완전성·취소 의미·정정·부분체결 필드·재접속 보장과 계좌 범위 확인이 필요하다.

## 재현 가능한 사용 경로

다음 코드를 저장소 루트에서 `PYTHONPATH=backend/src backend/.venv/bin/python`으로 실행할 수
있다. 예제 DB는 임시 디렉터리에만 생성한다. 테스트가 이 경로를 실제로 검증한다.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import ExecutionSqliteLedger
from strategy_workbench.adapters.outbound.fake_broker.facade.broker import FakeBroker
from strategy_workbench.application.offline_execution.facade.execution import OfflineExecutionService
from strategy_workbench.domain.order_execution.facade.orders import Account, Intent

with TemporaryDirectory() as directory:
    path = Path(directory) / "execution.sqlite"
    ledger = ExecutionSqliteLedger(path)
    broker = FakeBroker(Account(10_000, (("A", 10),)))
    service = OfflineExecutionService(ledger, broker)
    print(service.initialize())
    print(service.plan({"A": 15}))  # {'A': 5}
    intent = Intent("signal-1", "A", "buy", 5, 100, 10)
    broker.lose_next_ack = True
    print(service.submit(intent))  # UNKNOWN, 브로커 접수 이후 응답 유실
    ledger.close()

    ledger = ExecutionSqliteLedger(path)
    service = OfflineExecutionService(ledger, broker)
    print(service.recover())  # OPEN 확인, 재전송 없음
    broker.fill("signal-1", quantity=2, price_krw=100, fee_krw=1)
    print(service.recover())  # PARTIAL·현금·예약 대사
    print(service.plan({"A": 15}))  # {}, 보유 12 + 미체결 3
    service.stop()  # 미체결 주문은 남는다
    print(service.cancel("signal-1"))
    print(service.recover())  # 보유 12, 예약 0, 중지는 유지
    print(service.resume())
    print(service.plan({"A": 15}))  # {'A': 3}
    print(broker.submit_calls)  # 1
    ledger.close()
```

fake 객체는 외부 브로커의 별도 수명을 표현해 서비스/원장 재시작 후에도 유지한다. fake 자체의
프로세스 재시작 영속 저장은 없다. OS 종료 테스트는 실제 하위 프로세스에서 UNKNOWN을 커밋한 뒤
즉시 종료시켜 잠금 해제와 보수적 복구를 검증한다. 브로커 사실까지 동시에 사라지면 완전한 대사가
불가능하며 신규 전송을 막는다.

## 확인한 장애 경로와 남은 범위

`backend/tests/test_offline_execution.py`는 소형 인라인 계좌와 임시 DB를 사용한다.

- 저장 실패 zero-send, READY 저장 직후 복구, UNKNOWN 커밋 직후 실제 프로세스 종료
- 접수 후 ACK 유실, ACK 영속 기록 실패, 서비스/원장 재시작 후 한 번 전송 유지
- 부분체결·완전체결·중복·역순·과거 revision 충돌, 부분체결 취소·취소 ACK 유실·거절
- 최초 실제 보유와 매수/매도 현금, 다른 종목 미체결의 예약현금·예약주식 합산
- 수동 잔고 변경·지속 중지·명시 재개, 동일 파일의 복수 실행자/원자 claim, 다른 DB owner 거절
- 손상된 baseline/주문 JSON·잘못된 값 차단, bool/실수/음수/상한 수량 거절

```sh
backend/.venv/bin/python -m pytest backend/tests/test_offline_execution.py backend/tests/architecture -q
```

순수 오프라인 상태 제어의 증거다. 비용 후 수익성, 목표 비중 sizing, 실시간 시장 위험/가격 급변,
실제 브로커 장애, 거래일/계좌/세션 분리, 주문 rate limit, 실시간 알림/감사 UI, 네트워크 전송,
분산 실행자는 검증하지 않았다. S06 이후 및 실계좌 주문은 이 단계의 범위 밖이다.
