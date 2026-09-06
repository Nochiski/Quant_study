import { useState } from "react";

import {
  BacktestRunDetail,
  useBacktestResult,
  useBacktestStatus,
  useCancelBacktest,
  useStartBacktest,
} from "../../../entities/backtest";
import type { StrategySpec } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";

export const BacktestRunner = ({ strategy }: { strategy: StrategySpec }) => {
  const [runId, setRunId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("run"),
  );
  const [core, setCore] = useState<"rust" | "python">("rust");
  const [initialCash, setInitialCash] = useState(100_000_000);
  const [benchmark, setBenchmark] = useState("005930");
  const [oosStart, setOosStart] = useState("");
  const start = useStartBacktest();
  const status = useBacktestStatus(runId);
  const cancel = useCancelBacktest();
  const state = status.data ?? start.data?.run;
  const completed = state?.status === "completed";
  const result = useBacktestResult(runId, completed);
  const active =
    state?.status === "queued" ||
    state?.status === "running" ||
    state?.status === "cancel_requested";

  const run = async () => {
    const metricWindows =
      oosStart === ""
        ? []
        : [
            {
              scope: "out_of_sample" as const,
              start: oosStart,
              end: strategy.data.end,
              label: `OOS ${oosStart}`,
            },
          ];
    const accepted = await start.mutateAsync({
      strategy,
      core,
      initial_cash: initialCash,
      benchmark_security_id: benchmark === "" ? null : benchmark,
      metric_windows: metricWindows,
    });
    setRunId(accepted.run.run_id);
  };

  return (
    <section className="backtest-workspace">
      <header className="backtest-workspace__header">
        <div>
          <span className="section-kicker">{t("backtest.runner.kicker")}</span>
          <h2>{t("backtest.runner.title")}</h2>
          <p>{t("backtest.runner.description")}</p>
        </div>
        <span className="engine-badge engine-badge--ready">
          {t("backtest.runner.registry")}
        </span>
      </header>

      <section className="run-console">
        <div className="run-controls">
          <label>
            {t("backtest.runner.core")}
            <select
              aria-label={t("backtest.runner.core")}
              disabled={active}
              onChange={(event) =>
                setCore(event.target.value as "rust" | "python")
              }
              value={core}
            >
              <option value="rust">{t("backtest.runner.core.rust")}</option>
              <option value="python">{t("backtest.runner.core.python")}</option>
            </select>
          </label>
          <label>
            {t("backtest.runner.initialCash")}
            <input
              aria-label={t("backtest.runner.initialCash")}
              disabled={active}
              min={1}
              onChange={(event) => setInitialCash(Number(event.target.value))}
              type="number"
              value={initialCash}
            />
          </label>
          <label>
            {t("backtest.runner.benchmark")}
            <input
              aria-label={t("backtest.runner.benchmark")}
              disabled={active}
              onChange={(event) => setBenchmark(event.target.value)}
              value={benchmark}
            />
          </label>
          <label>
            {t("backtest.runner.oosStart")}
            <input
              aria-label={t("backtest.runner.oosStart")}
              disabled={active}
              max={strategy.data.end}
              min={strategy.data.start}
              onChange={(event) => setOosStart(event.target.value)}
              type="date"
              value={oosStart}
            />
          </label>
        </div>
        <div className="run-actions">
          <Button
            disabled={active || start.isPending}
            onClick={() => void run()}
            tone="primary"
          >
            {completed ? t("backtest.runner.rerun") : t("backtest.runner.run")}
          </Button>
          {active && runId !== null && (
            <Button
              disabled={
                cancel.isPending || state?.status === "cancel_requested"
              }
              onClick={() => void cancel.mutateAsync(runId)}
            >
              {t("backtest.runner.cancel")}
            </Button>
          )}
          <span>
            {strategy.data.start} → {strategy.data.end}
          </span>
        </div>

        {state !== undefined && (
          <div className="run-progress" aria-live="polite">
            <div>
              <strong>{state.status.replaceAll("_", " ")}</strong>
              <span>{state.message}</span>
              <code>{state.stage}</code>
            </div>
            <progress max={1} value={state.progress} />
            <small>{Math.round(state.progress * 100)}%</small>
          </div>
        )}
        {(start.isError || status.isError || result.isError) && (
          <p className="inline-state inline-state--error">
            {t("backtest.runner.error")}
          </p>
        )}
        {state?.status === "failed" && (
          <p className="inline-state inline-state--error">{state.error}</p>
        )}
      </section>

      {result.isPending && completed && (
        <p className="state-message">{t("backtest.runner.resultLoading")}</p>
      )}
      {result.data !== undefined && <BacktestRunDetail result={result.data} />}
    </section>
  );
};
