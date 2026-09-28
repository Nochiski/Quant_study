import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { diffCanonicalJson, projectDraftDiff } from "../model/diff-projection";
import {
  documentReducer,
  initialDocumentState,
  type CompileOutcome,
  type DocumentState,
} from "../model/document-state";

const compile = (canonicalJson: string, specHash: string): CompileOutcome => ({
  spec: JSON.parse(canonicalJson) as CompileOutcome["spec"],
  canonicalJson,
  specHash,
  schemaVersion: "1.1",
  sourceHash: "s".repeat(64),
  diagnostics: [],
});

const reduce = (
  state: DocumentState,
  ...actions: Parameters<typeof documentReducer>[1][]
) => actions.reduce(documentReducer, state);

const savedState = (): DocumentState => {
  let state = reduce(initialDocumentState(), {
    type: "load",
    format: "yaml",
    source: 'schema_version: "1.1"\ntitle: Alpha\n',
    strategyId: "s1",
    baseRevision: 2,
    baseSpecHash: "a".repeat(64),
  });
  state = reduce(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(state.source, "yaml"),
  });
  return reduce(state, {
    type: "compiled",
    version: state.sourceVersion,
    outcome: compile(
      '{"schema_version":"1.1","title":"Alpha","nested":{"a/b":1}}',
      "a".repeat(64),
    ),
  });
};

const editAndCompile = (
  state: DocumentState,
  source: string,
  canonicalJson: string,
  specHash: string,
): DocumentState => {
  let next = reduce(state, { type: "edit", source });
  next = reduce(next, {
    type: "parsed",
    version: next.sourceVersion,
    result: parseSource(source, "yaml"),
  });
  return reduce(next, {
    type: "compiled",
    version: next.sourceVersion,
    outcome: compile(canonicalJson, specHash),
  });
};

