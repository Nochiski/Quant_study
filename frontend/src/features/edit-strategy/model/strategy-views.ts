/** Representations of one StrategySpec; the active view is route-owned URL state. */
export const STRATEGY_VIEWS = [
  "yaml",
  "json",
  "form",
  "graph",
  "diff",
] as const;

export type StrategyView = (typeof STRATEGY_VIEWS)[number];

/** Source plus the read-only projections available after P4-06. */
export const PROJECTION_VIEWS = [
  "yaml",
  "json",
  "form",
] as const satisfies readonly StrategyView[];
