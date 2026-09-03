---
paths:
  - "frontend/src/**/api/**"
  - "frontend/src/**/model/**"
  - "frontend/src/**/hooks/**"
  - "frontend/src/app/**"
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

## 상태

- REST 서버 데이터는 query cache가 소유한다. client store에 응답을 복제하지 않는다.
- client store는 미저장 StrategySpec draft, 선택 ID, 패널 배치 같은 UI 상태만 소유한다.
- 저장된 StrategySpec revision과 미저장 draft를 같은 변수로 표현하지 않는다.
- 선택 객체 대신 `selectedId`, 계산 가능한 합계/score 대신 원본 입력만 저장한다.
- 상태 초기화 규칙은 그 상태 owner의 reducer/store action 안에 둔다. JSX `key`로 비즈니스 상태
  reset을 숨기지 않는다.
- SSE progress는 query cache에 병합하고, 매우 높은 빈도의 순수 표시값만 local store/ref를
  검토한다.
- graph 좌표·zoom·panel open 상태는 StrategySpec이 아니라 별도 UI metadata다.
