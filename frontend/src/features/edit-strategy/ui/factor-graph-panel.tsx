import { useState } from "react";

import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import type { DocumentDiagnostic } from "../model/document-state";
import {
  projectFactorGraphs,
  type FactorGraphProjection,
  type GraphFactorProjection,
  type GraphNodeProjection,
} from "../model/factor-graph-projection";
import {
  factorIndexAtPointer,
  pointerSelectsNode,
  type ExecutionPlansState,
} from "../model/use-execution-plans";
import "./factor-graph-panel.css";

type FactorGraphPanelProps = {
  state: ExecutionPlansState;
  diagnostics: DocumentDiagnostic[];
  selectedPointer?: string;
  onSelectPointer: (pointer: string) => void;
  onOpenSource: (pointer: string) => void;
};

const graphDiagnostics = (diagnostics: DocumentDiagnostic[]) =>
  diagnostics.filter(
    (diagnostic) =>
      diagnostic.nodeId != null || diagnostic.pointer.includes("/graph"),
  );

const stateMessage = (
  state: Exclude<FactorGraphProjection, { status: "ready" }>,
): string => {
  if (state.status === "blocked") return t(`plan.blocked.${state.reason}`);
  if (state.status === "incompatible") {
    return t("plan.incompatible")
      .replace("{expected}", state.expected)
      .replace("{actual}", state.actual ?? "—");
  }
  if (state.status === "error") return `${t("plan.error")} (${state.message})`;
  return t(`plan.${state.status}`);
};

