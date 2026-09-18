# Persistent Rust Engine 구현 계획

작성일: 2026-09-01
상태: M0~M6 완료 + 2026-09-17 Rust 루프 드라이버(세션당 FFI 0회) 완료
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

## 2026-09-17 Rust 루프 드라이버 체크포인트 (#98, PR #107 → #109 → 벤치 PR)

- 세션 루프(MARKET→FILL→NOTIFY→SESSION_CLOSE→ORDER 드레인, 자본변동, 비용·스냅샷 기록,
  잔여 주문 취소)를 Rust `PersistentEngine::drive()`/`submit_decision()`/`finish()`가 소유한다.
  구현: `rust/backtest_core/src/driver.rs`, `records.rs`, Python `engine/loop.py::_execute_persistent`.
- Python↔Rust 왕복은 전략 콜백(`drive` → `on_event` → `submit_decision`)과 적재·종료 배치에서만
  일어난다. `tests/test_core_parity.py::test_promoted_rust_makes_no_per_session_ffi`가 세션 단위
  호출 0회를 고정한다.
- 큐 payload·레코드 payload는 Rust wire이며 Python `PersistentEventStore`가 `finish()` 배치를
  seq 순서로 lazy materialize한다. 콜백 프레임은 포트폴리오·대기 주문을 콜백 시점에 고정하고
  `RustStrategyContext`가 읽을 때만 공개 객체를 만든다.
- 선언형 tape(`DeclarativeTapeStrategy`, 워크벤치 `TargetTapeStrategy`)는 `load_target_tape`로
  적재돼 Rust `tape.rs`가 결정까지 생성한다 — Python 콜백 0회. 규칙 정본은
  `engine/tape.py::evaluate_tape`.
- 측정 (`scripts/bench_universe.py --warmup 1 --repeat 5`, 2026-09-17, 동일 프로세스 교차 실행,
  측정 중 다른 프로세스가 CPU 85~97%를 점유해 절대값은 부풀려짐 — 배수는 같은 실행 안의 비교):

| 워크로드 | python | rust_legacy | rust | rust / python |
|---|---|---|---|---|
| 100종목 synthetic · callback | 2.876초 | 2.870초 | 0.681초 | **4.22배** |
| 100종목 synthetic · tape | 2.566초 | 2.662초 | 0.543초 | **4.73배** |
| 300종목 synthetic · callback | 7.378초 | 8.180초 | 2.080초 | **3.55배** |
| 300종목 synthetic · tape | 7.620초 | 7.767초 | 1.703초 | **4.47배** |
| 실제 4종목 fixture · callback | 0.215초 | 0.223초 | 0.069초 | **3.13배** |
| 실제 4종목 fixture · tape | 0.192초 | 0.242초 | 0.037초 | **5.18배** |

- **위 표의 배수는 측정 경계가 틀렸다 (DEFECT-301).** 타이머가 `engine.run()`만 감싸 lazy한
  Rust 결과의 조회 비용이 빠졌다. 정직한 경계로 다시 잰 수치는 아래 "2026-09-18 측정 경계 교정"이
  정본이며, 이 표는 무엇이 어떻게 틀렸는지를 남기기 위해 기록으로 보존한다.
- 원본 산출물: `benchmarks/baseline/rust-loop-100-callback.json`, `rust-loop-100-tape.json`,
  `rust-loop-300-callback.json`, `rust-loop-300-tape.json`, `rust-loop-real-fixture-callback.json`,
  `rust-loop-real-fixture-tape.json`. tape 표(`EqualWeightTape`) 생성은 타이머 밖이다 — 워크벤치에서도
  tape는 상류(`compile_target_tape`)에서 만들어 엔진에 넘기므로 `engine.run()` 비용만 잰다.
- orders/fills/최종 equity는 워크로드마다 세 코어가 동일. 전체 스위트 1192 passed(리뷰 반영 후), parity 201 passed,
  Rust 단위 테스트 22 passed.
