import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { isCollection, isNode, isPair, parseDocument, visit } from "yaml";

import {
  parseSource,
  pointerSegments,
  valueAtPointer,
} from "../../../shared/lib/yaml12";
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
        .chain(([p, key, value]) => {
          const parent = valueAtPointer(tree, p.pointer).value;
          const siblings = Object.keys(parent as Record<string, unknown>);
          // 절반은 형제 키 앞에(`before`), 절반은 마지막 키 뒤에 넣는다.
          const before = siblings.length
            ? fc.option(fc.constantFrom(...siblings), { nil: undefined })
            : fc.constant(undefined);
          return before.map((target) => ({
            kind: "insert-key" as const,
            parentPointer: p.pointer,
            key: `new_${key}`,
            value,
            ...(target === undefined ? {} : { before: target }),
          }));
        }),
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

/**
 * 주석 `# c<n>`은 주석 없는 직렬화의 n번째 줄 앞에 놓였다. 그 줄에서 시작하는 가장 얕은 pointer가
 * 주석의 소유자다(`- k: 1` 줄이면 항목 `/a/0`, `k:` 줄이면 `/k`). `remove`는 소유자가 삭제 대상
 * pointer 자신이거나 그 아래일 때만 주석을 지울 수 있고, 다른 pointer의 주석은 전부 남아야 한다
 * (P3-01 2차 리뷰 P2-R2: 관용 범위를 구현이 정한 편집 범위가 아니라 문서 구조로 정한다).
 */
const commentOwners = (tree: Record<string, unknown>): Map<string, string> => {
  const bare = toYaml(tree, "\n", []);
  const parsed = parseSource(bare, "yaml");
  if (parsed.status !== "ok") throw new Error("bare document must parse");
  const depth = (pointer: string): number => pointer.split("/").length;
  const ownerOfLine = new Map<number, string>();
  const consider = (pointer: string, line: number): void => {
    const current = ownerOfLine.get(line);
    if (current === undefined || depth(pointer) < depth(current))
      ownerOfLine.set(line, pointer);
  };
  // 주석 계획의 줄 번호는 0부터 세므로 parser의 line 기준과 무관하게 offset으로 다시 센다.
  const lineOf = (offset: number): number =>
    bare.slice(0, offset).split("\n").length - 1;
  for (const [pointer, range] of parsed.keyRanges)
    consider(pointer, lineOf(range.start.offset));
  // block 컨테이너의 value range는 첫 자식 줄에서 시작하므로 소유자 후보는 키와 시퀀스 항목뿐이다.
  for (const [pointer, range] of parsed.valueRanges)
    if (/\/\d+$/.test(pointer)) consider(pointer, lineOf(range.start.offset));
  const owners = new Map<string, string>();
  for (const [line, pointer] of ownerOfLine) owners.set(`# c${line}`, pointer);
  return owners;
};

const within = (pointer: string, subtree: string): boolean =>
  pointer === subtree || pointer.startsWith(`${subtree}/`);

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
        // 주석은 하나도 사라지지 않는다(P3-01 리뷰 P1-1·P1-2). 삭제된 subtree가 소유한 주석만 예외다.
        const before = commentLines(source);
        const kept = commentLines(edit.nextSource);
        if (op.kind !== "remove") {
          expect(kept).toEqual(before);
        } else {
          const owners = commentOwners(tree);
          const mustSurvive = before.filter((comment) => {
            const owner = owners.get(comment);
            if (owner === undefined) throw new Error(`owner of ${comment}`);
            return !within(owner, op.pointer);
          });
          expect(kept.filter((c) => mustSurvive.includes(c))).toEqual(
            mustSurvive,
          );
          expect(kept.every((c) => before.includes(c))).toBe(true);
        }
        if (eol === "\r\n")
          expect(edit.nextSource.replaceAll("\r\n", "")).not.toContain("\n");
      }),
      // 로컬 정밀 검사: `FC_NUM_RUNS=3000 npx vitest run <this file>`.
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 300) },
    );
  }, 300_000);
});

/**
 * block 문서의 비어 있지 않은 컬렉션 하나를 flow 표기(`{ … }`·`[ … ]`)로 바꾸고 닫는 괄호 뒤에 줄 끝 주석
 * `# 꼬리`를 붙인 문서와, 그 컨테이너 안을 겨냥한 연산 하나(#150 리뷰 P2-4: backlog 14·18 경로의 생성기).
 * flow 안 주석은 YAML이 거의 허용하지 않으므로 그 subtree의 주석은 지운다.
 */
