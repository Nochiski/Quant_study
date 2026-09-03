---
paths:
  - "frontend/src/**/ui/**"
  - "frontend/src/**/*.tsx"
  - "frontend/src/**/*.css"
  - "frontend/src/app/i18n/**"
---

# UI는 primitive·토큰·접근성 표면을 먼저 쓴다

이 규칙은 `michelo.frontend`의 `ui-components.md`, `styling.md`, `i18n-keys.md`,
`error-reporting.md`를 차용한다.

- 새 UI를 만들기 전에 `shared/ui`와 이미 선택된 component library를 검색한다. 같은 interaction을
  수제로 다시 만들지 않는다.
- 정적 스타일은 utility class, 런타임 연속값만 inline style을 쓴다.
- 색·간격·폰트는 semantic design token을 사용한다. 하드코딩 hex, 임의 Tailwind palette,
  `!important`, `@apply`는 금지한다.
- 버튼·입력·표·탭은 semantic role과 accessible name을 갖는다. 키보드 조작과 focus-visible을
  완료 조건에 포함한다.
- 사용자 문구는 `<domain>.<area>.<phrase>` i18n 키를 쓰고 ko/en을 함께 변경한다. 컴포넌트
  이름을 최상위 namespace로 쓰지 않는다.
- 사용자 에러는 번역된 복구 문구를, 개발 로그는 run/experiment/trial/spec 식별자와 기대/실제
  값을 담는다. 토큰·자격증명·서버 절대 경로는 노출하지 않는다.
- 성과 화면은 CAGR, Sharpe, Sortino, Calmar, MDD, 변동성, 회전율, 비용, 거래 수 같은 raw
  metric을 숨기지 않는다. 종합 score만 보여주는 화면은 금지한다.
