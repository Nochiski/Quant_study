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

/** CI가 보고한 반례 seed를 로컬에서 고정한다(#197). 비우면 fast-check가 매번 새 seed를 고른다. */
const fixedSeed = process.env.FC_SEED ? { seed: Number(process.env.FC_SEED) } : {};

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
      // 로컬 정밀 검사: `FC_NUM_RUNS=3000 npx vitest run <this file>`. CI 반례 재현: `FC_SEED=<seed>`.
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 300), ...fixedSeed },
    );
  }, 300_000);
});

/**
 * block 문서의 비어 있지 않은 컬렉션 하나를 flow 표기(`{ … }`·`[ … ]`)로 바꾸고 닫는 괄호 뒤에 줄 끝 주석
 * `# 꼬리`를 붙인 문서와, 그 컨테이너 안을 겨냥한 연산 하나(#150 리뷰 P2-4: backlog 14·18 경로의 생성기).
 * flow 안 주석은 YAML이 거의 허용하지 않으므로 그 subtree의 주석은 지운다.
 */
const flowDocument = documentAndOperation
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
  .map((doc) => doc!);

const flowDocumentAndOperation = flowDocument.chain(
  ({ source, eol, tree, container }) => {
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
  },
);

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
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 200), ...fixedSeed },
    );
  }, 300_000);
});

/**
 * 새 두 property가 함께 보는 편집 불변식: tree 동치, 편집 범위 밖 줄의 바이트 동일, 문서 폭(2칸)의 배수인
 * 들여쓰기, 주석은 생기지 않고 삭제가 아니면 사라지지도 않음, CRLF 유지. 편집을 돌려준다.
 */
const expectLawfulEdit = (
  source: string,
  eol: "\n" | "\r\n",
  tree: Record<string, unknown>,
  op: SourceOperation,
) => {
  const expected = applyToTree(tree, op);
  expect(expected, `${op.kind} must be valid`).not.toBeNull();
  const result = planSourceOperation(source, "yaml", op);
  expect(result.status, JSON.stringify({ op, source })).toBe("ok");
  if (result.status !== "ok") return null;
  const { edit } = result;
  const reparsed = parseSource(edit.nextSource, "yaml");
  expect(reparsed.status).toBe("ok");
  expect(reparsed.tree).toEqual(expected);
  const lines = source.split(eol);
  const nextLines = edit.nextSource.split(eol);
  const lineOf = (offset: number): number =>
    source.slice(0, offset).split(eol).length - 1;
  const firstTouched = lineOf(edit.from);
  const tailCount = lines.length - lineOf(Math.max(edit.from, edit.to)) - 1;
  expect(nextLines.slice(0, firstTouched)).toEqual(lines.slice(0, firstTouched));
  expect(nextLines.slice(nextLines.length - tailCount)).toEqual(
    lines.slice(lines.length - tailCount),
  );
  // 생성 문서는 2칸 폭이다. 빈 컨테이너를 다음 줄로 옮길 때 5칸이 되던 결함(#199)을 막는다.
  for (const line of nextLines)
    expect(
      (line.length - line.trimStart().length) % 2,
      JSON.stringify({ op, source, line }),
    ).toBe(0);
  const before = commentLines(source);
  const kept = commentLines(edit.nextSource);
  if (op.kind === "remove") expect(kept.every((c) => before.includes(c))).toBe(true);
  else expect(kept).toEqual(before);
  if (eol === "\r\n")
    expect(edit.nextSource.replaceAll("\r\n", "")).not.toContain("\n");
  return edit;
};

const ancestorOrSelf = (pointer: string, of: string): boolean =>
  of === pointer || of.startsWith(`${pointer}/`);

/**
 * flow 컨테이너 **자신과 그 바깥** block 조상을 겨냥한 연산(#199). 위 flow property는 컨테이너 안만 겨냥해,
 * flow 컨테이너가 부모의 마지막 내용일 때 부모를 비우거나 그 옆에 넣는 경로(`- - [a, b]`의 유일 항목 삭제)를
 * 뽑지 못했다.
 */
