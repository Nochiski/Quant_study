import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OperatorDefinition } from "../../../shared/api";
import { tOptional } from "../../../shared/config";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { OperatorCatalogState } from "../model/operator-palette";
import type { JsonSchema } from "../model/schema-navigator";
import type { SourceTransactions } from "../model/use-source-transactions";
import { FactorGraphPanel } from "../ui/factor-graph-panel";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const CATALOG: OperatorCatalogState = {
  status: "ready",
  definitions: (
    JSON.parse(
      readBackendFixture("strategy_documents/operator-catalog.json"),
    ) as { operators: OperatorDefinition[] }
  ).operators,
};
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");
// 자기 자신을 필수로 참조하는 노드 분기: `materializeSchemaValue`가 기본값을 만들지 못해
// `addNode`가 `unsupported-schema`로 거부하는 경로(P1-04 조용한 실패 제거).
const RECURSIVE_SCHEMA = {
  type: "object",
  properties: {
    factors: {
      type: "array",
      items: {
        type: "object",
        properties: {
          graph: {
            type: "object",
            properties: {
              nodes: { type: "array", items: { $ref: "#/$defs/Node" } },
            },
          },
        },
      },
    },
  },
  $defs: {
    Node: { oneOf: [{ $ref: "#/$defs/Loop" }] },
    Loop: {
      type: "object",
      "x-description-key": "strategy.node.constant",
      properties: {
        kind: { const: "constant" },
        node_id: { type: "string" },
        child: { $ref: "#/$defs/Loop" },
      },
      required: ["kind", "node_id", "child"],
    },
  },
} as unknown as JsonSchema;
const RECURSIVE_SOURCE = [
  "factors:",
  "  - factor_id: f",
  "    graph:",
  "      nodes: []",
  "",
].join("\n");
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
  operators: OperatorCatalogState = CATALOG,
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
        operators,
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
    // 연산자 먼저 고르기(P1-04): kind 드롭다운 없이 팔레트 항목을 누르면 kind가 따라온다.
    expect(editor().queryByRole("combobox", { name: "노드 종류" })).toBeNull();
    await user.click(editor().getByRole("button", { name: "데이터 필드 노드 추가" }));
    // 빈 그래프의 첫 노드는 출력 노드 지정과 한 트랜잭션이다(backlog 13).
    expect(transactions.apply).toHaveBeenLastCalledWith(
      [
        {
          kind: "insert-item",
          parentPointer: "/factors/1/graph/nodes",
          value: expect.objectContaining({ kind: "field", node_id: "field" }),
        },
        {
          kind: "replace-scalar",
          pointer: "/factors/1/graph/output_node_id",
          value: "field",
        },
      ],
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
    const input = selected.getByRole("combobox", { name: /\binput_node_id/ });
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
    const window = selected.getByRole("spinbutton", { name: /\bwindow/ });
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
    await user.selectOptions(settings.getByRole("combobox", { name: /\boutput_node_id/ }), "px");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/factors/0/graph/output_node_id", value: "px" },
      "output_node_id",
      "graph",
      { focusEditor: false },
    );
    await user.selectOptions(settings.getByRole("combobox", { name: /\bmissing_policy/ }), "zero");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/factors/0/graph/missing_policy", value: "zero" },
      "missing_policy",
      "graph",
      { focusEditor: false },
    );
  });

  it("renames a node together with its references and rejects a duplicate id (backlog 4)", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    renderEditor(WITH_SPARE, transactions, "/factors/0/graph/nodes/0");
    const selected = within(editor().getByRole("group", { name: /선택한 노드/ }));
    const nodeId = selected.getByRole("textbox", { name: /\bnode_id/ });
    await user.clear(nodeId);
    await user.type(nodeId, "px_close{Enter}");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      [
        { kind: "replace-scalar", pointer: "/factors/0/graph/nodes/0/node_id", value: "px_close" },
        { kind: "replace-scalar", pointer: "/factors/0/graph/nodes/1/input_node_id", value: "px_close" },
      ],
      "node_id",
      "graph",
      { focusEditor: false },
    );
    const calls = vi.mocked(transactions.apply).mock.calls.length;
    // 같은 그래프의 `px`로는 못 바꾼다: 적용 없이 사유를 안내한다.
    await user.clear(nodeId);
    await user.type(nodeId, "px{Enter}");
    expect(vi.mocked(transactions.apply).mock.calls).toHaveLength(calls);
    expect(selected.getByText("같은 그래프에 이미 있는 node_id입니다")).toBeInTheDocument();
    // 거부 뒤 다른 곳으로 포커스를 옮겨도(blur) 안내는 남고 적용은 없다(리뷰 P2-1).
    await user.tab();
    expect(vi.mocked(transactions.apply).mock.calls).toHaveLength(calls);
    expect(selected.getByText("같은 그래프에 이미 있는 node_id입니다")).toBeInTheDocument();
    // Escape로 되돌리면 값과 안내가 함께 원래대로(리뷰 P2-2).
    await user.click(nodeId);
    await user.keyboard("{Escape}");
    expect(nodeId).toHaveValue("close");
    expect(selected.queryByText("같은 그래프에 이미 있는 node_id입니다")).toBeNull();
  });

  it("refuses to remove a referenced node with the referencing pointers, removes an unreferenced one, and re-selects the graph", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    const onSelectPointer = renderEditor(WITH_SPARE, transactions, "/factors/0/graph/nodes/2");
    await user.click(editor().getByRole("button", { name: "close · 삭제" }));
    expect(transactions.apply).not.toHaveBeenCalled();
    // 거부 사유는 JSON Pointer가 아니라 노드 표시 이름이다(P1-04).
    expect(editor().getByRole("alert")).toHaveTextContent(
      "close을(를) 다른 곳이 참조하고 있어 삭제하지 않았습니다: mom_252",
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
    expect(editor().getByRole("alert")).toHaveTextContent(
      "close을(를) 다른 곳이 참조하고 있어 삭제하지 않았습니다: mom_252",
    );
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
    expect(
      editor().getByText(
        "노드를 추가할 수 없습니다: 구문 오류 · source를 먼저 고치세요",
      ),
    ).toBeInTheDocument();
    const locked = editor().getByRole("button", {
      name: "데이터 필드 노드 추가",
      hidden: true,
    });
    expect(locked).toBeDisabled();
    // 비활성 버튼이 이유를 가리킨다(P1-04 acceptance).
    expect(locked.getAttribute("aria-describedby")).toContain(
      editor()
        .getByText("노드를 추가할 수 없습니다: 구문 오류 · source를 먼저 고치세요")
        .id,
    );
    expect(editor().getByRole("status")).toHaveTextContent("px 반영됨");
    expect(editor().queryByText("title 반영됨")).toBeNull();
  });
});

