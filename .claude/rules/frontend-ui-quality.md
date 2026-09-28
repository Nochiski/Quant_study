---
paths:
  - "frontend/src/**/ui/**"
  - "frontend/src/**/*.tsx"
  - "frontend/src/**/*.css"
  - "frontend/src/shared/config/messages.ts"
---

# UI는 primitive·토큰·접근성 표면을 먼저 쓴다

이 규칙은 `michelo.frontend`의 `ui-components.md`, `styling.md`, `i18n-keys.md`,
`error-reporting.md`를 차용한다.

- 새 UI를 만들기 전에 `shared/ui`와 이미 선택된 component library를 검색한다. 같은 interaction을
  수제로 다시 만들지 않는다.
- 정적 스타일은 slice `ui/` 안의 CSS 파일 class(전역 reset·타이포는 `app/styles/`)로 쓰고,
  런타임 연속값만 inline style을 쓴다. Tailwind 같은 utility CSS 프레임워크는 들어와 있지 않다.
- 색·간격·폰트는 `app/styles/tokens.css`의 semantic design token(CSS 변수)을 사용한다. 원시 색 값은
  `tokens.css`에만 두고 컴포넌트 CSS에 하드코딩 hex를 쓰지 않는다. `!important`는 새로 쓰지 않는다
  (지금 있는 것은 `base.css`의 `[hidden]`·reduced-motion 재정의와 `dirty-leave-guard.css` 하나다).
- 버튼·입력·표·탭은 semantic role과 accessible name을 갖는다. 키보드 조작과 focus-visible을
  완료 조건에 포함한다.
- 사용자 문구는 `<domain>.<area>.<phrase>` i18n 키를 쓰고 ko/en을 함께 변경한다(사전은 `shared/config/messages.ts`). 컴포넌트
  이름을 최상위 namespace로 쓰지 않는다.
- 사용자 에러는 번역된 복구 문구를, 개발 로그는 run/experiment/trial/spec 식별자와 기대/실제
  값을 담는다. 토큰·자격증명·서버 절대 경로는 노출하지 않는다.
- 성과 화면은 CAGR, Sharpe, Sortino, Calmar, MDD, 변동성, 회전율, 비용, 거래 수 같은 raw
  metric을 숨기지 않는다. 종합 score만 보여주는 화면은 금지한다.
