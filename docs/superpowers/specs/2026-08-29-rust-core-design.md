# 로드맵 6단계: Rust 코어 포팅 설계 (2026-08-29)

설계 노트의 마지막 단계. "성능·타입 안정성이 필요한 이벤트 루프, 체결, 회계는 Python 기준
구현과 골든/Zipline 대조를 먼저 고정한 뒤 Rust + PyO3로 옮긴다"를 실행한다.
`feat/basket-short` 위에 스택된다.

## 범위: 6a부터, 슬라이스 단위로

Python 엔진은 4·5·D 단계로 방금 크게 바뀌었으므로 전부를 한 번에 옮기지 않는다.
**Python 구현을 진실 원천으로 유지**하고, 결정론적 순수 계산부터 Rust로 옮겨 두 구현의
동일성을 테스트로 고정한다. 옮긴 조각은 엔진 옵션(`core="rust"`)으로 켜고, 기본값은 Python.

| 하위 | 옮기는 것 | 경계 |
|---|---|---|
| 6a (이번) | 체결 가격 규칙 `execution_price`(OHLC × 주문 종류), 수량 변환 `floor_delta_shares`, 포트폴리오 회계 `Portfolio`(apply / charge / apply_corporate_action / mark / snapshot) | 함수 호출 단위 — 세션 루프는 Python에 남는다 |
| 6b (후속) | `BrokerSim.quote`(유동성 캡·슬리피지·여력 캡·FOK), `_BuyingPower` | 세션당 주문 배치 단위 |
| 6c (후속) | 이벤트 큐 + `_on_market` 전체(그룹 판정 포함) | 세션 단위 — Python 호출은 Snapshot/Decision 배치 |

**포함하지 않는 것(6a)**: 라우터·Capability·포트/어댑터·전략 API(Python 유지), 성능 최적화
(먼저 동일성), Python 없이 도는 standalone Rust 엔진.

## 확정한 설계 결정

### 1. 별도 확장 패키지 `backtest_core`, 메인 패키지는 hatchling 유지

- **선택**: `rust/backtest_core/`에 maturin 프로젝트(pyo3, cdylib)를 두고 `uv run maturin develop`
  로 venv에 설치한다. 메인 `backtest_engine`은 `import backtest_core`를 **선택적으로** 시도하고,
  없으면 Python 구현만 쓴다. maturin은 dev 의존성.
- **대안**: 메인 pyproject의 build-backend를 maturin으로 바꿔 mixed 레이아웃.
- **이유**: 순수 Python 사용자·CI가 Rust 툴체인 없이도 설치·테스트되어야 한다. 확장은 옵션이다.

### 2. 경계는 primitive struct — 도메인 dataclass는 Python에 남긴다

- Rust 함수는 `(f64, Decimal→str, enum→str)` 같은 원시 타입만 받고 돌려준다. Python 쪽
  어댑터(`backtest_engine/core/`)가 `Bar`·`OrderEvent`·`FillEvent`로 변환한다.
- 수량은 `Decimal`이지만 정수 주식 수만 쓰므로 Rust에서는 `i64`. 소수 수량이 오면 어댑터가
  `ValueError`. 비율(`ratio`)만 `f64`로 넘기고 단주 정산은 Rust에서 `floor(qty × ratio)`를
  `i64`로 계산한다 — Python과 같은 결과가 나오도록 Python 쪽도 정수 경로임을 테스트로 고정.
- 가격·현금은 `f64`(Python `float`와 동일 IEEE 연산 순서를 지켜 비트 동일 결과를 노린다).

### 3. 선택은 엔진 생성자 `core=`

- `BacktestEngine(config, core="python" | "rust")`. `"rust"`인데 확장이 없으면 `CoreUnavailable`
  예외(조용히 Python으로 떨어지지 않는다 — 성능 비교가 거짓이 된다).
- 내부: `engine/core.py`가 `ExecutionPricing`·`PortfolioLedger` 프로토콜 두 개를 정의하고
  Python 구현(현재 코드)과 Rust 어댑터를 같은 인터페이스로 노출. `BrokerSim`·`_Run`은
  프로토콜만 본다.

### 4. 동일성 검증이 성능보다 먼저

- 기존 골든·상태 전이·바스켓·공매도·MARGIN·자본변동 테스트를 `core` 파라미터로 양쪽에 실행.
  Rust 확장이 없으면 `rust` 파라미터는 skip(실패 아님) — CI 기본은 Python.
- EventStore 레코드 단위 diff: 같은 입력으로 두 코어를 돌려 orders/fills/order_updates/costs/
  snapshots를 순서·값(Decimal 정확 일치, float는 1e-9)으로 비교하는 테스트.
