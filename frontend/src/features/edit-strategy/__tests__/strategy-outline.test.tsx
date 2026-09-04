import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import type { JsonSchema } from "../model/schema-navigator";
import {
  findOutlineNode,
  projectStrategyOutline,
} from "../model/strategy-outline";
import { useStrategyOutline } from "../model/use-strategy-outline";
import { useOutlineNavigation } from "../model/use-outline-navigation";
import { StrategyOutline } from "../ui/strategy-outline";

afterEach(cleanup);

const objectSection = { type: "object", properties: {} };
const SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    schema_version: { type: "string" },
    title: { type: "string" },
    description: { type: "string" },
    data: {
      type: "object",
      properties: { market: { type: "string" } },
    },
    eligibility: {
      type: "object",
      properties: {
        rules: {
          type: "array",
          items: {
            type: "object",
            properties: { field_id: { type: "string" } },
          },
        },
      },
    },
    factors: {
      type: "object",
      properties: {
        factors: {
          type: "array",
          items: {
            type: "object",
            properties: {
              factor_id: { type: "string" },
              graph: {
                type: "object",
                properties: {
                  nodes: {
                    type: "array",
                    "x-defines": "node",
                    items: {
                      type: "object",
                      properties: {
                        node_id: { type: "string" },
                        field_id: { type: "string" },
                        kind: { type: "string" },
                      },
                    },
                  },
                },
              },
            },
          },
        },
      },
    },
    signal: objectSection,
    portfolio: objectSection,
    risk: {
      type: "object",
      properties: { max_name_weight: { type: "number" } },
    },
    execution: objectSection,
    parameters: {
      type: "array",
      "x-defines": "parameter",
      items: { type: "object", properties: {} },
    },
  },
};

const SOURCE = [
  'schema_version: "1.0"',
  "title: momentum",
  'description: ""',
  "data:",
  "  market: KRX",
  "eligibility:",
  "  rules:",
  "    - field_id: price.close",
  "factors:",
  "  factors:",
  "    - factor_id: momentum",
  "      graph:",
  "        nodes:",
  "          - field_id: price.close",
  "            node_id: close",
  "            kind: field",
  "signal: {}",
  "portfolio: {}",
  "risk:",
  "  max_name_weight: 0.05",
  "execution: {}",
  "parameters: []",
  "",
].join("\n");

const parsed = () => {
  const result = parseSource(SOURCE, "yaml");
  if (result.status !== "ok") throw new Error("outline fixture must parse");
  return result;
};

const parsedState = (): DocumentState => {
  const loaded = documentReducer(initialDocumentState(), {
    type: "load",
    format: "yaml",
    source: SOURCE,
    strategyId: "s1",
    baseRevision: 1,
    baseSpecHash: "a".repeat(64),
  });
  return documentReducer(loaded, {
    type: "parsed",
    version: loaded.sourceVersion,
    result: parsed(),
  });
};

