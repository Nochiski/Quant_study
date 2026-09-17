# Rust 실행 루프 이전 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `core="rust"` 실행 루프(MARKET → FILL → NOTIFY → SESSION_CLOSE → ORDER 드레인)를 Rust `PersistentEngine`이 처음부터 끝까지 소유하게 하고, 워크벤치 TargetTape 전략은 Python 콜백 0회로 완주시킨다. 이슈 #98.

**Architecture:** Rust runtime이 큐 payload·레코드 payload·자본변동 일정·실행 설정을 모두 소유하고, `drive()`가 다음 전략 콜백까지 세션을 진행한 뒤 `CallbackFrame`(이벤트 payload + 그 시점 포트폴리오/대기주문 스냅샷)을 돌려준다. Python은 `while frame := runtime.drive(): decision = strategy.on_event(...); runtime.submit_decision(token, wire)` 루프와 `finish()` 배치의 lazy materialization만 담당한다. 선언형 tape 전략은 Rust가 결정까지 생성해 콜백이 없다.

**Tech Stack:** Rust 2021 + pyo3 0.23 (`backend/rust/backtest_core`), Python 3.11 (`backend/src/backtest_engine`), pytest, maturin.

**불변 계약 (성능보다 우선):** EventStore trace(`seq, ts, kind, payload`)·Decision/Order/Fill/Group ID·float 연산 순서·오류 타입과 메시지 접두어가 `core="python"`과 byte 동일. `BacktestEngine.run()` 서명과 Python 전략 API 불변. `core="python"`, `core="rust_legacy"` 경로는 손대지 않는다.

**작업 순서 근거:** 이슈 #98은 Phase 1(TargetTape)을 먼저 적었지만, 콜백 0회 완주는 Rust가 세션 루프 전체를 돌릴 수 있어야 가능하므로 드라이버(이슈 Phase 2-1·2-2·2-4)를 PR A로 먼저 만들고, 그 위에 tape 네이티브 결정(Phase 1)을 PR B로 쌓는다. 벤치·문서는 PR C.

---

## 현재 병목 (2026-09-09 실측, 100종목·1,231세션)

| 항목 | 시간 | 위치 |
|---|---|---|
| Rust 본체 | 0.26초 | `process_market_index`·`submit_decision`·`close_current_session` |
| 세션마다 `PortfolioSnapshot` 재조립 | 0.59초 | `engine/core.py:385-410` |
| 라우팅 결과 `CompactOrder` 복원 | 0.26초 | `engine/wire.py:414-470` |
| Python 큐/레코드 래퍼 왕복 | 0.35초 | `engine/queue.py:118`, `engine/store.py:217` |
| feed 적재 | 0.28초 | `engine/loop.py:457` |

Python 1.87초 / rust 1.08초 (1.73배). 목표: 세션당 FFI 0회, TargetTape 경로 Python 대비 3배 이상.

---

## 파일 구조

### Rust (`backend/rust/backtest_core/src/`)

| 파일 | 책임 | 변경 |
|---|---|---|
| `driver.rs` | **신규.** 세션 루프: `drive()`, `on_market`, `on_session_close`, 큐 payload enum, 실행 설정, 자본변동 일정, 종료 시 잔여 주문 취소 | 생성 |
| `records.rs` | **신규.** Rust-native 레코드(`NativeRecord`, `RecordPayload`)와 Python tuple 변환. `compact_store.rs`를 대체 | 생성 |
| `persistent.rs` | `PersistentEngine` 필드 `pub(crate)` 공개, `StoredOrder`에 `decision_id/action_index/leg_index` 추가, pymethods에 `configure_run`·`load_corporate_actions`·`drive`·`finish`·`record_batch`·`equity_series`·`traded_notional` 추가, `submit_decision` 반환형 변경, `queue_*`·`record_append/extend` 제거 | 수정 |
| `callback.rs` | `CallbackFrame`에 `event`, `snapshot`, `open_orders` 필드 추가 | 수정 |
| `feed.rs` | `instrument_id(key)` 조회, `open_at(session, key)`, `has_bar(session, key)` 추가 | 수정 |
| `session.rs` | `py_float`, `py_list`를 `pub(crate)`로, `py_tuple` 추가 | 수정 |
| `event_queue.rs` | 변경 없음 (token = payload index) | — |
| `compact_store.rs` | 삭제 (records.rs로 대체) | 삭제 |
| `tape.rs` | **신규 (PR B).** 선언형 tape: `load_target_tape`, 세션별 결정 생성, no-bar 규칙 | 생성 |
| `lib.rs` | 모듈 등록 | 수정 |