describe("연산자 팔레트와 조용하지 않은 실패 (P1-04)", () => {
  it("연산자를 고르면 kind와 파라미터 기본값이 따라온다", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    const onSelectPointer = renderEditor(
      WITH_EMPTY,
      transactions,
      "/factors/1/graph",
    );
    const palette = within(
      editor().getByRole("group", { name: "연산자 팔레트" }),
    );
    // 항목은 이름·한 줄 설명·계산식을 함께 보인다(본문, `title` 아님). 계산식 문구는 i18n이
    // 소유하므로 여기 적지 않고 사전에서 읽는다. 계산식은 버튼 안이 아니라 본문에 있어야
    // 스크린리더에도 읽힌다 — 버튼 이름은 `aria-label`이 정하므로 그 안의 `<code>`는 지워진다
    // (리뷰 P3).
    const meanFormula = tOptional("strategy.operator.time_series.mean.formula");
    expect(meanFormula).not.toBeNull();
    expect(palette.getByText(meanFormula!)).toBeInTheDocument();
    expect(
      palette.getByRole("button", { name: "기간 평균 노드 추가" }),
    ).toHaveAccessibleDescription(expect.stringContaining(meanFormula!));
    await user.click(palette.getByRole("button", { name: "기간 평균 노드 추가" }));

    expect(transactions.apply).toHaveBeenLastCalledWith(
      [
        {
          kind: "insert-item",
          parentPointer: "/factors/1/graph/nodes",
          value: expect.objectContaining({
            kind: "time_series",
            node_id: "mean",
            operator: "mean",
            // 파라미터 기본값은 runtime schema가 채운다(`window` 필수, `lag` 기본값).
            window: expect.any(Number),
            lag: 0,
          }),
        },
        {
          kind: "replace-scalar",
          pointer: "/factors/1/graph/output_node_id",
          value: "mean",
        },
      ],
      "mean",
      "graph",
      { focusEditor: false },
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith("/factors/1/graph/nodes/0");
  });

  it("검색이 목록을 좁히고 맞는 항목이 없으면 그렇게 말한다", async () => {
    const user = userEvent.setup();
    renderEditor(WITH_EMPTY, stub(), "/factors/1/graph");
    const palette = within(
      editor().getByRole("group", { name: "연산자 팔레트" }),
    );
    await user.type(palette.getByRole("searchbox", { name: "연산자 검색" }), "zscore");

    expect(palette.getByRole("button", { name: "표준화 노드 추가" })).toBeInTheDocument();
    expect(palette.queryByRole("button", { name: "기간 평균 노드 추가" })).toBeNull();

    await user.clear(palette.getByRole("searchbox", { name: "연산자 검색" }));
    await user.type(palette.getByRole("searchbox", { name: "연산자 검색" }), "없는연산자");
    expect(palette.getByText("검색어와 맞는 연산자가 없습니다")).toBeInTheDocument();
  });

  it("카탈로그를 못 받으면 노드 kind만 보인다고 말한다(팔레트를 비우지 않는다)", () => {
    renderEditor(WITH_EMPTY, stub(), "/factors/1/graph", undefined, {
      status: "unavailable",
    });
    const palette = within(
      editor().getByRole("group", { name: "연산자 팔레트" }),
    );

    expect(
      palette.getByText(
        "연산자 목록을 불러오지 못했습니다 — 지금은 노드 종류만 보이고, 연산자는 노드 속성에서 고르세요",
      ),
    ).toBeInTheDocument();
    const add = palette.getByRole("button", { name: "기간 집계 노드 추가" });
    expect(add).toBeEnabled();
    expect(add.getAttribute("aria-describedby")).toContain(
      palette.getByText(
        "연산자 목록을 불러오지 못했습니다 — 지금은 노드 종류만 보이고, 연산자는 노드 속성에서 고르세요",
      ).id,
    );
  });

  it("노드를 만들지 못하면 조용히 넘어가지 않고 이유를 보인다", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    render(
      <FactorGraphPanel
        state={{ status: "blocked", reason: "invalid" }}
        diagnostics={[]}
        onSelectPointer={vi.fn()}
        onOpenSource={vi.fn()}
        editing={{
          tree: treeOf(RECURSIVE_SOURCE),
          schema: RECURSIVE_SCHEMA,
          transactions,
          catalogs: { equityFields: null, factors: null },
          operators: { status: "ready", definitions: [] },
        }}
      />,
    );
    await user.click(editor().getByRole("button", { name: "상수 노드 추가" }));

    expect(transactions.apply).not.toHaveBeenCalled();
    expect(editor().getByRole("alert")).toHaveTextContent(
      "상수: 이 노드의 스키마로는 기본값을 만들지 못해 추가하지 않았습니다",
    );
  });
});

