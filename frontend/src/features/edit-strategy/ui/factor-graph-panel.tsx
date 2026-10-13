import { useState } from "react";

import { t } from "../../../shared/config";
import { Badge, FailureNotice } from "../../../shared/ui";
import type { DocumentDiagnostic } from "../model/document-state";
import {
  projectFactorGraphs,
  type FactorGraphProjection,
  type GraphFactorProjection,
} from "../model/factor-graph-projection";
import type { OperatorCatalogState } from "../model/operator-palette";
import type { JsonSchema } from "../model/schema-navigator";
import {
  factorGraphPointer,
  factorIndexAtPointer,
  type ExecutionPlansState,
} from "../model/use-execution-plans";
import { useRevealSelection } from "../model/use-reveal-selection";
import type { SourceTransactions } from "../model/use-source-transactions";
import { authoredFactors } from "../model/graph-transactions";
import { FactorGraphEditor } from "./factor-graph-editor";
import type { FormCatalogs } from "./strategy-form-panel";
import { GraphPlanTable } from "./graph-plan-table";
import "./factor-graph-panel.css";

/** 편집 입력(P5-02). 없으면 읽기 전용 투영만 그린다(테스트·backend plan 뷰어). */
export type FactorGraphEditing = {
  tree: unknown;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  /** 연산자 카탈로그(P1-03). 팔레트가 읽는다 — 없으면 노드 kind만 보인다. */
  operators?: OperatorCatalogState;
  /** Graph → Form 왕복(P5-03). */
  onOpenSource?: (pointer: string) => void;
  /** 문서 경계(`documentEpoch`). 바뀌면 "재계산 중"에 쓰는 직전 투영을 버린다(3차 P1). */
  documentKey?: unknown;
  sourceVersion?: number;
  active?: boolean;
};

type FactorGraphPanelProps = {
  state: ExecutionPlansState;
  /** runtime schema. 투영의 입력·설정 칸 이름(#354)과 편집기가 함께 읽는다. 없으면 메타데이터를 기다린다. */
  schema: JsonSchema | null;
  diagnostics: DocumentDiagnostic[];
  selectedPointer?: string;
  /**
   * 같은 문제 행을 다시 눌렀을 때도 선택 카드를 다시 끌어오게 하는 신호. pointer가 같아도 이 값이 바뀌면
   * `useRevealSelection`의 effect가 다시 돈다(2차 리뷰 R2-2).
   */
  revealSignal?: number;
  onSelectPointer: (pointer: string) => void;
  onOpenSource: (pointer: string) => void;
  editing?: FactorGraphEditing;
};

const graphDiagnostics = (diagnostics: DocumentDiagnostic[]) =>
  diagnostics.filter(
    (diagnostic) =>
      diagnostic.nodeId != null || diagnostic.pointer.includes("/graph"),
  );

const stateMessage = (
  state: Exclude<FactorGraphProjection, { status: "ready" | "error" }>,
): string => {
  if (state.status === "blocked") return t(`plan.blocked.${state.reason}`);
  if (state.status === "incompatible") {
    return t("plan.incompatible")
      .replace("{expected}", state.expected)
      .replace("{actual}", state.actual ?? "—");
  }
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
      {state.status === "error" ? (
        <FailureNotice message={t("plan.error")} reason={state.reason} />
      ) : (
        <p role="status">{stateMessage(state)}</p>
      )}
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
    {/* fingerprint는 `title`로 감추지 않고 본문으로 보인다 — hover 없는 입력에서도 읽히고 복사된다(P1-04). */}
    {factor.graphHash !== null ? (
      <div>
        <dt>{t("plan.graphFingerprint")}</dt>
        <dd>
          <code className="factor-graph__fingerprint">{factor.graphHash}</code>
        </dd>
      </div>
    ) : null}
    {factor.planHash !== null ? (
      <div>
        <dt>{t("plan.planFingerprint")}</dt>
        <dd>
          <code className="factor-graph__fingerprint">{factor.planHash}</code>
        </dd>
      </div>
    ) : null}
  </dl>
);