### Python (`backend/src/backtest_engine/`)

| 파일 | 책임 | 변경 |
|---|---|---|
| `engine/loop.py` | persistent 경로를 `_execute_persistent()`로 분리: feed/설정/자본변동 적재 → drive 루프 → finish. Compact* match 분기 제거 | 수정 |
| `engine/store.py` | `PersistentEventStore` 재작성: Rust 배치 → 공개 이벤트 lazy materialization. side table은 DECISION(Python 결정 객체)·CORPORATE_ACTION(입력 객체)·MARKET(feed snapshot)만 | 수정 |
| `engine/context.py` | `RustStrategyContext`를 frame 기반 독립 클래스로: snapshot/open_orders를 frame wire에서 cached_property로 생성 | 수정 |
| `engine/core.py` | `PersistentPortfolio`·`PersistentOrderManager`에서 루프용 메서드 제거(레거시 `rust_legacy`는 Python `OrderManager`를 씀). `_snapshot_from_wire`는 store로 이동 | 수정 |
| `engine/wire.py` | `decision_to_wire`, `_route_error`, `supports_basic_decision`만 남김. `submit_basic_decision`, `route_basic_decision`, `CompactRoutingResult` 제거 | 수정 |
| `engine/queue.py` | `PersistentEventQueue`, `CompactFillOccurred`, `CompactOrderPlaced` 제거 | 수정 |
| `engine/compact.py` | 삭제 | 삭제 |
| `types/instruments.py` | `InstrumentId` 해시 캐시 | 수정 |
| `types/results.py` | `BacktestResult.lazy`가 snapshots loader도 받도록 | 수정 |
| `types/tape.py` | **신규 (PR B).** `TapeFrame`, `DeclarativeTapeStrategy` 프로토콜 | 생성 |
| `engine/tape.py` | **신규 (PR B).** Python reference `evaluate_tape()` (Rust와 같은 규칙) | 생성 |

### 워크벤치·테스트·벤치

| 파일 | 변경 |
|---|---|
| `src/strategy_workbench/adapters/outbound/backtest_engine/_adapter.py` | (PR B) `TargetTapeStrategy`가 `DeclarativeTapeStrategy` 구현, `on_event`는 `evaluate_tape()` 위임 |
| `tests/test_core_parity.py` | FFI 계측 테스트를 새 계약으로 갱신, 큐/compact 테스트 교체, tape 네이티브 parity 추가 |
| `tests/test_rust_driver.py` | **신규.** drive/finish 단위 계약(빈 feed, warmup, notify, CA, EquityWipedOut 매핑) |
| `scripts/bench_universe.py` | (PR C) `--strategy {callback,tape}` 옵션 |
| `benchmarks/baseline/rust-loop-*.json` | (PR C) 결과 |
| `docs/superpowers/specs/2026-09-01-persistent-rust-engine-implementation.md` | (PR C) 판정 갱신 |

---

## Wire 계약

레코드 kind 코드는 Python `RecordKind` enum 순서와 같다: MARKET=0, DECISION=1, ORDER=2, ORDER_UPDATE=3, FILL=4, SNAPSHOT=5, CORPORATE_ACTION=6, CORPORATE_ACTION_APPLIED=7, COST=8.

