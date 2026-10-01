/** 표현 선택은 URL이 소유한다. 문서의 원문·의미와 별개인 UI 상태다. */
export const STRATEGY_VIEWS = ["graph", "yaml"] as const;
export type StrategyView = (typeof STRATEGY_VIEWS)[number];
export const PROJECTION_VIEWS = STRATEGY_VIEWS;

/** 옛 링크는 원문을 바꾸지 않고 현재 표현으로 옮긴다. Diff는 revision 영역에서 연다. */
export const migrateStrategyView = (value: unknown): StrategyView =>
  value === "yaml" || value === "json" ? "yaml" : "graph";
