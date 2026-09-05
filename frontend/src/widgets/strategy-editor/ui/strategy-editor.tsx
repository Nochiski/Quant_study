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
import { BacktestRunner } from "../../../features/run-backtest";
import { t } from "../../../shared/config";

const pipeline = [
  "strategy.pipeline.data",
  "strategy.pipeline.factor",
  "strategy.pipeline.portfolio",
  "strategy.pipeline.risk",
  "strategy.pipeline.execution",
  "strategy.pipeline.backtest",
] as const;

type PipelineStep =
  "data" | "factor" | "portfolio" | "risk" | "execution" | "backtest";

const initialStep = (): PipelineStep => {
  const query = new URLSearchParams(window.location.search);
  if (query.has("run")) return "backtest";
  const requested = query.get("step");
  return requested === "data" ||
    requested === "portfolio" ||
    requested === "risk" ||
    requested === "execution" ||
    requested === "backtest"
    ? requested
    : "factor";
};

const StrategyEditorWorkbench = () => {
  const [activeStep, setActiveStep] = useState<PipelineStep>(initialStep);
  const { draft, update } = useStrategyDraft();

  return (
    <div className="workbench">
      <nav className="pipeline" aria-label="Strategy pipeline">
        {pipeline.map((key, index) => {
          const step = (
            [
              "data",
              "factor",
              "portfolio",
              "risk",
              "execution",
              "backtest",
            ] as const
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
      ) : activeStep === "execution" ? (
        <ExecutionEditor />
      ) : (
        <BacktestRunner strategy={draft} />
      )}
    </div>
  );
};

export const StrategyEditor = () => (
  <StrategyDraftProvider>
    <StrategyEditorWorkbench />
  </StrategyDraftProvider>
);
