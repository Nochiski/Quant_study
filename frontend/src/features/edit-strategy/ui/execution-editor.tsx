import type { StrategySpec } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { useStrategyDraft } from "../model/strategy-draft-context";

type Execution = StrategySpec["execution"];

export const ExecutionEditor = () => {
  const { draft, update } = useStrategyDraft();
  const updateExecution = (changes: Partial<Execution>) =>
    update((current) => ({
      ...current,
      execution: { ...current.execution, ...changes },
    }));

  return (
    <section className="strategy-stage" aria-labelledby="execution-stage-title">
      <header className="strategy-stage__header">
        <div>
          <span className="section-kicker">EXECUTION POLICY</span>
          <h2 id="execution-stage-title">{t("execution.title")}</h2>
          <p>{t("execution.description")}</p>
        </div>
      </header>
      <div className="execution-timeline" aria-label={t("execution.timeline")}>
        <div>
          <span>T</span>
          <strong>{t("execution.signalClose")}</strong>
        </div>
        <i aria-hidden="true">→</i>
        <div>
          <span>T+1</span>
          <strong>{t("execution.nextOpen")}</strong>
        </div>
      </div>
      <div className="strategy-stage__grid strategy-stage__grid--single">
        <article className="stage-card">
          <h3>{t("execution.assumptions")}</h3>
          <div className="control-grid">
            <label>
              <span>{t("execution.timing")}</span>
              <select disabled value={draft.execution.timing ?? "next_open"}>
                <option value="next_open">Next session open</option>
              </select>
            </label>
            <label>
              <span>{t("execution.orderStyle")}</span>
              <select disabled value={draft.execution.order_style ?? "market"}>
                <option value="market">Market</option>
              </select>
            </label>
            <label>
              <span>{t("execution.participation")}</span>
              <input
                max="1"
                min="0.001"
                step="0.01"
                type="number"
                value={draft.execution.participation_rate ?? 0.1}
                onChange={(event) =>
                  updateExecution({
                    participation_rate: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              <span>{t("execution.fee")}</span>
              <input
                min="0"
                step="0.1"
                type="number"
                value={draft.execution.fee_bps ?? 15}
                onChange={(event) =>
                  updateExecution({ fee_bps: Number(event.target.value) })
                }
              />
            </label>
            <label>
              <span>{t("execution.slippage")}</span>
              <input
                min="0"
                step="0.1"
                type="number"
                value={draft.execution.slippage_bps ?? 10}
                onChange={(event) =>
                  updateExecution({ slippage_bps: Number(event.target.value) })
                }
              />
            </label>
          </div>
          <p className="execution-note">{t("execution.lookAhead")}</p>
        </article>
      </div>
    </section>
  );
};
