import { queryOptions } from "@tanstack/react-query";

import { strategyWorkbenchApi } from "../../../shared/api";

export const marketKey = () => ["market"] as const;

/** 홈 시장 블록 한 벌. 값과 문장은 backend 가 정하고 화면은 그대로 보인다. */
export const marketOverviewQuery = () =>
  queryOptions({
    queryKey: [...marketKey(), "overview"],
    queryFn: () => strategyWorkbenchApi.getMarketOverview(),
  });

/** 세부 업종 하나의 종목. 홈 섹터맵 카드 안 드릴다운이 읽는다. */
export const marketSectorQuery = (sectorCode: string) =>
  queryOptions({
    queryKey: [...marketKey(), "sector", sectorCode],
    queryFn: () => strategyWorkbenchApi.getMarketSector(sectorCode),
  });
