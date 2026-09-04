/** Document sections in canonical order; `parameters` is part of the outline (WORKFLOW P2-03). */
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
