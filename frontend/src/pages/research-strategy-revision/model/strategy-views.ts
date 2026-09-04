/** Representations of one StrategySpec (authoring ADR D6); selection lives in the URL. */
export const STRATEGY_VIEWS = [
  "yaml",
  "json",
  "form",
  "graph",
  "diff",
] as const;
export type StrategyView = (typeof STRATEGY_VIEWS)[number];

/**
 * Views the revision page can render today: the stored source and its JSON projection.
 * Form, graph and diff arrive with P4.
 */
export const PROJECTION_VIEWS = [
  "yaml",
  "json",
] as const satisfies readonly StrategyView[];