```text
RecordWire      = (seq: int, session_index: int, kind: int, payload)
payload by kind:
  MARKET                   None
  DECISION                 (decision_id: str, native: NativeDecision | None)      # PR A: native는 항상 None
  ORDER                    OrderWire
  ORDER_UPDATE             (order_id, status: str, detail: str | None)
  FILL                     FillWire
  SNAPSHOT                 (cash, [(instrument_id, qty, avg, mkt, mv, upnl), ...], equity, gross)
  CORPORATE_ACTION         ca_index: int
  CORPORATE_ACTION_APPLIED (ca_index, old_qty, new_qty, old_avg, new_avg, cash_paid)
  COST                     (kind: str, instrument_id: int | None, amount)

OrderWire = (order_id, decision_id, instrument_id, quantity, side, order_type,
             limit_text | None, stop_text | None, tif, group_id | None, action_index, leg_index | None)
FillWire  = (fill_id, order_id, instrument_id, quantity, side, price, fee, slippage_per_share)

CallbackFrame:
  token: int, event_kind: "market"|"fill"|"order_update"|"corporate_action",
  session_index: int, ts: str,
  event: None | FillWire | (order_id, status, detail) | ca_index,
  snapshot: SNAPSHOT payload (콜백 시점 고정),
  open_orders: [(OrderWire, remaining), ...]  (콜백 시점 고정)

configure_run(fee_rate, default_participation: str|None, slippage: (str, f64, f64),
              schedule: "every_session"|"month_end", short_borrow_bps_annual, margin_interest_bps_annual,
              annualization_days, warmup_sessions, notify_fill, notify_order_update, notify_corporate_action)
load_corporate_actions([(session_index, key, symbol, action_type, ratio_text, event_ts_text, confirmed)])
drive() -> CallbackFrame | None
submit_decision(token, DecisionWire) -> (decision_id, RouteError | None)
finish() -> [RecordWire]          # 잔여 주문 취소 레코드 기록 후 배치 반환
record_batch() -> [RecordWire]    # 미종료 상태(전략 예외)에서도 partial trace 조회
equity_series() -> [f64]          # SNAPSHOT 레코드 순서
traded_notional() -> f64          # FILL 레코드 순서로 qty*price 누산
```

오류 매핑 (Rust `ValueError` 접두어 → Python 예외): `equity_wiped_out: ` → `EquityWipedOut`, `negative_position: ` → `NegativePositionError`, `negative_cash: ` → `NegativeCashError`. Route 오류는 기존 `_route_error` 코드 표.

---

## 드라이버 알고리즘 (Python `loop.py` 현재 동작을 그대로 옮긴다)

```text
drive():
  Ready → Running, 모든 세션 i에 Market(i) push (ts=i, priority MARKET)
  loop:
    queue 비면 → return None
    pop:
      Market(i):
        record MARKET(i)
        activate_pending
        for ca in ca_by_session[i] (입력 순서):
          record CORPORATE_ACTION(ca)
          remaining_by_id ← 현재 open orders
          if ca.confirmed:
            for order_id in cancel_for_key(ca.key):
              record ORDER_UPDATE(order_id, "cancelled",
                "cancelled by corporate action — instrument={symbol} action={action_type} ratio={ratio}
                 event_ts={event_ts} settled_at={session_ts} remaining={remaining}")
              if notify_order_update: push Notify(OrderUpdate) at ts=i
            applied ← apply_corporate_action_ratio(key, ratio, open_at(i, key))
            if applied: record CORPORATE_ACTION_APPLIED
          if notify_corporate_action: push Notify(CorporateAction(ca)) at ts=i
        ops ← process_market_index(i)     # 여기서 feed.current = i
        for op in ops (순서 유지):
          fill   → push Fill(FillWire) at ts=i; if notify_fill: push Notify(Fill(FillWire)) at ts=i
          update → record ORDER_UPDATE(status, detail or None); if notify_order_update: push Notify
          trigger/remove/drop_group → 이미 적용됨
        push SessionClose(i) at ts=i
      Fill(w):        record FILL(w)
      Notify(ev):     if session_count < warmup → continue
                      else → frame(kind, ev, snapshot=portfolio.snapshot(), open_orders) ; Awaiting(token, session=i) ; return frame
      SessionClose(i):
        (dispatch, costs, snap) ← close_current_session(...)
        for cost: record COST
        if snap.equity < 0 → Err("equity_wiped_out: equity fell below zero at session close — ts=… equity=… cash=… positions=[('sym', 'qty'), …]")
        record SNAPSHOT(snap)
        if dispatch and session_count >= warmup → frame("market", None, snapshot=snap) ; return
      Order(w):       record ORDER(w)

submit_decision(token, wire):
  token 검증 (기존)
  decision_id ← next 'D'
  record DECISION(decision_id, None)        # 오류여도 기록 (Python 순서와 동일)
  route_basic_decision → (orders, updates, groups, error)
  if error: lifecycle Failed; return (decision_id, error)
  for u in updates: record ORDER_UPDATE; if notify_order_update: push Notify at ts=awaiting_session
  for o in orders: push Order(OrderWire) at ts=awaiting_session, priority ORDER
  lifecycle Running; return (decision_id, None)

finish():
  finish_callbacks() 검증
  last ← sessions.len()-1
  for (order, remaining) in drain_orders() (open 뒤 pending 순):
    reason ← tif=="gtc" ? "run ended with GTC order still open — " : "{tif} order expired at last session — "
    record ORDER_UPDATE(order_id, "cancelled", reason + "instrument={symbol} remaining={remaining}") at last
  store.finish(); return batch
```