- 성능은 6a에서 측정만 한다(674MB 원장 단일 종목 골든크로스 wall-clock, 두 코어).

## 테스트·검증 계획

- `tests/test_core_parity.py`: (i) `execution_price` 규칙표 24케이스 양쪽 동일, (ii) `floor_delta_shares`
  경계값, (iii) `Portfolio` 시나리오(롱·숏·전환·분할·비용) 스냅샷 동일, (iv) 엔진 레코드 diff
  (골든크로스 fixture 슬라이스 2018–2020 + 4·5단계 골든 시나리오).
- Rust 단위 테스트(`cargo test`): 가격 규칙, 회계 불변조건(equity = cash + Σ mv).
- 게이트: `cargo clippy -D warnings`, `cargo fmt --check`, 기존 Python 게이트.
- 회귀: `core="python"` 기본 경로 결과 불변(KRX 데모 출력 동일).

## 구현 결과와 스펙 차이 (2026-08-29 6a 구현 완료)

- 가격 규칙을 `engine/pricing.py`로 분리하고 `BrokerSim(pricing=)`으로 주입한다. `ExecutionPricing`
  프로토콜은 broker 모듈에 두었다(core.py ↔ broker 순환 import 회피).
- Rust `Portfolio.snapshot`은 키 정렬 순서를 돌려주고, Python 원장의 dict 삽입 순서는
  `RustPortfolio` 어댑터가 별도 리스트로 재현한다 — 스냅샷 `positions` 순서까지 동일.
- 자본변동 산술(`floor(qty × ratio)`, 단주 현금)은 Python 어댑터가 Decimal로 계산하고 Rust는
  결과만 적용한다. Rust 쪽 `ratio`는 받지 않는다.
- Rust 오류는 `ValueError` 메시지 접두어(`negative_position:` / `negative_cash:`)로 종류를 알리고
  어댑터가 도메인 예외로 바꾼다.
- 동일성: 규칙표 19케이스(`tests/test_broker.py::RULE_TABLE`), `floor_delta_shares` 경계, 포트폴리오 8단계 시나리오, 엔진 5시나리오
  (골든·공매도·MARGIN·바스켓·분할) 모두 스냅샷·체결·주문·지표가 정확히 일치.
- 성능(측정만): 픽스처 슬라이스 1,619세션 골든크로스 — python 0.42s, rust 0.43s. 6a는 함수
  호출 단위라 FFI 왕복이 계산 이득을 상쇄한다. 이득은 6b(세션당 배치)·6c(루프)에서 기대한다.
- 게이트: `cargo fmt --check`, `cargo clippy --all-targets -D warnings`, `cargo test` 통과.
- (리뷰 반영) 동일성 테스트는 float 1e-9 허용오차가 아니라 **비트 동일**(`==`)을 요구한다 — 설계
  결정 2의 목표가 실제로 달성됐음을 테스트로 고정하는 편이 강하다. 다른 플랫폼·컴파일러에서
  깨지면 그때 연산 순서 차이를 찾아 고치는 것이 맞고, 허용오차로 덮지 않는다.

## 6b 구현 결과 (2026-08-29)

- Rust: `liquidity_cap(volume, participation_str)`(십진 문자열 정수 산술 = `Decimal(str(p))`),
  `quote_numbers(...)`(유동성 캡 → 슬리피지·지정가 clip → 여력 캡(숏 진입분) → FOK),
  `BuyingPower`(available/quantity_of/consume/checkpoint/restore).
- Python: `BrokerSim.quote`를 수치 코어(`QuoteCore` 프로토콜, `PythonQuoteCore`)와 진단 문자열로
  분리, `_BuyingPower`를 `engine/core.py`의 `PythonBuyingPower`/`RustBuyingPower`로 이동.
  슬리피지 모델은 플러그인 포트라 Python에 남고 주당 값만 코어에 넘긴다.
- 동일성: 견적 산술 격자(참여율 5 × 여력 4 × 보유 4 × 방향 2 × 지정가 3 × FOK 2), 여력 누산
  시나리오, 기존 엔진 5시나리오 전부 비트 동일 (424 passed).
- 성능(측정만): 슬라이스 1,619세션 — python 0.21s / rust 0.22s. 여전히 호출 단위 FFI가 지배.

## 6c 구현 결과 (2026-08-29)

