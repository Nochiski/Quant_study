/**
 * Canonical StrategySpec snippets projected from the backend runtime schema and coherent factor
 * catalog. This module owns no field list, enum, default, graph shape or factor direction: those
 * values are copied from backend-owned contracts. The five category names are UI placement from
 * WORKFLOW P4-05, not a second validation model.
 */
import type { FactorDefinition } from "../../../shared/api";
import {
  escapePointerSegment,
  describeYamlCursor,
  parseSource,
  type SourceFormat,
} from "../../../shared/lib/yaml12";
import {
  materializeSchemaValue,
  resolveRef,
  UnsupportedSchemaShape,
  type JsonSchema,
} from "./schema-navigator";
import {
  detectEol,
  planSourceOperation,
  type PlannedEdit,
  type SourceOperation,
} from "./source-transactions";

export const SNIPPET_CATEGORIES = [
  "data",
  "factor",
  "signal",
  "risk",
  "execution",
] as const;

export type SnippetCategory = (typeof SNIPPET_CATEGORIES)[number];

export type CanonicalSnippet = {
  id: string;
  category: SnippetCategory;
  /** Backend field name or factor label. */
  label: string;
  /** 섹션 스니펫은 루트 키를 추가하고, 팩터 스니펫은 루트 `factors` 시퀀스에 항목을 추가한다. */
  kind: "section" | "factor";
  sectionKey: string;
  identity: { field: string; value: unknown } | null;
  value: unknown;
};

export type SnippetCatalogSource = {
  schema: JsonSchema | null;
  factors: readonly FactorDefinition[];
  status: "loading" | "ready" | "unavailable" | "incompatible";
};

/** 커서 줄(반쯤 입력한 키)까지 포함한 단일 범위 편집. 텍스트 조립은 `planSourceOperation`이 했다. */
export type SnippetEdit = PlannedEdit;

export type SnippetEditFailure =
  "yaml-only" | "selection" | "cursor-context" | "duplicate" | "parse";

export type SnippetEditResult =
  | { status: "ok"; edit: SnippetEdit }
  | { status: "error"; reason: SnippetEditFailure };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const owns = (value: object, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);

const rootProperty = (schema: JsonSchema, key: string): JsonSchema | null => {
  const properties = isRecord(schema.properties) ? schema.properties : null;
  const property = properties?.[key];
  return isRecord(property) ? property : null;
};

type FactorAuthoringContract = {
  /** 팩터 시퀀스의 루트 키(schema 1.1: `factors`는 최상위 배열). 이름은 스키마에서 읽는다. */
  sectionKey: string;
  item: JsonSchema;
};

/**
 * 항목 타입이 backend `x-authoring-*` 마커를 가진 루트 배열을 찾는다. 키 이름은 스키마에서 읽고
 * 가정하지 않으므로 UI는 문서 레이아웃을 복제하지 않는다. 마커가 범용이라 두 개 이상이 매치되면
 * 순서에 기대지 않고 `null`로 fail-closed한다(P2-01 리뷰 P2-004).
 */
const factorAuthoringContract = (
  schema: JsonSchema,
): FactorAuthoringContract | null => {
  const root = isRecord(schema.properties) ? schema.properties : {};
  const matches: FactorAuthoringContract[] = [];
  for (const [sectionKey, candidate] of Object.entries(root)) {
    if (!isRecord(candidate)) continue;
    const collection = resolveRef(schema, candidate);
    if (collection === null) continue;
    if (collection.type !== "array" || !isRecord(collection.items)) continue;
    const item = resolveRef(schema, collection.items);
    if (item === null) continue;
    const itemProperties = isRecord(item.properties) ? item.properties : {};
    const required = Array.isArray(item.required)
      ? item.required.filter((key): key is string => typeof key === "string")
      : [];
    const mapped = required.every((key) => {
      const property = itemProperties[key];
      return (
        isRecord(property) &&
        (typeof property["x-authoring-source"] === "string" ||
          owns(property, "x-authoring-default"))
      );
    });
    if (
      mapped &&
      Object.values(itemProperties).some(
        (property) =>
          isRecord(property) && property["x-authoring-identity"] === true,
      )
    )
      matches.push({ sectionKey, item });
  }
  return matches.length === 1 ? matches[0]! : null;
};

