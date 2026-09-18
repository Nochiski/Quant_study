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

- [ ] **Step 1**: `make_portfolio`의 `PERSISTENT_RUST_CORES` 분기를 다음으로 바꾼다.

```python
    if core in PERSISTENT_RUST_CORES:
        raise CoreUnavailable(
            f"core={core!r} owns its portfolio inside PersistentEngine — "
            "use BacktestEngine(core=...) for engine runs, or core='rust_legacy' for the "
            "standalone backtest_core.Portfolio adapter"
        )
```

- [ ] **Step 2**: `core.py:249-403` `PersistentPortfolio` 클래스와 그것만 쓰는 import를 삭제한다. `PersistentOrderManager`가 남아 있으면 같이 확인해 삭제.
- [ ] **Step 3**: 테스트 이전. `tests/test_core_parity.py::RUST_ENGINE_CORES`를 쓰는 `test_portfolio_scenario_identical_across_cores`는 파라미터를 `["rust_legacy"]`로 좁힌다(엔진 코어 parity는 이미 trace 테스트가 덮는다). `core` fixture를 쓰는 `test_portfolio_errors_map_to_domain_exceptions`는 `["python", "rust_legacy"]`. `:420-421`, `:434`(DEFECT-603)과 `tests/test_lookup_index.py:93,175`의 `"rust"` → `"rust_legacy"`. `test_unavailable_core_is_an_error_not_a_fallback` 옆에 `make_portfolio("rust")`가 `CoreUnavailable`을 내는 테스트를 추가한다.
- [ ] **Step 4**: 게이트, 커밋 `refactor(engine): 엔진 경로에서 도달 불가한 PersistentPortfolio 제거`.

### Task 3.2: Rust 죽은 `#[pymethods]` 제거

- [ ] **Step 1**: Python 호출부 0개를 다시 확인한다.

```bash
for m in place_order register_group open_order_states open_group_states drop_group remove_order settle_order mark_triggered drain_orders next_decision_id next_order_id next_fill_id next_group_id activate_pending record_count failure_detail process_market apply_fill charge apply_corporate_action mark cash held_qty average_price portfolio_snapshot mark_current_session close_current_session current_session_count; do echo "$m: $(grep -rn "runtime\.$m(\|_inner\.$m(\|\.inner\.$m(" src tests scripts --include=*.py | wc -l)"; done
```

- [ ] **Step 2**: 0개인 메서드를 `#[pymethods]` 블록에서 제거한다. 드라이버가 내부에서 쓰는 것(`close_current_session`, `activate_pending_internal`, `apply_corporate_action_ratio`, `process_market_index`)은 일반 `impl PersistentEngine` 블록의 `pub(crate)`로 옮긴다. Rust 단위 테스트가 쓰는 것(`persistent.rs` `mod tests`의 `place_order`·`process_market` 등)은 `#[cfg(test)]` 헬퍼로 내리거나 테스트를 드라이버 경유로 고친다. `failure_detail` getter는 `tests/test_core_parity.py:931,1040`이 읽으므로 유지. `lifecycle_state`, `_debug_force_panic_on_market` 유지.
- [ ] **Step 3**: `tests/test_core_parity.py::test_promoted_rust_makes_no_per_session_ffi`의 "0회" 이름 목록에서 삭제된 이름을 빼고, 대신 `set(dir(proxies[0].inner))`에 삭제된 이름이 없음을 단언하는 줄을 추가한다 (공개 API 축소 고정).
- [ ] **Step 4**: 게이트, 커밋 `refactor(rust): Python 호출부가 없는 PersistentEngine 공개 메서드 제거`.

### Task 3.3: Python 정리

