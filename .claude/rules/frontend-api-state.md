---
paths:
  - "frontend/src/**/api/**"
  - "frontend/src/**/model/**"
  - "frontend/src/**/hooks/**"
  - "frontend/src/app/**"
  - "frontend/src/pages/**"
  - "frontend/src/shared/**"
---

# API 계약과 상태 owner를 복제하지 않는다

이 규칙은 `michelo.frontend`의 `api-sdk.md`와 `state-ownership.md`를 차용한다.

## API

- 모든 REST/SSE 호출과 요청·응답 타입은 `frontend/src/shared/api`의 생성된 OpenAPI SDK
  게이트웨이를 거친다. raw `fetch`/axios와 수기 DTO mirror는 신규 코드에서 금지한다.
- backend 계약이 아직 없으면 frontend 타입을 임시로 복제하지 않는다. backend schema를 먼저
  만들고 SDK를 다시 생성한다.
- binary download, SSE stream처럼 generator가 표현하지 못하는 wire 예외만 typed wrapper 한
  곳에 두고 사유를 남긴다.
- frontend validation은 빠른 피드백용이다. 저장·실행 가능 여부의 최종 판정과 오류 코드는
  backend가 소유한다.
- backend가 코드로 분기하는 422(`ApiRequestError.code`)는 `upgrade.error.<code>`·
  `backtest.error.<code>`처럼 코드를 키로 하는 문구로 번역한다. 번역이 없으면 일반 문구로
  떨어지되 원문 detail을 그대로 노출하지 않는다.
- 폴링 본문의 실패 코드(`BacktestRunState.error_code`, 어휘 SoT는 backend `RunFailureCode`)는
  `backtest.run.error.<code>`로 번역하고, 번역이 있으면 서버 원문(`error`)은 접힌 진단 상세로
  내린다. 422의 `backtest.error.*`와 키를 공유하지 않는다 — 편집기 툴바는 서버 detail을 본문으로
  쓰는 화면이라 같은 키가 있으면 detail이 덮인다.

## 상태

- REST 서버 데이터는 query cache가 소유한다. client store에 응답을 복제하지 않는다.
- client store는 미저장 StrategySpec draft, 선택 ID, 패널 배치 같은 UI 상태만 소유한다.
- 저장된 StrategySpec revision과 미저장 draft를 같은 변수로 표현하지 않는다.
- 선택 객체 대신 `selectedId`, 계산 가능한 합계/score 대신 원본 입력만 저장한다.
- 상태 초기화 규칙은 그 상태 owner의 reducer/store action 안에 둔다. JSX `key`로 비즈니스 상태
  reset을 숨기지 않는다.
- SSE progress는 query cache에 병합하고, 매우 높은 빈도의 순수 표시값만 local store/ref를
  검토한다.
- 예외 하나: AI 어시스턴트의 턴 투영(`entities/assistant`의 채팅 리듀서)은 local이 owner다. 서버
  이력(`GET /sessions/{id}`)의 owner는 여전히 query cache이고, 리듀서는 그 이력과 SSE 이벤트를 **같은
  멱등 경로**로 접는 파생 투영이다. 토큰 단위로 오는 텍스트 델타를 캐시에 다시 쓰지 않으려는 것이며,
  spec D9도 "마지막 반영 sequence"를 frontend local state로 지정한다. 이력은 덮어쓰지 않고 **턴별**
  sequence watermark로 병합한다 — 세션 하나짜리 watermark로 판정하면 스트림이 이력보다 먼저 붙었을 때
  앞선 턴의 이벤트가 조용히 사라진다.
- graph 좌표·zoom·panel open 상태는 StrategySpec이 아니라 별도 UI metadata다.