Python `_execute_persistent`:

```python
self._load_persistent_feed(run, feed)            # 기존
runtime.configure_run(...)                       # RunConfig + requirements에서
runtime.load_corporate_actions(rows)             # settlement index는 기존 코드로 계산
while (frame := runtime.drive()) is not None:
    event = store.frame_event(frame)             # MarketSnapshot | FillEvent | OrderUpdateEvent | CorporateActionEvent
    context = RustStrategyContext(now=ts, frame=frame, store=store, history_store=..., declared=..., universe_source=...)
    try:
        decision = run.strategy.on_event(context, event)
    except BaseException as error:
        runtime.fail_callback(frame.token, f"{type(error).__name__}: {error}"); raise
    if not supports_basic_decision(decision):
        runtime.fail_callback(frame.token, ...); raise RuntimeError(...)
    decision_id, error = runtime.submit_decision(frame.token, decision_to_wire(decision))
    store.record_callback(event, decision_id, decision)
    store.register_decision(decision_id, decision)
    if error is not None: raise _route_error(error)
store.finish()                                    # runtime.finish()
snapshots = store.snapshots()
return BacktestResult.lazy(run_id, snapshots=store.snapshots, orders=store.orders, fills=store.fills,
                           metrics=compute_metrics_from_values(runtime.equity_series(), runtime.traded_notional(), days))
```

---

## PR A — Rust 드라이버 (브랜치 `feat/rust-loop-driver`, base `main`)

### Task A1: InstrumentId 해시 캐시

**Files:**
- Modify: `backend/src/backtest_engine/types/instruments.py`
- Test: `backend/tests/test_types.py`

- [x] **Step 1: 실패 테스트**

```python
def test_instrument_id_hash_is_cached_and_equals_field_tuple_hash() -> None:
    instrument = InstrumentId("XKRX", "005930", AssetClass.EQUITY, "KRW")
    assert hash(instrument) == hash(("XKRX", "005930", AssetClass.EQUITY, "KRW"))
    assert instrument == InstrumentId("XKRX", "005930", AssetClass.EQUITY, "KRW")
    assert "_hash" not in {f.name for f in fields(instrument)}
```

- [x] **Step 2: 실행해 실패 확인** — `uv run pytest tests/test_types.py -k hash_is_cached -q` → `AttributeError`가 아니라 첫 assert가 통과하고 마지막 assert만 통과하므로, 캐시 유무를 검증하려면 `InstrumentId.__hash__ is not object.__hash__`와 `instrument.__dict__["_hash"]` 존재를 함께 단언한다.
- [x] **Step 3: 구현** — `__post_init__`에서 `object.__setattr__(self, "_hash", hash((venue, symbol, asset_class, currency)))`, `__hash__`는 `_hash` 반환. 주석(한글)으로 이유 기재: 스냅샷·bar 인덱스에서 세션마다 수십만 번 해시된다.
- [x] **Step 4: 통과 확인, 전체 `uv run pytest -q`**
- [x] **Step 5: 커밋** `perf(types): InstrumentId 해시를 생성 시 한 번만 계산`

