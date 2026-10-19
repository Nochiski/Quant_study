import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type LibraryFactorBody,
} from "../../../shared/api";

/** 내 팩터 목록·상세의 query key 뿌리. 저장·새 버전·숨기기는 이 뿌리를 무효화한다. */
export const libraryFactorsKey = () => ["factor-library"] as const;

export const libraryFactorsQuery = (includeHidden = false) =>
  queryOptions({
    queryKey: [...libraryFactorsKey(), "list", includeHidden],
    queryFn: () => strategyWorkbenchApi.listLibraryFactors(includeHidden),
  });

/** 전략 문서에 넣을 팩터 항목·파라미터 정의. 문서의 YAML 트리 그대로다. */
export type LibraryFactorTree = Record<string, unknown>;

type SaveLibraryFactor = {
  name: string;
  description?: string;
  factor: LibraryFactorTree;
  parameters: LibraryFactorTree[];
};

const useRefreshOnSuccess = () => {
  const queryClient = useQueryClient();
  return (saved: LibraryFactorBody) => {
    void queryClient.invalidateQueries({ queryKey: libraryFactorsKey() });
    return saved;
  };
};

export const useSaveLibraryFactor = () => {
  const refresh = useRefreshOnSuccess();
  return useMutation({
    mutationFn: (request: SaveLibraryFactor) =>
      strategyWorkbenchApi.saveLibraryFactor({
        name: request.name,
        description: request.description ?? "",
        factor: request.factor,
        parameters: request.parameters,
      }),
    onSuccess: refresh,
  });
};

export const useAddLibraryFactorVersion = () => {
  const refresh = useRefreshOnSuccess();
  return useMutation({
    mutationFn: (request: {
      libraryId: string;
      expectedLatest: number;
      factor: LibraryFactorTree;
      parameters: LibraryFactorTree[];
    }) =>
      strategyWorkbenchApi.addLibraryFactorVersion(request.libraryId, {
        factor: request.factor,
        parameters: request.parameters,
        expected_latest: request.expectedLatest,
      }),
    onSuccess: refresh,
  });
};

export const useSetLibraryFactorHidden = () => {
  const refresh = useRefreshOnSuccess();
  return useMutation({
    mutationFn: (request: { libraryId: string; hidden: boolean }) =>
      strategyWorkbenchApi.setLibraryFactorVisibility(
        request.libraryId,
        request.hidden,
      ),
    onSuccess: refresh,
  });
};

/**
 * 문서의 팩터가 라이브러리와 어떤 관계인지(같은 버전·예전 버전·고쳐 씀). 판정은 backend 가 compile 로 하고
 * 화면은 해시를 계산하지 않는다. 키는 compile 이 낸 `spec_hash` 라 같은 의미의 문서는 다시 묻지 않는다.
 */
export const useLibraryMatches = (
  source: string | null,
  specHash: string | null,
) =>
  useQuery({
    queryKey: [...libraryFactorsKey(), "matches", specHash],
    queryFn: ({ signal }) =>
      strategyWorkbenchApi.matchLibraryFactors(
        { format: "yaml", source: source ?? "" },
        signal,
      ),
    enabled: source !== null && specHash !== null,
    staleTime: 30_000,
  });
