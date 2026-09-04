import { useState } from "react";

import { useDatasetCatalog } from "../../../entities/dataset";
import {
  FactorBrowser,
  useFactorCatalog,
  useFactorPreview,
  type FactorDefinition,
  type FactorSignal,
} from "../../../entities/factor";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import {
  appendFactorTransform,
  factorTransformChain,
  quickTransforms,
  removeLastFactorTransform,
  type QuickTransform,
} from "../model/factor-transforms";
import { useStrategyDraft } from "../model/strategy-draft-context";

const transformLabels: Record<QuickTransform, string> = {
  lag: "Lag",
  rank: "Rank",
  zscore: "Z-score",
  winsorize: "Winsorize",
  neutralize: "Neutralize",
};

const metric = (value: number | null): string =>
  value === null ? "—" : value.toFixed(4);

const FactorPreviewControl = ({
  factor,
  snapshotId,
  start,
  end,
}: {
  factor: FactorSignal;
  snapshotId?: string;
  start: string;
  end: string;
}) => {
  const preview = useFactorPreview();
  return (
    <div className="factor-preview">
      <Button
        disabled={snapshotId === undefined || preview.isPending}
        onClick={() =>
          snapshotId !== undefined &&
          preview.mutate({
            graph: factor.graph,
            // provenance only: the backend adapter owns the snapshot and fails closed on mismatch
            expected_data_snapshot_id: snapshotId,
            as_of_start: start,
            as_of_end: end,
          })
        }
      >
        {preview.isPending
          ? t("factor.preview.loading")
          : t("factor.preview.run")}
      </Button>
      {preview.isError && (
        <span className="inline-state inline-state--error">
          {t("factor.preview.error")}
        </span>
      )}
      {preview.data !== undefined && (
        <dl className="factor-metrics" aria-label={t("factor.preview.metrics")}>
          <div>
            <dt>IC</dt>
            <dd>{metric(preview.data.analytics.information_coefficient)}</dd>
          </div>
          <div>
            <dt>Rank IC</dt>
            <dd>
              {metric(preview.data.analytics.rank_information_coefficient)}
            </dd>
          </div>
          <div>
            <dt>Q Spread</dt>
            <dd>{metric(preview.data.analytics.quantile_spread)}</dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>{(preview.data.analytics.coverage * 100).toFixed(1)}%</dd>
          </div>
          <div>
            <dt>Turnover</dt>
            <dd>{metric(preview.data.analytics.turnover)}</dd>
          </div>
          <div>
            <dt>Decay</dt>
            <dd>{metric(preview.data.analytics.decay)}</dd>
          </div>
        </dl>
      )}
    </div>
  );
};

export const QuickEditor = () => {
  const { draft, update } = useStrategyDraft();
  const [search, setSearch] = useState("");
  const catalog = useFactorCatalog({
    search: search.trim() || undefined,
    availability: ["implemented"],
    page_size: 12,
  });
  const dataset = useDatasetCatalog({ page_size: 1 });
  const selectedIds = new Set(
    draft.factors.factors.map((factor) => factor.factor_id),
  );

  const addFactor = (definition: FactorDefinition) => {
    if (definition.default_graph === null) return;
    const graph = definition.default_graph;
    update((current) =>
      current.factors.factors.some(
        (factor) => factor.factor_id === definition.factor_id,
      )
        ? current
        : {
            ...current,
            factors: {
              factors: [
                ...current.factors.factors,
                {
                  factor_id: definition.factor_id,
                  label: definition.label,
                  direction: definition.preference,
                  weight: 1,
                  graph,
                },
              ],
            },
          },
    );
  };

  return (
    <section
      className="editor-panel factor-editor"
      aria-label={t("builder.mode.quick")}
    >
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

      <section
        className="selected-factors"
        aria-labelledby="selected-factor-title"
      >
        <header>
          <div>
            <span className="section-kicker">COMPOSITE SIGNAL</span>
            <h2 id="selected-factor-title">{t("factor.selected.title")}</h2>
          </div>
          <span>{draft.factors.factors.length}</span>
        </header>
        <div className="factor-list">
          {draft.factors.factors.map((factor, index) => {
            const chain = factorTransformChain(factor.graph);
            return (
              <article className="factor-card" key={factor.factor_id}>
                <div className="factor-card__identity">
                  <strong>{factor.label}</strong>
                  <small>{factor.factor_id}</small>
                  <span className="transform-chain">
                    {chain.length === 0
                      ? t("factor.transform.raw")
                      : chain.map((item) => transformLabels[item]).join(" → ")}
                  </span>
                </div>
                <label>
                  <span>{t("strategy.factorWeight")}</span>
                  <input
                    aria-label={`${factor.label} ${t("strategy.factorWeight")}`}
                    type="number"
                    step="0.1"
                    value={factor.weight}
                    onChange={(event) =>
                      update((current) => ({
                        ...current,
                        factors: {
                          factors: current.factors.factors.map(
                            (item, itemIndex) =>
                              itemIndex === index
                                ? {
                                    ...item,
                                    weight: Number(event.target.value),
                                  }
                                : item,
                          ),
                        },
                      }))
                    }
                  />
                </label>
                <div
                  className="transform-toolbar"
                  aria-label={t("factor.transform.title")}
                >
                  {quickTransforms.map((transform) => (
                    <button
                      key={transform}
                      onClick={() =>
                        update((current) => ({
                          ...current,
                          factors: {
                            factors: current.factors.factors.map(
                              (item, itemIndex) =>
                                itemIndex === index
                                  ? {
                                      ...item,
                                      graph: appendFactorTransform(
                                        item.graph,
                                        transform,
                                      ),
                                    }
                                  : item,
                            ),
                          },
                        }))
                      }
                      type="button"
                    >
                      + {transformLabels[transform]}
                    </button>
                  ))}
                  <button
                    disabled={chain.length === 0}
                    onClick={() =>
                      update((current) => ({
                        ...current,
                        factors: {
                          factors: current.factors.factors.map(
                            (item, itemIndex) =>
                              itemIndex === index
                                ? {
                                    ...item,
                                    graph: removeLastFactorTransform(
                                      item.graph,
                                    ),
                                  }
                                : item,
                          ),
                        },
                      }))
                    }
                    type="button"
                  >
                    {t("factor.transform.undo")}
                  </button>
                </div>
                <FactorPreviewControl
                  end={draft.data.end}
                  factor={factor}
                  snapshotId={dataset.data?.snapshot.snapshot_id}
                  start={draft.data.start}
                />
                <button
                  className="factor-remove"
                  onClick={() =>
                    update((current) => ({
                      ...current,
                      factors: {
                        factors: current.factors.factors.filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      },
                    }))
                  }
                  type="button"
                >
                  {t("factor.selected.remove")}
                </button>
              </article>
            );
          })}
        </div>
      </section>

      <FactorBrowser
        factors={catalog.data?.factors ?? []}
        loading={catalog.isLoading}
        onAdd={addFactor}
        onSearch={setSearch}
        registryVersion={catalog.data?.registry_version}
        search={search}
        selectedIds={selectedIds}
        total={catalog.data?.total ?? 0}
      />
    </section>
  );
};
