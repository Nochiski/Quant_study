import { useId, useMemo, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { Badge, Button, Tabs, panelId, tabId } from "../../../shared/ui";
import { projectLinkedTraceRows } from "../model/linked-trace";
import { projectTargetTapeRows } from "../model/target-tape";
import type {
  StrategyDebuggerContext,
  StrategyDebuggerUnavailableReason,
} from "../model/strategy-trace";
import {
  useStrategyTrace,
  type StrategyTraceState,
} from "../model/use-strategy-trace";
import "./strategy-debugger.css";

type DebuggerSearchSelection = {
  asOf?: string;
  security?: string;
};

type StrategyDebuggerProps = {
  context: StrategyDebuggerContext | null;
  unavailableReason: StrategyDebuggerUnavailableReason | null;
  asOf?: string;
  security?: string;
  selectedPointer?: string;
  onSearchSelection: (selection: DebuggerSearchSelection) => void;
  onSelectPointer: (pointer: string) => void;
  executionPlan: ReactNode;
};

const TABS = ["trace", "target", "raw", "node", "plan"] as const;
type DebuggerTab = (typeof TABS)[number];

const tabItems = () => [
  { id: "trace" as const, label: t("debugger.tab.trace") },
  { id: "target" as const, label: t("debugger.tab.target") },
  { id: "raw" as const, label: t("debugger.tab.raw") },
  { id: "node" as const, label: t("debugger.tab.node") },
  { id: "plan" as const, label: t("debugger.tab.plan") },
];

const shortHash = (value: string): string => `${value.slice(0, 12)}…`;

const formatNumber = (value: number | string | boolean | null): string => {
  if (typeof value === "string") return value;
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 8,
  }).format(value);
};

const formatWeight = (value: number | null): string =>
  value === null
    ? "—"
    : `${new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      }).format(value * 100)}%`;

const unavailableMessage = (reason: StrategyDebuggerUnavailableReason) =>
  t(`debugger.unavailable.${reason}`);

const BLOCKED_MESSAGES = {
  document: "debugger.blocked.document",
  date: "debugger.blocked.date",
  security: "debugger.blocked.security",
  factor: "debugger.blocked.factor",
  node: "debugger.blocked.node",
  holdings: "debugger.blocked.holdings",
} as const;

const STATE_MESSAGES = {
  idle: "debugger.state.idle",
  loading: "debugger.state.loading",
  cancelled: "debugger.state.cancelled",
  discarded: "debugger.state.discarded",
} as const;

const StateNotice = ({ state }: { state: StrategyTraceState }) => {
  if (state.kind === "error")
    return (
      <div className="strategy-debugger__state" role="alert">
        <strong>{t("debugger.state.error")}</strong>
        <p>{state.message}</p>
      </div>
    );
  if (state.kind === "success") return null;
  const key =
    state.kind === "blocked"
      ? BLOCKED_MESSAGES[state.reason]
      : STATE_MESSAGES[state.kind];
  return (
    <div className="strategy-debugger__state" role="status">
      <p>{t(key)}</p>
    </div>
  );
};

