import { useEffect } from "react";

import {
  strategyWorkbenchApi,
  type SourceDiagnostic,
} from "../../../shared/api";
import { t } from "../../../shared/config";
import { locateRange, type ParsedSource } from "../../../shared/lib/yaml12";
import {
  shouldCompile,
  type CompileOutcome,
  type DocumentAction,
  type DocumentDiagnostic,
  type DocumentState,
} from "./document-state";

const COMPILE_DELAY_MS = 300;

/** Exact frontend range for the pointer, else the backend's range, else the nearest ancestor. */
const rangeFor = (
  diagnostic: SourceDiagnostic,
  parse: ParsedSource | null,
): DocumentDiagnostic["range"] => {
  // Missing root fields are anchored at the document start instead of underlining the entire
  // document. The editor expands this one-character range when it renders the marker.
  if (diagnostic.pointer === "" && parse) {
    const root = parse.valueRanges.get("");
    if (root) {
      const width = Math.min(
        1,
        Math.max(0, root.end.offset - root.start.offset),
      );
      return {
        start: root.start,
        end: {
          ...root.start,
          column: root.start.column + width,
          offset: root.start.offset + width,
        },
      };
    }
  }
  if (parse) {
    const exact =
      parse.valueRanges.get(diagnostic.pointer) ??
      parse.keyRanges.get(diagnostic.pointer);
    if (exact) return exact;
  }
  if (diagnostic.range) return diagnostic.range;
  return parse ? locateRange(parse, diagnostic.pointer) : null;
};

/**
 * Backend diagnostics become editor markers through their JSON Pointer: the frontend parse map
 * (UTF-16 offsets of this exact text) locates the range; the backend's own range is used only
 * for pointers the map does not hold, and the nearest ancestor is the last resort (WORKFLOW
 * P3-04 "pointer → source range").
 */
export const toDocumentDiagnostics = (
  diagnostics: readonly SourceDiagnostic[],
  parse: ParsedSource | null,
): DocumentDiagnostic[] =>
  diagnostics.map((diagnostic) => ({
    code: diagnostic.code,
    kind: diagnostic.kind,
    severity: diagnostic.severity,
    pointer: diagnostic.pointer,
    message: diagnostic.message,
    range: rangeFor(diagnostic, parse),
    nodeId: diagnostic.node_id ?? null,
  }));

/**
 * Binds the reducer to the backend compile API (semantic truth, editor ADR D2). A compile goes
 * out only for a parsed, syntactically valid, not-yet-compiled version outside IME composition
 * (`shouldCompile`), after a short debounce; the previous request is aborted when the text
 * changes again, and a reply for another `sourceVersion` is discarded by the reducer.
 */
export const useCompileDocument = (
  state: DocumentState,
  dispatch: (action: DocumentAction) => void,
): void => {
  useEffect(() => {
    if (!shouldCompile(state)) return;
    const version = state.sourceVersion;
    const request = { format: state.format, source: state.source };
    const parse = state.parse;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void strategyWorkbenchApi
        .compileStrategyDocument(request, controller.signal)
        .then((compiled) => {
          if (controller.signal.aborted) return;
          const outcome: CompileOutcome = {
            spec: compiled.spec,
            specHash: compiled.spec_hash,
            schemaVersion: compiled.schema_version,
            sourceHash: compiled.source_hash,
            diagnostics: toDocumentDiagnostics(compiled.diagnostics, parse),
          };
          dispatch({ type: "compiled", version, outcome });
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          // Transport failure: the text stays "structurally-valid" and is retried on the next
          // edit; the failure is surfaced as a capability diagnostic, not silently swallowed.
          dispatch({
            type: "compiled",
            version,
            outcome: {
              spec: null,
              specHash: null,
              schemaVersion: null,
              sourceHash: "",
              diagnostics: [
                {
                  code: "compile.unavailable",
                  kind: "capability",
                  severity: "error",
                  pointer: "",
                  message: t("problems.compileUnavailable").replace(
                    "{detail}",
                    error instanceof Error ? error.message : String(error),
                  ),
                  range: null,
                },
              ],
            },
          });
        });
    }, COMPILE_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [state, dispatch]);
};
