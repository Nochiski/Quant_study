import { queryOptions } from "@tanstack/react-query";

import { strategyWorkbenchApi } from "../../../shared/api";

/** A saved revision's exact document is immutable, so it never goes stale once loaded. */
export const strategyDocumentQuery = (strategyId: string, revision: number) =>
  queryOptions({
    queryKey: ["strategy", strategyId, "document", revision],
    queryFn: () =>
      strategyWorkbenchApi.getStrategyDocument(strategyId, revision),
    staleTime: Number.POSITIVE_INFINITY,
  });

/** The history grows with every save: refetched when used, invalidated by a successful save. */
export const strategyRevisionsQuery = (strategyId: string) =>
  queryOptions({
    queryKey: ["strategy", strategyId, "revisions"],
    queryFn: () => strategyWorkbenchApi.listStrategyRevisions(strategyId),
  });