describe("Strategy Outline projection", () => {
  it("derives root sections and uses only schema-owned semantic identities", () => {
    const result = parsed();
    const nodes = projectStrategyOutline(result, SCHEMA);
    expect(nodes.map((node) => node.label)).toEqual([
      "identity",
      "data",
      "eligibility",
      "factors",
      "signal",
      "portfolio",
      "risk",
      "execution",
      "parameters",
    ]);
    const factor = findOutlineNode(nodes, "/factors/factors/0");
    expect(factor).toMatchObject({
      arrayIndex: 0,
      semanticIdentity: null,
    });
    const node = findOutlineNode(nodes, "/factors/factors/0/graph/nodes/0");
    expect(node).toMatchObject({
      id: "/factors/factors/0/graph/nodes/0",
      pointer: "/factors/factors/0/graph/nodes/0",
      arrayIndex: 0,
      semanticIdentity: { namespace: "node", value: "close" },
    });
    expect(findOutlineNode(nodes, "/eligibility/rules/0")).toMatchObject({
      arrayIndex: 0,
      semanticIdentity: null,
    });
  });

  it("retains only the same document epoch's last valid tree during a parse error", () => {
    const valid = parsedState();
    const { result, rerender } = renderHook(
      ({ state }: { state: DocumentState }) =>
        useStrategyOutline(state, SCHEMA),
      { initialProps: { state: valid } },
    );
    expect(result.current?.stale).toBe(false);

    const edited = documentReducer(valid, {
      type: "edit",
      source: `${SOURCE}broken: [\n`,
    });
    const rejected = documentReducer(edited, {
      type: "parsed",
      version: edited.sourceVersion,
      result: parseSource(edited.source, "yaml"),
    });
    rerender({ state: rejected });
    expect(result.current?.stale).toBe(true);
    expect(
      findOutlineNode(result.current?.nodes ?? [], "/risk"),
    ).not.toBeNull();

    const anotherDocument = documentReducer(rejected, {
      type: "load",
      format: "yaml",
      source: "title: next\n",
      strategyId: "s2",
      baseRevision: 1,
      baseSpecHash: "b".repeat(64),
    });
    rerender({ state: anotherDocument });
    expect(result.current).toBeNull();
  });

  it("does not publish implicit-root or programmatic collection selections back to the URL", () => {
    const onSelectedPointer = vi.fn();
    let editorSource = SOURCE;
    const { result, rerender } = renderHook(
      ({ state }: { state: DocumentState }) =>
        useOutlineNavigation({
          state,
          schema: SCHEMA,
          selectedPointer: undefined,
          onSelectedPointer,
        }),
      { initialProps: { state: parsedState() } },
    );
    const editor: CodeEditorHandle = {
      getText: () => editorSource,
      setText: vi.fn(),
      replaceRange: vi.fn(),
      getSelection: () => ({ from: 0, to: 0 }),
      setSelection: (from, to = from) =>
        result.current.onEditorSelectionChange({
          from,
          to,
          documentChanged: false,
        }),
      offsetToPosition: () => ({ line: 0, column: 0 }),
      positionToOffset: () => 0,
      scrollTo: vi.fn(),
      focus: vi.fn(),
      getHistoryState: () => null,
      restoreHistoryState: vi.fn(),
    };

    act(() => result.current.onEditorReady(editor));
    expect(onSelectedPointer).not.toHaveBeenCalled();

    const risk = findOutlineNode(result.current.snapshot?.nodes ?? [], "/risk");
    expect(risk).not.toBeNull();
    act(() => result.current.onSelectOutlineNode(risk!));
    expect(onSelectedPointer.mock.calls).toEqual([["/risk", "outline"]]);

    const basics = findOutlineNode(result.current.snapshot?.nodes ?? [], "");
    expect(basics).not.toBeNull();
    act(() => result.current.onSelectOutlineNode(basics!));
    expect(onSelectedPointer).toHaveBeenLastCalledWith(undefined, "outline");

    editorSource = 'schema_version: "1.0"\ntitle: minimal\n';
    const loaded = documentReducer(parsedState(), {
      type: "load",
      format: "yaml",
      source: editorSource,
      strategyId: "s2",
      baseRevision: 1,
      baseSpecHash: "b".repeat(64),
    });
    const missingState = documentReducer(loaded, {
      type: "parsed",
      version: loaded.sourceVersion,
      result: parseSource(editorSource, "yaml"),
    });
    rerender({ state: missingState });
    const missingRisk = findOutlineNode(
      result.current.snapshot?.nodes ?? [],
      "/risk",
    );
    expect(missingRisk).toMatchObject({ present: false, range: null });
    act(() => result.current.onSelectOutlineNode(missingRisk!));
    expect(onSelectedPointer).toHaveBeenLastCalledWith("/risk", "outline");
    expect(onSelectedPointer).toHaveBeenCalledTimes(3);
  });

  it("lets an explicit route selection win and rejects skipped-version cursor offsets", () => {
    const first = parsedState();
    const secondSource = `${SOURCE}extra_one: true\n`;
    const edited = documentReducer(first, {
      type: "edit",
      source: secondSource,
    });
    const second = documentReducer(edited, {
      type: "parsed",
      version: edited.sourceVersion,
      result: parseSource(secondSource, "yaml"),
    });
    const onDirectRoute = vi.fn();
    const direct = renderHook(
      ({
        state,
        selectedPointer,
      }: {
        state: DocumentState;
        selectedPointer: string | undefined;
      }) =>
        useOutlineNavigation({
          state,
          schema: SCHEMA,
          selectedPointer,
          onSelectedPointer: onDirectRoute,
        }),
      { initialProps: { state: first, selectedPointer: "/title" } },
    );
    act(() =>
      direct.result.current.onEditorSelectionChange({
        from: secondSource.indexOf("extra_one"),
        to: secondSource.indexOf("extra_one"),
        documentChanged: true,
      }),
    );
    direct.rerender({ state: second, selectedPointer: "/risk" });
    expect(onDirectRoute).not.toHaveBeenCalled();
    direct.unmount();

    const thirdSource = `${secondSource}extra_two: true\n`;
    const editedAgain = documentReducer(edited, {
      type: "edit",
      source: thirdSource,
    });
    const third = documentReducer(editedAgain, {
      type: "parsed",
      version: editedAgain.sourceVersion,
      result: parseSource(thirdSource, "yaml"),
    });
    const onSkippedVersion = vi.fn();
    const skipped = renderHook(
      ({ state }: { state: DocumentState }) =>
        useOutlineNavigation({
          state,
          schema: SCHEMA,
          selectedPointer: "/title",
          onSelectedPointer: onSkippedVersion,
        }),
      { initialProps: { state: first } },
    );
    act(() =>
      skipped.result.current.onEditorSelectionChange({
        from: secondSource.indexOf("extra_one"),
        to: secondSource.indexOf("extra_one"),
        documentChanged: true,
      }),
    );
    skipped.rerender({ state: third });
    expect(onSkippedVersion).not.toHaveBeenCalled();
  });
});

