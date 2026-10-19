---
paths:
  - "backend/**/*.py"
  - "backend/README.md"
---

# 백엔드는 경로가 의존성 방향을 말하게 한다

이 규칙은 `Library.michelo/.claude/rules/package-boundary.md`의
`묶음 → 도메인 노드 → facade → DEPENDS_ON` 방식을 Strategy Workbench의 헥사고날 구조에
맞게 이식한 것이다.

## 고정 방향

```text
adapters/inbound  ─┐
                   ├─> application ─> domain
adapters/outbound ─┘          │ ^
                              │ └── application (같은 층, 조건부)
                              └─> application/<use_case>/ports/outgoing

bootstrap ─> application + adapters
```

- `domain/<domain>`: 순수 정책·값 타입. application, adapter, HTTP, DB, 파일, 환경 변수,
  `backtest_engine`을 import하지 않는다.
- `application/<use_case>`: 유스케이스와 그 유스케이스가 요구하는 port를 소유한다.
  concrete adapter를 import하지 않는다.
- domain·application은 numpy(core 의존성)를 순수 배열 계산에만 쓴다. pyarrow·duckdb는 adapter에만
  둔다. pyarrow는 optional extra `parquet`이라 그 extra 없는 운영 설치에서 import가 깨지고, duckdb는
  IO다(columnar-tape spec D2). CI `backend-no-extras` job은 dev 그룹으로 pyarrow를 설치하므로 이 경계를
  잡지 못하고, 아래 `test_forbidden_imports.py`가 막는다.
- application → application 화살표는 한 유스케이스가 **다른 유스케이스의 outgoing port를
  소비할 때만** 허용하며, port owner는 그 계약을 먼저 정의한 유스케이스다. 현재 선언된 6개는
  `strategy_authoring → strategy_design`, `portfolio_design → strategy_design`,
  `portfolio_design → factor_research`, `backtest_run → strategy_design`,
  `backtest_run → portfolio_design`, `assistant_chat → equity_workspace`이다(마지막은 AI
  어시스턴트가 데이터 필드 카탈로그를 `EquityDataPort`로 읽기 위한 의존, A-01에서 추가).
  `portfolio_design → strategy_design`은 scoped trace의 saved revision을 repository port로
  해소하기 위한 의존이다. 유스케이스 로직을 빌려 쓰려고 거는 화살표는 아니다.
- `adapters/inbound/<transport>`: HTTP/SSE/CLI 입력을 application 명령·조회로 변환한다.
- `adapters/outbound/<provider>`: application이 요구한 port를 DB/파일/엔진으로 구현한다.
  도메인 정책을 새로 판단하지 않는다. 도메인 변환 규칙을 어댑터가 다시 구현하지 않는다.
  `document_codec`은 domain의 `UPGRADE_STEPS`를 주석·순서를 보존하는 컨테이너 위에서 실행만
  하며, 결과가 dict 경로와 같은 tree를 내는지는 application이 다시 parse해 검사한다.
- adapter → adapter 의존은 선언된 공용 책임 재사용에 한정한다: SQLite 저장 어댑터는
  무의존 `sqlite_store`의 연결·DDL·시간 규칙을, `backtest_engine`은 `engine_portfolio`의
  엔진 요구사항·목표 변환을 읽는다. 이 두 간선은 도메인 정책을 복제할 근거가 아니다.
- S03 연구 실행은 `application/signal_research`가 자체 outgoing `SnapshotGuardPort`·
  `SignalPipelinePort`·`SignalLabelDataPort`를 소유한다. `adapters/outbound/research_snapshot`은
  기존 S01 검증 CLI를 UTF-8 subprocess로 연결하고, `adapters/outbound/signal_pipeline`은
  `application/portfolio_design` facade를 재사용해 기존 raw/factor pipeline 결과를 변환한다.
  `research_snapshot`은 현재 로드된 `strategy_workbench`·`backtest_engine`의 module origin과
  package search path를 S01 검증 대상 `backend/src`에 묶는다. 소스 checkout/editable과
  symlink resolve 일치를 지원하며 별도 wheel 위치는 unsupported로 차단한다. 메모리 변조
  attestation은 이 출처 검증의 범위 밖이다.
  이 bridge가 기존 서비스 호출을 담당하며 새 application에서 다른 application 서비스나
  concrete adapter를 import하지 않는다. `equity_duckdb`의 연구 label adapter는 자체 노드의 기존
  가격·달력·bar 조회와 원래 security_span을 재사용하며 연구 port와 `domain/signal_evaluation`에
  의존한다. 원장 `CellKind.MASKED` 키를 label port의 `adjustment_gaps`로 전달하며,
  개별 진입~만기 구간의 `adjustment_gap` 판정은 순수 evaluator가 소유한다.
  보존 검증·팩터 식·가격/상장 계약과 원장 가림 술어를 복제하거나 완화하지 않는다.
  `adapters/inbound/signal_research_cli`는 요청 해석·JSON 내보내기만 맡고 `bootstrap/signal_research`가
  별도 CLI composition root로 구체 구현을 조립한다. 기존 HTTP bootstrap·API/UI는 변경하지 않는다.
  상세 계약은 `docs/planning/research-to-trading/S03-runner.md`다.
