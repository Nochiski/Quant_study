import { useId } from "react";

import type { ExperimentRequest } from "../../../entities/experiment";
import { t, type MessageKey } from "../../../shared/config";
import { Button } from "../../../shared/ui";

export type Constraint = NonNullable<ExperimentRequest["constraints"]>[number];
type Operator = Constraint["operator"];

const OPERATORS: Record<Operator, MessageKey> = {
  lt: "experiments.new.operator.lt",
  le: "experiments.new.operator.le",
  gt: "experiments.new.operator.gt",
  ge: "experiments.new.operator.ge",
  eq: "experiments.new.operator.eq",
  ne: "experiments.new.operator.ne",
};

// 오른쪽을 다른 탐색 파라미터로 고를 때 select 값 앞에 붙인다. 상수 입력과 구별한다.
const PARAMETER_PREFIX = "parameter:";
const VALUE_CHOICE = "value";

/**
 * 탐색 축 사이의 조합 제약(로드맵 M6). 양쪽은 탐색하는 파라미터만 고를 수 있고, 허용 판정(종류·크기 비교·
 * 만족하는 조합이 있는지)은 backend 미리 계산이 한다 — 화면은 규칙을 복제하지 않는다. 상수는 입력 원문
 * 그대로 들고 있고, 요청 값으로 읽는 일은 파라미터 정의를 아는 폼이 한다.
 */
export const ConstraintEditor = ({
  explored,
  constraints,
  onChange,
}: {
  explored: readonly string[];
  constraints: readonly Constraint[];
  onChange: (next: Constraint[]) => void;
}) => {
  const legendId = useId();
  const update = (index: number, patch: Partial<Constraint>) =>
    onChange(
      constraints.map((constraint, position) =>
        position === index ? { ...constraint, ...patch } : constraint,
      ),
    );
  return (
    <fieldset className="data-list-page__filter" aria-labelledby={legendId}>
      <legend id={legendId}>{t("experiments.new.constraints")}</legend>
      {constraints.map((constraint, index) => {
        const right =
          constraint.right_parameter != null
            ? `${PARAMETER_PREFIX}${constraint.right_parameter}`
            : VALUE_CHOICE;
        const label = `${t("experiments.new.constraints")} ${index + 1}`;
        return (
          <span key={index} role="group" aria-label={label}>
            <select
              aria-label={`${label}: ${t("experiments.new.constraintLeft")}`}
              value={constraint.left}
              onChange={(event) => update(index, { left: event.target.value })}
            >
              {explored.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
            <select
              aria-label={`${label}: ${t("experiments.new.constraintOperator")}`}
              value={constraint.operator}
              onChange={(event) =>
                update(index, { operator: event.target.value as Operator })
              }
            >
              {Object.entries(OPERATORS).map(([value, key]) => (
                <option key={value} value={value}>
                  {t(key)}
                </option>
              ))}
            </select>
            <select
              aria-label={`${label}: ${t("experiments.new.constraintRight")}`}
              value={right}
              onChange={(event) => {
                const value = event.target.value;
                update(
                  index,
                  value.startsWith(PARAMETER_PREFIX)
                    ? {
                        right_parameter: value.slice(PARAMETER_PREFIX.length),
                        right_value: null,
                      }
                    : { right_parameter: null, right_value: "" },
                );
              }}
            >
              <option value={VALUE_CHOICE}>
                {t("experiments.new.constraintValue")}
              </option>
              {explored.map((id) => (
                <option key={id} value={`${PARAMETER_PREFIX}${id}`}>
                  {id}
                </option>
              ))}
            </select>
            {constraint.right_parameter == null ? (
              <input
                aria-label={`${label}: ${t("experiments.new.constraintValue")}`}
                value={String(constraint.right_value ?? "")}
                onChange={(event) =>
                  update(index, { right_value: event.target.value })
                }
              />
            ) : null}
            <Button
              onClick={() =>
                onChange(
                  constraints.filter((_, position) => position !== index),
                )
              }
            >
              {t("experiments.new.removeConstraint")}
            </Button>
          </span>
        );
      })}
      <Button
        disabled={explored.length === 0}
        onClick={() =>
          onChange([
            ...constraints,
            {
              left: explored[0]!,
              operator: "lt",
              right_parameter: explored[1] ?? null,
              right_value: explored[1] === undefined ? "" : null,
            },
          ])
        }
      >
        {t("experiments.new.addConstraint")}
      </Button>
    </fieldset>
  );
};
