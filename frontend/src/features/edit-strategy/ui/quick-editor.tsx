import { t } from "../../../shared/config";
import { useStrategyDraft } from "../model/strategy-draft-context";

export const QuickEditor = () => {
  const { draft, update } = useStrategyDraft();

  return (
    <section className="editor-panel" aria-label={t("builder.mode.quick")}>
      <div className="field-grid">
        <label>
          <span>{t("strategy.title")}</span>
          <input
            value={draft.title}
            onChange={(event) =>
              update((current) => ({ ...current, title: event.target.value }))
            }
          />
        </label>
        <label>
          <span>{t("strategy.selectionCount")}</span>
          <input
            type="number"
            min="1"
            value={draft.portfolio.selection_count ?? 20}
            onChange={(event) =>
              update((current) => ({
                ...current,
                portfolio: {
                  ...current.portfolio,
                  selection_count: Number(event.target.value),
                },
              }))
            }
          />
        </label>
        <label>
          <span>{t("strategy.maxNameWeight")}</span>
          <input
            type="number"
            min="0.01"
            max="1"
            step="0.01"
            value={draft.risk.max_name_weight ?? 0.1}
            onChange={(event) =>
              update((current) => ({
                ...current,
                risk: {
                  ...current.risk,
                  max_name_weight: Number(event.target.value),
                },
              }))
            }
          />
        </label>
      </div>
      <div className="factor-list">
        {draft.factors.factors.map((factor, index) => (
          <label className="factor-card" key={factor.factor_id}>
            <span>
              <strong>{factor.label}</strong>
              <small>{factor.factor_id}</small>
            </span>
            <span>
              {t("strategy.factorWeight")}
              <input
                type="number"
                step="0.1"
                value={factor.weight}
                onChange={(event) =>
                  update((current) => ({
                    ...current,
                    factors: {
                      factors: current.factors.factors.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, weight: Number(event.target.value) }
                          : item,
                      ),
                    },
                  }))
                }
              />
            </span>
          </label>
        ))}
      </div>
    </section>
  );
};
