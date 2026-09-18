# Rust 루프 드라이버 리뷰 후속 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이슈 #98 PR 스택(#107→#109→#110) 머지 후 리뷰(2026-09-18, Rust·Python·성능 실측 3인)에서 나온 결함·SoT 중복·죽은 코드·성능 여지를 전부 해소하고, 게이트를 정직한 측정 경계(`run()` + 결과 조회)로 다시 판정한다.

**Architecture:** 측정 교정 → 동작 결함 수정 → 죽은 코드 제거 → SoT 단일화(Rust가 wire 상수 소유, Python이 문자열 포맷 소유) → 성능(결과 조회 배치화, 워크벤치 columnar 결과, Rust hot loop, 메모리 레이아웃, feed columnar) → 최종 재측정·문서 순서로 11개 PR을 main 위에 스택으로 쌓는다. 앞 PR이 뒤 PR의 base다.

**Tech Stack:** Rust 2021 + pyo3 0.23 (`backend/rust/backtest_core`), Python 3.11 (`backend/src/backtest_engine`, `backend/src/strategy_workbench`), pytest, maturin, `scripts/bench_universe.py`.

**불변 계약 (성능보다 우선):** EventStore trace(`seq, ts, kind, payload`)·ID·float 연산 순서·오류 타입과 메시지 접두어가 `core="python"`과 byte 동일. `BacktestEngine.run()` 서명과 Python 전략 API 불변. `core="python"`·`core="rust_legacy"` 경로 삭제 금지(Phase 3-4 별도 이슈). 새 주석·PR·이슈는 한글.

---

## 리뷰 결과 → PR 매핑

| ID | 리뷰 결과 | PR |
|---|---|---|
| DEFECT-301 | 벤치 타이머가 `run()`만 재서 rust 배수 과대평가 (100종목 tape 5.2배 → 결과 조회 포함 2.2배) | PR 1 |
| RSS-1.27 | tape Peak RSS 1.27배의 원인은 lazy 조회 구간(`run()` 종료 시점은 0.97배) | PR 1(측정), PR 6·9(해소) |
| Phase 3-2 | 워크벤치 e2e 구간별 측정 미구현 | PR 1 |
| DEFECT-R01 | `on_session_close` 도메인 오류 후 lifecycle이 `Running`으로 남아 `drive()` 재진입 가능 | PR 2 |
| SoT-5 | 라우팅 오류 인코딩 2종 (`(code, detail)` 튜플 vs `"route_error:code:msg"` 문자열) | PR 2 |
| GAP-1/2/3 | CORPORATE_ACTION 알림·`month_end`·no_bar "미보유 먼저" 순서가 rust 드라이버 parity에 없음 | PR 2 |
| SLOP-R | `settings()?` 반환 버림, `remaining_by_id` 낭비, `current_session` 이중 호출, `history_window` 도달 불가 NaN 분기 | PR 2 |
| DEAD-1 | `PersistentPortfolio`(core.py:249-403)·`make_portfolio("rust")` 분기·그에 매달린 pymethods 약 20개 | PR 3 |
| DEAD-2 | persistent `_Run`이 주석과 달리 `OrderManager()` 생성, `compact_trace` token 자리 `seq` 중복, `wire.route_error` 래퍼, `feed.snapshots()` 이중 tuple | PR 3 |
| RULE-1 | `types/instruments.py:36` `# type: ignore` (규칙 금지), `bench_universe.py:324` 프로덕션 `assert`, `test_target_tape_strategy.py` 중복 테스트 2건 | PR 1·3 |
| SoT-1 | 큐 엔트리가 세션을 들고 있는데 `pop()`이 버려 `current_session()`으로 feed 커서에서 재유도 | PR 4 |
| SoT-3 | `process_market_index`가 `pub(crate)` + `#[pymethods]` 동시 노출 | PR 4 |
| SoT-4 | `PRIORITY_*` 상수와 `KIND_*` 코드가 Rust에 재선언, 어긋나도 잡는 테스트 없음 | PR 4·5 |
| SoT-6 | `registry_symbols()` 심볼 폴백 표를 결정마다 재조립 | PR 8 |
| DEFECT-302 | `RecordKind` 선언 순서가 wire 코드 (enum 중간 삽입 시 silent corrupt) | PR 5 |
| SoT-2 | `RustStrategyContext`가 `EngineStrategyContext` 메서드 7개를 예외 메시지까지 복사 | PR 5 |
| SoT-4' | ` no_bar=<tuple repr>` 포맷을 Python·Rust가 각각 구현 (`py_tuple`) | PR 4·5 |
| DEFECT-303 | `runtime_checkable` Protocol 구조 일치만으로 `on_event` 전체 생략 | PR 5 |
| PERF-1 | 레코드 materialize가 kind마다 배치 전체 재스캔 + 레코드당 FFI, Rust payload가 조회 중 상주 | PR 6 |
| PERF-2/3/4 | 워크벤치가 `Position` 123k·`FillEvent` 22k 객체를 만들어 합산만 함, `OrderEvent` 조립 10µs/건 | PR 7 |
| PERF-5/6 | `decision.clone()`, `registry_symbols` 재조립, tape 프레임의 snapshot·open_orders 낭비, `row_of` 선형 탐색, 라우터·원장 String 선형 탐색, `settings()?.clone()`, `current_marks` clone | PR 8 |
| MEM-1/2 | `RecordPayload` enum이 `OrderWire` 크기(약 240B)로 고정, 큐 arena 46,860 슬롯 `finish()`까지 상주 | PR 9 |
| PERF-7 | feed 적재가 `run()`의 10~15% (계획 2-5 미적용) | PR 10 |
| 게이트 재판정 | 정직한 경계로 100/300종목·4종목 fixture·RSS 재측정, 스펙·리포트·이슈 갱신 | PR 11 |

---

## 공통 규칙

### 브랜치·스택

