import type { NodeValueType } from "../../../shared/api";
import { t } from "../../../shared/config";
import type { CanvasMetadata } from "../model/canvas-metadata";

/** 타입·단위·순서·history는 backend 값이다. 화면은 계산하거나 추론하지 않는다. */
export const CanvasNodeInfo = ({ metadata }: { metadata: CanvasMetadata }) => {
  const { node, promotion, notExecuted } = metadata;
  return (
    <div className="node-canvas__metadata">
      <span className="factor-graph__contract">
        <code>
          {node.outputType === null
            ? "—"
            : t(`recipe.type.${node.outputType as NodeValueType}`)}
        </code>
        <span>{node.outputUnit ?? "—"}</span>
        <span>
          H {node.minimumHistorySessions ?? "—"} {t("plan.sessions")}
        </span>
      </span>
      <span>
        {t("graph.canvas.sequence").replace(
          "{sequence}",
          String(node.sequence ?? "—"),
        )}
        {node.isOutput ? (
          <span>
            {" "}
            · <span>{t("graph.outputReference")}</span>
          </span>
        ) : null}
        {notExecuted ? (
          <span>
            {" "}
            · <span>{t("graph.notExecuted")}</span>
          </span>
        ) : null}
      </span>
      {promotion ? (
        <span>
          {t("plan.node.booleanScore")} · {t("graph.canvas.readOnly")}
        </span>
      ) : null}
    </div>
  );
};