### Task A2: Rust 레코드 스토어 (`records.rs`)

**Files:**
- Create: `backend/rust/backtest_core/src/records.rs`
- Delete: `backend/rust/backtest_core/src/compact_store.rs`
- Modify: `lib.rs`, `persistent.rs` (필드 교체)

- [x] **Step 1: Rust 단위 테스트 작성** — `records.rs` 안 `#[cfg(test)]`: append 순서로 seq 증가, `finish()` 이후 append 거부, `equity_series()`가 SNAPSHOT만 순서대로, `traded_notional()`이 FILL의 `qty as f64 * price` 순차 누산.
- [x] **Step 2: 구현**

```rust
pub(crate) const KIND_MARKET: u8 = 0; // … KIND_COST = 8
pub(crate) struct OrderWire { order_id, decision_id, instrument_id: u32, quantity: i64, side, order_type,
    limit_text: Option<String>, stop_text: Option<String>, tif, group_id: Option<String>, action_index: usize, leg_index: Option<usize> }
pub(crate) struct FillWire { fill_id, order_id, instrument_id: u32, quantity: i64, side, price, fee, slip }
pub(crate) struct SnapshotWire { cash, rows: Vec<(u32, i64, f64, f64, f64, f64)>, equity, gross }
pub(crate) enum RecordPayload { Market, Decision { decision_id, native: Option<NativeDecision> }, Order(OrderWire),
    OrderUpdate { order_id, status, detail: Option<String> }, Fill(FillWire), Snapshot(SnapshotWire),
    CorporateAction(usize), CorporateActionApplied { ca, old_qty, new_qty, old_avg, new_avg, cash_paid },
    Cost { kind, instrument_id: Option<u32>, amount } }
pub(crate) struct NativeRecord { session_index: usize, kind: u8, payload: RecordPayload }
pub(crate) struct RecordStore { records: Vec<NativeRecord>, finished: bool }
impl RecordStore { append(session_index, payload) -> PyResult<()>; finish(); batch(py) -> Vec<PyObject>; equity_series(); traded_notional() }
```

`OrderWire`/`FillWire`/`SnapshotWire`는 `IntoPyObject`로 위 wire 계약의 tuple을 만든다 (`PyTuple::new`).

- [x] **Step 3: `cargo test`** 통과, `cargo clippy -D warnings` 통과
- [x] **Step 4: 커밋** `feat(rust): Rust-native 레코드 스토어 추가`

### Task A3: `StoredOrder` 확장, feed 조회, 문자열 헬퍼

**Files:** `persistent.rs`, `feed.rs`, `session.rs`

- [x] **Step 1:** `StoredOrder`에 `decision_id: String, action_index: usize, leg_index: Option<usize>` 추가. `from_routed`가 `decision_id`를 받아 채움. `from_tuple`(legacy `place_order`)은 빈 문자열/0/None.
- [x] **Step 2:** `PersistentFeed`에 `key_index: HashMap<String, u32>` 구축, `instrument_id(&str) -> Option<u32>`, `has_bar(session, key) -> bool`, `open_at(session, key) -> Option<f64>`, `session_len()`.
- [x] **Step 3:** `session.rs`의 `py_float`, `py_list`를 `pub(crate)`. `py_tuple(items: &[String]) -> String` 추가 (`('a',)`, `('a', 'b')`, `()`), 단위 테스트.
- [x] **Step 4:** `cargo test` / clippy, 커밋 `refactor(rust): 드라이버가 쓸 주문 메타·feed 조회·문자열 헬퍼`

### Task A4: 드라이버 (`driver.rs`) + pymethods

