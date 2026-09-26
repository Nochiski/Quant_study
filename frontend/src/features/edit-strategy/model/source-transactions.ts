import { stringify } from "yaml";

import {
  escapePointerSegment,
  parseSource,
  pointerSegments,
  valueAtPointer,
  type ParsedSource,
  type SourceFormat,
} from "../../../shared/lib/yaml12";

/**
 * source 트랜잭션 원시 연산 4종(spec D5, WORKFLOW P3-01). 편집기와 React를 모르는 순수 함수다.
 *
 * 정본은 YAML 텍스트 하나다. 각 연산은 pointer가 가리키는 **기존 범위**(parse가 준 key/value range)
 * 하나만 바꾸는 최소 범위 편집(`PlannedEdit`)을 만들고, 결과 텍스트를 같은 parser로 다시 읽어
 * (1) 파싱이 되고 (2) tree가 `applyToTree`의 결과와 같을 때만 돌려준다. 프론트가 문서를 직렬화해
 * 통째로 갈아끼우는 일은 없다(주석·순서·따옴표는 범위 밖에서 그대로다). 예외는 flow 표기 컨테이너
 * (`{ … }`·`[ … ]`)에 키·항목을 넣거나 거기서 지울 때다: block은 flow 안에 올 수 없으므로 가장 바깥 flow 컨테이너 전체를
 * 다시 직렬화해 교체한다 — 그 범위 안의 따옴표·숫자 표기는 정규화되고, 컨테이너 뒤 줄 끝 주석은 `key:`/`-`
 * 줄에 남긴다(Phase 5 backlog 14).
 *
 * 값이 없는 키(`factors:` → null)는 삽입 연산의 부모로 쓰일 때 빈 컨테이너로 본다(insert-key면 `{}`,
 * insert-item이면 `[]`). 내용이 전혀 없는 문서(빈 줄·주석뿐)는 루트 insert-key에 한해 빈 mapping으로
 * 본다: 새 문서와 스니펫 첫 삽입이 이 경로다.
 */
export type Scalar = string | number | boolean | null;

export type SourceOperation =
  | { kind: "replace-scalar"; pointer: string; value: Scalar }
  | {
      kind: "insert-key";
      parentPointer: string;
      key: string;
      value: unknown;
      /** 이 형제 키 앞에 넣는다(스니펫이 커서 위치를 옮길 때). 없으면 마지막 키 뒤. */
      before?: string;
    }
  | {
      kind: "insert-item";
      parentPointer: string;
      value: unknown;
      index?: number;
    }
  | { kind: "remove"; pointer: string };

export type PlannedEdit = {
  from: number;
  to: number;
  insert: string;
  nextSource: string;
  selection: { from: number; to: number };
};

export type PlanFailure =
  | "yaml-only"
  | "not-found"
  | "exists"
  | "not-scalar"
  | "not-mapping"
  | "not-sequence"
  | "parse";

export type PlanResult =
  | { status: "ok"; edit: PlannedEdit }
  | { status: "error"; reason: PlanFailure };

export type Eol = "\n" | "\r\n";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isScalar = (value: unknown): value is Scalar =>
  value === null ||
  typeof value === "string" ||
  typeof value === "number" ||
  typeof value === "boolean";

const deepEqual = (left: unknown, right: unknown): boolean =>
  JSON.stringify(left) === JSON.stringify(right);

/** tree 쪽 같은 연산. 결과가 텍스트 편집을 다시 읽은 tree와 같아야 한다(property test·preflight). */
export const applyToTree = (
  tree: Record<string, unknown>,
  op: SourceOperation,
): Record<string, unknown> | null => {
  const next = structuredClone(tree);
  const locate = (pointer: string): { parent: unknown; key: string } | null => {
    const segments = pointerSegments(pointer);
    if (segments.length === 0) return null;
    let parent: unknown = next;
    for (const segment of segments.slice(0, -1)) {
      if (Array.isArray(parent)) parent = parent[Number(segment)];
      else if (isRecord(parent)) parent = parent[segment];
      else return null;
    }
    return { parent, key: segments[segments.length - 1]! };
  };
  const at = (pointer: string): unknown => {
    let current: unknown = next;
    for (const segment of pointerSegments(pointer)) {
      if (Array.isArray(current)) current = current[Number(segment)];
      else if (isRecord(current)) current = current[segment];
      else return undefined;
    }
    return current;
  };
  const setAt = (pointer: string, value: unknown): boolean => {
    const found = locate(pointer);
    if (found === null) return false;
    if (Array.isArray(found.parent)) found.parent[Number(found.key)] = value;
    else if (isRecord(found.parent) && Object.hasOwn(found.parent, found.key))
      found.parent[found.key] = value;
    else return false;
    return true;
  };
  switch (op.kind) {
    case "replace-scalar": {
      const found = locate(op.pointer);
      if (found === null) return null;
      if (Array.isArray(found.parent)) {
        const index = Number(found.key);
        if (!isScalar(found.parent[index])) return null;
        found.parent[index] = op.value;
      } else if (
        isRecord(found.parent) &&
        Object.hasOwn(found.parent, found.key)
      ) {
        if (!isScalar(found.parent[found.key])) return null;
        found.parent[found.key] = op.value;
      } else return null;
      return next;
    }
    case "insert-key": {
      const parent = at(op.parentPointer);
      if (parent === null && op.parentPointer !== "") {
        // 값 없는 키를 빈 mapping으로 확장한다(형제가 없으니 `before`는 거부, 텍스트 쪽과 동일).
        if (op.before !== undefined) return null;
        if (!setAt(op.parentPointer, { [op.key]: structuredClone(op.value) }))
          return null;
        return next;
      }
      if (!isRecord(parent) || Object.hasOwn(parent, op.key)) return null;
      if (op.before === undefined) {
        parent[op.key] = structuredClone(op.value);
        return next;
      }
      // 형제 순서를 텍스트와 같게 유지한다(preflight의 tree 비교는 키 순서를 본다).
      if (!Object.hasOwn(parent, op.before)) return null;
      const entries = Object.entries(parent);
      for (const key of Object.keys(parent)) delete parent[key];
      for (const [key, value] of entries) {
        if (key === op.before) parent[op.key] = structuredClone(op.value);
        parent[key] = value;
      }
      return next;
    }
    case "insert-item": {
      const parent = at(op.parentPointer);
      if (parent === null && op.parentPointer !== "") {
        if ((op.index ?? 0) !== 0) return null;
        if (!setAt(op.parentPointer, [structuredClone(op.value)])) return null;
        return next;
      }
      if (!Array.isArray(parent)) return null;
      const index = op.index ?? parent.length;
      if (index < 0 || index > parent.length) return null;
      parent.splice(index, 0, structuredClone(op.value));
      return next;
    }
    case "remove": {
      const found = locate(op.pointer);
      if (found === null) return null;
      if (Array.isArray(found.parent)) {
        const index = Number(found.key);
        if (index >= found.parent.length) return null;
        found.parent.splice(index, 1);
      } else if (
        isRecord(found.parent) &&
        Object.hasOwn(found.parent, found.key)
      ) {
        delete found.parent[found.key];
      } else return null;
      return next;
    }
  }
};