- Rust `process_market(ts, entries, groups, bars, power, fee_rate, participation, slippage)`가 한
  세션의 그룹 판정(BEST_EFFORT/AON/PROPORTIONAL) → 단일 주문(매도 먼저) → DAY/IOC/FOK 만료를
  수행하고 **계획(ops)** — fill / update / trigger / remove / drop_group — 을 돌려준다.
  Python `_on_market_rust`가 ops를 순서대로 OrderManager·큐·EventStore에 적용한다.
  OrderManager(대기열 진실 원천)·라우터·전략 호출·자본변동 적용·비용·스냅샷은 Python에 남는다.
- 슬리피지는 내장 3종만 Rust에서 계산한다; 커스텀 `SlippageModel`은 `core="rust"`에서
  `CoreUnavailable`(run 시작 시). 진단 문자열까지 Python과 같게 만든다(`repr(float)`·리스트
  표기, 잔량 갱신 전 detail, 취소 순서 = 라우팅 순).
- 동일성: 주문 생명주기 11시나리오(GTC/DAY 지정가, STOP_LIMIT 부분체결, IOC/FOK, 여력·유동성
  0 대기, 바스켓 3정책, 정지 세션 분할+대기 주문)의 EventStore 레코드 전체가 비트 동일 (436 passed).
- 성능(측정만): 슬라이스 1,619세션 python 0.18s / rust 0.20s. 세션당 주문이 0~1개인 골든크로스
  데모에서는 Rust로 옮긴 산술이 전체 시간의 극히 일부라 이득이 없다 — 남은 비용은 큐·전략 호출·
  스냅샷 생성·EventStore(Python). 이득을 보려면 다종목·다주문 워크로드에서 재측정하거나 6d로
  세션 루프 자체(큐·스냅샷)를 옮겨야 한다.

## 6b·6c 리뷰 반영 (2026-08-29)

- BEST_EFFORT 그룹에 bar 결측 leg가 있어도 그룹을 버리지 않는다 (전체 leg 잔량 0일 때만).
- 진단 문자열: 여력·잔량은 견적 시점 값, STOP/LIMIT 가격은 Python `str(Decimal)` 원문,
  float는 Python `repr` 규칙(지수 표기·nan)으로 출력 — 새 시나리오 10종이 레코드 단위로 고정.
- `parse_decimal_ratio`/`liquidity_cap`은 checked 산술(오버플로는 ValueError), `ExecutionPolicy`가
  `max_participation ∈ (0, 1]`을 생성 시 검증.

## Zipline 대조 확장 (2026-08-29, 4d·5a 스펙에서 보류했던 항목 해소)

- 원본 CSV는 레포의 KRX 원장 슬라이스(005930, 2018-06-01→2024-12-30)에서 생성한다 — 외부 데이터 불필요.
- 하네스에 `buy-hold-slippage`(VolumeShare 슬리피지 + 참여율 캡, GTC 이월)와 `short-hold`(비중 −0.7
  공매도)를 추가. Zipline 쪽은 다음 bar 시가 기준 `NextBarOpenVolumeShareSlippage`로 정렬.
- 결과: 4개 시나리오 모두 최대 상대 오차 0. 스트레스(캡 48주·충격 24%)도 0.
- 부수 결함 수정: 라우터가 목표·증감·청산 주문에 `ExecutionPolicy.time_in_force`를 전달하지 않아
  GTC 정책 잔량이 이월되지 않던 문제 (`tests/test_router.py::test_target_orders_carry_execution_policy_time_in_force`).
  상세는 `tests/manual/README.md`.

## 6d 결과 (2026-08-29): 벤치마크가 가리킨 병목은 큐가 아니라 값 타입의 선형 조회였다

스펙의 선행 조건대로 다종목 워크로드를 먼저 측정했다. `scripts/bench_universe.py <원장 디렉토리>`
— 원장 마스터 캘린더 전 구간에 상장된 종목 N개, 2020-01-02→2024-12-30 1,231세션, 5세션마다
동일 비중(총 0.9) `SetPortfolioTarget(REPLACE)` 리밸런싱, 수수료 15bp. 100종목이면 주문 23,048건.

### 측정 (100종목, cProfile 누적 기준 상위)

| 항목 | 착수 전 | 원인 |
|---|---|---|
| `InstrumentId.__eq__` | 6.5M(rust)~10M(python) 호출, 4.7~7.2s | `MarketSnapshot.bar/has`·`PortfolioSnapshot.position`이 tuple 선형 탐색 → 세션당 O(종목²) |
| `Portfolio.snapshot` | 4,924회(세션당 4회), 2.9~5.3s | 매 조회마다 종목 수만큼 `Position` 재생성 |
| `HistoryStore.append` | 3.5s | 종목×5필드마다 `(InstrumentId, PriceField)` 튜플 해시 |
| `instrument_key` | 657k 호출, 1.2s | Rust 경계마다 문자열 포맷 |
| 이벤트 큐 push/pop | 48k 호출, 0.8s | — |