**Files:** Create `driver.rs`; Modify `persistent.rs`, `callback.rs`, `lib.rs`

- [x] **Step 1: Rust 단위 테스트 (driver.rs)** — 1종목 2세션 feed, `configure_run` every_session, warmup 0: `drive()` 첫 프레임이 session 0 "market", `submit_decision`에 NoAction wire → 두 번째 프레임 session 1, 세 번째 `None`; `finish()` 배치 kinds가 `[MARKET, SNAPSHOT, DECISION, MARKET, SNAPSHOT, DECISION]`. 두 번째 테스트: warmup 2면 프레임 없이 `None`. 세 번째: 매수 주문을 넣는 DecisionWire(SetPortfolioTarget weight 0.5) 제출 후 다음 세션 배치에 `ORDER`(세션 0, SESSION_CLOSE 뒤) → `MARKET`(1) → `ORDER_UPDATE(filled)` → `FILL` 순서.
- [x] **Step 2: 구현** — 위 알고리즘. `Queued` enum: `Market(usize) | Fill(FillWire) | Notify(NotifyPayload) | SessionClose(usize) | Order(OrderWire)`; `NotifyPayload = Fill(FillWire) | OrderUpdate{..} | CorporateAction(usize)`. `NativeEventQueue.push(session_index as i64, priority, token)` with token = `queued.len()` index. `PersistentEngine` 필드 추가: `run: Option<RunConfig>`, `corporate_actions: Vec<CaEntry>`, `ca_by_session: HashMap<usize, Vec<usize>>`, `queued: Vec<Queued>`, `records: RecordStore`, `awaiting_session: usize`, `started: bool`.
- [x] **Step 3: pymethods** — `configure_run`, `load_corporate_actions`, `drive`, `finish`(교체), `record_batch`(교체), `equity_series`, `traded_notional`, `submit_decision`(반환형 교체). 제거: `queue_push/queue_pop/queue_len/record_append/record_extend`. `CallbackFrame`에 `event: PyObject`, `snapshot: PyObject`, `open_orders: PyObject` getter.
- [x] **Step 4:** `cargo test`, `cargo clippy -D warnings`, `cargo fmt --check`, `uv run maturin develop --release`
- [x] **Step 5: 커밋** `feat(rust): drive()/finish() 세션 루프 드라이버`

### Task A5: Python store·context·loop 재작성

**Files:** `engine/store.py`, `engine/context.py`, `engine/loop.py`, `engine/core.py`, `engine/wire.py`, `engine/queue.py`, `types/results.py`; Delete `engine/compact.py`

- [x] **Step 1: 실패 테스트** — `tests/test_rust_driver.py`:
  - `test_persistent_run_makes_no_per_session_ffi`: CountingRuntime 프록시로 `drive` 호출 수 == decision tape 길이 + 1, `process_market_index`·`activate_pending`·`close_current_session`·`queue_push` 호출 0, `configure_run`·`load_feed`·`load_corporate_actions`·`finish` 각 1.
  - `test_golden_trace_identical_with_driver`: 기존 `ENGINE_SCENARIOS["golden"]` python vs rust `trace_bytes` 동일 (test_core_parity가 이미 커버 — 여기서는 rust 단독 실행이 `EquityWipedOut`·`NegativeCashError` 접두어를 매핑하는지: 초기 현금 소액 + 마진 없이 큰 주문 시나리오는 라우터가 거르므로, `equity_wiped_out`은 숏 포지션 폭등 시나리오(`test_short_selling` 픽스처 변형)로 재현).
  - `test_context_snapshot_is_fixed_at_callback_time`: 전략이 ctx를 보관하고 run 종료 후 `ctx.position_qty`를 읽어도 콜백 시점 값.
