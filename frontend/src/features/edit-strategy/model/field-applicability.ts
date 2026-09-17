import type { ApplicableWhen } from "../../../shared/api";
import { t } from "../../../shared/config";
import { valueAtPointer } from "../../../shared/lib/yaml12";

/**
 * backend `FIELD_APPLICABILITY` 한 행의 읽기 전용 투영(WORKFLOW P2-03, spec D4).
 *
 * 조건표의 owner는 backend다. 이 모듈은 contract `FieldContract.applicable_when`(또는 같은 모양의
 * runtime schema `x-applicable-when`)을 받아 "지금 읽히는가"만 판정한다. 조건 필드가 문서에 없으면
 * 호출자가 넘긴 `resolveDefault`(backend가 발행한 contract/schema `default`)로 판정하고, 발행된
 * 기본값도 없으면 판정 불가(null)다. frontend가 기본값을 지어내는 일은 없다.
 *
 * Form 배지는 이 모듈을 쓰지 않는다: backend compile이 낸 `strategy.field.inapplicable` 진단
 * pointer가 곧 배지다(명시 기재·기본값과 다름·`owned_by_error` 억제를 backend가 판정).
 */
export type ApplicabilityCondition = {
  pointer: string;
  /** `portfolio.side`처럼 문구용 경로. */
  path: string;
  equals: string | null;
  notNull: boolean;
  /** 현재 문서(또는 발행된 기본값)에서 성립하는가. 판정 근거가 없으면 null. */
  holds: boolean | null;
  /** 문서에 없어 발행된 기본값으로 판정했는가. */
  fromDefault: boolean;
};

export type FieldApplicability = {
  conditions: readonly ApplicabilityCondition[];
  descriptionKey: string;
  ownedByError: string | null;
  /** 모든 조건이 성립하면 true, 하나라도 거짓이면 false, 판정 불가 조건만 남으면 null. */
  applicable: boolean | null;
};

/** backend가 발행한 기본값 조회. `has`가 false면 그 pointer에 발행된 기본값이 없다. */
export type DefaultResolver = (pointer: string) => {
  has: boolean;
  value: unknown;
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

const judge = (
  value: unknown,
  equals: string | null,
  notNull: boolean,
): boolean => {
  if (notNull) return value !== null && value !== undefined;
  // backend `ApplicabilityCondition.holds_for`: `str(value) == equals` (enum 문자열 값).
  return value !== null && value !== undefined && String(value) === equals;
};

const conditionOf = (
  tree: unknown,
  pointer: string,
  equals: string | null,
  notNull: boolean,
  resolveDefault: DefaultResolver | undefined,
): { holds: boolean | null; fromDefault: boolean } => {
  const written =
    tree === undefined || tree === null
      ? { present: false, value: undefined }
      : valueAtPointer(tree, pointer);
  if (written.present) {
    return { holds: judge(written.value, equals, notNull), fromDefault: false };
  }
  const fallback = resolveDefault?.(pointer);
  if (fallback?.has) {
    return { holds: judge(fallback.value, equals, notNull), fromDefault: true };
  }
  return { holds: null, fromDefault: false };
};

export const projectApplicability = (
  when: ApplicableWhen,
  tree: unknown,
  resolveDefault?: DefaultResolver,
): FieldApplicability => {
  const conditions = when.all_of.map((condition) => ({
    pointer: condition.pointer,
    path: condition.pointer.replace(/^\//, "").replaceAll("/", "."),
    equals: condition.equals,
    notNull: condition.not_null,
    ...conditionOf(
      tree,
      condition.pointer,
      condition.equals,
      condition.not_null,
      resolveDefault,
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
