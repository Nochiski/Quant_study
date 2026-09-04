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
| dirty navigation blocker | `useBlocker({ shouldBlockFn, enableBeforeUnload })` (`@tanstack/react-router` d.ts 확인) | `useBlocker` (data router 필요) |
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
| query parameter의 view/path 복원 | `validateSearch`가 잘못된 `view=bogus`를 제거(undefined)하고 router가 URL을 다시 씀. 스파이크는 in-app `navigate()` 경로(`buildLocation` 정규화)로 관찰. direct entry에서는 `RouterProvider`의 Transitioner가 mount 시 `replace: true, ignoreBlocker: true`로 같은 정규화를 수행한다 |
| loader `notFound()` | root match의 `error.isNotFound`로 노출 → route `notFoundComponent`가 렌더 |
| 알 수 없는 path | root만 match → root `notFoundComponent`(global not-found) |
| 기존 single-page entry | `/?step=portfolio&run`의 `beforeLoad`가 search를 유지한 `redirect({ to: "/legacy/builder", search })`를 throw. `/legacy/builder?step=portfolio&run=true`는 typed search 유지, memory history back/forward 동작 |
| redirect | `redirect({ to, search })`는 `isRedirect`인 값을 throw. `beforeLoad` redirect는 `load-client`의 `followRedirect`가 `replace: true, ignoreBlocker: true`로 따라가며 provider의 history 구독이 후속 load를 만든다. 렌더러 없는 스파이크는 throw된 값과 `router.navigate()`로 대체 |
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
  selection(WORKFLOW 2.6의 "URL 소유 상태")은 여기서 복원된다. 잘못된 값은 throw하지 않고 **제거**한다
  (`undefined`). router가 URL을 다시 쓰므로 공유 링크가 깨지지 않는다.
- 기본값은 URL에 쓰지 않는다. `validateSearch`는 기본값을 채우지 않고 page가 읽을 때
  `search.view ?? "yaml"`로 적용한다. 기본값을 채우면 `/research/strategies/new` 같은 모든 공유 링크가
  mount 시 `?view=yaml…`로 확장(replace)된다.
- `loaderDeps`에 view/path/asOf/security를 넣지 않는다. match id는 `route.id + path + loaderDepsHash`라
  search-only 변경이 page remount와 loader 재실행을 일으키지 않게 한다.

### D2. Route tree (P2-02)

```text
/                                   → redirect /research/strategies/new
/research
  /strategies/new                   ← template 로드, draft base 상태
  /strategies/$strategyId/revisions/$revision
  /backtests/$runId
/legacy/builder                     ← 기존 StrategyBuilderPage (migration 기간)
/operations/*                       ← feature flag 뒤, placeholder page (ADR P0-01 D8, WORKFLOW 15절)
```

- 기존 진입 `/?step=...`, `/?run`은 `/legacy/builder`로 query를 유지하며 redirect한다. WORKFLOW P2-02의
  "기존 진입 URL은 새 전략 route로 redirect"를 이렇게 구체화한 이유: P0-01 D8/D9가 Quick/Advanced를
  migration 기간 legacy route로 유지하기로 했고, 현재 `strategy-editor.tsx`는 mount 시
  `window.location.search`의 `step`/`run`을 읽으므로 query를 보존한 redirect가 기존 북마크와 호환된다.
  query 없는 `/`만 `/research/strategies/new`로 간다. P6-06에서 legacy를 제거하면 `/legacy/builder`는
  `/research/strategies/new`로 redirect한다 (WORKFLOW P2-02 bullet 갱신).
- `/operations/*`는 항상 route tree에 등록하고, `VITE_ENABLE_OPERATIONS`가 truthy가 아니면 `beforeLoad`에서
  `notFound()`를 던진다. route 등록을 flag로 조건화하면 빌드마다 route tree 타입이 달라진다.

### D3. Dirty navigation

- `features/edit-strategy` document state의 `dirty`를 `useBlocker`로 route leave에 연결한다. blocker UI는
  `shared/ui` dialog primitive를 쓴다.
- 술어: `shouldBlockFn: ({ current, next }) => dirty && current.pathname !== next.pathname`. **search-only
  변경(view/path/asOf/security 선택)은 차단하지 않는다.** `@tanstack/history`의 `tryNavigation`은
  PUSH/REPLACE 모두에서 blocker를 부르고 same-route search 변경도 `shouldBlockFn`에 넘어오므로
  `() => dirty`로 쓰면 dirty인 동안 outline·view·날짜 선택이 매번 dialog에 막힌다.
- 저장 성공 후 revision URL 전환(P2-04)은 `savedSource`/`dirty=false` 반영 전에 navigate가 호출될 수
  있으므로 `navigate({ ..., ignoreBlocker: true })`로 수행한다.
- browser reload/close는 `useBlocker`의 `enableBeforeUnload: () => dirty`로 처리한다. 별도 `beforeunload`
  listener를 걸면 dialog가 두 번 뜬다.
- search 정규화 replace와 `beforeLoad` redirect는 router가 `ignoreBlocker: true`로 수행하므로 blocker를
  오발동시키지 않는다.

### D4. Loader와 Query cache

- saved revision route의 `loader`는 `queryClient.ensureQueryData(strategyRevisionQuery(id, rev))`로
  warm-up만 하고 데이터를 반환하지 않는다. page는 `useSuspenseQuery(strategyRevisionQuery(...))`로 읽고
  `useLoaderData`를 쓰지 않는다. `ensureQueryData`는 stale이어도 재요청하지 않으므로 loader 반환값을
  읽으면 409 conflict 후 refetch(P3-07)가 화면에 반영되지 않는다. staleTime 정책은 query options가
  소유한다.
- not-found(404)는 route `notFoundComponent`, 그 외 오류는 `errorComponent`로 구분한다.

## 4. 대안

- **react-router 8**: 충분히 가능하지만 typed search params를 수동으로 유지해야 하고, 이미 TanStack
  Query를 쓰는 상황에서 생태계가 둘로 나뉜다. 기각.
- **router 없이 `window.location` 유지**: direct entry·back/forward·blocker를 직접 구현해야 한다. 기각.

## 5. Rollback

route tree와 hook 진입점이 `app/router/`와 `shared/lib/router`에 국한되어 교체 범위를 찾기 쉽다.
`useSearch({ from })`, `useBlocker({ shouldBlockFn })`, `loader`는 react-router에 같은 시그니처가 없으므로
호출처(page·feature)도 함께 바뀐다. `shared/lib/router`의 re-export는 위임 함수가 아니라 import 경계이며
도메인 무관이라 shared 배치가 적합하다.
