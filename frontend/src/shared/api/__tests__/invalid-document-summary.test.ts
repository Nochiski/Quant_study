import { describe, expect, it } from "vitest";

import { invalidDocumentSummary } from "../strategy-workbench";

describe("invalidDocumentSummary (backlog 16)", () => {
  it("names the first error diagnostic with its pointer and the remaining count", () => {
    expect(
      invalidDocumentSummary({
        detail: {
          code: "strategy_document.invalid",
          source_hash: "a".repeat(64),
          schema_version: "1.1",
          diagnostics: [
            { code: "x", kind: "semantic", severity: "warning", pointer: "/risk", message: "경고", range: null, node_id: null },
            { code: "y", kind: "structural", severity: "error", pointer: "/factors/0/graph", message: "출력 노드 없음", range: null, node_id: null },
            { code: "z", kind: "semantic", severity: "error", pointer: "", message: "루트", range: null, node_id: null },
          ],
        },
      }),
    ).toBe("/factors/0/graph: 출력 노드 없음 (+2)");
    expect(
      invalidDocumentSummary({
        detail: { code: "strategy_document.invalid", diagnostics: [{ severity: "error", pointer: "", message: "루트" }] },
      }),
    ).toBe("/: 루트");
  });

  it("stays silent for other codes or without diagnostics", () => {
    expect(invalidDocumentSummary({ detail: { code: "strategy.revision_conflict", message: "x" } })).toBeUndefined();
    expect(invalidDocumentSummary({ detail: { code: "strategy_document.invalid", diagnostics: [] } })).toBeUndefined();
    expect(invalidDocumentSummary("boom")).toBeUndefined();
  });
});
