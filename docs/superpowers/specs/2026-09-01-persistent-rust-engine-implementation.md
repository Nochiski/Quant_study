# Persistent Rust Engine 구현 계획

작성일: 2026-09-01
상태: M0~M6 기능 구현 완료 — 2×/FFI 최적화 후속 필요
목표 브랜치: `main`
기준 커밋: `f93c4f5` (`refactor: split Rust backtest core into modules`)

이 문서는 구현 체크리스트이자 컨텍스트 압축 후 재개 문서다. 작업자는 새 세션에서 이 문서를
처음부터 읽고, 아래 **현재 재개 지점**과 체크박스를 기준으로 이어서 작업한다.

## 체크 규칙

- `[ ]`: 시작하지 않았거나 검증되지 않음.
- `[~]`: 구현 중. 작업을 중단하거나 컨텍스트가 압축되기 전에 재개 지점을 반드시 갱신한다.
- `[x]`: 구현과 해당 단계 검증이 모두 끝남.
- 코드를 작성했어도 테스트가 끝나지 않았으면 `[x]`로 바꾸지 않는다.
- 각 PR 단위가 끝날 때 이 문서의 체크박스, 검증 기록, 다음 작업을 함께 갱신한다.
- 기존 `core="python"`과 `core="rust"` 경로는 최종 전환 전까지 삭제하지 않는다.

## 2026-09-01 체크포인트

- 전체 체크리스트: 67개 중 39개 완료, 28개 남음.
- M0~M2 완료.
- M3: 9개 중 5개 완료. Rust instrument registry, columnar feed, 일괄 feed 전송,
  HistoryStore, 선언된 history window까지 이전.
- persistent runtime이 Portfolio, 주문·그룹 lifecycle, ID sequence, Decision Router,
  feed/history와 fill 회계를 소유.
- 100종목 주문 집중 벤치마크 최신 측정: Python 대비 1.719배.
- 작은 4종목 fixture: Python 대비 1.365배로 성능 회귀 없음.
- 다음 재개 지점: route 결과의 신규 주문·그룹을 Rust에 staging한 뒤 다음 MARKET에서
  활성화하여 주문별 Python→Rust FFI를 제거하고, Rust EventQueue 이전을 계속한다.

## 2026-09-02 M3 완료 체크포인트

- 전체 체크리스트: 67개 중 43개 완료, 24개 남음.
- M3 9개 항목 완료. feed, history, native EventQueue, session close/schedule,
  corporate action, short borrow/margin interest가 persistent Rust runtime으로 이전됨.
- route 결과의 주문·그룹을 Rust pending 영역에 저장하고 다음 MARKET에 일괄 활성화하여
  주문 22,243건의 개별 `place_order` FFI와 그룹 등록 FFI를 제거.
- Rust EventQueue가 `(UTC microsecond, priority, seq)` heap을 소유하고 Python typed payload는
  M4/M5 전환 동안 token side table로 호환 유지.
- 100종목 주문 집중 최신 측정: Python 1.996398초, persistent Rust 1.164860초,
  Python 대비 1.714배. orders/fills/final equity 동일.
- 다음 재개 지점: M4 `CallbackFrame`과 token 기반 pull callback 상태 머신.

## 2026-09-02 M4·M5 완료 체크포인트

- 전체 체크리스트: 67개 중 61개 완료, 6개 남음.
- M4 11개 항목 완료. Rust runtime이 token 기반 pull callback lifecycle을 소유하고,
  stale/double submit을 거절하며 전략 예외 시 FAILED 상태와 partial trace를 보존.
- `RustStrategyContext`가 callback 시점의 portfolio/open orders/history/universe view를 보존하며
  MARKET/FILL/ORDER_UPDATE/CORPORATE_ACTION callback 패리티를 유지.
- M5 7개 항목 완료. Rust append-only compact record index, 1,024건 record batch,
  compact Order/Fill/OrderUpdate payload와 `finish()` 결과 batch를 추가.
