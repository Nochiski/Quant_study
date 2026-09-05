import { queryOptions } from "@tanstack/react-query";

import { strategyWorkbenchApi } from "../../../shared/api";

export const strategiesKey = () => ["strategies"] as const;

export const strategiesQuery = (
  page: { offset?: number; limit?: number } = {},
) =>
  queryOptions({
    queryKey: [...strategiesKey(), page.offset ?? 0, page.limit ?? 50],
    queryFn: () => strategyWorkbenchApi.listStrategies(page),
  });

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

/** Two stored revisions never change, so their diff is immutable too. */
export const strategyDiffQuery = (
  strategyId: string,
  base: number,
  target: number,
) =>
  queryOptions({
    queryKey: ["strategy", strategyId, "diff", base, target],
    queryFn: () =>
      strategyWorkbenchApi.diffStrategyRevisions(strategyId, base, target),
    staleTime: Number.POSITIVE_INFINITY,
  });

export const strategyRevisionsKey = (strategyId: string) =>
  ["strategy", strategyId, "revisions"] as const;

/** The history grows with every save: refetched when used, invalidated by a successful save. */
export const strategyRevisionsQuery = (
  strategyId: string,
  page: { offset?: number; limit?: number } = {},
) =>
  queryOptions({
    queryKey: [
      ...strategyRevisionsKey(strategyId),
      page.offset ?? 0,
      page.limit ?? 50,
    ],
    queryFn: () => strategyWorkbenchApi.listStrategyRevisions(strategyId, page),
  });
