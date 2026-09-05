import { describe, expect, it } from "vitest";

import type { DocumentDiagnostic } from "../model/document-state";
import {
  DEFAULT_PROBLEM_FILTERS,
  deduplicateDiagnostics,
  projectProblems,
} from "../model/problem-list";

const diagnostic = (
  overrides: Partial<DocumentDiagnostic> = {},
): DocumentDiagnostic => ({
  code: "structure.required",
  kind: "structural",
  severity: "error",
  pointer: "/risk/max_name_weight",
  message: "field required",
  range: {
    start: { line: 1, column: 2, offset: 8 },
    end: { line: 1, column: 5, offset: 11 },
  },
  nodeId: "risk-limit",
  ...overrides,
});

describe("Problems projection", () => {
  it("deduplicates only diagnostics whose complete presentation identity matches", () => {
    const first = diagnostic();
    const distinctMessage = diagnostic({ message: "different reason" });
    const distinctRange = diagnostic({
      range: {
        start: { line: 2, column: 2, offset: 18 },
        end: { line: 2, column: 5, offset: 21 },
      },
    });
    const distinctNode = diagnostic({ nodeId: "another-node" });
    const distinctSeverity = diagnostic({ severity: "warning" });

    expect(
      deduplicateDiagnostics([
        first,
        { ...first },
        distinctMessage,
        distinctRange,
        distinctNode,
        distinctSeverity,
      ]),
    ).toEqual([
      first,
      distinctMessage,
      distinctRange,
      distinctNode,
      distinctSeverity,
    ]);
  });

  it("counts the deduplicated SoT before filtering and separates visible severity", () => {
    const structuralError = diagnostic();
    const syntaxError = diagnostic({
      code: "yaml.syntax",
      kind: "syntax",
      pointer: "",
      message: "bad YAML",
      nodeId: null,
    });
    const semanticWarning = diagnostic({
      code: "risk.high",
      kind: "semantic",
      severity: "warning",
      message: "high concentration",
    });
    const projection = projectProblems(
      [structuralError, structuralError, syntaxError, semanticWarning],
      { ...DEFAULT_PROBLEM_FILTERS, structural: false },
    );

    expect(projection.errorCount).toBe(2);
    expect(projection.warningCount).toBe(1);
    expect(projection.countsByKind).toEqual({
      syntax: 1,
      structural: 1,
      semantic: 1,
      capability: 0,
    });
    expect(projection.errors).toEqual([syntaxError]);
    expect(projection.warnings).toEqual([semanticWarning]);
  });
});
