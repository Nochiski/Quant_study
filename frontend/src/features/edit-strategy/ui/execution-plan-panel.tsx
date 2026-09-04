import { useState } from "react";

import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import {
  factorIndexAtPointer,
  nodePointerById,
  pointerSelectsNode,
  type ExecutionPlansState,
  type PlannedFactor,
} from "../model/use-execution-plans";
import "./execution-plan-panel.css";

type ExecutionPlanPanelProps = {
  state: ExecutionPlansState;
  selectedPointer?: string;
  onSelectPointer: (pointer: string) => void;
};

const blockedMessage = (
  reason: Extract<ExecutionPlansState, { status: "blocked" }>["reason"],
) => t(`plan.blocked.${reason}`);

const shortHash = (value: string): string => `${value.slice(0, 12)}…`;

const PlanState = ({
  state,
}: {
  state: Exclude<ExecutionPlansState, { status: "ready" }>;
}) => {
  const message =
    state.status === "blocked"
      ? blockedMessage(state.reason)
      : state.status === "incompatible"
        ? t("plan.incompatible")
            .replace("{expected}", state.expected)
            .replace("{actual}", state.actual ?? "—")
        : state.status === "error"
          ? `${t("plan.error")} (${state.message})`
          : t(`plan.${state.status}`);
  return (
    <div className="execution-plan__state" role="status">
      <strong>{t("plan.title")}</strong>
      <p>{message}</p>
    </div>
  );
};

const InvalidPlan = ({ factor }: { factor: PlannedFactor }) => (
  <div className="execution-plan__invalid" role="status">
    <Badge tone="error">{t("plan.invalid")}</Badge>
    <ul>
      {factor.explanation.validation.issues.map((issue) => (
        <li key={`${issue.code}:${issue.node_id ?? ""}:${issue.path}`}>
          <code>{issue.code}</code> {issue.message}
        </li>
      ))}
    </ul>
  </div>
);

export const ExecutionPlanPanel = ({
  state,
  selectedPointer,
  onSelectPointer,
}: ExecutionPlanPanelProps) => {
  const [chosenFactor, setChosenFactor] = useState(0);
  if (state.status !== "ready") return <PlanState state={state} />;
  if (state.factors.length === 0)
    return <PlanState state={{ status: "empty" }} />;

  const routeFactor = factorIndexAtPointer(selectedPointer);
  const activeIndex =
    routeFactor !== null && routeFactor < state.factors.length
      ? routeFactor
      : chosenFactor < state.factors.length
        ? chosenFactor
        : 0;
  const factor = state.factors[activeIndex];
  const plan = factor.explanation.plan;
  const planSteps = new Map(
    (plan?.steps ?? []).map((step) => [step.node_id, step]),
  );

  return (
    <div className="execution-plan">
      <div className="execution-plan__toolbar">
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
            {state.factors.map((item, index) => (
              <option value={index} key={`${item.factorId}:${index}`}>
                {item.label} · {item.factorId}
              </option>
            ))}
          </select>
        </label>
        <Badge tone="ok">{t("plan.current")}</Badge>
      </div>

      {plan === null ? (
        <InvalidPlan factor={factor} />
      ) : (
        <>
          <dl className="execution-plan__summary">
            <div>
              <dt>{t("plan.registry")}</dt>
              <dd>
                <code>{plan.registry_version}</code>
              </dd>
            </div>
            <div>
              <dt>{t("plan.dataSnapshot")}</dt>
              <dd>
                <code>{state.expectedDataSnapshotId}</code>
              </dd>
            </div>
            <div>
              <dt>{t("plan.minimumHistory")}</dt>
              <dd>
                {plan.minimum_history_sessions} {t("plan.sessions")}
              </dd>
            </div>
            <div>
              <dt>{t("plan.pitPolicy")}</dt>
              <dd>
                <Badge tone="ok">{plan.as_of_policy}</Badge>
              </dd>
            </div>
            <div>
              <dt>{t("plan.missingPolicy")}</dt>
              <dd>
                <code>{plan.missing_policy}</code>
              </dd>
            </div>
            <div>
              <dt>{t("plan.graphFingerprint")}</dt>
              <dd>
                <code title={plan.graph_hash}>
                  {shortHash(plan.graph_hash)}
                </code>
              </dd>
            </div>
            <div>
              <dt>{t("plan.planFingerprint")}</dt>
              <dd>
                <code title={plan.plan_hash}>{shortHash(plan.plan_hash)}</code>
              </dd>
            </div>
          </dl>

          <div className="execution-plan__table-wrap">
            <table className="execution-plan__table">
              <caption className="sr-only">{t("plan.table")}</caption>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">{t("plan.operation")}</th>
                  <th scope="col">{t("plan.inputs")}</th>
                  <th scope="col">{t("plan.output")}</th>
                  <th scope="col">{t("plan.history")}</th>
                </tr>
              </thead>
              <tbody>
                {plan.steps.map((step) => {
                  const pointer = nodePointerById(factor, step.node_id);
                  const selected =
                    pointer !== null &&
                    pointerSelectsNode(selectedPointer, pointer);
                  return (
                    <tr key={step.node_id} aria-selected={selected}>
                      <td>{step.sequence}</td>
                      <td>
                        <button
                          type="button"
                          className="execution-plan__node"
                          disabled={pointer === null}
                          aria-label={t("plan.openNode")
                            .replace("{node}", step.node_id)
                            .replace("{pointer}", pointer ?? "—")}
                          onClick={() =>
                            pointer !== null && onSelectPointer(pointer)
                          }
                        >
                          <code>{step.node_id}</code>
                          <span>{step.operation}</span>
                        </button>
                      </td>
                      <td>
                        {step.input_node_ids.length === 0 ? (
                          <span className="execution-plan__muted">—</span>
                        ) : (
                          <ul className="execution-plan__inputs">
                            {step.input_node_ids.map((nodeId) => {
                              const inputStep = planSteps.get(nodeId);
                              const inputPointer = nodePointerById(
                                factor,
                                nodeId,
                              );
                              return (
                                <li key={nodeId}>
                                  <button
                                    type="button"
                                    disabled={inputPointer === null}
                                    onClick={() =>
                                      inputPointer !== null &&
                                      onSelectPointer(inputPointer)
                                    }
                                  >
                                    <code>{nodeId}</code>
                                    <small>
                                      {inputStep?.output_type ?? "—"} ·{" "}
                                      {inputStep?.output_unit ?? "—"}
                                    </small>
                                  </button>
                                </li>
                              );
                            })}
                          </ul>
                        )}
                      </td>
                      <td>
                        <code>{step.output_type}</code>
                        <small>{step.output_unit}</small>
                      </td>
                      <td>
                        {step.minimum_history_sessions} {t("plan.sessions")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="execution-plan__fields">
            <strong>{t("plan.requiredFields")}</strong>{" "}
            {plan.required_field_ids.length > 0
              ? plan.required_field_ids.map((field) => (
                  <code key={field}>{field}</code>
                ))
              : "—"}
          </p>
        </>
      )}
    </div>
  );
};
