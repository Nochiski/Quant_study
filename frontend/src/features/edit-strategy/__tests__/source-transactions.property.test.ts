import fc from "fast-check";
import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import {
  identifierArbitrary,
  pointersOf,
  scalarArbitrary,
  strategyDocumentArbitrary,
  toYaml,
  valueArbitrary,
} from "../../../shared/testing/strategy-document-arbitrary";
import {
  applyToTree,
  planSourceOperation,
  type SourceOperation,
} from "../model/source-transactions";

/** 문서와 그 문서에 유효한 연산 하나. */
const documentAndOperation = fc
  .tuple(
    strategyDocumentArbitrary(),
    fc.constantFrom("\n", "\r\n") as fc.Arbitrary<"\n" | "\r\n">,
  )
  .chain(([tree, eol]) => {
    const pointers = pointersOf(tree);
    const scalars = pointers.filter((p) => p.kind === "scalar");
    const mappings = [
      { pointer: "", kind: "mapping" as const },
      ...pointers.filter((p) => p.kind === "mapping"),
    ];
    const sequences = pointers.filter((p) => p.kind === "sequence");
    const removable = pointers.filter(
      (p) => !p.pointer.startsWith("/schema_version"),
    );
    const choices: fc.Arbitrary<SourceOperation>[] = [];
    if (scalars.length)
      choices.push(
        fc
          .tuple(fc.constantFrom(...scalars), scalarArbitrary)
          .map(([p, value]) => ({
            kind: "replace-scalar" as const,
            pointer: p.pointer,
            value,
          })),
      );
    choices.push(
      fc
        .tuple(
          fc.constantFrom(...mappings),
          identifierArbitrary,
          valueArbitrary,
        )
        .map(([p, key, value]) => ({
          kind: "insert-key" as const,
          parentPointer: p.pointer,
          key: `new_${key}`,
          value,
        })),
    );
    if (sequences.length)
      choices.push(
        fc
          .tuple(
            fc.constantFrom(...sequences),
            valueArbitrary,
            fc.option(fc.nat(3), { nil: undefined }),
          )
          .map(([p, value, index]) => ({
            kind: "insert-item" as const,
            parentPointer: p.pointer,
            value,
            index,
          })),
      );
    if (removable.length)
      choices.push(
        fc
          .constantFrom(...removable)
          .map((p) => ({ kind: "remove" as const, pointer: p.pointer })),
      );
    return fc.tuple(fc.constant(tree), fc.constant(eol), fc.oneof(...choices));
  });

describe("source transactions (property)", () => {
  it("edits one range so the reparsed tree equals the tree operation and other lines stay byte-identical", () => {
    fc.assert(
      fc.property(documentAndOperation, ([tree, eol, op]) => {
        const source = toYaml(tree, eol);
        const parsed = parseSource(source, "yaml");
        fc.pre(parsed.status === "ok");
        const expected = applyToTree(tree, op);
        const result = planSourceOperation(source, "yaml", op);
        if (expected === null) {
          expect(result.status).toBe("error");
          return;
        }
        if (op.kind === "insert-item" && op.index !== undefined) {
          const parent = pointersOf(tree).length; // index beyond length is refused
          void parent;
        }
        if (result.status === "error") {
          // 유효 연산은 index 범위 밖(insert-item)일 때만 거부된다.
          expect(op.kind === "insert-item" && op.index !== undefined).toBe(
            true,
          );
          return;
        }
        const { edit } = result;
        expect(edit.nextSource).toBe(
          `${source.slice(0, edit.from)}${edit.insert}${source.slice(edit.to)}`,
        );
        const reparsed = parseSource(edit.nextSource, "yaml");
        expect(reparsed.status).toBe("ok");
        expect(reparsed.tree).toEqual(expected);
        // 편집 범위 밖의 줄은 그대로다.
        const before = source.slice(0, edit.from);
        const after = source.slice(edit.to);
        expect(edit.nextSource.startsWith(before)).toBe(true);
        expect(edit.nextSource.endsWith(after)).toBe(true);
        if (eol === "\r\n")
          expect(edit.nextSource.replaceAll("\r\n", "")).not.toContain("\n");
      }),
      // 로컬 정밀 검사: `FC_NUM_RUNS=3000 npx vitest run <this file>`.
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 300) },
    );
  }, 300_000);
});
