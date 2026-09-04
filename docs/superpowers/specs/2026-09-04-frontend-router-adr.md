# ADR: Frontend router와 route composition (P0-04)

> 작성: 2026-09-04
>
> 상태: Accepted
>
> 상위 ADR: [Strategy Authoring Contract](./2026-09-04-strategy-authoring-contract-adr.md)
> · Initiative tracker: [PLAN.md](../../planning/strategy-workbench-yaml-ui/PLAN.md)

## 1. 맥락

현재 frontend는 단일 page(`StrategyBuilderPage`)이며 router가 없다. `?step=`, `?run` query만
`window.location`에서 직접 읽는다. WORKFLOW P2-02는 `/research/strategies/new`,
`/research/strategies/:id/revisions/:revision`, `/research/backtests/:runId`와 미래 `/operations/*`
namespace, dirty navigation blocker, view/path/date query 복원을 요구한다.

## 2. 비교

| 기준 | `@tanstack/react-router` 1.170 | `react-router` 8.3 |
|---|---|---|
| 타입 안전 path/search params | 내장 (`validateSearch`, 라우트별 typed search) | path만 타입 추론, search는 수동 |
| dirty navigation blocker | `useBlocker({ shouldBlockFn })` (react-router 패키지 d.ts 확인) | `useBlocker` (data router 필요) |
| TanStack Query 통합 | `loader`에서 `queryClient.ensureQueryData`, 같은 생태계 | 가능하나 별도 패턴 |
| lazy route | `createLazyRoute` / `lazyRouteComponent` | `lazy()` |
| not-found/error boundary | route별 `notFoundComponent`, `errorComponent` | `errorElement` |
| 번들 delta (gzip, React 19 baseline 59 KB 대비, Vite 8 실측) | +26 KB (85 KB) | +29 KB (88 KB) |
| FSD 적합성 | code-based route tree를 `app/`에 두면 `pages`만 import | 동일 |

## 2.1 Deep-link 스파이크 결과

재현 스크립트: [`docs/superpowers/spikes/2026-09-04-router-deeplink/`](../spikes/2026-09-04-router-deeplink/README.md)
(`@tanstack/react-router` 1.170.32, React 19.2.8 pin, lockfile 커밋, `node --test`, React 렌더링 없음).

| 검증 항목 (WORKFLOW P0-04) | 결과 |
|---|---|
| `/research/strategies/$strategyId/revisions/$revision` direct entry | params·typed search(`view`, `path`, `asOf`)·loader data 복원 |
| query parameter의 view/path 복원 | `validateSearch`가 잘못된 `view=bogus`를 `yaml`로 정규화하고 URL을 `?view=yaml`로 다시 씀 |
| loader `notFound()` | root match의 `error.isNotFound`로 노출 → route `notFoundComponent`가 렌더 |
| 알 수 없는 path | root만 match → root `notFoundComponent`(global not-found) |
| 기존 single-page entry | `/legacy/builder?step=portfolio&run=true`가 query를 typed search로 유지, memory history back/forward 동작 |
| redirect | `redirect({ to })`는 `isRedirect`인 값을 throw. `beforeLoad` redirect와 search 정규화의 후속 navigate는 `RouterProvider`가 수행 (스파이크는 `router.navigate()`로 대체) |
| lazy route | `lazyRouteComponent`가 `preload()` 전까지 import를 지연 |
| dirty navigation blocker | `useBlocker` export 확인. 동작 검증은 P2-04 vitest(렌더 필요) |

TanStack Query 통합(`loader`에서 `queryClient.ensureQueryData`)과 error boundary(`errorComponent`)는 API
존재만 확인했고 동작은 P2-02/P2-04 테스트에서 검증한다.

## 3. 결정

### D1. `@tanstack/react-router`를 채택한다

- code-based route tree만 쓴다. file-based routing plugin은 `app/`과 `pages/` 경계를 흐리므로 쓰지
  않는다.
- route tree는 `src/app/router/`가 소유한다. 각 route component는 `pages/<slice>` public API에서만
  import한다. `pages`는 router hook을 `shared/lib/router`의 얇은 re-export로 쓴다.
- search params는 route마다 `validateSearch`로 typed schema를 선언한다. view/path/date/security
  selection(WORKFLOW 2.6의 "URL 소유 상태")은 여기서 복원된다. 잘못된 값은 throw하지 않고 기본값으로
  정규화한다(router가 URL을 다시 쓴다). 공유 링크가 깨지지 않게 하기 위해서다.

### D2. Route tree (P2-02)

```text
/                                   → redirect /research/strategies/new
/research
  /strategies/new                   ← template 로드, draft base 상태
  /strategies/$strategyId/revisions/$revision
  /backtests/$runId
/legacy/builder                     ← 기존 StrategyBuilderPage (migration 기간)
/operations/*                       ← feature flag 뒤, placeholder page (ADR P0-01 D8·14절)
```

- 기존 진입 `/?step=...`, `/?run`은 `/legacy/builder`로 query를 유지하며 redirect한다.
- `/operations/*`는 `VITE_ENABLE_OPERATIONS` 가 truthy일 때만 route가 등록된다.

### D3. Dirty navigation

- `features/edit-strategy` document state의 `dirty`를 `useBlocker({ shouldBlockFn })`로 route leave에
  연결한다. blocker UI는 `shared/ui` dialog primitive를 쓴다.
- browser reload/close는 `beforeunload`를 함께 건다.

### D4. Loader와 Query cache

- saved revision route의 `loader`는 `queryClient.ensureQueryData(strategyRevisionQuery(id, rev))`만
  호출한다. 응답은 query cache가 소유하고 loader 반환값을 store에 복제하지 않는다.
- not-found(404)는 route `notFoundComponent`, 그 외 오류는 `errorComponent`로 구분한다.

## 4. 대안

- **react-router 8**: 충분히 가능하지만 typed search params를 수동으로 유지해야 하고, 이미 TanStack
  Query를 쓰는 상황에서 생태계가 둘로 나뉜다. 기각.
- **router 없이 `window.location` 유지**: direct entry·back/forward·blocker를 직접 구현해야 한다. 기각.

## 5. Rollback

route tree와 hook re-export가 `app/router/`와 `shared/lib/router`에 국한되므로, 문제 시 두 위치만
react-router로 바꾼다. `pages` 컴포넌트는 router에 직접 의존하지 않는다.
