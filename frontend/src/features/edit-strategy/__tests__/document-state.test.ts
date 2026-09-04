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
  canonicalJson: '{"title":"x"}',
  specHash: hash,
  schemaVersion: "1.0",
  sourceHash: "s",
  diagnostics: [],
});

const withErrors = (kind: "structural" | "semantic"): CompileOutcome => ({
  spec: null,
  canonicalJson: null,
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
    // A reply for the previous version is the freshest spec there is: it is kept as a stale
    // result, and the phase of the current (unparsed) text is untouched.
    state = run(state, { type: "compiled", version: v1, outcome: okOutcome() });
    expect(state.compiled).not.toBeNull();
    expect(state.compiledVersion).toBe(v1);
    expect(isSpecStale(state)).toBe(true);
    expect(currentSpec(state)).toBeNull();
    expect(state.phase).toBe("parsing");
    // Replies older than what is held, or for a version that never existed, are dropped.
    const held = state;
    expect(
      run(state, {
        type: "compiled",
        version: v1 - 1,
        outcome: okOutcome("old"),
      }),
    ).toBe(held);
    expect(
      run(state, {
        type: "compiled",
        version: state.sourceVersion + 5,
        outcome: okOutcome("future"),
      }),
    ).toBe(held);
  });

  it("never lets a reply for the previous document land on the next one", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: A\n",
      strategyId: "A",
      baseRevision: 1,
      baseSpecHash: "hA",
    });
    const versionOfA = state.sourceVersion;
    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: B\n",
      strategyId: "B",
      baseRevision: 7,
      baseSpecHash: "hB",
    });
    expect(state.sourceVersion).toBeGreaterThan(versionOfA);
    const before = state;
    state = run(state, {
      type: "compiled",
      version: versionOfA,
      outcome: okOutcome("hA"),
    });
    expect(state).toBe(before);
    expect(currentSpec(state)).toBeNull();
  });

  it("drops the last valid compile when a different document is loaded", () => {
    let state = initialDocumentState("yaml", "title: A\n");
    state = run(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: okOutcome("hA"),
    });
    expect(state.lastValidCompiled?.outcome.specHash).toBe("hA");

    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: B\n",
      strategyId: "B",
      baseRevision: 1,
      baseSpecHash: "hB",
    });
    expect(state.lastValidCompiled).toBeNull();
    expect(state.compiled).toBeNull();
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
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
    });
    expect(state.phase).toBe("saved");
    expect(state.dirty).toBe(false);
    expect(state.baseRevision).toBe(4);
  });

  it("rejects save responses from an older loaded document", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: same\n",
      strategyId: "A",
      baseRevision: 1,
      baseSpecHash: "hA1",
    });
    const request = {
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      source: state.source,
    };
    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: same\n",
      strategyId: "B",
      baseRevision: 7,
      baseSpecHash: "hB7",
    });
    const loadedB = state;
    state = run(state, {
      type: "saved",
      strategyId: "A",
      revision: 2,
      specHash: "hA2",
      ...request,
    });
    expect(state).toBe(loadedB);
    expect(state.strategyId).toBe("B");
    expect(state.baseRevision).toBe(7);
  });

  it("keeps edits made during an in-flight save dirty", () => {
    let state = initialDocumentState("yaml", "");
    state = run(state, {
      type: "load",
      format: "yaml",
      source: "title: a\n",
      strategyId: "s1",
      baseRevision: 1,
      baseSpecHash: "h1",
    });
    state = run(state, { type: "edit", source: "title: b\n" });
    const request = {
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      source: state.source,
    };
    state = run(state, { type: "edit", source: "title: c\n" });
    state = run(state, {
      type: "saved",
      strategyId: "s1",
      revision: 2,
      specHash: "h2",
      ...request,
    });
    expect(state.source).toBe("title: c\n");
    expect(state.savedSource).toBe("title: b\n");
    expect(state.baseRevision).toBe(2);
    expect(state.dirty).toBe(true);
  });
});
