import { useState } from "react";

import { t, tName } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectObjectSection } from "../model/form-projection";
import {
  addNode,
  authoredFactors,
  GRAPH_OWNER,
  nodeKinds,
  removeNodeAt,
  renameNode,
  selectedNodePointer,
  setNodeField,
  type AddNodeFailure,
} from "../model/graph-transactions";
import {
  catalogNote,
  operatorPalette,
  type OperatorCatalogState,
  type PaletteEntry,
} from "../model/operator-palette";
import { schemaFacts, type JsonSchema } from "../model/schema-navigator";
import { factorGraphPointer } from "../model/use-execution-plans";
import { useRevealSelection } from "../model/use-reveal-selection";
import type { SourceTransactions } from "../model/use-source-transactions";
import {
  DiagnosticNotes,
  FormFieldsEditor,
  type CommitPlanner,
  type FormCatalogs,
} from "./strategy-form-panel";
import { OperatorPalette } from "./operator-palette";
import { TransactionFeedbackNote } from "./transaction-feedback";

const NO_FOCUS = { focusEditor: false } as const;

/** 삭제 거부 안내의 참조 pointer가 이 팩터의 몇 번째 노드를 가리키는가. 그래프 출력이면 매치가 없다. */
const NODE_INDEX = /\/graph\/nodes\/(\d+)(?:\/|$)/u;

