# Router deep-link spike (P0-04)

[Frontend router ADR](../../specs/2026-09-04-frontend-router-adr.md)의 route tree, typed search 정규화,
loader/notFound, lazy component, redirect/blocker API를 React 렌더링 없이 `@tanstack/react-router` core로
확인한다. repo 의존성이 아니며 `frontend/`와 분리된 throwaway 프로젝트다.

```powershell
cd docs/superpowers/spikes/2026-09-04-router-deeplink
npm ci
npm test
```

관찰된 제약: `validateSearch`가 URL과 다른 값을 돌려주는 경우(정규화)와 `beforeLoad`의 `redirect()`는
`RouterProvider`가 후속 navigate를 수행한다. 렌더러 없는 스파이크에서는 `router.navigate()`로 같은 경로를
탄다. `node_modules/`는 커밋하지 않고 `package-lock.json`은 커밋한다.