const factorValue = (
  contract: FactorAuthoringContract,
  definition: FactorDefinition,
): {
  value: Record<string, unknown>;
  identity: { field: string; value: unknown };
} | null => {
  const properties = isRecord(contract.item.properties)
    ? contract.item.properties
    : {};
  const catalog = definition as unknown as Record<string, unknown>;
  const value: Record<string, unknown> = {};
  let identity: { field: string; value: unknown } | null = null;
  for (const [field, property] of Object.entries(properties)) {
    if (!isRecord(property)) continue;
    const source = property["x-authoring-source"];
    const candidate =
      typeof source === "string" && owns(catalog, source)
        ? catalog[source]
        : owns(property, "x-authoring-default")
          ? property["x-authoring-default"]
          : undefined;
    if (candidate === undefined || candidate === null) return null;
    value[field] = candidate;
    if (property["x-authoring-identity"] === true) {
      if (identity !== null) return null;
      identity = { field, value: candidate };
    }
  }
  return identity === null ? null : { value, identity };
};

/** Builds the five-area catalog without copying StrategySpec keys or defaults into frontend code. */
export const buildCanonicalSnippetCatalog = (
  source: SnippetCatalogSource,
): CanonicalSnippet[] => {
  if (source.status !== "ready" || source.schema === null) return [];
  const { schema, factors } = source;
  const snippets: CanonicalSnippet[] = [];
  for (const category of SNIPPET_CATEGORIES) {
    if (category === "factor") continue;
    const property = rootProperty(schema, category);
    if (property === null) continue;
    try {
      snippets.push({
        id: `section:${category}`,
        category,
        label: category,
        kind: "section",
        sectionKey: category,
        identity: null,
        value: materializeSchemaValue(schema, property),
      });
    } catch (error) {
      if (!(error instanceof UnsupportedSchemaShape)) throw error;
    }
  }

  const contract = factorAuthoringContract(schema);
  if (contract === null) return snippets;
  for (const factor of factors.filter(
    (candidate) => candidate.availability === "implemented",
  )) {
    const preset = factorValue(contract, factor);
    if (preset === null) continue;
    snippets.push({
      id: `factor:${String(preset.identity.value)}`,
      category: "factor",
      label: factor.label,
      kind: "factor",
      sectionKey: contract.sectionKey,
      identity: preset.identity,
      value: preset.value,
    });
  }
  return snippets;
};

const containsSnippetIdentity = (
  tree: Record<string, unknown>,
  snippet: CanonicalSnippet,
): boolean => {
  if (snippet.kind !== "factor" || snippet.identity === null) return false;
  const collection = tree[snippet.sectionKey];
  return (
    Array.isArray(collection) &&
    collection.some(
      (item) =>
        isRecord(item) &&
        owns(item, snippet.identity!.field) &&
        Object.is(item[snippet.identity!.field], snippet.identity!.value),
    )
  );
};

/**
 * 커서 줄(반쯤 입력한 키나 빈 줄)을 통째로 뺀 원문과, 그 줄이 있던 자리(`anchor`). 마지막 줄이면 앞의
 * EOL을 함께 빼고 anchor는 문서 끝이다. 스니펫은 이 자리에 들어간다(P3-02 리뷰 P1-1: 선행 주석·빈 줄 위로
 * 올라가지 않는다).
 */
const withoutCursorLine = (
  source: string,
  lineStart: number,
  lineEnd: number,
): { text: string; anchor: number } => {
  const eol = source.startsWith("\r\n", lineEnd)
    ? 2
    : source.startsWith("\n", lineEnd)
      ? 1
      : 0;
  if (eol > 0)
    return {
      text: `${source.slice(0, lineStart)}${source.slice(lineEnd + eol)}`,
      anchor: lineStart,
    };
  const previous = source.slice(0, lineStart).replace(/\r?\n$/, "");
  return {
    text: `${previous}${source.slice(lineEnd)}`,
    anchor: previous.length,
  };
};

/**
 * 원문과 다음 원문의 차이를 커서 줄을 포함하는 단일 범위로 만든다. 공통 접두는 커서 줄의 키 시작
 * (`from`)까지만, 공통 접미는 커서 줄 끝(`lineEnd`)부터만 인정하므로 반쯤 입력한 키가 교체 범위에
 * 들어간다(편집기 history가 "sig → signal: …" 한 번으로 남는다).
 */
const singleRangeEdit = (
  source: string,
  next: string,
  from: number,
  lineEnd: number,
): SnippetEdit => {
  let prefix = 0;
  while (
    prefix < from &&
    prefix < next.length &&
    source[prefix] === next[prefix]
  )
    prefix += 1;
  let suffix = 0;
  while (
    suffix < source.length - lineEnd &&
    suffix < next.length - prefix &&
    source[source.length - 1 - suffix] === next[next.length - 1 - suffix]
  )
    suffix += 1;
  const to = source.length - suffix;
  const insert = next.slice(prefix, next.length - suffix);
  const cursor = prefix + insert.length;
  return {
    from: prefix,
    to,
    insert,
    nextSource: next,
    selection: { from: cursor, to: cursor },
  };
};

