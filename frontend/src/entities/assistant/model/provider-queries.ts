import {
  queryOptions,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  assistantProviderApi,
  type CreateProviderProfileRequestWritable,
} from "../../../shared/api";

export const assistantProvidersKey = () => ["assistant", "providers"] as const;

const refreshProviders = (queryClient: QueryClient): Promise<void> =>
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

/** 키는 변이 인자로만 흐르고 mutation 결과·캐시 어디에도 남지 않는다(응답에 그 필드가 없다). */
export const useCreateAssistantProvider = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateProviderProfileRequestWritable) =>
      assistantProviderApi.createProvider(input),
    onSuccess: () => refreshProviders(queryClient),
  });
};

export const useActivateAssistantProvider = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      assistantProviderApi.activateProvider(profileId),
    onSuccess: () => refreshProviders(queryClient),
  });
};

export const useDeleteAssistantProvider = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      assistantProviderApi.deleteProvider(profileId),
    onSuccess: () => refreshProviders(queryClient),
  });
};

/** 연결 테스트는 서버 상태를 바꾸지 않으므로 목록을 다시 읽지 않는다. */
export const useTestAssistantProvider = () =>
  useMutation({
    mutationFn: (profileId: string) =>
      assistantProviderApi.testProvider(profileId),
  });
