import {
  queryOptions,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  AssistantRequestError,
  assistantProviderApi,
} from "../../../shared/api";

export const assistantProvidersKey = () => ["assistant", "providers"] as const;

export const refreshAssistantProviders = (
  queryClient: QueryClient,
): Promise<void> =>
  queryClient.invalidateQueries({ queryKey: assistantProvidersKey() });

/**
 * 가용 공급자 종류와 등록된 프로파일.
 *
 * 캐시가 서버 상태의 한 벌짜리 사본이다 — 활성 여부·키 꼬리는 응답만 믿고 화면 state로 승격하지 않는다.
 */
export const assistantProvidersQuery = () =>
  queryOptions({
    queryKey: assistantProvidersKey(),
    queryFn: () => assistantProviderApi.listProviders(),
  });

/**
 * 404는 이 화면이 모르는 사이에 프로파일이 사라졌다는 뜻이다 — 다른 탭·다른 기기에서 지웠을 때.
 *
 * 그대로 두면 화면이 "목록을 새로 고치세요"라고 말하면서 새로 고칠 수단을 주지 않는다(수동 새로 고침
 * 버튼이 없고 `refetchOnWindowFocus`도 꺼져 있다). 사용자가 존재하지 않는 프로파일을 계속 조작하는
 * dead end라 여기서 목록을 한 번 다시 읽는다(리뷰 P3-1).
 */
export const refreshIfProviderGone = (
  queryClient: QueryClient,
  error: unknown,
): void => {
  if (error instanceof AssistantRequestError && error.status === 404) {
    void refreshAssistantProviders(queryClient);
  }
};

/**
 * 프로파일 id만 싣는 변이는 react-query mutation으로 둔다.
 *
 * 프로파일 생성처럼 **API 키를 싣는 호출은 여기에 두지 않는다** — TanStack Query는 변이 인자를
 * `Mutation.state.variables`에 보관하고 그 mutation은 `MutationCache`에 남는다(관찰자가 살아 있는 동안
 * gc도 일어나지 않는다). 키를 싣는 호출은 `features/configure-ai-providers`의 plain async 명령이
 * `assistantProviderApi`를 직접 부르고, 키는 호출 인자로만 흐른다(B-01 리뷰 P1-1).
 */
export const useActivateAssistantProvider = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      assistantProviderApi.activateProvider(profileId),
    onSuccess: () => refreshAssistantProviders(queryClient),
    onError: (error) => refreshIfProviderGone(queryClient, error),
  });
};

export const useDeleteAssistantProvider = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      assistantProviderApi.deleteProvider(profileId),
    onSuccess: () => refreshAssistantProviders(queryClient),
    onError: (error) => refreshIfProviderGone(queryClient, error),
  });
};
