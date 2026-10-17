import { useBacktestRequest } from "./backtest-queries";

/** 선택 실행의 원본 요청만 읽는다. 값 해소·허용 범위 검증은 기존 backend가 소유한다. */
export const useRunParameters = (
  runId: string | undefined,
  revision: { strategyId: string; revision: number; specHash: string },
) => {
  const query = useBacktestRequest(runId ?? null);
  const source = query.data?.strategy_source;
  const matches = source?.kind === "saved_revision" &&
    source.strategy_id === revision.strategyId &&
    source.revision === revision.revision &&
    source.expected_spec_hash === revision.specHash;
  const state = runId === undefined ? "default"
    : query.isPending ? "loading"
      : query.isError ? "error"
        : matches ? "selected" : "mismatch";
  return {
    state,
    runId,
    values: state === "selected" ? query.data?.parameter_values ?? {} : undefined,
    error: query.error,
    retry: () => { void query.refetch(); },
    ready: state === "default" || state === "selected",
  };
};

export type RunParameterContext = Pick<ReturnType<typeof useRunParameters>, "ready" | "runId" | "values">;
