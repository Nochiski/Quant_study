# Quant_study

퀀트 스터디 저장소. 데이터 수집·가공부터 백테스팅까지 각자 실습하고, 쓸 만한 코드는 공용으로 올려 함께 쓴다.

이벤트 드리븐 백테스트 엔진과 YAML-first Strategy Workbench를 만드는 학습 리포. 설계 아티팩트
(`backend/reference/`의 학습 노트)에서 고정한 전략 I/O 계약을 backend의
`backtest_engine` 패키지로 구현한다.

## 구조

```
backend/
├─ src/backtest_engine/     # Python API/reference engine: types, engine, ports, adapters, data
├─ src/strategy_workbench/  # workbench backend: domain→application→adapters, bootstrap 조립
├─ rust/backtest_core/      # Persistent Rust Engine
├─ tests/                   # engine/workbench/architecture 계약 테스트와 소형 fixture
├─ examples/                # 골든크로스 CSV/KRX parquet 데모
├─ scripts/                 # fixture 재생성·다종목 벤치마크
├─ benchmarks/              # 성능 측정 baseline
└─ reference/               # 2026-08-17 설계·Zipline 관찰 아카이브
frontend/                   # workbench UI: FSD app→pages→widgets→features→entities→shared
docs/                       # 공용 설계·로드맵·리포트
database/                   # 원장 수집(KRX·키움·KIS·DART·WISE)·stage·문서층 파이프라인 — src·docs·tests 추적, data/·logs/ 는 git 제외
workspace/         # 개인 작업 공간 workspace/<이름>/ — docs·src 추적, data/·logs/ 는 git 제외
```

Strategy Workbench의 전체 계획과 체크리스트는
[Strategy Workbench 구현 로드맵](docs/superpowers/specs/2026-09-03-strategy-workbench-roadmap.md)에 있다.
전략 authoring은 verbose YAML/JSON source로 전환 중이며 계약은
[Strategy Authoring Contract ADR](docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md),
PR 진행은 [docs/planning/strategy-workbench-yaml-ui/PLAN.md](docs/planning/strategy-workbench-yaml-ui/PLAN.md)가
추적한다. 기존 Quick/Advanced no-code 편집기는 migration 기간 legacy route로 유지된다.
Equity DB 계약이 확정되기 전에는 `backend`의 PIT mock adapter가 기준 구현이며, 실제 DB는 같은
application port를 구현하는 outbound adapter로 교체한다.

## 설계 아티팩트 요약

