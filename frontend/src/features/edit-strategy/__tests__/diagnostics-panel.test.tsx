import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentDiagnostic } from "../model/document-state";
import { DiagnosticsPanel } from "../ui/diagnostics-panel";

const located = (
  overrides: Partial<DocumentDiagnostic> = {},
): DocumentDiagnostic => ({
  code: "structure.required",
  kind: "structural",
  severity: "error",
  pointer: "/factors/0/graph/nodes/2/input_node_id",
  message: "input node is missing",
  range: {
    start: { line: 8, column: 4, offset: 80 },
    end: { line: 8, column: 12, offset: 88 },
  },
  nodeId: "momentum-rank",
  ...overrides,
});

afterEach(() => cleanup());

describe("DiagnosticsPanel", () => {
  it("groups errors before warnings, deduplicates exact rows and filters all four kinds", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const structural = located();
    const syntax = located({
      code: "yaml.syntax",
      kind: "syntax",
      pointer: "",
      message: "mapping is not closed",
      nodeId: null,
    });
    const semantic = located({
      code: "risk.concentration",
      kind: "semantic",
      severity: "warning",
      pointer: "/risk/max_name_weight",
      message: "weight is unusually high",
      nodeId: null,
    });
    const capability = located({
      code: "compile.unavailable",
      kind: "capability",
      pointer: "",
      message: "validation server unavailable",
      range: null,
      nodeId: null,
    });

    render(
      <DiagnosticsPanel
        diagnostics={[
          semantic,
          structural,
          { ...structural },
          capability,
          syntax,
        ]}
        stale={false}
        onSelect={onSelect}
      />,
    );

    const panel = screen.getByRole("region", { name: "문제" });
    expect(panel).toHaveTextContent("오류 3 · 경고 1");
    const errorSection = within(panel).getByRole("region", { name: "오류 3" });
    const warningSection = within(panel).getByRole("region", {
      name: "경고 1",
    });
    expect(within(errorSection).getAllByRole("listitem")).toHaveLength(3);
    expect(within(warningSection).getAllByRole("listitem")).toHaveLength(1);
    expect(panel.textContent!.indexOf("input node is missing")).toBeLessThan(
      panel.textContent!.indexOf("weight is unusually high"),
    );

    const filters = within(panel).getByRole("group", {
      name: "문제 유형 필터",
    });
    for (const label of ["구문", "구조", "검증", "서버"]) {
      expect(
        within(filters).getByRole("button", { name: new RegExp(label) }),
      ).toHaveAttribute("aria-pressed", "true");
    }
    await user.click(within(filters).getByRole("button", { name: /구조/ }));
    expect(
      screen.queryByRole("button", { name: /input node is missing/ }),
    ).not.toBeInTheDocument();
    expect(panel).toHaveTextContent("오류 3 · 경고 1");

    await user.click(
      screen.getByRole("button", { name: /weight is unusually high/ }),
    );
    expect(onSelect).toHaveBeenCalledWith(semantic);

    await user.click(within(filters).getByRole("button", { name: /구문/ }));
    await user.click(within(filters).getByRole("button", { name: /검증/ }));
    await user.click(within(filters).getByRole("button", { name: /서버/ }));
    expect(panel).toHaveTextContent("선택한 유형에 해당하는 문제가 없습니다.");
    await user.click(within(filters).getByRole("button", { name: /검증/ }));
    expect(
      screen.getByRole("button", { name: /weight is unusually high/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /mapping is not closed/ }),
    ).not.toBeInTheDocument();
  });

  it("copies the exact JSON Pointer and node ID without coupling copy to navigation", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const problem = located();

    render(
      <DiagnosticsPanel
        diagnostics={[problem]}
        stale={false}
        onSelect={onSelect}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: `JSON Pointer 복사: ${problem.pointer}`,
      }),
    );
    expect(writeText).toHaveBeenLastCalledWith(problem.pointer);
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getAllByRole("status").at(-1)).toHaveTextContent(
      `JSON Pointer ${problem.pointer} 복사됨`,
    );

    await user.click(
      screen.getByRole("button", {
        name: `노드 ID 복사: ${problem.nodeId!}`,
      }),
    );
    expect(writeText).toHaveBeenLastCalledWith(problem.nodeId);
  });

  it("keeps stale navigation disabled while leaving diagnostic identifiers copyable", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const problem = located();

    render(
      <DiagnosticsPanel diagnostics={[problem]} stale onSelect={vi.fn()} />,
    );

    expect(
      screen.getByRole("button", { name: /input node is missing/ }),
    ).toBeDisabled();
    const copy = screen.getByRole("button", { name: /JSON Pointer 복사/ });
    expect(copy).toBeEnabled();
    await user.click(copy);
    expect(writeText).toHaveBeenCalledWith(problem.pointer);
  });

  it("announces clipboard failures", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(() => Promise.reject(new Error("denied"))) },
      configurable: true,
    });
    render(
      <DiagnosticsPanel
        diagnostics={[located()]}
        stale={false}
        onSelect={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /JSON Pointer 복사/ }));
    expect(screen.getAllByRole("status").at(-1)).toHaveTextContent(
      "진단 식별자를 복사할 수 없습니다.",
    );
  });
});
