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
  universes: () => [...datasetKeys.all, "universes"] as const,
};

export const useDatasetCatalog = (query: EquityCatalogQuery) =>
  useQuery({
    queryKey: datasetKeys.catalog(query),
    queryFn: () => strategyWorkbenchApi.getEquityCatalog(query),
    staleTime: 60_000,
  });

/**
 * 연결된 시장 데이터가 실행에 쓸 수 있는 유니버스(id·표시명·설명). 선택지의 owner 는 backend 어댑터이고
 * 화면은 그대로 드롭다운으로 보인다. 어댑터 연결이 바뀌는 건 서버 재시작뿐이라 오래 신선하다고 본다.
 * 실패하면 화면은 id 직접 입력으로 돌아가므로 재시도로 오래 붙잡지 않는다.
 */
export const useEquityUniverses = () =>
  useQuery({
    queryKey: datasetKeys.universes(),
    queryFn: () => strategyWorkbenchApi.listEquityUniverses(),
    staleTime: 5 * 60_000,
    retry: false,
  });
