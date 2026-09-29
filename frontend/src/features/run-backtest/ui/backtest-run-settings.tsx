import { useEffect, useId, useRef, type SyntheticEvent } from "react";

import { t, tDescription } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import {
  DATE_INPUT_MAXIMUM,
  DATE_INPUT_MINIMUM,
  type RunEnvironmentField,
  type RunEnvironmentFieldError,
} from "../model/run-environment";
import {
  OOS_START_FIELD,
  type BacktestRunSettingsError,
} from "../model/run-settings";
import {
  runEnvironmentErrorMessage,
  runOptionErrorMessage,
} from "../model/run-settings-problems";
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

/** 날짜 칸이 덜 채워졌는지: 연도만 쳤거나 없는 날짜(2월 31일)면 값은 빈 문자열이고 `badInput` 이 선다. */
const isIncompleteDate = (event: SyntheticEvent<HTMLInputElement>): boolean =>
  event.currentTarget.type === "date" && event.currentTarget.validity.badInput;

const EnvironmentInput = ({
  field,
  value,
  error,
  onChange,
  onIncompleteChange,
}: {
  field: RunEnvironmentField;
  value: string;
  error: RunEnvironmentFieldError | undefined;
  onChange: (value: string) => void;
  /** `leaving` 은 칸을 떠날 때다. 칸 안에서 난 일(키를 뗌·값이 바뀜)은 실행 게이트만 고친다. */
  onIncompleteChange: (incomplete: boolean, leaving: boolean) => void;
}) => {
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const description = tDescription(field.descriptionKey);
  // 날짜를 덜 친 칸은 값이 여전히 빈 문자열이라 값만 보면 "값을 정하세요."가 된다(#264). 브라우저는 칸
  // 안에서 자리를 채우는 동안 input 이벤트를 내지 않으므로, 키를 뗄 때·값이 바뀔 때·칸을 떠날 때 `badInput` 을
  // 읽어 컨트롤러에 알린다. 이 칸들은 필수라 빈 값 자체가 오류이므로, 칸 안에서 읽은 덜 친 상태도 검증에
  // 넣어 칸 아래·요약 띠가 "값을 정하세요." 대신 날짜 문장을 쓰게 한다. 칸을 떠나지 않고 누른 백테스트
  // 단축키도 막는다(#266 재리뷰 P3-1, #297 재리뷰 P2-1). 치는 도중에 오류를 띄우지 않는 것은 비워 둘 수
  // 있는 OOS 칸뿐이다(#270 P3-R2, #297 리뷰 P3-1).
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
              : field.control === "date"
                ? DATE_INPUT_MINIMUM
                : undefined
          }
          max={
            field.control === "number"
              ? (field.maximum ?? field.exclusiveMaximum ?? undefined)
              : field.control === "date"
                ? DATE_INPUT_MAXIMUM
                : undefined
          }
          value={value}
          onChange={(event) => {
            onIncompleteChange(isIncompleteDate(event), false);
            onChange(event.target.value);
          }}
          onKeyUp={(event) =>
            onIncompleteChange(isIncompleteDate(event), false)
          }
          onBlur={(event) => onIncompleteChange(isIncompleteDate(event), true)}
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
    setDateIncomplete,
    panel,
    setPanelOpen,
  } = controller;
  const { open, focus } = panel;
  // OOS 칸의 덜 친 상태. 실행 설정 날짜 칸과 같은 규칙이다(`setDateIncomplete`).
  const markOosIncomplete = (
    event: SyntheticEvent<HTMLInputElement>,
    leaving: boolean,
  ): void =>
    setDateIncomplete(OOS_START_FIELD, isIncompleteDate(event), leaving);
  const optionErrors: ReadonlySet<BacktestRunSettingsError> = new Set(
    result.errors,
  );
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
                onIncompleteChange={(incomplete, leaving) =>
                  setDateIncomplete(field.name, incomplete, leaving)
                }
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
              aria-invalid={optionErrors.has("initial_cash") ? true : undefined}
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
              aria-invalid={
                optionErrors.has("annualization_days") ? true : undefined
              }
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
              min={DATE_INPUT_MINIMUM}
              max={DATE_INPUT_MAXIMUM}
              data-run-field={OOS_START_FIELD}
              aria-invalid={
                optionErrors.has("oos_out_of_range") ||
                optionErrors.has("oos_incomplete")
                  ? true
                  : undefined
              }
              value={fields.oosStart}
              onChange={(event) => {
                markOosIncomplete(event, false);
                setField("oosStart", event.target.value);
              }}
              onKeyUp={(event) => markOosIncomplete(event, false)}
              onBlur={(event) => markOosIncomplete(event, true)}
            />
            <small className="backtest-settings__hint">
              {t("backtest.settings.oosStart.hint")}
            </small>
          </label>
        </fieldset>
        {result.valid ? null : (
          <ul className="backtest-settings__errors" role="alert">
            {result.errors.map((error) => (
              <li key={error}>{runOptionErrorMessage(error)}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
};