- 브랜치 이름은 아래 각 PR 절의 것을 쓴다. PR N의 base는 PR N-1 브랜치, PR 1의 base는 `main`.
- 한 작업 트리(`candlefish/`)에서 순차 진행한다. PR N 착수: `git checkout -b <branch-N> <branch-N-1>`.
- 리뷰 반영 커밋은 스택 끝에 쌓는다. 앞 PR 브랜치에 fix가 필요하면 그 브랜치에 커밋 후 뒤 브랜치들을 `git rebase --onto`로 올린다.
- PR 본문은 `.claude/rules/pr-review.md` 양식. 설계 결정 절은 PR 4·5·6·7·9·10에 필수.
- 각 PR은 구현 완료 → 게이트 통과 → Opus 리뷰어 서브에이전트(읽기 전용, 이 문서 해당 절과 이슈 #98을 컨텍스트로 제공) → APPROVE → `git push -u origin <branch>` → `gh pr create --base <prev-branch>`.

### 게이트 (모든 PR 공통, `backend/`에서)

```bash
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release   # Rust 변경 시
cargo fmt --manifest-path rust/backtest_core/Cargo.toml -- --check
cargo clippy --manifest-path rust/backtest_core/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/backtest_core/Cargo.toml
uv run pytest tests/test_core_parity.py tests/test_rust_driver.py tests/test_tape.py -q
uv run pytest -q
uv run ruff check src tests scripts
uv run pyright
```

### AC 등급

각 PR의 AC는 세 등급을 명시한다. 등급이 "불필요"면 이유를 적는다.

1. **단위·parity**: 새 테스트가 실패→통과했고 전체 스위트가 통과한다.
2. **실측**: `scripts/bench_universe.py`·`scripts/bench_workbench_adapter.py` 수치. 같은 세션에서 base 브랜치와 back-to-back으로 재고 CPU 부하(`Get-CimInstance Win32_Processor | Select LoadPercentage`)를 함께 기록한다. 부하 40% 초과면 재측정.
3. **E2E**: 워크벤치 HTTP 경로(`uv run pytest tests/integration/test_backtest_http_api.py -q`, 특히 `test_python_reference_and_rust_core_have_golden_result_and_metric_parity`)와 필요 시 `scripts/bench_workbench_adapter.py`로 실제 어댑터 실행. 결과 계약·워크벤치 경로·타입 계약을 건드린 PR에만 요구한다.

### 벤치 측정 표준 (PR 1 이후)

```bash
# 시간 (run + 결과 조회 분리 기록)
uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 100 --synthetic --core all --strategy callback --warmup 1 --repeat 5 --json-out benchmarks/baseline/rust-loop-100-callback.json
uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 100 --synthetic --core all --strategy tape     --warmup 1 --repeat 5 --json-out benchmarks/baseline/rust-loop-100-tape.json
uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 300 --synthetic --core all --strategy callback --warmup 1 --repeat 5 --json-out benchmarks/baseline/rust-loop-300-callback.json
uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 300 --synthetic --core all --strategy tape     --warmup 1 --repeat 5 --json-out benchmarks/baseline/rust-loop-300-tape.json
uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 4 --core all --strategy callback --warmup 1 --repeat 5 --json-out benchmarks/baseline/rust-loop-real-fixture-callback.json
uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 4 --core all --strategy tape     --warmup 1 --repeat 5 --json-out benchmarks/baseline/rust-loop-real-fixture-tape.json
# Peak RSS (코어별 프로세스 격리, 결과 조회 포함)
for core in python rust; do for s in callback tape; do
  uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 100 --synthetic --core $core --strategy $s --warmup 1 --repeat 3 --json-out benchmarks/baseline/rust-loop-memory-100-$s-$core.json
done; done
# 워크벤치 어댑터 e2e 구간 (Phase 3-2)
uv run python scripts/bench_workbench_adapter.py --instruments 100 --core all --repeat 3 --json-out benchmarks/baseline/rust-loop-workbench-100.json
```

---

## PR 1 — 벤치 측정 경계 교정 + 워크벤치 구간 측정 (`fix/bench-honest-boundary`, base `main`)

**배경:** DEFECT-301. `scripts/bench_universe.py:283-292`가 `engine.run()`만 재고 `result.snapshots[-1]`·`len(result.orders)`·`len(result.fills)`는 타이머 밖이다. rust 코어는 `BacktestResult.lazy`라 이 조회에서 객체를 만들고 python 코어는 `run()` 안에서 만든다. `process_peak_rss_bytes`는 프로세스 전체 peak라 `--core all`에서 세 코어가 같은 값을 받는다.

**Files:**
- Modify: `backend/scripts/bench_universe.py`
- Create: `backend/scripts/bench_workbench_adapter.py`
- Modify: `backend/benchmarks/baseline/rust-loop-*.json` (11개, 재기록)
- Modify: `docs/superpowers/specs/2026-09-01-persistent-rust-engine-implementation.md` "2026-09-17 재판정" 절
- Modify: `docs/rust-python-benchmark-report.html` 게이트 표

### Task 1.1: `run_once`가 `run()`과 결과 조회를 분리해 잰다

- [ ] **Step 1**: `run_once`를 다음으로 교체한다 (`assert` 제거는 Step 3).

```python
    def run_once(core: str) -> tuple[Timing, tuple[float, int, int]]:
        engine = BacktestEngine(
            RunConfig(run_id=f"bench-{core}", initial_cash=1_000_000_000, fee_bps=15),
            core=core,
        )
        strategy = (
            EqualWeightTape(instruments, args.every, 0.9, feed)
            if args.strategy == "tape"
            else EqualWeightRebalance(instruments, args.every, 0.9)
        )
        profiler = cProfile.Profile() if args.profile else None
        if profiler is not None:
            profiler.enable()
        started = time.perf_counter()
        result = engine.run(strategy, feed)
        run_seconds = time.perf_counter() - started
        # 결과 조회를 타이머 안에 넣는다 — rust 코어는 여기서 공개 객체를 만들고 python 코어는
        # run() 안에서 이미 만들었다. 두 코어를 같은 경계로 재야 배수가 뜻을 가진다.
        materialize_started = time.perf_counter()
        snapshots, orders, fills = result.snapshots, result.orders, result.fills
        materialize_seconds = time.perf_counter() - materialize_started
        if profiler is not None:
            profiler.disable()
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
        final_equity = snapshots[-1].equity if snapshots else float("nan")
        return (
            Timing(run_seconds, materialize_seconds, peak_rss_bytes()),
            (final_equity, len(orders), len(fills)),
        )
```

- [ ] **Step 2**: 파일 상단에 `Timing`을 추가한다.

```python
@dataclass(frozen=True)
class Timing:
    """한 회차의 구간별 wall time과 그 시점까지의 프로세스 peak RSS."""

    run_seconds: float
    materialize_seconds: float
    peak_rss_after_materialize_bytes: int

    @property
    def total_seconds(self) -> float:
        return self.run_seconds + self.materialize_seconds
```

- [ ] **Step 3**: 집계 부분을 교체한다. `speedup_vs_python`은 **total** 기준, `run_speedup_vs_python`을 참고용으로 남긴다. `assert isinstance(core_payload, dict)`는 `core_payload: dict[str, object] = {}` 로컬 변수로 바꿔 `payload["cores"] = core_payload`로 넣는다. `process_peak_rss_bytes`는 유지하되 코어별 `peak_rss_after_materialize_bytes`(그 코어 마지막 회차의 값)를 추가하고, 출력 줄에 `run=… materialize=… total=…`을 찍는다.

- [ ] **Step 4**: `--core all` 실행에서 RSS 격리가 안 된다는 경고를 docstring에 적고, `--core` 단일 실행이 RSS 정본임을 "벤치 측정 표준"과 같은 문장으로 남긴다.

- [ ] **Step 5**: 실행해 형식을 확인한다.

Run: `uv run python scripts/bench_universe.py tests/fixtures/krx_parquet --instruments 4 --core all --strategy tape --warmup 1 --repeat 2`
Expected: 코어당 `run=… materialize=… total=… speedup=…x` 줄, 세 코어 signature 동일.

- [ ] **Step 6**: 커밋 `fix(bench): 결과 조회를 타이머 안에 넣어 코어 간 측정 경계를 맞춘다`.

### Task 1.2: 워크벤치 어댑터 구간 측정 스크립트 (Phase 3-2)

- [ ] **Step 1**: `scripts/bench_workbench_adapter.py` 생성. `bench_universe.py`의 synthetic 유니버스 생성 함수를 import해 `BacktestExecutionRequest`를 만들고(`tests/integration/test_backtest_http_api.py`의 `_run_body`가 쓰는 fixture 경로를 참고해 `BacktestEngineExecutorAdapter.execute`에 직접 넣는다), 다음 구간을 `time.perf_counter`로 분리 기록한다: `engine.run`, `event_store.costs()`, `result.snapshots/orders/fills` 조회, `_artifacts`, `AnalysisPoint` 생성, `compute_analytics`, 전체. 구간 계측은 어댑터를 수정하지 않고 `execute`의 `progress` 콜백 스테이지 경계 + 결과 객체 접근 시각으로 잰다. 정확한 구간이 콜백만으로 안 잡히면 어댑터 `execute` 안의 단계 함수(`_artifacts`, `compute_analytics`)를 monkeypatch로 감싸 시간을 누적한다.
- [ ] **Step 2**: `--core {python,rust,all}`, `--instruments`, `--repeat`, `--json-out`. 출력 JSON: `{"workload": …, "cores": {core: {"stage_seconds": {...}, "total_seconds": …, "speedup_vs_python": …}}}`. 두 코어의 metrics·series가 다르면 `RuntimeError`.
- [ ] **Step 3**: 실행해 100종목 tape에서 rust `engine.run` 대비 나머지 구간 비율을 확인한다 (리뷰 실측: 변환 합계가 run의 1.46배).
- [ ] **Step 4**: 커밋 `bench(workbench): 어댑터 e2e 구간별 측정 스크립트 (#98 Phase 3-2)`.

### Task 1.3: 기준선 재기록과 문서

- [ ] **Step 1**: "벤치 측정 표준"의 명령을 전부 실행해 JSON 11개를 다시 쓴다. 부하 40% 이하에서.
- [ ] **Step 2**: 스펙 "2026-09-17 재판정" 아래에 "2026-09-18 측정 경계 교정" 절을 추가한다. 표는 `run()`만 / `run()`+조회 두 열, Peak RSS는 코어 격리 값. 게이트 판정을 정직한 경계로 다시 쓴다 (예상: tape 3배 미달, 2배 통과, RSS tape 미달). 이 PR은 개선이 아니라 측정 교정이므로 "PR 6~10 이후 재판정"을 명시.
- [ ] **Step 3**: `docs/rust-python-benchmark-report.html` 게이트 표·memory 카드에 같은 수치 반영.
- [ ] **Step 4**: 커밋 `docs(bench): 정직한 측정 경계로 기준선·게이트 판정 재기록`.

### AC

- 단위·parity: 전체 스위트 통과 (벤치는 테스트 대상 아님, `testing.md`).
- 실측: JSON 11개에 `run_seconds`·`materialize_seconds`·`total_seconds`·코어별 RSS가 있다. 100종목 tape `speedup_vs_python`(total)이 기존 4.73배와 다르며, 리뷰 실측(2.0~2.3배)과 같은 자릿수다.
- E2E: `bench_workbench_adapter.py`가 python·rust 두 코어에서 같은 metrics·series로 완주하고 JSON을 남긴다.

---

## PR 2 — 드라이버 실패 상태 전이·구조화 라우팅 오류·parity 공백 (`fix/driver-failed-lifecycle`, base PR 1)

**배경:** DEFECT-R01(`driver.rs:317-326`), SoT-5(`tape.rs:191-194` 문자열 인코딩), GAP-1/2/3, SLOP-R.

**Files:**
- Modify: `backend/rust/backtest_core/src/driver.rs`, `tape.rs`, `persistent.rs`, `feed.rs`, `lib.rs`
- Modify: `backend/src/backtest_engine/engine/loop.py` (`_drive`), `wire.py`
- Modify: `backend/tests/test_rust_driver.py`, `tests/test_core_parity.py`, `tests/test_corporate_action_engine.py`, `tests/test_tape.py`

### Task 2.1: 세션 처리 오류가 runtime을 `Failed`로 만든다 (DEFECT-R01)

- [ ] **Step 1**: Rust 테스트를 `driver.rs` `mod tests`에 추가한다 (기존 `runtime(warmup)`·`target(ts, w)` 헬퍼 사용). 자본 100으로 시작해 가격 100짜리 1주를 사고 다음 세션 마감 전 가격이 -1로 떨어지게 feed를 만들 수 없으므로, `RunSettings.margin_interest_bps_annual`을 거대하게 잡아 음수 현금에서 이자 비용이 equity를 음수로 만드는 시나리오를 쓴다. 단언: 첫 `drive()`가 `equity_wiped_out:` 접두어 Err, 이후 `drive()`는 `persistent runtime is failed` Err, `finish_internal()`도 Err, `lifecycle_state() == "failed"`.
- [ ] **Step 2**: `cargo test`로 실패 확인 (두 번째 `drive()`가 Ok를 돌려줌).
- [ ] **Step 3**: `drive_internal`의 드레인 루프를 `self.drain_until_callback()`로 분리하고 오류를 잡아 상태를 바꾼다.

```rust
    pub(crate) fn drive_internal(&mut self) -> PyResult<Option<CallbackFrame>> {
        // (기존 lifecycle match와 시작 처리 유지)
        match self.drain_until_callback() {
            Ok(frame) => Ok(frame),
            Err(error) => {
                // 세션 처리 중 도메인 오류(자본 소진·음수 현금·정산 bar 없음)는 라우팅 오류와
                // 같이 runtime을 실패 상태로 고정한다 — 재진입하면 비용은 반영됐지만 SNAPSHOT이
                // 없는 세션 뒤로 조용히 이어진다.
                self.lifecycle = Lifecycle::Failed;
                self.failure_message = Some(error.to_string());
                Err(error)
            }
        }
    }
```

- [ ] **Step 4**: Python 테스트 `tests/test_rust_driver.py`에 추가: `EquityWipedOut` 발생 후 같은 runtime(`engine`의 `_Run`은 접근 불가하므로 `make_persistent_runtime`을 monkeypatch로 잡아 두는 기존 패턴 `tests/test_rust_driver.py:108-124` 사용)에 `drive()`를 다시 부르면 `ValueError` "persistent runtime is failed", `finish()`는 "cannot finish failed".
- [ ] **Step 5**: 게이트 실행, 커밋 `fix(rust): 세션 처리 도메인 오류 후 runtime을 Failed로 고정 (DEFECT-R01)`.

### Task 2.2: 라우팅 오류를 구조화 예외로 통일 (SoT-5)

- [ ] **Step 1**: `lib.rs`에 `pyo3::create_exception!(backtest_core, RouteErrorException, pyo3::exceptions::PyValueError, "라우팅 오류. args=(code, message)");` 를 두고 `m.add("RouteErrorException", py.get_type::<RouteErrorException>())`로 등록한다.
- [ ] **Step 2**: `tape.rs::submit_native`에서 `PyValueError::new_err(format!("route_error:{code}:{message}"))` 대신 `RouteErrorException::new_err((code, message))`.
- [ ] **Step 3**: `loop.py::_drive`에서 `message.startswith("route_error:")` 분기를 삭제하고 `except backtest_core.RouteErrorException as error: code, message = error.args; raise route_error_from((code, message)) from error`로 바꾼다. `backtest_core`는 `importlib.import_module` 로 모듈 상단에서 지연 import(`core_available` 패턴과 동일). 나머지 `equity_wiped_out:`·`negative_*:` 접두어 분기는 `portfolio.rs`가 `rust_legacy` 어댑터와 공유하므로 유지한다.
- [ ] **Step 4**: `tests/test_tape.py`에 rust tape 경로에서 라우팅 오류(선언 안 한 REPLACE scope 등 `RouterConfig` 위반)가 `UndeclaredActionReturned`로 매핑되는 테스트를 추가하고 python 경로와 예외 타입·메시지 접두어가 같음을 단언.
- [ ] **Step 5**: 게이트, 커밋 `refactor(rust): tape 경로 라우팅 오류를 구조화 예외로 넘긴다`.

### Task 2.3: parity 공백 3건

- [ ] **Step 1** (GAP-1): `tests/test_corporate_action_engine.py::run`에 `core: str = "python"` 파라미터를 추가하고, `events=frozenset({EventKind.MARKET, EventKind.CORPORATE_ACTION})`로 선언한 전략 시나리오(확인된 분할 + 미확인 배당)를 `python`·`rust`로 돌려 `trace_bytes()`·`decision_tape_bytes()` 동일, rust 콜백 이벤트가 `CorporateActionEvent`이고 `event.action_type`·`ratio`가 입력과 같음을 단언하는 테스트를 `@RUST_ONLY`로 추가. (index 정합: `load_corporate_actions` enumerate ↔ `bind_corporate_actions` 순서.)
- [ ] **Step 2** (GAP-2): `tests/test_core_parity.py`에 `MonthEndSession` 스케줄 전략(3개월치 synthetic bar)으로 python/rust DECISION 수와 `trace_bytes()` 동일 테스트 추가.
- [ ] **Step 3** (GAP-3): `tests/test_tape.py::_delisting_frames`와 같은 fixture에서 target 순서를 "미보유 no_bar 종목 먼저, 보유 종목 나중"으로 둔 프레임을 추가하고 reason의 `no_bar=(…)` 순서가 두 경로에서 같음을 단언.
- [ ] **Step 4**: 실패하는 테스트가 있으면 드라이버를 고친다(예상: 없음). 커밋 `test(engine): rust 드라이버 parity 공백 — 자본변동 알림·월말 스케줄·no_bar 순서`.

### Task 2.4: 드라이버 소규모 정리 (별도 `refactor` 커밋)

- [ ] `driver.rs:286` `self.settings()?;` → `let _ = self.settings()?; // configure_run 선행 확인` 대신 `self.require_configured()?` 이름의 헬퍼로 의도를 드러낸다.
- [ ] `driver.rs:433-437` `remaining_by_id` 생성을 `if entry.confirmed` 블록 안으로 이동.
- [ ] `driver.rs:304-305` `current_session()` 결과를 지역 변수로 받아 `session_count()` 재호출 제거 (PR 4에서 큐가 세션을 돌려주면 이 코드는 사라진다. 여기서는 최소 수정).
- [ ] `feed.rs:300-304` field 유효성 검사를 루프 앞으로 옮기고 `_ => f64::NAN` 분기를 `unreachable!` 이 아니라 match 자체를 `field` enum(`Open|High|Low|Close|Volume`)으로 미리 변환한 값으로 바꾼다.
- [ ] 커밋 `refactor(rust): 드라이버·feed 정리 — 의도 없는 호출·낭비 할당 제거`.

### AC

- 단위·parity: Task 2.1 Rust 테스트 + Python 테스트, 2.2 라우팅 오류 매핑 테스트, 2.3 테스트 3건 모두 실패→통과. 전체 스위트 통과.
- 실측: 불필요 (동작 수정·테스트 추가만).
- E2E: 불필요 (워크벤치 경로 계약 불변). 단 `tests/integration/test_backtest_http_api.py -q` 통과는 확인.

---

## PR 3 — 죽은 persistent API·중복·규칙 위반 제거 (`refactor/drop-dead-persistent-api`, base PR 2)

**배경:** DEAD-1/2, RULE-1. `PersistentPortfolio`는 `loop.py:161`이 non-persistent 코어에서만 `make_portfolio`를 부르므로 엔진 실행 경로에서 도달 불가이고, `make_portfolio("rust")`는 테스트에서만 두 번째 runtime을 만든다. Rust `Portfolio` 회계의 Python-side parity oracle은 `RustPortfolio`(`rust_legacy`, 같은 `backtest_core.Portfolio`)가 이미 맡는다.

**Files:**
- Modify: `backend/src/backtest_engine/engine/core.py`, `loop.py`, `store.py`, `wire.py`, `types/instruments.py`
- Modify: `backend/rust/backtest_core/src/persistent.rs`
- Modify: `backend/tests/test_core_parity.py`, `tests/test_lookup_index.py`, `tests/test_target_tape_strategy.py`

### Task 3.1: `PersistentPortfolio`와 `make_portfolio("rust")` 삭제

- [x] **Step 1**: `make_portfolio`의 `PERSISTENT_RUST_CORES` 분기를 다음으로 바꾼다.

```python
    if core in PERSISTENT_RUST_CORES:
        raise CoreUnavailable(
            f"core={core!r} owns its portfolio inside PersistentEngine — "
            "use BacktestEngine(core=...) for engine runs, or core='rust_legacy' for the "
            "standalone backtest_core.Portfolio adapter"
        )
```

- [x] **Step 2**: `core.py:249-403` `PersistentPortfolio` 클래스와 그것만 쓰는 import를 삭제한다. `PersistentOrderManager`가 남아 있으면 같이 확인해 삭제.
- [x] **Step 3**: 테스트 이전. `tests/test_core_parity.py::RUST_ENGINE_CORES`를 쓰는 `test_portfolio_scenario_identical_across_cores`는 파라미터를 `["rust_legacy"]`로 좁힌다(엔진 코어 parity는 이미 trace 테스트가 덮는다). `core` fixture를 쓰는 `test_portfolio_errors_map_to_domain_exceptions`는 `["python", "rust_legacy"]`. `:420-421`, `:434`(DEFECT-603)과 `tests/test_lookup_index.py:93,175`의 `"rust"` → `"rust_legacy"`. `test_unavailable_core_is_an_error_not_a_fallback` 옆에 `make_portfolio("rust")`가 `CoreUnavailable`을 내는 테스트를 추가한다.
- [x] **Step 4**: 게이트, 커밋 `refactor(engine): 엔진 경로에서 도달 불가한 PersistentPortfolio 제거` (`ba669cd`).

### Task 3.2: Rust 죽은 `#[pymethods]` 제거

- [x] **Step 1**: Python 호출부 0개를 다시 확인한다.

```bash
for m in place_order register_group open_order_states open_group_states drop_group remove_order settle_order mark_triggered drain_orders next_decision_id next_order_id next_fill_id next_group_id activate_pending record_count failure_detail process_market apply_fill charge apply_corporate_action mark cash held_qty average_price portfolio_snapshot mark_current_session close_current_session current_session_count; do echo "$m: $(grep -rn "runtime\.$m(\|_inner\.$m(\|\.inner\.$m(" src tests scripts --include=*.py | wc -l)"; done
```

- [x] **Step 2**: 0개인 메서드를 `#[pymethods]` 블록에서 제거한다. 드라이버가 내부에서 쓰는 것(`close_current_session`, `activate_pending_internal`, `apply_corporate_action_ratio`, `process_market_index`)은 일반 `impl PersistentEngine` 블록의 `pub(crate)`로 옮긴다. Rust 단위 테스트가 쓰는 것(`persistent.rs` `mod tests`의 `place_order`·`process_market` 등)은 `#[cfg(test)]` 헬퍼로 내리거나 테스트를 드라이버 경유로 고친다. `failure_detail` getter는 `tests/test_core_parity.py:931,1040`이 읽으므로 유지. `lifecycle_state`, `_debug_force_panic_on_market` 유지.
- [x] **Step 3**: `tests/test_core_parity.py::test_promoted_rust_makes_no_per_session_ffi`의 "0회" 이름 목록에서 삭제된 이름을 빼고, 대신 `set(dir(proxies[0].inner))`에 삭제된 이름이 없음을 단언하는 줄을 추가한다 (공개 API 축소 고정).
- [x] **Step 4**: 게이트, 커밋 `refactor(rust): Python 호출부가 없는 PersistentEngine 공개 메서드 제거` (`4eb3a77`).
- 삭제 목록: `record_count`, `activate_pending`, `next_decision_id`, `next_order_id`, `next_fill_id`, `next_group_id`, `place_order`, `register_group`, `open_order_states`, `open_group_states`, `drop_group`, `remove_order`, `settle_order`, `mark_triggered`, `drain_orders`, `process_market`, `mark_current_session`, `apply_fill`, `charge`, `apply_corporate_action`, `mark`, `cash`, `held_qty`, `average_price`, `portfolio_snapshot`. `pub(crate)`로 이동: `cancel_for_key`, `process_market_index`, `close_current_session`, `apply_corporate_action_ratio`. 함께 죽은 `OrderState` 별칭·`StoredOrder::from_tuple`·`RecordStore::len`도 제거했다.

### Task 3.3: Python 정리

- [x] `loop.py:143-159`: persistent 분기에서 `self.order_manager = OrderManager()` 제거, 타입을 `OrderManager | None`으로 하고 python 경로 진입 시 `None` 검사(`_ledger` 패턴처럼 `_order_manager(run)` 헬퍼). `run.queue`·`run.corporate_actions`도 persistent에서 만들지 않는다면 같은 처리.
- [x] `store.py:542-547` `compact_trace`를 `(seq, session_index, kind)` 3-튜플로 바꾸고 호출부(`tests/`)를 맞춘다.
- [x] `wire.py:270-273` `route_error` 삭제, 호출부 `loop.py`에서 `if error_wire is not None: raise route_error_from(error_wire)`.
- [x] `loop.py:432,583` `tuple(feed.snapshots())` 이중 생성: `DataFeed`에 `snapshot_tuple` 속성이 없다면 `_load_persistent_feed`가 `(instruments, snapshots)`를 돌려주게 해 한 번만 만든다.
- [x] `types/instruments.py:36`: `return self._hash  # pyright: ignore[reportAttributeAccessIssue]  # reason: __post_init__이 object.__setattr__로 채우는 캐시 필드`. 실제 pyright 룰명은 실행해 확인.
- [x] `tests/test_target_tape_strategy.py:78-113` 두 테스트를 "어댑터는 `evaluate_tape`에 위임하고 `target_tape:<iso>` 접두어·`idle_reason`만 붙인다"를 검증하는 한 테스트로 줄인다 (no_bar 규칙 자체는 `tests/test_tape.py`가 소유).
- [x] 커밋 `refactor(engine): persistent 경로 죽은 코드·규칙 위반 정리` (`a1f872d`).
- 결과: `tests/test_core_parity.py` 통과 수 205 → 203 (포트폴리오 단독 parity −2, 도메인 예외 매핑 −2, persistent 거절 테스트 +2). 전체 스위트 1200 → 1197 passed / 13 skipped (위 −2, `test_target_tape_strategy.py` 중복 테스트 통합 −1).

### 리뷰 반영 (`cb174e5`)

- [x] FFI 테스트의 `queue_push`·`queue_pop`·`record_append`·`record_extend` 0회 단언은 Rust에 없는 이름이라 공허했다. 삭제하고 삭제 메서드 블랙리스트도 공개 이름 전체 집합 단언으로 바꿨다.
- [x] 엔진 레벨 parity에 `flip` 시나리오 추가 — 롱 +5에서 한 번의 매도 13주로 −8까지 넘어가 평단이 리셋되는 경로(`test_short_selling` 헬퍼 재사용).
- [x] `identifiers_are_deterministic`을 드라이버 경로 테스트 `identifier_prefixes_come_from_the_paths_that_mint_them`(driver.rs)으로 대체 — `submit_internal`의 D, 라우터의 O·G, `apply_market_ops`의 F를 실제로 읽는다.
- 결과: `tests/test_core_parity.py` 203 → 206(`flip` 3파라미터), 전체 1197 → 1200 passed / 13 skipped. Rust 테스트 24개 유지.

### AC

- 단위·parity: 전체 스위트 통과, `test_promoted_rust_makes_no_per_session_ffi`가 삭제 API 부재를 고정. `grep -rn "PersistentPortfolio" src tests` 0건.
- 실측: 불필요 (동작 불변). 단 삭제 전후 `tests/test_core_parity.py` 통과 수가 줄어든 만큼(rust 파라미터 제거) PR 본문에 개수 명시.
- E2E: `tests/integration/test_backtest_http_api.py -q` 통과.

---

## PR 4 — Rust가 wire 상수·큐 세션을 소유 (`refactor/rust-owns-wire-constants`, base PR 3)

**배경:** SoT-1, SoT-3, SoT-4, SoT-4'(Rust 쪽), DEFECT-302의 Rust 절반.

**Files:**
- Modify: `backend/rust/backtest_core/src/event_queue.rs`, `driver.rs`, `records.rs`, `tape.rs`, `session.rs`, `lib.rs`, `persistent.rs`
- Modify: `backend/src/backtest_engine/engine/store.py` (`_decision`만), `tests/test_core_parity.py`

### Task 4.1: 큐가 세션을 돌려준다 (SoT-1)

- [ ] **Step 1**: `event_queue.rs` 테스트를 `pop()`이 `(ts, token)`을 돌려주도록 고친다.
- [ ] **Step 2**: `pub(crate) fn pop(&mut self) -> PyResult<(TimestampKey, u64)>`. `driver.rs::pop`이 `Option<(usize, Queued)>`를 돌려주고, `drive_internal`의 `Fill`/`Notify`/`Order` 분기가 popped session을 쓴다. `current_session()`은 드레인 루프에서 제거. 남은 호출부(`persistent.rs` 등)가 없으면 함수도 삭제.
- [ ] **Step 3**: parity 게이트, 커밋 `refactor(rust): 큐 엔트리의 세션을 드라이버가 그대로 쓴다`.

### Task 4.2: wire 상수 export (SoT-4, DEFECT-302 Rust 절반)

- [ ] **Step 1**: `records.rs`에 `pub(crate) const RECORD_KIND_NAMES: [(&str, u8); 9] = [("market", KIND_MARKET), …, ("cost", KIND_COST)];`, `driver.rs`에 `pub(crate) const EVENT_PRIORITY_NAMES: [(&str, u8); 5]`.
- [ ] **Step 2**: `lib.rs`에서 `m.add("RECORD_KIND_CODES", PyDict)`·`m.add("EVENT_PRIORITIES", PyDict)`로 등록.
- [ ] **Step 3**: `tests/test_core_parity.py`에 `@RUST_ONLY` 테스트: `backtest_core.RECORD_KIND_CODES == {kind.value: code for kind, code in store._RECORD_KIND_CODES.items()}` (9쌍 전부), `backtest_core.EVENT_PRIORITIES == {p.name.lower(): int(p) for p in EventPriority}`. (Python 쪽 명시 코드는 PR 5에서 넣으므로 이 PR에서는 현재 `_RECORD_KIND_CODES` 그대로 비교.)
- [ ] **Step 4**: 커밋 `feat(rust): 레코드 kind 코드·이벤트 우선순위를 모듈 상수로 노출해 Python과 대조`.

### Task 4.3: `process_market_index` 공개 해제 (SoT-3)

- [x] PR 3 Task 3.2(`4eb3a77`)에서 함께 처리했다 — `#[pymethods]`에서 빼 `pub(crate)`로 옮겼고, FFI 테스트의 `dir()` 부재 단언에도 들어 있다. PR 4에서는 할 일이 없다.

### Task 4.4: no_bar 문자열 조립을 Python으로 (SoT-4')

- [ ] **Step 1**: `tape.rs::native_decision`에서 `reason`을 `tape_frame.reason.clone()` 그대로 두고 `no_bar`만 채운다. `format!("{} no_bar={}", …, py_tuple(&no_bar))` 삭제. `NativeSubmission.decision.2`(DecisionWire의 reason)에도 raw reason. `session.rs::py_tuple` 삭제.
- [ ] **Step 2**: `store.py::_decision`에서 `frame_session, kept_wires, no_bar, reason = native` 뒤 `if no_bar: reason = f"{reason} no_bar={tuple(no_bar)}"`. 이 한 줄이 `engine/tape.py::evaluate_tape`의 `reason += f" no_bar={tuple(untradable)}"`와 같은 표현식이어야 하므로 `engine/tape.py`에 `def no_bar_reason(reason: str, symbols: Sequence[str]) -> str` 를 두고 두 곳이 함께 부른다.
- [ ] **Step 3**: 라우터가 reason을 읽지 않음을 `grep -n "decision.2\|reason" persistent_router.rs`로 확인하고 PR 본문에 적는다.
- [ ] **Step 4**: `tests/test_tape.py`의 reason 단언(`"tape:d3 no_bar=('000660', '000030:1')"`)이 그대로 통과해야 한다. 커밋 `refactor(tape): no_bar 사유 포맷의 정본을 Python evaluate_tape 한 곳으로`.

### AC

- 단위·parity: 4.2 상수 대조 테스트, 큐 테스트, 기존 tape reason 단언 통과. 전체 스위트.
- 실측: 100종목 tape `--repeat 3` back-to-back으로 total 시간이 PR 3 대비 ±3% 이내 (회귀 없음 확인).
- E2E: 불필요 (reason byte 동일성은 parity 테스트가 고정, 워크벤치 계약 불변). `tests/integration` 통과 확인.

---

## PR 5 — Python SoT: RecordKind 코드·컨텍스트 base·tape 명시 옵트인 (`refactor/python-sot-context-tape-marker`, base PR 4)

**배경:** DEFECT-302 Python 절반, SoT-2, DEFECT-303.

**Files:**
- Modify: `backend/src/backtest_engine/engine/store.py`, `context.py`, `tape.py`, `loop.py`, `types/tape.py`
- Modify: `backend/src/strategy_workbench/adapters/outbound/backtest_engine/_adapter.py`
- Modify: `backend/tests/test_tape.py`, `tests/test_context.py`, `tests/test_core_parity.py`

### Task 5.1: `RecordKind` 명시 코드 (DEFECT-302)

- [ ] **Step 1**: 테스트 먼저: `tests/test_core_parity.py` 상수 대조 테스트(PR 4)가 `RecordKind.code` 속성을 읽도록 바꾼다. `RecordKind`에 멤버를 중간 삽입해도 코드가 안 바뀜을 보이는 단위 테스트는 enum을 동적으로 못 바꾸므로, 대신 `_RECORD_KIND_CODES`가 선언 순서와 무관한 리터럴임을 `store.py` 코드로 보장하고 9쌍 대조 테스트로 고정한다.
- [ ] **Step 2**:

```python
class RecordKind(Enum):
    """value는 trace 직렬화 이름, code는 Rust `records.rs` `KIND_*`와 같은 wire 코드다.

    코드는 선언 순서가 아니라 리터럴이다 — 멤버를 추가할 때 기존 코드를 바꾸지 않는다.
    Rust와의 일치는 `tests/test_core_parity.py`가 `backtest_core.RECORD_KIND_CODES`로 고정한다.
    """

    MARKET = ("market", 0)
    DECISION = ("decision", 1)
    ORDER = ("order", 2)
    ORDER_UPDATE = ("order_update", 3)
    FILL = ("fill", 4)
    SNAPSHOT = ("snapshot", 5)
    CORPORATE_ACTION = ("corporate_action", 6)  # 사건 도착 (적용 여부와 무관)
    CORPORATE_ACTION_APPLIED = ("corporate_action_applied", 7)  # 포지션에 실제 적용된 기록
    COST = ("cost", 8)  # 차입·이자 등 Fill 없는 현금 차감

    def __init__(self, label: str, code: int) -> None:
        self.label = label
        self.code = code


_RECORD_KIND_BY_CODE: dict[int, RecordKind] = {kind.code: kind for kind in RecordKind}
```

`kind.value`를 쓰던 곳(`_normalized`, `compact_trace`, 오류 메시지)은 `kind.label`로. `_RECORD_KINDS[kind_code]` → `_RECORD_KIND_BY_CODE[kind_code]`, `_RECORD_KIND_CODES[kind]` → `kind.code`. `grep -rn "RecordKind\.\w*\.value\|\.kind\.value" src tests`로 전수 교체.

- [ ] **Step 3**: 게이트, 커밋 `refactor(store): RecordKind wire 코드를 선언 순서가 아닌 명시 리터럴로 (DEFECT-302)`.

### Task 5.2: 전략 컨텍스트 공통 base (SoT-2)

- [ ] **Step 1**: `context.py`에 non-dataclass mixin을 둔다.

```python
class _DeclaredContextMethods:
    """두 컨텍스트 구현이 공유하는 조회 메서드. 서브클래스는 `now`·`snapshot`·`history_store`·
    `declared`·`universe_source`·`_open_orders_view()`를 제공한다."""

    now: datetime
    history_store: HistoryStore
    declared: frozenset[HistoryRequest]
    universe_source: UniverseResult | None

    @property
    def snapshot(self) -> PortfolioSnapshot:  # pragma: no cover — 서브클래스가 정의
        raise NotImplementedError

    def _open_orders_view(self) -> tuple[OpenOrderSnapshot, ...]:
        raise NotImplementedError

    def history(self, request: HistoryRequest) -> PriceWindow: ...   # 기존 본문 그대로 1벌
    def current_weight(...)/position_qty/cash/portfolio_value/universe/open_orders  # 기존 본문 1벌
```

`EngineStrategyContext(_DeclaredContextMethods)`는 dataclass 필드 `snapshot: PortfolioSnapshot`을 그대로 두고(`@property` 오버라이드 없이 필드가 우선한다는 점을 pyright로 확인; 충돌하면 base의 `snapshot`을 프로퍼티가 아니라 타입 선언 `snapshot: PortfolioSnapshot`로만 둔다), `_open_orders_view`는 `self.open_orders_snapshot` 반환. `RustStrategyContext(_DeclaredContextMethods)`는 `cached_property snapshot`과 `_open_orders_view → self._open_orders`.

- [ ] **Step 2**: `tests/test_context.py`에 두 컨텍스트가 `UndeclaredDataAccess`·`UniverseNotProvided`에서 같은 메시지를 내는지 단언하는 테스트 추가 (`RustStrategyContext`는 `frame`을 `SimpleNamespace(snapshot=…, open_orders=[])`로 대체).
- [ ] **Step 3**: 게이트, 커밋 `refactor(context): 두 전략 컨텍스트의 조회 메서드를 한 벌로`.

### Task 5.3: 선언형 tape 명시 옵트인 (DEFECT-303)

- [ ] **Step 1**: 테스트 먼저. `tests/test_tape.py`에 `_TapeStrategy`와 같은 네 이름을 갖지만 ABC를 상속하지 않는 `_LookAlike` 클래스를 만들어 `core="rust"`로 돌리면 `callbacks > 0`(콜백 경로)임을 단언. 현재는 실패한다(콜백 0).
- [ ] **Step 2**: `types/tape.py`의 `DeclarativeTapeStrategy`를 `Protocol`에서 `abc.ABC`로 바꾼다. `tape_frames`·`idle_reason`을 `@abstractmethod`/`@property @abstractmethod`, `requirements`·`on_event`는 그대로 추상. docstring에 "구조 일치가 아니라 상속으로만 tape 경로에 들어간다 — 우연히 같은 이름을 가진 전략의 `on_event`가 조용히 건너뛰어지지 않게"를 적는다.
- [ ] **Step 3**: `engine/tape.py::is_declarative_tape` 삭제 (pass-through). `loop.py:444`는 `isinstance(run.strategy, DeclarativeTapeStrategy)`, `loop.py:497-499` 재검사는 타입 내로잉을 위해 `strategy = run.strategy; if not isinstance(strategy, DeclarativeTapeStrategy): raise TypeError(...)`로 남기되 메시지에 `type(strategy).__name__`.
- [ ] **Step 4**: `_adapter.py::TargetTapeStrategy(DeclarativeTapeStrategy)`, `tests/test_tape.py::_TapeStrategy(DeclarativeTapeStrategy)`. `idle_reason`은 클래스 속성 그대로 두어도 ABC 프로퍼티 추상 충족 여부를 pyright·런타임으로 확인 (안 되면 `@property`로).
- [ ] **Step 5**: `test_declarative_tape_protocol_is_detected`를 `isinstance` 기반으로 바꾸고 look-alike 케이스 포함. 게이트, 커밋 `fix(tape): 선언형 tape 경로를 명시 상속 옵트인으로 (DEFECT-303)`.

### AC

- 단위·parity: 5.1 9쌍 대조, 5.2 메시지 동일 테스트, 5.3 look-alike 테스트 실패→통과. 전체 스위트.
- 실측: 불필요 (동작 불변, 콜백 경로 비용 변화 없음).
- E2E: **필요.** `TargetTapeStrategy`의 기반 타입이 바뀌므로 `uv run pytest tests/integration/test_backtest_http_api.py -q` 전부 통과 + `scripts/bench_workbench_adapter.py --instruments 100 --core all --repeat 1`로 rust 경로가 여전히 콜백 0회(`drive` 1회)로 완주하고 metrics가 python과 같음을 JSON으로 첨부.

---

## PR 6 — 결과 조회를 kind 배치 FFI로, 변환 후 Rust payload 해제 (`perf/materialize-by-kind`, base PR 5)

**배경:** PERF-1, RSS-1.27. 실측(100종목 tape): lazy 조회 0.48초 = `run()`의 1.38배. 그중 `record_payload` FFI는 0.08초, 나머지는 Python 객체 생성. `_payloads(kind)`가 kind마다 70,334 레코드 인덱스를 재스캔한다. RSS는 Rust 레코드 스토어(약 31MB)와 Python 객체가 동시에 살아 1.27배.

**Files:**
- Modify: `backend/rust/backtest_core/src/records.rs`, `persistent.rs`
- Modify: `backend/src/backtest_engine/engine/store.py`
- Modify: `backend/tests/test_core_parity.py`, `tests/test_tape.py`

### 설계 (구현 확정)

- Rust `drain_payloads(kind: u8, limit: usize) -> Vec<(u64, usize, PyObject)>`: 해당 kind의 `(seq, session_index, payload)`를 seq 순서로 `limit`개까지 넘기면서 **넘긴 자리를 그 호출 안에서 해제한다**. 빈 목록이면 그 kind는 끝이다. 종료 전에는 레코드가 더 쌓일 수 있어 거부한다. kind별 커서를 들고 있어 청크마다 처음부터 다시 훑지 않는다.
- Rust `record_payloads(kind: u8)`: 해제하지 않는 kind 단위 조회. 종료 전 partial trace 전용이다 (그 경로는 해제할 수 없다).
- `RecordPayload::Released { kind }`가 넘긴 자리를 대신하며 원래 kind를 들고 있어 `index()`가 해제 전후로 같은 행을 답한다. 넘긴 payload를 `record_payloads`·`equity_series`·`traded_notional`로 다시 읽으면 조회 이름·seq·kind를 담은 오류다. 호출 순서(`finish()` 직후 metrics → 이후 결과 조회)는 `loop.py`에서 확인하고 docstring에 명시한다. 모든 레코드를 넘기면 인덱스 Vec까지 반납하고(인덱스는 `finish()`가 이미 Python에 넘겼다) 이후 읽기 조회는 빈 답 대신 오류다 — 가져가는 호출인 `drain_payloads`만 빈 목록으로 답한다.
- Python `_payloads(kind)`: `_kind_payloads: dict[RecordKind, tuple[RecordPayload, ...]]`에 없으면 `drain_payloads(kind.code, 512)`를 빈 청크가 올 때까지 돌려 그 자리에서 공개 객체로 바꾸고 kind별 튜플로 캐시한다. seq → payload 사전은 두지 않는다 — kind별 튜플이 seq 순서이므로 `records` 프로퍼티는 인덱스를 훑으며 kind마다 커서를 하나씩 밀어 조립한다. 종료 전에는 `record_payloads(kind)`로 읽고 해제도 캐시도 하지 않는다.
- ORDER는 decision_id로 결정을 되살리므로 DECISION을 먼저 materialize해 `_decisions`를 채운다. `_decision_by_id`의 인덱스도 `record_payloads(DECISION)` 한 번으로 만든다(`_payload(seq)` 개별 호출 제거).
- 단건 `record_payload(seq)`는 삭제한다. `frame_event`는 콜백 프레임 wire를 쓰고 partial trace는 `record_payloads`를 쓰므로 Python 호출자가 남지 않는다.

**원안(kind 통째 배치 + 별도 `release_payloads`)을 쓰지 않은 이유:** 원안대로 먼저 구현해 재보니 tape peak RSS가 216.9 MiB로 **올라갔다**(같은 창에서 python 171.2 MiB 대비 1.27배, 게이트 미달). 한 kind를 통째로 받으면 그 kind의 wire tuple 전부가 Rust payload 전부·완성된 Python 객체 전부와 한순간에 같이 살아, 해제를 나중에 하든 말든 peak가 셋의 합이 되기 때문이다. 청크로 나눠 넘기면서 그 자리에서 해제하면 그 구간이 청크 크기로 묶인다. 청크 크기는 128/512/2048에서 peak RSS가 tape 208.8 / 209.0 / 210.3 MiB, callback 202.0 / 203.0 / 204.5 MiB로 1.5~2.5 MiB 안에서 평탄했고 조회 시간 차이는 측정 노이즈 범위였다 — 가운데인 512를 골랐다.

### Tasks

- [x] **6.1** Rust 테스트: `live_records_of(KIND_FILL)`가 seq 순서, 해제 후 조회가 "released" 오류, finish 전 drain 거부, 청크 경계와 kind별 커서, 전부 넘긴 뒤 읽기 조회 오류. 구현. 커밋 `feat(rust): 레코드 payload를 kind 단위로 넘기고 그 자리를 해제한다` (19a8fcb).
- [x] **6.2** Python: 위 설계대로 `store.py` 재작성. `test_promoted_rust_makes_no_per_session_ffi`가 kind당 `drain_payloads` 1회와 `record_payloads` 0회(종료 전 전용), 넘긴 뒤 재조회 오류를 고정한다. `test_tape_order_materialization_indexes_decisions_once`는 private 필드 단언 대신 kind별 호출 순서를 보는 `..._reads_each_kind_once`로 바꿨다 (리뷰 지적 5절). 커밋 `perf(store): 결과 조회를 kind 청크 FFI로 바꾸고 넘겨받은 payload를 즉시 해제` (3826246).
- [x] **6.3** 실측 후 스펙 후속 백로그 항목 갱신. 커밋 `docs(spec): 결과 조회 배치화 후 Peak RSS 재측정` (b6a4bac).
- [x] **리뷰 반영** DEFECT-601(변환 실패로 부분 해제된 kind의 재조회가 짧은 튜플을 돌려주던 것) fix와 죽은 단건 조회 삭제·주석 정리 refactor (a6c934d, 08bcbdf).

### AC

- 단위·parity: 전체 스위트, `trace_bytes()` parity 전부 통과, FFI 계측 테스트 갱신.
- 실측 (**게이트**): 코어 격리 Peak RSS(결과 조회 포함) tape·callback 모두 python 대비 **1.25배 이하**. 100종목 tape `materialize_seconds`가 PR 5 대비 30% 이상 감소. `run_seconds` 회귀 ±3% 이내.
- E2E: `tests/integration` 통과 + `bench_workbench_adapter.py` python/rust metrics 동일.

**결과:** Peak RSS 게이트 **통과** — base 커밋 JSON 기준 callback 1.23배 → 1.18배, tape 1.28배 → 1.22배. `run_seconds` 회귀 **없음**(프로세스마다 1회만 도는 측정에서 0.3559초 → 0.3535초). 조회 시간 **미달** — 같은 프로세스 A/B(각 24 표본, 최솟값)로 tape −12.4%, callback −10.3%로 목표 −30%에 못 미친다. cProfile 기준 FFI는 조회 시간의 약 4%(청크 조회 0.028초/90회)뿐이고, 스냅샷 1,225개가 만드는 `Position` 123,625개가 약 65%다. 남은 비용은 두 코어가 같이 무는 Python 객체 생성이라 FFI 경계로는 줄지 않는다 — 스펙 후속 백로그에 레코드 메모리 레이아웃(`Vec<NativeRecord>` 인라인 슬롯 약 14 MB)과 함께 남겼다.

한 프로세스에서 `--repeat`으로 반복하면 `run_seconds`가 3~7% 느려 보이는데, 직전 회차의 조회가 실제로 메모리를 반납해 다음 회차가 페이지를 다시 폴트하기 때문이다. 프로세스마다 1회만 도는 측정에서 사라지므로 엔진 회귀가 아니라 벤치 하네스의 회차 간 간섭이다.

---

## PR 7 — 워크벤치 결과를 columnar 접근으로 (`perf/workbench-result-columnar`, base PR 6)

**배경:** PERF-2/3/4. 어댑터 `_adapter.py:176-215, 293-333`은 `result.snapshots`의 모든 `Position`(123k)을 `RawPosition`으로, `result.orders`/`fills`를 `RawOrder`/`RawFill`로 다시 옮기고 fills를 세 번 합산한다. 엔진 공개 객체(`Position`·`PortfolioSnapshot`·`OrderEvent`·`FillEvent`)는 중간 산물일 뿐이다. 리뷰 실측: rust 경로에서 결과 변환 합계가 `engine.run`의 1.46배.

**Files:**
- Create: `backend/src/backtest_engine/types/result_tables.py` (`SnapshotRow`, `PositionRow`, `OrderRow`, `FillRow`, `FillTotals` frozen dataclass; 필드는 `Raw*`가 요구하는 것과 동일한 primitive)
- Modify: `backend/src/backtest_engine/engine/store.py` (`EventStore.result_tables() -> ResultTables`, `PersistentEventStore` 오버라이드), `types/results.py`(`BacktestResult.tables: Callable` lazy 추가 여부는 설계 결정으로), `rust/backtest_core/src/records.rs`·`persistent.rs`(`snapshot_rows()`, `position_rows()`, `order_rows()`, `fill_rows()`, `fill_totals()`)
- Modify: `backend/src/strategy_workbench/adapters/outbound/backtest_engine/_adapter.py` (`_artifacts`, `AnalysisPoint`, `AnalyticsInput` 합계가 tables를 쓴다)
- Modify: `backend/src/backtest_engine/engine/store.py::order_from_wire` (enum 조회 dict 캐시, decision→actions 캐시)

### 설계 결정 (PR 본문에 그대로)

- `ResultTables`는 엔진 공개 계약이며 python·persistent 두 store가 같은 값을 낸다. python store는 기존 객체에서 파생, persistent store는 Rust에서 바로 받는다. 합계(`traded_notional`, `total_fees`, `total_slippage_cost`, 세션별 `positions_value`)는 Python `sum(...)`과 같은 좌→우 결합 순서로 Rust가 누산한다 (`0.0 + x0 + x1 …`; Python `sum`의 시작값 int 0은 첫 항에서 float으로 승격되어 결과가 같다). 이 동일성은 `tests/test_core_parity.py`에서 python/rust `result_tables()` 전체 동등으로 고정한다.
- 어댑터는 `result.snapshots/orders/fills`를 더는 읽지 않는다. `RawPosition.quantity=str(position.quantity)`는 `str(Decimal(int))`와 `str(int)`가 같으므로 `str(row.quantity)`.
- `_slice_analytics`·`TradeOutcome` 재구성이 `FillEvent`를 요구하면 그 부분만 `FillRow`로 옮긴다. 옮길 수 없는 필드가 있으면 이 PR에서 `fills`만 남기고 PR 본문에 사유를 적는다.

### Tasks

- [x] **7.1** `result_tables.py` 타입과 python `EventStore.result_tables()` (기존 객체에서 파생) + 테스트 (`tests/test_engine_golden.py` 패턴으로 golden 값).
- [x] **7.2** Rust `result_tables()` + persistent store 오버라이드 + parity 테스트(python/rust `result_tables()` ==). 계획의 `*_rows()`/`fill_totals()` 다섯 pymethod 대신 레코드 한 번 순회로 다섯 벡터와 합계를 한꺼번에 답하는 pymethod 하나로 합쳤다 — kind마다 레코드를 다시 훑지 않는다.
- [x] **7.3** 어댑터 전환. `tests/integration/test_backtest_http_api.py::test_python_reference_and_rust_core_have_golden_result_and_metric_parity`가 그대로 통과한다 (artifacts·series·metrics 동일).
- [x] **7.4** PR 8로 넘긴다. `order_from_wire`/`fill_from_wire`는 죽지 않았다 — `open_orders_from_wire`가 세션마다 미체결 주문 수만큼, `frame_event`가 fill 콜백마다 부르는 hot loop 경로다. 다만 그 낭비의 뿌리(tape 프레임 snapshot·open_orders 조회)가 PR 8 범위와 겹쳐 그쪽에서 함께 처리한다.
- [x] 커밋 5개, 기능 커밋의 마지막이 `perf(workbench): 결과 아티팩트를 엔진 columnar 테이블에서 직접 만든다`.

### AC

- 단위·parity: `result_tables()` python/rust 동등 테스트, golden 테스트, 전체 스위트.
- 실측 (**게이트**): `bench_workbench_adapter.py --instruments 100 --core all --repeat 3`에서 rust 경로 `engine.run` 이후 구간 합계가 PR 6 대비 50% 이상 감소하고, e2e total 배수(python/rust)가 PR 1 기준선(약 2.0배)보다 커진다. 수치를 PR 본문 표로.
- E2E (**필수**): `tests/integration/test_backtest_http_api.py -q` 전부 통과. `bench_workbench_adapter.py` JSON에서 두 코어 metrics·series 동일. 프론트 e2e(`browser-e2e` CI job)는 PR CI에서 확인.

**결과:** 단위·parity·E2E **통과** — `result_tables()` 동등을 시나리오 27개 × rust 코어 3종으로 고정했고 `tests/integration` 163건이 전부 통과한다 (HTTP golden parity가 두 코어 artifacts 동등을 단언). 실측 게이트는 **세션 잡음 폭 안에서 50% 경계**다. 같은 장비·같은 빌드에서 base의 어댑터·벤치 스크립트만 되돌려 back-to-back으로 잰 repeat 7 짝(`engine.run` 0.4074초 → 0.4117초로 두 창의 속도가 같다)에서 `engine.run` 이후 합계 1.1679초 → 0.6057초로 **−48.1%**를 기록했고, 리뷰어 재현은 −50.1%다. 다섯 번의 짝 측정이 48.1 / 49.2 / 49.9 / 51.9 / 52.1%, 각자의 `engine.run`으로 정규화한 중앙값이 −49.0%다.

구간별(rust, repeat 7, 초): 결과 조회 0.5285(`result_materialize`) + 0.0004(`event_store_costs`) → 0.0331(`result_tables`)로 **−93.7%**. 이 PR이 옮긴 경계가 여기다. `artifacts` 0.5560 → 0.5110, `analysis_points` 0.0208 → 0.0011. e2e 배수(python/rust)는 1.348배 → 1.915배이고, python 총시간이 같은 회차에 함께 느려진 몫을 빼고 before의 python 3.2041초를 기준으로 재면 1.746배다. 어느 쪽이든 PR 1 기준선을 넘는다.

남은 `artifacts` 0.51초의 84%가 워크벤치 도메인 모델 객체 생성이다 (`RawPosition` 122,595개, frozen dataclass 생성자만 행당 약 1.06µs). 측정해 보고 기각한 미세 최적화: positional 생성자 인자는 7필드 레코드의 인자 이름을 지우는 대가로 구간의 3.5%, list comprehension은 잡음 범위, `str(quantity)`는 positions 루프의 7%다.

`Raw*` 여섯 클래스에 `slots=True`를 붙이는 안도 구현해 재고 **되돌렸다**. 단일 코어 rust A/B를 교대로 4회(`--repeat 5`) 돌려 `engine.run`으로 정규화하면 `artifacts/engine.run`이 1.024(무 slots) → 1.053(slots)으로 **약 3% 느리다**. 같은 프로세스 안에서 slots 유무만 다른 dataclass를 비교한 micro도 생성이 14% 느렸다 — frozen dataclass는 어느 쪽이든 `object.__setattr__`를 타는데 CPython 3.11의 key-sharing dict 경로가 slot descriptor보다 빨라서다. 대신 **메모리는 줄었다**: 인스턴스당 184 → 88바이트(−52%), 프로세스 peak RSS 372.7MiB → 355.6MiB(−4.5%). 시간 게이트(3% 이상 이득) 미달이라 이 PR에서는 되돌리고, RSS 관점의 판단은 PR 11로 넘긴다.

**python 코어는 e2e 약 10% 느려졌다** (3.2041초 → 3.5142초, `engine.run` 이후 0.6795초 → 0.8434초). python 코어는 공개 객체를 이미 갖고 있어 테이블 생성이 순수 추가 패스이고, 그 비용 0.2206초가 artifacts·analysis_points에서 아낀 0.074초보다 크다 (정수 수량 검사는 그중 0.030초뿐이고 나머지는 패스 자체). 어댑터가 코어별로 분기하는 대안보다 낫다고 판단해, 프로덕션 경로인 rust가 객체 생성을 통째로 건너뛰도록 **참조 코어가 치르는 비용**으로 받아들였다.

### 설계 이탈 (PR 본문에 그대로)

1. `AnalysisPoint`를 snapshot 행에서 다시 만들지 않고 `artifacts.snapshots`에서 만든다. net exposure 공식이 `execute`와 `_artifacts` 두 곳에 있던 중복을 `_artifacts` 하나로 합친다. bit 동일하고 `analysis_points` 구간이 0.0208초 → 0.0011초가 됐다.
2. `_closed_trades`가 `tables` 외에 미리 만든 `session_dates`·`security_ids`를 함께 받는다. `_artifacts`가 이미 만든 조회표를 다시 만들지 않기 위해서다.
3. Rust 해제 가드를 kind별로 건다. 테이블이 읽는 SNAPSHOT/ORDER/FILL/COST가 해제됐을 때만 오류이고, DECISION/MARKET을 먼저 공개 객체로 만든 뒤에도 테이블 조회는 답한다.

벤치 스크립트는 코어별 `peak_rss_bytes`를 JSON에 남긴다 (`--core all`은 프로세스 peak가 코어별로 갈라지지 않아 null + `rss_isolated: false`). PR 11의 워크벤치 RSS 재판정 입력이다. 스펙 문서는 PR 11에서 일괄 갱신한다.

리뷰 조건부 APPROVE (정확성 결함 0, Minor 1건). DEFECT-701(중단된 실행에서 persistent가 feed 전체를 답해 python 코어와 `sessions`·`instruments`가 갈림)은 `fix(engine): 중단된 실행의 결과 테이블 세션·종목을 레코드 구간으로 자른다`로 반영했다 — `sessions` 계약의 정본을 feed 길이가 아니라 레코드로 고치고 partial trace parity 테스트를 추가했다.

---

## PR 8 — Rust hot loop 할당·탐색 제거 (`perf/rust-hot-loop`, base PR 7)

**배경:** PERF-5/6, SoT-6. 실측 A/B(300종목, 저부하): `row_of` O(1) 인덱스만으로 callback run −6.7%, tape −1.3%. 나머지는 코드 읽기 추정. `drive`가 100→300종목에서 선형(3.06배)이므로 라우터·원장 O(U²) 탐색은 이 규모에서 지배적이지 않다 — 구현하되 측정으로 유지 여부를 정한다.

**Files:** `driver.rs`, `feed.rs`, `tape.rs`, `callback.rs`, `persistent_router.rs`, `portfolio.rs`

### Tasks (각각 별도 커밋, 각 커밋 후 parity 게이트)

- [x] **8.1** `route_basic_decision(decision: &DecisionWire)` — 라우터는 `decision.3`을 `iter()`로만 읽는다. `driver.rs`의 `decision_for_orders = decision.clone()` 삭제. `StoredOrder::from_routed`는 이미 `&DecisionWire`를 받는다.
- [x] **8.2** `PersistentFeed::new`가 `symbol_by_key: HashMap<String, String>`을 한 번 만들고 `registry_symbols()`가 그 참조를 빌려준다. 결정별 재조립과 bars 병합을 없앴다 — bars의 심볼은 같은 `symbols` 배열을 같은 instrument id로 읽으므로 등록부와 항상 같고, 같은 key가 다른 symbol로 두 번 등록되는 경우만 `new`가 거부한다(Rust 테스트 `registry_rejects_one_key_with_two_symbols`).
- [x] **8.3** tape 경로 경량 프레임 — 구현해 재고 **되돌렸다**. 아래 "8.3을 되돌린 이유" 참고.
- [x] **8.4** `feed.rs::row_of` O(1): `row_index: Vec<u32>`(세션 × 종목, 없으면 `u32::MAX`)를 적재 시 채우고 `has_bar`·`open_at`·`history_window`·`settlement_session_index`가 쓴다. 세션별 `HashMap<u32, usize>` 생성이 사라졌다. 행 수가 u32를 넘거나 인덱스 크기가 usize를 넘으면 적재를 거부한다.
- [x] **8.5** `on_market`·`on_session_close`의 `settings()?.clone()` 제거 — `RunSettings`를 `Arc`로 감싸 `settings_arc()`가 참조 카운트만 올린다. `slippage`는 읽기 전용이라 `process_market_index`·`process_market_values`·`process_market_impl`이 참조로 받는다.
- [x] **8.6** (PR 7 Task 7.4 이관) 콜백 프레임 wire 변환 — **이미 lazy라 변경 없음**. `RustStrategyContext._open_orders`·`snapshot`이 `functools.cached_property`이고 `frame_event`의 market 분기는 미리 만든 `MarketSnapshot`을 돌려준다. 계측 실행(50종목 1,231세션)에서 `open_orders_from_wire` 호출 0회, `snapshot_from_wire` 1,231회(전략이 실제로 읽는 값), `order_from_wire`·`_decision_by_id` 11,763회(모두 `run()` 밖 결과 조회 구간). `_decision_by_id`는 `_decisions` dict에 캐시하므로 결정당 한 번만 재구성한다 — 추가 캐시 불필요.
- [x] **8.7** `RouteContext::position_index: HashMap<&str, usize>`(결정 시작 시 1회)로 `held`/`market_value` O(1), `Portfolio::ledger_slots: HashMap<String, usize>`로 `ledger_index` O(1). 삽입 순서 정본은 `ledgers` Vec 그대로고 표는 위치만 따라간다.
- [x] **8.8** `current_marks`가 등록부 문자열을 빌려주고 `Portfolio::mark_refs`가 `get_mut`으로 값만 갱신한다 — 세션마다 종목 수만큼 나던 key 할당 제거. Python legacy 경로의 `mark`는 그대로.

### 항목별 A/B (300종목, `--repeat 9` 표본 최소값, 부하 ≤30%, 초)

측정은 누적이다 — 각 행은 그 항목까지 적용한 빌드다.

| 항목 | callback | tape | 판정 |
| --- | --- | --- | --- |
| base (`fa4289c`) | 1.041 | 1.008 | — |
| 8.1 `decision.clone()` 제거 | 1.017 | 0.997 | 유지 |
| 8.2 심볼 폴백 표 1회 구축 | 0.929 | 0.923 | 유지 |
| 8.3 tape 경량 프레임 | (0.960) | (0.928) | **되돌림** |
| 8.4 `row_of` O(1) | 0.941 | 0.925 | 유지 |
| 8.5 `settings()?.clone()` 제거 | 0.950 | 0.934 | 유지 |
| 8.6 콜백 wire 변환 | — | — | 확인만 (변경 없음) |
| 8.7 라우터·원장 인덱스 | 0.812 | 0.793 | 유지 |
| 8.8 `current_marks` clone 제거 | 0.807 | 0.787 | 유지 |

**남은 할당원 (PR 9 검토):** `Portfolio::snapshot()` 세션당 4회 key clone(약 148만 String), `current_closes()` 74만, `session_market()` 37만. 셋 다 PR 8 범위 밖이고 상세는 아래 AC 절 "남은 레버"에 있다.

이 장비의 측정 잡음이 ±3~5%라 8.1·8.4·8.5·8.8은 단독으로는 오차 범위 안이다. 셋 다 자료구조를 늘리지 않고 일을 덜어내기만 하므로(8.4만 1.5MiB 인덱스를 더한다) 되돌리지 않았고, 대신 아래 back-to-back 측정으로 합산 효과를 고정했다. 8.2와 8.7만 단독으로 잡음을 넘는다.

#### 8.3을 되돌린 이유

`make_native_frame`(snapshot 빈 값·open_orders 빈 Vec)을 만들어 tape 경로에 붙였는데 300종목에서 callback·tape 둘 다 오차 범위였다. 이 벤치가 실행하는 workload에서는 원리상 아낄 것이 없다.

1. 두 전략 모두 `requirements().events`에 `MARKET`만 선언한다. `notify_fill`·`notify_order_update`·`notify_corporate_action`이 모두 false라 NOTIFY 분기 자체가 한 번도 돌지 않는다 — `make_native_frame`이 아끼려던 `snapshot_wire()` 호출이 없다.
2. SESSION_CLOSE 분기의 snapshot은 `on_session_close`가 SNAPSHOT 레코드용으로 이미 만든 값을 넘겨받는다. 남는 낭비는 `open_orders_wire()`뿐인데, 시장가 주문이 다음 세션 MARKET에서 전량 체결되므로 세션 마감 시점의 `self.orders`는 사실상 비어 있다.

즉 이 항목은 **fill·order_update 알림을 선언한 tape 전략**에서만 값을 한다 (체결마다 포지션 수에 비례하는 `snapshot_wire()`가 붙는다). 벤치가 그 형태를 재현하지 못해 측정으로 유지를 정당화할 수 없어 되돌렸다. 알림 선언 tape workload를 재는 벤치 옵션이 생기면 다시 올릴 항목이다.

### AC

- 단위·parity: 전체 스위트, 세 코어 signature 동일.
- 실측 (**게이트**): 300종목 callback·tape `run_seconds`가 PR 7 대비 **10% 이상 감소**(같은 세션 back-to-back, `--repeat 5` 중앙값). Peak RSS 회귀 없음(`row_index` 포함해도 tape 1.25배 유지).
- E2E: `tests/integration` 통과 (계약 불변).

**결과:** 게이트 **통과**. 단위·parity는 `cargo test` 32건과 `uv run pytest -q` 1,307건 통과, 세 코어 signature가 base와 동일하다(300종목 orders 52,344 / fills 52,121 / final_equity 2,402,131,747, 100종목 22,243 / 22,155 / 2,321,985,874). E2E는 `tests/integration/test_backtest_http_api.py` 6건 통과와 `bench_workbench_adapter.py --instruments 100 --core all --repeat 1` 완주.

실측은 같은 장비에서 `fa4289c`와 이 브랜치 tip을 번갈아 빌드해 back-to-back으로 쟀다(`--repeat 9`, 매 세트 전 CPU 부하가 연속 3회 30% 이하일 때만 실행).

| 세트 | base 중앙값 | head 중앙값 | Δ | base 최소 | head 최소 | Δ | base peak RSS | head peak RSS | Δ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 300종목 callback | 1.0603 | 0.8349 | **−21.3%** | 1.0408 | 0.7997 | −23.2% | 446.4MiB | 447.1MiB | +0.16% |
| 300종목 tape | 1.0259 | 0.7963 | **−22.4%** | 1.0080 | 0.7707 | −23.5% | 449.1MiB | 449.8MiB | +0.16% |
| 100종목 callback | 0.3502 | 0.2983 | −14.8% | 0.3398 | 0.2906 | −14.5% | 205.4MiB | 206.0MiB | +0.29% |
| 100종목 tape | 0.3324 | 0.2914 | −12.3% | 0.3261 | 0.2813 | −13.7% | 210.2MiB | 209.9MiB | −0.14% |

Peak RSS는 `--core rust` 단독 실행값이라 격리돼 있다. `row_index`가 더하는 4B × 세션 × 종목(300종목 1,231세션 = 1.5MiB)이 여기 들어 있고, 회귀는 +0.29% 이하로 게이트(+1%) 안이다.

### 남은 레버 (PR 9 이후로)

이 PR 범위 밖이라 손대지 않았지만 측정 중 드러난 것:

1. **`Portfolio::snapshot()`이 세션당 네 번 불리고 매번 포지션 key를 전부 clone한다** (`process_market_values` 1회, `close_current_session` 2회, `route_basic_decision` 1회). 300종목 1,231세션이면 약 148만 건이고 대부분 즉시 버려진다 — 호출자 넷 중 셋이 key를 instrument id로 바꾸거나 인덱스 키로만 쓴다. 빌려주는 `snapshot_rows()`를 따로 두면 되지만 `SnapshotTuple`·`BuyingPower`·`snapshot_wire_from`·라우터를 함께 건드려야 해 별도 PR이 맞다.
2. **`feed.current_closes()`가 결정마다 key·symbol을 clone한다** (600건 × 1,231 = 약 74만 건). `HashMap<&str, (&str, f64)>`로 빌려주면 되지만 `RouteContext.bars` 타입과 `route_basic_decision` 시그니처가 바뀐다.
3. **`feed.session_market()`이 세션마다 `HashMap<String, BarTuple>`을 key clone으로 만든다** (약 37만 건). Python에 노출된 `process_market` 경로와 타입을 공유해 바꾸려면 session.rs까지 이어진다.

---

## PR 9 — 레코드·큐 메모리 레이아웃 (`perf/record-and-queue-memory`, base PR 8)

**배경:** MEM-1/2. `RecordPayload`가 `OrderWire`(String 6개) 크기로 고정되어 70,334 레코드 × 약 248B ≈ 17MB, `Queued` 46,860 슬롯 × 240B ≈ 11MB가 `finish()`까지 상주.

### Tasks

- [ ] **9.1** `RecordPayload::Order(Box<OrderWire>)`, `Fill(Box<FillWire>)`, `Snapshot(Box<SnapshotWire>)`, `Decision { native: Option<Box<NativeDecision>> }`. `std::mem::size_of::<RecordPayload>()`를 단언하는 Rust 테스트(≤ 48B).
- [ ] **9.2** 큐 arena를 free-list slab으로: `queued: Vec<Option<Queued>>` + `free: Vec<usize>`. `push`는 `free.pop()` 슬롯 재사용, `pop`은 `take` 후 `free.push(token)`. heap 엔트리의 `seq`가 순서를 보장하므로 token 재사용은 안전(같은 token이 heap에 두 번 있을 수 없음 — pop 후에만 free). Rust 테스트: 1,000세션 MARKET pre-push 후 슬롯 수가 `sessions + max_live_per_session` 이하.
- [ ] **9.3** `row_index` 메모리 상한 — PR 8이 더한 `Vec<u32>`(세션 × 종목)는 usize 오버플로만 막고 크기 자체는 무제한이다 (3,000종목 × 5,000세션 = 60MB). `slots > rows × K`면 세션별 해시 폴백으로 내려가거나, 바이트 예산을 넘으면 적재 오류로 거부한다. K와 예산은 실측(300종목 1.5MiB / 행 369,300 = 밀도 약 0.8%)으로 정한다.
- [ ] **9.4** 측정 후 커밋 정리.

### AC

- 단위·parity: 전체 스위트.
- 실측 (**게이트**): 코어 격리 Peak RSS tape·callback이 PR 8 대비 감소, `run_seconds` ±3%.
- E2E: 불필요 (내부 레이아웃).

---

## PR 10 — feed columnar 적재 (`perf/feed-columnar`, base PR 9) — go/no-go 게이트 있음

**배경:** PERF-7. `_load_persistent_feed`가 `Bar` 123,100개를 6개 comprehension으로 훑어 `run()`의 10~15%. 계획 2-5(NumPy columnar 1회 전송) 미적용. 어댑터는 `request.dataset.bars`에서 `Bar` 객체를 만든 뒤 `DataFeed(bars)`에 넣으므로 진짜 이득은 "`Bar` 객체 생성 자체를 건너뛰기"에서 나온다.

### Go/No-Go

PR 9까지 반영 후 100종목 tape에서 feed 적재(`_load_persistent_feed` + `DataFeed.__init__`)가 e2e total의 **10% 미만이면 이 PR은 열지 않고** 이슈 #98 댓글에 수치와 함께 "보류"로 기록한다. 10% 이상이면 진행.

### 설계

- `DataFeed.from_columns(sessions: Sequence[datetime], instruments: Sequence[InstrumentId], offsets, instrument_ids, opens, highs, lows, closes, volumes)` 생성자 추가. 내부는 columnar 저장, `snapshots()`·`sessions`는 기존 계약을 lazy로 만족(`MarketSnapshot`은 python 코어와 MARKET 레코드 side table이 필요할 때 세션 단위로 생성·캐시). `DataFeed(bars)`는 유지.
- `_load_persistent_feed`는 columnar면 배열을 그대로 넘기고(numpy `float64`/`int64` 배열 → pyo3 `PyReadonlyArray`는 새 의존성 `numpy` crate가 필요하므로 **금지**; 대신 `array.array('d')`의 buffer를 `Vec<f64>`로 받는 pyo3 `Vec<f64>` 변환 vs `bytes` 변환을 측정해 빠른 쪽), `Bar` 기반이면 기존 경로.
- 어댑터는 `request.dataset.bars`에서 바로 columnar를 만든다.

### AC

- 단위·parity: `DataFeed.from_columns`와 `DataFeed(bars)`가 같은 `snapshots()`를 내는 테스트, `TimeReversalError` 등 검증 동일, 전체 스위트.
- 실측: 100종목 tape e2e total에서 feed 구간 50% 이상 감소.
- E2E (**필수**): `tests/integration` 통과, `bench_workbench_adapter.py` metrics 동일.

---

## PR 11 — 최종 재측정·문서·이슈 종료 (`docs/rust-loop-final-gates`, base PR 10 또는 PR 9)

- [ ] "벤치 측정 표준" 전부 재실행 (부하 40% 이하), JSON 12개 갱신.
- [ ] 스펙 "2026-09-18 측정 경계 교정" 아래 "최종 판정" 절: 게이트 표(100/300종목 total 배수, 4종목 fixture, RSS, FFI 0회, parity) + 남은 항목.
- [ ] `docs/rust-python-benchmark-report.html` 갱신, `docs/superpowers/plans/2026-09-17-rust-engine-loop.md` 상단에 이 문서 링크.
- [ ] 이슈 #98 댓글: PR 링크 11개, 최종 표, `.claude/rules/pr-review.md` 양식으로 남은 결정(Phase 3-4). 게이트 전부 통과면 종료 제안.
- [ ] 이슈 #98 댓글에 후속 항목으로 남길 것: PR 8에서 되돌린 tape 경량 프레임(8.3)은 FILL/ORDER_UPDATE 알림을 선언한 tape 워크로드 벤치 옵션이 생기면 다시 올린다. 코어 간 instrument key 충돌 거부 통일(현재 persistent만 거부, python 코어는 완주). #135(`7E+2` 수량 표기).
  - [ ] PR 8에서 되돌린 tape 경량 프레임(8.3) — 알림(fill·order_update) 선언 tape 워크로드를 재는 벤치 옵션이 생기면 `make_native_frame`을 다시 올린다. 현재 벤치는 `MARKET`만 선언해 NOTIFY 분기가 돌지 않아 측정으로 유지를 정당화할 수 없었다.
- [ ] 메모리 `rust-loop-driver-pr-stack.md` 갱신.

### AC

- 실측: 표의 모든 수치가 이 PR 커밋 시점 JSON과 일치.
- E2E: CI(backend·frontend·browser-e2e) 통과.

---

## 검증 명령 (각 Task 종료 시)

"공통 규칙 > 게이트" 참조. Rust를 건드린 Task는 `maturin develop --release` 후 Python 게이트를 돌린다. 리뷰어 서브에이전트에게는 이 문서의 해당 PR 절, 이슈 #98, `git diff <base>...HEAD`를 준다.

## 실행 기록

| PR | 브랜치 | 상태 | PR 링크 | 리뷰 |
|---|---|---|---|---|
| 1 | `fix/bench-honest-boundary` | 리뷰 APPROVE·PR 생성 | #123 | Opus APPROVE (A01·A02 반영) |
| 2 | `fix/driver-failed-lifecycle` | 리뷰 APPROVE·PR 생성 | #124 | Opus APPROVE (권고 3건 반영) |
| 3 | `refactor/drop-dead-persistent-api` | 리뷰 APPROVE·PR 생성 | #127 | Opus APPROVE (Minor 3건 반영) |
| 4 | `refactor/rust-owns-wire-constants` | 리뷰 APPROVE·PR 생성 | #128 | Opus APPROVE (warmup 단일화·따옴표 parity 반영) |
| 5 | `refactor/python-sot-context-tape-marker` | 리뷰 APPROVE·PR 생성 | #129 | Opus APPROVE (issubclass 고정 반영) |
| 6 | `perf/materialize-by-kind` | 리뷰 APPROVE·PR 생성 | #134 | Opus APPROVE (DEFECT-601 반영, RSS 1.18/1.22배 통과) |
| 7 | `perf/workbench-result-columnar` | 리뷰 조건부 APPROVE·PR 생성 | #136 | Opus (DEFECT-701·캐시·RSS 계측 반영, post-run −48%(경계), e2e 1.26→1.91배) |
| 8 | `perf/rust-hot-loop` | 대기 | | |
| 9 | `perf/record-and-queue-memory` | 대기 | | |
| 10 | `perf/feed-columnar` | go/no-go 대기 | | |
| 11 | `docs/rust-loop-final-gates` | 대기 | | |