const LinkedTraceResult = ({
  state,
  selectedNodeId,
}: {
  state: StrategyTraceState;
  selectedNodeId: string;
}) => {
  if (state.kind !== "success") return null;
  const target = state.response.target;
  const rows = projectLinkedTraceRows(
    state.response,
    state.request.security_ids,
    state.linkedRows,
  );
  return (
    <div className="strategy-debugger__pipeline-list">
      {rows.map(({ securityId, raw, nodes, construction }) => (
        <article className="strategy-debugger__pipeline" key={securityId}>
          <header className="strategy-debugger__pipeline-header">
            <h3>{securityId}</h3>
            {construction === null ? (
              <Badge tone="warn">{t("debugger.target.unavailable")}</Badge>
            ) : (
              <>
                <Badge tone={construction.selected ? "ok" : "neutral"}>
                  {construction.selected
                    ? t("debugger.value.selected")
                    : t("debugger.value.excluded")}
                </Badge>
                <Badge
                  tone={
                    construction.constraint_effect === "removed"
                      ? "error"
                      : construction.constraint_effect === "adjusted"
                        ? "warn"
                        : "neutral"
                  }
                >
                  {construction.constraint_effect}
                </Badge>
              </>
            )}
          </header>
          <ol className="strategy-debugger__pipeline-stages">
            <li>
              <strong>{t("debugger.stage.raw")}</strong>
              <div className="strategy-debugger__stage-values">
                {raw.length === 0 ? (
                  <span>{t("debugger.raw.empty")}</span>
                ) : (
                  raw.map((field) => (
                    <span key={field.field_id}>
                      <code>{field.field_id}</code>
                      <span className="strategy-debugger__numeric">
                        {formatNumber(field.value)}
                      </span>
                      <Badge tone={field.kind === "observed" ? "ok" : "warn"}>
                        {field.kind}
                      </Badge>
                      <small>{field.available_date}</small>
                    </span>
                  ))
                )}
              </div>
            </li>
            <li>
              <strong>{t("debugger.stage.nodes")}</strong>
              <div className="strategy-debugger__node-chain">
                {nodes.map((nodeRow) => (
                  <span
                    key={nodeRow.node_id}
                    className={
                      nodeRow.node_id === selectedNodeId
                        ? "strategy-debugger__node-chip strategy-debugger__node-chip--selected"
                        : "strategy-debugger__node-chip"
                    }
                  >
                    <code>{nodeRow.node_id}</code>
                    <span>{nodeRow.operation}</span>
                    <span className="strategy-debugger__numeric">
                      {formatNumber(nodeRow.value)}
                    </span>
                    <Badge tone={nodeRow.status === "ok" ? "ok" : "warn"}>
                      {nodeRow.status}
                    </Badge>
                  </span>
                ))}
              </div>
            </li>
            {construction === null || target === null ? (
              <li>
                <strong>{t("debugger.target.unavailable")}</strong>
                <span>{t("debugger.target.partial")}</span>
              </li>
            ) : (
              <>
                <li>
                  <strong>{t("debugger.stage.contribution")}</strong>
                  <div className="strategy-debugger__stage-values">
                    {construction.factor_contributions.map((contribution) => (
                      <span key={contribution.factor_id}>
                        <code>{contribution.factor_id}</code>
                        <span>
                          {contribution.direction} ×{" "}
                          {formatNumber(contribution.configured_weight)}
                        </span>
                        <span className="strategy-debugger__numeric">
                          {formatNumber(contribution.normalized_contribution)}
                        </span>
                        <Badge
                          tone={contribution.status === "ok" ? "ok" : "warn"}
                        >
                          {contribution.status}
                        </Badge>
                      </span>
                    ))}
                  </div>
                </li>
                <li>
                  <strong>{t("debugger.stage.composite")}</strong>
                  <span className="strategy-debugger__stage-primary">
                    {formatNumber(construction.composite_score)}
                  </span>
                </li>
                <li>
                  <strong>{t("debugger.stage.selection")}</strong>
                  <span className="strategy-debugger__stage-primary">
                    {t("debugger.column.rank")} {formatNumber(construction.rank)} ·{" "}
                    {construction.side ?? t("debugger.value.none")}
                  </span>
                  {construction.exclusion_reasons.length > 0 ? (
                    <code>{construction.exclusion_reasons.join(", ")}</code>
                  ) : null}
                </li>
                <li>
                  <strong>{t("debugger.stage.unconstrained")}</strong>
                  <span className="strategy-debugger__stage-primary">
                    {formatWeight(construction.unconstrained_target_weight)}
                  </span>
                </li>
                <li>
                  <strong>{t("debugger.stage.constrained")}</strong>
                  <span className="strategy-debugger__stage-primary">
                    {formatWeight(construction.constrained_target_weight)}
                  </span>
                  <Badge
                    tone={
                      construction.constraint_effect === "removed"
                        ? "error"
                        : construction.constraint_effect === "adjusted"
                          ? "warn"
                          : "neutral"
                    }
                  >
                    {construction.constraint_effect}
                  </Badge>
                </li>
                {construction.estimated_order_delta !== null ? (
                  <li>
                    <strong>{t("debugger.stage.orderDelta")}</strong>
                    <span className="strategy-debugger__stage-primary">
                      {t("debugger.order.previous")}{" "}
                      {formatWeight(construction.previous_weight)} →{" "}
                      {t("debugger.order.delta")}{" "}
                      {formatWeight(construction.estimated_order_delta)}
                    </span>
                    <small>
                      {target.signal_as_of} → {target.execution_on} ·{" "}
                      {t("debugger.order.assumption")}
                    </small>
                  </li>
                ) : null}
              </>
            )}
          </ol>
        </article>
      ))}
    </div>
  );
};

