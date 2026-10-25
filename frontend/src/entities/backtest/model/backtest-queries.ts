import { useMemo } from "react";
import {
  keepPreviousData,
  queryOptions,
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  ApiRequestError,
  strategyWorkbenchApi,
  type BacktestRunSpec,
  type RunKind,
} from "../../../shared/api";

import { runEnvironmentFields } from "./run-environment-fields";
import { RUN_STATUS } from "./run-status";

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
  page: {
    offset?: number;
    limit?: number;
    strategyId?: string;
    kind?: RunKind;
  } = {},
) =>
  queryOptions({
    queryKey: [
      ...backtestHistoryKey(),
      page.strategyId ?? null,
      page.kind ?? null,
      page.offset ?? 0,
      page.limit ?? 50,
    ],
    queryFn: () => strategyWorkbenchApi.listBacktests(page),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => RUN_STATUS[item.run.status].active)
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

export const useRunEnvironmentSchema = () => {
  const schema = useQuery(runEnvironmentSchemaQuery());
  const environmentFields = useMemo(
    () => schema.data === undefined ? [] : runEnvironmentFields(schema.data.schema),
    [schema.data],
  );
  return { ...schema, environmentFields };
};

const trialPreviewKey = () => ["backtest", "trial-preview"] as const;
const trialLedgerKey = () => ["backtest", "trial-ledger"] as const;

/**
 * 계열 시도 원장(검증 랩 spec D2). 시도 묶음·실행 역할(대표·재확인·대기·결과 없음)·N 은 backend 가 정하고
 * 화면은 그대로 보인다.
 */
export const trialLedgerQuery = (strategyId: string) =>
  queryOptions({
    queryKey: [...trialLedgerKey(), strategyId],
    queryFn: () => strategyWorkbenchApi.getTrialLedger(strategyId),
  });

/**
 * 다른 계열을 이 계열에 합친다. 되돌릴 수 없다. 합치면 두 계열의 원장과 캐시한 시도 판정이 모두 옛 값이다.
 */
export const useMergeTrialLineage = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (merge: { strategyId: string; sourceStrategyId: string }) =>
      strategyWorkbenchApi.mergeTrialLineage(
        merge.strategyId,
        merge.sourceStrategyId,
      ),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: trialPreviewKey() });
      return queryClient.invalidateQueries({ queryKey: trialLedgerKey() });
    },
  });
};

export const useStartBacktest = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (spec: BacktestRunSpec) =>
      strategyWorkbenchApi.startBacktest(spec),
    onSuccess: () => {
      // 접수한 실행이 원장을 바꾸므로 캐시한 시도 판정은 옛 값이다 — 돌아와 패널을 열면 다시 묻는다.
      queryClient.removeQueries({ queryKey: trialPreviewKey() });
      return retireBacktestHistoryQueries(queryClient);
    },
  });
};

/**
 * 실행 전 미리 계산(검증 랩 spec D2). 새 시도인지·시도 수가 얼마가 되는지는 backend 가 판정하고 화면은 응답을
 * 보이기만 한다. 거절(봉인 겹침 등)은 같은 요청이면 늘 같으므로 자동 재시도는 하지 않는다.
 * 실행 설정을 한 글자 고칠 때마다 요청이 바뀌므로, 새 답이 오기 전까지 직전 답을 보여 패널 높이가
 * 출렁이지 않게 한다(`isPlaceholderData` 로 옛 답임을 표시한다).
 */
export const useBacktestTrialPreview = (spec: BacktestRunSpec | null) =>
  useQuery({
    queryKey: [...trialPreviewKey(), spec],
    queryFn:
      spec === null
        ? skipToken
        : () => strategyWorkbenchApi.previewBacktestTrial(spec),
    retry: false,
    placeholderData: keepPreviousData,
  });

export const useBacktestStatus = (runId: string | null) =>
  useQuery({
    queryKey: ["backtest", runId, "status"],
    queryFn: () => strategyWorkbenchApi.getBacktestStatus(runId ?? ""),
    enabled: runId !== null,
    refetchInterval: (query) =>
      query.state.status === "error" ||
      (query.state.data !== undefined && !RUN_STATUS[query.state.data.status].active)
        ? false
        : query.state.dataUpdateCount < STATUS_POLL_FAST_READS
          ? STATUS_POLL_FAST_MS
          : STATUS_POLL_SLOW_MS,
  });

/** 실행 한 행(종류·소유 실험). 종류는 바뀌지 않는다. */
export const useBacktestSummary = (runId: string) =>
  useQuery({
    queryKey: ["backtest", runId, "summary"],
    queryFn: () => strategyWorkbenchApi.getBacktestSummary(runId),
    staleTime: Number.POSITIVE_INFINITY,
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

/** 초기자본 기준 계좌 보고서는 완료된 실행에서만 읽고 실행별 query cache에 둔다. */
export const useBacktestAccountReport = (runId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ["backtest", runId, "account-report"],
    retry: false,
    refetchOnReconnect: false,
    queryFn: () => strategyWorkbenchApi.backtestAccountReport(runId ?? ""),
    enabled: runId !== null && enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });

/**
 * 완료 결과의 보유·매매 투영. 결과 파일은 완료 뒤 바뀌지 않으므로 다시 읽지 않는다. 세션·종목 조회는 화면이
 * 고른 것 하나씩만 읽는다 — 매매한 세션 전부의 보유를 한 번에 받지 않는다.
 */
const holdingsQueryOptions = { retry: false, refetchOnReconnect: false, staleTime: Number.POSITIVE_INFINITY } as const;

export const useBacktestHoldings = (runId: string, enabled: boolean) =>
  useQuery({
    queryKey: ["backtest", runId, "holdings"],
    queryFn: () => strategyWorkbenchApi.getBacktestHoldings(runId),
    enabled,
    ...holdingsQueryOptions,
  });

export const useBacktestSessionHoldings = (runId: string, session: string | null) =>
  useQuery({
    queryKey: ["backtest", runId, "holdings", "session", session],
    queryFn:
      session === null
        ? skipToken
        : () => strategyWorkbenchApi.getBacktestSessionHoldings(runId, session),
    // 다른 날을 고르는 동안 직전 날의 표를 두어 표 높이가 출렁이지 않게 한다.
    placeholderData: keepPreviousData,
    ...holdingsQueryOptions,
  });

export const useBacktestSecurityTrades = (runId: string, securityId: string | null) =>
  useQuery({
    queryKey: ["backtest", runId, "holdings", "security", securityId],
    queryFn:
      securityId === null
        ? skipToken
        : () => strategyWorkbenchApi.getBacktestSecurityTrades(runId, securityId),
    placeholderData: keepPreviousData,
    ...holdingsQueryOptions,
  });

/** 원본 결과와 다른 조회 시점의 원장 값이다. 자동 재조회 없이 명시한 때만 새 근거를 읽는다. */
export const useBacktestValidationReport = (runId: string | null, enabled: boolean) => {
  const query = useQuery({
    queryKey: ["backtest", runId, "validation-report"],
    queryFn: () => strategyWorkbenchApi.getBacktestValidationReport(runId ?? ""),
    enabled: runId !== null && enabled,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    retryOnMount: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  return {
    ...query,
    refresh: () => {
      if (query.error instanceof ApiRequestError && query.error.status === 410) return;
      return query.refetch();
    },
  };
};

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
