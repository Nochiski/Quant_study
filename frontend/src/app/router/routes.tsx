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

import { isRunKind, type RunKind } from "../../entities/backtest";
import { strategyDocumentQuery } from "../../entities/strategy";
import {
  migrateStrategyView,
  isNewDraftId,
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
import { t } from "../../shared/config";
import { isJsonPointer } from "../../shared/lib/yaml12";
import { AppShell } from "../../widgets/app-shell";
import { ExperimentCompletionNotice } from "../../widgets/experiment-notice";

/** Everything routes can read without importing the app: query cache and feature flags. */
export type RouterContext = {
  queryClient: QueryClient;
  operationsEnabled: boolean;
};

type StrategyDocumentSearch = {
  view?: StrategyView;
  compare?: boolean;
  recipe?: boolean;
  path?: string;
  asOf?: string;
  security?: string;
  draft?: string;
};
type BacktestHistorySearch = {
  offset?: number;
  strategy?: string;
  kind?: RunKind;
};

/** Selection/projection state for every StrategySpec authoring route. */
const strategyDocumentSearch = (
  search: Record<string, unknown>,
): StrategyDocumentSearch => {
  const path = typeof search.path === "string" ? search.path : undefined;
  return {
    view: migrateStrategyView(search.view),
    recipe: search.recipe === true || search.recipe === "true" ? true : undefined,
    compare:
      search.compare === true || search.compare === "true" || search.view === "diff"
        ? true
        : undefined,
    path:
      path !== undefined && path !== "" && isJsonPointer(path)
        ? path
        : undefined,
    asOf: typeof search.asOf === "string" ? search.asOf : undefined,
    security: typeof search.security === "string" ? search.security : undefined,
    draft: isNewDraftId(search.draft) ? search.draft : undefined,
  };
};

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
const StrategiesPage = lazyRouteComponent(
  () => import("../../pages/research-strategies"),
  "StrategiesPage",
);
const BacktestsPage = lazyRouteComponent(
  () => import("../../pages/research-backtests"),
  "BacktestsPage",
);
const ExperimentsPage = lazyRouteComponent(
  () => import("../../pages/research-experiments"),
  "ExperimentsPage",
);
const ExperimentPage = lazyRouteComponent(
  () => import("../../pages/research-experiment"),
  "ExperimentPage",
);
const NewExperimentPage = lazyRouteComponent(
  () => import("../../pages/research-experiment-new"),
  "NewExperimentPage",
);
const SettingsPage = lazyRouteComponent(
  () => import("../../pages/settings"),
  "SettingsPage",
);

const offsetOf = (value: unknown): number | undefined => {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) && value > 0 ? value : undefined;
  }
  if (typeof value !== "string" || !/^[1-9]\d*$/u.test(value)) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : undefined;
};

const strategyHistorySearch = (
  search: Record<string, unknown>,
): { offset?: number } => ({
  offset: offsetOf(search.offset),
});

const backtestHistorySearch = (
  search: Record<string, unknown>,
): BacktestHistorySearch => ({
  offset: offsetOf(search.offset),
  strategy:
    typeof search.strategy === "string" && search.strategy.trim() !== ""
      ? search.strategy.trim()
      : undefined,
  kind: isRunKind(search.kind) ? search.kind : undefined,
});

const textOf = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;

/** 새 실험의 기반: 백테스트 실행(`run`) 또는 같은 설정으로 다시 만들 실험(`from`). */
const newExperimentSearch = (
  search: Record<string, unknown>,
): { run?: string; from?: string } => ({
  run: textOf(search.run),
  from: textOf(search.from),
});

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: () => {
    const { operationsEnabled } = rootRoute.useRouteContext();
    return (
      <AppShell operationsEnabled={operationsEnabled}>
        <Outlet />
        <ExperimentCompletionNotice />
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
  beforeLoad: () => {
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
  beforeLoad: () => {
    throw redirect({
      to: "/research/strategies/new",
      search: {},
      replace: true,
    });
  },
});

const newStrategyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/strategies/new",
  validateSearch: strategyDocumentSearch,
  component: NewStrategyPage,
});

const strategiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/strategies",
  validateSearch: strategyHistorySearch,
  component: StrategiesPage,
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

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

const backtestsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/backtests",
  validateSearch: backtestHistorySearch,
  component: BacktestsPage,
});

const backtestRunRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/backtests/$runId",
  component: BacktestRunPage,
});

const experimentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/experiments",
  component: ExperimentsPage,
});

const newExperimentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/experiments/new",
  validateSearch: newExperimentSearch,
  component: NewExperimentPage,
});

const experimentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/experiments/$experimentId",
  component: ExperimentPage,
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
  strategiesRoute,
  newStrategyRoute,
  strategyRevisionRoute,
  backtestsRoute,
  backtestRunRoute,
  experimentsRoute,
  newExperimentRoute,
  experimentRoute,
  settingsRoute,
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