/** 문서의 줄 종결자: 혼재하면 다수결(P3-01 리뷰 P2-9). */
export const detectEol = (source: string): Eol => {
  const crlf = source.split("\r\n").length - 1;
  const lf = source.split("\n").length - 1 - crlf;
  return crlf > lf ? "\r\n" : "\n";
};

/**
 * 문서의 들여쓰기 폭. mapping 아래 첫 중첩 키의 열(부모 키 열 기준)을 쓰고, 그런 키가 없으면 첫
 * 시퀀스 항목의 `-` 열을 쓴다. 둘 다 없으면 2.
 */
const detectIndentUnit = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
): number => {
  for (const [pointer, range] of parsed.keyRanges) {
    const segments = pointerSegments(pointer);
    if (segments.length < 2 || /^\d+$/.test(segments[segments.length - 2]!))
      continue;
    const parentPointer = parentPointerOf(pointer);
    const parentKey = parsed.keyRanges.get(parentPointer);
    // flow `{ a: 1 }` 안의 키(부모 키와 같은 줄이거나 다음 줄의 `{ … }`)는 들여쓰기 근거가 아니다(backlog 14).
    if (
      (parentKey !== undefined && parentKey.start.line === range.start.line) ||
      isFlowContainer(source, parsed, parentPointer)
    )
      continue;
    const parentColumn = parentKey?.start.column ?? 0;
    if (range.start.column > parentColumn)
      return range.start.column - parentColumn;
  }
  for (const [pointer, range] of parsed.valueRanges) {
    const segments = pointerSegments(pointer);
    if (segments.length < 2 || segments[segments.length - 1] !== "0") continue;
    const parentColumn =
      parsed.keyRanges.get(parentPointerOf(pointer))?.start.column ?? 0;
    const lineStart = lineStartOf(source, range.start.offset);
    const dash = source.slice(lineStart).search(/\S|$/);
    if (dash > parentColumn) return dash - parentColumn;
  }
  return 2;
};

const lineStartOf = (source: string, offset: number): number =>
  source.lastIndexOf("\n", Math.max(0, offset - 1)) + 1;

/** `offset`이 속한 줄의 끝(EOL 직전). */
const lineEndOf = (source: string, offset: number): number => {
  const newline = source.indexOf("\n", offset);
  if (newline < 0) return source.length;
  return source[newline - 1] === "\r" ? newline - 1 : newline;
};

/** 시퀀스 항목 값 앞의 `-` offset. `- - x`처럼 겹친 항목도 자기 `-`만 잡는다. 없으면 null. */
const dashOffsetOf = (source: string, valueStart: number): number | null => {
  let cursor = valueStart - 1;
  while (cursor >= 0 && source[cursor] === " ") cursor -= 1;
  return cursor >= 0 && source[cursor] === "-" ? cursor : null;
};

/** offset 앞의 같은 줄 내용이 `- `(겹침 포함)뿐인가: 시퀀스 항목 첫 줄에 붙어 있는 키/값. */
const onDashLine = (source: string, offset: number): boolean =>
  /^ *(?:- +)+$/.test(source.slice(lineStartOf(source, offset), offset));

const indentLines = (fragment: string, indent: string, eol: Eol): string =>
  fragment
    .split("\n")
    .map((line, index) => (index === 0 ? line : `${indent}${line}`))
    .join(eol);

const yamlBlock = (value: unknown): string =>
  stringify(value, { lineWidth: 0 }).replace(/\n$/, "");

/** 스칼라 한 줄 literal. 줄바꿈이 있는 문자열은 JSON 따옴표 문자열(유효한 YAML 1.2)로 한 줄에 둔다. */
const scalarLiteral = (value: Scalar): string => {
  if (typeof value === "string" && /[\r\n]/.test(value))
    return JSON.stringify(value);
  const dumped = yamlBlock(value);
  return dumped.includes("\n") ? JSON.stringify(value) : dumped;
};

const valueAt = (tree: Record<string, unknown>, pointer: string): unknown =>
  valueAtPointer(tree, pointer).value;

const childKeyColumn = (
  parsed: Extract<ParsedSource, { status: "ok" }>,
  parentPointer: string,
  parent: Record<string, unknown>,
): number | null => {
  for (const key of Object.keys(parent)) {
    const range = parsed.keyRanges.get(
      `${parentPointer}/${escapePointerSegment(key)}`,
    );
    if (range) return range.start.column;
  }
  return null;
};