const RawResult = ({ state }: { state: StrategyTraceState }) => {
  if (state.kind !== "success") return null;
  if (state.response.raw.length === 0)
    return (
      <div className="strategy-debugger__state" role="status">
        <p>{t("debugger.raw.empty")}</p>
      </div>
    );
  return (
    <div className="strategy-debugger__table-wrap">
      <table className="strategy-debugger__table">
        <caption className="sr-only">{t("debugger.raw.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("debugger.column.security")}</th>
            <th scope="col">{t("debugger.column.field")}</th>
            <th scope="col">{t("debugger.column.value")}</th>
            <th scope="col">{t("debugger.column.availableDate")}</th>
            <th scope="col">{t("debugger.column.status")}</th>
          </tr>
        </thead>
        <tbody>
          {state.response.raw.map((row) => (
            <tr key={`${row.security_id}:${row.field_id}`}>
              <td>
                <code>{row.security_id}</code>
              </td>
              <td>
                <code>{row.field_id}</code>
              </td>
              <td className="strategy-debugger__numeric">
                {formatNumber(row.value)}
              </td>
              <td>{row.available_date}</td>
              <td>
                <Badge tone={row.kind === "observed" ? "ok" : "warn"}>
                  {row.kind}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {state.response.raw_truncated ? (
        <p className="strategy-debugger__note" role="status">
          {t("debugger.raw.truncated")}
        </p>
      ) : null}
    </div>
  );
};

const TargetResult = ({
  state,
  selectedNodeId,
}: {
  state: StrategyTraceState;
  selectedNodeId: string;
}) => {
  if (state.kind !== "success") return null;
  if (state.response.target === null)
    return (
      <div className="strategy-debugger__state" role="status">
        <p>{t("debugger.target.empty")}</p>
      </div>
    );
  const rows = projectTargetTapeRows(
    state.response,
    state.request.security_ids,
    selectedNodeId,
    state.selectedRows,
  );
  return (
    <div className="strategy-debugger__table-wrap">
      <table className="strategy-debugger__table">
        <caption className="sr-only">{t("debugger.target.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("debugger.column.security")}</th>
            <th scope="col">{t("debugger.column.score")}</th>
            <th scope="col">{t("debugger.column.rank")}</th>
            <th scope="col">{t("debugger.column.selected")}</th>
            <th scope="col">{t("debugger.column.exclusion")}</th>
            <th scope="col">{t("debugger.column.target")}</th>
            <th scope="col">{t("debugger.column.nodeValue")}</th>
            <th scope="col">{t("debugger.column.nodeStatus")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.securityId}>
              <td>
                <code>{row.securityId}</code>
              </td>
              <td className="strategy-debugger__numeric">
                {formatNumber(row.score)}
              </td>
              <td className="strategy-debugger__numeric">
                {formatNumber(row.rank)}
              </td>
              <td>
                {row.selected === null ? (
                  "—"
                ) : (
                  <Badge tone={row.selected ? "ok" : "neutral"}>
                    {row.selected
                      ? t("debugger.value.yes")
                      : t("debugger.value.no")}
                  </Badge>
                )}
              </td>
              <td>
                {row.exclusionReasons.length === 0 ? (
                  "—"
                ) : (
                  <code>{row.exclusionReasons.join(", ")}</code>
                )}
              </td>
              <td className="strategy-debugger__numeric">
                {formatWeight(row.targetWeight)}
              </td>
              <td className="strategy-debugger__numeric">
                {formatNumber(row.nodeValue)}
              </td>
              <td>
                {row.nodeStatus === null ? (
                  "—"
                ) : (
                  <Badge tone={row.nodeStatus === "ok" ? "ok" : "warn"}>
                    {row.nodeStatus}
                  </Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const NodeResult = ({
  state,
  selectedNodeId,
}: {
  state: StrategyTraceState;
  selectedNodeId: string;
}) => {
  if (state.kind !== "success") return null;
  const rows = state.selectedRows.filter((row) => row.node_id === selectedNodeId);
  if (rows.length === 0)
    return (
      <div className="strategy-debugger__state" role="status">
        <p>{t("debugger.node.empty")}</p>
      </div>
    );
  return (
    <div className="strategy-debugger__table-wrap">
      <table className="strategy-debugger__table">
        <caption className="sr-only">{t("debugger.node.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("debugger.column.security")}</th>
            <th scope="col">{t("debugger.column.node")}</th>
            <th scope="col">{t("debugger.column.operation")}</th>
            <th scope="col">{t("debugger.column.inputs")}</th>
            <th scope="col">{t("debugger.column.value")}</th>
            <th scope="col">{t("debugger.column.status")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.security_id}:${row.node_id}`}>
              <td>
                <code>{row.security_id}</code>
              </td>
              <td>
                <code>{row.node_id}</code>
              </td>
              <td>{row.operation}</td>
              <td>
                {row.inputs.length === 0
                  ? "—"
                  : row.inputs.map((input) => (
                      <code
                        key={input.node_id}
                        className="strategy-debugger__input"
                      >
                        {input.node_id}={formatNumber(input.value)}
                      </code>
                    ))}
              </td>
              <td className="strategy-debugger__numeric">
                {formatNumber(row.value)}
              </td>
              <td>
                <Badge tone={row.status === "ok" ? "ok" : "warn"}>
                  {row.status}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const Provenance = ({ state }: { state: StrategyTraceState }) => {
  if (state.kind !== "success") return null;
  const { response } = state;
  return (
    <div
      className="strategy-debugger__provenance"
      aria-label={t("debugger.provenance")}
    >
      <span>
        {t("debugger.provenance.spec")}{" "}
        <code title={response.spec_hash}>{shortHash(response.spec_hash)}</code>
      </span>
      <span>
        {t("debugger.provenance.snapshot")} <code>{response.snapshot_id}</code>
      </span>
      <span>
        {t("debugger.provenance.registry")}{" "}
        <code>{response.registry_version}</code>
      </span>
      <span>
        {t("debugger.provenance.plan")}{" "}
        <code title={response.plan_hash}>{shortHash(response.plan_hash)}</code>
      </span>
      <span>
        {t("debugger.provenance.asOf")} <code>{response.as_of}</code>
      </span>
    </div>
  );
};

export const StrategyDebugger = ({
  context,
  unavailableReason,
  asOf,
  security,
  selectedPointer,
  onSearchSelection,
  onSelectPointer,
  executionPlan,
}: StrategyDebuggerProps) => {
  const idBase = useId();
  const [tab, setTab] = useState<DebuggerTab>("trace");
  const routedFactor = context?.factors.find(
    (factor) =>
      selectedPointer === factor.pointer ||
      selectedPointer?.startsWith(`${factor.pointer}/`) === true,
  );
  const factor = routedFactor ?? context?.factors[0];
  const routedNode = factor?.nodes.find(
    (node) =>
      selectedPointer === node.pointer ||
      selectedPointer?.startsWith(`${node.pointer}/`) === true,
  );
  const node =
    routedNode ??
    factor?.nodes.find(
      (candidate) => candidate.nodeId === factor.outputNodeId,
    ) ??
    factor?.nodes[0];
  const externalAsOf = asOf ?? "";
  const externalSecurity = security ?? "";
  const externalDocumentEpoch = context?.documentEpoch ?? -1;
  const [scope, setScope] = useState(() => ({
    externalAsOf,
    externalSecurity,
    externalDocumentEpoch,
    selectedAsOf: externalAsOf,
    selectedSecurity: externalSecurity,
    selectedHoldings: "",
  }));
  if (
    scope.externalAsOf !== externalAsOf ||
    scope.externalSecurity !== externalSecurity ||
    scope.externalDocumentEpoch !== externalDocumentEpoch
  ) {
    // Guarded render-time adjustment keeps back/forward authoritative without a cascading effect.
    setScope({
      externalAsOf,
      externalSecurity,
      externalDocumentEpoch,
      selectedAsOf: externalAsOf,
      selectedSecurity: externalSecurity,
      selectedHoldings:
        scope.externalDocumentEpoch === externalDocumentEpoch
          ? scope.selectedHoldings
          : "",
    });
  }
  const { selectedAsOf, selectedSecurity, selectedHoldings } = scope;
  const selection = useMemo(
    () => ({
      asOf: selectedAsOf,
      security: selectedSecurity,
      factorId: factor?.factorId ?? "",
      nodeId: node?.nodeId ?? "",
      startingHoldings: selectedHoldings,
    }),
    [
      factor?.factorId,
      node?.nodeId,
      selectedAsOf,
      selectedHoldings,
      selectedSecurity,
    ],
  );
  const trace = useStrategyTrace(context, selection);
  const loading = trace.state.kind === "loading";

  return (
    <div className="strategy-debugger">
      <form
        className="strategy-debugger__controls"
        aria-label={t("debugger.controls")}
        onSubmit={(event) => {
          event.preventDefault();
          onSearchSelection({
            asOf: selectedAsOf || undefined,
            security: selectedSecurity || undefined,
          });
          void trace.run();
        }}
      >
        <label>
          <span>{t("debugger.date")}</span>
          <input
            type="date"
            aria-label={t("debugger.date")}
            value={selectedAsOf}
            min={context?.start}
            max={context?.end}
            aria-describedby={`${idBase}-date-note`}
            disabled={context === null}
            onChange={(event) => {
              setScope((current) => ({
                ...current,
                selectedAsOf: event.target.value,
              }));
              onSearchSelection({
                asOf: event.target.value || undefined,
                security: selectedSecurity || undefined,
              });
            }}
          />
          <small id={`${idBase}-date-note`}>{t("debugger.date.note")}</small>
        </label>
        <label className="strategy-debugger__security-control">
          <span>{t("debugger.security")}</span>
          <input
            type="text"
            value={selectedSecurity}
            placeholder={t("debugger.security.placeholder")}
            autoComplete="off"
            spellCheck={false}
            disabled={context === null}
            onChange={(event) =>
              setScope((current) => ({
                ...current,
                selectedSecurity: event.target.value,
              }))
            }
            onBlur={(event) =>
              onSearchSelection({
                asOf: selectedAsOf || undefined,
                security: event.target.value || undefined,
              })
            }
          />
        </label>
        <label className="strategy-debugger__holdings-control">
          <span>{t("debugger.holdings")}</span>
          <input
            type="text"
            value={selectedHoldings}
            placeholder={t("debugger.holdings.placeholder")}
            aria-describedby={`${idBase}-holdings-note`}
            autoComplete="off"
            spellCheck={false}
            disabled={context === null}
            onChange={(event) =>
              setScope((current) => ({
                ...current,
                selectedHoldings: event.target.value,
              }))
            }
          />
          <small id={`${idBase}-holdings-note`}>
            {t("debugger.holdings.note")}
          </small>
        </label>
        <label>
          <span>{t("debugger.factor")}</span>
          <select
            value={factor?.factorId ?? ""}
            disabled={context === null || context.factors.length === 0}
            onChange={(event) => {
              const next = context?.factors.find(
                (candidate) => candidate.factorId === event.target.value,
              );
              const output = next?.nodes.find(
                (candidate) => candidate.nodeId === next.outputNodeId,
              );
              if (output !== undefined) onSelectPointer(output.pointer);
              else if (next !== undefined) onSelectPointer(next.pointer);
            }}
          >
            {(context?.factors ?? []).map((item) => (
              <option value={item.factorId} key={item.factorId}>
                {item.label} · {item.factorId}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t("debugger.node")}</span>
          <select
            value={node?.nodeId ?? ""}
            disabled={factor === undefined}
            onChange={(event) => {
              const next = factor?.nodes.find(
                (candidate) => candidate.nodeId === event.target.value,
              );
              if (next !== undefined) onSelectPointer(next.pointer);
            }}
          >
            {(factor?.nodes ?? []).map((item) => (
              <option value={item.nodeId} key={item.nodeId}>
                {item.nodeId} · {item.operation}
              </option>
            ))}
          </select>
        </label>
        <div className="strategy-debugger__actions">
          <Button
            type="submit"
            tone="primary"
            size="small"
            disabled={trace.prepared.kind !== "ready" || loading}
          >
            {loading ? t("debugger.running") : t("debugger.run")}
          </Button>
          {loading ? (
            <Button size="small" tone="ghost" onClick={trace.cancel}>
              {t("debugger.cancel")}
            </Button>
          ) : null}
        </div>
      </form>

      <div className="strategy-debugger__identity">
        {context === null ? (
          <p role="status">
            {unavailableMessage(unavailableReason ?? "document")}
          </p>
        ) : (
          <>
            <Badge tone="info">
              {context.strategySource.kind === "saved_revision"
                ? t("debugger.source.saved")
                : t("debugger.source.inline")}
            </Badge>
            <span>
              {t("debugger.sourceVersion")} {context.sourceVersion}
            </span>
          </>
        )}
        <Provenance state={trace.state} />
      </div>

      {trace.state.kind === "success" &&
      trace.state.response.warnings?.length ? (
        <ul className="strategy-debugger__warnings" role="status">
          {trace.state.response.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      {trace.state.kind === "success" && trace.state.linkedTruncated ? (
        <p className="strategy-debugger__note" role="status">
          {t("debugger.node.aggregateTruncated")}
        </p>
      ) : null}

      <StateNotice state={trace.state} />

      <div className="strategy-debugger__results">
        <Tabs
          label={t("debugger.results")}
          items={tabItems()}
          value={tab}
          onChange={setTab}
          idBase={idBase}
        />
        {TABS.map((item) => (
          <div
            key={item}
            id={panelId(idBase, item)}
            role="tabpanel"
            aria-labelledby={tabId(idBase, item)}
            className="strategy-debugger__panel"
            hidden={tab !== item}
          >
            {tab === item ? (
              item === "trace" ? (
                <LinkedTraceResult
                  state={trace.state}
                  selectedNodeId={node?.nodeId ?? ""}
                />
              ) : item === "target" ? (
                <TargetResult
                  state={trace.state}
                  selectedNodeId={node?.nodeId ?? ""}
                />
              ) : item === "raw" ? (
                <RawResult state={trace.state} />
              ) : item === "node" ? (
                <NodeResult
                  state={trace.state}
                  selectedNodeId={node?.nodeId ?? ""}
                />
              ) : (
                executionPlan
              )
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
};
