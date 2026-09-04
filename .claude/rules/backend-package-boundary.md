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
  소비할 때만** 허용하며, port owner는 그 계약을 먼저 정의한 유스케이스다. 현재 선언된 3개는
  `strategy_authoring → strategy_design`, `backtest_run → strategy_design`,
  `backtest_run → portfolio_design`이다. 유스케이스 로직을 빌려 쓰려고 거는 화살표는 아니다.
- `adapters/inbound/<transport>`: HTTP/SSE/CLI 입력을 application 명령·조회로 변환한다.
- `adapters/outbound/<provider>`: application이 요구한 port를 DB/파일/엔진으로 구현한다.
  도메인 정책을 새로 판단하지 않는다.
- `bootstrap`: concrete adapter를 선택하고 주입하는 유일한 composition root다.
- `backend/src/backtest_engine`/`backend/rust/backtest_core`는 실행 커널이다. 접근은 향후
  `adapters/outbound/backtest_engine`에서만 허용한다.

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

- 계약 SoT는 `application/equity_workspace/ports/outgoing/equity_data.py`다.
- 현재 기준 구현은 `adapters/outbound/equity_mock`이다. fixture는 결정적이고
  PIT available-date, revision, recommended lag, 실제 0/결측/미수집/coverage gap을 구분한다.
- 실제 DB가 와도 domain/application을 DB 스키마에 맞춰 바꾸지 않는다. 새
  `adapters/outbound/equity_duckdb`가 port에 맞춘다.
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

새 위반은 whitelist에 넣지 말고 owner나 방향을 다시 설계한다.
