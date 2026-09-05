import { useId, useMemo, useRef, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { useVirtualWindow } from "../../../shared/lib/virtual-window";
import { Badge, Button, Tabs, panelId, tabId } from "../../../shared/ui";
import {
  projectLinkedTraceRows,
  type LinkedTraceRow,
} from "../model/linked-trace";
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

const TABLE_ROW_HEIGHT = 64;
const PIPELINE_ROW_HEIGHT = 324;
const NODE_CHIP_HEIGHT = 58;

type VirtualTableProps<Row> = {
  rows: readonly Row[];
  caption: string;
  columns: readonly string[];
  keyFor: (row: Row, index: number) => string;
  cells: (row: Row, index: number) => ReactNode;
  footer?: ReactNode;
};

/** The trace feature owns table meaning; the shared hook owns only scroll-window math. */
const VirtualTable = <Row,>({
  rows,
  caption,
  columns,
  keyFor,
  cells,
  footer,
}: VirtualTableProps<Row>) => {
  const viewportRef = useRef<HTMLDivElement>(null);
  const virtual = useVirtualWindow(viewportRef, {
    itemCount: rows.length,
    itemHeight: TABLE_ROW_HEIGHT,
    fallbackViewportHeight: 384,
  });
  const visible = rows.slice(virtual.start, virtual.end);
  const spacer = (height: number, key: string) =>
    height > 0 ? (
      <tr
        key={key}
        className="strategy-debugger__virtual-spacer"
        aria-hidden="true"
      >
        <td colSpan={columns.length} style={{ height }} />
      </tr>
    ) : null;

  return (
    <div
      ref={viewportRef}
      className="strategy-debugger__table-wrap strategy-debugger__virtual-viewport"
      role="region"
      aria-label={caption}
      tabIndex={0}
      data-rendered-rows={visible.length}
      data-total-rows={rows.length}
      data-virtualized={virtual.virtualized || undefined}
      onKeyDown={virtual.onKeyDown}
      onScroll={virtual.onScroll}
    >
      <table
        className="strategy-debugger__table"
        aria-rowcount={rows.length + 1}
      >
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr aria-rowindex={1}>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spacer(virtual.paddingBefore, "before")}
          {visible.map((row, visibleIndex) => {
            const index = virtual.start + visibleIndex;
            return (
              <tr key={keyFor(row, index)} aria-rowindex={index + 2}>
                {cells(row, index)}
              </tr>
            );
          })}
          {spacer(virtual.paddingAfter, "after")}
        </tbody>
      </table>
      {footer}
    </div>
  );
};

