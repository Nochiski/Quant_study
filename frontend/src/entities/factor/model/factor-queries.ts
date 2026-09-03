import { useMutation, useQuery } from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type FactorCatalogQuery,
  type FactorGraphRequest,
  type FactorPreviewRequest,
} from "../../../shared/api";

export const factorKeys = {
  all: ["factor"] as const,
  catalogs: () => [...factorKeys.all, "catalog"] as const,
  catalog: (query: FactorCatalogQuery) =>
    [...factorKeys.catalogs(), query] as const,
  validations: () => [...factorKeys.all, "validation"] as const,
  validation: (request: FactorGraphRequest) =>
    [...factorKeys.validations(), request] as const,
};

export const useFactorCatalog = (query: FactorCatalogQuery) =>
  useQuery({
    queryKey: factorKeys.catalog(query),
    queryFn: () => strategyWorkbenchApi.getFactorCatalog(query),
    staleTime: 60_000,
  });

export const useFactorValidation = (request: FactorGraphRequest) =>
  useQuery({
    queryKey: factorKeys.validation(request),
    queryFn: () => strategyWorkbenchApi.validateFactorGraph(request),
    staleTime: 30_000,
  });

export const useFactorPreview = () =>
  useMutation({
    mutationFn: (request: FactorPreviewRequest) =>
      strategyWorkbenchApi.previewFactorGraph(request),
  });