export const FactorGraphPanel = ({
  state,
  schema,
  diagnostics,
  selectedPointer,
  revealSignal,
  onSelectPointer,
  onOpenSource,
  editing,
}: FactorGraphPanelProps) => {
  const [chosenFactor, setChosenFactor] = useState(0);
  const container = useRevealSelection<HTMLElement>(
    selectedPointer,
    revealSignal,
  );
  const projected = projectFactorGraphs(state, schema);
  // 편집 확정 뒤 backend plan을 다시 받는 동안(loading) 직전 ready 투영을 "재계산 중" 배지와 함께 유지한다 —
  // DAG가 사라졌다 돌아오며 편집기가 점프하지 않도록(P5-02 acceptance, 리뷰 OBS-132-05). 렌더 중 파생 상태.
  const documentKey = editing?.documentKey;
  const [lastReady, setLastReady] = useState<{
    state: ExecutionPlansState;
    documentKey: unknown;
    projection: Extract<FactorGraphProjection, { status: "ready" }>;
  } | null>(null);
  if (projected.status === "ready" && lastReady?.state !== state)
    setLastReady({ state, documentKey, projection: projected });
  // 편집 확정 뒤 plan은 blocked(stale: 이전 compile의 spec이 남아 있어 stale 판정이 pending보다 먼저) →
  // blocked(pending) → loading → ready로 흐른다. 직전 투영은 같은 문서 안에서만 쓰고, 문서 경계(`documentKey`)가
  // 바뀌면 버린다(3차 리뷰 P1: blocked에서 버리면 편집 경로에서 기능이 사라진다; 4차: `stale`도 같은 구간이다).
  const held =
    lastReady !== null && Object.is(lastReady.documentKey, documentKey)
      ? lastReady
      : null;
  const recomputing =
    editing !== undefined &&
    (state.status === "loading" ||
      (state.status === "blocked" &&
        (state.reason === "stale" || state.reason === "pending"))) &&
    held !== null;
  const projection = recomputing ? held.projection : projected;
  const routeFactor = factorIndexAtPointer(selectedPointer);
  const authored = editing === undefined ? [] : authoredFactors(editing.tree);
  // 편집 표면은 backend plan이 없어도(빈 그래프·compile error·대기) 문서의 팩터로 그린다(Phase 4 감사 R4).
  const editor = (factorCount: number, factorSelect: boolean) => {
    if (editing === undefined || schema === null) return null;
    const index =
      routeFactor !== null && routeFactor < factorCount
        ? routeFactor
        : chosenFactor < factorCount
          ? chosenFactor
          : 0;
    return (
      <FactorGraphEditor
        tree={editing.tree}
        backendFactor={
          projected.status === "ready" &&
          !recomputing &&
          authored.filter((item) => item.factorId === authored[index]?.factorId)
            .length === 1
            ? projected.factors.find(
                (item) =>
                  item.factorIndex === index &&
                  item.factorId === authored[index]?.factorId,
              )
            : undefined
        }
        documentKey={editing.documentKey}
        sourceVersion={editing.sourceVersion}
        active={editing.active}
        schema={schema}
        transactions={editing.transactions}
        catalogs={editing.catalogs}
        diagnostics={diagnostics}
        operators={editing.operators}
        factorIndex={index}
        selectedPointer={selectedPointer}
        revealSignal={revealSignal}
        onSelectPointer={onSelectPointer}
        factorSelect={factorSelect}
        onOpenSource={editing.onOpenSource}
      />
    );
  };
  if (projection.status !== "ready" || projection.factors.length === 0) {
    return (
      <>
        <GraphState
          state={
            projection.status !== "ready" ? projection : { status: "empty" }
          }
          diagnostics={diagnostics}
          onOpenSource={onOpenSource}
        />
        {editor(authored.length, true)}
      </>
    );
  }

  const factorCount =
    editing === undefined ? projection.factors.length : authored.length;
  const activeIndex =
    routeFactor !== null && routeFactor < factorCount
      ? routeFactor
      : chosenFactor < factorCount
        ? chosenFactor
        : 0;
  const factor =
    editing === undefined
      ? projection.factors[activeIndex]
      : projection.factors.find(
          (item) => item.factorId === authored[activeIndex]?.factorId,
        );
  if (factor === undefined) return <>{editor(authored.length, true)}</>;
  const graphLevelIssues = factor.issues.filter(
    (issue) => issue.node_id === null,
  );

  return (
    <section
      ref={container}
      className="factor-graph"
      aria-label={t("graph.title")}
    >
      <header className="factor-graph__toolbar">
        <div>
          <strong>{t("graph.title")}</strong>
          <span>
            {editing === undefined
              ? t("graph.planOnly")
              : t("graph.planWithEdit")}
          </span>
          {recomputing ? (
            <Badge tone="warn">{t("graph.recomputing")}</Badge>
          ) : null}
        </div>
        <label>
          <span>{t("plan.factor")}</span>
          <select
            value={activeIndex}
            onChange={(event) => {
              const next = Number(event.target.value);
              setChosenFactor(next);
              onSelectPointer(factorGraphPointer(next));
            }}
          >
            {(editing === undefined ? projection.factors : authored).map(
              (item, index) => (
                <option value={index} key={`${item.factorId}:${index}`}>
                  {item.label
                    ? `${item.label} · ${item.factorId}`
                    : item.factorId}
                </option>
              ),
            )}
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

      {editing === undefined ? (
        <GraphPlanTable
          factor={factor}
          selectedPointer={selectedPointer}
          onSelectPointer={onSelectPointer}
          onOpenSource={onOpenSource}
        />
      ) : null}

      {editor(authored.length, false)}
    </section>
  );
};
