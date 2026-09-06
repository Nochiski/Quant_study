import { useCallback, useMemo, useState } from "react";

import {
  buildBacktestRunOptions,
  DEFAULT_BACKTEST_RUN_SETTINGS,
  type BacktestDateRange,
  type BacktestRunSettingsFields,
} from "./run-settings";

export const useBacktestRunSettings = (dateRange: BacktestDateRange | null) => {
  const [fields, setFields] = useState<BacktestRunSettingsFields>(
    DEFAULT_BACKTEST_RUN_SETTINGS,
  );
  const result = useMemo(
    () => buildBacktestRunOptions(fields, dateRange),
    [dateRange, fields],
  );
  const setField = useCallback(
    <Key extends keyof BacktestRunSettingsFields>(
      field: Key,
      value: BacktestRunSettingsFields[Key],
    ): void => setFields((current) => ({ ...current, [field]: value })),
    [],
  );

  return {
    fields,
    setField,
    result,
    dateRange,
    requestOptions: result.options,
  };
};

export type BacktestRunSettingsController = ReturnType<
  typeof useBacktestRunSettings
>;
