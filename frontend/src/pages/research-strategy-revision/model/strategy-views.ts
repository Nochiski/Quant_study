/** Representations of one StrategySpec (authoring ADR D6); selection lives in the URL. */
export const STRATEGY_VIEWS = [
  "yaml",
  "json",
  "form",
  "graph",
  "diff",
] as const;
export type StrategyView = (typeof STRATEGY_VIEWS)[number];

/** Views the page can actually render today; the rest arrive with P3/P4. */
export const IMPLEMENTED_VIEWS = [
  "json",
] as const satisfies readonly StrategyView[];
