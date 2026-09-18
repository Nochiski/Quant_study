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
 * 통째로 갈아끼우는 일은 없다(주석·순서·따옴표는 범위 밖에서 그대로다).
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
        // 값 없는 키를 빈 mapping으로 확장한다.
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
    const parentColumn =
      parsed.keyRanges.get(parentPointerOf(pointer))?.start.column ?? 0;
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

const planInsertKey = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "insert-key" }>,
  eol: Eol,
  unit: number,
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
  if (op.parentPointer === "") {
    if (Object.keys(parent).length === 0) {
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
      const from = trimmedEnd;
      return {
        from,
        to: source.length,
        insert,
        cursor: from + insert.length,
      };
    }
    const anchor = insertKeyAnchor(source, parsed, "", parent, op.before);
    if (anchor === null) return "not-found";
    const insert = anchor.leading
      ? `${eol}${indentLines(fragment, "", eol)}`
      : `${indentLines(fragment, "", eol)}${eol}`;
    return {
      from: anchor.at,
      to: anchor.at,
      insert,
      cursor: anchor.leading
        ? anchor.at + insert.length
        : anchor.at + insert.length - eol.length,
    };
  }
  const parentRange = parsed.valueRanges.get(op.parentPointer)!;
  const existingColumn = childKeyColumn(parsed, op.parentPointer, parent);
  if (existingColumn === null) {
    // flow `{}`: `key:` 뒤(시퀀스 항목이면 `-` 뒤)부터 value 끝까지를 block mapping으로 교체.
    const keyRange = parsed.keyRanges.get(op.parentPointer);
    const dash = dashOffsetOf(source, parentRange.start.offset);
    const from =
      afterColon(source, parsed, op.parentPointer) ??
      (dash === null ? parentRange.start.offset : dash + 1);
    const parentColumn =
      keyRange?.start.column ?? Math.max(0, parentRange.start.column - unit);
    const indent = " ".repeat(parentColumn + unit);
    const insert = `${eol}${indent}${indentLines(fragment, indent, eol)}`;
    return {
      from,
      to: parentRange.end.offset,
      insert,
      cursor: from + insert.length,
    };
  }
  const indent = " ".repeat(existingColumn);
  const anchor = insertKeyAnchor(
    source,
    parsed,
    op.parentPointer,
    parent,
    op.before,
  );
  if (anchor === null) return "not-found";
  if (anchor.leading) {
    const insert = `${eol}${indent}${indentLines(fragment, indent, eol)}`;
    return {
      from: anchor.at,
      to: anchor.at,
      insert,
      cursor: anchor.at + insert.length,
    };
  }
  // `before` 키가 자기 `-` 줄에 붙은 첫 키면 그 자리에 새 키를 넣고 기존 키를 다음 줄로 민다.
  const insert = `${indentLines(fragment, indent, eol)}${eol}${indent}`;
  return {
    from: anchor.at,
    to: anchor.at,
    insert,
    cursor: anchor.at + insert.length - eol.length - indent.length,
  };
};

/**
 * 새 키가 들어갈 자리. `before`가 없으면 마지막 키 내용 줄 끝(leading: 줄바꿈을 앞에 붙여 붙인다).
 * `before`가 있으면 그 앞 형제의 내용 줄 끝이고, 앞 형제가 없으면 부모 `key:` 줄 끝(루트는 문서 시작에서
 * 줄바꿈을 뒤에 붙인다). `before` 키가 `- key:`처럼 dash 줄의 첫 키면 그 키 자리(leading: false).
 * 앞 형제 뒤에 넣으므로 `before` 키 위의 주석은 계속 `before` 키를 설명한다.
 */
