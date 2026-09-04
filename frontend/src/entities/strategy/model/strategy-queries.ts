import { queryOptions } from "@tanstack/react-query";

import { strategyWorkbenchApi } from "../../../shared/api";

/** A saved revision is immutable, so it never goes stale once loaded. */
export const strategyRevisionQuery = (strategyId: string, revision: number) =>
  queryOptions({
    queryKey: ["strategy", strategyId, "revision", revision],
    queryFn: () => strategyWorkbenchApi.getStrategy(strategyId, revision),
    staleTime: Number.POSITIVE_INFINITY,
  });
