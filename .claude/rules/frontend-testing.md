---
paths:
  - "frontend/src/**/__tests__/**"
  - "frontend/src/**/*.test.ts"
  - "frontend/src/**/*.test.tsx"
  - "frontend/e2e/**"
---

# 프론트 테스트는 사용자 동작과 wire 경계를 검증한다

이 규칙은 `michelo.frontend`의 `testing.md`와 `e2e-impact.md`를 차용한다.

- 사용자 대면 동작이 같을 때 깨지는 내부 구현 테스트를 만들지 않는다. 내부 state, private
  method, 자식 컴포넌트, CSS class를 직접 단언하지 않는다.
- query 우선순위는 role > label > text > test id다. role을 줄 수 있는 요소에 test id로
  우회하면 컴포넌트 접근성을 먼저 고친다.
- 상호작용이나 조건 분기가 없는 단순 존재 테스트는 만들지 않는다.
- API 모듈 자체를 mock하지 않고 MSW로 wire 요청·응답·오류를 검증한다.
- Quick Builder ↔ Advanced Graph ↔ StrategySpec round-trip은 property/generative test로
  lossless를 검증한다.
- UI/API/i18n 변경 시 관련 Playwright spec의 test id, 문구, API path 영향을 검색하고 함께
  수정한다.
- mock 성공 경로뿐 아니라 PIT 경고, invalid spec, failed/pruned trial, cancellation, 부분 결과
  표시를 테스트한다.