큐·세션 종료는 전체의 10% 미만이었다. 6d로 계획했던 "큐·세션 종료·스냅샷 생성 Rust 이전"은
병목이 아닌 곳을 옮기는 것이라 **하지 않는다**. 대신 동작 불변인 Python 인덱싱으로 병목을 제거했다.

### 변경 (동작 불변 — `tests/test_core_parity.py`와 착수 전 448개 테스트 전부, KRX 데모 골든 그대로)

- `MarketSnapshot`·`PortfolioSnapshot`: 종목 → Bar/Position dict 인덱스를 `functools.cached_property`로
  첫 조회 때 만든다. dataclass 필드가 아니므로 `fields()`/`asdict`/`==`/`hash`/`repr`에 나타나지
  않는다 (리뷰 DEFECT-001: 처음엔 `field(init=False, compare=False)`로 두었는데 `asdict`가
  `dict[InstrumentId, …]` 키를 dict로 바꾸다 `TypeError` — 전략 작성자가 이벤트를 직렬화하면 죽는다).
- `Portfolio.snapshot(ts)`(Python·Rust 양쪽): `(ts, snapshot)` 메모, 상태를 바꾸는 명령
  (`apply`/`charge`/`apply_corporate_action`/`mark`)마다 비운다. CQS 유지 — 조회는 몇 번 불러도 같다.
- `HistoryStore`: 종목별 dict 안에 필드 시리즈를 두어 세션당 종목 수만큼만 해시한다.
- `instrument_key`: `functools.lru_cache(maxsize=65_536)` (InstrumentId는 불변·해시 가능, 스윕에서 무한 성장 방지).

### 결과

| 워크로드 | python 전 → 후 | rust 전 → 후 |
|---|---|---|
| 100종목·1,231세션·주문 23k | 15.1s → 2.1s | 8.8s → 1.9s |
| 300종목·1,231세션·주문 63k | — → 5.8s | — → 5.7s |

두 코어의 fills·orders·최종 equity는 종목 수와 무관하게 동일하다. 종목 수 3배에 시간 3.1배 —
선형 스케일링. 남은 시간(100종목 rust, 인덱스 즉시 생성 버전 프로파일 9.4s 중)은 전략 dispatch·
라우터 2.4s, Python↔Rust 엔트리 마샬링 3.0s, 히스토리 append 1.6s, 스냅샷 2회/세션 1.6s, 큐 0.8s다 —
전략이 Python 객체(MarketSnapshot·PortfolioSnapshot)를 받는 한 Rust로 더 옮겨도 이 몫은
사라지지 않는다.

### 테스트·검증

- `tests/test_lookup_index.py`: 인덱스 조회의 값 동등성(동일 값 다른 객체), 미존재 종목
  진단, 인덱스가 스냅샷의 `==`/`hash`/`fields()`/`asdict`에 영향 없음, 히스토리 세션 축 정렬(결측 NaN), 스냅샷 메모가
  상태 변화(`apply`) 전까지만 같은 객체를 돌려주고 ts가 다르면 새로 만드는지 (python·rust 양쪽).
- 회귀: 전체 테스트 458개 통과, `examples/run_krx_demo.py` 최종 equity 9,143,752 KRW·102/102 불변.

### 6d 리뷰 반영 (Opus·Sonnet, 2026-08-29)

- DEFECT-001: 인덱스를 dataclass 필드로 두면 `asdict`가 `TypeError` — `cached_property`로 이전
  (`tests/test_lookup_index.py::test_snapshots_stay_plain_dataclasses_for_asdict`).
- DEFECT-002: `PortfolioSnapshot`에 같은 종목이 두 번 들어오면 dict 인덱스가 마지막 값만 봐
  first-wins였던 조회가 조용히 바뀐다 — `__post_init__`에서 중복을 `ValueError`로 막는다
  (엔진 경로는 원장 키 유일성으로 도달 불가, 공개 생성자 방어).
- 메모 무효화 테스트를 `charge`·`mark`까지 확장, 벤치마크 하네스의 O(N²) 종목 탐색(tuple→frozenset)·
  `--core choices`·빈 결과 방어 보강 — 위 표의 수치는 보강 후 재측정값.

## 다음 단계

Rust 세션 루프 이전은 측정 결과로 닫는다. 더 줄이려면 전략 경계를 바꿔야 한다(예: 배치 이벤트·
배열형 스냅샷) — 그것은 전략 API 변경이라 별도 스펙 대상이다.
