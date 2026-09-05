import { useCallback, useEffect, useState } from "react";

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
    // Unknown-key diagnostics identify the misspelled field itself. Other diagnostics describe
    // the field's value, so prefer its value range while retaining a key-only fallback.
    const exact =
      diagnostic.code === "structure.unknown_key"
        ? parse.keyRanges.get(diagnostic.pointer)
        : (parse.valueRanges.get(diagnostic.pointer) ??
          parse.keyRanges.get(diagnostic.pointer));
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
) => {
  // Explicit "Validate" (toolbar): recompile the current version even if it was compiled
  // already, e.g. after a transport failure. Bumping the nonce re-runs the effect below.
  const [forced, setForced] = useState<{
    version: number;
    nonce: number;
  } | null>(null);
  const validateNow = useCallback(() => {
    setForced((previous) => ({
      version: state.sourceVersion,
      nonce: (previous?.nonce ?? 0) + 1,
    }));
  }, [state.sourceVersion]);
  const forcedNow = forced !== null && forced.version === state.sourceVersion;
  const canForce =
    !state.composing &&
    state.parsedVersion === state.sourceVersion &&
    state.parse?.status === "ok";

  useEffect(() => {
    // If editing outruns the initial debounced compile, obtain the immutable saved baseline in
    // a separate request. Its reducer action can only fill `savedCanonicalJson`; it cannot mark
    // the current text valid or replace the current compile. Thus both sides of semantic Diff
    // remain backend canonical payloads without a client-side serializer.
    if (
      !state.dirty ||
      state.savedSource === null ||
      state.baseSpecHash === null ||
      state.savedCanonicalJson !== null
    ) {
      return;
    }
    const documentEpoch = state.documentEpoch;
    const source = state.savedSource;
    const expectedSpecHash = state.baseSpecHash;
    const controller = new AbortController();
    void strategyWorkbenchApi
      .compileStrategyDocument(
        { format: state.format, source },
        controller.signal,
      )
      .then((compiled) => {
        if (
          controller.signal.aborted ||
          compiled.canonical_json === null ||
          compiled.spec_hash !== expectedSpecHash
        ) {
          return;
        }
        dispatch({
          type: "base-compiled",
          documentEpoch,
          source,
          specHash: compiled.spec_hash,
          canonicalJson: compiled.canonical_json,
        });
      })
      .catch(() => {
        // The current draft compile owns visible availability diagnostics. A baseline retry is
        // naturally triggered by the next edit while the canonical payload remains absent.
      });
    return () => controller.abort();
  }, [
    dispatch,
    state.baseSpecHash,
    state.dirty,
    state.documentEpoch,
    state.format,
    state.savedCanonicalJson,
    state.savedSource,
  ]);

  useEffect(() => {
    if (!shouldCompile(state) && !(forcedNow && canForce)) return;
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
            canonicalJson: compiled.canonical_json,
            specHash: compiled.spec_hash,
            schemaVersion: compiled.schema_version,
            sourceHash: compiled.source_hash,
            diagnostics: toDocumentDiagnostics(compiled.diagnostics, parse),
          };
          dispatch({ type: "compiled", version, outcome });
          if (forcedNow) setForced(null); // the forced compile is consumed by its reply
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (forcedNow) setForced(null);
          // Transport failure: the text stays "structurally-valid" and is retried on the next
          // edit; the failure is surfaced as a capability diagnostic, not silently swallowed.
          dispatch({
            type: "compiled",
            version,
            outcome: {
              spec: null,
              canonicalJson: null,
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
  }, [state, dispatch, forcedNow, canForce, forced?.nonce]);

  const validating =
    !state.composing &&
    state.parse?.status === "ok" &&
    state.parsedVersion === state.sourceVersion &&
    (state.compiledVersion !== state.sourceVersion || forcedNow);
  return { validateNow, validating };
};