- `BacktestResult.orders`/`fills`는 최초 조회 시 공개 tuple로 materialize되고 metrics는 compact fill
  batch에서 한 번 계산한다. `engine.event_store`의 기존 trace/typed query 동작은 유지.
- 100종목 주문 집중 최신 측정: Python 2.072538초, persistent Rust 1.125227초,
  Python 대비 1.842배. orders/fills/final equity 동일.
- 격리 프로세스 peak RSS: Python 169.0 MiB, persistent Rust 197.5 MiB, 1.169배로
  M5 허용 기준 1.25배 이하.
- 다음 재개 지점: M6 panic→Python 예외 변환과 runtime poison hardening.

## 2026-09-03 M6 완료 체크포인트

- 전체 구현 체크리스트: 67개 중 67개 완료.
- 공개 `core="rust"`를 persistent runtime으로 승격했다. 실험 이름이던
  `rust_persistent`는 호환 alias로 유지하고, 구 세션 단위 경로는 `rust_legacy`로 분리했다.
- 구 `backtest_core.process_market()`와 `core="rust_legacy"`는 `DeprecationWarning`을 내며
  별도 정리 PR 전까지 패리티 테스트 대상으로 유지한다.
- 실제 Rust panic을 `RustCorePanic`으로 변환하고 runtime을 `FAILED`로 poison하며 partial trace를
  보존하는 통합 테스트를 추가했다.
- 빈 feed, halted/missing bar, GTC run-end와 randomized differential을 공개 Rust 경로 및 호환
  경로에서 검증했다.
- 전체 저장소 fixture는 전 기간 완주 4종목뿐이므로, 100/300종목 측정은 실제 KRX 가격 경로를
  결정론적으로 복제해 동일 원장 로직에 투입했다. 세 코어의 equity/orders/fills가 모두 동일했다.
- 100종목 중앙값: Python 2.053842초, legacy Rust 2.039406초, persistent Rust 1.194862초.
  Python 대비 1.719배로 최소 1.5배 게이트는 통과했으나 2배 목표에는 미달했다.
- 300종목 중앙값: Python 5.311545초, legacy Rust 5.456815초, persistent Rust 3.281942초.
  Python 대비 1.618배로 2배 목표에는 미달했다.
- 실제 4종목 fixture 중앙값: Python 0.142606초, persistent Rust 0.104528초로 1.364배이며
  작은 fixture 회귀 금지 게이트를 통과했다.
- 기능 마이그레이션은 완료했지만 엄격한 세션당 FFI 0회 게이트는 아직 미달이다.
  `process_market_index`, session close 및 Rust queue 어댑터 경계를 callback-to-callback driver로
  합치는 작업이 다음 성능 최적화 지점이다.

## 현재 재개 지점

> 이 블록은 작업을 진행할 때마다 최신 상태로 덮어쓴다.

