import { useQuery } from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type FactorCatalogQuery,
} from "../../../shared/api";

export const factorKeys = {
  all: ["factor"] as const,
  catalogs: () => [...factorKeys.all, "catalog"] as const,
  catalog: (query: FactorCatalogQuery) =>
    [...factorKeys.catalogs(), query] as const,
};

export const useFactorCatalog = (query: FactorCatalogQuery) =>
  useQuery({
    queryKey: factorKeys.catalog(query),
    queryFn: () => strategyWorkbenchApi.getFactorCatalog(query),
    staleTime: 60_000,
  });