- S05 오프라인 실행은 `domain/order_execution`(정수 수량·KRW 값/누적 사실 전이·대사),
  `application/offline_execution`(자체 `BrokerPort`·`LedgerPort`, 전송·복구 제어),
  `adapters/outbound/execution_sqlite`(전용 의도 원장)·`adapters/outbound/fake_broker`(네트워크 없는
  장애 fixture)가 소유한다. `execution_sqlite → sqlite_store`는 위 공용 연결·DDL 규칙의 재사용이며
  실행 DB의 스키마·owner는 execution_sqlite에 남는다. application은 두 concrete adapter를
  import하지 않는다. fake의 증분 `fill`은 저장 전 `domain/order_execution`의 `classify_event`를
  재사용하며, 지정가 위반을 포함한 검증 실패 시 주문·revision·계좌를 바꾸지 않는다.
  이 slice의 조립은 호출자/테스트가 명시적으로 수행한다. 기존 bootstrap·HTTP에는
  연결하지 않는다. 상세 실행 계약은 `docs/planning/research-to-trading/S05-offline-execution.md`다.
- 완료 run의 계좌 보고서는 `domain/backtest`의 순수 projection·전용
  `facade/account_report.py`가 금액·상태·초기자본 기준 해석을 소유한다.
  `application/backtest_run`은 `self.result(run_id)`의 완료·해시·역직렬화 검증을 한 번 재사용한 뒤
  projection을 호출하고 application facade를 통해 응답 타입을 공개한다. HTTP는 이를 별도
  `GET /api/v1/backtests/{run_id}/account-report`로 제공하며 기존 결과 오류를 보존한다.
  adapter·frontend는 금액을 다시 계산하지 않으며 assistant용 오류→None 포트를 재사용하지 않는다.
  기존 결과 저장·엔진·일반 analytics·시도 원장은 이 조회 책임에 포함하지 않는다.
- 완료 run의 검증 보고서는 같은 `application/backtest_run`이 `self.result(run_id)`를 한 번 검증한 뒤
  manifest의 실행 입력·저장 출처로 기존 `_lineage`·`trial_ledger`를 재사용한다. 인라인 출처는 기존
  `_resolve`와 동등하게 provenance를 None으로 넘겨 명시한 계열을 보존하며, 은퇴 리비전을 재접수
  검사하지 않는다. `domain/experiment`의 `run_deflated_sharpe_evidence`가 DSR 값·사유·집계 근거를
  함께 판정하며 기존 scalar·후보 선택도 이 판정을 재사용한다. application이 조회 시각을 주입하고,
  HTTP의 별도 읽기 전용 `GET /api/v1/backtests/{run_id}/validation-report`가 그대로 노출한다.
  저장된 FULL PSR은 재계산하지 않으며 결과·시도 수·선택 스냅숏을 갱신하지 않는다.
- 완료 run의 팩터 회귀(V4-05)도 같은 `application/backtest_run`이 `self.result(run_id)`를 한 번
  검증한 뒤, 이미 주입된 `PortfolioDesignService.run_pipeline`을 run의 실행 설정으로 다시 돌려 유니버스
  단면을 읽는다. 새 port·application 화살표는 없다. 모멘텀·가치 기본 graph를 레지스트리에서 읽으려고
  facade `DEPENDS_ON`에 `domain.factor`를 더했고, 레지스트리는 bootstrap이 주입한다. 팩터 수익률·회귀
  규칙은 `domain/analytics`가 소유하며 HTTP `GET /api/v1/backtests/{run_id}/factor-regression`이 그대로
  노출한다.