- 현재 단계: M6 — 하드닝과 전환
- 현재 작업: M0~M6 기능 체크리스트 완료
- 마지막 완료 항목: 공개 `rust` 승격, panic poison, edge-case/벤치마크/문서 검증
- 다음 작업: callback-to-callback Rust driver로 세션별 FFI를 제거하고 2× 목표 재측정
- 알려진 blocker: 없음
- 작업 트리의 기존 사용자/선행 변경: 없음
- 마지막 검증:
  - `cargo fmt --manifest-path rust/backtest_core/Cargo.toml -- --check` — passed
  - `cargo clippy --manifest-path rust/backtest_core/Cargo.toml --all-targets -- -D warnings`
  - `cargo test --manifest-path rust/backtest_core/Cargo.toml` — 13 passed
  - `uv run pytest tests/test_core_parity.py -q` — 199 passed
  - `uv run pytest -q` — 540 passed
  - `uv run ruff check src tests scripts examples` — passed
  - `uv run pyright` — 0 errors
  - `uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 100
    --synthetic --core all --warmup 1 --repeat 5
    --json-out benchmarks/baseline/persistent-rust-m1.json`
    - Python 2.250524초, current Rust 2.261499초, persistent Rust 1.809038초
    - persistent Rust는 Python 대비 1.244배, current Rust 대비 1.250배
    - 세 코어 모두 orders 22,243 / fills 22,155 / 최종 equity 2,321,985,873.9513392
  - M2 basic Router: `benchmarks/baseline/persistent-rust-m2-basic-router.json`
    - Python 2.482101초, current Rust 2.365712초, persistent Rust 2.056015초
    - 시스템 부하 분산이 커 절대값/배수는 참고용이며 결과 signature는 모두 동일
  - M3 history/fill accounting: `benchmarks/baseline/persistent-rust-m3-history-fill-accounting.json`
    - Python 2.364468초 / persistent Rust 1.375215초 — Python 대비 1.719배
    - orders 22,243 / fills 22,155 / 최종 equity가 동일
  - 작은 fixture: `benchmarks/baseline/persistent-rust-m3-small.json`
    - Python 0.138627초 / persistent Rust 0.101595초 — Python 대비 1.365배, 회귀 없음
  - M3 batched orders/costs: `benchmarks/baseline/persistent-rust-m3-batched-orders-costs.json`
    - Python 1.969302초 / persistent Rust 1.259849초 — Python 대비 1.563배
    - route가 주문·그룹을 Rust pending 영역에 staging하여 주문 22,243건의 개별 FFI를 제거
    - orders 22,243 / fills 22,155 / 최종 equity가 동일
  - M3 native EventQueue: `benchmarks/baseline/persistent-rust-m3-native-event-queue.json`
    - Python 1.996398초 / persistent Rust 1.164860초 — Python 대비 1.714배
    - Rust가 timestamp/priority/FIFO sequence heap을 소유하며 결과 signature가 동일
  - M5 final: `benchmarks/baseline/persistent-rust-m5-final.json`
    - Python 2.072538초 / current Rust 1.968841초 / persistent Rust 1.125227초
    - persistent Rust는 Python 대비 1.842배, current Rust 대비 1.750배
    - orders 22,243 / fills 22,155 / 최종 equity가 동일
  - M5 isolated peak RSS:
    - `benchmarks/baseline/persistent-rust-m5-memory-python.json` — 169.0 MiB
    - `benchmarks/baseline/persistent-rust-m5-memory-rust.json` — 197.5 MiB
    - persistent/Python = 1.169배, M5 gate 1.25배 이하 통과
  - M6 actual fixture: `benchmarks/baseline/persistent-rust-m6-real-fixture.json`
    - Python 0.142606초 / legacy Rust 0.157687초 / persistent Rust 0.104528초
    - 4종목·1,231세션·4,924 bars, persistent Rust는 Python 대비 1.364배
  - M6 100종목: `benchmarks/baseline/persistent-rust-m6-100.json`
    - Python 2.053842초 / legacy Rust 2.039406초 / persistent Rust 1.194862초
    - persistent Rust는 Python 대비 1.719배, orders 22,243 / fills 22,155 / equity 동일
  - M6 300종목: `benchmarks/baseline/persistent-rust-m6-300.json`
    - Python 5.311545초 / legacy Rust 5.456815초 / persistent Rust 3.281942초
    - persistent Rust는 Python 대비 1.618배, orders 52,344 / fills 52,121 / equity 동일

## 전환 전 문제 정의

기준선 당시 `core="rust"`는 Persistent Engine이 아니었다. Python `_Run`이 아래 상태를 소유했다.

- `HistoryStore`
- `PortfolioLedger`
- `OrderManager`
- `DecisionRouter`
- `EventStore`
- `EventQueue`

매 세션 `_on_market_rust()`는 모든 대기 주문, 그룹, Bar를 tuple/list/dict로 다시 만들고
`backtest_core.process_market()`을 호출한다. Rust는 한 세션의 체결 계획만 반환하고, Python이
그 결과를 다시 `FillEvent`, `OrderUpdateEvent`, Queue 이벤트로 복원한다.

