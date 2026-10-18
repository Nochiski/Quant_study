import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiRequestError, strategyWorkbenchApi } from "../../../shared/api";

/** 같은 계산의 computing 응답만 제한해서 읽는다. 오류/부재를 작업 대기열로 바꾸지 않는다. */
export const useFactorRegression = (runId: string, fingerprint: string) => {
  const client = useQueryClient();
  const [owner, setOwner] = useState({ runId, started: false, baseline: 0 });
  const active = owner.runId === runId && owner.started;
  const key = ["backtest", runId, "factor-regression", fingerprint] as const;
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      const report =
        await strategyWorkbenchApi.getBacktestFactorRegression(runId);
      if (report.run_id !== runId || report.run_fingerprint !== fingerprint)
        throw new Error("factor regression identity differs from its run");
      return report;
    },
    enabled: (state) =>
      active &&
      !(
        state.state.error instanceof ApiRequestError &&
        state.state.error.status === 410
      ),
    staleTime: Infinity,
    retry: false,
    retryOnMount: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    refetchInterval: (state) =>
      active &&
      state.state.error === null &&
      state.state.data?.report.unavailable_reason === "computing" &&
      state.state.dataUpdateCount - owner.baseline < 36
        ? 5_000
        : false,
  });
  const gone =
    query.error instanceof ApiRequestError && query.error.status === 410;
  return {
    ...query,
    started: active,
    pollingStopped:
      active &&
      query.error === null &&
      !query.isFetching &&
      query.data?.report.unavailable_reason === "computing" &&
      (client.getQueryState(key)?.dataUpdateCount ?? 0) - owner.baseline >= 36,
    gone,
    start: () => {
      if (gone || query.isFetching) return;
      setOwner({
        runId,
        started: true,
        baseline: client.getQueryState(key)?.dataUpdateCount ?? 0,
      });
      if (active || query.data !== undefined) void query.refetch();
    },
  };
};
