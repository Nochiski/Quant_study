---
paths:
  - "frontend/src/**/*.ts"
  - "frontend/src/**/*.tsx"
  - "frontend/src/**/*.css"
---

# 프론트엔드는 FSD 단방향으로 배치한다

이 규칙은 `michelo.frontend`의 `fsd-architecture`, `slice-naming`, `shared-boundary` 규칙을
웹 Strategy Workbench에 맞게 차용한다.

## 레이어

```text
app -> pages -> widgets -> features -> entities -> shared
```

- 상위 레이어는 아래 레이어만 import한다.
- 같은 레이어의 서로 다른 slice는 직접 import하지 않는다. 조합은 한 단계 위에서 한다.
- type-only import도 같은 규칙을 따른다.
- slice 외부 import는 해당 slice의 `index.ts` public API만 거친다. slice 내부는 상대 경로를
  쓴다.
- `app`은 router/provider/composition, `pages`는 route 화면, `widgets`는 큰 UI 조합,
  `features`는 사용자 행동, `entities`는 도메인 명사, `shared`는 도메인 무관 기반이다.

## 이름과 owner

- entity는 명사형: `strategy`, `factor`, `experiment`, `metric`, `dataset`.
- feature는 동사형: `edit-strategy`, `debug-strategy`, `compare-candidates`,
  `inspect-run`.
- `form.ts`, `use-data.ts`, `manager.ts`처럼 slice를 떼면 의미가 사라지는 이름은 금지한다.
- `shared`나 범용 app/widget에 특정 팩터·전략·실험 분기를 두지 않는다. 그 도메인이 사라질
  때 함께 지울 코드라면 owner slice로 내린다.
- 복수 entity 조합은 widget/page가 한다. feature가 다른 feature를 import하지 않는다.

`eslint-plugin-boundaries`가 레이어 방향, 같은 레이어 격리, public API 우회를 `npm run lint`에서
강제한다. 린터 예외를 추가하지 말고 owner slice를 다시 설계한다.
