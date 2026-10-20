import {
  queryOptions,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import { brokerConnectionApi } from "../../../shared/api";

export const kiwoomMockConnectionKey = () =>
  ["broker-connections", "kiwoom-mock"] as const;

export const refreshKiwoomMockConnection = (
  queryClient: QueryClient,
): Promise<void> =>
  queryClient.invalidateQueries({ queryKey: kiwoomMockConnectionKey() });

/** 저장 여부와 계좌번호 끝 4자리. 서버 응답 한 벌만 캐시한다. */
export const kiwoomMockConnectionQuery = () =>
  queryOptions({
    queryKey: kiwoomMockConnectionKey(),
    queryFn: () => brokerConnectionApi.status(),
  });

/**
 * 키를 싣지 않는 삭제만 mutation으로 둔다. 저장은 키를 싣으므로 `features/configure-kiwoom-mock`의
 * plain async 명령이 부른다 — mutation 인자는 `MutationCache`에 남는다(AI 키 설정과 같은 규칙).
 */
export const useDeleteKiwoomMockConnection = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => brokerConnectionApi.remove(),
    onSuccess: () => refreshKiwoomMockConnection(queryClient),
  });
};