describe("노드 pointer 진단이 붙는 자리 (P1-04 리뷰 차단 2)", () => {
  /** backend가 실제로 내는 모양: 노드 **객체** pointer + `factor.graph.*` 코드. */
  const NODE_DIAGNOSTIC = {
    code: "factor.graph.time_series_window",
    kind: "semantic" as const,
    severity: "error" as const,
    pointer: "/factors/0/graph/nodes/1",
    message: "window는 1 이상이고 lag는 0 이상이어야 합니다: window=0 lag=0",
    range: null,
    nodeId: "mom_252",
  };

  it("노드 카드 한 곳에만 본문이 붙고 DOM id가 겹치지 않는다", () => {
    render(
      <FactorGraphPanel
        state={{ status: "blocked", reason: "invalid" }}
        diagnostics={[NODE_DIAGNOSTIC]}
        selectedPointer="/factors/0/graph/nodes/1"
        onSelectPointer={vi.fn()}
        onOpenSource={vi.fn()}
        editing={{
          tree: treeOf(VERBOSE),
          schema: SCHEMA,
          transactions: stub(),
          catalogs: { equityFields: null, factors: null },
          operators: CATALOG,
        }}
      />,
    );

    const row = editor()
      .getByRole("button", { name: "노드 편집: mom_252" })
      .closest("li");
    expect(row).toHaveTextContent(NODE_DIAGNOSTIC.message);
    // 선택한 노드 패널은 같은 문장을 다시 그리지 않는다 — 정보가 늘지 않는 사본이다(2차 리뷰 P3).
    expect(
      editor().getByRole("group", { name: /선택한 노드/ }),
    ).not.toHaveTextContent(NODE_DIAGNOSTIC.message);
    // 문장은 편집기 안에서 한 번만 나온다.
    expect(editor().getAllByText(NODE_DIAGNOSTIC.message)).toHaveLength(1);
    // 본문이지 assertive 알림이 아니다(리뷰 P3).
    expect(editor().queryAllByRole("alert")).toEqual([]);
    // 같은 pointer를 그리는 두 자리가 같은 id를 내던 회귀(2차 리뷰 P3).
    const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
    expect(ids.length).toBe(new Set(ids).size);
  });
});
