import { useMutation } from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type PortfolioPreviewRequest,
} from "../../../shared/api";

export const usePortfolioPreview = () =>
  useMutation({
    mutationFn: (request: PortfolioPreviewRequest) =>
      strategyWorkbenchApi.previewPortfolio(request),
  });
