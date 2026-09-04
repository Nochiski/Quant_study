// P0-04 deep-link spike: React 렌더링 없이 router core만으로 route tree, typed search 정규화, loader,
// notFound, lazy component, redirect/blocker API를 확인한다. ADR: ../../specs/2026-09-04-frontend-router-adr.md
//
// 관찰된 제약: search 정규화(validateSearch가 URL과 다른 값을 돌려줌)와 beforeLoad redirect는 RouterProvider의
// Transitioner가 후속 navigate를 수행한다. 렌더러 없는 이 스파이크에서는 `router.navigate()`로 같은 경로를 탄다.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  isRedirect,
  lazyRouteComponent,
  notFound,
  redirect,
  useBlocker,
} from "@tanstack/react-router";

const views = ["yaml", "json", "form", "graph", "diff"];

const buildRouter = (initial) => {
  const root = createRootRoute({ notFoundComponent: () => null });
  const index = createRoute({
    getParentRoute: () => root,
    path: "/",
    beforeLoad: () => {
      throw redirect({ to: "/research/strategies/new" });
    },
  });
  const legacy = createRoute({
    getParentRoute: () => root,
    path: "/legacy/builder",
    validateSearch: (search) => ({
      step: typeof search.step === "string" ? search.step : undefined,
      run: search.run !== undefined,
    }),
  });
  const newStrategy = createRoute({
    getParentRoute: () => root,
    path: "/research/strategies/new",
    component: lazyRouteComponent(() => Promise.resolve({ default: () => null })),
  });
  const revision = createRoute({
    getParentRoute: () => root,
    path: "/research/strategies/$strategyId/revisions/$revision",
    validateSearch: (search) => ({
      view: views.includes(search.view) ? search.view : "yaml",
      path: typeof search.path === "string" ? search.path : undefined,
      asOf: typeof search.asOf === "string" ? search.asOf : undefined,
    }),
    loader: ({ params }) => {
      if (params.revision === "999") throw notFound();
      return { strategyId: params.strategyId, revision: Number(params.revision) };
    },
  });
  const backtest = createRoute({
    getParentRoute: () => root,
    path: "/research/backtests/$runId",
  });
  const routeTree = root.addChildren([index, legacy, newStrategy, revision, backtest]);
  return createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initial] }),
    defaultPreload: false,
  });
};

const leaf = (router) => router.state.matches.at(-1);

test("revision deep link restores typed search params, params and loader data", async () => {
  const router = buildRouter(
    "/research/strategies/abc/revisions/3?view=graph&path=%2Frisk%2Fmax_name_weight&asOf=2026-08-31",
  );
  await router.load();
  const match = leaf(router);
  assert.equal(match.routeId, "/research/strategies/$strategyId/revisions/$revision");
  assert.deepEqual({ ...match.params }, { strategyId: "abc", revision: "3" });
  assert.deepEqual(match.search, { view: "graph", path: "/risk/max_name_weight", asOf: "2026-08-31" });
  assert.deepEqual(match.loaderData, { strategyId: "abc", revision: 3 });
});

test("invalid view is normalised to yaml and written back to the URL", async () => {
  const router = buildRouter("/research/strategies/abc/revisions/1?view=yaml");
  await router.load();
  await router.navigate({
    to: "/research/strategies/$strategyId/revisions/$revision",
    params: { strategyId: "abc", revision: "2" },
    search: { view: "bogus" },
  });
  assert.equal(leaf(router).search.view, "yaml");
  assert.equal(router.state.location.searchStr, "?view=yaml");
});

test("loader notFound surfaces as a notFound error on the root match", async () => {
  const router = buildRouter("/research/strategies/abc/revisions/1?view=yaml");
  await router.load();
  await router.navigate({
    to: "/research/strategies/$strategyId/revisions/$revision",
    params: { strategyId: "abc", revision: "999" },
    search: { view: "yaml" },
  });
  const root = router.state.matches[0];
  assert.equal(root.routeId, "__root__");
  assert.ok(root.error && root.error.isNotFound, "root match carries the notFound error");
});

test("unknown path matches only the root route (global not-found)", async () => {
  const router = buildRouter("/research/strategies/abc/revisions/1?view=yaml");
  await router.load();
  await router.navigate({ to: "/operations/orders" });
  assert.equal(router.state.location.pathname, "/operations/orders");
  assert.deepEqual(router.state.matches.map((m) => m.routeId), ["__root__"]);
});

test("legacy entry keeps its query and back/forward works on memory history", async () => {
  // `run`만 있는 URL은 validateSearch가 `run=true`로 정규화하므로(스파이크 제약) 정규화된 형태로 진입한다.
  const router = buildRouter("/legacy/builder?step=portfolio&run=true");
  await router.load();
  assert.deepEqual(leaf(router).search, { step: "portfolio", run: true });
  await router.navigate({ to: "/research/backtests/$runId", params: { runId: "r1" } });
  assert.equal(router.state.location.pathname, "/research/backtests/r1");
  router.history.back();
  await router.load();
  assert.equal(router.state.location.pathname, "/legacy/builder");
  router.history.forward();
  await router.load();
  assert.equal(router.state.location.pathname, "/research/backtests/r1");
});

test("redirect() produces a router redirect the provider follows; blocker hook exists", () => {
  let thrown;
  try {
    throw redirect({ to: "/research/strategies/new" });
  } catch (error) {
    thrown = error;
  }
  assert.ok(isRedirect(thrown));
  assert.equal(thrown.options.to, "/research/strategies/new");
  assert.equal(typeof useBlocker, "function");
});

test("lazyRouteComponent defers the import until preload/render", async () => {
  let imported = 0;
  const lazy = lazyRouteComponent(() => {
    imported += 1;
    return Promise.resolve({ default: () => null });
  });
  assert.equal(imported, 0);
  await lazy.preload();
  assert.equal(imported, 1);
});
