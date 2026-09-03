import { t } from "../../../shared/config";
import { useStrategyDraft } from "../model/strategy-draft-context";

export const AdvancedGraph = () => {
  const { draft } = useStrategyDraft();

  return (
    <section
      className="editor-panel graph"
      aria-label={t("builder.mode.advanced")}
    >
      {draft.factors.factors.map((factor) => (
        <article className="graph-factor" key={factor.factor_id}>
          <header>
            <span className="graph-factor__direction">{factor.direction}</span>
            <strong>{factor.label}</strong>
            <code>{factor.graph.output_node_id}</code>
          </header>
          <div className="graph-nodes">
            {factor.graph.nodes.length === 0 ? (
              <p>{t("strategy.graphEmpty")}</p>
            ) : (
              factor.graph.nodes.map((node, index) => (
                <div className="graph-node" key={node.node_id}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{node.kind ?? "node"}</strong>
                  <code>{node.node_id}</code>
                </div>
              ))
            )}
          </div>
        </article>
      ))}
    </section>
  );
};
