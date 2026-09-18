import { useState } from "react";

import { t } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectObjectSection } from "../model/form-projection";
import {
  addNode,
  authoredFactors,
  GRAPH_OWNER,
  nodeKinds,
  removeNode,
  selectedNodePointer,
} from "../model/graph-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import { factorGraphPointer } from "../model/use-execution-plans";
import type { SourceTransactions } from "../model/use-source-transactions";
import { FormFieldsEditor, type FormCatalogs } from "./strategy-form-panel";
import { TransactionFeedbackNote } from "./transaction-feedback";

const NO_FOCUS = { focusEditor: false } as const;

type FactorGraphEditorProps = {
  tree: unknown;
  schema: JsonSchema;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  diagnostics: DocumentDiagnostic[];
  factorIndex: number;
  selectedPointer?: string;
  onSelectPointer: (pointer: string) => void;
  /** plan projection이 없을 때는 팩터 선택도 편집기가 맡는다. */
  factorSelect: boolean;
};

/**
 * 팩터 그래프 편집 표면(WORKFLOW P5-02). 노드 목록·속성·그래프 설정은 parse tree에서 그리고(Form과 같은
 * 입력), 연산은 `graph-transactions.ts`(추가·삭제 가드)와 P4-02 필드 컨트롤(`FormFieldsEditor`: 속성·
 * 입력 재연결·출력·정책)이 만들어 `SourceTransactions.apply(op, label, "graph")`로 넘긴다. backend plan이
 * 없어도(빈 그래프·compile error) 편집할 수 있어야 P4-03이 만든 빈 팩터에 첫 노드를 넣는 경로가 열린다.
 */
