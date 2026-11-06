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
import { stockChartSearch } from "../../entities/chart";
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
import { LedgerRefreshNotice } from "../../features/refresh-ledger";
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
  run?: string;
  /** 새 전략 단계별 안내를 보인다(RD-04-D). 끄면 같은 초안을 편집기로 본다. */
  guide?: boolean;
  /** 안내의 지금 단계 id(`guideSteps`). 모르는 값이면 화면이 첫 단계로 본다. */
  step?: string;
  /** 안내 오른쪽 칸의 탭(AI 도우미·문서 미리보기). 없으면 AI 공급자 연결 여부로 화면이 고른다. */
  assist?: "assistant" | "preview";
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
    run: typeof search.run === "string" && search.run.trim() !== "" ? search.run.trim() : undefined,
    guide: search.guide === true || search.guide === "true" ? true : undefined,
    step: typeof search.step === "string" && search.step !== "" ? search.step : undefined,
    assist:
      search.assist === "assistant" || search.assist === "preview" ? search.assist : undefined,
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
const ResearchHomePage = lazyRouteComponent(
  () => import("../../pages/research-home"),
  "ResearchHomePage",
);
const StrategiesPage = lazyRouteComponent(
  () => import("../../pages/research-strategies"),
  "StrategiesPage",
);
const StrategySessionPage = lazyRouteComponent(
  () => import("../../pages/research-strategies"),
  "StrategySessionPage",
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
const FactorLibraryPage = lazyRouteComponent(
  () => import("../../pages/research-factors"),
  "FactorLibraryPage",
);
const StockChartPage = lazyRouteComponent(
  () => import("../../pages/research-stock-chart"),
  "StockChartPage",
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

// 옛 빌더 북마크는 값이 무엇이든(`?run=123` 은 숫자, `?step=` 는 빈 값으로 읽힌다) 있기만 하면 보낸다.
const presentOf = (value: unknown): string | undefined =>
  value === undefined || value === null ? undefined : String(value);

/** 홈: 섹터맵 드릴다운 자리(`sector`)와 옛 빌더 북마크(`step`·`run`)만 읽는다. */
const homeSearch = (
  search: Record<string, unknown>,
): { sector?: string; step?: string; run?: string } => ({
  sector: textOf(search.sector),
  step: presentOf(search.step),
  run: presentOf(search.run),
});

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: () => {
    const { operationsEnabled } = rootRoute.useRouteContext();
    return (
      <AppShell operationsEnabled={operationsEnabled}>
        <Outlet />
        <ExperimentCompletionNotice />
        <LedgerRefreshNotice />
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
  // 홈(재설계 P6). 옛 빌더 북마크(`?step=…&run=…`)만 예전처럼 새 전략 편집기로 보낸다.
  validateSearch: homeSearch,
  beforeLoad: ({ search }) => {
    if (search.step === undefined && search.run === undefined) return;
    throw redirect({
      to: "/research/strategies/new",
      search: {},
      replace: true,
    });
  },
  component: ResearchHomePage,
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

const strategySessionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/strategies/$strategyId",
  component: StrategySessionPage,
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

// 개별종목차트: 종목·주기·표시 기간을 주소가 갖는다(`stockChartSearch`가 모르는 값은 버린다).
const stockChartRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/chart",
  validateSearch: stockChartSearch,
  component: StockChartPage,
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

const factorLibraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/research/factors",
  component: FactorLibraryPage,
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
  strategySessionRoute,
  strategyRevisionRoute,
  backtestsRoute,
  backtestRunRoute,
  experimentsRoute,
  newExperimentRoute,
  experimentRoute,
  factorLibraryRoute,
  stockChartRoute,
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
