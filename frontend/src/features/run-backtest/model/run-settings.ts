import type { BacktestRunSpec } from "../../../shared/api";

export type BacktestDateRange = {
  start: string;
  end: string;
};

export type BacktestRunSettingsFields = {
  core: NonNullable<BacktestRunSpec["core"]>;
  initialCashKrw: string;
  benchmarkSecurityId: string;
  annualizationDays: string;
  oosStart: string;
};

export type BacktestRunSettingsError =
  "initial_cash" | "annualization_days" | "date_range_unavailable";

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
 */
export const buildBacktestRunOptions = (
  fields: BacktestRunSettingsFields,
  dateRange: BacktestDateRange | null,
): BacktestRunSettingsResult => {
  const errors: BacktestRunSettingsError[] = [];
  const initialCashText = fields.initialCashKrw.trim();
  const initialCashKrw = Number(initialCashText);
  if (initialCashText === "" || !Number.isFinite(initialCashKrw))
    errors.push("initial_cash");
  const annualizationText = fields.annualizationDays.trim();
  const annualizationDays = Number(annualizationText);
  // JSON numbers are JavaScript numbers at this boundary. Reject integers that cannot be
  // represented losslessly before they can mutate accepted-request provenance. Positivity and
  // the business range remain backend-owned semantics.
  if (annualizationText === "" || !Number.isSafeInteger(annualizationDays))
    errors.push("annualization_days");

  const oosStart = fields.oosStart.trim();
  if (oosStart !== "" && dateRange === null)
    errors.push("date_range_unavailable");

  if (errors.length > 0) return { valid: false, options: null, errors };

  return {
    valid: true,
    errors: [],
    options: {
      core: fields.core,
      initial_cash: initialCashKrw,
      benchmark_security_id: fields.benchmarkSecurityId.trim() || null,
      annualization_days: annualizationDays,
      metric_windows:
        oosStart === "" || dateRange === null
          ? []
          : [
              {
                scope: "out_of_sample",
                start: oosStart,
                end: dateRange.end,
                label: `OOS ${oosStart}`,
              },
            ],
    },
  };
};
