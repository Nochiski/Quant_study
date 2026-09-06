import { useState } from "react";

import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import type { BacktestRunSettingsError } from "../model/run-settings";
import type { BacktestRunSettingsController } from "../model/use-backtest-run-settings";
import "./backtest-run-settings.css";

type BacktestRunSettingsProps = {
  controller: BacktestRunSettingsController;
  disabled?: boolean;
};

const errorMessage = (error: BacktestRunSettingsError): string =>
  t(`backtest.settings.error.${error}`);

/** Execution assumptions are independent from StrategySpec authoring and sent as RunSpec fields. */
export const BacktestRunSettings = ({
  controller,
  disabled = false,
}: BacktestRunSettingsProps) => {
  const { fields, setField, result, dateRange } = controller;
  const [open, setOpen] = useState(false);
  return (
    <details
      className="backtest-settings"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary aria-label={t("backtest.settings.open")}>
        {t("backtest.settings.title")}
        <Badge tone={result.valid ? "neutral" : "error"}>
          {result.valid
            ? t("backtest.settings.ready")
            : t("backtest.settings.invalid")}
        </Badge>
      </summary>
      <div className="backtest-settings__popover" hidden={!open}>
        <fieldset disabled={disabled}>
          <legend className="sr-only">{t("backtest.settings.title")}</legend>
          <label>
            <span>{t("backtest.settings.core")}</span>
            <select
              value={fields.core}
              onChange={(event) =>
                setField(
                  "core",
                  event.target.value === "python" ? "python" : "rust",
                )
              }
            >
              <option value="rust">{t("backtest.settings.core.rust")}</option>
              <option value="python">
                {t("backtest.settings.core.python")}
              </option>
            </select>
          </label>
          <label>
            <span>{t("backtest.settings.initialCash")}</span>
            <input
              inputMode="decimal"
              step="any"
              type="number"
              value={fields.initialCashKrw}
              onChange={(event) =>
                setField("initialCashKrw", event.target.value)
              }
            />
          </label>
          <label>
            <span>{t("backtest.settings.benchmark")}</span>
            <input
              autoComplete="off"
              spellCheck={false}
              value={fields.benchmarkSecurityId}
              onChange={(event) =>
                setField("benchmarkSecurityId", event.target.value)
              }
            />
          </label>
          <label>
            <span>{t("backtest.settings.annualizationDays")}</span>
            <input
              inputMode="numeric"
              step="1"
              type="number"
              value={fields.annualizationDays}
              onChange={(event) =>
                setField("annualizationDays", event.target.value)
              }
            />
          </label>
          <label>
            <span>{t("backtest.settings.oosStart")}</span>
            <input
              type="date"
              value={fields.oosStart}
              onChange={(event) => setField("oosStart", event.target.value)}
            />
          </label>
        </fieldset>
        <p className="backtest-settings__range">
          {dateRange === null
            ? t("backtest.settings.rangeUnavailable")
            : `${dateRange.start} → ${dateRange.end}`}
        </p>
        {result.valid ? null : (
          <ul className="backtest-settings__errors" role="alert">
            {result.errors.map((error) => (
              <li key={error}>{errorMessage(error)}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
};