/** pointer의 값이 flow 컬렉션(`{ … }`·`[ … ]`)으로 적혀 있는가. 범위 시작 글자로 판정한다. */
const isFlowContainer = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  pointer: string,
): boolean => {
  const range = parsed.valueRanges.get(pointer);
  if (range === undefined) return false;
  const first = source[range.start.offset];
  return first === "{" || first === "[";
};

/**
 * `pointer`에서 위로 올라가며 flow 컬렉션이 이어지는 가장 바깥 pointer. `pointer` 자신이 flow가 아니면 null.
 * block 컬렉션은 flow 안에 들어갈 수 없으므로, flow 안에 무언가를 넣으려면 여기서부터 block으로 다시 써야 한다.
 */
const topmostFlowAncestor = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  pointer: string,
): string | null => {
  if (!isFlowContainer(source, parsed, pointer)) return null;
  let top = pointer;
  while (top !== "") {
    const parent = parentPointerOf(top);
    if (!isFlowContainer(source, parsed, parent)) break;
    top = parent;
  }
  return top;
};

/**
 * flow 컬렉션에 키·항목 넣기·지우기(Phase 5 감사 backlog 14·18): 바깥쪽 flow 컨테이너 전체를 연산 적용 뒤의
 * 값으로 block 직렬화해 그 범위(`key:` 뒤 또는 `-` 뒤부터 값 끝까지)에 교체한다. 빈 `{}`·`[]`와 `{ a: 1 }`·
 * `[x, y]` 같은 한 줄 표기를 같은 규칙으로 연다. 지워서 비면 `{}`/`[]`를 그 자리에 둔다(루트 flow 포함 — 빈
 * 문서는 parse 결과가 null이라 tree 동치를 만족할 수 없다). flow 안 주석은 YAML이 거의 허용하지 않으며
 * 보존하지 않는다; 컨테이너 뒤 줄 끝 주석과 `key:`/`-` 줄·값 줄 사이의 자기 줄 주석은 남긴다 — 결과는
 * preflight(tree 동치)가 검증한다.
 */
const replaceFlowContainer = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: SourceOperation,
  top: string,
  eol: Eol,
  unit: number,
): Plan | PlanFailure => {
  const next = applyToTree(parsed.tree, op);
  if (next === null) return "not-found";
  const value = valueAt(next, top);
  const block = yamlBlock(value);
  const range = parsed.valueRanges.get(top)!;
  if (block === "{}" || block === "[]") {
    // 삭제로 컨테이너가 비면 block으로 열 것이 없다 — flow 빈 컨테이너를 그 자리에 둔다(backlog 18). 루트도
    // 같다: block 루트의 "비우지 않는다"는 마지막 키 삭제를 거부하는 규칙인데, flow 루트는 `{}`로 남아
    // parse가 되므로 거부할 이유가 없다(#150 리뷰 P2-2).
    return {
      from: range.start.offset,
      to: range.end.offset,
      insert: block,
      cursor: range.start.offset + block.length,
    };
  }
  // 닫는 `}`·`]` 뒤 같은 줄의 주석은 컨테이너(`key:`/`-` 줄)의 것이다 — 새 마지막 항목에 붙지 않게 그 줄에
  // 남긴다(#149 리뷰 P2-4). `to`는 주석까지 삼키고 주석은 삽입 첫머리에 다시 쓴다.
  const lineEnd = lineEndOf(source, range.end.offset);
  const tail = source.slice(range.end.offset, lineEnd);
  const comment = tail.trim().startsWith("#") ? ` ${tail.trim()}` : "";
  const to = comment === "" ? range.end.offset : lineEnd;
  if (top === "") {
    // 루트 전체가 flow면 문서 본문을 block mapping으로 바꾼다(주석은 첫 줄로).
    const insert = `${comment === "" ? "" : `${comment.trim()}${eol}`}${indentLines(block, "", eol)}`;
    return {
      from: range.start.offset,
      to,
      insert,
      cursor: range.start.offset + insert.length,
    };
  }
  // 값이 `key:`/`-`와 다른 줄에서 시작하면(`risk: # 원래` 다음 줄에 `{ … }`) 값 줄의 시작부터 교체한다 — 키 줄
  // 주석과 그 사이 자기 줄 주석이 그대로 남는다(#149 재검토 P2-7, #150 리뷰 P2-1). 닫는 괄호 뒤 주석은 그때
  // 값 자리 첫 줄에 따로 둔다.
  const rewrite = (
    anchorOffset: number,
    indent: string,
  ): Plan => {
    const sameLine = lineEndOf(source, anchorOffset) >= range.start.offset;
    if (sameLine) {
      const insert = `${comment}${eol}${indent}${indentLines(block, indent, eol)}`;
      return {
        from: anchorOffset,
        to,
        insert,
        cursor: anchorOffset + insert.length,
      };
    }
    const from = lineStartOf(source, range.start.offset);
    const lead = comment === "" ? "" : `${indent}${comment.trim()}${eol}`;
    const insert = `${lead}${indent}${indentLines(block, indent, eol)}`;
    return { from, to, insert, cursor: from + insert.length };
  };
  const colon = afterColon(source, parsed, top);
  if (colon !== null) {
    // `key: { … }` → `key:` 뒤에서 줄을 바꾸고 키 열 + 폭으로 들여쓴다.
    const keyColumn = parsed.keyRanges.get(top)!.start.column;
    return rewrite(colon, " ".repeat(keyColumn + unit));
  }
  const dash = dashOffsetOf(source, range.start.offset);
  if (dash !== null) {
    // `- { … }` → `-` 뒤에서 줄을 바꾸고 `-` 열 + 폭으로 들여쓴다(빈 `- []`·`- {}` 확장과 같은 모양 —
    // 문서 고유 폭 `unit`을 쓰므로 2칸이 아닌 문서에서는 예전 `+2`와 다르다, #149 리뷰 P2-5).
    return rewrite(dash + 1, " ".repeat(dash - lineStartOf(source, dash) + unit));
  }
  const indent = " ".repeat(range.start.column);
  const insert = indentLines(block, indent, eol);
  return {
    from: range.start.offset,
    to: range.end.offset,
    insert,
    cursor: range.start.offset + insert.length,
  };
};

