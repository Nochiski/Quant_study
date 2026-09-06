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
  | "initial_cash"
  | "annualization_days"
  | "oos_range"
  | "date_range_unavailable";

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
  benchmarkSecurityId: "005930",
  annualizationDays: "252",
  oosStart: "",
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/u;

/**
 * Parse UI representation only. The backend remains the owner of accepted execution semantics,
 * defaults and final validation; successful fields are sent explicitly for a reproducible run.
 */
export const buildBacktestRunOptions = (
  fields: BacktestRunSettingsFields,
  dateRange: BacktestDateRange | null,
): BacktestRunSettingsResult => {
  const errors: BacktestRunSettingsError[] = [];
  const initialCashKrw = Number(fields.initialCashKrw);
  if (!Number.isFinite(initialCashKrw) || initialCashKrw <= 0)
    errors.push("initial_cash");
  const annualizationDays = Number(fields.annualizationDays);
  if (!Number.isSafeInteger(annualizationDays) || annualizationDays <= 0)
    errors.push("annualization_days");

  const oosStart = fields.oosStart.trim();
  if (oosStart !== "") {
    if (dateRange === null) errors.push("date_range_unavailable");
    else if (
      !ISO_DATE.test(oosStart) ||
      oosStart < dateRange.start ||
      oosStart > dateRange.end
    )
      errors.push("oos_range");
  }

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
