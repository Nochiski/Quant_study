import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  notFound,
  Outlet,
  redirect,
  type RouterHistory,
} from "@tanstack/react-router";

import { strategyDocumentQuery } from "../../entities/strategy";
import {
  STRATEGY_VIEWS,
  type StrategyView,
} from "../../features/edit-strategy";
import { ApiRequestError } from "../../shared/api";
import { OperationsPlaceholderPage } from "../../pages/operations-placeholder";
import { BacktestRunPage } from "../../pages/research-backtest";
import {
  NotFoundPage,
  RouteErrorPage,
  RoutePendingPage,
} from "../../pages/route-states";
import { StrategyBuilderPage } from "../../pages/strategy-builder";
import { t } from "../../shared/config";
import { isJsonPointer } from "../../shared/lib/yaml12";
import { AppShell } from "../../widgets/app-shell";

/** Everything routes can read without importing the app: query cache and feature flags. */
export type RouterContext = {
  queryClient: QueryClient;
  operationsEnabled: boolean;
};

const isView = (value: unknown): value is StrategyView =>
  typeof value === "string" &&
  (STRATEGY_VIEWS as readonly string[]).includes(value);

type LegacySearch = { step?: string; run?: string };
type StrategyDocumentSearch = {
  view?: StrategyView;
  path?: string;
  asOf?: string;
  security?: string;
};

/** Selection/projection state for every StrategySpec authoring route. */
const strategyDocumentSearch = (
  search: Record<string, unknown>,
): StrategyDocumentSearch => {
  const path = typeof search.path === "string" ? search.path : undefined;
  return {
    view: isView(search.view) ? search.view : undefined,
    path:
      path !== undefined && path !== "" && isJsonPointer(path)
        ? path
        : undefined,
    asOf: typeof search.asOf === "string" ? search.asOf : undefined,
    security: typeof search.security === "string" ? search.security : undefined,
  };
};

/** Idempotent: invalid values are dropped, defaults are never written to the URL (ADR D1). */
const legacySearch = (search: Record<string, unknown>): LegacySearch => ({
  step: typeof search.step === "string" ? search.step : undefined,
  // `run` carries a run id (legacy `?run=<id>`); bare `?run` stays an empty string.
  run: search.run === undefined ? undefined : String(search.run),
});

// The editor pages (parser, CodeMirror, schema assist) are the heavy part of the app; they load
// on first navigation so the entry chunk stays small (editor ADR D1).
const NewStrategyPage = lazyRouteComponent(
  () => import("../../pages/research-strategy-new"),
  "NewStrategyPage",
);
const StrategyRevisionPage = lazyRouteComponent(
  () => import("../../pages/research-strategy-revision"),
  "StrategyRevisionPage",
);

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: () => {
    const { operationsEnabled } = rootRoute.useRouteContext();
    return (
      <AppShell operationsEnabled={operationsEnabled}>
        <Outlet />
      </AppShell>
    );
  },
  notFoundComponent: NotFoundPage,
  errorComponent: RouteErrorPage,
  pendingComponent: RoutePendingPage,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: legacySearch,
  beforeLoad: ({ search }) => {
    // Existing bookmarks (`/?step=`, `/?run`) keep their query on the legacy route (ADR D2).
    if (search.step !== undefined || search.run !== undefined) {
      throw redirect({ to: "/legacy/builder", search, replace: true });
    }
    throw redirect({
      to: "/research/strategies/new",
      search: {},
      replace: true,
    });
  },
});

const legacyBuilderRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/legacy/builder",
  validateSearch: legacySearch,
  component: StrategyBuilderPage,
});

const newStrategyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/strategies/new",
  validateSearch: strategyDocumentSearch,
  component: NewStrategyPage,
});

const strategyRevisionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/strategies/$strategyId/revisions/$revision",
  validateSearch: strategyDocumentSearch,
  // Warm-up only (ADR D4): the page reads through useSuspenseQuery, never useLoaderData.
  loader: async ({ context, params }) => {
    if (!/^[1-9]\d*$/.test(params.revision)) throw notFound();
    const revision = Number(params.revision);
    try {
      await context.queryClient.ensureQueryData(
        strategyDocumentQuery(params.strategyId, revision),
      );
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 404)
        throw notFound();
      throw error;
    }
  },
  component: StrategyRevisionPage,
});

const backtestRunRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/backtests/$runId",
  component: BacktestRunPage,
});

const operationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/operations",
  // Always registered so the route tree type is stable; hidden behind the flag (ADR D2).
  beforeLoad: ({ context }) => {
    if (!context.operationsEnabled) throw notFound();
  },
});

const deploymentsRoute = createRoute({
  getParentRoute: () => operationsRoute,
  path: "/deployments",
  component: () => <OperationsPlaceholderPage area={t("nav.deployments")} />,
});
const realtimeRoute = createRoute({
  getParentRoute: () => operationsRoute,
  path: "/realtime",
  component: () => <OperationsPlaceholderPage area={t("nav.realtime")} />,
});
const ordersRoute = createRoute({
  getParentRoute: () => operationsRoute,
  path: "/orders",
  component: () => <OperationsPlaceholderPage area={t("nav.orders")} />,
});
const positionsRoute = createRoute({
  getParentRoute: () => operationsRoute,
  path: "/positions",
  component: () => <OperationsPlaceholderPage area={t("nav.positions")} />,
});
const riskRoute = createRoute({
  getParentRoute: () => operationsRoute,
  path: "/risk",
  component: () => <OperationsPlaceholderPage area={t("nav.risk")} />,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  legacyBuilderRoute,
  newStrategyRoute,
  strategyRevisionRoute,
  backtestRunRoute,
  operationsRoute.addChildren([
    deploymentsRoute,
    realtimeRoute,
    ordersRoute,
    positionsRoute,
    riskRoute,
  ]),
]);

export const createAppRouter = (
  context: RouterContext,
  history?: RouterHistory,
) =>
  createRouter({
    routeTree,
    context,
    history,
    defaultPendingComponent: RoutePendingPage,
    defaultErrorComponent: RouteErrorPage,
    defaultNotFoundComponent: NotFoundPage,
    defaultPreload: "intent",
    defaultPendingMs: 200,
    scrollRestoration: true,
  });

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
