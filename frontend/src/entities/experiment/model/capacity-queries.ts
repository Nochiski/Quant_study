import {
  queryOptions,
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ApiRequestError,
  strategyWorkbenchApi,
  type CapacitySweepRequest,
} from "../../../shared/api";
import { experimentsKey } from "./experiment-queries";

export const capacityReportQuery = (experimentId: string) =>
  queryOptions({
    queryKey: [...experimentsKey(), experimentId, "capacity"],
    queryFn: () => strategyWorkbenchApi.getExperimentCapacity(experimentId),
    enabled: (query) =>
      !(
        query.state.error instanceof ApiRequestError &&
        query.state.error.status === 410
      ),
    retry: false,
    retryOnMount: false,
    refetchOnReconnect: false,
  });
export const useCapacityPreview = (request: CapacitySweepRequest | null) =>
  useQuery({
    queryKey: [...experimentsKey(), "capacity-preview", request],
    queryFn:
      request === null
        ? skipToken
        : () => strategyWorkbenchApi.previewCapacitySweep(request),
    enabled: (query) =>
      request !== null &&
      !(
        query.state.error instanceof ApiRequestError &&
        query.state.error.status === 410
      ),
    retry: false,
    retryOnMount: false,
    refetchOnReconnect: false,
  });
export const useCreateCapacity = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: CapacitySweepRequest) =>
      strategyWorkbenchApi.createCapacitySweep(request),
    onSuccess: () => client.invalidateQueries({ queryKey: experimentsKey() }),
  });
};
