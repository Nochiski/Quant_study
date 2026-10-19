/**
 * 안내의 팩터 담기(RD-04-D). 팩터 카탈로그(backend registry)를 분류(`category`)로 묶고, 담을 수 있는
 * 팩터는 스니펫 카탈로그(`buildCanonicalSnippetCatalog`)가 만든 preset 값 그대로 목록 끝에 넣는다. 분류·
 * 가용성·preset 모양은 모두 backend 응답이고 여기서는 묶기만 한다.
 */
import type { FactorDefinition } from "../../../shared/api";
import type { CanonicalSnippet } from "./canonical-snippets";
import type { FormListSection } from "./form-projection";
import { addPresetItemOperation } from "./form-transactions";
import type { SourceOperation } from "./source-transactions";

export type FactorCategory = FactorDefinition["category"];

/** 분류 하나와 그 안의 팩터(카탈로그 순서). 분류 순서는 카탈로그에 처음 나온 순서다. */
export type FactorGroup = {
  category: FactorCategory;
  factors: readonly FactorDefinition[];
};

export const groupFactors = (
  factors: readonly FactorDefinition[],
): FactorGroup[] => {
  const groups = new Map<FactorCategory, FactorDefinition[]>();
  for (const factor of factors) {
    const group = groups.get(factor.category) ?? [];
    group.push(factor);
    groups.set(factor.category, group);
  }
  return [...groups].map(([category, items]) => ({ category, factors: items }));
};

/** 담기 판정: 담을 연산, 또는 못 담는 이유(가용성·이미 담음·preset 없음). */
export type FactorAddition =
  | { status: "ready"; operation: SourceOperation }
  | {
      status: "blocked";
      reason: "catalog_only" | "unavailable" | "added" | "no-preset";
    };

/**
 * 팩터 하나를 담는 연산. preset 은 스니펫 카탈로그의 `factor:<id>` 항목이고, 이미 같은 identity 가 목록에
 * 있으면 막는다(스니펫의 중복 판정과 같은 규칙: identity 값이 같다).
 */
export const factorAddition = (
  factor: FactorDefinition,
  snippets: readonly CanonicalSnippet[],
  list: FormListSection | null,
): FactorAddition => {
  if (factor.availability !== "implemented")
    return { status: "blocked", reason: factor.availability };
  const snippet = snippets.find(
    (candidate) =>
      candidate.kind === "factor" &&
      candidate.identity?.value === factor.factor_id,
  );
  if (snippet === undefined || snippet.identity === null || list === null)
    return { status: "blocked", reason: "no-preset" };
  const { field, value } = snippet.identity;
  const added = list.items.some((item) =>
    item.fields.some(
      (candidate) => candidate.key === field && Object.is(candidate.value, value),
    ),
  );
  if (added) return { status: "blocked", reason: "added" };
  return {
    status: "ready",
    operation: addPresetItemOperation(list, snippet.value),
  };
};