- V6-01 모의 사전등록은 `domain/backtest`의 동결 등록·필수 기준 비교와
  `application/holdout_registration`의 자체 source-pin·simulation-executor·ledger 포트가 소유한다.
  다른 application 서비스를 직접 import하지 않는다. 기존 `research_sqlite`가 schema v9의
  별도 simulation 등록·claim·결과 테이블과 commit을 맡는다. commit 성공 반환 뒤에만 executor를
  호출하며 실제 원천 adapter·HTTP·bootstrap 연결은 없다. 기존 trial/N·blocked-attempt를 쓰지 않는다.
- `bootstrap`: concrete adapter를 선택하고 주입하는 유일한 composition root다.
- 연속 계좌 평가는 `application/continuous_account`가 자체 실험 출처·파이프라인·실행·이력
  포트를 소유하고, 기존 `application/backtest_run`의 outgoing 데이터 포트만 재사용한다.
  `continuous_pipeline` outbound bridge가 기존 실험·저장 리비전·선정 파이프라인 facade를
  연결하며 application끼리 서비스를 import하지 않는다. 이 bridge가 `engine_portfolio`의
  요구사항 판정을 재사용하는 adapter 간선과 `continuous_account_sqlite → sqlite_store`의
  공용 연결 간선을 허용한다. 새로운 선정·회계 정책을 복제하는 근거는 아니다.
  별도 `backtest_engine/_continuous.py`는 같은 노드의 `_run_kernel`·`_artifacts`와 기존
  analytics owner를 재사용한다. 혼합 후보를 단일 전략 `BacktestRunResult`로 위장하지 않는다.
  별도 SQLite 이력의 단일 running claim·요청 UUID 멱등성·시작 시 interrupted 처리는
  단일 서버 프로세스 배포 계약이며 기존 실험 worker·trial key·계열 원장을 변경하지 않는다.
- `backend/src/backtest_engine`/`backend/rust/backtest_core`는 실행 커널이다. 커널을 import하는
  노드는 둘이다: `adapters/outbound/backtest_engine`(실행·벤치마크 곡선)과
  `adapters/outbound/engine_portfolio`(엔진 요구사항·능력 판정, 목표 행동 변환). 그 밖의 노드에서
  커널을 import하지 않는다. 이 경계를 막는 architecture 테스트는 아직 없어 리뷰로 지킨다.
- 백테스트 측정 세션 계약은 `application/backtest_run`의 `BacktestDataset.sessions`가 소유한다.
  기본값 없는 keyword-only 입력이며 equity adapter가 실제 공통 거래일 달력을 답한다.
  dataset은 오름차순·중복 없음·측정 bar의 달력 포함을 검증하고 `backtest_engine` adapter는
  bar가 전혀 없는 거래일도 빈 세션으로 커널에 전달한다. 선택 종목·벤치마크 bar의 합집합으로
  달력을 재구성하지 않아 DAY 만료와 벤치마크 선택에 독립적인 실행 일정을 유지한다.

## 노드와 facade

- 의존 그래프의 노드는 `<layer>/<responsibility>`다. 묶음 폴더(`domain`, `application`,
  `adapters`) 자체는 노드가 아니며 그 루트에 구현을 두지 않는다.
- 모든 노드는 `facade/` 폴더를 갖고, `facade/__init__.py`에
  `DEPENDS_ON: tuple[str, ...]`을 선언한다.
- 다른 노드의 심볼은 `<node>.facade.<responsibility>` 경로로만 import한다. 내부 파일 deep
  import와 package root 재수출은 금지한다.
- facade 파일은 책임 하나를 이름으로 드러낸다. `facade.py`, `common.py`, `shared.py`,
  `misc.py`, `utils.py`, `io.py` 같은 owner 없는 이름을 만들지 않는다.
- facade 파일마다 `__all__`이 있어야 한다. `facade/__init__.py`는 의존 선언만 갖고 심볼을
  재수출하지 않는다.
- facade에는 경계 검증/Result wrapping이 있는 진입 함수 또는 타입·예외·클래스 재수출만
  둔다. 단순 pass-through wrapper는 만들지 않는다.

