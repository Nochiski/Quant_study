import { useQuery } from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type EquityCatalogQuery,
} from "../../../shared/api";

export const datasetKeys = {
  all: ["dataset"] as const,
  catalogs: () => [...datasetKeys.all, "catalog"] as const,
  catalog: (query: EquityCatalogQuery) =>
    [...datasetKeys.catalogs(), query] as const,
};

export const useDatasetCatalog = (query: EquityCatalogQuery) =>
  useQuery({
    queryKey: datasetKeys.catalog(query),
    queryFn: () => strategyWorkbenchApi.getEquityCatalog(query),
    staleTime: 60_000,
  });
