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

/** The runtime schema changes only with a deploy; the server also answers 304 on its ETag. */
export const strategySchemaQuery = () =>
  queryOptions({
    queryKey: ["strategy", "document-schema"],
    queryFn: () => strategyWorkbenchApi.getStrategyDocumentSchema(),
    staleTime: 5 * 60_000,
  });

/** Field contract plus the catalog versions it was built against. */
export const strategyContractQuery = () =>
  queryOptions({
    queryKey: ["strategy", "document-contract"],
    queryFn: () => strategyWorkbenchApi.getStrategyDocumentContract(),
    staleTime: 5 * 60_000,
  });

/** The history grows with every save: refetched when used, invalidated by a successful save. */
export const strategyRevisionsQuery = (strategyId: string) =>
  queryOptions({
    queryKey: ["strategy", strategyId, "revisions"],
    queryFn: () => strategyWorkbenchApi.listStrategyRevisions(strategyId),
  });