그 결과 주문 집중 100종목 벤치마크에서 중앙값은 다음 수준이다.

- Python: 2.826초
- 현재 Rust: 2.643초
- Rust 개선: 1.07배, 약 6.5%

## 최종 목표

Python은 전략과 공개 API를 유지하고 Rust가 실행 상태를 소유한다.

```text
Python BacktestEngine facade
  ├─ capability/requirements 검증
  ├─ Python Strategy.on_event()
  └─ BacktestResult 호환 객체

Persistent Rust Engine
  ├─ Feed/session cursor
  ├─ HistoryStore
  ├─ EventQueue + priority + sequence
  ├─ DecisionRouter
  ├─ OrderManager + basket groups + ID sequences
  ├─ Portfolio + buying power + costs
  ├─ Corporate actions
  └─ compact EventStore
```

최종 실행 프로토콜은 Rust가 Python을 직접 재진입하는 push callback이 아니라 Python이 Rust를
구동하는 pull 방식으로 한다.

```python
runner = backtest_core.PersistentEngine(config_wire, requirements_wire, feed_batch)

while frame := runner.run_until_callback():
    context = RustStrategyContext(frame)
    decision = strategy.on_event(context, frame.event)
    runner.submit_decision(frame.token, decision_to_wire(decision))

result = result_from_batch(runner.finish())
```

Rust 상태 머신:

```text
READY → RUNNING → AWAITING_DECISION(token) → RUNNING → FINISHED
                                               └──────→ FAILED
```

## 불변 계약

다음은 성능보다 우선한다.

- 이벤트 순서: `MARKET → FILL → NOTIFY → SESSION_CLOSE → ORDER`
- 같은 priority는 단조 증가 `seq` 기준 FIFO.
- T 세션 종가 결정은 T+1 이후에만 체결.
- 전략이 T에 보는 포트폴리오는 T의 체결과 비용이 반영된 상태.
- Python reference와 Decision/Order/Fill/Group ID가 동일.
- `EventStore`의 record `seq`, `ts`, `kind`, payload 순서가 동일.
- Fill 가격, 수량, 수수료와 float 연산 순서가 동일.
- 포지션 삽입 순서와 snapshot 합산 순서가 동일.
- 오류 타입, 발생 시점, 메시지 핵심 접두어가 동일.
- 정수 주식 수량 경계를 유지한다.
- 기존 Python 전략 API와 `BacktestEngine.run()` 서명을 변경하지 않는다.

## 단계별 계획

### M0 — 기준선과 계약 고정

- [x] 기존 Python/Rust 전체 패리티 테스트 확보.
- [x] 주문 집중 100종목 벤치마크 기준선 측정.
- [x] 현재/목표 아키텍처 HTML 시각화 작성.
- [x] 구현 재개 문서와 체크 규칙 작성.
- [x] EventStore 전체 record를 안정적인 비교 형태로 정규화하는 trace helper 추가.
- [x] 전략 callback의 `(event, decision)` 순서를 담는 Decision Tape 타입 추가.
- [x] 골든·숏·마진·바스켓·분할 시나리오의 trace fixture 또는 snapshot test 추가.
- [x] benchmark가 raw samples와 JSON 결과를 저장할 수 있게 개선.

완료 게이트:

- 같은 입력의 trace가 반복 실행에서 byte-for-byte 동일.
- 벤치마크가 Python/current Rust/persistent Rust를 같은 프로세스로 교차 측정 가능.

### M1 — Persistent runtime 첫 절단면

목표: 세션 사이 주문·그룹·ID sequence·포트폴리오를 Rust 메모리에 유지한다. Python 전략과
Router는 이 단계에서 유지해 변경 폭을 제한한다.

