/**
 * Form 컨트롤 → source 트랜잭션 번역(WORKFLOW P4-02, spec D6). 순수 함수: 컨트롤이 확정한 값을
 * `SourceOperation` 하나로 바꾸고, 입력 텍스트를 스키마 컨트롤 규칙으로 읽는다. 텍스트 편집과 preflight는
 * `planSourceOperation`의 몫이고 여기서는 fragment를 조립하지 않는다.
 */
import { findReferences, type DocumentReference } from "./document-references";
import type {
  FormControl,
  FormField,
  FormListItem,
  FormSection,
} from "./form-projection";
import {
  materializeSchemaValue,
  resolveRef,
  UnsupportedSchemaShape,
  type JsonSchema,
  schemaFacts,
} from "./schema-navigator";
import type { Scalar, SourceOperation } from "./source-transactions";

export type ObjectSection = Extract<FormSection, { kind: "object" }>;
export type ListSection = Extract<FormSection, { kind: "list" }>;

export type DraftParse =
  | { status: "ok"; value: Scalar }
  | { status: "invalid"; reason: "number" | "integer" | "range" | "date" };

/** 입력 텍스트를 컨트롤 규칙(스키마 type·범위·format)으로 읽는다. 빈 텍스트는 빈 문자열/무효다. */
export const parseDraft = (control: FormControl, draft: string): DraftParse => {
  if (control.kind === "number") {
    const trimmed = draft.trim();
    if (
      trimmed === "" ||
      !/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(trimmed)
    )
      return { status: "invalid", reason: "number" };
    const value = Number(trimmed);
    if (!Number.isFinite(value)) return { status: "invalid", reason: "number" };
    if (control.integer && !Number.isInteger(value))
      return { status: "invalid", reason: "integer" };
    const belowMin =
      control.min !== null &&
      (control.min.inclusive
        ? value < control.min.value
        : value <= control.min.value);
    const aboveMax =
      control.max !== null &&
      (control.max.inclusive
        ? value > control.max.value
        : value >= control.max.value);
    if (belowMin || aboveMax) return { status: "invalid", reason: "range" };
    return { status: "ok", value };
  }
  if (control.kind === "date") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.trim()))
      return { status: "invalid", reason: "date" };
    return { status: "ok", value: draft.trim() };
  }
  return { status: "ok", value: draft };
};

/** 현재 값을 입력 텍스트로. null·미작성은 빈 문자열. */
export const draftOf = (value: unknown): string =>
  value === null || value === undefined
    ? ""
    : typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : JSON.stringify(value);

/**
 * 값 확정 → 연산 하나. 작성된 필드는 `replace-scalar`, 미작성 필드는 섹션에 `insert-key`, 섹션 자체가
 * 없으면 루트에 섹션을 `{ key: value }`로 `insert-key`(트랜잭션 한 번, undo 한 번).
 */
export const fieldOperation = (
  section: ObjectSection,
  field: FormField,
  value: Scalar,
): SourceOperation => {
  if (field.written)
    return { kind: "replace-scalar", pointer: field.pointer, value };
  if (section.written || section.pointer === "")
    return {
      kind: "insert-key",
      parentPointer: section.pointer,
      key: field.key,
      value,
    };
  return {
    kind: "insert-key",
    parentPointer: "",
    key: section.key,
    value: { [field.key]: value },
  };
};

/** "기본값으로": 작성된 키를 지운다(부모가 비면 `planRemove`가 `{}`로 접는다). */
export const resetOperation = (field: FormField): SourceOperation => ({
  kind: "remove",
  pointer: field.pointer,
});

/** "설정 안 함": nullable 필드를 null로. 미작성이고 기본값이 null이면 이미 그 상태라 연산이 없다. */
export const unsetOperation = (
  section: ObjectSection,
  field: FormField,
): SourceOperation | null => {
  if (!field.nullable) return null;
  if (!field.written && field.defaultValue === null) return null;
  return fieldOperation(section, field, null);
};

/** 목록 항목을 필드 편집의 "섹션"으로 본다: 항목은 문서에 있으므로 `written`이고 필드는 그 아래 `insert-key`/`replace-scalar`. */
export const itemSection = (
  section: ListSection,
  item: FormListItem,
): ObjectSection => ({
  kind: "object",
  pointer: item.pointer,
  key: section.key,
  written: true,
  fields: item.fields,
  // 항목 안의 배열(`choices` 등)은 `list-link` 필드로 남는다(중첩 목록은 object 섹션 전용, P4-05).
  lists: [],
  diagnostics: item.diagnostics,
});

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const decodeSegment = (segment: string): string =>
  segment.replaceAll("~1", "/").replaceAll("~0", "~");

/** `itemSchemaPointer`가 가리키는 노드(`$ref` 해소 전). */
const itemSchemaRaw = (
  schema: JsonSchema,
  section: ListSection,
): JsonSchema | null => {
  let node: unknown = schema;
  for (const segment of section.itemSchemaPointer.slice(1).split("/")) {
    if (!isRecord(node)) return null;
    node = node[decodeSegment(segment)];
  }
  return isRecord(node) ? resolveRef(schema, node) : null;
};