describe("Strategy Outline tree", () => {
  const snapshot = () => {
    const result = parsed();
    return {
      documentEpoch: 1,
      sourceVersion: 1,
      parsed: result,
      nodes: projectStrategyOutline(result, SCHEMA),
      stale: false,
      staleReason: null,
    } as const;
  };

  it("selects by JSON Pointer and supports ARIA tree keyboard navigation", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onCollapse = vi.fn();
    const view = render(
      <StrategyOutline
        snapshot={snapshot()}
        selectedPointer="/risk"
        onSelect={onSelect}
        onCollapse={onCollapse}
      />,
    );
    const tree = screen.getByRole("tree", {
      name: "StrategySpec 문서 구조",
    });
    const risk = within(tree).getByRole("treeitem", {
      name: "risk",
      selected: true,
    });
    expect(risk).toHaveAttribute("aria-expanded", "true");
    risk.focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toHaveAccessibleName("max_name_weight");
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(risk);
    await user.keyboard("{ArrowLeft}");
    expect(risk).toHaveAttribute("aria-expanded", "false");
    expect(onCollapse).toHaveBeenLastCalledWith(
      expect.objectContaining({ pointer: "/risk" }),
    );
    expect(document.activeElement).toBe(risk);

    view.rerender(
      <StrategyOutline
        snapshot={snapshot()}
        selectedPointer="/risk/max_name_weight"
        onSelect={onSelect}
        onCollapse={onCollapse}
      />,
    );
    expect(risk).toHaveAttribute("aria-expanded", "true");
    expect(
      within(tree).getByRole("treeitem", {
        name: "max_name_weight",
        selected: true,
      }),
    ).toBeVisible();
    await user.click(
      risk.querySelector<HTMLElement>('[data-disclosure="true"]')!,
    );
    expect(onCollapse).toHaveBeenLastCalledWith(
      expect.objectContaining({ pointer: "/risk" }),
    );
    expect(document.activeElement).toBe(risk);
    const riskValue = within(tree).getByRole("treeitem", {
      name: "max_name_weight",
    });
    expect(riskValue.parentElement).toHaveAttribute("role", "group");
    expect(riskValue.parentElement?.parentElement).toBe(risk);

    risk.focus();
    await user.keyboard("{End}");
    expect(document.activeElement).toHaveAccessibleName("parameters");
    await user.keyboard("{Home}");
    expect(document.activeElement).toHaveAccessibleName("기본 정보");
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith(
      expect.objectContaining({ pointer: "" }),
    );
  });

  it("shows technical index and semantic node identity as distinct labels and filters ancestors", async () => {
    const user = userEvent.setup();
    const view = render(
      <StrategyOutline
        snapshot={snapshot()}
        selectedPointer="/factors/factors/0/graph/nodes/0"
        onSelect={vi.fn()}
        onCollapse={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("treeitem", {
        name: "배열 인덱스 0, node_id close",
        selected: true,
      }),
    ).toHaveAttribute("title", "/factors/factors/0/graph/nodes/0");
    screen
      .getByRole("treeitem", {
        name: "배열 인덱스 0, node_id close",
      })
      .focus();
    view.rerender(
      <StrategyOutline
        snapshot={snapshot()}
        selectedPointer="/risk/max_name_weight"
        onSelect={vi.fn()}
        onCollapse={vi.fn()}
      />,
    );
    const tabbable = Array.from(
      screen.getByRole("tree").querySelectorAll('[role="treeitem"]'),
    ).filter((item) => item.getAttribute("tabindex") === "0");
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0]).toHaveAccessibleName("max_name_weight");

    await user.type(
      screen.getByRole("searchbox", { name: "전략 구조 필터" }),
      "max_name_weight",
    );
    const tree = screen.getByRole("tree");
    expect(within(tree).getByRole("treeitem", { name: "risk" })).toBeVisible();
    expect(
      within(tree).getByRole("treeitem", { name: "max_name_weight" }),
    ).toBeVisible();
    expect(within(tree).queryByRole("treeitem", { name: "data" })).toBeNull();
  });
});
