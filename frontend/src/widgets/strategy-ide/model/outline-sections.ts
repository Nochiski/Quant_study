/**
 * TEMPORARY (P2-03 placeholder): the root sections of the authoring document in canonical order.
 * The owner of this fact is `StrategySpec` (backend); P3-03 replaces this list with the root
 * property order of the P1-05 runtime schema. Do not add consumers beyond the IDE outline.
 */
export const OUTLINE_SECTIONS = [
  "identity",
  "data",
  "eligibility",
  "factors",
  "signal",
  "portfolio",
  "risk",
  "execution",
  "parameters",
] as const;

export type OutlineSection = (typeof OUTLINE_SECTIONS)[number];
