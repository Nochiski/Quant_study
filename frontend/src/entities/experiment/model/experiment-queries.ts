import {
  infiniteQueryOptions,
  keepPreviousData,
  queryOptions,
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type ExperimentRequest,
} from "../../../shared/api";

export const experimentsKey = () => ["experiments"] as const;

/**
 * 실험 목록(최근에 만든 순, 응답의 `next_after` 로 다음 쪽을 더 읽는다)과 대기열 표면(슬롯 사용량·우선순위
 * 상한). 상태·진행 수·끝났는지(`finished`)는 backend 가 파생해 싣는다(spec D6). 읽은 쪽 어디에든 끝나지 않은
 * 실험이 있으면 2초마다 읽은 쪽을 모두 다시 읽는다 — 뒤쪽의 일시정지한 실험도 재개·취소할 수 있다(#402
 * 리뷰 P2-1).
 */
export const experimentsQuery = () =>
  infiniteQueryOptions({
    queryKey: [...experimentsKey(), "list"],
    queryFn: ({ pageParam }) => strategyWorkbenchApi.listExperiments(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_after ?? undefined,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((item) => !item.finished),
      )
        ? 2_000
        : false,
  });

export const experimentQuery = (experimentId: string) =>
  queryOptions({
    queryKey: [...experimentsKey(), experimentId],
    queryFn: () => strategyWorkbenchApi.getExperiment(experimentId),
  });

/**
 * 시작 전 미리 계산(spec D2). 조합·실행 수·창·시도 수 변화는 backend 가 정한다. 거절은 같은 요청이면 늘
 * 같으므로 다시 묻지 않는다.
 */
export const useExperimentPreview = (request: ExperimentRequest | null) =>
  useQuery({
    queryKey: [...experimentsKey(), "preview", request],
    queryFn:
      request === null
        ? skipToken
        : () => strategyWorkbenchApi.previewExperiment(request),
    retry: false,
    // 칸을 바꾸는 동안 앞 계산을 보여 둔다(새 계산이 오면 바뀐다).
    placeholderData: keepPreviousData,
  });

export const useCreateExperiment = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ExperimentRequest) =>
      strategyWorkbenchApi.createExperiment(request),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: experimentsKey() }),
  });
};

/** 일시정지·재개·우선순위와 취소. 대기열 규칙은 backend 가 적용하고 화면은 목록을 다시 읽는다. */
export const useControlExperiment = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      experimentId,
      ...controls
    }: {
      experimentId: string;
      paused?: boolean;
      priority?: number;
    }) => strategyWorkbenchApi.controlExperiment(experimentId, controls),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: experimentsKey() }),
  });
};

export const useCancelExperiment = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (experimentId: string) =>
      strategyWorkbenchApi.cancelExperiment(experimentId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: experimentsKey() }),
  });
};
