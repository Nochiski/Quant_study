# Quant_study

퀀트 스터디 저장소. 데이터 수집·가공부터 백테스팅까지 각자 실습하고, 쓸 만한 코드는 공용으로 올려 함께 쓴다.

이벤트 드리븐 백테스트 엔진과 YAML-first Strategy Workbench를 만드는 학습 리포. 설계 아티팩트
(`backend/reference/`의 학습 노트)에서 고정한 전략 I/O 계약을 backend의
`backtest_engine` 패키지로 구현한다.

> 처음 사용하는 경우 [Strategy Workbench 사용자 매뉴얼](docs/manual/strategy-workbench/README.md)에서
> 실제 화면을 따라 샘플 전략 작성, 오류 수정, 리비전 저장, 중간 결과 추적, 백테스트까지 진행할 수 있다.

## 바로 실행

Python 3.11 이상, uv, Node.js 22.18 이상이 필요하다. 저장소 루트에서 처음 한 번 의존성을 설치한다.
아래 명령은 macOS·Linux·Windows에서 같다.

```sh
npm ci --prefix frontend
uv sync --project backend --extra parquet --extra equity --extra llm
```

한 터미널에서 frontend와 backend를 함께 실행하고, 준비가 끝나면 브라우저를 연다.

```sh
uv run dev --open
```

브라우저를 자동으로 열지 않으려면 다음 명령을 쓴다. 종료할 때 `Ctrl+C`를 누르면 두 서버가 함께
종료된다. `dev`는 backend·frontend 의존성을 자동 설치하거나 동기화하지 않으며 설치한 extra와
Rust 확장을 유지한다. 단 서버를 시작하기 전에 Rust 백테스트 코어(`backtest_core`)가 없거나 Rust
소스가 마지막 빌드 뒤에 바뀌었으면, 이미 설치된 backend 환경 안에서 그 확장만 release로 빌드한다.

```sh
uv run dev
```