- [x] **Step 2: `store.py`** — `PersistentEventStore.__init__(runtime, sessions, instruments, market_snapshots, corporate_actions)`; `register_decision`, `frame_event(frame)`, `snapshot_from_wire(session_index, wire)`, `order_from_wire`, `fill_from_wire`, `records`(lazy, seq 순), `_payloads(kind)`, `snapshots()`, `orders()`, `fills()`, `compact_trace()` → `(seq, session_index, kind_value, seq)`, `finish()` → `runtime.finish()` 배치 보관. ORDER의 `source_action`은 `decisions[decision_id].actions[action_index]`(leg_index 있으면 `.legs[leg_index]`).
- [x] **Step 3: `context.py`** — `RustStrategyContext` 독립 frozen dataclass(`now, frame, store, history_store, declared, universe_source`), `snapshot`·`_lazy_open_orders`는 `cached_property`. `StrategyContext` 프로토콜 메서드 전부 구현.
- [x] **Step 4: `loop.py`** — `_execute`에서 `if run.persistent_runtime is not None: return self._execute_persistent(...)`; `_execute_persistent` 위 의사코드; `_on_market_persistent`, `_record_compact_update`, Compact* import·match 분기 제거. `_Run`에서 `PersistentOrderManager`·`PersistentEventQueue` 생성 제거(persistent일 때 `order_manager=None`, `queue=None`, `router=None`).
- [x] **Step 5: `core.py`** — `PersistentOrderManager` 삭제, `PersistentPortfolio`는 `register_instruments`/`snapshot`(ctx용 아님, 삭제 가능)만 남기거나 통째로 삭제하고 `_Run.portfolio`를 persistent에서 None으로. `make_persistent_runtime` 유지.
- [x] **Step 6: `wire.py`, `queue.py`, `results.py`** — 위 파일 구조표대로. `BacktestResult.lazy(snapshots=Callable)`.
- [x] **Step 7: 테스트 갱신** — `test_core_parity.py`: `test_promoted_rust_sends_one_decision_batch_per_callback`를 새 계약으로(위 A5-1로 이동 후 삭제), `test_persistent_event_queue_matches_timestamp_priority_and_fifo_order` 삭제(Rust `event_queue.rs` 테스트가 커버), `test_persistent_callback_tokens_reject_stale_and_double_submit`는 `configure_run` 후 `drive()`로 프레임 획득하도록, `test_persistent_finish_keeps_order_fill_results_lazy_and_compact_traceable`는 `PersistentEventStore.order_from_wire/fill_from_wire`를 패치해 materialize 횟수 검증.
- [x] **Step 8: 게이트** — `uv run pytest -q`, `ruff`, `pyright`
- [x] **Step 9: 커밋** `feat(engine): persistent 경로를 Rust drive() 루프로 전환`

### Task A6: 벤치 확인 + PR A (#107)

- [x] `uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 100 --synthetic --core all --warmup 1 --repeat 3` 결과를 PR 본문에 기록 (목표: rust ≤ 0.6초)
- [x] `--profile`로 남은 Python 시간이 전략 콜백·최종 materialization뿐인지 확인
- [x] 브랜치 push, `gh pr create --base main` (pr-review.md 양식), Opus 리뷰어 서브에이전트 1명 → 수정 → 재리뷰 APPROVE

---

## PR B — 선언형 tape 네이티브 결정 (브랜치 `feat/rust-tape-native`, base `feat/rust-loop-driver`, #109)

### Task B1: 엔진 측 선언형 tape 계약

**Files:** Create `types/tape.py`, `engine/tape.py`; Test `tests/test_tape.py`

```python
@dataclass(frozen=True)
class TapeFrame:
    action: SetPortfolioTarget      # targets는 WeightTarget만 허용 (검증)
    reason: str

class DeclarativeTapeStrategy(Strategy, Protocol):
    idle_reason: str
    def tape_frames(self) -> Mapping[date, TapeFrame]: ...

def evaluate_tape(frames, idle_reason, event, ctx) -> StrategyDecision:
    # MarketSnapshot 아니면 no_action(idle_reason)
    # frame = frames.get(event.ts.date()); 없으면 no_action(idle_reason)
    # kept/untradable: bar 있으면 유지, 없고 보유(ctx.position_qty != 0)면 QuantityTarget(held), 아니면 제외
    # reason = frame.reason + (f" no_bar={untradable}" if untradable else "")   # untradable은 tuple[str]
    # return StrategyDecision.of(event.ts, replace(frame.action, targets=kept), reason)
```

