import type { ApplicableWhen, FieldContract } from "../../../shared/api";
import { t } from "../../../shared/config";
import { pointerSegments } from "../../../shared/lib/yaml12";

/**
 * backend `FIELD_APPLICABILITY` 한 행의 읽기 전용 투영(WORKFLOW P2-03, spec D4).
 *
 * 조건표의 owner는 backend다. 이 모듈은 contract `FieldContract.applicable_when`(또는 같은 모양의
 * runtime schema `x-applicable-when`)을 받아 **현재 parse tree 값**으로 "지금 읽히는가"만 판정한다.
 * 조건을 새로 만들거나 기본값을 채워 넣지 않는다: tree에 없는 pointer는 판정 불가(null)다.
 */
export type ApplicabilityCondition = {
  pointer: string;
  /** `portfolio.side`처럼 문구용 경로. */
  path: string;
  equals: string | null;
  notNull: boolean;
  /** 현재 문서에서 성립하는가. tree가 없거나 값이 없으면 null. */
  holds: boolean | null;
};

export type FieldApplicability = {
  conditions: readonly ApplicabilityCondition[];
  descriptionKey: string;
  ownedByError: string | null;
  /** 모든 조건이 성립하면 true, 하나라도 거짓이면 false, 판정 불가 조건만 남으면 null. */
  applicable: boolean | null;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** runtime schema의 `x-applicable-when`은 untyped JSON이라 경계에서 모양을 확인한다. */
export const isApplicableWhen = (value: unknown): value is ApplicableWhen =>
  isRecord(value) &&
  Array.isArray(value.all_of) &&
  value.all_of.every(
    (condition) =>
      isRecord(condition) &&
      typeof condition.pointer === "string" &&
      (condition.equals === null || typeof condition.equals === "string") &&
      typeof condition.not_null === "boolean",
  ) &&
  typeof value.description_key === "string" &&
  (value.owned_by_error === null || typeof value.owned_by_error === "string");

const readPointer = (
  tree: unknown,
  pointer: string,
): { present: boolean; value: unknown } => {
  let current = tree;
  for (const segment of pointerSegments(pointer)) {
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(segment) || Number(segment) >= current.length)
        return { present: false, value: undefined };
      current = current[Number(segment)];
    } else if (isRecord(current) && Object.hasOwn(current, segment)) {
      current = current[segment];
    } else {
      return { present: false, value: undefined };
    }
  }
  return { present: true, value: current };
};

const conditionHolds = (
  tree: unknown,
  pointer: string,
  equals: string | null,
  notNull: boolean,
): boolean | null => {
  if (tree === undefined || tree === null) return null;
  const { present, value } = readPointer(tree, pointer);
  if (notNull) return present && value !== null && value !== undefined;
  // 문서에 명시되지 않은 조건 필드는 backend 기본값이 결정한다. frontend는 기본값을 알지 못하므로
  // 판정하지 않는다(null).
  if (!present || value === null || value === undefined) return null;
  return String(value) === equals;
};

export const projectApplicability = (
  when: ApplicableWhen,
  tree: unknown,
): FieldApplicability => {
  const conditions = when.all_of.map((condition) => ({
    pointer: condition.pointer,
    path: condition.pointer.replace(/^\//, "").replaceAll("/", "."),
    equals: condition.equals,
    notNull: condition.not_null,
    holds: conditionHolds(
      tree,
      condition.pointer,
      condition.equals,
      condition.not_null,
    ),
  }));
  const applicable = conditions.some((condition) => condition.holds === false)
    ? false
    : conditions.every((condition) => condition.holds === true)
      ? true
      : null;
  return {
    conditions,
    descriptionKey: when.description_key,
    ownedByError: when.owned_by_error,
    applicable,
  };
};

/** "portfolio.side = long_short 그리고 portfolio.selection_method = top_n" 같은 문구. */
export const describeApplicabilityConditions = (
  applicability: FieldApplicability,
): string =>
  applicability.conditions
    .map((condition) =>
      condition.equals === null
        ? t("contract.applicable.condition.set").replace(
            "{path}",
            condition.path,
          )
        : `${condition.path} = ${condition.equals}`,
    )
    .join(t("contract.applicable.and"));

/**
 * contract의 모든 조건 행을 pointer별로 판정한다. Form 투영이 필드 옆에 "읽히지 않음"을 붙일 때 쓴다.
 * branch 행(노드 분기)은 조건표에 없으므로 template pointer만 다룬다.
 */
export const applicabilityByPointer = (
  contract: readonly FieldContract[],
  tree: unknown,
): ReadonlyMap<string, FieldApplicability> => {
  const result = new Map<string, FieldApplicability>();
  for (const row of contract) {
    if (row.applicable_when == null || row.branch) continue;
    result.set(row.pointer, projectApplicability(row.applicable_when, tree));
  }
  return result;
};