## Equity adapter

- 조회 계약 SoT는 `application/equity_workspace/ports/outgoing/equity_data.py`다. raw PIT
  관측 계약은 `application/portfolio_design/ports/outgoing/raw_observations.py`가 별도로
  소유하며, 두 포트는 같은 셀에 같은 값·공개일을 답해야 한다. 같은 디렉터리의 열 포트
  `raw_panel.py`(`RawPanel`)는 이 원시 계약을 열로 담을 뿐 정본이 아니다(columnar-tape T2-01).
- 구현은 둘이다. `adapters/outbound/equity_mock`은 테스트·e2e용 결정적 fixture이며 PIT
  available-date, revision, recommended lag, 실제 0/결측/미수집/coverage gap/원장이 가린 셀(masked)을
  구분한다.
  `adapters/outbound/equity_duckdb`는 실데이터(로컬 원장)를 읽는다. 어느 쪽을 쓸지는
  composition root가 정한다: `bootstrap/_http.py`가 `STRATEGY_WORKBENCH_EQUITY_ADAPTER`(기본
  `mock`)와 `STRATEGY_WORKBENCH_EQUITY_ROOT`를 읽고, 허용 값 목록은 `bootstrap/_container.py`의
  `EQUITY_ADAPTERS`다.
- 두 어댑터가 같은 field_id에 답하는 필드 계약(단위·값 타입·랙·빈도)의 owner는
  `.claude/rules/strategy-workbench-sot.md`의 equity 필드 계약 행이다.
- 실제 DB에 맞춰 domain/application을 DB 스키마대로 바꾸지 않는다. `equity_duckdb`가 port에
  맞춘다.
- 설정한 adapter가 없거나 실패하면 mock으로 조용히 fallback하지 않는다.
  이 금지는 adapter를 통째로 갈아 끼우는 것에 대한 것이다. `equity_duckdb`가 원장
  `dataset_profile`에 행이 없는 필드를 선언표 상수로 읽는 랙 폴백은 허용하되, 그 상수는 원장
  선언과 같아야 하고(`tests/contract/test_equity_fallback_lag.py`) 부팅 경고
  `profile_lag_fallback`으로 드러낸다.
- mock의 raw PIT port(`load_raw_observations`)는 fixture 달력 안에서는 `load_panel`과 같은
  Observation 행·PIT cut-off를 읽고, 달력 밖에서는 절대 영업일 index 기반 synthetic 시계열을
  만든다. 두 경우 모두 (security, date)만의 함수이며 query window에 의존하지 않는다. 실패는
  `status`/`detail` 값으로 돌려주고 미지 field·universe를 합성하지 않는다. 백테스트 데이터
  포트(`load_backtest_dataset`)도 fixture 밖 종목·벤치마크를 합성하지 않고 duckdb처럼 거절한다(#361).

## 강제 게이트

`backend/tests/architecture/test_dependency_direction.py`가 다음을 실패시킨다.

- 선언되지 않은 cross-node import
- 다른 노드의 facade를 우회한 deep import
- `DEPENDS_ON`에 없는 노드 또는 의존 순환

import는 절대·상대 표기를 가리지 않는다. 게이트가 파일 위치로 상대 import를 절대 모듈명으로
복원해 같은 두 검사에 태운다 (DEFECT-101 이전에는 `level == 0`만 봐서 상대 표기가 검사 밖이었다).

같은 `backend/tests/architecture/` 폴더의 게이트가 셋 더 있다.

- `test_forbidden_imports.py`: domain·application은 pydantic·pyarrow·duckdb를 import하지 않고, 누구도 PyYAML을
  import하지 않으며(YAML 1.2 파서는 ruamel.yaml 하나), 공급자 SDK(`anthropic`·`openai`)는 자기
  outbound adapter 안에만 있다. AST의 `import` 문만 보므로 문자열 import는 리뷰로 확인한다.
- `test_run_environment_ownership.py`: 실행 설정은 `RunEnvironment` 하나가 소유한다. `src`
  전체에서 옛 전략 문서 경로(`data`·`execution`·`missing_policy`) 읽기가 0건이다.
- `test_turn_budget_constants.py`: AI 턴 예산 상수를 공급자 adapter가 다시 선언하지 않는다.

새 위반은 whitelist에 넣지 말고 owner나 방향을 다시 설계한다.
