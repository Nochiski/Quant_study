import { useReducer, useState } from "react";

import type { ChartTarget, Timeframe } from "../../../entities/chart";
import { finderReducer, INITIAL_PANEL } from "./finder-panel";
import { useFinderResults } from "./use-finder-results";

/**
 * 자동 찾기 한 벌: 패널 상태(리듀서)와 열린 생성 패널의 서버 결과. 다른 종목·주기로 넘어가면 패널을 닫는다 — 구간
 * 날짜와 첫 점이 그 계열의 봉이라 다른 계열에서 뜻이 없다.
 */
export const useLevelFinder = (target: ChartTarget, timeframe: Timeframe) => {
  const [panel, dispatch] = useReducer(finderReducer, INITIAL_PANEL);
  const scopeKey = `${target.kind}|${target.code}|${timeframe}`;
  const [scope, setScope] = useState(scopeKey);
  if (scope !== scopeKey) {
    // 렌더 중 상태 맞추기: 다른 계열로 넘어가면 패널을 닫는다
    setScope(scopeKey);
    dispatch({ type: "reset" });
  }
  const results = useFinderResults(target, timeframe, panel);
  return { panel, dispatch, results };
};

export type LevelFinder = ReturnType<typeof useLevelFinder>;
