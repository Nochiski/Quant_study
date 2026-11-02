import { useQueries } from "@tanstack/react-query";

import { strategyLatestQuery } from "./strategy-queries";

/**
 * 전략 id → 지금 이름. 아직 읽는 중이면 null 이고, 읽기에 실패한 전략은 빠진다(그때만 id 로 대신한다).
 * 읽는 중에 id 를 보였다가 이름으로 바꾸면 목록이 깜빡이며 id 가 먼저 보인다.
 */
export type StrategyTitles = ReadonlyMap<string, string | null>;

/** 이름을 읽는 동안 이름 자리에 두는 표시. */
export const STRATEGY_TITLE_PENDING = "…";

/** 화면에 보일 이름. 읽는 중이면 자리표시, 실패했으면 전략 id. */
export const strategyTitle = (titles: StrategyTitles, strategyId: string): string => {
  const title = titles.get(strategyId);
  return title === null ? STRATEGY_TITLE_PENDING : (title ?? strategyId);
};

/**
 * 화면에 보일 전략들의 지금 이름을 한 번에 읽는다. 같은 전략은 한 번만 조회하고, 이름을 바꾸면
 * 새 리비전이 생기므로 지난 실행에도 지금 이름이 보인다.
 */
export const useStrategyTitles = (
  strategyIds: readonly (string | null | undefined)[],
): StrategyTitles => {
  const ids = [
    ...new Set(strategyIds.filter((id): id is string => Boolean(id))),
  ].sort();
  return useQueries({
    queries: ids.map((id) => strategyLatestQuery(id)),
    combine: (results) =>
      new Map<string, string | null>(
        results.flatMap((result, index): [string, string | null][] =>
          result.data !== undefined
            ? [[ids[index]!, result.data.spec.title]]
            : result.isPending
              ? [[ids[index]!, null]]
              : [],
        ),
      ),
  });
};

/** 저장 리비전을 사람이 알아보는 이름으로. 읽는 중이면 자리표시, 읽지 못했으면 전략 id 로 대신한다. */
export const strategyRevisionLabel = (
  titles: StrategyTitles,
  strategyId: string,
  revision: number,
): string => `${strategyTitle(titles, strategyId)} · v${revision}`;
