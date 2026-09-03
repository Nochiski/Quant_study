import { useState } from "react";

import {
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
  const [activeStep, setActiveStep] = useState<"data" | "factor">("factor");
  const { draft, update } = useStrategyDraft();

  return (
    <div className="workbench">
      <nav className="pipeline" aria-label="Strategy pipeline">
        {pipeline.map((key, index) => {
          const step = index === 0 ? "data" : index === 1 ? "factor" : null;
          return (
            <button
              className={step === activeStep ? "is-active" : ""}
              disabled={step === null}
              key={key}
              onClick={() => step !== null && setActiveStep(step)}
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
      ) : (
        <StrategyEditorWorkspace />
      )}
    </div>
  );
};

export const StrategyEditor = () => (
  <StrategyDraftProvider>
    <StrategyEditorWorkbench />
  </StrategyDraftProvider>
);