describe("StrategySpec diff projection", () => {
  it("diffs backend canonical payloads generically with escaped JSON Pointers", () => {
    expect(
      diffCanonicalJson(
        '{"nested":{"a/b":1,"x~y":[1,2]}}',
        '{"nested":{"a/b":2,"x~y":[1,3],"new":true}}',
      ),
    ).toEqual([
      {
        pointer: "/nested/a~1b",
        kind: "changed",
        before: 1,
        after: 2,
      },
      {
        pointer: "/nested/new",
        kind: "added",
        before: null,
        after: true,
      },
      {
        pointer: "/nested/x~0y/1",
        kind: "changed",
        before: 2,
        after: 3,
      },
    ]);
  });

  it("shows comment-only source changes while backend spec hash proves no semantic change", () => {
    const base = savedState();
    const state = editAndCompile(
      base,
      `${base.source}# research note\n`,
      base.savedCanonicalJson!,
      base.baseSpecHash!,
    );
    const projection = projectDraftDiff(state);

    expect(projection.source).toMatchObject({ added: 1, removed: 0 });
    expect(projection.source.rows[0]).toMatchObject({
      kind: "added",
      text: "# research note",
    });
    expect(projection.semantic).toMatchObject({
      status: "ready",
      changes: [],
      baseSpecHash: "a".repeat(64),
      currentSpecHash: "a".repeat(64),
    });
  });

  it("reports semantic leaf changes only from two backend canonical payloads", () => {
    const base = savedState();
    const state = editAndCompile(
      base,
      'schema_version: "1.1"\ntitle: Beta\n',
      '{"schema_version":"1.1","title":"Beta","nested":{"a/b":1}}',
      "b".repeat(64),
    );

    expect(projectDraftDiff(state).semantic).toMatchObject({
      status: "ready",
      changes: [
        {
          pointer: "/title",
          kind: "changed",
          before: "Alpha",
          after: "Beta",
        },
      ],
    });
  });

  it("keeps exact text diff but withholds semantics for invalid current source", () => {
    const base = savedState();
    let state = reduce(base, { type: "edit", source: 'title: "broken\n' });
    state = reduce(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    const projection = projectDraftDiff(state);

    expect(projection.source.added + projection.source.removed).toBeGreaterThan(
      0,
    );
    expect(projection.semantic).toEqual({ status: "current-unavailable" });
  });

  it("folds the compile-added promotion nodes so one added node is one row (BACKLOG-014)", () => {
    // 문서는 비교 노드 `gt`가 출력이다. compile 은 그래프 끝에 승격 노드 셋을 붙인다(P2-07).
    // 사용자가 노드 하나를 더하면 canonical 위치 비교로는 붙인 노드 셋이 한 칸씩 밀려 "바뀐 것"이 된다.
    const node = (id: string, extra: Record<string, unknown> = {}) => ({
      kind: "field",
      node_id: id,
      field_id: "price.close",
      ...extra,
    });
    const promoted = (nodes: unknown[]) =>
      JSON.stringify({
        factors: [
          {
            factor_id: "f",
            graph: {
              nodes: [
                ...nodes,
                { kind: "constant", node_id: "__promote_f_one", value: 1 },
                { kind: "constant", node_id: "__promote_f_zero", value: 0 },
                {
                  kind: "conditional",
                  node_id: "__promote_f",
                  predicate_node_id: "gt",
                  true_node_id: "__promote_f_one",
                  false_node_id: "__promote_f_zero",
                },
              ],
              output_node_id: "__promote_f",
            },
          },
        ],
        schema_version: "1.2",
      });
    const gt = {
      kind: "comparison",
      node_id: "gt",
      operator: "gt",
      left_node_id: "a",
      right_node_id: "b",
    };
    const yaml = (extra: string) =>
      [
        'schema_version: "1.2"',
        "title: t",
        "factors:",
        "  - factor_id: f",
        "    graph:",
        "      nodes:",
        "        - { kind: field, node_id: a, field_id: price.close }",
        "        - { kind: field, node_id: b, field_id: price.close }",
        "        - { kind: comparison, node_id: gt, operator: gt, left_node_id: a, right_node_id: b }",
        extra,
        "      output_node_id: gt",
        "",
      ]
        .filter((line) => line !== "")
        .join("\n");
    let base = reduce(initialDocumentState(), {
      type: "load",
      format: "yaml",
      source: yaml(""),
      strategyId: "s1",
      baseRevision: 1,
      baseSpecHash: "a".repeat(64),
    });
    base = reduce(base, {
      type: "parsed",
      version: base.sourceVersion,
      result: parseSource(base.source, "yaml"),
    });
    base = reduce(base, {
      type: "compiled",
      version: base.sourceVersion,
      outcome: compile(promoted([node("a"), node("b"), gt]), "a".repeat(64)),
    });

    const state = editAndCompile(
      base,
      yaml("        - { kind: field, node_id: c, field_id: price.close }"),
      promoted([node("a"), node("b"), gt, node("c")]),
      "b".repeat(64),
    );

    expect(projectDraftDiff(state).semantic).toMatchObject({
      status: "ready",
      changes: [
        {
          pointer: "/factors/0/graph/nodes/3",
          kind: "added",
          before: null,
          after: node("c"),
        },
      ],
    });
    // 문서 tree 없이 비교하면 붙인 노드 셋이 밀려 여러 행이 된다(이전 동작, 계약 비교용).
    expect(
      diffCanonicalJson(
        promoted([node("a"), node("b"), gt]),
        promoted([node("a"), node("b"), gt, node("c")]),
      )!.length,
    ).toBeGreaterThan(1);
  });

  it("fails closed if hashes differ but canonical payloads do not", () => {
    const base = savedState();
    const state = editAndCompile(
      base,
      `${base.source}# impossible hash drift\n`,
      base.savedCanonicalJson!,
      "b".repeat(64),
    );
    expect(projectDraftDiff(state).semantic).toEqual({ status: "incoherent" });
  });

  it("treats a new draft as source against empty with no invented semantic base", () => {
    const state = reduce(initialDocumentState(), {
      type: "edit",
      source: 'schema_version: "1.1"\ntitle: New\n',
    });
    const projection = projectDraftDiff(state);
    expect(projection.sourceBase).toBe("empty-draft");
    expect(projection.source.added).toBe(3);
    expect(projection.source.rows.at(-1)).toMatchObject({
      kind: "added",
      marker: "final-newline",
    });
    expect(projection.semantic).toEqual({ status: "no-base" });
  });
});