- [x] `core="rust_persistent"`를 실험 코어로 추가.
- [x] Rust `PersistentEngine` PyClass와 명시적인 lifecycle 추가.
- [x] Rust runtime이 decision/order/fill/group ID sequence를 소유.
- [x] Rust runtime이 open order와 triggered/remaining 상태를 소유.
- [x] Rust runtime이 basket group 상태를 소유.
- [x] Rust runtime이 Portfolio 상태를 소유.
- [x] Python `PersistentOrderManager` 어댑터 추가.
- [x] Python `PersistentPortfolio` 어댑터 추가.
- [x] 새 주문·취소·정정 상태를 runtime에 한 번만 반영.
- [x] `process_market()` 입력에서 전체 open entries/groups 재마샬링 제거.
- [x] persistent path가 기존 Python `FillEvent`/EventQueue 적용 순서를 보존.
- [x] `core="rust_persistent"` 패리티 시나리오 추가.

완료 게이트:

- 전체 테스트 통과.
- persistent 경로가 세션마다 open order/group 목록을 Python에서 전달하지 않음.
- Python과 persistent Rust의 EventStore trace가 동일.
- 현재 Rust보다 주문 집중 벤치마크가 악화되지 않음.

측정 결과(`benchmarks/baseline/persistent-rust-m1.json`):

- Python: 2.250524초 (1.000배)
- current Rust: 2.261499초 (0.995배)
- persistent Rust: 1.809038초 (1.244배)
- persistent Rust는 current Rust보다 약 20.0% 짧은 시간에 완료했다.
- 주문 수, 체결 수, 최종 equity는 세 코어가 동일했다.

### M2 — Decision Router와 주문 lifecycle 이전

목표: Python 결정만 Rust에 전달하고 주문 생성부터 만료까지 Rust가 담당한다.

- [x] Rust `DecisionWire`/`ActionWire` 버전 계약 추가.
- [x] `NoAction`, `SetPortfolioTarget`, `SetPositionTarget` 포팅.
- [x] `AdjustPosition`, `LiquidatePosition` 포팅.
- [x] `SubmitOrder`, `CancelOrder`, `ReplaceOrder` 포팅.
- [x] Basket `best_effort`, `proportional`, `all_or_none` 포팅.
- [x] feature/action declaration 검증 포팅.
- [x] oversell, short, margin 검증과 오류 매핑 포팅.
- [x] Python DecisionRouter를 persistent 경로에서 제거.
- [x] Python은 callback당 DecisionBatch 한 번만 전달.

완료 게이트:

- [x] 모든 action 유형 randomized differential 통과.
- [x] ID와 오류 발생 순서까지 Python reference와 동일함을 오류 differential로 고정.
- [x] 전략 callback당 Python→Rust 호출이 1회 이하임을 계측 테스트로 고정.

### M3 — Feed, History, EventQueue 이전

목표: 세션 루프와 시장 상태가 Rust에서 지속된다.

- [x] instrument registry를 만들고 내부 키를 `u32`로 전환.
- [x] timestamp/session offsets/OHLCV columnar batch 입력 추가.
- [x] 전체 feed를 실행 시작 시 한 번만 Rust로 전송.
- [x] Rust HistoryStore와 결측 NaN 정렬 규칙 포팅.
- [x] 선언된 HistoryRequest window 생성 포팅.
- [x] Rust EventQueue `(ts, priority, seq)` 포팅.
- [x] session close 평가와 schedule 판정 포팅.
- [x] corporate action settlement session과 적용 포팅.
- [x] short borrow/margin interest 비용 포팅.

완료 게이트:

- 세션당 Bar dict 생성 제거.
- retained context가 과거 `now` 기준으로 동일한 history를 반환.
- look-ahead 방지 테스트 전부 통과.

### M4 — Python 전략 callback 브리지