- 격리 프로세스 peak RSS (100종목, `--core` 단독 실행, `benchmarks/baseline/rust-loop-memory-100-*.json`):
  callback python 169.8 MiB / rust 209.0 MiB = 1.23배(게이트 1.25배 **통과**), tape python 170.4 MiB /
  rust 216.7 MiB = 1.27배(**근접 미달**). 첫 측정은 1.46/1.55배였는데 종료 배치가 Rust wire를 Python
  tuple로 한 번에 복제하는 구간이 원인이라, 레코드 인덱스만 넘기고 payload는 `record_payload(seq)`로
  필요할 때 읽도록 바꾸고 종료 시 큐 arena·tape 프레임을 해제했다.
- 최종 게이트 판정 **(2026-09-18 측정 경계 교정으로 무효 — 정본은 아래 "2026-09-18 최종 판정
  (리뷰 후속 PR 1~11)" 절이다)**: 세션당 FFI 0회 **통과**. 100종목 callback 4.22배(목표 2배 **통과**),
  tape 경로 4.73배(최소 3배 **통과**, 목표 5배는 근접 미달). 300종목 callback 3.55배·tape 4.47배(목표 2배 **통과**).
  4종목 fixture 회귀 없음(3.13~5.18배 향상).
- 남은 Python 시간: feed 적재(열 comprehension), 전략 콜백 본체와 `decision_to_wire`, 결과 조회 시
  `record_payload(seq)` FFI(레코드당 1회, 100종목이면 수만 회 — RSS와 맞바꾼 선택)와 스냅샷 materialization. 다음 병목은 워크벤치의 결과 변환·분석 지표(엔진 밖).

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

- 현재 단계: #98 리뷰 후속 스택 — PR 1~10 생성 완료(#123 #124 #127 #128 #129 #134 #136 #137 #138 #141),
  PR 11(`docs/rust-loop-final-gates`)에서 최종 재측정·문서 정리
- 현재 작업: 최종 게이트 판정 문서화와 이슈 #98 종료 댓글 초안
  (`docs/superpowers/plans/2026-09-18-issue-98-closing-comment.md`)
- 마지막 완료 항목: 전 워크로드 재측정 (위 "2026-09-18 최종 판정" 절). 게이트 7개 중 6개 통과,
  TargetTape 엔진 경계 최소 3배만 미달(2.41배)
- 다음 작업: 이슈 #98 종료 여부 결정. Python 코어 삭제 범위는 이슈 원문 "결정 필요" 2번
  (#98 Phase 3-4) — 기본안은 parity oracle을 위해 "테스트 전용 reference 유지"
- 알려진 blocker: 없음
- 작업 트리의 기존 사용자/선행 변경: 없음
- 마지막 검증:
  - 2026-09-18 PR 11 tip: `uv run ruff check src tests scripts` — passed,
    `uv run pyright` — 0 errors, `uv run pytest -q` — 1,328 passed / 13 skipped
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

M6 측정 판정 (2026-09-03):

- 100종목 최소 1.5배: **통과(1.719배)**. 목표 2배는 미달.
- 300종목 목표 2배: **미달(1.618배)**.
- 작은 4종목 fixture 회귀 금지: **통과(1.364배 향상)**.
- 결과 패리티: **통과**. 세 코어의 equity/orders/fills와 differential trace가 동일하다.
- 세션당 FFI 0회: **미달**. persistent state 재마샬링은 제거했지만 Python event loop에서
  session market/close와 queue 어댑터를 호출한다.

2026-09-17 재판정 (Rust 루프 드라이버, 위 체크포인트 표):

- 100종목 목표 2배: **통과(callback 4.22배, tape 4.73배)**.
- 300종목 목표 2배: **통과(callback 3.55배, tape 4.47배)**.
- 4종목 fixture 회귀 금지: **통과(3.13~5.18배 향상)**.
- 세션당 FFI 0회: **통과**. 왕복은 전략 콜백과 적재·종료 배치뿐이다.
- Peak RSS 1.25배 이하: callback **통과(1.23배)**, tape **근접 미달(1.27배)**.

2026-09-18 측정 경계 교정 (DEFECT-301):

`scripts/bench_universe.py`의 타이머가 `engine.run()`만 감쌌고 `result.snapshots`·`orders`·
`fills` 조회는 밖에 있었다. Rust 코어의 `BacktestResult`는 lazy라 결과를 읽는 시점에 공개 객체를
만들고 Python 코어는 `run()` 안에서 이미 만들므로, 위 2026-09-17 표는 Rust 쪽 비용만 빼고 잰
값이다. 아래는 조회를 타이머 안에 넣어 두 코어를 같은 경계로 다시 잰 수치다 (2026-09-18,
`--warmup 1 --repeat 5`, Rust `--release`, 측정 중 CPU 부하 14~18%).

| 워크로드 | python total | rust total | `run()`만 배수 | `run()`+조회 배수 |
|---|---|---|---|---|
| 100종목 synthetic · callback | 1.700초 | 0.787초 | 4.91배 | **2.16배** |
| 100종목 synthetic · tape | 1.654초 | 0.811초 | 4.94배 | **2.04배** |
| 300종목 synthetic · callback | 4.178초 | 2.164초 | 3.81배 | **1.93배** |
| 300종목 synthetic · tape | 4.101초 | 2.256초 | 3.94배 | **1.82배** |
| 실제 4종목 fixture · callback | 0.110초 | 0.051초 | 3.71배 | **2.14배** |
| 실제 4종목 fixture · tape | 0.107초 | 0.039초 | 5.93배 | **2.77배** |

Rust `total`의 절반 안팎이 결과 조회다 (100종목 callback 0.346초 + 0.440초, 300종목 callback
1.097초 + 1.074초). Python 코어의 조회 구간은 모든 워크로드에서 중앙값 0.2~0.9µs, 최대 표본
2.1µs다 — 이미 만들어진 튜플을 꺼내는 속성 접근 비용뿐이다.

| 코어 격리 Peak RSS (100종목, `--core <one>` 단독 실행) | python | rust | 배수 |
|---|---|---|---|
| callback | 169.6 MiB | 209.4 MiB | **1.23배** |
| tape | 170.3 MiB | 217.9 MiB | **1.28배** |

`--core all`은 세 코어가 한 프로세스를 공유해 같은 peak를 받으므로 RSS 정본이 아니다. JSON의
`workload.rss_isolated`가 그 구분을 들고 있다.

워크벤치 e2e (`scripts/bench_workbench_adapter.py --instruments 100 --repeat 3`,
`benchmarks/baseline/rust-loop-workbench-100.json`, HTTP 요청과 같은 모양의 out-of-sample
metric window 1개 포함): 어댑터 전체 python 3.160초 → rust 2.613초로 **1.21배**. Rust에서
`engine.run` 0.407초 뒤에 오는 구간이 1.228초로 `engine.run`의 **3.02배**이며, 그 안에서 결과
조회 0.584초와 raw artifact 변환 0.556초가 지배적이다.

`engine.run` 앞의 dataset→엔진 입력 변환(python 0.528초 / rust 0.776초)과 전략·피드 조립
(python 0.175초 / rust 0.176초)은 코어와 무관한 같은 Python 코드다. 회차 사이에
`gc.collect()`를 넣자 전략·피드 조립의 코어 간 차이는 사라졌고, 남은 dataset 변환 쪽 차이는
할당기 상태 차이로 읽어야 한다. 어느 쪽이든 Rust로 줄일 수 없는 구간이다.

게이트 재판정 (정직한 경계 기준):

- 100종목 목표 2배: **통과(callback 2.16배, tape 2.04배)**. 2026-09-17 기록의 4.22/4.73배는 무효.
- 300종목 목표 2배: **미달(callback 1.93배, tape 1.82배)**. 2026-09-17 기록의 3.55/4.47배는 무효.
- TargetTape 경로 최소 3배 / 목표 5배 (이슈 #98 원문 게이트): **미달(2.04배)**. 최소선도
  넘지 못한다. 2026-09-17 기록의 4.73배는 조회 비용이 빠진 값이다.
- 4종목 fixture 회귀 금지: **통과(2.14~2.77배 향상)**.
- 세션당 FFI 0회: **통과**. 측정 경계와 무관하며 판정이 바뀌지 않는다.
- Peak RSS 1.25배 이하: callback **통과(1.23배)**, tape **미달(1.28배)**.

이 절은 성능 개선이 아니라 측정 교정이다. 코드 동작은 그대로이고 배수만 정직해졌다. 후속
PR(materialize 배치, 워크벤치 columnar 변환, hot loop, 메모리)이 끝난 뒤 같은 경계로 다시 재서
게이트를 재판정한다.

2026-09-18 결과 조회 배치화 후 재측정 (PR 6):

`PersistentEventStore`가 kind마다 레코드 인덱스 전체를 재스캔하고 payload를 `record_payload(seq)`로
한 건씩 읽던 경로를 `drain_payloads(kind, 512)` 청크 조회로 바꿨다. Rust는 청크를 넘기면서 그
자리를 바로 해제하므로 wire tuple·공개 객체·Rust payload가 함께 사는 구간이 청크 크기로 묶인다.

| 코어 격리 Peak RSS (100종목, `--core <one>` 단독, `--warmup 1 --repeat 5`) | python | rust | 배수 |
|---|---|---|---|
| callback | 170.4 MiB | 201.6 MiB | **1.18배** (직전 1.23배) |
| tape | 171.2 MiB | 209.1 MiB | **1.22배** (직전 1.28배) |

결과 조회 시간은 같은 프로세스에서 두 구현을 번갈아 돌린 A/B(각 24 표본, 최솟값 기준)로 쟀다.
tape 0.4207초 → 0.3687초(**−12.4%**), callback 0.4111초 → 0.3687초(**−10.3%**). 프로세스마다
1회씩만 도는 측정(6 프로세스)에서는 tape 0.4133초 → 0.3548초(**−14.2%**)이고 `run()`은
0.3559초 → 0.3535초로 변화가 없다.

한 프로세스 안에서 `--repeat`으로 반복하면 `run()`이 3~7% 느려 보이는데, 이는 직전 회차의
조회가 실제로 메모리를 반납해 다음 회차가 페이지를 다시 폴트하기 때문이다. 프로세스마다
1회만 도는 측정에서 사라지므로 엔진 회귀가 아니라 벤치 하네스의 회차 간 간섭이다.

워크벤치 e2e(`--instruments 100 --core all --repeat 1`)의 `result_materialize`는 0.5755초 →
0.5092초(−11.5%), `event_store_costs`는 0.0026초 → 0.0009초다. 두 코어의 metrics·series는
그대로 일치한다.

목표였던 조회 시간 −30%에는 못 미친다. cProfile로 보면 FFI는 조회 시간의 3% 수준(청크 조회
0.028초/90회)이고, 스냅샷 1,225개가 만드는 `Position` 123,625개가 조회의 약 65%를 차지한다.
남은 비용은 Python 객체 생성이라 FFI 경계를 더 손봐도 줄지 않는다. Peak RSS 쪽도 payload 힙은
돌려주지만 `Vec<NativeRecord>`의 인라인 슬롯은 남아 있어, 다음 개선은 레코드 메모리 레이아웃이다.

2026-09-18 최종 판정 (리뷰 후속 PR 1~11):

리뷰 후속 스택 PR 1~10(#123 #124 #127 #128 #129 #134 #136 #137 #138 #141)을 전부 쌓은 tip에서
"벤치 측정 표준"의 전 워크로드를 다시 쟀다 (`--warmup 1 --repeat 5`, Rust `--release`, 실행
직전 CPU 부하 15~39%). 산출물은 `benchmarks/baseline/rust-loop-*.json` 16개다.

| 이슈 #98 게이트 | 최소 | 목표 | PR 1 기준선 | 최종 | 판정 |
|---|---|---|---|---|---|
| 100종목 TargetTape 경로 | 3배 | 5배 | 2.04배 | 엔진 2.41배 / 워크벤치 e2e 3.23배 | 엔진 경계 **미달**, 워크벤치 경계 최소선 통과 |
| 100종목 Python 전략 경로 | 2배 | 3배 | 2.16배 | 2.48배 | 최소 **통과**, 목표 미달 |
| 300종목 | 2배 | 3배 | callback 1.93배 · tape 1.82배 | callback 2.35배 · tape 2.43배 | 최소 **통과**, 목표 미달 |
| 실제 4종목 fixture | 회귀 10% 이내 | 현행 이상 | callback 2.14배 · tape 2.77배 | callback 2.41배 · tape 2.59배 | **통과** |
| 세션당 FFI | 0회 | — | 0회 | 0회 | **통과** |
| Peak RSS | Python 대비 1.25배 이하 | — | callback 1.23배 · tape 1.28배 | callback 1.15배 · tape 1.19배 | **통과** |
| 결과 패리티 | 100% | — | 통과 | 통과 | **통과** |

엔진 벤치 상세 (`scripts/bench_universe.py`, 중앙값, `speedup`은 `run()` + 결과 조회 합):

| 워크로드 | python total | rust run | rust 조회 | rust total | 배수 | `run()`만 배수 |
|---|---|---|---|---|---|---|
| 100종목 synthetic · callback | 1.696초 | 0.268초 | 0.407초 | 0.685초 | **2.48배** | 6.32배 |
| 100종목 synthetic · tape | 1.650초 | 0.241초 | 0.430초 | 0.685초 | **2.41배** | 6.83배 |
| 300종목 synthetic · callback | 4.723초 | 0.837초 | 1.190초 | 2.012초 | **2.35배** | 5.64배 |
| 300종목 synthetic · tape | 4.471초 | 0.721초 | 1.106초 | 1.841초 | **2.43배** | 6.20배 |
| 실제 4종목 fixture · callback | 0.115초 | 0.030초 | 0.016초 | 0.048초 | **2.41배** | 3.84배 |
| 실제 4종목 fixture · tape | 0.122초 | 0.030초 | 0.017초 | 0.047초 | **2.59배** | 4.09배 |

세 코어의 orders / fills / 최종 equity는 워크로드마다 동일하다 (100종목 22,243 / 22,155 /
2,321,985,874, 300종목 52,344 / 52,121 / 2,402,131,747, 4종목 982 / 978 / 2,739,581,588).
각 열은 독립된 중앙값이라 `run` + `조회`가 `total`과 정확히 맞아떨어지지는 않는다 — 회차마다
어느 구간이 느렸는지가 다르기 때문이며, 배수 계산의 정본은 `total` 열이다.

**배수의 상한은 결과 조회다.** Rust `total`의 59~63%(4종목 fixture는 34~37%)가 `run()` 뒤의 결과 조회이고 그 대부분은
Python 공개 객체 생성이다(스냅샷 1,225개 × 종목 수만큼의 `Position`). `run()`만 보면 5.6~6.8배로
이슈가 예상한 "5배 이상"에 들어간다. 조회를 더 줄이려면 FFI가 아니라 공개 객체 계약 자체를
건드려야 하고, 그것은 `BacktestResult` 불변 계약 밖이다.

워크벤치 e2e (`scripts/bench_workbench_adapter.py --instruments 100 --repeat 5`, 중앙값):

| 구간 | python | rust |
|---|---|---|
| `dataset_to_engine_inputs` | 0.0002초 | 0.0003초 |
| `strategy_and_feed_build` | 0.1437초 | 0.1373초 |
| `engine.run` | 2.3242초 | 0.2238초 |
| `result_tables` | 0.1974초 | 0.0298초 |
| `artifacts` | 0.5549초 | 0.4522초 |
| `analysis_points` | 0.0013초 | 0.0015초 |
| `compute_analytics` | 0.0163초 | 0.0182초 |
| `manifest` | 0.0645초 | 0.0325초 |
| **e2e total** | **3.332초** | **0.911초** |

코어 격리 실행 기준 **3.66배**이고, 같은 프로세스에서 두 코어를 번갈아 돌린 `--core all`
실행에서는 3.223초 / 0.998초로 **3.23배**다. PR 1 기준선 1.21배에서 올라왔다. 구간별 수치의
정본은 격리 실행이다 — `--core all`에서는 자동 순환 GC가 rust `compute_analytics`에 붙어
0.018초가 0.168초로 찍힌다. 벤치 스크립트에 `gc.freeze()`는 넣지 않았다(넣으면 기존 JSON
전체와 비교가 끊긴다). 어댑터 격리 실행 peak RSS는 python 318.6MiB / rust 329.4MiB로 1.03배다.

| 코어 격리 Peak RSS (100종목, `--core <one>` 단독 실행) | python | rust | 배수 |
|---|---|---|---|
| callback | 169.8MiB | 194.8MiB | **1.15배** (PR 1 기준선 1.23배) |
| tape | 170.2MiB | 202.1MiB | **1.19배** (PR 1 기준선 1.28배) |

**측정 도구의 한계 (메모리 항목을 이 지표 하나로 판정하면 안 된다).**
`scripts/bench_universe.py::peak_rss_bytes()`는 Windows `PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize`
이므로 run 도중 잠깐 커밋됐다 풀리는 버퍼를 잡지 못한다. PR 9의 큐 arena가 그 예다 — 300종목
기준 상주가 30.4MiB에서 약 20KiB로 줄었는데 working set peak은 움직이지 않았고
`PeakPagefileUsage`(peak commit)로만 −5.16MiB가 보였다. 이 실행의 working set peak은 큐가 가장
큰 순간이 아니라 결과 조회 구간에서 정해지기 때문이다. 어떤 변경이 peak working set을 안
움직였다는 사실은 "메모리를 안 줄였다"는 뜻이 아니다.

희소 유니버스 (`--density`, PR 11에서 추가):

벤치의 synthetic 유니버스는 밀도 100%라 PR 9가 넣은 `RowIndex::Sparse` 경로가 한 번도 돌지
않았다. `--density`가 종목마다 길이 `round(sessions × density)`의 연속 상장 구간만 남겨 격자를
비운다. Rust는 어느 표현을 골랐는지 노출하지 않으므로 벤치가 같은 식(`slots × 4B ≤ rows × 20B`)을
재현해 JSON `workload.row_index_expected`에 남긴다.

| 워크로드 (300종목 tape) | 밀도 | 행 조회표 | bars | python total | rust run | rust total | 배수 |
|---|---|---|---|---|---|---|---|
| `--density 0.15` | 15.03% | Sparse | 55,500 | 1.049초 | 0.200초 | 0.433초 | 2.42배 |
| `--density 0.2` | 19.98% | Sparse | 73,800 | 1.368초 | 0.245초 | 0.581초 | 2.36배 |
| `--density 0.2005` | 20.06% | Dense | 74,100 | 1.409초 | 0.227초 | 0.532초 | 2.65배 |

뒤 두 줄이 `row_at` 해시 비용의 A/B다. 상장 구간 246 / 247 세션 차이라 bar 수가 0.4%밖에 안
다른데 표현만 Sparse / Dense로 갈린다. **Sparse가 rust `run()`에서 8.1% 느리다**(bar 수로
정규화하면 8.5%). 조회표 자체 크기는 이 경계에서 양쪽 모두 약 1.4MiB로 같고, Sparse가 값을
하는 구간은 밀도가 더 낮은 누적 유니버스다 (문서 예: 3,000종목 × 5,000세션에서 Dense는
60MiB 고정, Sparse는 실제 행 수에만 비례). 세 코어 결과 signature는 희소 실행에서도 동일하다.

희소 워크로드의 성격도 남긴다: 밀도 15%에서 주문 23,315건 중 체결은 1,991건뿐이다. `REPLACE`
목표가 상폐된 보유 종목을 매도하려 하는데 그 세션에 bar가 없어 day 주문이 만료되기 때문이다.
밀도 100% 워크로드(주문 52,344 / 체결 52,121)와 체결 비중이 다르므로 두 줄을 서로 빼서
읽으면 안 된다 — Sparse / Dense 비교는 위 A/B 짝 안에서만 유효하다.

남은 항목:

- TargetTape 엔진 경계 최소 3배. 현재 2.41배이고 남은 거리는 결과 조회(공개 객체 생성)에 있다.
- 두 게이트의 "목표"(100종목 3배·5배, 300종목 3배)는 미달이며 같은 이유다.
- 코어 간 instrument key 충돌 거부가 persistent에만 있다 (python 코어는 완주한다).
- 라우터 `instrument_not_snapshot` 메시지의 `available` 목록이 `HashMap` 순서(비결정)이고
  python 쪽(`types/market.py::MarketSnapshot.bar`)은 feed 순서 + `ts=` 접두를 담는다. byte
  동일이 아니고 이를 고정하는 테스트도 없다.
- `BuyingPower`가 `#[pyclass]`라 수명을 못 갖고 `HashMap<String, _>` 둘을 key마다 채운다.
- PR 8에서 되돌린 tape 경량 프레임 — 알림(fill·order_update) 선언 tape 워크로드를 재는 벤치
  옵션이 생기면 다시 올린다.
- `engine/store.py`가 `engine/tape.py::no_bar_reason`을 import한다. 사유 포맷의 정본을 한 곳으로
  모은 결과지만 store → tape 방향이 생겼다.

후속 백로그:

- [x] Rust가 다음 전략 callback까지 market, fill, update, close와 queue drain을 진행하는 driver API.
- [x] callback frame의 portfolio/open-order view를 콜백 시점 wire로 고정하고 lazy 변환.
- [ ] 서로 다른 실제 종목 100/300개를 포함한 외부 원장으로 성능 게이트 재검증.
- [x] 워크벤치 end-to-end 구간별 측정 (#98 Phase 3-2). `scripts/bench_workbench_adapter.py`.
- [x] 종료 배치를 레코드 단위 lazy payload 조회로 (callback 1.23배). tape 1.27배는 후속.
- [x] 결과 조회를 kind 청크 FFI로 바꾸고 넘겨받은 payload를 즉시 해제 (2026-09-18, 아래 절).
  callback 1.18배 · tape 1.22배로 둘 다 게이트 통과. materialize 시간은 −10~−14%에 그친다.
- [ ] 레코드 payload 메모리 레이아웃 (큰 variant를 `Box`로). 해제해도 `Vec<NativeRecord>`의
  인라인 슬롯(레코드당 약 200 B, 70k 레코드에서 약 14 MB)은 남는다.
- [ ] 결과 조회 시간의 정본은 Python 공개 객체 생성이다. 100종목 스냅샷 1,225개가 종목마다
  `Position`을 만들어 123,625개가 되고 그것만으로 조회의 약 65%다. 조회 시간을 더 줄이려면
  FFI가 아니라 이 객체 수를 건드려야 한다 (두 코어가 같이 무는 비용이라 배수는 안 움직인다).

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
