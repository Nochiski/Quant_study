import { useMutation, useQuery } from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type EquityCatalogQuery,
  type ResearchPanelPreviewRequest,
  type UniverseHistoryQuery,
} from "../../../shared/api";

export const datasetKeys = {
  all: ["dataset"] as const,
  catalogs: () => [...datasetKeys.all, "catalog"] as const,
  catalog: (query: EquityCatalogQuery) =>
    [...datasetKeys.catalogs(), query] as const,
  universes: () => [...datasetKeys.all, "universe"] as const,
  universe: (query: UniverseHistoryQuery) =>
    [...datasetKeys.universes(), query] as const,
  panels: () => [...datasetKeys.all, "panel"] as const,
  panel: (request: ResearchPanelPreviewRequest) =>
    [...datasetKeys.panels(), request] as const,
};

export const useDatasetCatalog = (query: EquityCatalogQuery) =>
  useQuery({
    queryKey: datasetKeys.catalog(query),
    queryFn: () => strategyWorkbenchApi.getEquityCatalog(query),
    staleTime: 60_000,
  });

export const useUniversePreview = (query: UniverseHistoryQuery) =>
  useQuery({
    queryKey: datasetKeys.universe(query),
    queryFn: () => strategyWorkbenchApi.previewUniverse(query),
    staleTime: 60_000,
  });

export const useResearchPanelPreview = () =>
  useMutation({
    mutationFn: (request: ResearchPanelPreviewRequest) =>
      strategyWorkbenchApi.previewPanel(request),
  });