- [x] `CallbackFrame`과 단조 증가 token 추가.
- [x] `run_until_callback()` 추가.
- [x] `submit_decision(token, decision)` 추가.
- [x] stale/double submit 거절.
- [x] MARKET, FILL, ORDER_UPDATE, CORPORATE_ACTION callback 지원.
- [x] Python `RustStrategyContext` 구현.
- [x] portfolio/current weight/position/cash 조회 지원.
- [x] open orders 조회 지원.
- [x] declared history window를 NumPy 배열로 반환.
- [x] universe membership 조회 지원.
- [x] 전략 예외 발생 시 runtime을 FAILED 상태로 전환하고 partial trace 보존.

완료 게이트:

- Python 전략 코드를 수정하지 않고 persistent runtime에서 실행.
- Rust가 GIL을 잡은 상태로 Python callback을 직접 호출하지 않음.
- 전략이 context를 보관했다가 이후 조회하는 기존 동작 보존.

### M5 — EventStore와 결과 배치

- [x] Rust compact Record 타입과 append-only store 추가.
- [x] MARKET/DECISION/ORDER/UPDATE/FILL/SNAPSHOT/CA/COST record 포팅.
- [x] 실행 중 Python Event 객체 생성을 제거.
- [x] `finish()` 결과 batch 추가.
- [x] Python BacktestResult/EventStore lazy materialization 어댑터 추가.
- [x] metrics Rust 계산 또는 결과 batch 기반 Python 일괄 계산.
- [x] 디버그 trace 모드 추가.

완료 게이트:

- 실행 중 fill/order마다 Python 객체를 생성하지 않음.
- 공개 `BacktestResult`와 `engine.event_store` 동작 호환.
- Peak RSS가 Python reference의 1.25배 이하.

검증 결과:

- `CompactOrder`/`CompactFill`은 `finish()` 전 공개 Event를 만들지 않으며 결과 필드 최초 조회 시
  기존 tuple 타입으로 materialize된다.
- primitive `(seq, timestamp_micros, kind, payload_token)` debug trace가 전체 record 순서를 보존한다.
- 100종목 주문 집중 벤치마크 1.842배, 격리 peak RSS 1.169배로 완료 게이트를 통과했다.

### M6 — 하드닝과 전환

- [x] seed 기반 randomized differential suite 추가.
- [x] Rust panic을 Python 예외로 변환하고 runtime poison 처리.
- [x] 빈 feed, halted bar, missing bar, GTC run end 케이스 검증.
- [x] 대규모 100/300종목 원장 경로 벤치마크(저장소 제약상 실제 KRX 경로를 확장).
- [x] 작은 fixture 성능 회귀 검사.
- [x] 문서와 HTML 벤치마크 결과 갱신.
- [x] `rust_persistent`를 `rust`로 승격.
- [x] 구 `process_market` 경로 deprecation 후 별도 정리 PR에서 제거.

최종 성능 게이트:

- 100종목 주문 집중: Python 대비 최소 1.5배, 목표 2배.
- 300종목 실제 원장: Python 대비 목표 2배.
- 작은 4종목 fixture: Python 대비 10% 이상 악화 금지.
- 결과 패리티: 100%.
- 세션당 FFI: 0회. 전략 callback과 종료 batch에서만 왕복.

M6 측정 판정:

- 100종목 최소 1.5배: **통과(1.719배)**. 목표 2배는 미달.
- 300종목 목표 2배: **미달(1.618배)**.
- 작은 4종목 fixture 회귀 금지: **통과(1.364배 향상)**.
- 결과 패리티: **통과**. 세 코어의 equity/orders/fills와 differential trace가 동일하다.
- 세션당 FFI 0회: **미달**. persistent state 재마샬링은 제거했지만 Python event loop에서
  session market/close와 queue 어댑터를 호출한다.

후속 최적화 백로그(위 67개 기능 체크리스트와 별도):

- Rust가 다음 전략 callback까지 market, fill, update, close와 queue drain을 진행하는 driver API.
- callback frame의 portfolio/open-order/history view를 compact 또는 lazy batch로 묶어 왕복 수 축소.
- 서로 다른 실제 종목 100/300개를 포함한 외부 원장으로 성능 게이트 재검증.

