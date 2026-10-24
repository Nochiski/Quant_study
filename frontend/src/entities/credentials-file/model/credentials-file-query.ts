import { queryOptions } from "@tanstack/react-query";

import { settingsApi } from "../../../shared/api";

export const credentialsFileKey = () =>
  ["settings", "credentials-file"] as const;

/** 저장 파일 경로는 서버가 켜질 때 정해져 바뀌지 않는다. 한 번 받아 둔다. */
export const credentialsFileQuery = () =>
  queryOptions({
    queryKey: credentialsFileKey(),
    queryFn: () => settingsApi.credentialsFile(),
    staleTime: Number.POSITIVE_INFINITY,
  });
