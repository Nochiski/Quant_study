/**
 * Frontend document state machine (WORKFLOW P3-01, editor ADR D2, router ADR D3).
 *
 *   editing → parsing → structurally-valid → semantically-valid → saved
 *   editing → syntax-invalid | structure-invalid | semantic-invalid
 *
 * Owns: source text and its version, the parse result and source map, the last compiled spec
 * (never overwritten by a failed parse: it becomes *stale* and is view-only), the saved base
 * (revision + spec hash + exact source) and the dirty flag. Semantic truth (`spec`, `spec_hash`,
 * structural/semantic diagnostics) only ever comes from the backend compile API; this reducer
 * just records the answer for the version it was asked about and discards out-of-order replies.
 */
import type { StrategySpec } from "../../../entities/strategy";
import type {
  ParsedSource,
  SourceFormat,
  SourceRange,
} from "../../../shared/lib/yaml12";

export type DocumentPhase =
  | "editing"
  | "parsing"
  | "syntax-invalid"
  | "structure-invalid"
  | "semantic-invalid"
  | "structurally-valid"
  | "semantically-valid"
  | "saved";

export type DiagnosticKind =
  "syntax" | "structural" | "semantic" | "capability";

export type DocumentDiagnostic = {
  code: string;
  kind: DiagnosticKind;
  severity: "error" | "warning";
  pointer: string;
  message: string;
  range: SourceRange | null;
  nodeId?: string | null;
};

/** What the backend compile call answered for one `sourceVersion`. */
export type CompileOutcome = {
  spec: StrategySpec | null;
  specHash: string | null;
  schemaVersion: string | null;
  sourceHash: string;
  diagnostics: DocumentDiagnostic[];
};

export type DocumentState = {
  format: SourceFormat;
  source: string;
  sourceVersion: number;
  composing: boolean;
  parse: ParsedSource | null;
  parsedVersion: number;
  compiled: CompileOutcome | null;
  compiledVersion: number;
  baseRevision: number | null;
  baseSpecHash: string | null;
  savedSource: string | null;
  strategyId: string | null;
  dirty: boolean;
  phase: DocumentPhase;
};

export type DocumentAction =
  | {
      type: "load";
      format: SourceFormat;
      source: string;
      strategyId: string | null;
      baseRevision: number | null;
      baseSpecHash: string | null;
    }
  | { type: "edit"; source: string }
  | { type: "composing"; composing: boolean }
  | { type: "parsed"; version: number; result: ParsedSource }
  | { type: "compiled"; version: number; outcome: CompileOutcome }
  | {
      type: "saved";
      strategyId: string;
      revision: number;
      specHash: string;
      source: string;
    };

export const initialDocumentState = (
  format: SourceFormat = "yaml",
  source = "",
): DocumentState => ({
  format,
  source,
  sourceVersion: 0,
  composing: false,
  parse: null,
  parsedVersion: -1,
  compiled: null,
  compiledVersion: -1,
  baseRevision: null,
  baseSpecHash: null,
  savedSource: null,
  strategyId: null,
  dirty: false,
  phase: "editing",
});

const phaseFromCompile = (
  outcome: CompileOutcome,
  saved: boolean,
): DocumentPhase => {
  const errors = outcome.diagnostics.filter((d) => d.severity === "error");
  if (errors.some((d) => d.kind === "syntax")) return "syntax-invalid";
  if (errors.some((d) => d.kind === "structural")) return "structure-invalid";
  if (errors.length > 0) return "semantic-invalid";
  if (outcome.spec === null) return "structure-invalid";
  return saved ? "saved" : "semantically-valid";
};

export const documentReducer = (
  state: DocumentState,
  action: DocumentAction,
): DocumentState => {
  switch (action.type) {
    case "load":
      return {
        ...initialDocumentState(action.format, action.source),
        strategyId: action.strategyId,
        baseRevision: action.baseRevision,
        baseSpecHash: action.baseSpecHash,
        savedSource: action.baseRevision === null ? null : action.source,
        dirty: false,
        phase: "editing",
      };
    case "edit": {
      if (action.source === state.source) return state;
      return {
        ...state,
        source: action.source,
        sourceVersion: state.sourceVersion + 1,
        dirty:
          state.savedSource === null
            ? action.source.length > 0
            : action.source !== state.savedSource,
        phase: state.composing ? "editing" : "parsing",
      };
    }
    case "composing":
      if (action.composing === state.composing) return state;
      return {
        ...state,
        composing: action.composing,
        // Leaving composition with an unparsed version lets the parser run.
        phase:
          !action.composing && state.parsedVersion !== state.sourceVersion
            ? "parsing"
            : state.phase,
      };
    case "parsed": {
      if (action.version !== state.sourceVersion) return state; // stale reply
      return {
        ...state,
        parse: action.result,
        parsedVersion: action.version,
        phase:
          action.result.status === "ok"
            ? "structurally-valid"
            : "syntax-invalid",
      };
    }
    case "compiled": {
      if (action.version !== state.sourceVersion) return state; // out-of-order reply discarded
      const saved =
        state.baseSpecHash !== null &&
        action.outcome.specHash === state.baseSpecHash &&
        !state.dirty;
      return {
        ...state,
        compiled: action.outcome,
        compiledVersion: action.version,
        phase: phaseFromCompile(action.outcome, saved),
      };
    }
    case "saved":
      return {
        ...state,
        strategyId: action.strategyId,
        baseRevision: action.revision,
        baseSpecHash: action.specHash,
        savedSource: action.source,
        dirty: action.source !== state.source,
        phase:
          action.source === state.source &&
          state.compiledVersion === state.sourceVersion
            ? "saved"
            : state.phase,
      };
  }
};

/** The parser may run only when the text is not mid-IME-composition and the version is new. */
export const shouldParse = (state: DocumentState): boolean =>
  !state.composing && state.parsedVersion !== state.sourceVersion;

/** The compile call goes out only for a parsed, syntactically valid, not-yet-compiled version. */
export const shouldCompile = (state: DocumentState): boolean =>
  !state.composing &&
  state.parsedVersion === state.sourceVersion &&
  state.parse?.status === "ok" &&
  state.compiledVersion !== state.sourceVersion;

/** A compiled spec that no longer matches the text: view-only, shown with an explicit badge. */
export const isSpecStale = (state: DocumentState): boolean =>
  state.compiled?.spec != null && state.compiledVersion !== state.sourceVersion;

/** A spec safe to execute: current, compiled without errors. */
export const currentSpec = (state: DocumentState): StrategySpec | null =>
  state.compiled !== null &&
  state.compiledVersion === state.sourceVersion &&
  state.compiled.spec !== null &&
  !state.compiled.diagnostics.some((d) => d.severity === "error")
    ? state.compiled.spec
    : null;

/** Diagnostics for the current text: parser syntax markers now, backend markers once compiled. */
export const currentDiagnostics = (
  state: DocumentState,
): DocumentDiagnostic[] => {
  if (state.parsedVersion !== state.sourceVersion || state.parse === null)
    return [];
  if (state.parse.status === "rejected") {
    return state.parse.diagnostics.map((d) => ({
      code: d.code,
      kind: "syntax",
      severity: "error",
      pointer: "",
      message: d.message,
      range: d.range,
    }));
  }
  return state.compiledVersion === state.sourceVersion &&
    state.compiled !== null
    ? state.compiled.diagnostics
    : [];
};
