/**
 * Form 컨트롤 → source 트랜잭션 번역(WORKFLOW P4-02, spec D6). 순수 함수: 컨트롤이 확정한 값을
 * `SourceOperation` 하나로 바꾸고, 입력 텍스트를 스키마 컨트롤 규칙으로 읽는다. 텍스트 편집과 preflight는
 * `planSourceOperation`의 몫이고 여기서는 fragment를 조립하지 않는다.
 */
import type { DocumentState } from "./document-state";
import type { FormControl, FormField, FormSection } from "./form-projection";
import type { Scalar, SourceOperation } from "./source-transactions";

export type ObjectSection = Extract<FormSection, { kind: "object" }>;

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

export type FormDisabledReason = "json" | "syntax" | "composing" | "editor";

/** 컨트롤을 잠그는 이유(우선순위: 문서 형식 → IME → 구문 → 편집기). `enabled`면 null. */
export const formDisabledReason = (
  state: DocumentState,
  enabled: boolean,
): FormDisabledReason | null => {
  if (enabled) return null;
  if (state.format !== "yaml") return "json";
  if (state.composing) return "composing";
  if (
    state.parse === null ||
    state.parse.status !== "ok" ||
    state.parsedVersion !== state.sourceVersion
  )
    return "syntax";
  return "editor";
};