화면 주소는 <http://localhost:5173/>, backend health endpoint는
<http://127.0.0.1:8000/api/v1/health>다. 기본 포트 8000·5173 중 하나라도 사용 중이면 시작을
거절하며 기존 서버를 종료하지 않는다. 개별 기동은 아래 [실행](#실행) 절을 참고한다.

기본 `Rust` core로 백테스트하려면 Rust 툴체인(<https://rustup.rs>)만 설치해 두면 된다. `uv run dev`가
시작 전에 확장 설치 여부와 Rust 소스 지문(`backend/.venv/backtest_core.build-stamp`)을 보고 필요할
때만 빌드한다. 처음 빌드는 몇 분 걸릴 수 있다. cargo를 찾지 못하거나 빌드가 실패하면 경고만 내고
서버는 그대로 시작하며, 그때는 백테스트 실행 설정에서 `Python reference`를 선택한다. 자동 빌드를
끄려면 `uv run dev --no-rust-build` 또는 환경 변수 `QUANT_STUDY_DEV_SKIP_RUST_BUILD=1`을 쓴다.
`dev`를 거치지 않고 backend만 띄울 때는 다음 명령으로 직접 설치한다.

```sh
cd backend
uv run --no-sync maturin develop --manifest-path rust/backtest_core/Cargo.toml --release
cd ..
```

extra 목록은 CI(`.github/workflows/ci.yml`)와 같다. `equity`는 실데이터 duckdb adapter, `llm`은 AI
어시스턴트 공급자 SDK다. extra를 빼고 `uv sync`를 다시 돌리면 설치해 둔 extra 패키지와
`maturin develop`로 넣은 Rust 확장이 함께 빠지므로, 그때는 extra를 포함한 환경 설치를 다시 실행한다.
Rust 확장은 다음 `uv run dev`가 다시 빌드한다.

편집기는 **전략 편집**과 **중간 결과**를 명시해서 전환하며 원문·추적 범위·결과를 유지한다.
구조·계약·AI 보조 영역은 필요할 때 열고, 그래프에서는 전략 구성·선정 미리보기·팩터 레시피·노드 캔버스의
입구로 현재 과업을 고른다. 화면 구성과 점검 범위는
[편집기 사용성 검토](docs/planning/ui-layout-polish/editor-usability-review.md)를 참고한다.

실험 trial의 **이 설정 추적**은 그 실행의 파라미터를 현재 문서·현재 실행 환경의 선정 미리보기·추적·백테스트에
적용한다. 출처를 읽지 못하거나 저장 리비전이 다르면 차단하며, **문서 기본값으로 돌아가기**를 명시해야 기본값을 쓴다.
선택한 실행 당시의 기간이나 환경을 재현하는 기능은 아니다.

## 현재 제품 범위

| 영역                                                        | 상태                                                             |
| ----------------------------------------------------------- | ---------------------------------------------------------------- |
| verbose YAML 작성, schema/semantic 검증, Contract Inspector | 구현 완료                                                        |
| immutable 리비전 저장, 기존 전략 편집, 원문·의미 Diff       | 구현 완료                                                        |
| 팩터 그래프, TargetTape와 노드별 중간 결과 추적             | 구현 완료                                                        |
| Python reference·Persistent Rust 백테스트와 전문 결과 화면  | 구현 완료                                                        |
| 전략·리비전·백테스트 이력과 실행 provenance                 | 구현 완료                                                        |
| 파라미터 실험·워크포워드 창별 비교                           | 구현 완료. 창마다 초기자본을 다시 시작한 비교 결과로 표시        |
| mock 연속 계좌 평가                                        | 구현 완료. 실험 상세에서 명시 실행·진행·계좌 결과·실패 이유 확인 |
| 실행 설정 패널(시장·기간·유니버스·체결·비용)                | 구현 완료. schema 1.2부터 전략 문서 밖에서 실행마다 정한다       |
| schema 1.2 업그레이더                                       | 구현 완료. 1.0·1.1 문서를 1.2로 옮긴다                           |
| AI 어시스턴트                                               | 구현 완료. 설정 화면에서 공급자 연결, 전략 화면 채팅에서 제안 적용 |
| 백테스트 결과 AI 설명                                       | 구현 완료. 결과 화면 사이드바에서 결과와 지표를 쉬운 말로 설명   |
| 그래프 표현(파이프라인·레시피·노드 캔버스)                  | 구현 완료. strategy-language-2-0 P4~P6(2026-10-13)               |
| 실제 시장 DB                                                | 카엘 서버 equity 층을 `ledger_sync`로 로컬에 받아 duckdb 어댑터로 연결(기본은 PIT mock adapter) |
| 실자료 수익성 검증                                         | 2026-10-19 주간 예정(2026-10-17 기준). mock 검증과 별도           |
| 배포·실시간·실브로커 주문·운영 리스크                       | 후속 범위. 키움 실거래 연결·실제 주문은 구현하지 않음            |

YAML 원문은 authoring source, backend의 typed `StrategySpec`은 실행 의미의 단일 정본이다.
JSON/Diff는 같은 compile 결과의 read-only projection이고, Form과 Graph는 runtime schema × parse tree
projection 위에서 source 트랜잭션으로 YAML을 고치는 편집기다(주석·순서 보존, undo 한 번). frontend가
전략 의미나 `spec_hash`를 별도로 계산하지 않는다. 실제 배포는 편집 중 draft가 아닌
`strategy_id + revision + spec_hash` 참조만 허용하는 방향으로 확장한다.
schema 1.2부터 시장·기간·유니버스·체결·비용은 전략 문서가 아니라 실행 요청의 `RunEnvironment`(실행
설정)가 소유한다. 실행 설정을 바꿔도 `spec_hash`와 revision은 그대로이고 `environment_hash`만 달라진다.

완료된 파라미터 실험의 **연속 계좌 평가**는 mock 자료에서 동결된 창별 후보를 한 계좌로 이어
실행한다. 초기자본 기준 손익·비용·위험 지표와 평가 기간·후보 출처를 별도 기록하며 기존 창별
재시작 비교와 구분한다. 이 연결은 실자료 수익성이나 실거래 준비를 검증한 결과가 아니다.
현재 범위와 다음 연구의 기준은 [연구→자동매매 진행 현황](docs/planning/research-to-trading/README.md)과
[S04 연속 평가 계약](docs/planning/research-to-trading/S04-walk-forward.md)을 따른다.

홀드아웃 준비의 [V6-01 사전등록·모의 검증 core](docs/planning/validation-lab/V6-01-preregistration-simulation.md)는
동결 후보·기준과 일회성 모의 claim을 영속화한다. 합성 지표로 절차를 확인하는 범위이며 실제 봉인
개봉·실자료 후보 통과·개봉 UI는 제공하지 않는다. 이 core는 #547로 머지된 부분 구현이며 V6 전체 완료가 아니다.

키움 없이 먼저 진행하는 범위와 다음 작업은 [키움 독립 작업 인계](docs/planning/validation-lab/KIWOOM-INDEPENDENT.md)를
따른다. 회귀 접수 상한(#546)·V6 모의 core(#547)·선택 파라미터 배선(#548)은 머지됐고 V5-02는 완료됐다.
후보 지도(#549, main `45328d0b`)·비용/회귀 보고서(#550, main `366dc241`)·IDE 연구 진입점(#551, main `c2f46dfb`)도 머지됐다. 검증 랩은 26/28(93%)로 V5 화면을 완료했으며, 이는 전체 자동매매의 완료율이 아니다. 이후에도 사전등록 모의 화면, 데이터·회계 검산, 신호 연구·실자료 계좌 평가와 운영 준비를 진행할 수 있다.
실제 모의계좌 주문·체결 대사는 브로커 연결이 필요하다. 2026-10-19 주간 예정인 실자료 수익성 평가는 기능·화면의
기술 QA와 별도이며, 봉인 개봉·자본 투입·실거래 승인을 뜻하지 않는다.

## 구조

편집기 제목 옆 **연구**에서 계열 시도 수를 확인하고, 저장한 현재 원문·선택 파라미터·현재 환경을
기존 실험 만들기로 보내거나 레시피 탐색을 전환한다. 미저장 변경을 옛 저장본으로 대신 보내지 않는다.
범위와 검증 기준은 [V5-07 연구 진입점](docs/planning/validation-lab/V5-07-ide-research.md)을 따른다.

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
docs/
├─ manual/                  # 사용자 매뉴얼
├─ planning/                # initiative 기획 패키지(WORKFLOW·PLAN)
├─ product/user-stories/    # 페르소나·유저 스토리 하네스
└─ superpowers/             # 설계 spec·ADR·로드맵, 구현 플랜, 스파이크
tools/quant_study_dev/      # 루트 개발 명령: `uv run dev`, `uv run server`, 스토리 검사·충돌 표식 검사
database/                   # 원장 수집(KRX·키움·KIS·DART·WISE)·stage·equity 파이프라인 + 서버 SFTP 동기화(ledger_sync) — src·docs·tests 추적, data/·logs/ 는 git 제외
workspace/         # 개인 작업 공간 workspace/<이름>/ — docs·src 추적, data/·logs/ 는 git 제외
```

전략 작성 화면의 YAML source와 JSON/Form/Graph/Diff projection은 같은 StrategySpec 계약을 사용한다.
파라미터 실험·워크포워드 비교·mock 연속 계좌 평가는 실험 화면에서 확인한다.

완료 백테스트의 검증 카드는 저장된 PSR과 현재 계열의 시도 수를 반영한 DSR·계산 근거를 보여준다.
원장은 명시적으로 다시 조회하며, 통계 값이 없는 이유와 조회 오류를 구분한다. 실수익성 검증을
뜻하지 않으며 자세한 범위와 남은 작업은 [실행 검증 카드 계약](docs/planning/validation-lab/V5-02-run-validation-card.md)을 따른다.

Equity 층은 `backend`의 duckdb outbound adapter(`STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb`)가 읽고,
환경변수를 주지 않으면 같은 application port를 구현하는 PIT mock adapter로 뜬다. 실데이터 연결 절차는
아래 [실데이터 연결](#실데이터-연결) 절 참고.

## 문서 색인

Strategy Workbench의 전체 계획과 체크리스트는
[Strategy Workbench 구현 로드맵](docs/superpowers/specs/2026-09-03-strategy-workbench-roadmap.md)이
소유한다. 작성 계약의 출발점은
[Strategy Authoring Contract ADR](docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md)이다.

기획 패키지는 initiative마다 WORKFLOW(PR scope)와 PLAN(진행 상태의 단일 기준)을 둔다.

| 기획 패키지                                                                           | 범위                                         | 상태                  |
| ------------------------------------------------------------------------------------- | -------------------------------------------- | --------------------- |
| [strategy-workbench-yaml-ui](docs/planning/strategy-workbench-yaml-ui/README.md)     | YAML-first Strategy Workbench 문서 IDE       | 완료                  |
| [strategy-gui-editing](docs/planning/strategy-gui-editing/README.md)                 | schema 1.1, Form/Graph 편집                  | 완료                  |
| [ai-assistant](docs/planning/ai-assistant/README.md)                                 | AI 어시스턴트, 백테스트 결과 AI 설명         | 완료                  |
| [strategy-language-2-0](docs/planning/strategy-language-2-0/README.md)               | schema 1.2, 실행 설정, 그래프 표현           | 완료                  |
| [research-to-trading](docs/planning/research-to-trading/README.md)                 | 연구 보존·신호 평가·연속 계좌·오프라인 실행 | mock 연속 평가 UI 연결, 실자료 수익성 검증은 별도 |

설계 spec:

- [화면 디자인 정본](design-system/strategy-workbench/MASTER.md): 화면 위계·타이포·컨트롤·반응형 배치 기준
- [schema 1.1 · GUI 편집](docs/superpowers/specs/2026-09-17-strategy-schema-1-1-and-gui-editing-design.md)
- [AI 어시스턴트](docs/superpowers/specs/2026-09-20-ai-assistant-design.md)
- [전략 언어 2.0(schema 1.2 · 그래프 표현)](docs/superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)
- [AI 백테스트 결과 설명](docs/superpowers/specs/2026-09-27-ai-backtest-result-explain.md)

제품과 사용자:

- [사용자 매뉴얼](docs/manual/strategy-workbench/README.md): 화면을 따라가는 사용법
- [유저 스토리 하네스](docs/product/user-stories/README.md): 페르소나 세 명의 스토리와 e2e 추적.
  모든 개발의 기준선이다(`.claude/rules/user-story-harness.md`).

## 설계 아티팩트 요약

원본은 [백테스트 엔진 설계 노트](https://claude.ai/code/artifact/d1124798-840e-4282-a945-8317e4b7066d?org=20cfa914-bd88-4167-936b-209dd561dfb7)이고,
저장소 사본은 [backend/reference/sangmok/result/html/index.html](backend/reference/sangmok/result/html/index.html)이다.
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

## 현재 구현 범위

- 로드맵 3단계(`NoAction`, `SetPortfolioTarget`, `LiquidatePosition`)에 더해 4단계
  주문 생명주기를 구현했다 (`docs/superpowers/specs/2026-08-29-order-lifecycle-step4-design.md`):
  - 4a `SetPositionTarget`(Weight/Quantity/Notional), `AdjustPosition`
  - 4b `SubmitOrder`로 MARKET/LIMIT/STOP/STOP_LIMIT, DAY/GTC — 일봉 OHLC 기반 체결 규칙
  - 4c `CancelOrder`/`ReplaceOrder`, `ctx.open_orders()`, 전략에 FILL/ORDER_UPDATE 전달
  - 4d 거래량 참여율 부분체결, IOC/FOK, 슬리피지 포트(`NoSlippage`/`FixedBps`/`VolumeShare`)
    LIMIT/STOP·IOC/FOK·`max_participation`은
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
uv sync --extra parquet --extra equity --extra llm   # 의존성 설치 (Python 3.11+, CI와 같은 extra)
uv run pytest                       # 엔진 + Strategy Workbench 테스트
uv run ruff check src tests examples scripts tools
uv run pyright
uv run python examples/run_demo.py      # PyKRX CSV(005930)로 골든크로스 백테스트
uv run python examples/run_krx_demo.py  # KRX 원장 parquet 슬라이스로 동일 전략 실행
uv run python examples/run_krx_demo.py <원장 디렉토리>   # quant-data 빌드 전체 대상
uv run --no-sync maturin develop --manifest-path rust/backtest_core/Cargo.toml --release  # 기본 Workbench 백테스트에 필요
uv run python examples/run_krx_demo.py --core rust      # Rust 코어로 같은 데모
```

Strategy Workbench 개발 서버는 저장소 루트에서 함께 기동한다. 의존성·선택적 Rust 확장 설치는
위 [바로 실행](#바로-실행)을 따른다.

```sh
uv run dev --open
```

`--open`은 frontend와 backend의 HTTP 준비를 확인한 뒤 브라우저를 연다. 생략하면 서버만 시작한다.
기본 포트는 backend `127.0.0.1:8000`, frontend `127.0.0.1:5173`이며 `Ctrl+C`로 둘 다 종료한다.
두 서버는 같은 DB·데이터 adapter 환경 변수를 상속하고 기본값은 PIT mock adapter다.

각 서버를 따로 실행하는 기존 명령도 사용할 수 있다.

```sh
# 저장소 루트 · 터미널 1
uv run server

# 저장소 루트 · 터미널 2
npm run dev
```

`uv run server`는 backend의 FastAPI/Uvicorn 개발 서버(`127.0.0.1:8000`, reload)를,
`npm run dev`는 frontend의 Vite 개발 서버(`localhost:5173`)를 실행한다. 백엔드 옵션은
그대로 전달된다(예: `uv run server --port 8123`). 기존처럼 `backend`와 `frontend`
디렉터리 안에서 각각 실행해도 같은 owner의 설정을 사용한다. HTTP 런타임의 저장 파일은 다음과
같다. 다른 위치가 필요하면 서버 시작 전에 해당 환경 변수를 설정한다.

| 기록 | 기본 경로 | 경로 환경 변수 |
| --- | --- | --- |
| 전략 리비전 | `backend/.local/strategy-revisions.sqlite3` | `STRATEGY_WORKBENCH_DB_PATH` |
| 백테스트·실험 | `backend/.local/research.sqlite3` | `STRATEGY_WORKBENCH_RESEARCH_DB_PATH` |
| 별도 연속 계좌 평가 | `backend/.local/continuous-accounts.sqlite3` | `STRATEGY_WORKBENCH_CONTINUOUS_DB_PATH` |

연속 평가 DB에는 요청 ID·진행·완료 결과를 별도로 보존한다. 같은 요청의 재전송은 다시 실행하지
않으며 서버 재시작 때 미완료 평가는 `interrupted`로 남는다. 서버 자체는 Rust 확장 없이도 시작하지만,
기본 `rust` core 백테스트는 위 `maturin develop` 설치가 없으면 명시적인 `CoreUnavailable`로
실패하며 Python core로 조용히 대체하지 않는다.

화면별 사용법과 오류 복구는 [사용자 매뉴얼](docs/manual/strategy-workbench/README.md)에 있다.
파라미터 탐색 실험 상세에서는 [지도와 후보 선택](docs/planning/validation-lab/V5-04-candidate-map.md)으로
칸과 주변 성과를 비교하고, 학습 창·이유를 명시해 선택 기록과 해당 실행의 결과 검증 카드를 읽는다.
선택 당시 N·DSR은 현재 원장 조회값과 구분하며 추천을 실거래 판단으로 사용하지 않는다.
매뉴얼 스크린샷은 두 서버를 격리 DB로 실행한 뒤 `frontend`에서
`npm run docs:capture`로 실제 브라우저 시나리오를 다시 실행해 갱신한다.

## 실데이터 연결

카엘 서버의 equity 층(29표, 현재 빌드 약 1.5GB)을 SFTP 읽기 전용 계정으로 로컬에 받아 워크벤치와
엔진이 읽는다. 절차·판단 기준의 정본은 [database/docs/LEDGER_SYNC.md](database/docs/LEDGER_SYNC.md)다.

먼저 두 가지를 준비한다. 서버 주소와 SFTP 계정은 저장소에 두지 않으므로 `setx QL_SYNC_HOST <주소>`·
`setx QL_SYNC_USER <계정>`으로 사용자 환경변수에 한 번 넣는다(새 창부터 적용, 계정은 서버 운영자에게
받는다). 빠지면 `sync`가 접속 전에 `error: server host is not set` 또는 `error: server user is not set`으로
끝난다. 동기화 래퍼는 backend 환경의 duckdb를 쓰므로 위 [바로 실행](#바로-실행)의 `uv sync`처럼
`equity` extra를 설치해 둔다.

```powershell
# 1) 전량 수신 + 판본·파일·내용 검증 + 카탈로그 재생성 (~/quant-ledger/data/equity)
database\scripts\ledger_sync.ps1 sync

# 2) 매일 10:00 KST 증분 동기화 등록(서버 아침 확정판 뒤). 판본이 바뀐 표만 받고 같은 파티션은 재사용한다
database\scripts\register_daily_sync.ps1

# 3) 워크벤치를 실데이터로 기동
$env:STRATEGY_WORKBENCH_EQUITY_ADAPTER = "duckdb"
$env:STRATEGY_WORKBENCH_EQUITY_ROOT = "$HOME\quant-ledger\data\equity"
uv run server
```

`ledger_sync verify`는 서버 MANIFEST의 current_build·파일 크기·파티션 `content_hash`(duckdb 재계산) 세
층위로 사본이 서버와 같은지 확인하고, `status --remote`는 서버보다 뒤처진 표를 보여 준다. 실데이터 위에서
그래프를 편집한 전략의 백테스트가 끝까지 도는 브라우저 시나리오는 `frontend`에서
`$env:E2E_REAL_EQUITY_ROOT = <루트>; npm run test:e2e`로 돌린다(변수가 없으면 mock 릴리스 게이트만 돈다,
`frontend/e2e/README.md`). 실데이터 security_id는 `{ticker}:{span_seq}`(예: `005930:1`)이며 실행 설정의
벤치마크는 비우면 벤치마크 없이 실행한다.

기동 뒤에는 서버 로그의 부팅 경고를 한 번 본다. 경고 코드별 조치는 `database/docs/LEDGER_SYNC.md` §5,
코드별 뜻과 카탈로그에 기대는 필드는 `database/docs/EQUITY_FIELD_MAP.md` §3 「부팅 검사」가 정본이다.

## 검증: Zipline 대조

엔진 회계는 Zipline과의 세션 단위 equity 대조로 검증됐다 — buy-hold, 골든크로스, 슬리피지
(VolumeShare + 참여율 캡·GTC 이월), 공매도 buy-hold 네 시나리오 모두 최대 상대 오차 0
(1,619세션, KRX 원장 슬라이스에서 생성한 CSV). 실행 방법과 리포트는
`backend/tests/manual/README.md` 참고.

## 데이터

원장(KRX·키움·KIS·DART·WISE 수집분)은 카엘 서버가 정본이다. 저장소에는 데이터를 넣지 않는다.
협업자는 서버 SFTP로 equity 층만 받는다(`database/docs/LEDGER_SYNC.md`, 설계
`docs/superpowers/specs/2026-09-19-ledger-sftp-sync-design.md`). 원장 SQLite(`raw/`)는 매일 갱신되는
라이브 파일이라 받지 않는다. 수집·stage·equity 설계는 `database/README.md` 참고.

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

`CLAUDE.md`, `AGENTS.md`와 `.claude/rules/`에 정리되어 있다. 코드 주석과 GitHub Issue·PR의
제목, 본문, 댓글, 리뷰는 한글로 작성하며 세부 예외는 `collaboration-language.md`만 정본으로 삼는다.

| 파일                          | 범위                                 | 내용                                                               |
| ----------------------------- | ------------------------------------ | ------------------------------------------------------------------ |
| `code-style.md`               | `**/*.py`                            | 기존 헬퍼 재사용, 기능/정리 커밋 분리, ruff·pyright 게이트, 네이밍 |
| `python.md`                   | `**/*.py`                            | 성공/실패는 튜플 대신 Result 값 타입으로                           |
| `error-messages.md`           | `**/*.py`                            | 예외·로그에 재현 가능한 컨텍스트 포함                              |
| `testing.md`                  | `{backend,database,tools}/**/*.py`, `backend/rust/` | 테스트를 언제 쓰나·어떻게 쓰나·언제 지우나(커버리지는 기준 아님), real > fake > mock, 산출물 단언 금지, 런타임 상태 경로 격리 |
| `pr-review.md`                | 전체                                 | PR 본문 양식, 결함 보고 4요소                                      |
| `collaboration-language.md`   | 전체                                 | 코드 주석·Issue·PR 한글 작성 규칙의 단일 정본                      |
| `backend-package-boundary.md` | `backend/**/*.py`                    | 헥사고날 방향, facade, `DEPENDS_ON`, mock adapter 경계             |
| `strategy-workbench-sot.md`   | `backend/`, `frontend/`, `database/src/equity/`, 관련 spec·기획 패키지 | 전략·팩터·지표·상태의 단일 owner |
| `user-story-harness.md`       | `backend/`, `frontend/`, `docs/{product/user-stories,planning,manual}/` | 사용자 동작을 바꾸는 PR은 관련 유저 스토리와 e2e 태그를 같은 PR에서 갱신 |
| `frontend-fsd.md`             | `frontend/src/`                      | FSD 단방향, slice 격리, public API                                 |
| `frontend-api-state.md`       | frontend API/state                   | 생성 SDK, 서버·draft·UI 상태 소유권                                |
| `frontend-ui-quality.md`      | frontend UI                          | primitive, token, 접근성, i18n, raw metric                         |
| `frontend-testing.md`         | frontend test/e2e                    | 사용자 동작·wire 경계 테스트, 프론트 경로의 쓰나·지우나 기준          |
| `frontend-react-effects.md`   | `frontend/src/`                      | React 밖 콜백이 읽는 상태는 commit 시점에 동기화(`useCommittedRef`) |
