import type { FieldApplicability } from "../../../shared/api";
import { t } from "../../../shared/config";

/**
 * 전략 문서 칸 적용 조건의 문구(spec D4). 판정은 `shared/api` 의 `projectApplicability` 한 벌이 한다.
 *
 * Form은 회색 처리(`FormField.applicable`)에만 그 판정을 쓰고, 경고 배지는 backend compile이 낸
 * `strategy.field.inapplicable` 진단 pointer가 낸다(명시 기재·기본값과 다름·`owned_by_error` 억제를
 * backend가 판정). Inspector도 같은 판정을 조건 목록으로 보여준다.
 */

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