원본은 [백테스트 엔진 설계 노트](https://claude.ai/code/artifact/d1124798-840e-4282-a945-8317e4b7066d?org=20cfa914-bd88-4167-936b-209dd561dfb7)다.
Zipline 사용법 자체보다 커스텀 엔진을 만들기 전에 전략 I/O 계약, 책임 경계,
Capability 협상과 향후 Rust 포팅 경계를 고정한 구현 전 설계 기준이다.

```text
Requirements → Capability 검증 → StrategyEvent + 읽기 전용 Context
             → StrategyDecision(Action 묶음) → Validator / Router / Risk
             → Order → BrokerSim → Fill → Portfolio / Snapshot
```

- 전략은 `requirements()`로 필요한 데이터·이벤트·Action·기능을 먼저 선언하고,
  `on_event(ctx, event) -> StrategyDecision`에서 판단만 반환한다. 설정과 전략 고유
  State는 소유할 수 있지만 Context나 Portfolio를 직접 변경하지 않는다.
- 엔진은 시계열 정렬, 선언된 데이터의 준비, Action 검증과 수량 변환, 주문 생명주기,
  체결·비용·슬리피지, 포트폴리오 회계와 성과 기록을 소유한다.
- `NoAction`, 목표 비중/수량, 증감, 긴급 청산, 직접 주문, 취소·정정, Basket은 의미가
  다른 합 타입으로 표현한다. 타입이 정의된 것(`DEFINED`)과 실행 코드가 있는 것
  (`IMPLEMENTED`)은 별개이며, 미구현 요구는 첫 Bar 전에 구체적으로 거절한다.
- 동일 종목의 Bar는 시간순이어야 하고, 주문 생성만으로 현금·보유 수량이 바뀌어서는
  안 된다. 거래 상태는 Fill로만 바뀌며 Snapshot은 Bar와 Fill에서 재계산 가능해야 한다.
- 일봉 종가로 만든 판단은 같은 종가에 체결하지 않고 다음 시가에 실행한다. Decision,
  Action, Order, Fill, Snapshot은 append-only로 남겨 결과를 역추적한다.
- 전략 연구는 Python에 유지한다. 성능·타입 안정성이 필요한 이벤트 루프, 체결과 회계는
  Python 기준 구현과 골든/Zipline 대조를 먼저 고정한 뒤 Rust + PyO3로 옮기는 경로를
  택한다. Python 호출은 종목별 Bar가 아니라 시점별 Snapshot과 Action 배치 단위로 한다.
- 검증은 단위, 손계산 골든, Zipline 대조, 회귀, 계약, 상태 전이 테스트로 나눈다.

## 현재 브랜치 구현 범위

- 로드맵 3단계(`NoAction`, `SetPortfolioTarget`, `LiquidatePosition`)에 더해 4단계
  주문 생명주기를 구현했다 (`docs/superpowers/specs/2026-08-29-order-lifecycle-step4-design.md`):
  - 4a `SetPositionTarget`(Weight/Quantity/Notional), `AdjustPosition`
  - 4b `SubmitOrder`로 MARKET/LIMIT/STOP/STOP_LIMIT, DAY/GTC — 일봉 OHLC 기반 체결 규칙
  - 4c `CancelOrder`/`ReplaceOrder`, `ctx.open_orders()`, 전략에 FILL/ORDER_UPDATE 전달
  - 4d 거래량 참여율 부분체결, IOC/FOK, 슬리피지 포트(`NoSlippage`/`FixedBps`/`VolumeShare`)
  남은 `NOT_IMPLEMENTED`는 5단계(Basket, 공매도, MARGIN)뿐이며
  `reference_engine_capabilities()`에 명시된다. LIMIT/STOP·IOC/FOK·`max_participation`은
  해당 `EngineFeature`를 `requirements()`에 선언한 전략만 쓸 수 있다.
- 엔진 내부는 `(ts, priority, seq)`로 정렬되는 이벤트 큐 하나로 흐른다:
  `MARKET(대기 주문 체결) → FILL(포트폴리오 반영) → NOTIFY(전략 알림) → SESSION_CLOSE(평가·전략 호출) → ORDER(주문 등록)`.
  T 종가 판단은 T+1 세션부터 체결된다. 주문 생성은 현금·보유를 바꾸지 않고,
  미체결 주문은 DAY 만료·run 종료 시 반드시 `CANCELLED`로 기록된다.
- 시장 데이터는 헥사고날 경계로 분리된다. 엔진·전략은 `ports.BarSource`가 돌려주는
  `LoadResult`와 `DataFeed`만 보고, 파일 형식·벤더 SDK·DB는 `adapters/`에만 존재한다.
  새 채널은 `load_bars(BarQuery) -> LoadResult` 하나를 구현하면 붙는다.
- 정제(OHLC 정책·거래정지 제거·시간 역행 거절)는 `data/cleaning.py` 한 곳에서 하고,
  손댄 행 수는 항상 `repaired_rows`/`dropped_rows`로 보고한다 (silent 보정 금지).
- 5단계(`docs/superpowers/specs/2026-08-29-basket-short-margin-design.md`): `SHORT_SELLING`
  선언 시 음수 포지션(방향 전환 시 평균단가 재설정)과 세션 종료 차입 비용, `MARGIN` 선언 시
  매수 여력 = `max_gross_leverage × equity − 총노출`과 음수 현금 이자(둘 다 `CostAccrued`로
  기록, equity < 0이면 `EquityWipedOut`), `BasketAction`은 leg를 함께 견적해 BEST_EFFORT /
  ALL_OR_NONE / PROPORTIONAL로 판정한다. 이제 Action·Feature 축에 `NOT_IMPLEMENTED`가 없고
  TIMER 이벤트·`MonthEndSession`만 남는다.
- Persistent Rust 엔진(`docs/superpowers/specs/2026-09-01-persistent-rust-engine-implementation.md`):
  `BacktestEngine(core="rust")`는 세션 사이 주문·그룹·포트폴리오·feed/history·라우터·큐·callback
  lifecycle·compact result store를 Rust 메모리에 유지한다. Python은 전략 callback과 공개 API를
  담당하고 `BacktestResult.orders/fills`는 최초 조회 시 기존 tuple로 materialize된다. Python
  reference와 전체 EventStore trace를 고정한 상태에서 100종목 주문 집중 부하는 1.719배,
  300종목 확장 부하는 1.618배 빨랐다. `rust_persistent`는 전환 호환 alias이고, 구 세션 단위
  구현은 `rust_legacy`로만 남아 deprecation warning을 낸다. 최소 1.5배 게이트는 통과했지만
  2배와 세션당 FFI 0회는 후속 최적화 목표다. 측정과 구조는
  [HTML 벤치마크 리포트](docs/rust-python-benchmark-report.html)에 시각화했다.
- 데이터 후속(D, `docs/superpowers/specs/2026-08-29-data-followups-design.md`): 원장의
  상장주식수 변화로 액면분할·병합을 검출해 `run(corporate_actions=)`로 넘기면 엔진이 사건
  세션 시작에 보유 수량·평균단가를 조정하고(단주는 시가 현금 정산) 대기 주문을 취소한다.
  종목마스터 일별 스냅샷으로 만든 `UniverseResult`를 `run(universe=)`로 주면 전략이
  `ctx.universe()`로 그 세션의 상장 종목만 본다(look-ahead 차단). 새 데이터 채널은
  `backend/tests/test_bar_source_contract.py`의 빌더 하나로 계약 전체를 통과해야 한다 (sqlite로 검증).

## 실행

```bash
cd backend
uv sync --extra parquet             # 의존성 설치 (Python 3.11+, pyarrow 포함)
uv run pytest                       # 엔진 + Strategy Workbench 테스트
uv run ruff check src tests examples scripts
uv run pyright
uv run python examples/run_demo.py      # PyKRX CSV(005930)로 골든크로스 백테스트
uv run python examples/run_krx_demo.py  # KRX 원장 parquet 슬라이스로 동일 전략 실행
uv run python examples/run_krx_demo.py <원장 디렉토리>   # quant-data 빌드 전체 대상
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release  # Rust 코어(선택)
uv run python examples/run_krx_demo.py --core rust      # Rust 코어로 같은 데모
```

Strategy Workbench 개발 서버:

```bash
# 저장소 루트 · 터미널 1
uv run server

# 저장소 루트 · 터미널 2
npm ci --prefix frontend
npm run dev
```

`uv run server`는 backend의 FastAPI/Uvicorn 개발 서버(`127.0.0.1:8000`, reload)를,
`npm run dev`는 frontend의 Vite 개발 서버(`localhost:5173`)를 실행한다. 백엔드 옵션은
그대로 전달된다(예: `uv run server --port 8123`). 기존처럼 `backend`와 `frontend`
디렉터리 안에서 각각 실행해도 같은 owner의 설정을 사용한다.

## 검증: Zipline 대조

엔진 회계는 Zipline과의 세션 단위 equity 대조로 검증됐다 — buy-hold, 골든크로스, 슬리피지
(VolumeShare + 참여율 캡·GTC 이월), 공매도 buy-hold 네 시나리오 모두 최대 상대 오차 0
(1,619세션, KRX 원장 슬라이스에서 생성한 CSV). 실행 방법과 리포트는
`backend/tests/manual/README.md` 참고.

## 데이터

원장(KRX·키움·KIS·DART·WISE 수집분)은 카엘 서버가 정본이다. 저장소에는 데이터를 넣지 않는다.
공유 방식은 `docs/superpowers/specs/2026-08-25-quant-ledger-sharing-design.md`, 수집·stage 설계는
`database/README.md` 참고.

## 작업 규칙

1. **남의 `workspace/` 폴더는 건드리지 않는다.** 개인 공간 안에서는 구조도 스타일도 자유. 이것만 지키면 충돌이 날 일이 없다.
2. **공용 영역 변경은 상의하거나 PR 로.** `.gitignore`, `.claude/rules/`, `README.md`,
   `backend/`, `frontend/`, `docs/`가 해당된다.
3. **데이터 파일은 커밋하지 않는다.** 시세 CSV·parquet 등은 `.gitignore` 에서 막아 두었다. 저장소에는 **데이터를 만들어 내는 스크립트**를 넣고, 데이터는 각자 로컬에서 재현한다.
4. **API 토큰·키는 절대 커밋하지 않는다.** `*_token.json`, `*.token` 은 `.gitignore` 에서 막아 두었다.

데이터를 둘 곳이 필요하면 `workspace/<이름>/data/` 를 쓰면 된다(DB 파이프라인은 `database/data/`) — `workspace/*/data/`·`database/data/` 규칙으로
이미 git 에서 제외된다. 손으로 계산할 수 있는 소형 테스트 픽스처만
`backend/tests/fixtures/` 아래 CSV·parquet 으로 예외 허용.

## 코딩 규칙

`.claude/rules/` 에 정리되어 있다.

| 파일 | 범위 | 내용 |
|---|---|---|
| `code-style.md` | `**/*.py` | 기존 헬퍼 재사용, 기능/정리 커밋 분리, ruff·pyright 게이트, 네이밍 |
| `python.md` | `**/*.py` | 성공/실패는 튜플 대신 Result 값 타입으로 |
| `error-messages.md` | `**/*.py` | 예외·로그에 재현 가능한 컨텍스트 포함 |
| `testing.md` | `backend/tests/`, `backend/scripts/` | 산출물 파일 존재/내용을 단언하는 테스트 금지 |
| `pr-review.md` | 전체 | PR 본문 양식, 결함 보고 4요소 |
| `backend-package-boundary.md` | `backend/**/*.py` | 헥사고날 방향, facade, `DEPENDS_ON`, mock adapter 경계 |
| `strategy-workbench-sot.md` | `backend/`, `frontend/` | 전략·팩터·지표·상태의 단일 owner |
| `frontend-fsd.md` | `frontend/src/` | FSD 단방향, slice 격리, public API |
| `frontend-api-state.md` | frontend API/state | 생성 SDK, 서버·draft·UI 상태 소유권 |
| `frontend-ui-quality.md` | frontend UI | primitive, token, 접근성, i18n, raw metric |
| `frontend-testing.md` | frontend test/e2e | 사용자 동작·wire 경계 테스트 |