const parentPointerOf = (pointer: string): string => {
  const segments = pointerSegments(pointer);
  return segments.length <= 1
    ? ""
    : `/${segments.slice(0, -1).map(escapePointerSegment).join("/")}`;
};

type Plan = { from: number; to: number; insert: string; cursor: number };

const planReplaceScalar = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "replace-scalar" }>,
): Plan | PlanFailure => {
  const range = parsed.valueRanges.get(op.pointer);
  if (!range) return "not-found";
  const current = valueAt(parsed.tree, op.pointer);
  if (!isScalar(current)) return "not-scalar";
  const literal = scalarLiteral(op.value);
  // block scalar의 range는 줄바꿈까지 포함하므로 내용 끝까지만 교체한다(P3-01 리뷰 P2-8).
  let to = range.end.offset;
  while (
    to > range.start.offset &&
    (source[to - 1] === "\n" || source[to - 1] === "\r")
  )
    to -= 1;
  return {
    from: range.start.offset,
    to,
    insert: literal,
    cursor: range.start.offset + literal.length,
  };
};

/**
 * pointer 아래 **내용**의 마지막 offset. yaml 노드 range는 mapping/sequence 뒤에 따라오는 주석 줄까지
 * 포함할 수 있어 삽입·삭제 경계로 쓰면 다음 키를 설명하는 주석을 삼킨다. 그래서 키 범위와 leaf
 * (스칼라·빈 컨테이너) 값 범위만 모아 최댓값을 잡는다.
 */
const contentEnd = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  pointer: string,
): number | null => {
  const within = (candidate: string): boolean =>
    pointer === ""
      ? true
      : candidate === pointer || candidate.startsWith(`${pointer}/`);
  let end = -1;
  for (const [candidate, range] of parsed.keyRanges) {
    if (within(candidate)) end = Math.max(end, range.end.offset);
  }
  for (const [candidate, range] of parsed.valueRanges) {
    if (!within(candidate)) continue;
    const value = valueAtPointer(parsed.tree, candidate).value;
    const leaf =
      (!isRecord(value) && !Array.isArray(value)) ||
      (isRecord(value) && Object.keys(value).length === 0) ||
      (Array.isArray(value) && value.length === 0);
    if (leaf) end = Math.max(end, range.end.offset);
  }
  if (end < 0) return null;
  // block scalar(`|`/`>`)의 값 range는 마지막 줄바꿈까지 포함한다(P3-01 리뷰 P1-1): 내용의 끝으로 되돌린다.
  while (end > 0 && (source[end - 1] === "\n" || source[end - 1] === "\r"))
    end -= 1;
  return end;
};

/** `key:` 바로 뒤 offset. 빈 컨테이너(`{}`/`[]`)를 block으로 바꾸거나 컨테이너를 비울 때의 교체 시작점. */
const afterColon = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  pointer: string,
): number | null => {
  const keyRange = parsed.keyRanges.get(pointer);
  if (!keyRange) return null;
  const colon = source.indexOf(":", keyRange.end.offset);
  return colon < 0 ? null : colon + 1;
};

/**
 * 삽입 자리. `after-line`은 그 줄 끝 뒤에 `EOL + indent + fragment`, `line-start`는 그 줄 시작에
 * `indent + fragment + EOL`, `inline-before`는 dash 줄에 붙은 첫 키/항목 자리에 `fragment + EOL + indent`
 * (기존 것은 다음 줄로 밀린다).
 */
type Anchor = {
  at: number;
  mode: "after-line" | "line-start" | "inline-before";
};

const isLineStart = (source: string, offset: number): boolean =>
  offset === 0 || source[offset - 1] === "\n";

/**
 * 호출자가 준 offset(스니펫의 커서 줄 자리)이 허용 구간 `[lower, upper]` 안의 줄 시작(또는 문서 끝)이면
 * 그 자리를 쓴다. 구간 밖이면 기본 앵커를 쓴다(형제 순서와 텍스트 위치가 어긋나면 preflight가 막는다).
 */
const anchorFromOption = (
  source: string,
  offset: number | undefined,
  lower: number,
  upper: number,
): Anchor | null => {
  if (offset === undefined || offset < lower || offset > upper) return null;
  if (isLineStart(source, offset)) return { at: offset, mode: "line-start" };
  if (offset === source.length) return { at: offset, mode: "after-line" };
  return null;
};

const insertAt = (
  anchor: Anchor,
  fragment: string,
  indent: string,
  eol: Eol,
): Plan => {
  const body = indentLines(fragment, indent, eol);
  if (anchor.mode === "after-line") {
    const insert = `${eol}${indent}${body}`;
    return {
      from: anchor.at,
      to: anchor.at,
      insert,
      cursor: anchor.at + insert.length,
    };
  }
  if (anchor.mode === "line-start") {
    const insert = `${indent}${body}${eol}`;
    return {
      from: anchor.at,
      to: anchor.at,
      insert,
      cursor: anchor.at + insert.length - eol.length,
    };
  }
  const insert = `${body}${eol}${indent}`;
  return {
    from: anchor.at,
    to: anchor.at,
    insert,
    cursor: anchor.at + insert.length - eol.length - indent.length,
  };
};

