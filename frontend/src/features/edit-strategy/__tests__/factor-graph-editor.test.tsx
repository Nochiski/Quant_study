import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { JsonSchema } from "../model/schema-navigator";
import type { SourceTransactions } from "../model/use-source-transactions";
import { FactorGraphPanel } from "../ui/factor-graph-panel";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");
// 참조되지 않는 노드 `px`를 하나 더 둔 문서(삭제 가드 통과 경로).
const WITH_SPARE = VERBOSE.replace(
  "      output_node_id: mom_252\n",
  "        - kind: field\n          node_id: px\n          field_id: price.volume\n      output_node_id: mom_252\n",
);
// 같은 그래프에 `node_id: spare`가 둘(둘 다 미참조)이고 `node_id`가 없는 노드가 하나 더 있는 문서.
const WITH_DUPLICATE = VERBOSE.replace(
  "      output_node_id: mom_252\n",
  "        - kind: field\n          node_id: spare\n          field_id: price.volume\n        - kind: field\n          node_id: spare\n          field_id: price.open\n        - kind: field\n          field_id: price.high\n      output_node_id: mom_252\n",
);
// `kind`가 분기와 안 맞는 노드가 있는 문서.
const WITH_MYSTERY = VERBOSE.replace(
  "      output_node_id: mom_252\n",
  "        - node_id: mystery\n          operator: sum\n      output_node_id: mom_252\n",
);
// P4-03 "항목 추가"가 만드는 빈 팩터가 둘째로 있는 문서.
const WITH_EMPTY = VERBOSE.replace(
  "portfolio:\n",
  "  - factor_id: blank\n    direction: high\n    graph:\n      nodes: []\n      output_node_id: \"\"\nportfolio:\n",
);

const treeOf = (source: string): unknown => {
  const parsed = parseSource(source, "yaml");
  if (parsed.status !== "ok") throw new Error("fixture must parse");
  return parsed.tree;
};

const stub = (overrides: Partial<SourceTransactions> = {}): SourceTransactions => ({
  apply: vi.fn(() => true),
  run: vi.fn(() => true),
  feedback: { status: "idle" },
  feedbackFor: () => ({ status: "idle" }),
  onEditorReady: vi.fn(),
  enabled: true,
  disabled: null,
  settling: false,
  ...overrides,
});

const renderEditor = (
  source: string,
  transactions: SourceTransactions,
  selectedPointer?: string,
  onOpenForm?: (pointer: string) => void,
) => {
  const onSelectPointer = vi.fn();
  render(
    <FactorGraphPanel
      state={{ status: "blocked", reason: "invalid" }}
      diagnostics={[]}
      selectedPointer={selectedPointer}
      onSelectPointer={onSelectPointer}
      onOpenSource={vi.fn()}
      editing={{
        tree: treeOf(source),
        schema: SCHEMA,
        transactions,
        catalogs: { equityFields: null, factors: null },
        onOpenForm,
      }}
    />,
  );
  return onSelectPointer;
};

const editor = () => within(screen.getByRole("region", { name: "그래프 편집" }));