export const FactorGraphEditor = ({
  tree,
  schema,
  transactions,
  catalogs,
  diagnostics,
  factorIndex,
  selectedPointer,
  onSelectPointer,
  factorSelect,
}: FactorGraphEditorProps) => {
  const factors = authoredFactors(tree);
  const factorPointer = `/factors/${factorIndex}`;
  const graphPointer = factorGraphPointer(factorIndex);
  const kinds = nodeKinds(schema, tree, factorPointer);
  const [kind, setKind] = useState<string>("");
  const chosenKind = kind || (kinds[0]?.[0] ?? "");
  // 삭제 거부 안내는 판정을 낸 tree에만 붙는다(P4-03 리뷰 P2-2와 같은 규칙).
  const [blocked, setBlocked] = useState<{
    tree: unknown;
    nodeId: string;
    by: string[];
  } | null>(null);
  const blockedNow = blocked !== null && blocked.tree === tree ? blocked : null;
  const disabled = transactions.disabled;
  const graphSection = projectObjectSection(
    schema,
    tree,
    diagnostics,
    graphPointer,
    "graph",
  );
  const nodes = graphSection?.lists.find((list) => list.key === "nodes");
  const nodePointer = selectedNodePointer(selectedPointer, factorPointer);
  const selectedItem =
    nodePointer === null
      ? undefined
      : nodes?.items.find((item) => item.pointer === nodePointer);
  const nodeSection =
    selectedItem === undefined
      ? null
      : projectObjectSection(
          schema,
          tree,
          diagnostics,
          selectedItem.pointer,
          selectedItem.summary,
        );

  const add = (): void => {
    const added = addNode(tree, factorPointer, chosenKind, schema);
    if ("error" in added) return;
    const nextIndex = nodes?.items.length ?? 0;
    if (transactions.apply(added.op, added.nodeId, GRAPH_OWNER, NO_FOCUS))
      onSelectPointer(`${graphPointer}/nodes/${nextIndex}`);
  };
  const remove = (nodeId: string): void => {
    const removal = removeNode(tree, factorPointer, nodeId);
    if ("error" in removal) {
      if (removal.error === "referenced")
        setBlocked({ tree, nodeId, by: removal.by });
      return;
    }
    const removedSelected =
      removal.kind === "remove" && removal.pointer === nodePointer;
    if (transactions.apply(removal, nodeId, GRAPH_OWNER, NO_FOCUS) && removedSelected)
      onSelectPointer(graphPointer);
  };

  if (factors.length === 0) {
    return (
      <section className="factor-graph__editor" aria-label={t("graph.editTitle")}>
        <p className="factor-graph__editor-state">{t("graph.noFactors")}</p>
      </section>
    );
  }

  return (
    <section className="factor-graph__editor" aria-label={t("graph.editTitle")}>
      <header className="factor-graph__editor-toolbar">
        <div>
          <strong>{t("graph.editTitle")}</strong>
          {disabled !== null ? (
            <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
          ) : (
            <Badge tone="ok">{t("graph.editable")}</Badge>
          )}
        </div>
        {factorSelect ? (
          <label>
            <span>{t("plan.factor")}</span>
            <select
              value={factorIndex}
              onChange={(event) =>
                onSelectPointer(factorGraphPointer(Number(event.target.value)))
              }
            >
              {factors.map((factor) => (
                <option value={factor.index} key={`${factor.factorId}:${factor.index}`}>
                  {factor.label ? `${factor.label} · ` : ""}
                  {factor.factorId}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </header>
      <TransactionFeedbackNote
        feedback={transactions.feedbackFor(GRAPH_OWNER)}
        owner={GRAPH_OWNER}
      />
      <fieldset className="factor-graph__editor-section" disabled={disabled !== null}>
        <legend>{t("graph.nodesTitle")}</legend>
        <div className="factor-graph__editor-add">
          {kinds.length > 1 ? (
            <select
              aria-label={t("graph.nodeKind")}
              value={chosenKind}
              onChange={(event) => setKind(event.target.value)}
            >
              {kinds.map(([name]) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : null}
          <Button
            size="small"
            onClick={add}
            disabled={chosenKind === ""}
            aria-label={t("graph.addNode")}
          >
            {t("graph.addNode")}
          </Button>
        </div>
        {nodes === undefined || nodes.items.length === 0 ? (
          <p className="factor-graph__editor-state">{t("graph.noNodes")}</p>
        ) : (
          <ul className="factor-graph__editor-nodes">
            {nodes.items.map((item) => {
              const kindField = item.fields.find((field) => field.key === "kind");
              const active = item.pointer === nodePointer;
              return (
                <li key={item.pointer} aria-current={active ? "true" : undefined}>
                  <button
                    type="button"
                    className="factor-graph__editor-node"
                    onClick={() => onSelectPointer(item.pointer)}
                    aria-label={t("graph.editNode").replace("{node}", item.summary)}
                  >
                    <strong>{item.summary}</strong>
                    {kindField !== undefined ? (
                      <code>{String(kindField.value ?? "")}</code>
                    ) : null}
                  </button>
                  <Button
                    size="small"
                    tone="danger"
                    onClick={() => remove(item.summary)}
                    aria-label={`${item.summary} · ${t("graph.removeNode")}`}
                  >
                    {t("graph.removeNode")}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
        {blockedNow !== null ? (
          <p className="strategy-form__invalid" role="alert">
            {t("graph.removeBlocked")
              .replace("{node}", blockedNow.nodeId)
              .replace("{pointers}", blockedNow.by.join(", "))}
          </p>
        ) : null}
      </fieldset>
      {graphSection !== null && graphSection.written ? (
        <fieldset className="factor-graph__editor-section" disabled={disabled !== null}>
          <legend>{t("graph.settingsTitle")}</legend>
          <FormFieldsEditor
            section={graphSection}
            transactions={transactions}
            catalogs={catalogs}
            owner={GRAPH_OWNER}
          />
        </fieldset>
      ) : null}
      <fieldset className="factor-graph__editor-section" disabled={disabled !== null}>
        <legend>
          {t("graph.selectedNode")}
          {selectedItem !== undefined ? <code>{selectedItem.summary}</code> : null}
        </legend>
        {nodeSection === null ? (
          <p className="factor-graph__editor-state">{t("graph.noSelection")}</p>
        ) : (
          <FormFieldsEditor
            section={nodeSection}
            transactions={transactions}
            catalogs={catalogs}
            owner={GRAPH_OWNER}
          />
        )}
      </fieldset>
    </section>
  );
};
