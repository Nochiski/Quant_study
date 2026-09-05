import {
  useFactorValidation,
  type FactorGraph,
  type FactorSignal,
} from "../../../entities/factor";
import { t } from "../../../shared/config";
import {
  appendFactorTransform,
  nodeInputIds,
  quickTransforms,
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

const AdvancedFactorGraph = ({
  factor,
  factorIds,
  parameterIds,
  onGraphChange,
}: {
  factor: FactorSignal;
  factorIds: string[];
  parameterIds: string[];
  onGraphChange: (graph: FactorGraph) => void;
}) => {
  const validation = useFactorValidation({
    graph: factor.graph,
    factor_ids: factorIds,
    parameter_ids: parameterIds,
  });
  const contractByNode = new Map(
    validation.data?.node_contracts.map((contract) => [
      contract.node_id,
      contract,
    ]),
  );

  return (
    <article className="graph-factor">
      <header>
        <span className="graph-factor__direction">{factor.direction}</span>
        <strong>{factor.label}</strong>
        <code>{factor.graph.output_node_id}</code>
      </header>
      <div className="graph-summary" aria-live="polite">
        <span className={validation.data?.valid ? "is-valid" : "is-invalid"}>
          {validation.isLoading
            ? t("factor.graph.validating")
            : validation.data?.valid
              ? t("factor.graph.valid")
              : t("factor.graph.invalid")}
        </span>
        {validation.data !== undefined && (
          <span>
            {t("factor.graph.minHistory")}:{" "}
            {validation.data.minimum_history_sessions}
          </span>
        )}
      </div>
      <div className="transform-toolbar transform-toolbar--advanced">
        {quickTransforms.map((transform) => (
          <button
            key={transform}
            onClick={() =>
              onGraphChange(appendFactorTransform(factor.graph, transform))
            }
            type="button"
          >
            + {transformLabels[transform]}
          </button>
        ))}
      </div>
      <div className="graph-nodes">
        {factor.graph.nodes.length === 0 ? (
          <p>{t("strategy.graphEmpty")}</p>
        ) : (
          factor.graph.nodes.map((node, index) => {
            const contract = contractByNode.get(node.node_id);
            const issues =
              validation.data?.issues.filter(
                (issue) => issue.node_id === node.node_id,
              ) ?? [];
            const operation = "operator" in node ? node.operator : node.kind;
            return (
              <div
                className={`graph-node ${issues.length > 0 ? "graph-node--invalid" : ""}`}
                key={node.node_id}
              >
                <span className="graph-node__sequence">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="graph-node__body">
                  <strong>{node.kind}</strong>
                  <code>{node.node_id}</code>
                  <span>{operation}</span>
                  {nodeInputIds(node).length > 0 && (
                    <small>
                      {t("factor.graph.inputs")}:{" "}
                      {nodeInputIds(node).join(", ")}
                    </small>
                  )}
                  {issues.map((issue) => (
                    <small
                      className="graph-node__issue"
                      key={`${issue.code}-${issue.path}`}
                    >
                      {issue.message}
                    </small>
                  ))}
                </div>
                <div className="graph-node__contract">
                  <span>{contract?.value_type ?? "numeric_series"}</span>
                  <code>{contract?.unit ?? "unknown"}</code>
                  <small>H {contract?.minimum_history_sessions ?? "—"}</small>
                </div>
              </div>
            );
          })
        )}
      </div>
      {validation.data !== undefined &&
        validation.data.issues
          .filter((issue) => issue.node_id === null)
          .map((issue) => (
            <p className="inline-state inline-state--error" key={issue.code}>
              {issue.message}
            </p>
          ))}
    </article>
  );
};

export const AdvancedGraph = () => {
  const { draft, update } = useStrategyDraft();
  const factorIds = draft.factors.factors.map((factor) => factor.factor_id);
  const parameterIds = (draft.parameters ?? []).map(
    (parameter) => parameter.parameter_id,
  );

  return (
    <section
      className="editor-panel graph"
      aria-label={t("builder.mode.advanced")}
    >
      {draft.factors.factors.map((factor, index) => (
        <AdvancedFactorGraph
          factor={factor}
          factorIds={factorIds}
          key={factor.factor_id}
          onGraphChange={(graph) =>
            update((current) => ({
              ...current,
              factors: {
                factors: current.factors.factors.map((item, itemIndex) =>
                  itemIndex === index ? { ...item, graph } : item,
                ),
              },
            }))
          }
          parameterIds={parameterIds}
        />
      ))}
    </section>
  );
};