/** 부모 `key:` 줄 끝(줄 끝 주석 뒤). 시퀀스 항목이 부모면(`- - x`) 바깥 `-` 뒤. */
const parentLineEnd = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  parentPointer: string,
): number | null => {
  const colon = afterColon(source, parsed, parentPointer);
  if (colon !== null) return lineEndOf(source, colon);
  const parentRange = parsed.valueRanges.get(parentPointer);
  if (parentRange === undefined) return null;
  const dash = dashOffsetOf(source, parentRange.start.offset);
  return dash === null ? null : dash + 1;
};

const planInsertKey = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "insert-key" }>,
  eol: Eol,
  unit: number,
  anchorOffset: number | undefined,
): Plan | PlanFailure => {
  const parent = valueAt(parsed.tree, op.parentPointer);
  if (parent === undefined) return "not-found";
  const fragment = yamlBlock({ [op.key]: op.value });
  if (parent === null && op.parentPointer !== "") {
    // 값 없는 키(`key:`): 그 줄 끝 뒤에 block mapping을 연다(줄 끝 주석은 그대로 둔다).
    if (op.before !== undefined) return "not-found";
    return openEmptyValue(
      source,
      parsed,
      op.parentPointer,
      fragment,
      eol,
      unit,
    );
  }
  if (!isRecord(parent)) return "not-mapping";
  if (Object.hasOwn(parent, op.key)) return "exists";
  if (op.before !== undefined && !Object.hasOwn(parent, op.before))
    return "not-found";
  if (op.parentPointer === "" && Object.keys(parent).length === 0) {
    // flow `{}` 루트면 그 범위를 block mapping으로 교체한다.
    const rootRange = parsed.valueRanges.get("");
    if (rootRange !== undefined) {
      const insert = indentLines(fragment, "", eol);
      return {
        from: rootRange.start.offset,
        to: rootRange.end.offset,
        insert,
        cursor: rootRange.start.offset + insert.length,
      };
    }
    // 내용 없는 문서: 주석·빈 줄만 있으면 그 뒤에, 아무것도 없으면 전체를 첫 키로 바꾼다.
    const trimmedEnd = source.replace(/\s+$/, "").length;
    const insert =
      trimmedEnd === 0
        ? indentLines(fragment, "", eol)
        : `${eol}${indentLines(fragment, "", eol)}`;
    return {
      from: trimmedEnd,
      to: source.length,
      insert,
      cursor: trimmedEnd + insert.length,
    };
  }
  // flow 표기(`{}`·`{ a: 1 }`, 또는 flow 안의 mapping)면 바깥 flow 컨테이너부터 block으로 다시 쓴다(backlog 14).
  const flowTop = topmostFlowAncestor(source, parsed, op.parentPointer);
  if (flowTop !== null)
    return replaceFlowContainer(source, parsed, op, flowTop, eol, unit);
  const existingColumn =
    op.parentPointer === ""
      ? 0
      : childKeyColumn(parsed, op.parentPointer, parent);
  if (existingColumn === null) return "not-mapping";
  const anchor = insertKeyAnchor(
    source,
    parsed,
    op.parentPointer,
    parent,
    op.before,
    anchorOffset,
  );
  if (anchor === null) return "not-found";
  return insertAt(anchor, fragment, " ".repeat(existingColumn), eol);
};

/**
 * 새 키의 자리. 기본은 **앞 형제의 내용 줄 끝 뒤**다(`before`가 없으면 마지막 키 뒤, 앞 형제가 없으면
 * 부모 `key:` 줄 끝, 루트 첫 키 앞이면 문서 시작). 그래서 `before` 키 위의 독립 주석은 계속 `before`를
 * 설명한다. `before`가 `- key:`처럼 dash 줄의 첫 키면 그 자리에 들어가고 기존 키는 다음 줄로 밀린다.
 * 호출자가 offset을 주면(스니펫의 커서 줄) 앞 형제 뒤 ~ `before` 줄 시작 사이에서 그 자리를 쓴다.
 */
const insertKeyAnchor = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  parentPointer: string,
  parent: Record<string, unknown>,
  before: string | undefined,
  anchorOffset: number | undefined,
): Anchor | null => {
  const keys = Object.keys(parent);
  const pointerOf = (key: string): string =>
    `${parentPointer}/${escapePointerSegment(key)}`;
  const position = before === undefined ? keys.length : keys.indexOf(before);
  if (position < 0) return null;
  const previous = keys[position - 1];
  let lower: number;
  if (previous !== undefined) {
    const end = contentEnd(source, parsed, pointerOf(previous));
    if (end === null) return null;
    lower = lineEndOf(source, end);
  } else if (parentPointer === "") {
    lower = 0;
  } else {
    const end = parentLineEnd(source, parsed, parentPointer);
    if (end === null) return null;
    lower = end;
  }
  const beforeRange =
    before === undefined ? undefined : parsed.keyRanges.get(pointerOf(before));
  if (before !== undefined && beforeRange === undefined) return null;
  if (beforeRange !== undefined && onDashLine(source, beforeRange.start.offset))
    return { at: beforeRange.start.offset, mode: "inline-before" };
  const upper =
    beforeRange === undefined
      ? source.length
      : lineStartOf(source, beforeRange.start.offset);
  const chosen = anchorFromOption(source, anchorOffset, lower, upper);
  if (chosen !== null) return chosen;
  if (previous === undefined && parentPointer === "")
    return { at: 0, mode: "line-start" };
  return { at: lower, mode: "after-line" };
};

/** 값 없는 키(`key:` 또는 `key: # c`) 줄 끝 뒤에 block 컨테이너를 연다. 항목/키 열은 부모 키 열 + 폭. */
const openEmptyValue = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  parentPointer: string,
  fragment: string,
  eol: Eol,
  unit: number,
): Plan | PlanFailure => {
  const keyRange = parsed.keyRanges.get(parentPointer);
  const colon = afterColon(source, parsed, parentPointer);
  if (keyRange === undefined || colon === null) return "not-found";
  const indent = " ".repeat(keyRange.start.column + unit);
  return insertAt(
    { at: lineEndOf(source, colon), mode: "after-line" },
    fragment,
    indent,
    eol,
  );
};