/**
 * 커서 문맥을 `insert-key`/`insert-item` 연산으로 번역한다(P3-02). 텍스트 조립·들여쓰기·EOL·preflight는
 * 모두 `planSourceOperation`의 몫이고, 이 함수는 (1) 커서 줄의 반쯤 입력한 키를 뺀 원문을 만들고
 * (2) 커서 줄의 위치를 형제 순서(`before`/`index`)로 옮겨 적은 뒤 (3) 결과를 원문 대비 단일 범위
 * 편집으로 되돌린다.
 */
export const planSnippetEdit = (
  source: string,
  format: SourceFormat,
  selection: { from: number; to: number },
  snippet: CanonicalSnippet,
): SnippetEditResult => {
  if (format !== "yaml") return { status: "error", reason: "yaml-only" };
  if (
    !Number.isInteger(selection.from) ||
    !Number.isInteger(selection.to) ||
    selection.from < 0 ||
    selection.to < 0 ||
    selection.from > source.length ||
    selection.to > source.length ||
    selection.from !== selection.to
  )
    return { status: "error", reason: "selection" };
  // 중복 판정은 커서 문맥보다 먼저, 원문 전체로 한다(커서가 어디든 같은 팩터는 한 번만).
  const original = parseSource(source, "yaml");
  if (
    original.status === "ok" &&
    containsSnippetIdentity(original.tree, snippet)
  )
    return { status: "error", reason: "duplicate" };
  const cursor = selection.from;
  const context = describeYamlCursor(source, cursor);
  if (context === null || context.mode !== "key")
    return { status: "error", reason: "cursor-context" };

  const lineStart = source.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
  const foundLineEnd = source.indexOf("\n", cursor);
  const lineEnd =
    foundLineEnd < 0
      ? source.length
      : foundLineEnd > 0 && source[foundLineEnd - 1] === "\r"
        ? foundLineEnd - 1
        : foundLineEnd;
  if (source.slice(cursor, lineEnd).trim() !== "")
    return { status: "error", reason: "cursor-context" };
  if (!/^ *$/.test(source.slice(lineStart, context.from)))
    return { status: "error", reason: "cursor-context" };
  if (!snippet.sectionKey.startsWith(context.prefix))
    return { status: "error", reason: "cursor-context" };

  const { text: stripped, anchor } = withoutCursorLine(
    source,
    lineStart,
    lineEnd,
  );
  const parsed = parseSource(stripped, "yaml");
  const tree = parsed.status === "ok" ? parsed.tree : {};
  if (original.status !== "ok" && containsSnippetIdentity(tree, snippet))
    return { status: "error", reason: "duplicate" };
  // 커서 줄이 빠진 원문에서 커서 줄 뒤에 오던 형제는 offset이 lineStart 이상이다.
  const after = (pointer: string): boolean => {
    const range =
      parsed.keyRanges.get(pointer) ?? parsed.valueRanges.get(pointer);
    return range !== undefined && range.start.offset >= lineStart;
  };
  const sequencePointer = `/${escapePointerSegment(snippet.sectionKey)}`;
  let op: SourceOperation;
  if (context.pointer === "") {
    const before = Object.keys(tree).find((key) =>
      after(`/${escapePointerSegment(key)}`),
    );
    op = {
      kind: "insert-key",
      parentPointer: "",
      key: snippet.sectionKey,
      value: snippet.kind === "section" ? snippet.value : [snippet.value],
      ...(before === undefined ? {} : { before }),
    };
  } else if (
    snippet.kind === "factor" &&
    context.pointer === sequencePointer &&
    context.prefix === ""
  ) {
    const items = tree[snippet.sectionKey];
    const count = Array.isArray(items) ? items.length : 0;
    let index = 0;
    while (index < count && !after(`${sequencePointer}/${index}`)) index += 1;
    op = {
      kind: "insert-item",
      parentPointer: sequencePointer,
      value: snippet.value,
      index,
    };
  } else {
    return { status: "error", reason: "cursor-context" };
  }

  const planned = planSourceOperation(stripped, "yaml", op, {
    eol: detectEol(source),
    anchor,
  });
  if (planned.status === "error") {
    const reason: SnippetEditFailure =
      planned.reason === "exists"
        ? "duplicate"
        : planned.reason === "parse"
          ? "parse"
          : "cursor-context";
    return { status: "error", reason };
  }
  return {
    status: "ok",
    edit: singleRangeEdit(
      source,
      planned.edit.nextSource,
      context.from,
      lineEnd,
    ),
  };
};
