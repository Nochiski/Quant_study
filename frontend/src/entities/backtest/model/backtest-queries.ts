import {
  queryOptions,
  skipToken,
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

// 실행 상태 폴링 간격(#161). 짧은 run 은 곧 끝나므로 처음 몇 번은 빠르게, 그 뒤로는 1초마다 묻는다.
// 실데이터 tape 는 수십 초라 250ms 고정이면 run 한 건에 수백 번 GET 이 나간다.
const STATUS_POLL_FAST_MS = 250;
const STATUS_POLL_FAST_READS = 8;
const STATUS_POLL_SLOW_MS = 1_000;

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

/**
 * 실행 전 미리 계산(검증 랩 spec D2). 새 시도인지·시도 수가 얼마가 되는지는 backend 가 판정하고 화면은 응답을
 * 보이기만 한다. 거절(봉인 겹침 등)은 같은 요청이면 늘 같으므로 다시 묻지 않는다.
 */
export const useBacktestTrialPreview = (spec: BacktestRunSpec | null) =>
  useQuery({
    queryKey: ["backtest", "trial-preview", spec],
    queryFn:
      spec === null
        ? skipToken
        : () => strategyWorkbenchApi.previewBacktestTrial(spec),
    retry: false,
  });

export const useBacktestStatus = (runId: string | null) =>
  useQuery({
    queryKey: ["backtest", runId, "status"],
    queryFn: () => strategyWorkbenchApi.getBacktestStatus(runId ?? ""),
    enabled: runId !== null,
    refetchInterval: (query) =>
      query.state.data !== undefined && terminal.has(query.state.data.status)
        ? false
        : query.state.dataUpdateCount < STATUS_POLL_FAST_READS
          ? STATUS_POLL_FAST_MS
          : STATUS_POLL_SLOW_MS,
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