/** union 분기의 discriminator 값(`kind` const)과 해소된 분기 스키마. 그래프 노드 union도 같은 모양(P5-01). */
export const branchKind = (
  schema: JsonSchema,
  member: unknown,
): [string, JsonSchema] | null => {
  if (!isRecord(member)) return null;
  const branch = resolveRef(schema, member);
  if (branch === null) return null;
  const properties = isRecord(branch.properties) ? branch.properties : {};
  // discriminator도 `$ref`를 풀고 `schemaFacts`로 읽는다(감사 DEFECT-P4X-003, P4-05 리뷰 P2-1:
  // const 해석 규칙의 owner는 하나이고 backend가 `kind`를 정의 참조로 바꿔도 분기를 찾는다).
  const kindNode = isRecord(properties.kind)
    ? resolveRef(schema, properties.kind)
    : null;
  const marker = kindNode === null ? undefined : schemaFacts(kindNode).constValue;
  return typeof marker === "string" ? [marker, branch] : null;
};

/** 목록 스키마가 union이면 고를 수 있는 `kind` 목록, 아니면 null. */
export const itemKinds = (
  schema: JsonSchema,
  section: ListSection,
): string[] | null => {
  const node = itemSchemaRaw(schema, section);
  if (node === null || !Array.isArray(node.oneOf)) return null;
  return node.oneOf
    .map((member) => branchKind(schema, member))
    .filter((entry): entry is [string, JsonSchema] => entry !== null)
    .map(([kind]) => kind);
};

/**
 * 항목 추가: 스키마에서 최소 항목을 materialize해 `insert-item`(끝에). union이면 `kind`가 필요하다.
 * 스키마를 따라갈 수 없거나 재귀·과대면 null(버튼 비활성).
 */
export const addItemOperation = (
  schema: JsonSchema,
  section: ListSection,
  kind: string | null = null,
): SourceOperation | null => {
  const raw = itemSchemaRaw(schema, section);
  if (raw === null) return null;
  let node: JsonSchema | null = raw;
  if (Array.isArray(raw.oneOf)) {
    node =
      raw.oneOf
        .map((member) => branchKind(schema, member))
        .find((entry) => entry !== null && entry[0] === kind)?.[1] ?? null;
  }
  if (node === null) return null;
  try {
    return appendOperation(section, materializeSchemaValue(schema, node));
  } catch (error) {
    if (error instanceof UnsupportedSchemaShape) return null;
    throw error;
  }
};

/** 미리 만든 값(팩터 카탈로그 preset 등)을 항목으로 추가. */
export const addPresetItemOperation = (
  section: ListSection,
  value: unknown,
): SourceOperation => appendOperation(section, value);

/**
 * 항목을 목록 끝에 넣는 연산. 목록 키가 문서에 없으면(새 전략 starter·생략형 문서) 키를 열면서 첫 항목을
 * 넣는 `insert-key` 한 번이다 — 스니펫(`planSnippetEdit`)·object 섹션(`fieldOperation`)과 같은 규칙
 * (리뷰 DEFECT-125-01: `insert-item`은 키가 없으면 `not-found`라 활성 버튼이 언제나 실패했다).
 * 중첩 목록(`eligibility.rules`)은 부모 object까지 없으면 루트에 부모를 `{ key: [item] }`로 연다(P4-05).
 */
const appendOperation = (
  section: ListSection,
  value: unknown,
): SourceOperation => {
  if (section.written)
    return { kind: "insert-item", parentPointer: section.pointer, value };
  if (section.parentWritten || section.parentPointer === "")
    return {
      kind: "insert-key",
      parentPointer: section.parentPointer,
      key: section.key,
      value: [value],
    };
  return {
    kind: "insert-key",
    parentPointer: "",
    key: decodeSegment(section.parentPointer.slice(1)),
    value: { [section.key]: [value] },
  };
};

/**
 * 항목 삭제 전 참조 검사: 항목의 identity(`<namespace>_id`)를 문서 다른 곳이 참조하면 그 pointer 목록을
 * 돌려주고 삭제는 거부한다(D7 삭제 가드와 같은 규칙). identity는 스키마 `x-authoring-identity` 필드
 * (`item.identityKey`) 우선, 없으면 카탈로그 참조가 아닌 첫 `*_id` 문자열 필드(`parameter_id`·`node_id`).
 * `field_id` 같은 카탈로그 필드는 정의가 아니라 참조라 identity가 아니다(리뷰 P2-1).
 */
export const removalBlockers = (
  tree: unknown,
  item: FormListItem,
): DocumentReference[] => {
  const writtenString = (field: FormField): boolean =>
    field.written && typeof field.value === "string";
  const identity =
    item.fields.find(
      (field) => field.key === item.identityKey && writtenString(field),
    ) ??
    item.fields.find(
      (field) =>
        field.key.endsWith("_id") &&
        field.control.kind !== "catalog" &&
        writtenString(field),
    );
  if (identity === undefined) return [];
  const namespace = identity.key.slice(0, -"_id".length);
  return findReferences(
    tree,
    namespace,
    identity.value as string,
    item.pointer,
  );
};

export const removeItemOperation = (item: FormListItem): SourceOperation => ({
  kind: "remove",
  pointer: item.pointer,
});