const flowNeighbourOperation = flowDocument.chain(
  ({ source, eol, tree, container }) => {
    const pointers = pointersOf(tree).filter(
      (p) => !p.pointer.startsWith("/schema_version"),
    );
    const outer = pointers.filter((p) => ancestorOrSelf(p.pointer, container));
    const strictOuter = outer.filter((p) => p.pointer !== container);
    const sequences = strictOuter.filter((p) => p.kind === "sequence");
    const mappings = [
      { pointer: "", kind: "mapping" as const },
      ...strictOuter.filter((p) => p.kind === "mapping"),
    ];
    const choices: fc.Arbitrary<SourceOperation>[] = [
      fc
        .constantFrom(...outer)
        .map((p) => ({ kind: "remove" as const, pointer: p.pointer })),
      fc
        .tuple(fc.constantFrom(...mappings), identifierArbitrary, valueArbitrary)
        .map(([p, key, value]) => ({
          kind: "insert-key" as const,
          parentPointer: p.pointer,
          key: `new_${key}`,
          value,
        })),
    ];
    if (sequences.length)
      choices.push(
        fc
          .tuple(fc.constantFrom(...sequences), valueArbitrary)
          .chain(([p, value]) => {
            const items = valueAtPointer(tree, p.pointer).value as unknown[];
            return fc
              .option(fc.integer({ min: 0, max: items.length }), { nil: undefined })
              .map((index) => ({
                kind: "insert-item" as const,
                parentPointer: p.pointer,
                value,
                index,
              }));
          }),
      );
    return fc.tuple(
      fc.constant(source),
      fc.constant(eol),
      fc.constant(tree),
      fc.oneof(...choices),
    );
  },
);


/**
 * 항목 값이 `-` 줄 다음 줄에서 시작하게 바꾸는 네 모양(#199, #206 리뷰 P3-1). `- # 대시` 한 가지로는 `-`와 내용
 * 사이의 빈 줄·자기 줄 주석을 건너는 `-` 탐색과 bare `-` 경로를 뽑지 못했다.
 */
const DASH_VARIANTS = ["comment", "bare", "blank", "own-comment"] as const;
type DashVariant = (typeof DASH_VARIANTS)[number];

const dashVariantText = (
  head: string,
  indent: string,
  eol: string,
  variant: DashVariant,
): string => {
  switch (variant) {
    case "comment":
      return `${head} # 대시${eol}${indent}`;
    case "bare":
      return `${head}${eol}${indent}`;
    case "blank":
      return `${head}${eol}${eol}${indent}`;
    case "own-comment":
      return `${head} # 대시${eol}${indent}# 속${eol}${indent}`;
  }
};

/**
 * 루트 스칼라와 시퀀스 키만 있는 문서. 항목은 스칼라, 스칼라 시퀀스, 스칼라 mapping이고 안쪽 시퀀스는 한 번 더 겹칠
 * 수 있다(`- - - x`). 시퀀스 항목 아래 mapping 키는 폭 판정의 키 분기가 건너뛰므로 폭은 항목 `-` 열로 정해진다.
 */
const sequenceOnlyDocumentArbitrary = (() => {
  const leafItem = fc.oneof(
    scalarArbitrary,
    fc.array(scalarArbitrary, { minLength: 1, maxLength: 3 }),
    fc.dictionary(identifierArbitrary, scalarArbitrary, { minKeys: 1, maxKeys: 3 }),
  );
  const item = fc.oneof(
    leafItem,
    fc.array(fc.array(scalarArbitrary, { minLength: 1, maxLength: 2 }), {
      minLength: 1,
      maxLength: 2,
    }),
  );
  return fc
    .tuple(
      fc.dictionary(identifierArbitrary, scalarArbitrary, { minKeys: 1, maxKeys: 2 }),
      fc.dictionary(identifierArbitrary, fc.array(item, { minLength: 1, maxLength: 3 }), {
        minKeys: 1,
        maxKeys: 2,
      }),
    )
    .map(([leaf, rest]) => ({ schema_version: "1.1", ...leaf, ...rest }));
})();