const flowDocumentAndOperation = documentAndOperation
  .chain(([source, eol, tree]) => {
    const containers = pointersOf(tree).filter((p) => {
      if (p.kind === "scalar") return false;
      const value = valueAtPointer(tree, p.pointer).value;
      return Array.isArray(value)
        ? value.length > 0
        : Object.keys(value as Record<string, unknown>).length > 0;
    });
    if (containers.length === 0) return fc.constant(null);
    return fc.constantFrom(...containers).map((container) => {
      const doc = parseDocument(source);
      const path = pointerSegments(container.pointer).map((segment, index, all) => {
        const parent = valueAtPointer(
          tree,
          all.slice(0, index).length === 0
            ? ""
            : `/${all.slice(0, index).join("/")}`,
        ).value;
        return Array.isArray(parent) ? Number(segment) : segment;
      });
      const node = doc.getIn(path, true);
      if (!isCollection(node)) return null;
      // 고른 컨테이너 자신의 앞 주석은 남긴다 — 그래야 yaml이 flow 값을 키와 다른 줄에 찍는 표본(키 줄과
      // 값 줄 사이 주석, #150 P2-1 경로)이 생성된다(#150 재검토 P2-7).
      const ownCommentBefore = node.commentBefore;
      visit(node, (_key, child) => {
        if (isNode(child) || isPair(child)) {
          if (isNode(child)) {
            child.commentBefore = null;
            child.comment = null;
            child.spaceBefore = false;
          }
          if (isCollection(child)) child.flow = true;
        }
      });
      node.commentBefore = ownCommentBefore;
      node.flow = true;
      node.comment = " 꼬리"; // yaml은 `#` 바로 뒤에 붙이므로 앞 공백을 준다.
      const flowSource = doc.toString({ lineWidth: 0 }).replaceAll("\n", eol);
      const parsed = parseSource(flowSource, "yaml");
      if (parsed.status !== "ok") return null;
      return { source: flowSource, eol, tree: parsed.tree, container: container.pointer };
    });
  })
  .filter((doc) => doc !== null)
  .chain((doc) => {
    const { source, eol, tree, container } = doc!;
    const inside = pointersOf(tree).filter((p) => p.pointer.startsWith(`${container}/`));
    const mappings = pointersOf(tree).filter(
      (p) => p.kind === "mapping" && (p.pointer === container || p.pointer.startsWith(`${container}/`)),
    );
    const sequences = pointersOf(tree).filter(
      (p) => p.kind === "sequence" && (p.pointer === container || p.pointer.startsWith(`${container}/`)),
    );
    const choices: fc.Arbitrary<SourceOperation>[] = [];
    if (mappings.length)
      choices.push(
        fc.tuple(fc.constantFrom(...mappings), identifierArbitrary, valueArbitrary).map(
          ([p, key, value]) => ({
            kind: "insert-key" as const,
            parentPointer: p.pointer,
            key: `new_${key}`,
            value,
          }),
        ),
      );
    if (sequences.length)
      choices.push(
        fc.tuple(fc.constantFrom(...sequences), valueArbitrary).map(([p, value]) => ({
          kind: "insert-item" as const,
          parentPointer: p.pointer,
          value,
        })),
      );
    if (inside.length)
      choices.push(
        fc.constantFrom(...inside).map((p) => ({ kind: "remove" as const, pointer: p.pointer })),
      );
    return fc.tuple(
      fc.constant(source),
      fc.constant(eol),
      fc.constant(tree),
      fc.constant(container),
      fc.oneof(...choices),
    );
  });

describe("flow containers (property)", () => {
  it("rewrites the flow container only: reparsed tree equals the tree operation, lines outside survive byte for byte, the trailing comment survives", () => {
    fc.assert(
      fc.property(flowDocumentAndOperation, ([source, eol, tree, container, op]) => {
        const expected = applyToTree(tree, op);
        expect(expected, `${op.kind} must be valid`).not.toBeNull();
        const result = planSourceOperation(source, "yaml", op);
        expect(result.status, JSON.stringify({ op, source })).toBe("ok");
        if (result.status !== "ok") return;
        const { edit } = result;
        const reparsed = parseSource(edit.nextSource, "yaml");
        expect(reparsed.status).toBe("ok");
        expect(reparsed.tree).toEqual(expected);
        // 가장 바깥 flow 컨테이너(여기서는 `container`)의 값 줄 밖은 바이트 동일하다.
        const parsed = parseSource(source, "yaml");
        if (parsed.status !== "ok") throw new Error("flow source must parse");
        const range = parsed.valueRanges.get(container)!;
        const lines = source.split(eol);
        const lineOf = (offset: number): number =>
          source.slice(0, offset).split(eol).length - 1;
        const first = lineOf(range.start.offset);
        const last = lineOf(range.end.offset);
        const nextLines = edit.nextSource.split(eol);
        expect(nextLines.slice(0, first)).toEqual(lines.slice(0, first));
        const tailCount = lines.length - last - 1;
        expect(nextLines.slice(nextLines.length - tailCount)).toEqual(
          lines.slice(lines.length - tailCount),
        );
        // 컨테이너 뒤 줄 끝 주석은 남는다(#149 P2-4).
        expect(edit.nextSource).toContain("# 꼬리");
        // flow 밖의 주석은 하나도 사라지지 않는다.
        const before = commentLines(source);
        const kept = commentLines(edit.nextSource);
        expect(kept.filter((c) => before.includes(c))).toEqual(before);
        if (eol === "\r\n")
          expect(edit.nextSource.replaceAll("\r\n", "")).not.toContain("\n");
      }),
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 200) },
    );
  }, 300_000);
});
