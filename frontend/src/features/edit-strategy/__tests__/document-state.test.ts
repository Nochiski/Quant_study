import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import {
  currentDiagnostics,
  currentSpec,
  documentReducer,
  initialDocumentState,
  isSpecStale,
  shouldCompile,
  shouldParse,
  type CompileOutcome,
  type DocumentState,
} from "../model/document-state";

const spec = { title: "x" } as unknown as CompileOutcome["spec"];

const okOutcome = (hash = "h1"): CompileOutcome => ({
  spec,
  specHash: hash,
  schemaVersion: "1.0",
  sourceHash: "s",
  diagnostics: [],
});

const withErrors = (kind: "structural" | "semantic"): CompileOutcome => ({
  spec: null,
  specHash: null,
  schemaVersion: "1.0",
  sourceHash: "s",
  diagnostics: [
    {
      code: `${kind}.x`,
      kind,
      severity: "error",
      pointer: "/title",
      message: "bad",
      range: null,
    },
  ],
});

const run = (
  state: DocumentState,
  ...actions: Parameters<typeof documentReducer>[1][]
) => actions.reduce(documentReducer, state);

describe("document state machine", () => {
  it("walks editing → parsing → structurally-valid → semantically-valid", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, { type: "edit", source: "title: a\n" });
    expect(state.phase).toBe("parsing");
    expect(shouldParse(state)).toBe(true);
    expect(state.dirty).toBe(true);

    state = run(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    expect(state.phase).toBe("structurally-valid");
    expect(shouldParse(state)).toBe(false);
    expect(shouldCompile(state)).toBe(true);

    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: okOutcome(),
    });
    expect(state.phase).toBe("semantically-valid");
    expect(currentSpec(state)).toBe(spec);
    expect(shouldCompile(state)).toBe(false);
  });

  it("keeps the last compiled spec as stale when a later edit fails to parse", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, { type: "edit", source: "title: a\n" });
    state = run(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: okOutcome(),
    });

    state = run(state, { type: "edit", source: 'title: "unterminated\n' });
    expect(isSpecStale(state)).toBe(true);
    expect(currentSpec(state)).toBeNull();
    state = run(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    expect(state.phase).toBe("syntax-invalid");
    expect(state.compiled?.spec).toBe(spec); // never overwritten by a failed parse
    expect(isSpecStale(state)).toBe(true);
    expect(shouldCompile(state)).toBe(false); // invalid source never reaches the backend
    expect(currentDiagnostics(state).map((d) => [d.kind, d.code])).toEqual([
      ["syntax", "yaml.syntax"],
    ]);
  });

  it("discards out-of-order parse and compile replies", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, { type: "edit", source: "title: a\n" });
    const v1 = state.sourceVersion;
    state = run(state, { type: "edit", source: "title: ab\n" });
    state = run(state, {
      type: "parsed",
      version: v1,
      result: parseSource("title: a\n", "yaml"),
    });
    expect(state.parsedVersion).toBe(-1);
    state = run(state, { type: "compiled", version: v1, outcome: okOutcome() });
    expect(state.compiled).toBeNull();
    expect(state.phase).toBe("parsing");
  });

  it("does not parse while an IME composition is active and resumes after it ends", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, { type: "composing", composing: true });
    state = run(state, { type: "edit", source: "title: 한\n" });
    expect(state.phase).toBe("editing");
    expect(shouldParse(state)).toBe(false);
    state = run(state, { type: "composing", composing: false });
    expect(state.phase).toBe("parsing");
    expect(shouldParse(state)).toBe(true);
  });

  it("classifies structural and semantic failures and tracks the saved base", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: a\n",
      strategyId: "s1",
      baseRevision: 3,
      baseSpecHash: "h1",
    });
    expect(state.dirty).toBe(false);
    state = run(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: withErrors("structural"),
    });
    expect(state.phase).toBe("structure-invalid");
    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: withErrors("semantic"),
    });
    expect(state.phase).toBe("semantic-invalid");
    expect(currentSpec(state)).toBeNull();

    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: okOutcome("h1"),
    });
    expect(state.phase).toBe("saved"); // matches the base hash and the text is unchanged

    state = run(state, { type: "edit", source: "title: b\n" });
    expect(state.dirty).toBe(true);
    state = run(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: okOutcome("h2"),
    });
    expect(state.phase).toBe("semantically-valid");
    state = run(state, {
      type: "saved",
      strategyId: "s1",
      revision: 4,
      specHash: "h2",
      source: "title: b\n",
    });
    expect(state.phase).toBe("saved");
    expect(state.dirty).toBe(false);
    expect(state.baseRevision).toBe(4);
  });
});