/** `-`만(겹침 포함) 있고 줄 끝 주석만 올 수 있는 줄. 그룹 1은 마지막 `-` 앞까지다. */
const DASH_ONLY_LINE = /^( *(?:- +)*)-(?: +#.*)?$/;

/**
 * 시퀀스 항목(비어 있지 않은 block 컨테이너) 하나의 값을 `-` 줄 다음 줄로 내린 문서와, 그 항목 안이나 그 항목까지의
 * 조상을 겨냥한 연산(#199). 내용 줄은 원래 내용 열에 두므로 들여쓰기는 그대로 2칸 폭이다. 삽입은 맨 앞
 * (`index: 0`, 첫 키 앞 `before`)도 뽑는다 — `-` 줄 끝을 앵커로 쓰는 `parentLineEnd` 경로다(#206 리뷰 P3-1).
 */
const dashCommentDocumentAndOperation = fc
  .tuple(
    // 절반은 시퀀스만 있는 문서다: 중첩 mapping 키가 없어야 `detectIndentUnit`의 시퀀스 분기(항목 `-` 열)가 폭을
    // 정한다. 일반 문서는 거의 항상 키 분기가 먼저 이겨 그 분기의 회귀를 놓쳤다(#206 리뷰 P3-1).
    fc.oneof(strategyDocumentArbitrary(), sequenceOnlyDocumentArbitrary),
    fc.constantFrom("\n", "\r\n") as fc.Arbitrary<"\n" | "\r\n">,
    commentPlanArbitrary(),
  )
  .chain(([generated, eol, comments]) => {
    const source = toYaml(generated, eol, comments);
    const parsed = parseSource(source, "yaml");
    if (parsed.status !== "ok") return fc.constant(null);
    const tree = parsed.tree;
    const items = pointersOf(tree).filter((p) => {
      if (p.kind === "scalar" || !/\/\d+$/.test(p.pointer)) return false;
      const value = valueAtPointer(tree, p.pointer).value;
      const size = Array.isArray(value)
        ? value.length
        : Object.keys(value as Record<string, unknown>).length;
      const range = parsed.valueRanges.get(p.pointer);
      if (size === 0 || range === undefined) return false;
      const lineStart = source.lastIndexOf("\n", range.start.offset - 1) + 1;
      return /^ *(?:- +)+$/.test(source.slice(lineStart, range.start.offset));
    });
    if (items.length === 0) return fc.constant(null);
    return fc
      .tuple(fc.constantFrom(...items), fc.constantFrom(...DASH_VARIANTS))
      .map(([item, variant]) => {
        const range = parsed.valueRanges.get(item.pointer)!;
        const head = source.slice(0, range.start.offset).replace(/ +$/, "");
        const indent = " ".repeat(range.start.column);
        const text = `${dashVariantText(head, indent, eol, variant)}${source.slice(range.start.offset)}`;
        const reparsed = parseSource(text, "yaml");
        if (reparsed.status !== "ok" || JSON.stringify(reparsed.tree) !== JSON.stringify(tree))
          return null;
        return { source: text, eol, tree, item: item.pointer, variant };
      });
  })
  .filter((doc) => doc !== null)
  .chain((doc) => {
    const { source, eol, tree, item, variant } = doc!;
    const pointers = pointersOf(tree).filter(
      (p) => !p.pointer.startsWith("/schema_version"),
    );
    const inside = pointers.filter((p) => ancestorOrSelf(item, p.pointer));
    const choices: fc.Arbitrary<SourceOperation>[] = [
      fc
        .constantFrom(
          ...pointers.filter(
            (p) => ancestorOrSelf(item, p.pointer) || ancestorOrSelf(p.pointer, item),
          ),
        )
        .map((p) => ({ kind: "remove" as const, pointer: p.pointer })),
    ];
    const mappings = inside.filter((p) => p.kind === "mapping");
    const sequences = inside.filter((p) => p.kind === "sequence");
    if (mappings.length)
      choices.push(
        fc
          .tuple(fc.constantFrom(...mappings), identifierArbitrary, valueArbitrary)
          .chain(([p, key, value]) => {
            const siblings = Object.keys(
              valueAtPointer(tree, p.pointer).value as Record<string, unknown>,
            );
            // 첫 키 앞(`before`)을 자주 뽑는다: 항목 자신이면 `-` 줄 끝이 앵커다.
            const before = siblings.length
              ? fc.option(fc.constantFrom(...siblings), { nil: undefined, freq: 2 })
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
        fc.tuple(fc.constantFrom(...sequences), valueArbitrary).chain(([p, value]) => {
          const length = (valueAtPointer(tree, p.pointer).value as unknown[]).length;
          // 맨 앞(`index: 0`)을 따로 크게 뽑는다.
          return fc
            .oneof(
              fc.constant(0),
              fc.integer({ min: 0, max: length }),
              fc.constant(undefined),
            )
            .map((index) => ({
              kind: "insert-item" as const,
              parentPointer: p.pointer,
              value,
              ...(index === undefined ? {} : { index }),
            }));
        }),
      );
    return fc.tuple(
      fc.constant(source),
      fc.constant(eol),
      fc.constant(tree),
      fc.constant(item),
      fc.constant(variant),
      fc.oneof(...choices),
    );
  });

describe("flow containers as the target or around it (property, #199)", () => {
  it("removes or inserts around a flow container with the edit invariants", () => {
    fc.assert(
      fc.property(flowNeighbourOperation, ([source, eol, tree, op]) => {
        expectLawfulEdit(source, eol, tree, op);
      }),
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 200), ...fixedSeed },
    );
  }, 300_000);
});

describe("items whose content starts below the dash line (property, #199)", () => {
  it("edits inside or around the item with the edit invariants, keeps the dash-line comment and puts an emptied container at the dash column plus the unit", () => {
    fc.assert(
      fc.property(
        dashCommentDocumentAndOperation,
        ([source, eol, tree, item, variant, op]) => {
          const edit = expectLawfulEdit(source, eol, tree, op);
          if (edit === null) return;
          const removesItem = op.kind === "remove" && ancestorOrSelf(op.pointer, item);
          if (!removesItem && (variant === "comment" || variant === "own-comment"))
            expect(edit.nextSource).toContain("# 대시");
          // `-`만 있는 줄 바로 아래의 빈 `[]`·`{}`는 그 `-` 열 + 문서 폭(2)에 온다. 짝수 검사만으로는 폭을 4로
          // 잘못 잡아 6칸에 놓는 회귀를 놓친다(#206 리뷰 P3-1: `detectIndentUnit`).
          const lines = edit.nextSource.split(eol);
          lines.forEach((line, index) => {
            if (!/^ *(?:\[\]|\{\})$/.test(line)) return;
            const previous = lines
              .slice(0, index)
              .reverse()
              .find((candidate) => candidate.trim() !== "");
            const dashLine = previous === undefined ? null : DASH_ONLY_LINE.exec(previous);
            if (dashLine === null) return;
            expect(
              line.length - line.trimStart().length,
              JSON.stringify({ op, source, line }),
            ).toBe(dashLine[1]!.length + 2);
          });
        },
      ),
      { numRuns: Number(process.env.FC_NUM_RUNS ?? 200), ...fixedSeed },
    );
  }, 300_000);
});
