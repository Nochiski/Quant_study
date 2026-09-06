import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type BacktestRunSpec,
} from "../../../shared/api";

const terminal = new Set(["completed", "cancelled", "failed"]);

export const backtestHistoryKey = () => ["backtests", "history"] as const;

const retireBacktestHistoryQueries = async (
  queryClient: QueryClient,
): Promise<void> => {
  await queryClient.cancelQueries({ queryKey: backtestHistoryKey() });
  queryClient.removeQueries({ queryKey: backtestHistoryKey() });
};

export const backtestHistoryQuery = (
  page: { offset?: number; limit?: number; strategyId?: string } = {},
) =>
  queryOptions({
    queryKey: [
      ...backtestHistoryKey(),
      page.strategyId ?? null,
      page.offset ?? 0,
      page.limit ?? 50,
    ],
    queryFn: () => strategyWorkbenchApi.listBacktests(page),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => !terminal.has(item.run.status))
        ? 1_000
        : false,
  });

export const useStartBacktest = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (spec: BacktestRunSpec) =>
      strategyWorkbenchApi.startBacktest(spec),
    onSuccess: () => retireBacktestHistoryQueries(queryClient),
  });
};

export const useBacktestStatus = (runId: string | null) =>
  useQuery({
    queryKey: ["backtest", runId, "status"],
    queryFn: () => strategyWorkbenchApi.getBacktestStatus(runId ?? ""),
    enabled: runId !== null,
    refetchInterval: (query) =>
      query.state.data !== undefined && terminal.has(query.state.data.status)
        ? false
        : 250,
  });

export const useBacktestRequest = (runId: string | null) =>
  useQuery({
    queryKey: ["backtest", runId, "request"],
    queryFn: () => strategyWorkbenchApi.getBacktestRequest(runId ?? ""),
    enabled: runId !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });

export const useBacktestResult = (runId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ["backtest", runId, "result"],
    queryFn: () => strategyWorkbenchApi.getBacktestResult(runId ?? ""),
    enabled: runId !== null && enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });

export const useCancelBacktest = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => strategyWorkbenchApi.cancelBacktest(runId),
    onSuccess: async (state, runId) => {
      queryClient.setQueryData(["backtest", runId, "status"], state);
      await retireBacktestHistoryQueries(queryClient);
    },
  });
};