- [x] 테스트: `test_target_tape_strategy.py`의 두 케이스를 `evaluate_tape`로 옮겨 동일 결과 단언 (reason 문자열 포함).

### Task B2: Rust tape (`tape.rs`)

- [x] `load_target_tape(frames: Vec<(usize session_index, Vec<TargetWire>, ExecutionWire, String scope, String reason)>, idle_reason: String)`
- [x] `drive()`에서 프레임을 돌려주기 직전 `self.tape.is_some()`이면 Rust가 결정을 만든다: market 콜백 → 해당 세션 frame 없으면 NoAction(idle_reason); 있으면 no-bar 규칙(`feed.has_bar`, `portfolio.held_qty`)으로 kept `TargetWire` 목록과 untradable symbols → reason → `DecisionWire(1, ts, reason, [("set_portfolio_target", kept, scope, execution, None, [])])`; 비-market 콜백 → NoAction(idle_reason). 그 뒤 `submit_decision` 내부 함수를 같은 세션으로 호출. DECISION 레코드의 `native = Some(NativeDecision { frame_session: Option<usize>, kept: Vec<(u32, kind, f64|i64)>, no_bar: Vec<String>, reason })`.
- [x] Rust 단위 테스트: 보유 종목 bar 없음 → QuantityTarget 유지, 미보유 → 제외, reason 포맷 `target_tape:2018-04-27 no_bar=('005930:1', '000030:1')`.

### Task B3: Python 연결

- [x] `loop.py::_execute_persistent`: `isinstance`가 아니라 `hasattr(strategy, "tape_frames")`로 선언형 판단 → 세션 date → index 매핑 후 `load_target_tape`; drive 루프는 그대로(프레임이 오지 않음).
- [x] `store.py`: DECISION payload `native`가 있으면 `StrategyDecision`을 `TapeFrame.action`에서 재구성 (`replace(action, targets=kept)`; kept는 wire → `WeightTarget`/`QuantityTarget(Decimal)`), 없으면 `no_action(ts, idle_reason)`.
- [x] `_adapter.py`: `TargetTapeStrategy`에 `idle_reason = "target_tape_idle"`, `tape_frames()`(frame.signal_as_of → `TapeFrame(bridge.to_target_action(frame, max_participation), f"target_tape:{iso}")`), `on_event`는 `evaluate_tape` 위임.
- [x] parity 테스트(`tests/test_core_parity.py`): 같은 tape 전략을 python core와 rust core로 돌려 `trace_bytes` 동일, rust 경로 decision tape 비어 있음, `drive` 호출 1회.
- [x] 게이트 통과 후 커밋, stacked PR 생성, Opus 리뷰

---

## PR C — 벤치·문서 (브랜치 `feat/rust-loop-bench`, base `feat/rust-tape-native`)

- [x] `scripts/bench_universe.py --strategy {callback,tape}`: `tape`는 `EqualWeightRebalance`와 같은 목표를 `DeclarativeTapeStrategy`로 미리 만든 표 (5세션마다 동일 비중)
- [x] 측정: 100/300종목 synthetic × {callback, tape} × {python, rust}, 실제 4종목 fixture. `benchmarks/baseline/rust-loop-100.json`, `rust-loop-300.json`, `rust-loop-real-fixture.json`
- [x] 스펙 문서 "현재 재개 지점"·M6 판정 갱신, `docs/rust-python-benchmark-report.html` 수치 갱신, 이슈 #98 댓글로 최종 배수 보고
- [ ] stacked PR, Opus 리뷰

---

## 검증 명령 (각 Task 종료 시)

```bash
cd backend
cargo fmt --manifest-path rust/backtest_core/Cargo.toml -- --check
cargo clippy --manifest-path rust/backtest_core/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/backtest_core/Cargo.toml
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release
uv run pytest tests/test_core_parity.py -q
uv run pytest -q
uv run ruff check src tests scripts examples
uv run pyright
```