type FactorGraphEditorProps = {
  tree: unknown;
  schema: JsonSchema;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  diagnostics: DocumentDiagnostic[];
  /** 연산자 카탈로그(P1-03). 팔레트가 읽는 유일한 연산자 목록이다. */
  operators?: OperatorCatalogState;
  factorIndex: number;
  selectedPointer?: string;
  /**
   * 같은 문제 행을 다시 눌렀을 때도 선택 카드를 다시 끌어오게 하는 신호. pointer가 같아도 이 값이 바뀌면
   * `useRevealSelection`의 effect가 다시 돈다(2차 리뷰 R2-2).
   */
  revealSignal?: number;
  onSelectPointer: (pointer: string) => void;
  /** plan projection이 없을 때는 팩터 선택도 편집기가 맡는다. */
  factorSelect: boolean;
  /** "Form에서 열기": 이 팩터의 Form 항목으로 간다(P5-03 왕복, pointer `/factors/N`). */
  onOpenForm?: (pointer: string) => void;
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
  operators = { status: "loading" },
  factorIndex,
  selectedPointer,
  revealSignal,
  onSelectPointer,
  factorSelect,
  onOpenForm,
}: FactorGraphEditorProps) => {
  const container = useRevealSelection<HTMLElement>(
    selectedPointer,
    revealSignal,
  );
  const factors = authoredFactors(tree);
  const activeFactorId = factors[factorIndex]?.factorId ?? `#${factorIndex + 1}`;
  const factorPointer = `/factors/${factorIndex}`;
  const graphPointer = factorGraphPointer(factorIndex);
  const kinds = nodeKinds(schema, tree, factorPointer);
  // 노드 종류 이름은 backend가 분기 스키마에 발행한 설명 키에서 온다(P1-03). frontend에 kind
  // 목록·이름을 손으로 적지 않는다.
  const kindNames = new Map(
    kinds.map(([kindKey, branch]) => [
      kindKey,
      tName(schemaFacts(branch).descriptionKey),
    ]),
  );
  // 팔레트 목록. kind 목록(`kinds`)은 노드 라벨과 팔레트의 입력으로만 남고 화면에서는 숨는다
  // (WORKFLOW P1-04: kind 드롭다운 대신 연산자를 먼저 고른다).
  const palette = operatorPalette(
    schema,
    tree,
    factorPointer,
    operators.status === "ready" ? operators.definitions : null,
  );
  // 삭제 거부 안내는 판정을 낸 tree에만 붙는다(P4-03 리뷰 P2-2와 같은 규칙). `by`가 null이면 문서에서 못 찾은 경우.
  const [blocked, setBlocked] = useState<{
    tree: unknown;
    label: string;
    by: string[] | null;
  } | null>(null);
  const blockedNow = blocked !== null && blocked.tree === tree ? blocked : null;
  // 노드 추가 실패도 같은 규칙으로 그 문서에만 붙인다(P1-04).
  const [addFailure, setAddFailure] = useState<{
    tree: unknown;
    entry: string;
    reason: AddNodeFailure;
  } | null>(null);
  const addFailureNow =
    addFailure !== null && addFailure.tree === tree ? addFailure : null;
  const disabled = transactions.disabled;
  const paletteDisabled =
    disabled !== null
      ? t("graph.palette.locked").replace(
          "{reason}",
          t(`form.disabled.${disabled}`),
        )
      : transactions.settling
        ? t("graph.palette.settling")
        : null;
  const graphSection = projectObjectSection(
    schema,
    tree,
    diagnostics,
    graphPointer,
    "graph",
  );
  const nodes = graphSection?.lists.find((list) => list.key === "nodes");
  // 표시 이름은 `node_id`(없으면 첫 문자열 값)라 겹칠 수 있다 → 겹치면 문서 순번을 붙여 접근성 이름을 유일하게
  // 한다(리뷰 DEFECT-132-01(b)). 연산은 언제나 pointer로 한다.
  const labelOf = (index: number): string | null => {
    const items = nodes?.items ?? [];
    const summary = items[index]?.summary;
    if (summary === undefined) return null;
    const duplicated = items.filter((item) => item.summary === summary).length > 1;
    return duplicated ? `${summary} (${index + 1})` : summary;
  };
  // 삭제 거부 안내는 JSON Pointer가 아니라 사람이 보는 노드 이름으로 말한다(P1-04). 그래프 자신의
  // `output_node_id`처럼 노드 밖 참조는 그 자리를 이름으로 부른다.
  const referenceLabel = (pointer: string): string => {
    const index = NODE_INDEX.exec(pointer)?.[1];
    if (index === undefined) return t("graph.outputReference");
    // 그린 목록 밖 인덱스면 이름이 없다. 안내 문장이 빈칸이 되지 않게 pointer 원문으로 떨어진다
    // (리뷰 P3: 조용한 실패를 없애는 PR에서 안내가 비는 것은 같은 계열의 퇴행이다).
    return labelOf(Number(index)) ?? pointer;
  };
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

  // `node_id` 확정은 rename이다(backlog 4): 중복·빈 값은 거부하고, 아니면 같은 그래프 안 참조까지 한 트랜잭션으로
  // 바꾼다. 다른 속성은 기본 필드 연산.
  const planNodeCommit: CommitPlanner = (field, value) => {
    if (selectedItem === undefined || field.key !== "node_id") return null;
    const renamed = renameNode(
      tree,
      factorPointer,
      selectedItem.pointer,
      typeof value === "string" ? value : String(value ?? ""),
    );
    if (!("error" in renamed)) return renamed;
    return {
      invalid:
        renamed.error === "duplicate"
          ? "duplicateNodeId"
          : renamed.error === "empty"
            ? "emptyNodeId"
            : "missingNode",
    };
  };

  // 팔레트에서 고른 항목으로 노드를 만든다: kind와 파라미터 기본값은 `addNode`가 스키마에서 채우고
  // 연산자는 고른 값 그대로다. 실패는 화면에 이유를 남긴다 — 조용히 아무 일도 안 하지 않는다(P1-04).
  const add = (entry: PaletteEntry): void => {
    const added = addNode(
      tree,
      factorPointer,
      entry.kind,
      schema,
      entry.operator,
    );
    if ("error" in added) {
      setAddFailure({ tree, entry: entry.name, reason: added.error });
      return;
    }
    setAddFailure(null);
    const nextIndex = nodes?.items.length ?? 0;
    if (transactions.apply(added.ops, added.nodeId, GRAPH_OWNER, NO_FOCUS))
      onSelectPointer(`${graphPointer}/nodes/${nextIndex}`);
  };
  // 삭제는 표시 이름이 아니라 pointer로 한다(리뷰 DEFECT-132-01: 중복·누락 `node_id`에서 다른 노드가 지워졌다).
  const remove = (target: string, label: string): void => {
    const removal = removeNodeAt(tree, factorPointer, target);
    if ("error" in removal) {
      setBlocked({
        tree,
        label,
        by: removal.error === "referenced" ? removal.by : null,
      });
      return;
    }
    const removedSelected = target === nodePointer;
    if (transactions.apply(removal, label, GRAPH_OWNER, NO_FOCUS) && removedSelected)
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
    <section
      ref={container}
      className="factor-graph__editor"
      aria-label={t("graph.editTitle")}
    >
      <header className="factor-graph__editor-toolbar">
        <div>
          <strong>{t("graph.editTitle")}</strong>
          {disabled !== null ? (
            <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
          ) : (
            <Badge tone="ok">{t("graph.editable")}</Badge>
          )}
          {onOpenForm !== undefined ? (
            <Button
              size="small"
              tone="ghost"
              onClick={() => onOpenForm(factorPointer)}
              aria-label={`${activeFactorId} · ${t("graph.openForm")}`}
            >
              {t("graph.openForm")}
            </Button>
          ) : null}
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
      {/* 추가·삭제는 위치 pointer 연산이라 직전 편집의 parse가 따라올 때까지 잠근다(P5-03 리뷰 DEFECT-133-01). */}
      <fieldset
        className="factor-graph__editor-section"
        disabled={disabled !== null || transactions.settling}
      >
        <legend>{t("graph.nodesTitle")}</legend>
        <OperatorPalette
          groups={palette}
          disabledReason={paletteDisabled}
          catalogNote={catalogNote(operators)}
          onPick={add}
        />
        {addFailureNow === null ? null : (
          <p className="strategy-form__invalid" role="alert">
            {t(`graph.addFailed.${addFailureNow.reason}`).replace(
              "{entry}",
              addFailureNow.entry,
            )}
          </p>
        )}
        {nodes === undefined || nodes.items.length === 0 ? (
          <p className="factor-graph__editor-state">{t("graph.noNodes")}</p>
        ) : (
          <ul className="factor-graph__editor-nodes">
            {nodes.items.map((item, index) => {
              const kindField = item.fields.find((field) => field.key === "kind");
              const active = item.pointer === nodePointer;
              const label = labelOf(index) ?? item.pointer;
              return (
                <li key={item.pointer} aria-current={active ? "true" : undefined}>
                  <button
                    type="button"
                    className="factor-graph__editor-node"
                    onClick={() => onSelectPointer(item.pointer)}
                    aria-label={t("graph.editNode").replace("{node}", label)}
                  >
                    <strong>{item.summary}</strong>
                    {kindField === undefined ? null : (
                      <>
                        {kindNames.get(String(kindField.value ?? "")) ===
                        undefined ? null : (
                          <span className="factor-graph__node-kind">
                            {kindNames.get(String(kindField.value ?? ""))}
                          </span>
                        )}
                        <code>{String(kindField.value ?? "")}</code>
                      </>
                    )}
                  </button>
                  <Button
                    size="small"
                    tone="danger"
                    onClick={() => remove(item.pointer, label)}
                    aria-label={`${label} · ${t("graph.removeNode")}`}
                  >
                    {t("graph.removeNode")}
                  </Button>
                  {/* 노드 진단은 노드 객체 pointer로 오므로 그 카드 안에 본문을 붙인다 — Form 목록
                      항목과 같은 모양이다(리뷰 차단 2). */}
                  <DiagnosticNotes
                    id={`${item.pointer}-notes`}
                    diagnostics={item.diagnostics}
                  />
                </li>
              );
            })}
          </ul>
        )}
        {blockedNow !== null ? (
          <p className="strategy-form__invalid" role="alert">
            {blockedNow.by === null
              ? t("graph.removeMissing").replace("{node}", blockedNow.label)
              : t("graph.removeBlocked")
                  .replace("{node}", blockedNow.label)
                  .replace(
                    "{nodes}",
                    [...new Set(blockedNow.by.map(referenceLabel))].join(", "),
                  )}
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
            selectedPointer={selectedPointer}
          />
        </fieldset>
      ) : null}
      <fieldset className="factor-graph__editor-section" disabled={disabled !== null}>
        <legend>
          {t("graph.selectedNode")}
          {selectedItem !== undefined ? <code>{selectedItem.summary}</code> : null}
        </legend>
        {selectedItem !== undefined && selectedItem.branches !== null ? (
          // `kind`가 없거나 분기와 안 맞는 노드(리뷰 DEFECT-132-02): P4-03 목록과 같은 kind 선택을 제시한다.
          <div className="factor-graph__editor-add">
            <span className="factor-graph__editor-state">
              {t("form.list.branchNeeded").replace(
                "{kinds}",
                selectedItem.branches.join(", "),
              )}
            </span>
            <select
              aria-label={`${selectedItem.summary} · ${t("form.list.kind")}`}
              value=""
              onChange={(event) => {
                if (event.target.value === "") return;
                transactions.apply(
                  setNodeField(tree, selectedItem.pointer, "kind", event.target.value),
                  "kind",
                  GRAPH_OWNER,
                  NO_FOCUS,
                );
              }}
            >
              <option value="">{t("form.list.kind")}</option>
              {selectedItem.branches.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        ) : nodeSection === null ? (
          <p className="factor-graph__editor-state">{t("graph.noSelection")}</p>
        ) : (
          <FormFieldsEditor
            section={nodeSection}
            transactions={transactions}
            catalogs={catalogs}
            owner={GRAPH_OWNER}
            planCommit={planNodeCommit}
            selectedPointer={selectedPointer}
          />
        )}
      </fieldset>
    </section>
  );
};
