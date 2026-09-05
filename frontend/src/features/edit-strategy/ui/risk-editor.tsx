import type { StrategySpec } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { useStrategyDraft } from "../model/strategy-draft-context";

type Risk = StrategySpec["risk"];

export const RiskEditor = () => {
  const { draft, update } = useStrategyDraft();
  const updateRisk = (changes: Partial<Risk>) =>
    update((current) => ({
      ...current,
      risk: { ...current.risk, ...changes },
    }));

  return (
    <section className="strategy-stage" aria-labelledby="risk-stage-title">
      <header className="strategy-stage__header">
        <div>
          <span className="section-kicker">EXPOSURE GUARDRAILS</span>
          <h2 id="risk-stage-title">{t("risk.title")}</h2>
          <p>{t("risk.description")}</p>
        </div>
      </header>
      <div className="strategy-stage__grid strategy-stage__grid--single">
        <article className="stage-card">
          <h3>{t("risk.exposure.title")}</h3>
          <div className="control-grid">
            <label>
              <span>{t("risk.gross")}</span>
              <input
                min="0.01"
                step="0.1"
                type="number"
                value={draft.risk.gross_exposure ?? 1}
                onChange={(event) =>
                  updateRisk({ gross_exposure: Number(event.target.value) })
                }
              />
            </label>
            <label>
              <span>{t("risk.net")}</span>
              <input
                step="0.1"
                type="number"
                value={draft.risk.net_exposure ?? 1}
                onChange={(event) =>
                  updateRisk({ net_exposure: Number(event.target.value) })
                }
              />
            </label>
            <label>
              <span>{t("risk.nameCap")}</span>
              <input
                max="1"
                min="0.001"
                step="0.01"
                type="number"
                value={draft.risk.max_name_weight ?? 0.1}
                onChange={(event) =>
                  updateRisk({ max_name_weight: Number(event.target.value) })
                }
              />
            </label>
            <label>
              <span>{t("risk.sectorCap")}</span>
              <input
                max="1"
                min="0.001"
                step="0.01"
                type="number"
                value={draft.risk.max_sector_weight ?? 0.3}
                onChange={(event) =>
                  updateRisk({ max_sector_weight: Number(event.target.value) })
                }
              />
            </label>
            <label>
              <span>{t("risk.riskField")}</span>
              <input
                placeholder="risk.volatility"
                value={draft.risk.risk_field_id ?? ""}
                onChange={(event) =>
                  updateRisk({ risk_field_id: event.target.value || null })
                }
              />
            </label>
            <label className="toggle-control">
              <input
                checked={draft.risk.sector_neutral ?? false}
                onChange={(event) =>
                  updateRisk({ sector_neutral: event.target.checked })
                }
                type="checkbox"
              />
              <span>{t("risk.sectorNeutral")}</span>
            </label>
          </div>
        </article>
      </div>
    </section>
  );
};
