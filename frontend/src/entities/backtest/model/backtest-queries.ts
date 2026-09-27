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

/**
 * 실행 설정 스키마(P2-01, spec D6). 배포로만 바뀌고 서버가 `schema_hash` 를 ETag 로 답하므로 전략 문서
 * 스키마와 같은 staleTime 을 쓴다. 실행 설정 패널이 필드·기본값·범위를 읽는 유일한 출처다.
 */
export const runEnvironmentSchemaQuery = () =>
  queryOptions({
    queryKey: ["backtest", "run-environment-schema"],
    queryFn: () => strategyWorkbenchApi.getRunEnvironmentSchema(),
    staleTime: 5 * 60_000,
  });

export const useRunEnvironmentSchema = () =>
  useQuery(runEnvironmentSchemaQuery());

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
