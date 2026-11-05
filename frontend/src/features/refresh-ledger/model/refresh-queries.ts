import {
  queryOptions,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type LedgerRefreshBody,
  type RefreshCode,
} from "../../../shared/api";

export const ledgerRefreshKey = () => ["ledger-refresh"] as const;

/** 받거나 반영하는 동안 다시 읽는 주기. 진행 상태는 서버가 갖고 있어 탭을 닫았다 열어도 같다. */
export const REFRESH_POLL_MS = 2_000;

const BUSY: ReadonlySet<RefreshCode> = new Set(["running", "applying"]);

export const isRefreshing = (status: LedgerRefreshBody | undefined): boolean =>
  status !== undefined && BUSY.has(status.code);

export const ledgerRefreshQuery = () =>
  queryOptions({
    queryKey: ledgerRefreshKey(),
    queryFn: () => strategyWorkbenchApi.getLedgerRefresh(),
    refetchInterval: (query) =>
      isRefreshing(query.state.data) ? REFRESH_POLL_MS : false,
    // 앱을 다시 세우는 동안 잠깐 연결이 끊긴다 — 다음 주기에 다시 묻는다.
    retry: (failures) => failures < 30,
    retryDelay: REFRESH_POLL_MS,
  });

const store = (
  queryClient: QueryClient,
  status: LedgerRefreshBody,
): LedgerRefreshBody => {
  queryClient.setQueryData(ledgerRefreshKey(), status);
  return status;
};

/** 서버 판을 다시 확인한다(원격 목록만 읽어 싸다). */
export const useCheckLedgerRefresh = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => strategyWorkbenchApi.checkLedgerRefresh(),
    onSuccess: (status) => store(queryClient, status),
  });
};

export const useStartLedgerRefresh = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => strategyWorkbenchApi.startLedgerRefresh(),
    onSuccess: (status) => store(queryClient, status),
    // 거절(409)이면 서버가 지금 상태로 이유를 말한다 — 다시 읽어 그 문장을 보인다.
    onError: () =>
      queryClient.invalidateQueries({ queryKey: ledgerRefreshKey() }),
  });
};

/**
 * 앱에 새 데이터가 반영됐다 — 화면의 모든 서버 조회를 다시 읽는다(새 컨테이너가 답한다).
 * 새로고침 상태 자체는 이미 최신이라 다시 묻지 않는다.
 */
export const refreshAfterApply = (queryClient: QueryClient): Promise<void> =>
  queryClient.invalidateQueries({
    predicate: (query) => query.queryKey[0] !== ledgerRefreshKey()[0],
  });

export type RefreshStep = "download" | "catalog" | "verify" | "apply";
export const REFRESH_STEPS: readonly RefreshStep[] = [
  "download",
  "catalog",
  "verify",
  "apply",
];

/** 지금 단계. `applying` 은 동기화가 끝난 뒤의 앱 반영이다. */
export const currentStep = (status: LedgerRefreshBody): RefreshStep | null => {
  if (status.code === "applying") return "apply";
  if (status.code !== "running") return null;
  const stage = status.progress?.stage;
  if (stage === "catalog" || stage === "verify") return stage;
  if (stage === "done") return "apply";
  return "download";
};

/** 받기 단계만 끝을 안다(받은 바이트/전체). 나머지는 null — 막대를 퍼센트로 그리지 않는다. */
export const downloadFraction = (status: LedgerRefreshBody): number | null => {
  const progress = status.progress;
  if (
    currentStep(status) !== "download" ||
    progress === null ||
    progress === undefined
  )
    return null;
  const total = progress.bytes_total;
  if (total === null || total === undefined || total <= 0) return null;
  return Math.min(1, progress.bytes_done / total);
};