## 첫 구현 슬라이스 상세

M1에서는 최종 아키텍처로 바로 점프하지 않는다. 다음 형태로 시작한다.

```text
Python
  Strategy + DecisionRouter + EventQueue + EventStore
                 │
                 │ 신규 주문/취소/정정만 전달
                 ▼
Rust PersistentEngine
  Portfolio + OrderBook + Groups + ID sequences
                 │
                 │ process_market(ts, bars) — entries/groups는 내부 상태 사용
                 ▼
Python
  Fill/Update 이벤트를 기존 순서대로 적용
```

이 단계에서 Python은 immutable `OrderEvent` 객체를 결과 호환을 위해 보관할 수 있지만,
`remaining`, `triggered`, open/closed 여부의 단일 진실 원천은 Rust다. 같은 mutable 상태를 양쪽에서
동시에 갱신하지 않는다.

예상 Rust API 초안:

```python
runtime = backtest_core.PersistentEngine(
    initial_cash,
    allow_short,
    allow_margin,
    max_gross_leverage,
)

runtime.next_decision_id()
runtime.next_order_id()
runtime.next_fill_id()
runtime.next_group_id()
runtime.place_order(order_wire)
runtime.register_group(group_wire)
runtime.remove_order(order_id)
runtime.open_order_states()
runtime.process_market(ts, bars, fee_rate, participation, slippage)
runtime.apply_fill(key, side, quantity, price, fee)
runtime.mark(closes)
runtime.portfolio_snapshot()
```

## 검증 명령

Rust 포맷·정적 검사·단위 테스트:

```powershell
cargo fmt --manifest-path rust/backtest_core/Cargo.toml -- --check
cargo clippy --manifest-path rust/backtest_core/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/backtest_core/Cargo.toml
```

확장 재빌드와 패리티:

```powershell
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release
uv run pytest tests/test_core_parity.py -q
uv run pytest -q
```

Python 정적 검사:

```powershell
uv run ruff check src tests scripts examples
uv run pyright
```

## 주요 위험과 대응

### Python context 보관

전략이 callback 이후에도 context를 보관할 수 있다. Rust engine의 현재 상태를 직접 참조하게 하면
과거 context가 미래 데이터를 보게 된다. `CallbackFrame`은 `now`, portfolio, open orders와 history
end index를 고정해야 한다.

### Decimal과 float 동일성

수량과 가격 조건의 Decimal 원문은 wire에서 문자열 또는 정확한 scaled integer로 보존한다. f64
연산은 Python reference와 결합 순서를 맞춘다.

### EventStore 메모리 이중화

Rust compact store 전체를 실행 종료 시 Python 객체로 한꺼번에 복제하면 peak memory가 커질 수 있다.
결과 종류별 lazy materialization 또는 필요한 subset 배치를 사용한다.

### 커스텀 slippage

현재 Rust 코어처럼 built-in slippage만 persistent 경로에서 지원한다. Python callback slippage는 성능
목표를 훼손하므로 별도 설계 전까지 명시적으로 거절한다.

### 상태 이중 소유

마이그레이션 중 가장 큰 위험이다. mutable 주문 잔량, triggered, 그룹 생존 여부, sequence는 각 단계에서
정확히 한 구현만 소유한다. shadow 비교는 동일 입력으로 별도 실행하며 production 경로에서 double-write
하지 않는다.

## 완료 정의

Persistent Rust Engine 구현 완료는 단순히 `PersistentEngine` 클래스가 생기는 것이 아니다.

- Python 전략 API가 변경되지 않는다.
- 전체 EventStore trace가 reference와 동일하다.
- 세션별 open order/Bar 마샬링이 없다.
- 실행 상태가 Rust에 세션 간 유지된다.
- 실데이터에서 Python 대비 최소 1.5배, 목표 2배를 달성한다.
- 기존 Python 코어가 진실 원천이자 fallback으로 남는다.
