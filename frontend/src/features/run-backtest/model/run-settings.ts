import type { BacktestRunSpec } from "../../../shared/api";
import type { RunEnvironmentValidation } from "./run-environment";

export type BacktestRunSettingsFields = {
  core: NonNullable<BacktestRunSpec["core"]>;
  initialCashKrw: string;
  benchmarkSecurityId: string;
  annualizationDays: string;
  oosStart: string;
};

/**
 * `environment` 는 실행 설정 칸 중 하나라도 비었거나 규칙을 어겼다는 뜻이다(칸별 사유는 패널이 보인다).
 * `oos_out_of_range` 는 OOS 시작일이 실행 기간 밖이다 — 기간의 owner 는 실행 설정이다(schema 1.2).
 */
export type BacktestRunSettingsError =
  "initial_cash" | "annualization_days" | "environment" | "oos_out_of_range";

export type BacktestRunOptions = Omit<
  BacktestRunSpec,
  "strategy" | "strategy_source"
>;

export type BacktestRunSettingsResult =
  | { valid: true; options: BacktestRunOptions; errors: [] }
  | {
      valid: false;
      options: null;
      errors: BacktestRunSettingsError[];
    };

export const DEFAULT_BACKTEST_RUN_SETTINGS: BacktestRunSettingsFields = {
  core: "rust",
  initialCashKrw: "100000000",
  // 종목 ID 어휘는 연결된 equity 어댑터가 정한다(mock `sec-005930-1`, 실데이터 `005930:1`). frontend 가
  // 특정 어휘를 기본값으로 굽으면 다른 어댑터에서 백테스트가 시작조차 못 하므로(이슈 #154) 비워 둔다 —
  // 비우면 벤치마크 없이 실행한다.
  benchmarkSecurityId: "",
  annualizationDays: "252",
  oosStart: "",
};

/**
 * Parse UI representation only. The backend remains the owner of accepted execution semantics,
 * defaults and final validation; successful fields are sent explicitly for a reproducible run.
 * 실행 설정(`environment`)은 패널이 스키마로 검증한 값을 그대로 싣는다(P3-02).
 */
export const buildBacktestRunOptions = (
  fields: BacktestRunSettingsFields,
  environment: RunEnvironmentValidation,
): BacktestRunSettingsResult => {
  const errors: BacktestRunSettingsError[] = [];
  const initialCashText = fields.initialCashKrw.trim();
  const initialCashKrw = Number(initialCashText);
  // 0 이하는 backend `BacktestRunSpec.__post_init__` 가 거절한다(이슈 #260). 패널이 먼저 막아 배지가
  // "준비됨"인데 시작이 거절되는 일을 없앤다. 최종 판정은 여전히 backend 다.
  if (
    initialCashText === "" ||
    !Number.isFinite(initialCashKrw) ||
    initialCashKrw <= 0
  )
    errors.push("initial_cash");
  const annualizationText = fields.annualizationDays.trim();
  const annualizationDays = Number(annualizationText);
  // JSON numbers are JavaScript numbers at this boundary. Reject integers that cannot be
  // represented losslessly before they can mutate accepted-request provenance.
  if (
    annualizationText === "" ||
    !Number.isSafeInteger(annualizationDays) ||
    annualizationDays <= 0
  )
    errors.push("annualization_days");
  if (!environment.valid) errors.push("environment");

  const oosStart = fields.oosStart.trim();
  const range = environment.valid ? environment.environment : null;
  if (
    oosStart !== "" &&
    range !== null &&
    (oosStart < range.start || oosStart > range.end)
  )
    errors.push("oos_out_of_range");

  if (errors.length > 0 || range === null)
    return { valid: false, options: null, errors };

  return {
    valid: true,
    errors: [],
    options: {
      core: fields.core,
      initial_cash: initialCashKrw,
      benchmark_security_id: fields.benchmarkSecurityId.trim() || null,
      annualization_days: annualizationDays,
      environment: range,
      metric_windows:
        oosStart === ""
          ? []
          : [
              {
                scope: "out_of_sample",
                start: oosStart,
                end: range.end,
                label: `OOS ${oosStart}`,
              },
            ],
    },
  };
};