describe("FactorGraphEditor (P5-02)", () => {
  it("draws the authored nodes without a backend plan and adds a node through addNode (audit R4)", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    const onSelectPointer = renderEditor(WITH_EMPTY, transactions, "/factors/1/graph");
    // plan이 막혀 있어도 팩터 선택과 노드 목록은 문서에서 그린다.
    expect(screen.getByRole("status")).toBeInTheDocument();
    const factorSelect = editor().getByRole("combobox", { name: "팩터 그래프" });
    expect(factorSelect).toHaveValue("1");
    expect(editor().getByText("노드가 없습니다 — 노드 추가로 시작하세요")).toBeInTheDocument();
    await user.selectOptions(editor().getByRole("combobox", { name: "노드 종류" }), "field");
    await user.click(editor().getByRole("button", { name: "노드 추가" }));
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "insert-item",
        parentPointer: "/factors/1/graph/nodes",
        value: expect.objectContaining({ kind: "field", node_id: "field" }),
      },
      "field",
      "graph",
      { focusEditor: false },
    );
    // 추가 직후 새 노드 pointer를 선택한다(pointer 규약 R6).
    expect(onSelectPointer).toHaveBeenLastCalledWith("/factors/1/graph/nodes/0");
    // 팩터 선택은 그래프 pointer로 이동한다.
    await user.selectOptions(factorSelect, "0");
    expect(onSelectPointer).toHaveBeenLastCalledWith("/factors/0/graph");
  });

  it("edits the selected node with the form controls, scoping input candidates to the graph without self", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    renderEditor(WITH_SPARE, transactions, "/factors/0/graph/nodes/1");
    const selected = within(editor().getByRole("group", { name: /선택한 노드/ }));
    // `mom_252`(time_series)의 속성: window(number), input_node_id(reference → 같은 그래프의 다른 노드).
    const input = selected.getByRole("combobox", { name: /^input_node_id/ });
    const options = within(input)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toContain("close");
    expect(options).toContain("px");
    expect(options).not.toContain("mom_252");
    await user.selectOptions(input, "px");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "replace-scalar",
        pointer: "/factors/0/graph/nodes/1/input_node_id",
        value: "px",
      },
      "input_node_id",
      "graph",
      { focusEditor: false },
    );
    const window = selected.getByRole("spinbutton", { name: /^window/ });
    await user.clear(window);
    await user.type(window, "126{Enter}");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/factors/0/graph/nodes/1/window", value: 126 },
      "window",
      "graph",
      { focusEditor: false },
    );
    // 그래프 설정: output_node_id(reference)·missing_policy(enum).
    const settings = within(editor().getByRole("group", { name: "그래프 설정" }));
    await user.selectOptions(settings.getByRole("combobox", { name: /^output_node_id/ }), "px");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/factors/0/graph/output_node_id", value: "px" },
      "output_node_id",
      "graph",
      { focusEditor: false },
    );
    await user.selectOptions(settings.getByRole("combobox", { name: /^missing_policy/ }), "zero");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/factors/0/graph/missing_policy", value: "zero" },
      "missing_policy",
      "graph",
      { focusEditor: false },
    );
  });

  it("refuses to remove a referenced node with the referencing pointers, removes an unreferenced one, and re-selects the graph", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    const onSelectPointer = renderEditor(WITH_SPARE, transactions, "/factors/0/graph/nodes/2");
    await user.click(editor().getByRole("button", { name: "close · 삭제" }));
    expect(transactions.apply).not.toHaveBeenCalled();
    expect(editor().getByRole("alert")).toHaveTextContent(
      "/factors/0/graph/nodes/1/input_node_id",
    );
    await user.click(editor().getByRole("button", { name: "px · 삭제" }));
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "remove", pointer: "/factors/0/graph/nodes/2" },
      "px",
      "graph",
      { focusEditor: false },
    );
    // 선택돼 있던 노드를 지우면 그래프 pointer로 돌아간다.
    expect(onSelectPointer).toHaveBeenLastCalledWith("/factors/0/graph");
  });

  it("removes by pointer so duplicate or missing node ids never delete another node (DEFECT-132-01)", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    renderEditor(WITH_DUPLICATE, transactions, "/factors/0/graph");
    // 참조된 노드(close ← mom_252)는 거부하고 참조 pointer를 알려 준다.
    await user.click(editor().getByRole("button", { name: "close · 삭제" }));
    expect(transactions.apply).not.toHaveBeenCalled();
    expect(editor().getByRole("alert")).toHaveTextContent("/factors/0/graph/nodes/1/input_node_id");
    // 중복 표시 이름(spare ×2)은 문서 순번으로 구분되고, 누른 행의 pointer가 지워진다(id로 첫 노드를 찾지 않는다).
    await user.click(editor().getByRole("button", { name: "spare (4) · 삭제" }));
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "remove", pointer: "/factors/0/graph/nodes/3" },
      "spare (4)",
      "graph",
      { focusEditor: false },
    );
    // node_id가 없는 노드는 첫 문자열 값(field_id)로 표시되고 역시 pointer로 지운다.
    await user.click(editor().getByRole("button", { name: "price.high · 삭제" }));
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "remove", pointer: "/factors/0/graph/nodes/4" },
      "price.high",
      "graph",
      { focusEditor: false },
    );
  });

  it("offers a kind select for a node whose kind does not resolve (DEFECT-132-02)", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    renderEditor(WITH_MYSTERY, transactions, "/factors/0/graph/nodes/2");
    const selected = within(editor().getByRole("group", { name: /선택한 노드/ }));
    expect(selected.queryByText("노드를 선택하면 속성을 편집합니다")).toBeNull();
    await user.selectOptions(selected.getByRole("combobox", { name: "mystery · 종류" }), "unary");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "insert-key", parentPointer: "/factors/0/graph/nodes/2", key: "kind", value: "unary" },
      "kind",
      "graph",
      { focusEditor: false },
    );
  });

  it("opens the factor in the Form from the Graph editor (P5-03 round trip)", async () => {
    const user = userEvent.setup();
    const onOpenForm = vi.fn();
    renderEditor(WITH_SPARE, stub(), "/factors/0/graph", onOpenForm);
    await user.click(editor().getByRole("button", { name: "momentum · Form에서 열기" }));
    expect(onOpenForm).toHaveBeenCalledWith("/factors/0");
  });

  it("locks the editor with the hook's reason and shows only graph-owned feedback", () => {
    renderEditor(
      WITH_SPARE,
      stub({
        enabled: false,
        disabled: "syntax",
        feedbackFor: (owner) =>
          owner === "graph"
            ? { status: "applied", owner: "graph", label: "px" }
            : { status: "applied", owner: "form", label: "title" },
      }),
      "/factors/0/graph/nodes/2",
    );
    expect(editor().getByText("구문 오류 · source를 먼저 고치세요")).toBeInTheDocument();
    expect(editor().getByRole("button", { name: "노드 추가", hidden: true })).toBeDisabled();
    expect(editor().getByRole("status")).toHaveTextContent("px 반영됨");
    expect(editor().queryByText("title 반영됨")).toBeNull();
  });
});
