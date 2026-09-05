import { useId, useMemo, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { Badge, Button, Tabs, panelId, tabId } from "../../../shared/ui";
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

const TABS = ["target", "node", "plan"] as const;
type DebuggerTab = (typeof TABS)[number];

const tabItems = () => [
  { id: "target" as const, label: t("debugger.tab.target") },
  { id: "node" as const, label: t("debugger.tab.node") },
  { id: "plan" as const, label: t("debugger.tab.plan") },
];

const shortHash = (value: string): string => `${value.slice(0, 12)}…`;

const formatNumber = (value: number | boolean | null): string => {
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

const TargetResult = ({ state }: { state: StrategyTraceState }) => {
  if (state.kind !== "success") return null;
  if (state.response.target === null)
    return (
      <div className="strategy-debugger__state" role="status">
        <p>{t("debugger.target.empty")}</p>
      </div>
    );
  const nodeId = state.request.node_ids?.[0] ?? "";
  const rows = projectTargetTapeRows(
    state.response,
    state.request.security_ids,
    nodeId,
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

const NodeResult = ({ state }: { state: StrategyTraceState }) => {
  if (state.kind !== "success") return null;
  if (state.response.trace.rows.length === 0)
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
          {state.response.trace.rows.map((row) => (
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
      {state.response.trace.has_more ? (
        <p className="strategy-debugger__note" role="status">
          {t("debugger.node.truncated")}
        </p>
      ) : null}
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
  const [tab, setTab] = useState<DebuggerTab>("target");
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
  const externalAsOf = asOf ?? context?.end ?? "";
  const externalSecurity = security ?? "";
  const [scope, setScope] = useState(() => ({
    externalAsOf,
    externalSecurity,
    selectedAsOf: externalAsOf,
    selectedSecurity: externalSecurity,
  }));
  if (
    scope.externalAsOf !== externalAsOf ||
    scope.externalSecurity !== externalSecurity
  ) {
    // Guarded render-time adjustment keeps back/forward authoritative without a cascading effect.
    setScope({
      externalAsOf,
      externalSecurity,
      selectedAsOf: externalAsOf,
      selectedSecurity: externalSecurity,
    });
  }
  const { selectedAsOf, selectedSecurity } = scope;
  const selection = useMemo(
    () => ({
      asOf: selectedAsOf,
      security: selectedSecurity,
      factorId: factor?.factorId ?? "",
      nodeId: node?.nodeId ?? "",
    }),
    [factor?.factorId, node?.nodeId, selectedAsOf, selectedSecurity],
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
            value={selectedAsOf}
            min={context?.start}
            max={context?.end}
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
              item === "target" ? (
                <TargetResult state={trace.state} />
              ) : item === "node" ? (
                <NodeResult state={trace.state} />
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
