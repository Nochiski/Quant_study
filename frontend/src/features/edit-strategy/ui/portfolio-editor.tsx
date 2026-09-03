import {
  PortfolioPreview,
  usePortfolioPreview,
} from "../../../entities/portfolio";
import type { StrategySpec } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { useStrategyDraft } from "../model/strategy-draft-context";

type Portfolio = StrategySpec["portfolio"];
type Signal = StrategySpec["signal"];
type EligibilityRule = NonNullable<
  StrategySpec["eligibility"]["rules"]
>[number];

const nullableNumber = (value: string): number | null =>
  value.trim() === "" ? null : Number(value);

export const PortfolioEditor = () => {
  const { draft, update } = useStrategyDraft();
  const preview = usePortfolioPreview();

  const updatePortfolio = (changes: Partial<Portfolio>) =>
    update((current) => ({
      ...current,
      portfolio: { ...current.portfolio, ...changes },
    }));
  const updateSignal = (changes: Partial<Signal>) =>
    update((current) => ({
      ...current,
      signal: { ...current.signal, ...changes },
    }));
  const updateRule = (index: number, changes: Partial<EligibilityRule>) =>
    update((current) => ({
      ...current,
      eligibility: {
        rules: (current.eligibility.rules ?? []).map((rule, ruleIndex) =>
          ruleIndex === index ? { ...rule, ...changes } : rule,
        ),
      },
    }));

  return (
    <section className="strategy-stage" aria-labelledby="portfolio-stage-title">
      <header className="strategy-stage__header">
        <div>
          <span className="section-kicker">PORTFOLIO CONSTRUCTION</span>
          <h2 id="portfolio-stage-title">{t("portfolio.title")}</h2>
          <p>{t("portfolio.description")}</p>
        </div>
        <Button
          disabled={preview.isPending}
          onClick={() => preview.mutate({ spec: draft })}
          tone="primary"
        >
          {preview.isPending
            ? t("portfolio.preview.loading")
            : t("portfolio.preview.run")}
        </Button>
      </header>

      <div className="strategy-stage__grid">
        <article className="stage-card">
          <h3>{t("portfolio.selection.title")}</h3>
          <div className="control-grid">
            <label>
              <span>{t("portfolio.side")}</span>
              <select
                value={draft.portfolio.side ?? "long_only"}
                onChange={(event) =>
                  updatePortfolio({
                    side: event.target.value as NonNullable<Portfolio["side"]>,
                  })
                }
              >
                <option value="long_only">Long only</option>
                <option value="long_short">Long / Short</option>
              </select>
            </label>
            <label>
              <span>{t("portfolio.selection.method")}</span>
              <select
                value={draft.portfolio.selection_method ?? "top_n"}
                onChange={(event) =>
                  updatePortfolio({
                    selection_method: event.target.value as NonNullable<
                      Portfolio["selection_method"]
                    >,
                  })
                }
              >
                <option value="top_n">Top / Bottom N</option>
                <option value="percentile">Percentile</option>
              </select>
            </label>
            {(draft.portfolio.selection_method ?? "top_n") === "top_n" ? (
              <>
                <label>
                  <span>{t("portfolio.selection.longCount")}</span>
                  <input
                    min="1"
                    type="number"
                    value={draft.portfolio.selection_count ?? 20}
                    onChange={(event) =>
                      updatePortfolio({
                        selection_count: Number(event.target.value),
                      })
                    }
                  />
                </label>
                {(draft.portfolio.side ?? "long_only") === "long_short" && (
                  <label>
                    <span>{t("portfolio.selection.shortCount")}</span>
                    <input
                      min="1"
                      type="number"
                      value={draft.portfolio.short_selection_count ?? 20}
                      onChange={(event) =>
                        updatePortfolio({
                          short_selection_count: Number(event.target.value),
                        })
                      }
                    />
                  </label>
                )}
              </>
            ) : (
              <label>
                <span>{t("portfolio.selection.percentile")}</span>
                <input
                  max="0.5"
                  min="0.01"
                  step="0.01"
                  type="number"
                  value={draft.portfolio.selection_percentile ?? 0.1}
                  onChange={(event) =>
                    updatePortfolio({
                      selection_percentile: Number(event.target.value),
                    })
                  }
                />
              </label>
            )}
            <label>
              <span>{t("portfolio.weighting")}</span>
              <select
                value={draft.portfolio.weighting ?? "equal"}
                onChange={(event) =>
                  updatePortfolio({
                    weighting: event.target.value as NonNullable<
                      Portfolio["weighting"]
                    >,
                  })
                }
              >
                <option value="equal">Equal</option>
                <option value="factor_score">Factor score</option>
                <option value="rank">Rank</option>
                <option value="risk">Inverse risk</option>
              </select>
            </label>
          </div>
        </article>

        <article className="stage-card">
          <h3>{t("portfolio.signal.title")}</h3>
          <div className="control-grid">
            <label>
              <span>{t("portfolio.signal.threshold")}</span>
              <input
                placeholder={t("common.optional")}
                step="0.01"
                type="number"
                value={draft.signal.score_threshold ?? ""}
                onChange={(event) =>
                  updateSignal({
                    score_threshold: nullableNumber(event.target.value),
                  })
                }
              />
            </label>
            <label>
              <span>{t("portfolio.signal.regimeField")}</span>
              <input
                placeholder="market.regime"
                value={draft.signal.regime_field_id ?? ""}
                onChange={(event) =>
                  updateSignal({ regime_field_id: event.target.value || null })
                }
              />
            </label>
            <label>
              <span>{t("portfolio.signal.regimeMinimum")}</span>
              <input
                placeholder={t("common.optional")}
                step="0.01"
                type="number"
                value={draft.signal.regime_minimum ?? ""}
                onChange={(event) =>
                  updateSignal({
                    regime_minimum: nullableNumber(event.target.value),
                  })
                }
              />
            </label>
          </div>
        </article>

        <article className="stage-card">
          <div className="stage-card__title">
            <h3>{t("portfolio.eligibility.title")}</h3>
            <Button
              onClick={() =>
                update((current) => ({
                  ...current,
                  eligibility: {
                    rules: [
                      ...(current.eligibility.rules ?? []),
                      {
                        field_id: "price.market_cap",
                        operator: "gte",
                        value: 0,
                      },
                    ],
                  },
                }))
              }
            >
              {t("portfolio.eligibility.add")}
            </Button>
          </div>
          {(draft.eligibility.rules ?? []).length === 0 ? (
            <p className="inline-state">{t("portfolio.eligibility.empty")}</p>
          ) : (
            <div className="rule-list">
              {(draft.eligibility.rules ?? []).map((rule, index) => (
                <div className="rule-row" key={`${rule.field_id}-${index}`}>
                  <input
                    aria-label={`${t("portfolio.eligibility.field")} ${index + 1}`}
                    value={rule.field_id}
                    onChange={(event) =>
                      updateRule(index, { field_id: event.target.value })
                    }
                  />
                  <select
                    aria-label={`${t("portfolio.eligibility.operator")} ${index + 1}`}
                    value={rule.operator}
                    onChange={(event) =>
                      updateRule(index, {
                        operator: event.target
                          .value as EligibilityRule["operator"],
                      })
                    }
                  >
                    <option value="gt">&gt;</option>
                    <option value="gte">≥</option>
                    <option value="lt">&lt;</option>
                    <option value="lte">≤</option>
                    <option value="eq">=</option>
                  </select>
                  <input
                    aria-label={`${t("portfolio.eligibility.value")} ${index + 1}`}
                    step="any"
                    type="number"
                    value={rule.value}
                    onChange={(event) =>
                      updateRule(index, { value: Number(event.target.value) })
                    }
                  />
                  <button
                    className="icon-button"
                    aria-label={`${t("portfolio.eligibility.remove")} ${index + 1}`}
                    onClick={() =>
                      update((current) => ({
                        ...current,
                        eligibility: {
                          rules: (current.eligibility.rules ?? []).filter(
                            (_, ruleIndex) => ruleIndex !== index,
                          ),
                        },
                      }))
                    }
                    type="button"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="stage-card">
          <h3>{t("portfolio.rebalance.title")}</h3>
          <div className="control-grid">
            <label>
              <span>{t("portfolio.rebalance.frequency")}</span>
              <select
                value={draft.portfolio.rebalance ?? "monthly"}
                onChange={(event) =>
                  updatePortfolio({
                    rebalance: event.target.value as NonNullable<
                      Portfolio["rebalance"]
                    >,
                  })
                }
              >
                <option value="every_n_sessions">Every N sessions</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Month end</option>
                <option value="quarterly">Quarter end</option>
              </select>
            </label>
            {draft.portfolio.rebalance === "every_n_sessions" && (
              <label>
                <span>{t("portfolio.rebalance.sessions")}</span>
                <input
                  min="1"
                  type="number"
                  value={draft.portfolio.rebalance_every_n_sessions ?? 21}
                  onChange={(event) =>
                    updatePortfolio({
                      rebalance_every_n_sessions: Number(event.target.value),
                    })
                  }
                />
              </label>
            )}
            <label>
              <span>{t("portfolio.turnoverBuffer")}</span>
              <input
                min="0"
                type="number"
                value={draft.portfolio.turnover_buffer_count ?? 0}
                onChange={(event) =>
                  updatePortfolio({
                    turnover_buffer_count: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              <span>{t("portfolio.minimumTrade")}</span>
              <input
                min="0"
                step="0.001"
                type="number"
                value={draft.portfolio.minimum_trade_weight ?? 0}
                onChange={(event) =>
                  updatePortfolio({
                    minimum_trade_weight: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              <span>{t("portfolio.liquidityField")}</span>
              <input
                placeholder={t("common.optional")}
                value={draft.portfolio.liquidity_field_id ?? ""}
                onChange={(event) =>
                  updatePortfolio({
                    liquidity_field_id: event.target.value || null,
                  })
                }
              />
            </label>
            <label>
              <span>{t("portfolio.minimumLiquidity")}</span>
              <input
                placeholder={t("common.optional")}
                step="any"
                type="number"
                value={draft.portfolio.minimum_liquidity ?? ""}
                onChange={(event) =>
                  updatePortfolio({
                    minimum_liquidity: nullableNumber(event.target.value),
                  })
                }
              />
            </label>
          </div>
        </article>
      </div>

      {preview.isError && (
        <p className="inline-state inline-state--error">
          {t("portfolio.preview.error")}
        </p>
      )}
      {preview.data !== undefined && (
        <PortfolioPreview preview={preview.data} />
      )}
    </section>
  );
};
