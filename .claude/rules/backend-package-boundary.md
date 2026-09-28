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
- `bootstrap`: concrete adapter를 선택하고 주입하는 유일한 composition root다.
- `backend/src/backtest_engine`/`backend/rust/backtest_core`는 실행 커널이다. 커널을 import하는
  노드는 둘이다: `adapters/outbound/backtest_engine`(실행·벤치마크 곡선)과
  `adapters/outbound/engine_portfolio`(엔진 요구사항·능력 판정, 목표 행동 변환). 그 밖의 노드에서
  커널을 import하지 않는다. 이 경계를 막는 architecture 테스트는 아직 없어 리뷰로 지킨다.

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
  소유하며, 두 포트는 같은 셀에 같은 값·공개일을 답해야 한다.
- 구현은 둘이다. `adapters/outbound/equity_mock`은 테스트·e2e용 결정적 fixture이며 PIT
  available-date, revision, recommended lag, 실제 0/결측/미수집/coverage gap을 구분한다.
  `adapters/outbound/equity_duckdb`는 실데이터(로컬 원장)를 읽는다. 어느 쪽을 쓸지는
  composition root가 정한다: `bootstrap/_http.py`가 `STRATEGY_WORKBENCH_EQUITY_ADAPTER`(기본
  `mock`)와 `STRATEGY_WORKBENCH_EQUITY_ROOT`를 읽고, 허용 값 목록은 `bootstrap/_container.py`의
  `EQUITY_ADAPTERS`다.
- 두 어댑터가 같은 field_id에 답하는 필드 계약(단위·값 타입·랙·빈도)의 owner는
  `.claude/rules/strategy-workbench-sot.md`의 equity 필드 계약 행이다.
- 실제 DB에 맞춰 domain/application을 DB 스키마대로 바꾸지 않는다. `equity_duckdb`가 port에
  맞춘다.
- 설정한 adapter가 없거나 실패하면 mock으로 조용히 fallback하지 않는다.
- mock의 raw PIT port(`load_raw_observations`)는 fixture 달력 안에서는 `load_panel`과 같은
  Observation 행·PIT cut-off를 읽고, 달력 밖에서는 절대 영업일 index 기반 synthetic 시계열을
  만든다. 두 경우 모두 (security, date)만의 함수이며 query window에 의존하지 않는다. 실패는
  `status`/`detail` 값으로 돌려주고 미지 field·universe를 합성하지 않는다.

## 강제 게이트

`backend/tests/architecture/test_dependency_direction.py`가 다음을 실패시킨다.

- 선언되지 않은 cross-node import
- 다른 노드의 facade를 우회한 deep import
- `DEPENDS_ON`에 없는 노드 또는 의존 순환

import는 절대·상대 표기를 가리지 않는다. 게이트가 파일 위치로 상대 import를 절대 모듈명으로
복원해 같은 두 검사에 태운다 (DEFECT-101 이전에는 `level == 0`만 봐서 상대 표기가 검사 밖이었다).

같은 `backend/tests/architecture/` 폴더의 게이트가 셋 더 있다.

- `test_forbidden_imports.py`: domain·application은 pydantic을 import하지 않고, 누구도 PyYAML을
  import하지 않으며(YAML 1.2 파서는 ruamel.yaml 하나), 공급자 SDK(`anthropic`·`openai`)는 자기
  outbound adapter 안에만 있다. AST의 `import` 문만 보므로 문자열 import는 리뷰로 확인한다.
- `test_run_environment_ownership.py`: 실행 설정은 `RunEnvironment` 하나가 소유한다. `src`
  전체에서 옛 전략 문서 경로(`data`·`execution`·`missing_policy`) 읽기가 0건이다.
- `test_turn_budget_constants.py`: AI 턴 예산 상수를 공급자 adapter가 다시 선언하지 않는다.

새 위반은 whitelist에 넣지 말고 owner나 방향을 다시 설계한다.