const VirtualNodeChain = ({
  nodes,
  selectedNodeId,
}: {
  nodes: LinkedTraceRow["nodes"];
  selectedNodeId: string;
}) => {
  const selectedIndex = nodes.findIndex(
    (row) => row.node_id === selectedNodeId,
  );
  const viewportRef = useRef<HTMLDivElement>(null);
  const virtual = useVirtualWindow(viewportRef, {
    itemCount: nodes.length,
    itemHeight: NODE_CHIP_HEIGHT,
    fallbackViewportHeight: 174,
    anchorIndex: selectedIndex < 0 ? null : selectedIndex,
    overscan: 3,
    threshold: 24,
  });
  return (
    <div
      ref={viewportRef}
      className="strategy-debugger__node-chain"
      role="list"
      aria-label={t("debugger.stage.nodes")}
      tabIndex={0}
      data-rendered-rows={virtual.end - virtual.start}
      data-total-rows={nodes.length}
      data-virtualized={virtual.virtualized || undefined}
      onKeyDown={virtual.onKeyDown}
      onScroll={virtual.onScroll}
    >
      {virtual.paddingBefore > 0 ? (
        <span
          className="strategy-debugger__virtual-block"
          style={{ height: virtual.paddingBefore }}
          aria-hidden="true"
        />
      ) : null}
      {nodes.slice(virtual.start, virtual.end).map((nodeRow, visibleIndex) => {
        const index = virtual.start + visibleIndex;
        return (
          <span
            key={`${nodeRow.security_id}:${nodeRow.node_id}`}
            role="listitem"
            aria-posinset={index + 1}
            aria-setsize={nodes.length}
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
        );
      })}
      {virtual.paddingAfter > 0 ? (
        <span
          className="strategy-debugger__virtual-block"
          style={{ height: virtual.paddingAfter }}
          aria-hidden="true"
        />
      ) : null}
    </div>
  );
};

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
  const rows =
    state.kind === "success"
      ? projectLinkedTraceRows(
          state.response,
          state.request.security_ids,
          state.linkedRows,
          state.selectedRows,
        )
      : [];
  const viewportRef = useRef<HTMLDivElement>(null);
  const virtual = useVirtualWindow(viewportRef, {
    itemCount: rows.length,
    itemHeight: PIPELINE_ROW_HEIGHT,
    fallbackViewportHeight: 648,
    overscan: 1,
    threshold: 12,
  });
  if (state.kind !== "success") return null;
  const target = state.response.target;
  return (
    <div
      ref={viewportRef}
      className="strategy-debugger__pipeline-list"
      role="list"
      aria-label={t("debugger.tab.trace")}
      tabIndex={0}
      data-rendered-rows={virtual.end - virtual.start}
      data-total-rows={rows.length}
      data-virtualized={virtual.virtualized || undefined}
      onKeyDown={virtual.onKeyDown}
      onScroll={virtual.onScroll}
    >
      {virtual.paddingBefore > 0 ? (
        <div
          className="strategy-debugger__virtual-block"
          style={{ height: virtual.paddingBefore }}
          aria-hidden="true"
        />
      ) : null}
      {rows
        .slice(virtual.start, virtual.end)
        .map(({ securityId, raw, nodes, construction }, visibleIndex) => (
          <article
            className="strategy-debugger__pipeline"
            key={securityId}
            role="listitem"
            aria-posinset={virtual.start + visibleIndex + 1}
            aria-setsize={rows.length}
          >
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
                <VirtualNodeChain
                  nodes={nodes}
                  selectedNodeId={selectedNodeId}
                />
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
                      {t("debugger.column.rank")}{" "}
                      {formatNumber(construction.rank)} ·{" "}
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
      {virtual.paddingAfter > 0 ? (
        <div
          className="strategy-debugger__virtual-block"
          style={{ height: virtual.paddingAfter }}
          aria-hidden="true"
        />
      ) : null}
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
    <VirtualTable
      rows={state.response.raw}
      caption={t("debugger.raw.caption")}
      columns={[
        t("debugger.column.security"),
        t("debugger.column.field"),
        t("debugger.column.value"),
        t("debugger.column.availableDate"),
        t("debugger.column.status"),
      ]}
      keyFor={(row) => `${row.security_id}:${row.field_id}`}
      cells={(row) => (
        <>
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
        </>
      )}
      footer={
        state.response.raw_truncated ? (
          <p className="strategy-debugger__note" role="status">
            {t("debugger.raw.truncated")}
          </p>
        ) : null
      }
    />
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
    <VirtualTable
      rows={rows}
      caption={t("debugger.target.caption")}
      columns={[
        t("debugger.column.security"),
        t("debugger.column.score"),
        t("debugger.column.rank"),
        t("debugger.column.selected"),
        t("debugger.column.exclusion"),
        t("debugger.column.target"),
        t("debugger.column.nodeValue"),
        t("debugger.column.nodeStatus"),
      ]}
      keyFor={(row) => row.securityId}
      cells={(row) => (
        <>
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
        </>
      )}
    />
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
  const rows = state.selectedRows.filter(
    (row) => row.node_id === selectedNodeId,
  );
  if (rows.length === 0)
    return (
      <div className="strategy-debugger__state" role="status">
        <p>{t("debugger.node.empty")}</p>
      </div>
    );
  return (
    <VirtualTable
      rows={rows}
      caption={t("debugger.node.caption")}
      columns={[
        t("debugger.column.security"),
        t("debugger.column.node"),
        t("debugger.column.operation"),
        t("debugger.column.inputs"),
        t("debugger.column.value"),
        t("debugger.column.status"),
      ]}
      keyFor={(row) => `${row.security_id}:${row.node_id}`}
      cells={(row) => (
        <>
          <td>
            <code>{row.security_id}</code>
          </td>
          <td>
            <code>{row.node_id}</code>
          </td>
          <td>{row.operation}</td>
          <td>
            {row.inputs.length === 0 ? (
              "—"
            ) : (
              <span className="strategy-debugger__inputs">
                {row.inputs.map((input) => (
                  <code
                    key={input.node_id}
                    className="strategy-debugger__input"
                  >
                    {input.node_id}={formatNumber(input.value)}
                  </code>
                ))}
              </span>
            )}
          </td>
          <td className="strategy-debugger__numeric">
            {formatNumber(row.value)}
          </td>
          <td>
            <Badge tone={row.status === "ok" ? "ok" : "warn"}>
              {row.status}
            </Badge>
          </td>
        </>
      )}
    />
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
          const submittedSelection = {
            asOf: selectedAsOf || undefined,
            security: selectedSecurity || undefined,
          };
          // Route search changes remount this panel. Keep the request owner alive until the
          // submitted calculation settles, then publish the exact scope as a deep link.
          void trace.run().finally(() => onSearchSelection(submittedSelection));
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
