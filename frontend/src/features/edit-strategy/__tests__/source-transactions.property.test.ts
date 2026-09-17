import fc from "fast-check";
import { describe, expect, it } from "vitest";

import { parseSource, valueAtPointer } from "../../../shared/lib/yaml12";
import {
  commentPlanArbitrary,
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

/** 문서 텍스트(주석 포함)와 그 텍스트를 parse한 tree에 유효한 연산 하나. */
const documentAndOperation = fc
  .tuple(
    strategyDocumentArbitrary(),
    fc.constantFrom("\n", "\r\n") as fc.Arbitrary<"\n" | "\r\n">,
    commentPlanArbitrary(),
  )
  .map(([tree, eol, comments]) => {
    const source = toYaml(tree, eol, comments);
    const parsed = parseSource(source, "yaml");
    return { source, eol, tree: parsed.status === "ok" ? parsed.tree : null };
  })
  .filter((doc) => doc.tree !== null)
  .chain((doc) => {
    const tree = doc.tree!;
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
          .tuple(fc.constantFrom(...sequences), valueArbitrary)
          .chain(([p, value]) => {
            const items = valueAtPointer(tree, p.pointer).value;
            const length = Array.isArray(items) ? items.length : 0;
            return fc
              .option(fc.integer({ min: 0, max: length }), { nil: undefined })
              .map((index) => ({
                kind: "insert-item" as const,
                parentPointer: p.pointer,
                value,
                index,
              }));
          }),
      );
    if (removable.length)
      choices.push(
        fc
          .constantFrom(...removable)
          .map((p) => ({ kind: "remove" as const, pointer: p.pointer })),
      );
    return fc.tuple(
      fc.constant(doc.source),
      fc.constant(doc.eol),
      fc.constant(tree),
      fc.oneof(...choices),
    );
  });

/** 독립 주석 줄. `- key:` 첫 키 삭제 뒤에는 주석이 `- # c` 모양으로 남을 수 있어 `-` 표식을 건너뛴다. */
const commentLines = (text: string): string[] =>
  text
    .split(/\r?\n/)
    .filter((line) => /^\s*(?:-\s+)*#/.test(line))
    .map((line) => line.replace(/^\s*(?:-\s+)*/, ""));

describe("source transactions (property)", () => {
  it("edits one line range so the reparsed tree equals the tree operation, other lines and all comments survive", () => {
    fc.assert(
      fc.property(documentAndOperation, ([source, eol, tree, op]) => {
        const expected = applyToTree(tree, op);
        expect(
          expected,
          `${op.kind} must be valid for the generated tree`,
        ).not.toBeNull();
        const result = planSourceOperation(source, "yaml", op);
        expect(result.status, JSON.stringify({ op, source })).toBe("ok");
        if (result.status !== "ok") return;
        const { edit } = result;
        expect(edit.nextSource).toBe(
          `${source.slice(0, edit.from)}${edit.insert}${source.slice(edit.to)}`,
        );
        const reparsed = parseSource(edit.nextSource, "yaml");
        expect(reparsed.status).toBe("ok");
        expect(reparsed.tree).toEqual(expected);
        // 편집 범위가 걸친 줄 밖의 줄은 줄 단위로 바이트 동일하다(P3-01 리뷰 P2-2).
        const lines = source.split(eol);
        const nextLines = edit.nextSource.split(eol);
        const lineOf = (offset: number): number =>
          source.slice(0, offset).split(eol).length - 1;
        const firstTouched = lineOf(edit.from);
        const lastTouched = lineOf(Math.max(edit.from, edit.to));
        expect(nextLines.slice(0, firstTouched)).toEqual(
          lines.slice(0, firstTouched),
        );
        const tailCount = lines.length - lastTouched - 1;
        expect(nextLines.slice(nextLines.length - tailCount)).toEqual(
          lines.slice(lines.length - tailCount),
        );
        // 주석은 하나도 사라지지 않는다(P3-01 리뷰 P1-1·P1-2). 삭제된 subtree 안의 주석만 예외다.
        if (op.kind !== "remove") {
          expect(commentLines(edit.nextSource)).toEqual(commentLines(source));
        } else {
          const kept = commentLines(edit.nextSource);
          const removedRange = source.slice(edit.from, edit.to);
          const dropped = commentLines(removedRange);
          expect(kept.length + dropped.length).toBe(
            commentLines(source).length,
          );
        }
        if (eol === "\r\n")
          expect(edit.nextSource.replaceAll("\r\n", "")).not.toContain("\n");
      }),
      // 로컬 정밀 검사: `FC_NUM_RUNS=3000 npx vitest run <this file>`.
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 300) },
    );
  }, 300_000);
});
