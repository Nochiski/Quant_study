import {
  runEnvironmentName,
  type RunEnvironmentField,
} from "../../../entities/backtest";
import { t } from "../../../shared/config";
import {
  DATE_INPUT_MAXIMUM,
  DATE_INPUT_MINIMUM,
  type RunEnvironmentFieldError,
} from "./run-environment";
import type { BacktestRunSettingsResult } from "./run-settings";

/**
 * 실행을 막는 칸 하나(DEFECT-242-01). `target` 은 패널 입력의 `data-run-field` 값이라 "이 칸으로 가기"가 그
 * 칸에 초점을 옮긴다. 실행 설정 칸은 필드 이름, 실행 옵션 칸은 오류 코드(`initial_cash` 등)다.
 */
export type RunSettingsProblem = {
  target: string;
  kind: "missing" | "invalid";
  /** 칸 이름(스키마 라벨, 단위 없이). */
  name: string;
  /** 칸 이름과 이유를 담은 한 문장. */
  sentence: string;
};

const boundOf = (
  field: RunEnvironmentField,
  error: RunEnvironmentFieldError,
): number | null => {
  if (error === "minimum") return field.minimum;
  if (error === "exclusiveMinimum") return field.exclusiveMinimum;
  if (error === "maximum") return field.maximum;
  if (error === "exclusiveMaximum") return field.exclusiveMaximum;
  return null;
};

/** 칸 옆 오류 문장. 범위에는 스키마의 표시 단위를 붙인다(예: "0bp 이상이어야 합니다."). */
export const runEnvironmentErrorMessage = (
  field: RunEnvironmentField,
  error: RunEnvironmentFieldError,
): string => {
  const bound = boundOf(field, error);
  return t(`backtest.settings.environment.error.${error}`)
    .replace(
      "{bound}",
      bound === null ? "" : `${bound}${field.displayUnit ?? ""}`,
    )
    .replace("{minimum}", DATE_INPUT_MINIMUM)
    .replace("{maximum}", DATE_INPUT_MAXIMUM);
};

/**
 * 실행을 막는 칸을 패널 순서대로 모은다: 실행 설정 칸(스키마 순서) 다음 실행 옵션 칸. 실행 설정 전체가
 * 무효라는 뜻의 `environment` 오류는 칸별 문제로 이미 드러나므로 따로 세지 않는다.
 */
export const runSettingsProblems = (
  fields: readonly RunEnvironmentField[],
  errors: Readonly<Record<string, RunEnvironmentFieldError>>,
  result: BacktestRunSettingsResult,
): RunSettingsProblem[] => [
  ...fields.flatMap((field): RunSettingsProblem[] => {
    const error = errors[field.name];
    if (error === undefined) return [];
    const name = runEnvironmentName(field);
    return [
      error === "required"
        ? {
            target: field.name,
            kind: "missing",
            name,
            sentence: t("backtest.settings.problem.missing").replace(
              "{field}",
              name,
            ),
          }
        : {
            target: field.name,
            kind: "invalid",
            name,
            sentence: t("backtest.settings.problem.invalid")
              .replace("{field}", name)
              .replace("{reason}", runEnvironmentErrorMessage(field, error)),
          },
    ];
  }),
  ...result.errors
    .filter((error) => error !== "environment")
    .map((error): RunSettingsProblem => ({
      target: error,
      kind: "invalid",
      name: error,
      sentence: t(`backtest.settings.error.${error}`),
    })),
];

/**
 * 막힌 이유 한 문장. 빈 칸뿐이면 그 칸 이름을 모두 적고("실행 설정에서 시작일·종료일·유니버스 칸을
 * 채우세요."), 값이 틀린 칸이 있으면 패널 순서의 첫 칸 이름과 이유를 적고 나머지 개수를 붙인다.
 */
export const runSettingsBlockedReason = (
  problems: readonly RunSettingsProblem[],
): string | null => {
  const [first] = problems;
  if (first === undefined) return null;
  if (problems.every((problem) => problem.kind === "missing"))
    return t("backtest.settings.incomplete").replace(
      "{fields}",
      problems.map((problem) => problem.name).join("·"),
    );
  return problems.length === 1
    ? first.sentence
    : `${first.sentence} ${t("backtest.settings.problem.more").replace(
        "{count}",
        String(problems.length - 1),
      )}`;
};
