import { createContext, useContext } from "react";

import type {
  StrategySpec,
  StrategyValidation,
} from "../../../entities/strategy";

export type StrategyDraftContextValue = {
  draft: StrategySpec;
  dirty: boolean;
  savedRevision: number | null;
  validation: StrategyValidation | null;
  pending: boolean;
  notice: string | null;
  update: (recipe: (draft: StrategySpec) => StrategySpec) => void;
  validate: () => Promise<void>;
  save: () => Promise<void>;
};

export const StrategyDraftContext =
  createContext<StrategyDraftContextValue | null>(null);

export const useStrategyDraft = (): StrategyDraftContextValue => {
  const context = useContext(StrategyDraftContext);
  if (context === null) {
    throw new Error(
      "useStrategyDraft must be used inside StrategyDraftProvider",
    );
  }
  return context;
};
