import { useEffect, useId, useRef } from "react";

import { t, tDescription } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import type {
  RunEnvironmentField,
  RunEnvironmentFieldError,
} from "../model/run-environment";
import type { BacktestRunSettingsError } from "../model/run-settings";
import { runEnvironmentErrorMessage } from "../model/run-settings-problems";
import type { BacktestRunSettingsController } from "../model/use-backtest-run-settings";
import "./backtest-run-settings.css";
import {
  runEnvironmentLabel,
  runEnvironmentOptionLabel,
} from "../../../entities/backtest";

type BacktestRunSettingsProps = {
  controller: BacktestRunSettingsController;
  disabled?: boolean;
};

const errorMessage = (error: BacktestRunSettingsError): string =>
  t(`backtest.settings.error.${error}`);

const EnvironmentInput = ({
  field,
  value,
  error,
  onChange,
}: {
  field: RunEnvironmentField;
  value: string;
  error: RunEnvironmentFieldError | undefined;
  onChange: (value: string) => void;
}) => {
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const description = tDescription(field.descriptionKey);
  const describedBy =
    [description === null ? null : hintId, error === undefined ? null : errorId]
      .filter((id): id is string => id !== null)
      .join(" ") || undefined;
  // 한 줄 뜻·오류는 label 밖에 둔다 — label 안에 두면 접근 가능한 이름이 뜻 문장까지 늘어난다.
  const common = {
    id: inputId,
    "aria-describedby": describedBy,
    "aria-invalid": error === undefined ? undefined : true,
    "aria-required": field.required || undefined,
    "data-run-field": field.name,
  } as const;
  return (
    <div className="backtest-settings__field">
      <label htmlFor={inputId}>{runEnvironmentLabel(field)}</label>
      {field.control === "select" ? (
        <select
          {...common}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {field.options.map((option) => (
            <option key={option} value={option}>
              {runEnvironmentOptionLabel(field, option)}
            </option>
          ))}
        </select>
      ) : (
        <input
          {...common}
          autoComplete="off"
          spellCheck={false}
          type={
            field.control === "number"
              ? "number"
              : field.control === "date"
                ? "date"
                : "text"
          }
          inputMode={field.control === "number" ? "decimal" : undefined}
          step={field.control === "number" ? "any" : undefined}
          min={
            field.control === "number"
              ? (field.minimum ?? field.exclusiveMinimum ?? undefined)
              : undefined
          }
          max={
            field.control === "number"
              ? (field.maximum ?? field.exclusiveMaximum ?? undefined)
              : undefined
          }
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {description === null ? null : (
        <small id={hintId} className="backtest-settings__hint">
          {description}
        </small>
      )}
      {error === undefined ? null : (
        <small id={errorId} className="backtest-settings__field-error">
          {runEnvironmentErrorMessage(field, error)}
        </small>
      )}
    </div>
  );
};

/**
 * 실행 설정 패널. 실행 환경(시장·빈도·기간·유니버스·체결·비용·결측)은 실행 설정 스키마가 그리는
 * 칸이고, 그 아래 실행 옵션(core·초기 자본·벤치마크·연환산·OOS)은 실행 요청(`BacktestRunSpec`)의
 * 필드다. 둘 다 전략 문서 밖이라 바꿔도 전략 revision 이 늘지 않는다(spec D6).
 */
export const BacktestRunSettings = ({
  controller,
  disabled = false,
}: BacktestRunSettingsProps) => {
  const {
    fields,
    setField,
    result,
    schemaStatus,
    environmentFields,
    environmentValues,
    environmentErrors,
    setEnvironmentValue,
    panel,
    setPanelOpen,
  } = controller;
  const { open, focus } = panel;
  const popoverRef = useRef<HTMLDivElement>(null);
  // 요약 띠·차단 안내가 "이 칸으로 가기"를 요청하면 패널이 열린 뒤 그 칸에 초점을 옮긴다. 요청 한 번
  // (nonce)에 한 번만 움직여, 사용자가 패널을 닫았다 다시 열 때 초점을 빼앗지 않는다.
  const handledNonce = useRef<number | null>(null);
  const focusNonce = focus?.nonce ?? null;
  const focusField = focus?.field ?? null;
  useEffect(() => {
    if (!open || focusNonce === null || focusField === null) return;
    if (handledNonce.current === focusNonce) return;
    handledNonce.current = focusNonce;
    const target = popoverRef.current?.querySelector<HTMLElement>(
      `[data-run-field="${CSS.escape(focusField)}"]`,
    );
    target?.focus();
    target?.scrollIntoView?.({ block: "nearest" });
  }, [focusField, focusNonce, open]);
  return (
    <details
      className="backtest-settings"
      open={open}
      onToggle={(event) => setPanelOpen(event.currentTarget.open)}
    >
      <summary aria-label={t("backtest.settings.open")}>
        {t("backtest.settings.title")}
        <Badge tone={result.valid ? "neutral" : "error"}>
          {result.valid
            ? t("backtest.settings.ready")
            : t("backtest.settings.invalid")}
        </Badge>
      </summary>
      <div
        ref={popoverRef}
        className="backtest-settings__popover"
        hidden={!open}
      >
        <fieldset
          disabled={disabled}
          className="backtest-settings__environment"
        >
          <legend>{t("backtest.settings.environment")}</legend>
          <p className="backtest-settings__note">
            {t("backtest.settings.environment.note")}
          </p>
          {schemaStatus === "ready" ? (
            environmentFields.map((field) => (
              <EnvironmentInput
                key={field.name}
                field={field}
                value={environmentValues[field.name] ?? ""}
                error={environmentErrors[field.name]}
                onChange={(value) => setEnvironmentValue(field.name, value)}
              />
            ))
          ) : (
            <p
              className="backtest-settings__note"
              role={schemaStatus === "error" ? "alert" : "status"}
            >
              {schemaStatus === "error"
                ? t("backtest.settings.environment.schemaError")
                : t("backtest.settings.environment.schemaLoading")}
            </p>
          )}
        </fieldset>
        <fieldset disabled={disabled}>
          <legend>{t("backtest.settings.options")}</legend>
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
              data-run-field="initial_cash"
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
            <small className="backtest-settings__hint">
              {t("backtest.settings.benchmark.hint")}
            </small>
          </label>
          <label>
            <span>{t("backtest.settings.annualizationDays")}</span>
            <input
              inputMode="numeric"
              step="1"
              type="number"
              data-run-field="annualization_days"
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
              data-run-field="oos_out_of_range"
              value={fields.oosStart}
              onChange={(event) => setField("oosStart", event.target.value)}
            />
            <small className="backtest-settings__hint">
              {t("backtest.settings.oosStart.hint")}
            </small>
          </label>
        </fieldset>
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