const planInsertItem = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "insert-item" }>,
  eol: Eol,
  unit: number,
  anchorOffset: number | undefined,
): Plan | PlanFailure => {
  const parent = valueAt(parsed.tree, op.parentPointer);
  if (parent === undefined) return "not-found";
  const fragment = yamlBlock([op.value]); // `- ...` 로 시작
  if (parent === null && op.parentPointer !== "") {
    if ((op.index ?? 0) !== 0) return "not-found";
    return openEmptyValue(
      source,
      parsed,
      op.parentPointer,
      fragment,
      eol,
      unit,
    );
  }
  if (!Array.isArray(parent)) return "not-sequence";
  const index = op.index ?? parent.length;
  if (index < 0 || index > parent.length) return "not-found";
  // flow 표기(`[]`·`[x, y]`, 또는 flow 안의 시퀀스)면 바깥 flow 컨테이너부터 block으로 다시 쓴다(backlog 14).
  const flowTop = topmostFlowAncestor(source, parsed, op.parentPointer);
  if (flowTop !== null)
    return replaceFlowContainer(source, parsed, op, flowTop, eol, unit);
  // 항목 열: 첫 항목 자기 `-`의 열(`- - x`처럼 겹친 시퀀스는 안쪽 `-`).
  const dashOf = (i: number): number | null => {
    const item = parsed.valueRanges.get(`${op.parentPointer}/${i}`);
    return item === undefined ? null : dashOffsetOf(source, item.start.offset);
  };
  const firstDash = dashOf(0);
  if (firstDash === null) return "not-sequence";
  const indent = " ".repeat(firstDash - lineStartOf(source, firstDash));
  // 새 항목의 자리는 키와 같은 규칙이다: **앞 항목의 내용 줄 끝 뒤**(index 0은 부모 `key:` 줄 끝 뒤).
  // 그래서 대상 항목 위의 독립 주석은 계속 그 대상을 설명한다(P3-02 리뷰 P2-1: insert-key와 통일).
  // `- - x`처럼 바깥 `-`와 줄을 나눠 쓰는 안쪽 첫 항목 앞에는 그 자리에 넣고 기존 항목을 다음 줄로 민다.
  let lower: number;
  if (index === 0) {
    if (onDashLine(source, firstDash))
      return insertAt(
        { at: firstDash, mode: "inline-before" },
        fragment,
        indent,
        eol,
      );
    const end = parentLineEnd(source, parsed, op.parentPointer);
    if (end === null) return "not-found";
    lower = end;
  } else {
    const end = contentEnd(source, parsed, `${op.parentPointer}/${index - 1}`);
    if (end === null) return "not-found";
    lower = lineEndOf(source, end);
  }
  const targetDash = index < parent.length ? dashOf(index) : null;
  if (index < parent.length && targetDash === null) return "not-sequence";
  const upper =
    targetDash === null ? source.length : lineStartOf(source, targetDash);
  const anchor =
    anchorFromOption(source, anchorOffset, lower, upper) ??
    ({ at: lower, mode: "after-line" } as const);
  return insertAt(anchor, fragment, indent, eol);
};

