/**
 * 내 팩터 라이브러리(US-CS-08)를 전략 문서에 넣고 바꾸는 연산. 라이브러리는 참조가 아니라 **복사**다
 * (2026-10-19 결정): 꺼내 쓰면 그 버전의 팩터 항목과 그래프가 읽는 파라미터 정의가 문서에 들어가고,
 * 새 버전으로 바꾸기는 그 항목의 `graph` 교체 한 번이다. 모든 연산은 기존 source 트랜잭션으로 적용돼
 * 되돌리기 한 번에 취소된다. 버전 비교(같음·예전·고쳐 씀)는 backend `matches` 가 소유한다.
 */
import { escapePointerSegment, valueAtPointer } from "../../../shared/lib/yaml12";
import type { FormListSection, FormProjection } from "./form-projection";
import { addPresetItemOperation } from "./form-transactions";
import type { SourceOperation } from "./source-transactions";

/** 라이브러리 버전 한 개에서 문서에 넣을 몫. */
export type LibraryVersionCopy = {
  factor: Record<string, unknown>;
  parameters: readonly Record<string, unknown>[];
};

export type LibraryInsertion =
  | { status: "ready"; operations: SourceOperation[] }
  | {
      status: "blocked";
      reason: "added" | "unavailable" | "parameter-conflict" | "no-list";
    };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** 같은 JSON 값인가(키 순서 무관). 파라미터 정의가 같으면 문서의 것을 그대로 쓴다. */
const sameJson = (left: unknown, right: unknown): boolean => {
  if (Array.isArray(left) && Array.isArray(right))
    return (
      left.length === right.length &&
      left.every((item, index) => sameJson(item, right[index]))
    );
  if (isRecord(left) && isRecord(right)) {
    const keys = Object.keys(left);
    return (
      keys.length === Object.keys(right).length &&
      keys.every((key) => Object.hasOwn(right, key) && sameJson(left[key], right[key]))
    );
  }
  return Object.is(left, right);
};

/** Form 투영에서 루트 목록 섹션(`factors`·`parameters`)을 찾는다. 이름은 섹션 키 그대로다. */
export const rootList = (
  projection: FormProjection | null,
  key: string,
): FormListSection | null => {
  const section = projection?.sections.find(
    (candidate) => candidate.kind === "list" && candidate.pointer === `/${escapePointerSegment(key)}`,
  );
  return section?.kind === "list" ? section : null;
};

const documentList = (tree: unknown, key: string): unknown[] => {
  const found = valueAtPointer(tree, `/${escapePointerSegment(key)}`);
  return found.present && Array.isArray(found.value) ? found.value : [];
};

/**
 * 버전이 읽는 파라미터 가운데 문서에 없는 것을 넣는 연산. 같은 id 가 이미 있으면 정의가 같을 때만 그대로
 * 쓰고, 다르면 null(넣지 않는다 — 이름을 바꾸면 그래프의 참조까지 고쳐야 해서 사용자가 정하게 둔다).
 */
const parameterOperations = (
  tree: unknown,
  parameters: FormListSection | null,
  wanted: readonly Record<string, unknown>[],
): SourceOperation[] | null => {
  const existing = documentList(tree, "parameters").filter(isRecord);
  const missing: Record<string, unknown>[] = [];
  for (const parameter of wanted) {
    const same = existing.find((item) => item.parameter_id === parameter.parameter_id);
    if (same === undefined) missing.push(parameter);
    else if (!sameJson(same, parameter)) return null;
  }
  if (missing.length === 0) return [];
  if (parameters === null) return null;
  if (parameters.written)
    return missing.map((value) => ({
      kind: "insert-item",
      parentPointer: parameters.pointer,
      value,
    }));
  return [
    {
      kind: "insert-key",
      parentPointer: parameters.parentPointer,
      key: parameters.key,
      value: missing,
    },
  ];
};

/** 라이브러리 팩터를 문서 `factors` 끝에 복사해 넣는다. 같은 키(`factor_id`)가 있으면 막는다. */
export const libraryInsertion = (
  copy: LibraryVersionCopy,
  available: boolean,
  tree: unknown,
  projection: FormProjection | null,
): LibraryInsertion => {
  if (!available) return { status: "blocked", reason: "unavailable" };
  const factors = rootList(projection, "factors");
  if (factors === null) return { status: "blocked", reason: "no-list" };
  const key = copy.factor.factor_id;
  if (
    documentList(tree, "factors").some(
      (item) => isRecord(item) && item.factor_id === key,
    )
  )
    return { status: "blocked", reason: "added" };
  const parameters = parameterOperations(
    tree,
    rootList(projection, "parameters"),
    copy.parameters,
  );
  if (parameters === null)
    return { status: "blocked", reason: "parameter-conflict" };
  return {
    status: "ready",
    operations: [addPresetItemOperation(factors, copy.factor), ...parameters],
  };
};

/** 문서 팩터 하나의 `graph` 를 라이브러리 새 버전으로 바꾼다(빠진 파라미터는 함께 넣는다). */
export const libraryReplacement = (
  factorIndex: number,
  copy: LibraryVersionCopy,
  tree: unknown,
  projection: FormProjection | null,
): SourceOperation[] | null => {
  const pointer = `/factors/${factorIndex}`;
  const item = valueAtPointer(tree, pointer);
  if (!item.present || !isRecord(item.value) || !isRecord(copy.factor.graph))
    return null;
  const parameters = parameterOperations(
    tree,
    rootList(projection, "parameters"),
    copy.parameters,
  );
  if (parameters === null) return null;
  return [
    ...(Object.hasOwn(item.value, "graph")
      ? [{ kind: "remove", pointer: `${pointer}/graph` } as const]
      : []),
    { kind: "insert-key", parentPointer: pointer, key: "graph", value: copy.factor.graph },
    ...parameters,
  ];
};

/** 저장할 팩터 항목과 그 그래프가 읽는 문서 파라미터 정의. 판정·검증은 backend 가 compile 로 한다. */
export const factorForLibrary = (
  tree: unknown,
  itemPointer: string,
): LibraryVersionCopy | null => {
  const item = valueAtPointer(tree, itemPointer);
  if (!item.present || !isRecord(item.value)) return null;
  return {
    factor: item.value,
    parameters: documentList(tree, "parameters").filter(isRecord),
  };
};