- [ ] `loop.py:143-159`: persistent 분기에서 `self.order_manager = OrderManager()` 제거, 타입을 `OrderManager | None`으로 하고 python 경로 진입 시 `None` 검사(`_ledger` 패턴처럼 `_order_manager(run)` 헬퍼). `run.queue`·`run.corporate_actions`도 persistent에서 만들지 않는다면 같은 처리.
- [ ] `store.py:542-547` `compact_trace`를 `(seq, session_index, kind)` 3-튜플로 바꾸고 호출부(`tests/`)를 맞춘다.
- [ ] `wire.py:270-273` `route_error` 삭제, 호출부 `loop.py`에서 `if error_wire is not None: raise route_error_from(error_wire)`.
- [ ] `loop.py:432,583` `tuple(feed.snapshots())` 이중 생성: `DataFeed`에 `snapshot_tuple` 속성이 없다면 `_load_persistent_feed`가 `(instruments, snapshots)`를 돌려주게 해 한 번만 만든다.
- [ ] `types/instruments.py:36`: `return self._hash  # pyright: ignore[reportAttributeAccessIssue]  # reason: __post_init__이 object.__setattr__로 채우는 캐시 필드`. 실제 pyright 룰명은 실행해 확인.
- [ ] `tests/test_target_tape_strategy.py:78-113` 두 테스트를 "어댑터는 `evaluate_tape`에 위임하고 `target_tape:<iso>` 접두어·`idle_reason`만 붙인다"를 검증하는 한 테스트로 줄인다 (no_bar 규칙 자체는 `tests/test_tape.py`가 소유).
- [ ] 커밋 `refactor(engine): persistent 경로 죽은 코드·규칙 위반 정리`.

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

- [ ] `#[pymethods]`에서 빼고 `pub(crate)`만 남긴다. FFI 테스트 이름 목록에서 제거하고 `dir()` 부재 단언에 추가. 커밋 `refactor(rust): process_market_index를 드라이버 내부 API로`.

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

### 설계

- Rust `record_payloads(kind: u8) -> Vec<(u64, usize, PyObject)>`: 해당 kind의 `(seq, session_index, payload)`를 seq 순서로 한 번에 돌려준다. 종료 전(partial trace)에도 동작.
- Rust `release_payloads(kind: u8)`: 해당 kind의 payload를 `RecordPayload::Released`로 바꿔 메모리를 해제한다. `finished == false`면 오류. `equity_series`·`traded_notional`은 `Released`를 만나면 오류(호출 순서는 Python이 `finish()` 직후 metrics 계산으로 고정돼 있음을 `loop.py`에서 확인하고 docstring에 명시).
- Python `_payloads(kind)`: `_materialized_kinds: set[RecordKind]`에 없으면 `record_payloads(kind.code)`로 받아 전부 materialize해 `_materialized[seq]`에 넣고, `_finished_batch is not None`이면 `release_payloads`. `records` 프로퍼티는 모든 kind에 대해 `_payloads`를 부른 뒤 인덱스 순서로 `Record`를 만든다. `_decision_by_id`의 인덱스도 `record_payloads(DECISION)` 한 번으로 만든다(`_payload(seq)` 개별 호출 제거).
- `record_payload(seq)`는 `frame_event`·partial trace용으로 유지.

### Tasks

- [ ] **6.1** Rust 테스트: `record_payloads(KIND_FILL)`가 seq 순서, `release_payloads` 후 `payload(seq)`가 "released" 오류, finish 전 release 오류. 구현. 커밋 `feat(rust): kind 단위 레코드 payload 배치 조회와 해제`.
- [ ] **6.2** Python: 위 설계대로 `store.py` 재작성. `tests/test_core_parity.py::test_promoted_rust_makes_no_per_session_ffi`에 `calls["record_payloads"] == 1`(fills 조회 1회), `calls["record_payload"] == 0` 추가. `tests/test_tape.py::test_tape_order_materialization_indexes_decisions_once`를 private 필드 단언 대신 "`record_payloads` 호출 수가 kind 수 이하"로 바꾼다 (리뷰 지적 5절). 커밋 `perf(store): 결과 조회를 kind 배치 FFI로 바꾸고 변환한 Rust payload를 해제`.
- [ ] **6.3** 실측 후 스펙 후속 백로그 항목 갱신.

### AC

- 단위·parity: 전체 스위트, `trace_bytes()` parity 전부 통과, FFI 계측 테스트 갱신.
- 실측 (**게이트**): 코어 격리 Peak RSS(결과 조회 포함) tape·callback 모두 python 대비 **1.25배 이하**. 100종목 tape `materialize_seconds`가 PR 5 대비 30% 이상 감소. `run_seconds` 회귀 ±3% 이내.
- E2E: `tests/integration` 통과 + `bench_workbench_adapter.py` python/rust metrics 동일.

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

