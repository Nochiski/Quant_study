import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FactorDefinition } from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { buildCanonicalSnippetCatalog } from "../model/canonical-snippets";
import { findReferences } from "../model/document-references";
import {
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { projectForm } from "../model/form-projection";
import {
  addItemOperation,
  itemKinds,
  removalBlockers,
  type ListSection,
} from "../model/form-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import type { SourceTransactions } from "../model/use-source-transactions";
import { StrategyFormPanel } from "../ui/strategy-form-panel";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");

const FACTOR: FactorDefinition = {
  availability: "implemented",
  category: "price",
  default_graph: {
    nodes: [{ kind: "field", node_id: "px", field_id: "price.close" }],
    output_node_id: "px",
    missing_policy: "drop",
  },
  description: "Server factor",
  factor_id: "server.momentum",
  label: "Server momentum",
  minimum_history_sessions: 1,
  missing_policy: "drop",
  output_unit: "score",
  preference: "high",
  required_field_ids: ["price.close"],
};

const parsedState = (source: string): DocumentState => {
  const base = initialDocumentState("yaml", source);
  return {
    ...base,
    parse: parseSource(source, "yaml"),
    parsedVersion: base.sourceVersion,
  };
};

const listSection = (source: string, key: string): ListSection => {
  const found = projectForm(
    SCHEMA,
    parseSource(source, "yaml"),
    [],
  ).sections.find((s) => s.key === key);
  if (found === undefined || found.kind !== "list") throw new Error(key);
  return found;
};

const stub = (): SourceTransactions => ({
  apply: vi.fn(),
  run: vi.fn(),
  feedback: { status: "idle" },
  onEditorReady: vi.fn(),
  enabled: true,
  disabled: null,
});

describe("list transactions (P4-03)", () => {
  it("materializes a minimal item from the schema, choosing the union branch by kind", () => {
    const parameters = listSection(VERBOSE, "parameters");
    expect(itemKinds(SCHEMA, parameters)).toEqual([
      "float",
      "integer",
      "choice",
    ]);
    expect(addItemOperation(SCHEMA, parameters, "integer")).toEqual({
      kind: "insert-item",
      parentPointer: "/parameters",
      value: {
        kind: "integer",
        parameter_id: "",
        default: 0,
        minimum: 0,
        maximum: 0,
        step: 1,
      },
    });
    expect(addItemOperation(SCHEMA, parameters, null)).toBeNull();
    const factors = listSection(VERBOSE, "factors");
    expect(itemKinds(SCHEMA, factors)).toBeNull();
    const added = addItemOperation(SCHEMA, factors);
    expect(added).toMatchObject({
      kind: "insert-item",
      parentPointer: "/factors",
      value: {
        factor_id: "",
        direction: "high",
        weight: 1,
        graph: { nodes: [], output_node_id: "" },
      },
    });
  });

  it("finds references by `<namespace>_id` outside the defining item", () => {
    const tree = parseSource(
      `${VERBOSE}`.replace(
        "      output_node_id: mom_252\n",
        "      output_node_id: mom_252\n        # ref\n",
      ),
      "yaml",
    );
    if (tree.status !== "ok") throw new Error("fixture");
    const doc = {
      ...tree.tree,
      factors: [
        ...(tree.tree.factors as unknown[]),
        {
          factor_id: "blend",
          direction: "high",
          graph: {
            nodes: [
              { kind: "saved_factor", node_id: "m", factor_id: "momentum" },
            ],
            output_node_id: "m",
          },
        },
      ],
    };
    expect(findReferences(doc, "factor", "momentum", "/factors/0")).toEqual([
      { pointer: "/factors/1/graph/nodes/0/factor_id" },
    ]);
    expect(findReferences(doc, "factor", "blend", "/factors/1")).toEqual([]);
    expect(
      findReferences(doc, "node", "close", "/factors/0/graph/nodes/0"),
    ).toEqual([{ pointer: "/factors/0/graph/nodes/1/input_node_id" }]);
    const projected = projectForm(
      SCHEMA,
      { ...tree, tree: doc },
      [],
    ).sections.find((s) => s.key === "factors");
    if (projected === undefined || projected.kind !== "list")
      throw new Error("factors");
    expect(removalBlockers(doc, projected.items[0]!)).toHaveLength(1);
    expect(removalBlockers(doc, projected.items[1]!)).toHaveLength(0);
  });
});

describe("StrategyFormPanel list sections", () => {
  const renderList = (
    source: string,
    transactions: SourceTransactions,
    onOpenGraph = vi.fn(),
  ) => {
    const state = parsedState(source);
    render(
      <StrategyFormPanel
        projection={projectForm(SCHEMA, state.parse, [])}
        schema={SCHEMA}
        tree={
          state.parse !== null && state.parse.status === "ok"
            ? state.parse.tree
            : {}
        }
        transactions={transactions}
        catalogs={{ equityFields: null, factors: [FACTOR] }}
        catalogSnippets={buildCanonicalSnippetCatalog({
          schema: SCHEMA,
          factors: [FACTOR],
          status: "ready",
        })}
        onOpenGraph={onOpenGraph}
      />,
    );
    return onOpenGraph;
  };

  it("adds items from the schema and from the factor catalog, and removes unreferenced items", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    const onOpenGraph = renderList(VERBOSE, transactions);
    const factors = within(screen.getByRole("group", { name: /^factors/ }));
    await user.selectOptions(
      factors.getByRole("combobox", { name: "factors · 카탈로그에서 추가" }),
      "factor:server.momentum",
    );
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "insert-item",
        parentPointer: "/factors",
        value: expect.objectContaining({ factor_id: "server.momentum" }),
      },
      "Server momentum",
      "form",
      { focusEditor: false },
    );
    await user.click(
      factors.getByRole("button", { name: "momentum · Graph에서 열기" }),
    );
    expect(onOpenGraph).toHaveBeenCalledWith("/factors/0/graph");
    await user.click(factors.getByRole("button", { name: "momentum · 삭제" }));
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "remove", pointer: "/factors/0" },
      "momentum",
      "form",
      { focusEditor: false },
    );
    // 항목 필드는 P4-02 컨트롤을 재사용한다(항목 pointer 아래 replace-scalar).
    await user.selectOptions(
      factors.getByRole("combobox", { name: /^direction/ }),
      "low",
    );
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/factors/0/direction", value: "low" },
      "direction",
      "form",
      { focusEditor: false },
    );
    const parameters = within(
      screen.getByRole("group", { name: /^parameters/ }),
    );
    await user.selectOptions(
      parameters.getByRole("combobox", { name: "parameters · 종류" }),
      "choice",
    );
    await user.click(
      parameters.getByRole("button", { name: "parameters · 항목 추가" }),
    );
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "insert-item",
        parentPointer: "/parameters",
        value: expect.objectContaining({ kind: "choice" }),
      },
      "parameters",
      "form",
      { focusEditor: false },
    );
  });

  it("shows the x-default-from sibling value as the placeholder of an omitted item field", () => {
    // factor `label`은 생략하면 backend가 `factor_id`로 채운다 → placeholder도 그 값(P4-02 리뷰 013).
    const transactions = stub();
    renderList(VERBOSE.replace('    label: "모멘텀"\n', ""), transactions);
    const factors = within(screen.getByRole("group", { name: /^factors/ }));
    const label = factors.getAllByRole("textbox", { name: /^label/ })[0]!;
    expect(label).toHaveValue("");
    expect(label).toHaveAttribute("placeholder", "momentum");
    expect(
      factors.getByText("생략하면 factor_id 값(momentum)을 씁니다"),
    ).toBeInTheDocument();
  });

  it("refuses to remove an item that another node references and lists the references", async () => {
    const user = userEvent.setup();
    const transactions = stub();
    const source = VERBOSE.replace(
      "portfolio:\n",
      "  - factor_id: blend\n    direction: high\n    graph:\n      nodes:\n        - kind: saved_factor\n          node_id: m\n          factor_id: momentum\n      output_node_id: m\nportfolio:\n",
    );
    renderList(source, transactions);
    const factors = within(screen.getByRole("group", { name: /^factors/ }));
    await user.click(factors.getByRole("button", { name: "momentum · 삭제" }));
    expect(transactions.apply).not.toHaveBeenCalled();
    expect(factors.getByRole("alert")).toHaveTextContent(
      "/factors/1/graph/nodes/0/factor_id",
    );
    await user.click(factors.getByRole("button", { name: "blend · 삭제" }));
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "remove", pointer: "/factors/1" },
      "blend",
      "form",
      { focusEditor: false },
    );
  });
});
