import { queryOptions, useMutation, useQuery } from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type BacktestRunSpec,
} from "../../../shared/api";

const terminal = new Set(["completed", "cancelled", "failed"]);

export const backtestHistoryQuery = (
  page: { offset?: number; limit?: number; strategyId?: string } = {},
) =>
  queryOptions({
    queryKey: [
      "backtests",
      "history",
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

export const useStartBacktest = () =>
  useMutation({
    mutationFn: (spec: BacktestRunSpec) =>
      strategyWorkbenchApi.startBacktest(spec),
  });

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

export const useBacktestResult = (runId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ["backtest", runId, "result"],
    queryFn: () => strategyWorkbenchApi.getBacktestResult(runId ?? ""),
    enabled: runId !== null && enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });

export const useCancelBacktest = () =>
  useMutation({
    mutationFn: (runId: string) => strategyWorkbenchApi.cancelBacktest(runId),
  });
