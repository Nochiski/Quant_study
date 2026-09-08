import { foldCode, foldable, foldedRanges } from "@codemirror/language";
import { openSearchPanel } from "@codemirror/search";
import { EditorView } from "@codemirror/view";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CodeEditorView } from "../code-editor-view";

afterEach(cleanup);

const largeStrategyYaml = (nodeCount = 500): string => {
  const nodes = [
    "          - node_id: n0",
    "            field_id: price.close",
    "            kind: field",
    ...Array.from({ length: nodeCount - 1 }, (_, offset) => {
      const index = offset + 1;
      return [
        `          - node_id: n${index}`,
        "            operator: momentum",
        `            input_node_id: n${index - 1}`,
        "            window: 20",
        "            kind: time_series",
        "            lag: 0",
      ].join("\n");
    }),
  ];
  return [
    'schema_version: "1.0"',
    'title: "500-node strategy"',
    'description: "P6-04 editor performance fixture"',
    "data:",
    "  market: KRX",
    '  start: "2021-01-01"',
    '  end: "2026-08-31"',
    "  universe_id: krx.common-stock",
    "  frequency: daily",
    "eligibility:",
    "  rules: []",
    "factors:",
    "  factors:",
    "    - factor_id: deep_momentum",
    '      label: "Deep momentum"',
    "      direction: high",
    "      weight: 1.0",
    "      graph:",
    "        nodes:",
    ...nodes,
    `        output_node_id: n${nodeCount - 1}`,
    "        missing_policy: drop",
    "signal:",
    "  method: weighted_sum",
    "portfolio:",
    "  selection_count: 20",
    "  rebalance: monthly",
    "risk:",
    "  max_name_weight: 0.05",
    "execution:",
    "  timing: next_open",
    "  fee_bps: 15.0",
    "parameters: []",
    "",
  ].join("\n");
};

describe("CodeEditor 500-node performance contract", () => {
  it("keeps warmed synchronous keystrokes under 16ms with folding and search enabled", async () => {
    const source = largeStrategyYaml();
    expect(source.match(/^\s+- node_id:/gm)).toHaveLength(500);
    expect(source.split("\n").length).toBeGreaterThanOrEqual(3_000);
    let latest = source;
    render(
      <CodeEditorView
        value={source}
        language="yaml"
        ariaLabel="large strategy editor"
        onChange={(text) => {
          latest = text;
        }}
      />,
    );
    const textbox = await screen.findByRole("textbox", {
      name: "large strategy editor",
    });
    let view: EditorView | null = null;
    await waitFor(() => {
      view = EditorView.findFromDOM(textbox);
      expect(view).not.toBeNull();
    });
    const editor = view as unknown as EditorView;

    const factorsOffset = source.indexOf("factors:");
    const factorsLine = editor.state.doc.lineAt(factorsOffset);
    expect(
      foldable(editor.state, factorsLine.from, factorsLine.to),
    ).not.toBeNull();
    act(() => {
      editor.dispatch({ selection: { anchor: factorsLine.from } });
      expect(foldCode(editor)).toBe(true);
      expect(openSearchPanel(editor)).toBe(true);
    });
    let foldCount = 0;
    foldedRanges(editor.state).between(0, editor.state.doc.length, () => {
      foldCount += 1;
    });
    expect(foldCount).toBeGreaterThan(0);
    expect(editor.dom.querySelector(".cm-search")).not.toBeNull();

    let insertion = source.indexOf('strategy"');
    const samples: number[] = [];
    act(() => {
      for (let index = 0; index < 30; index += 1) {
        const started = performance.now();
        editor.dispatch({
          changes: { from: insertion, insert: "x" },
          selection: { anchor: insertion + 1 },
          userEvent: "input.type",
        });
        const elapsed = performance.now() - started;
        insertion += 1;
        if (index >= 5) samples.push(elapsed);
      }
    });

    const ordered = [...samples].sort((left, right) => left - right);
    const p95 = ordered[Math.ceil(ordered.length * 0.95) - 1] ?? Infinity;
    expect(
      p95,
      `p95 synchronous input latency was ${p95.toFixed(2)}ms`,
    ).toBeLessThan(16);
    expect(latest.length).toBe(source.length + 30);
  }, 15_000);
});