const planRemove = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "remove" }>,
  eol: Eol,
  unit: number,
): Plan | PlanFailure => {
  const parentPointer = parentPointerOf(op.pointer);
  const parent = valueAt(parsed.tree, parentPointer);
  const exists =
    parsed.valueRanges.has(op.pointer) || parsed.keyRanges.has(op.pointer);
  if (!exists || op.pointer === "") return "not-found";
  // 부모가 flow 표기(또는 flow 안)면 삽입과 같은 경로로 가장 바깥 flow 컨테이너를 다시 쓴다(backlog 18:
  // 삽입만 되고 삭제는 block 앵커를 요구하던 비대칭). 비면 `{}`/`[]`가 그 자리에 남는다.
  const flowTop = topmostFlowAncestor(source, parsed, parentPointer);
  if (flowTop !== null)
    return replaceFlowContainer(source, parsed, op, flowTop, eol, unit);
  // 삭제로 부모가 비면 구조를 유지한다: mapping은 `{}`, sequence는 `[]`(루트는 비우지 않는다).
  const emptiesParent =
    parentPointer !== "" &&
    ((isRecord(parent) && Object.keys(parent).length === 1) ||
      (Array.isArray(parent) && parent.length === 1));
  if (emptiesParent) {
    // 부모가 키 아래면 `key:` 뒤부터, 시퀀스 항목이면(`- - x`) 항목 내용 시작부터 교체한다.
    const colon = afterColon(source, parsed, parentPointer);
    const parentRange = parsed.valueRanges.get(parentPointer);
    if (colon === null && parentRange === undefined) return "not-found";
    const to = contentEnd(source, parsed, parentPointer);
    if (to === null) return "not-found";
    const dash =
      colon === null ? dashOffsetOf(source, parentRange!.start.offset) : null;
    const from =
      colon ?? (dash === null ? parentRange!.start.offset : dash + 1);
    const empty = Array.isArray(parent) ? "[]" : "{}";
    // `key: # 메모`처럼 부모 줄에 줄 끝 주석이 있으면 그 줄은 두고 다음 줄에 빈 컨테이너를 쓴다(P3-02 리뷰 P2-2).
    const parentLine = lineEndOf(source, from);
    // 부모 줄의 `#`이 부모의 줄 끝 주석인 것은 부모 내용(첫 자식)이 다음 줄에서 시작할 때뿐이다(`key: # 메모`
    // 다음 줄에 자식). 내용이 같은 줄에서 시작하면(`- - …`) 그 줄의 `#`은 지워질 자식 안의 스칼라일 수 있다.
    // 한 줄 자식 `- - "#tag"`(P5-01 리뷰 P2-1)는 물론, 여러 줄 자식 `- - k: "#tag"` 뒤에 자식 줄이 이어질 때도
    // 인용 스칼라 속 `#`을 주석으로 오인해 dash 줄을 남긴 채 `[]`를 덧붙여 tree가 깨졌다(#197). `#`은 YAML
    // 주석 규칙대로 줄 시작이나 공백 뒤일 때만 주석으로 본다.
    const contentOnLaterLine =
      parentRange !== undefined && parentRange.start.offset > parentLine;
    if (
      contentOnLaterLine &&
      /(?:^|[ \t])#/.test(source.slice(from, parentLine))
    ) {
      const keyRange = parsed.keyRanges.get(parentPointer);
      const column =
        keyRange?.start.column ??
        (dash === null ? 0 : dash - lineStartOf(source, dash));
      const insert = `${eol}${" ".repeat(column + unit)}${empty}`;
      return {
        from: parentLine,
        to,
        insert,
        cursor: parentLine + insert.length,
      };
    }
    const insert = ` ${empty}`;
    return { from, to, insert, cursor: from + insert.length };
  }
  const segments = pointerSegments(op.pointer);
  const own = segments[segments.length - 1]!;
  const valueRange = parsed.valueRanges.get(op.pointer);
  if (Array.isArray(parent)) {
    // 시퀀스 항목: 자기 `-`가 줄을 여는 보통의 항목은 mapping 키 삭제와 같은 규칙(자기 줄들만, 다음
    // 항목을 설명하는 주석은 남김)으로 지운다. `- - x`처럼 바깥 `-`와 한 줄을 나눠 쓰는 안쪽 첫
    // 항목은 dash 줄 첫 키와 같은 규칙이다: 자기 `-`부터 자기 내용 줄 끝까지 지우고 다음 줄의
    // 들여쓰기를 걷어 다음 줄 내용(주석이든 다음 항목이든)이 바깥 `- ` 뒤에 오게 한다(P3-01 2차 P2-R1).
    const index = Number(own);
    const dash =
      valueRange === undefined
        ? null
        : dashOffsetOf(source, valueRange.start.offset);
    if (dash === null) return "not-found";
    const ownEnd = contentEnd(source, parsed, op.pointer);
    if (ownEnd === null) return "not-found";
    if (!onDashLine(source, dash) || index + 1 >= parent.length) {
      return removeLines(
        source,
        leadingCommentsStart(source, lineStartOf(source, dash), eol),
        ownEnd,
        eol,
      );
    }
    const nextLineStart = lineEndOf(source, ownEnd) + eol.length;
    const nextContent =
      nextLineStart + source.slice(nextLineStart).search(/\S|$/);
    return { from: dash, to: nextContent, insert: "", cursor: dash };
  }
  const keyRange = parsed.keyRanges.get(op.pointer)!;
  const siblings = Object.keys(parent as Record<string, unknown>);
  const position = siblings.indexOf(own);
  const following = siblings[position + 1];
  if (onDashLine(source, keyRange.start.offset)) {
    // `- key: v` 첫 키: `-`는 남기고 키부터 자기 내용 줄 끝까지 지운 뒤, 다음 줄의 들여쓰기를 걷어
    // 다음 줄 내용(주석이든 다음 키든)이 `- ` 바로 뒤에 오게 한다(주석 보존, P3-01 리뷰 P1-2).
    if (following === undefined) return "not-found";
    const ownEnd = contentEnd(source, parsed, op.pointer);
    if (ownEnd === null) return "not-found";
    const endOfLine = lineEndOf(source, ownEnd);
    const nextLineStart = endOfLine + eol.length;
    const nextContent =
      nextLineStart + source.slice(nextLineStart).search(/\S|$/);
    return {
      from: keyRange.start.offset,
      to: nextContent,
      insert: "",
      cursor: keyRange.start.offset,
    };
  }
  const ownEnd = contentEnd(source, parsed, op.pointer);
  if (ownEnd === null) return "not-found";
  return removeLines(
    source,
    leadingCommentsStart(source, lineStartOf(source, keyRange.start.offset), eol),
    ownEnd,
    eol,
  );
};

/**
 * `lineStart` 줄 바로 위에 붙은 연속 독립 주석 줄(공백 + `#…`)의 시작 offset. 삭제 대상 위의 주석은 그
 * 대상을 설명하므로 함께 지운다 — 삽입 앵커 규칙("앞 형제 내용 줄 끝 뒤"에 넣어 대상 위 주석이 대상을
 * 따라간다)의 짝이고 property test의 주석 소유 규칙(소유자 = 그 줄에서 시작하는 pointer) 안이다
 * (Phase 4 감사 R3: 노드·항목 삭제 뒤 고아 주석). 빈 줄이나 다른 내용을 만나면 멈춘다. 주석 블록이 문서
 * 첫 줄(offset 0)에서 시작하면 파일 헤더로 보아 손대지 않는다(P5-01 리뷰 P2-2: 첫 루트 키를 지울 때 머리
 * 주석이 사라지던 문제).
 */