- [ ] **7.1** `result_tables.py` 타입과 python `EventStore.result_tables()` (기존 객체에서 파생) + 테스트 (`tests/test_engine_golden.py` 패턴으로 golden 값).
- [ ] **7.2** Rust `*_rows()`/`fill_totals()` + persistent store 오버라이드 + parity 테스트(python/rust `result_tables()` ==).
- [ ] **7.3** 어댑터 전환. `tests/integration/test_backtest_http_api.py::test_python_reference_and_rust_core_have_golden_result_and_metric_parity`가 그대로 통과해야 한다 (artifacts·series·metrics 동일).
- [ ] **7.4** `order_from_wire` 미세 최적화 (`_SIDE = {s.value: s for s in Side}` 등 3개 dict, `_decision_by_id` 결과의 `actions` 튜플 캐시). 이건 `result.orders`를 직접 쓰는 외부 호출자를 위한 것.
- [ ] 커밋 3~4개, 마지막 `perf(workbench): 결과 아티팩트를 엔진 columnar 테이블에서 직접 만든다`.

### AC

- 단위·parity: `result_tables()` python/rust 동등 테스트, golden 테스트, 전체 스위트.
- 실측 (**게이트**): `bench_workbench_adapter.py --instruments 100 --core all --repeat 3`에서 rust 경로 `engine.run` 이후 구간 합계가 PR 6 대비 50% 이상 감소하고, e2e total 배수(python/rust)가 PR 1 기준선(약 2.0배)보다 커진다. 수치를 PR 본문 표로.
- E2E (**필수**): `tests/integration/test_backtest_http_api.py -q` 전부 통과. `bench_workbench_adapter.py` JSON에서 두 코어 metrics·series 동일. 프론트 e2e(`browser-e2e` CI job)는 PR CI에서 확인.

---

## PR 8 — Rust hot loop 할당·탐색 제거 (`perf/rust-hot-loop`, base PR 7)

**배경:** PERF-5/6, SoT-6. 실측 A/B(300종목, 저부하): `row_of` O(1) 인덱스만으로 callback run −6.7%, tape −1.3%. 나머지는 코드 읽기 추정. `drive`가 100→300종목에서 선형(3.06배)이므로 라우터·원장 O(U²) 탐색은 이 규모에서 지배적이지 않다 — 구현하되 측정으로 유지 여부를 정한다.

**Files:** `driver.rs`, `feed.rs`, `tape.rs`, `callback.rs`, `persistent_router.rs`, `portfolio.rs`

### Tasks (각각 별도 커밋, 각 커밋 후 parity 게이트)