const insertKeyAnchor = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  parentPointer: string,
  parent: Record<string, unknown>,
  before: string | undefined,
): { at: number; leading: boolean } | null => {
  const keys = Object.keys(parent);
  const pointerOf = (key: string): string =>
    `${parentPointer}/${escapePointerSegment(key)}`;
  if (before === undefined) {
    const last = keys[keys.length - 1];
    if (last === undefined) return null;
    const end = contentEnd(source, parsed, pointerOf(last));
    if (end === null) return null;
    return { at: lineEndOf(source, end), leading: true };
  }
  const position = keys.indexOf(before);
  if (position < 0) return null;
  const beforeRange = parsed.keyRanges.get(pointerOf(before));
  if (beforeRange === undefined) return null;
  if (onDashLine(source, beforeRange.start.offset))
    return { at: beforeRange.start.offset, leading: false };
  if (position > 0) {
    const end = contentEnd(source, parsed, pointerOf(keys[position - 1]!));
    if (end === null) return null;
    return { at: lineEndOf(source, end), leading: true };
  }
  if (parentPointer === "") return { at: 0, leading: false };
  const colon = afterColon(source, parsed, parentPointer);
  if (colon === null) return null;
  return { at: lineEndOf(source, colon), leading: true };
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
  const at = lineEndOf(source, colon);
  const indent = " ".repeat(keyRange.start.column + unit);
  const insert = `${eol}${indent}${indentLines(fragment, indent, eol)}`;
  return { from: at, to: at, insert, cursor: at + insert.length };
};

const planInsertItem = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "insert-item" }>,
  eol: Eol,
  unit: number,
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
  const parentRange = parsed.valueRanges.get(op.parentPointer)!;
  if (parent.length === 0) {
    // flow `[]`: `key:` 뒤(시퀀스 항목이면 `-` 뒤)부터 block sequence로 교체. 항목은 부모 키 열 + 폭.
    const keyRange = parsed.keyRanges.get(op.parentPointer);
    const dash = dashOffsetOf(source, parentRange.start.offset);
    const from =
      afterColon(source, parsed, op.parentPointer) ??
      (dash === null ? parentRange.start.offset : dash + 1);
    const parentColumn =
      keyRange?.start.column ?? Math.max(0, parentRange.start.column - unit);
    const indent = " ".repeat(parentColumn + unit);
    const insert = `${eol}${indent}${indentLines(fragment, indent, eol)}`;
    return {
      from,
      to: parentRange.end.offset,
      insert,
      cursor: from + insert.length,
    };
  }
  // 항목 열: 첫 항목 자기 `-`의 열(`- - x`처럼 겹친 시퀀스는 안쪽 `-`).
  const firstItem = parsed.valueRanges.get(`${op.parentPointer}/0`);
  const firstDash =
    firstItem === undefined
      ? null
      : dashOffsetOf(source, firstItem.start.offset);
  if (firstDash === null) return "not-sequence";
  const itemColumn = firstDash - lineStartOf(source, firstDash);
  const indent = " ".repeat(itemColumn);
  if (index < parent.length) {
    const target = parsed.valueRanges.get(`${op.parentPointer}/${index}`);
    const dash =
      target === undefined ? null : dashOffsetOf(source, target.start.offset);
    if (dash === null) return "not-sequence";
    // 대상 항목 위의 독립 주석은 대상을 설명하므로 새 항목은 그 주석보다 **위**가 아니라 `-` 자리에
    // 들어간다(주석은 새 항목이 아니라 밀려난 대상과 함께 남는다).
    // 대상 항목의 `-` 자리에 새 항목을 넣고 대상은 다음 줄로 밀린다.
    const insert = `${indentLines(fragment, indent, eol)}${eol}${indent}`;
    return {
      from: dash,
      to: dash,
      insert,
      cursor: dash + insert.length - eol.length - indent.length,
    };
  }
  const lastEnd = contentEnd(
    source,
    parsed,
    `${op.parentPointer}/${parent.length - 1}`,
  );
  if (lastEnd === null) return "not-found";
  const at = lineEndOf(source, lastEnd);
  const insert = `${eol}${indent}${indentLines(fragment, indent, eol)}`;
  return { from: at, to: at, insert, cursor: at + insert.length };
};

const planRemove = (
  source: string,
  parsed: Extract<ParsedSource, { status: "ok" }>,
  op: Extract<SourceOperation, { kind: "remove" }>,
  eol: Eol,
): Plan | PlanFailure => {
  const parentPointer = parentPointerOf(op.pointer);
  const parent = valueAt(parsed.tree, parentPointer);
  const exists =
    parsed.valueRanges.has(op.pointer) || parsed.keyRanges.has(op.pointer);
  if (!exists || op.pointer === "") return "not-found";
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
      return removeLines(source, lineStartOf(source, dash), ownEnd, eol);
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
    lineStartOf(source, keyRange.start.offset),
    ownEnd,
    eol,
  );
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
        ? planInsertKey(source, parsed, op, eol, unit)
        : op.kind === "insert-item"
          ? planInsertItem(source, parsed, op, eol, unit)
          : planRemove(source, parsed, op, eol);
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