const leadingCommentsStart = (
  source: string,
  lineStart: number,
  eol: Eol,
): number => {
  let start = lineStart;
  while (start >= eol.length && source.slice(start - eol.length, start) === eol) {
    const previousLineStart = lineStartOf(source, start - eol.length);
    const line = source.slice(previousLineStart, start - eol.length);
    if (!/^\s*#/.test(line)) break;
    start = previousLineStart;
  }
  return start === 0 ? lineStart : start;
};

/** `from` 줄부터 `contentEnd`가 속한 줄까지 통째로(EOL 포함) 지운다. 마지막 줄이면 앞 EOL을 지운다. */
const removeLines = (
  source: string,
  from: number,
  ownEnd: number,
  eol: Eol,
): Plan => {
  const endOfContentLine = lineEndOf(source, ownEnd);
  if (endOfContentLine < source.length) {
    return {
      from,
      to: endOfContentLine + eol.length,
      insert: "",
      cursor: from,
    };
  }
  const previousEol = Math.max(0, from - eol.length);
  return {
    from: previousEol,
    to: endOfContentLine,
    insert: "",
    cursor: previousEol,
  };
};

/** 빈 줄·주석뿐인 문서는 parser가 거부하므로, 루트 insert-key에 한해 빈 mapping으로 읽는다. */
const parseOrEmpty = (source: string, op: SourceOperation): ParsedSource => {
  const parsed = parseSource(source, "yaml");
  if (parsed.status === "ok") return parsed;
  const blank = source
    .split(/\r?\n/)
    .every((line) => line.trim() === "" || line.trimStart().startsWith("#"));
  if (!blank || op.kind !== "insert-key" || op.parentPointer !== "")
    return parsed;
  return {
    status: "ok",
    format: "yaml",
    tree: {},
    valueRanges: new Map(),
    keyRanges: new Map(),
    diagnostics: [],
  };
};

export const planSourceOperation = (
  source: string,
  format: SourceFormat,
  op: SourceOperation,
  options: {
    /** 줄 종결자를 문서 밖에서 정한다(스니펫이 커서 줄을 뺀 원문을 넘길 때 원래 문서의 EOL). */
    eol?: Eol;
    /**
     * 삽입 연산이 들어갈 offset(줄 시작 또는 문서 끝). 형제 순서(`before`/`index`)가 허용하는 구간 안일
     * 때만 쓰이고, 아니면 기본 앵커(앞 형제 내용 줄 끝 뒤)로 간다. 스니펫이 커서 줄 자리를 지키는 데 쓴다.
     */
    anchor?: number;
  } = {},
): PlanResult => {
  if (format !== "yaml") return { status: "error", reason: "yaml-only" };
  const parsed = parseOrEmpty(source, op);
  if (parsed.status !== "ok") return { status: "error", reason: "parse" };
  const eol = options.eol ?? detectEol(source);
  const unit = detectIndentUnit(source, parsed);
  const plan =
    op.kind === "replace-scalar"
      ? planReplaceScalar(source, parsed, op)
      : op.kind === "insert-key"
        ? planInsertKey(source, parsed, op, eol, unit, options.anchor)
        : op.kind === "insert-item"
          ? planInsertItem(source, parsed, op, eol, unit, options.anchor)
          : planRemove(source, parsed, op, eol, unit);
  if (typeof plan === "string") return { status: "error", reason: plan };
  const expected = applyToTree(parsed.tree, op);
  if (expected === null) return { status: "error", reason: "not-found" };
  const finish = (
    from: number,
    to: number,
    insert: string,
    cursor: number,
  ): PlanResult => {
    const nextSource = `${source.slice(0, from)}${insert}${source.slice(to)}`;
    const reparsed = parseSource(nextSource, "yaml");
    if (reparsed.status !== "ok" || !deepEqual(reparsed.tree, expected))
      return { status: "error", reason: "parse" };
    return {
      status: "ok",
      edit: {
        from,
        to,
        insert,
        nextSource,
        selection: { from: cursor, to: cursor },
      },
    };
  };
  const first = finish(plan.from, plan.to, plan.insert, plan.cursor);
  if (first.status === "ok") return first;
  // 문자열 스칼라가 plain으로 다른 타입으로 읽히면 따옴표로 한 번 더 시도한다.
  if (op.kind === "replace-scalar" && typeof op.value === "string") {
    const quoted = JSON.stringify(op.value);
    return finish(plan.from, plan.to, quoted, plan.from + quoted.length);
  }
  return first;
};

/**
 * 연산 여러 개를 한 트랜잭션으로: 앞 연산의 `nextSource` 위에 다음 연산을 `planSourceOperation`으로 순차
 * 계획하고(각 단계가 preflight를 거친다), 원문과 최종 텍스트의 공통 접두·접미를 뺀 구간 하나로 합쳐
 * 편집기 `replaceRange` 한 번(undo 1회)이 되게 한다. 한 단계라도 실패하면 그 사유로 전체가 실패한다.
 * 연산 하나면 `planSourceOperation`과 같다(Phase 5 감사 backlog 13: 빈 그래프의 첫 노드 + 출력 지정).
 */
export const planSourceOperations = (
  source: string,
  format: SourceFormat,
  ops: readonly SourceOperation[],
): PlanResult => {
  if (ops.length === 0) return { status: "error", reason: "not-found" };
  const eol = detectEol(source);
  let text = source;
  let last: PlannedEdit | null = null;
  for (const op of ops) {
    const planned = planSourceOperation(text, format, op, { eol });
    if (planned.status !== "ok") return planned;
    text = planned.edit.nextSource;
    last = planned.edit;
  }
  if (last === null || ops.length === 1) return { status: "ok", edit: last! };
  let prefix = 0;
  while (
    prefix < source.length &&
    prefix < text.length &&
    source[prefix] === text[prefix]
  )
    prefix += 1;
  let suffix = 0;
  while (
    suffix < source.length - prefix &&
    suffix < text.length - prefix &&
    source[source.length - 1 - suffix] === text[text.length - 1 - suffix]
  )
    suffix += 1;
  return {
    status: "ok",
    edit: {
      from: prefix,
      to: source.length - suffix,
      insert: text.slice(prefix, text.length - suffix),
      nextSource: text,
      selection: last.selection,
    },
  };
};
