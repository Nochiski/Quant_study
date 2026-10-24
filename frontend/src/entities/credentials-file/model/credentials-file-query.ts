import { queryOptions, type QueryClient } from "@tanstack/react-query";

import { settingsApi } from "../../../shared/api";

export const credentialsFileKey = () =>
  ["settings", "credentials-file"] as const;

/**
 * 경로는 서버가 켜질 때 정해지지만 `problems`는 파일을 손으로 고치거나 화면에서 저장할 때마다 바뀐다.
 * 짧게 신선하다고 보고 창으로 돌아오면 다시 묻는다. 연결 저장·삭제가 끝나면 `refreshCredentialsFile`로
 * 바로 다시 읽는다(재리뷰 4).
 */
export const credentialsFileQuery = () =>
  queryOptions({
    queryKey: credentialsFileKey(),
    queryFn: () => settingsApi.credentialsFile(),
    staleTime: 10_000,
    refetchOnWindowFocus: true,
  });

export const refreshCredentialsFile = (
  queryClient: QueryClient,
): Promise<void> =>
  queryClient.invalidateQueries({ queryKey: credentialsFileKey() });
