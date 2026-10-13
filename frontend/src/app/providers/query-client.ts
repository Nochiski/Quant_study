import { QueryClient } from "@tanstack/react-query";

import { ApiRequestError } from "../../shared/api";

/**
 * 실패한 조회는 한 번 다시 묻는다. 410(Gone)은 다시 물어도 나아지지 않으므로 묻지 않는다 — 결과 파일을 읽을 수
 * 없는 완료 run이 그렇다(#330).
 */
const retryQuery = (failureCount: number, error: Error): boolean =>
  failureCount < 1 &&
  !(error instanceof ApiRequestError && error.status === 410);

export const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: retryQuery, refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