const GraphState = ({
  state,
  diagnostics,
  onOpenSource,
}: {
  state: Exclude<FactorGraphProjection, { status: "ready" }>;
  diagnostics: DocumentDiagnostic[];
  onOpenSource: (pointer: string) => void;
}) => {
  const relevant = graphDiagnostics(diagnostics);
  return (
    <section className="factor-graph__state" aria-label={t("graph.title")}>
      <strong>{t("graph.title")}</strong>
      <p role="status">{stateMessage(state)}</p>
      {relevant.length > 0 ? (
        <ul className="factor-graph__diagnostics">
          {relevant.map((diagnostic, index) => (
            <li
              key={`${diagnostic.code}:${diagnostic.pointer}:${diagnostic.nodeId ?? ""}:${index}`}
            >
              <Badge tone={diagnostic.severity === "error" ? "error" : "warn"}>
                {diagnostic.code}
              </Badge>
              {diagnostic.nodeId ? <code>{diagnostic.nodeId}</code> : null}
              <span>{diagnostic.message}</span>
              {diagnostic.pointer ? (
                <button
                  type="button"
                  onClick={() => onOpenSource(diagnostic.pointer)}
                >
                  {t("graph.openSource")}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
};

const shortHash = (value: string): string => `${value.slice(0, 12)}…`;

const FactorSummary = ({
  factor,
  registryVersion,
  dataSnapshotId,
}: {
  factor: GraphFactorProjection;
  registryVersion: string;
  dataSnapshotId: string;
}) => (
  <dl className="factor-graph__summary">
    <div>
      <dt>{t("graph.planSource")}</dt>
      <dd>
        <Badge tone={factor.valid ? "ok" : "error"}>
          {factor.source === "execution-plan"
            ? t("graph.backendPlan")
            : t("graph.validationOnly")}
        </Badge>
      </dd>
    </div>
    <div>
      <dt>{t("plan.registry")}</dt>
      <dd>
        <code>{registryVersion}</code>
      </dd>
    </div>
    <div>
      <dt>{t("plan.dataSnapshot")}</dt>
      <dd>
        <code>{dataSnapshotId}</code>
      </dd>
    </div>
    <div>
      <dt>{t("plan.minimumHistory")}</dt>
      <dd>
        {factor.minimumHistorySessions} {t("plan.sessions")}
      </dd>
    </div>
    {factor.graphHash !== null ? (
      <div>
        <dt>{t("plan.graphFingerprint")}</dt>
        <dd>
          <code title={factor.graphHash}>{shortHash(factor.graphHash)}</code>
        </dd>
      </div>
    ) : null}
    {factor.planHash !== null ? (
      <div>
        <dt>{t("plan.planFingerprint")}</dt>
        <dd>
          <code title={factor.planHash}>{shortHash(factor.planHash)}</code>
        </dd>
      </div>
    ) : null}
  </dl>
);

const GraphNode = ({
  node,
  selectedPointer,
  onSelectPointer,
  onOpenSource,
}: {
  node: GraphNodeProjection;
  selectedPointer?: string;
  onSelectPointer: (pointer: string) => void;
  onOpenSource: (pointer: string) => void;
}) => {
  const selected =
    node.pointer !== null && pointerSelectsNode(selectedPointer, node.pointer);
  return (
    <li
      className={`factor-graph__node factor-graph__node--${node.kind}`}
      aria-current={selected ? "true" : undefined}
      data-selected={selected || undefined}
      data-node-id={node.nodeId}
    >
      <header>
        <span className="factor-graph__sequence">
          {String(node.sequence).padStart(2, "0")}
        </span>
        <button
          type="button"
          className="factor-graph__node-select"
          disabled={node.pointer === null}
          onClick={() => node.pointer !== null && onSelectPointer(node.pointer)}
          aria-label={t("graph.selectNode").replace("{node}", node.nodeId)}
        >
          <strong>{node.nodeId}</strong>
          <code>{node.operation}</code>
        </button>
        {node.isOutput ? <Badge tone="accent">OUTPUT</Badge> : null}
      </header>

      {node.inputs.length > 0 ? (
        <div className="factor-graph__inputs">
          <span>{t("graph.incoming")}</span>
          <ul>
            {node.inputs.map((input, index) => (
              <li key={`${input.role}:${input.nodeId}:${index}`}>
                <span className="factor-graph__edge" aria-hidden="true">
                  →
                </span>
                <button
                  type="button"
                  disabled={input.pointer === null}
                  onClick={() =>
                    input.pointer !== null && onSelectPointer(input.pointer)
                  }
                  aria-label={t("graph.selectInput")
                    .replace("{role}", input.role)
                    .replace("{node}", input.nodeId)}
                >
                  <span>{input.role}</span>
                  <code>{input.nodeId}</code>
                  <small>
                    {input.outputType ?? "unknown"} ·{" "}
                    {input.outputUnit ?? "unknown"}
                  </small>
                </button>
                {input.pointer === null ? (
                  <Badge tone="error">{t("graph.missingInput")}</Badge>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="factor-graph__root">{t("graph.sourceNode")}</p>
      )}

      {node.details.length > 0 ? (
        <dl className="factor-graph__details">
          {node.details.map((detail) => (
            <div key={detail.label}>
              <dt>{detail.label}</dt>
              <dd>
                <code>{detail.value}</code>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      <footer>
        <div className="factor-graph__contract">
          <code>{node.outputType ?? "unknown"}</code>
          <span>{node.outputUnit ?? "unknown"}</span>
          <span>
            H {node.minimumHistorySessions ?? "—"} {t("plan.sessions")}
          </span>
        </div>
        <button
          type="button"
          disabled={node.pointer === null}
          onClick={() => node.pointer !== null && onOpenSource(node.pointer)}
        >
          {t("graph.openSource")}
        </button>
      </footer>

      {node.issues.length > 0 ? (
        <ul className="factor-graph__node-issues">
          {node.issues.map((issue, index) => (
            <li key={`${issue.code}:${issue.path}:${index}`}>
              <Badge tone={issue.severity === "warning" ? "warn" : "error"}>
                {issue.code}
              </Badge>
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
};

export const FactorGraphPanel = ({
  state,
  diagnostics,
  selectedPointer,
  onSelectPointer,
  onOpenSource,
}: FactorGraphPanelProps) => {
  const [chosenFactor, setChosenFactor] = useState(0);
  const projection = projectFactorGraphs(state);
  if (projection.status !== "ready") {
    return (
      <GraphState
        state={projection}
        diagnostics={diagnostics}
        onOpenSource={onOpenSource}
      />
    );
  }
  if (projection.factors.length === 0) {
    return (
      <GraphState
        state={{ status: "empty" }}
        diagnostics={diagnostics}
        onOpenSource={onOpenSource}
      />
    );
  }

  const routeFactor = factorIndexAtPointer(selectedPointer);
  const activeIndex =
    routeFactor !== null && routeFactor < projection.factors.length
      ? routeFactor
      : chosenFactor < projection.factors.length
        ? chosenFactor
        : 0;
  const factor = projection.factors[activeIndex];
  const graphLevelIssues = factor.issues.filter(
    (issue) => issue.node_id === null,
  );

  return (
    <section className="factor-graph" aria-label={t("graph.title")}>
      <header className="factor-graph__toolbar">
        <div>
          <strong>{t("graph.title")}</strong>
          <span>{t("graph.readOnly")}</span>
        </div>
        <label>
          <span>{t("plan.factor")}</span>
          <select
            value={activeIndex}
            onChange={(event) => {
              const next = Number(event.target.value);
              setChosenFactor(next);
              onSelectPointer(`/factors/factors/${next}/graph`);
            }}
          >
            {projection.factors.map((item, index) => (
              <option value={index} key={`${item.factorId}:${index}`}>
                {item.label} · {item.factorId}
              </option>
            ))}
          </select>
        </label>
      </header>

      <FactorSummary
        factor={factor}
        registryVersion={projection.expectedRegistryVersion}
        dataSnapshotId={projection.expectedDataSnapshotId}
      />

      {graphLevelIssues.length > 0 ? (
        <ul className="factor-graph__diagnostics">
          {graphLevelIssues.map((issue, index) => (
            <li key={`${issue.code}:${issue.path}:${index}`}>
              <Badge tone={issue.severity === "warning" ? "warn" : "error"}>
                {issue.code}
              </Badge>
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="factor-graph__canvas" tabIndex={0}>
        <ol aria-label={t("graph.dag")}>
          {factor.nodes.map((node) => (
            <GraphNode
              key={`${node.sequence}:${node.nodeId}`}
              node={node}
              selectedPointer={selectedPointer}
              onSelectPointer={onSelectPointer}
              onOpenSource={onOpenSource}
            />
          ))}
        </ol>
      </div>
    </section>
  );
};
