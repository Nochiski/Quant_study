import type { DiagnosticKind, DocumentDiagnostic } from "./document-state";

export const DIAGNOSTIC_KINDS = [
  "syntax",
  "structural",
  "semantic",
  "capability",
] as const satisfies readonly DiagnosticKind[];

export type ProblemFilters = Readonly<Record<DiagnosticKind, boolean>>;

export const DEFAULT_PROBLEM_FILTERS: ProblemFilters = {
  syntax: true,
  structural: true,
  semantic: true,
  capability: true,
};

export type ProblemProjection = {
  /** Exact diagnostics after presentation-only deduplication, in backend order. */
  diagnostics: DocumentDiagnostic[];
  errors: DocumentDiagnostic[];
  warnings: DocumentDiagnostic[];
  countsByKind: Record<DiagnosticKind, number>;
  errorCount: number;
  warningCount: number;
};

/**
 * Identity is deliberately made from every observable diagnostic field. The backend diagnostic
 * array remains the semantic SoT: this key only removes field-for-field-equivalent presentation
 * rows and never coalesces findings merely because they share a code or JSON Pointer.
 */
export const diagnosticIdentity = (diagnostic: DocumentDiagnostic): string =>
  JSON.stringify([
    diagnostic.code,
    diagnostic.kind,
    diagnostic.severity,
    diagnostic.pointer,
    diagnostic.message,
    diagnostic.nodeId ?? null,
    diagnostic.range === null
      ? null
      : [
          diagnostic.range.start.line,
          diagnostic.range.start.column,
          diagnostic.range.start.offset,
          diagnostic.range.end.line,
          diagnostic.range.end.column,
          diagnostic.range.end.offset,
        ],
  ]);

export const deduplicateDiagnostics = (
  diagnostics: readonly DocumentDiagnostic[],
): DocumentDiagnostic[] => {
  const seen = new Set<string>();
  return diagnostics.filter((diagnostic) => {
    const identity = diagnosticIdentity(diagnostic);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
};

/**
 * Builds the Problems-panel view without inventing or reclassifying diagnostics. Counts describe
 * the deduplicated backend/parser answer; filters only decide which rows enter the two visible
 * severity sections.
 */
export const projectProblems = (
  diagnostics: readonly DocumentDiagnostic[],
  filters: ProblemFilters,
): ProblemProjection => {
  const deduplicated = deduplicateDiagnostics(diagnostics);
  const countsByKind: Record<DiagnosticKind, number> = {
    syntax: 0,
    structural: 0,
    semantic: 0,
    capability: 0,
  };
  let errorCount = 0;
  let warningCount = 0;

  for (const diagnostic of deduplicated) {
    countsByKind[diagnostic.kind] += 1;
    if (diagnostic.severity === "error") errorCount += 1;
    else warningCount += 1;
  }

  const visible = deduplicated.filter((diagnostic) => filters[diagnostic.kind]);
  return {
    diagnostics: deduplicated,
    errors: visible.filter((diagnostic) => diagnostic.severity === "error"),
    warnings: visible.filter((diagnostic) => diagnostic.severity === "warning"),
    countsByKind,
    errorCount,
    warningCount,
  };
};
