import { t } from "../../../shared/config";
import type { GraphFactorProjection } from "../model/factor-graph-projection";
import { pointerSelectsNode } from "../model/use-execution-plans";
import { CanvasNodeInfo } from "./canvas-node-info";

/** 편집 원문이 없는 조회자는 backend 투영만 읽는다. 가짜 원문/트랜잭션을 만들지 않는다. */
export const GraphPlanTable = ({
  factor,
  selectedPointer,
  onSelectPointer,
  onOpenSource,
}: {
  factor: GraphFactorProjection;
  selectedPointer?: string;
  onSelectPointer: (pointer: string) => void;
  onOpenSource: (pointer: string) => void;
}) => (
  <div className="factor-graph__plan-table">
    <table aria-label={t("graph.dag")}>
      <tbody>
        {factor.nodes.map((node, index) => {
          const name =
            node.origin === "boolean-score"
              ? t("plan.node.booleanScore")
              : node.nodeId;
          return (
            <tr
              key={`${node.nodeId}:${index}`}
              data-node-id={node.nodeId}
              aria-current={
                node.origin === "document" &&
                node.pointer !== null &&
                pointerSelectsNode(selectedPointer, node.pointer)
                  ? "true"
                  : undefined
              }
            >
              <th scope="row">
                <button
                  type="button"
                  disabled={node.pointer === null}
                  aria-label={t("graph.selectNode").replace("{node}", name)}
                  onClick={() =>
                    node.pointer !== null && onSelectPointer(node.pointer)
                  }
                >
                  {name}
                </button>
              </th>
              <td>
                <CanvasNodeInfo
                  metadata={{
                    node,
                    notExecuted:
                      factor.source === "execution-plan" && !node.planned,
                  }}
                />
              </td>
              <td>
                {node.inputs.map((input, i) => (
                  <button
                    key={i}
                    type="button"
                    disabled={input.pointer === null}
                    aria-label={t("graph.selectInput")
                      .replace("{role}", input.role)
                      .replace("{node}", input.nodeId)}
                    onClick={() =>
                      input.pointer !== null && onSelectPointer(input.pointer)
                    }
                  >
                    <span>{input.role}</span> · {input.nodeId}
                  </button>
                ))}
              </td>
              <td>
                {node.details.map((detail) => (
                  <p key={detail.label}>
                    {detail.label}: <code>{detail.value}</code>
                  </p>
                ))}
                {node.issues.map((issue, i) => (
                  <p key={i}>
                    <strong>{issue.code}</strong> <span>{issue.message}</span>
                  </p>
                ))}
              </td>
              <td>
                <button
                  type="button"
                  disabled={node.pointer === null}
                  onClick={() =>
                    node.pointer !== null && onOpenSource(node.pointer)
                  }
                >
                  {t("graph.openSource")}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  </div>
);
