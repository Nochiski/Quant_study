import { useState } from "react";

import {
  ExecutionEditor,
  PortfolioEditor,
  RiskEditor,
  StrategyDraftProvider,
  StrategyEditorWorkspace,
  useStrategyDraft,
} from "../../../features/edit-strategy";
import { ResearchDataSelector } from "../../../features/select-research-data";
import { t } from "../../../shared/config";

const pipeline = [
  "strategy.pipeline.data",
  "strategy.pipeline.factor",
  "strategy.pipeline.portfolio",
  "strategy.pipeline.risk",
  "strategy.pipeline.execution",
] as const;

const StrategyEditorWorkbench = () => {
  const [activeStep, setActiveStep] = useState<
    "data" | "factor" | "portfolio" | "risk" | "execution"
  >("factor");
  const { draft, update } = useStrategyDraft();

  return (
    <div className="workbench">
      <nav className="pipeline" aria-label="Strategy pipeline">
        {pipeline.map((key, index) => {
          const step = (
            ["data", "factor", "portfolio", "risk", "execution"] as const
          )[index];
          return (
            <button
              className={step === activeStep ? "is-active" : ""}
              key={key}
              onClick={() => setActiveStep(step)}
              type="button"
            >
              {t(key)}
            </button>
          );
        })}
      </nav>
      {activeStep === "data" ? (
        <ResearchDataSelector
          onChange={(data) =>
            update((current) => ({
              ...current,
              data,
            }))
          }
          value={draft.data}
        />
      ) : activeStep === "factor" ? (
        <StrategyEditorWorkspace />
      ) : activeStep === "portfolio" ? (
        <PortfolioEditor />
      ) : activeStep === "risk" ? (
        <RiskEditor />
      ) : (
        <ExecutionEditor />
      )}
    </div>
  );
};

export const StrategyEditor = () => (
  <StrategyDraftProvider>
    <StrategyEditorWorkbench />
  </StrategyDraftProvider>
);