- [ ] **8.1** `route_basic_decision(decision: &DecisionWire)` — 라우터는 `decision.3`을 `iter()`로만 읽는다(`:675,684,689,701,707,725` 확인). `driver.rs:622` `decision_for_orders = decision.clone()` 삭제.
- [ ] **8.2** `PersistentFeed`가 `load_feed` 시 `symbol_by_key: HashMap<String, String>`을 한 번 만들고 `&self`로 빌려준다. `driver.rs:618-621`의 결정별 `registry_symbols()` + bars 병합 제거 — bars의 심볼은 등록부와 같으므로 병합 자체가 불필요함을 `feed.rs::new`에서 단언(같은 key에 다른 symbol이면 오류).
- [ ] **8.3** tape 경로 프레임: `drive_internal`에서 `self.tape.is_some()`이면 `make_frame` 대신 `make_native_frame(kind, session, event)`(snapshot·open_orders 빈 값)로 만들어 `submit_native`에 넘긴다. `native_decision`이 `frame.snapshot`·`open_orders`를 읽지 않음을 확인(`tape.rs:92-184`). callback 경로는 `notify_*` 미선언 시에도 `open_orders_wire()`를 만들므로, `CallbackFrame.open_orders`를 `OnceCell`/lazy가 아닌 "전략이 `open_orders()`를 호출할 때만 Python이 요청"하는 형태로 바꾸려면 frozen 계약(콜백 시점 값)이 깨지므로 **유지**. 대신 `order_wire` 내부 String clone을 `Arc<str>` 또는 `Rc`로 바꾸는 것은 측정 후 결정.
- [ ] **8.4** `feed.rs::row_of` O(1): `row_index: Vec<u32>` (sessions × instruments, 없으면 `u32::MAX`). 메모리 = 4B × sessions × instruments (300종목 1,231세션 1.5MB, 3,000종목 15MB). `load_feed`에서 채운다. `has_bar`·`open_at`·`history_window`의 세션별 HashMap 생성(`feed.rs:289-292`)도 이 인덱스로 대체.
- [ ] **8.5** `on_session_close`·`on_market`의 `settings()?.clone()` — `RunSettings`에서 필요한 필드만 `Copy` 값으로 꺼내는 `SessionCosts { borrow, interest, days }` 구조체와 `schedule: Arc<str>`.
- [ ] **8.6** `feed.rs::current_marks`가 `(instrument_id, close)`를 돌려주고 `portfolio.rs::mark`가 key 대신 id로 마크를 저장하도록 바꾸려면 `Portfolio`가 key→id를 알아야 하므로 범위 초과. 대신 `marks: HashMap<String, f64>` 갱신 시 `entry(key).or_insert`로 String 재할당을 줄이는 수준으로 한다. 측정 후 효과 없으면 되돌린다.
- [ ] **8.7** `persistent_router.rs::RouteContext`에 `position_index: HashMap<&str, usize>`를 결정 시작 시 한 번 만들어 `held`/`market_value`가 O(1). `portfolio.rs::ledger_index`는 삽입 순서 `Vec`를 유지한 채 `index: HashMap<String, usize>`를 병행 관리(`remove_ledger` 시 재구축).
- [ ] **8.8** 측정 후, 효과가 측정 오차(±2%) 안인 커밋은 되돌린다(PR 본문에 A/B 표 첨부).

### AC

- 단위·parity: 전체 스위트, 세 코어 signature 동일.
- 실측 (**게이트**): 300종목 callback·tape `run_seconds`가 PR 7 대비 **10% 이상 감소**(같은 세션 back-to-back, `--repeat 5` 중앙값). Peak RSS 회귀 없음(`row_index` 포함해도 tape 1.25배 유지).
- E2E: `tests/integration` 통과 (계약 불변).

---

## PR 9 — 레코드·큐 메모리 레이아웃 (`perf/record-and-queue-memory`, base PR 8)

**배경:** MEM-1/2. `RecordPayload`가 `OrderWire`(String 6개) 크기로 고정되어 70,334 레코드 × 약 248B ≈ 17MB, `Queued` 46,860 슬롯 × 240B ≈ 11MB가 `finish()`까지 상주.

### Tasks

- [ ] **9.1** `RecordPayload::Order(Box<OrderWire>)`, `Fill(Box<FillWire>)`, `Snapshot(Box<SnapshotWire>)`, `Decision { native: Option<Box<NativeDecision>> }`. `std::mem::size_of::<RecordPayload>()`를 단언하는 Rust 테스트(≤ 48B).
- [ ] **9.2** 큐 arena를 free-list slab으로: `queued: Vec<Option<Queued>>` + `free: Vec<usize>`. `push`는 `free.pop()` 슬롯 재사용, `pop`은 `take` 후 `free.push(token)`. heap 엔트리의 `seq`가 순서를 보장하므로 token 재사용은 안전(같은 token이 heap에 두 번 있을 수 없음 — pop 후에만 free). Rust 테스트: 1,000세션 MARKET pre-push 후 슬롯 수가 `sessions + max_live_per_session` 이하.
- [ ] **9.3** 측정 후 커밋 정리.

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
| 3 | `refactor/drop-dead-persistent-api` | 대기 | | |
| 4 | `refactor/rust-owns-wire-constants` | 대기 | | |
| 5 | `refactor/python-sot-context-tape-marker` | 대기 | | |
| 6 | `perf/materialize-by-kind` | 대기 | | |
| 7 | `perf/workbench-result-columnar` | 대기 | | |
| 8 | `perf/rust-hot-loop` | 대기 | | |
| 9 | `perf/record-and-queue-memory` | 대기 | | |
| 10 | `perf/feed-columnar` | go/no-go 대기 | | |
| 11 | `docs/rust-loop-final-gates` | 대기 | | |
