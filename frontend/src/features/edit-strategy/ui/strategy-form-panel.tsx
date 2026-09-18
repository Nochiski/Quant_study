import {
  useId,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import type {
  DatasetFieldProfile,
  FactorDefinition,
} from "../../../shared/api";
import { t, tOptional } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import type {
  FormField,
  FormProjection,
  FormSection,
} from "../model/form-projection";
import {
  draftOf,
  fieldOperation,
  parseDraft,
  resetOperation,
  unsetOperation,
  type ObjectSection,
} from "../model/form-transactions";
import type { Scalar } from "../model/source-transactions";
import type {
  SourceTransactions,
  TransactionFeedback,
} from "../model/use-source-transactions";
import "./strategy-form-panel.css";

export type FormCatalogs = {
  equityFields: readonly DatasetFieldProfile[] | null;
  factors: readonly FactorDefinition[] | null;
};

type StrategyFormPanelProps = {
  /** null이면 runtime schema를 아직 못 받았다. */
  projection: FormProjection | null;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
};

const UNSET = "__unset__";

const FORM_OWNER = "form";

const feedbackText = (feedback: TransactionFeedback): ReactNode => {
  if (feedback.status === "idle" || feedback.owner !== FORM_OWNER) return null;
  if (feedback.status === "applied")
    return (
      <p className="strategy-form__feedback" role="status">
        {t("form.feedback.applied").replace("{label}", feedback.label)}
      </p>
    );
  const reason = tOptional(`form.feedback.${feedback.reason}`);
  return (
    <p
      className="strategy-form__feedback strategy-form__feedback--error"
      role="alert"
    >
      {(reason ?? t("form.feedback.failed")).replace("{label}", feedback.label)}
    </p>
  );
};

/**
 * 편집 가능한 Form 패널(WORKFLOW P4-02). 모든 변경은 `SourceTransactions.apply` 한 번이고 YAML source에
 * 바로 반영되며 편집기 undo로 되돌린다. 컨트롤 종류·범위·기본값은 projection(=runtime schema)이 정한다.
 * 목록 섹션(factors·rules·parameters)의 편집은 P4-03이 더한다.
 */
export const StrategyFormPanel = ({
  projection,
  transactions,
  catalogs,
}: StrategyFormPanelProps) => {
  const disabled = transactions.disabled;
  return (
    <section className="strategy-form" aria-label={t("form.panel.label")}>
      <header className="strategy-form__header">
        <div>
          <strong>{t("form.panel.label")}</strong>
          <span>{t("form.panel.notice")}</span>
        </div>
        {disabled !== null ? (
          <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
        ) : (
          <Badge tone="ok">{t("form.panel.enabled")}</Badge>
        )}
      </header>
      {feedbackText(transactions.feedback)}
      {projection === null ? (
        <p className="strategy-form__state" role="status">
          {t("form.panel.loading")}
        </p>
      ) : (
        projection.sections.map((section) => (
          <FormSectionView
            key={section.pointer === "" ? "root" : section.pointer}
            section={section}
            transactions={transactions}
            catalogs={catalogs}
            disabled={disabled !== null}
          />
        ))
      )}
    </section>
  );
};

const FormSectionView = ({
  section,
  transactions,
  catalogs,
  disabled,
}: {
  section: FormSection;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  disabled: boolean;
}) => {
  const title = section.key === "" ? t("form.section.root") : section.key;
  if (section.kind === "list") {
    return (
      <fieldset className="strategy-form__section" disabled={disabled}>
        <legend>
          <code>{title}</code>
        </legend>
        <p className="strategy-form__state">
          {t("form.list.pending").replace(
            "{count}",
            String(section.items.length),
          )}
        </p>
      </fieldset>
    );
  }
  return (
    <fieldset className="strategy-form__section" disabled={disabled}>
      <legend>
        <code>{title}</code>
        {section.written ? null : (
          <span className="strategy-form__hint">
            {` · ${t("form.section.omitted")}`}
          </span>
        )}
      </legend>
      {section.fields.map((field) => (
        <FormFieldRow
          key={field.pointer}
          section={section}
          field={field}
          transactions={transactions}
          catalogs={catalogs}
          disabled={disabled}
        />
      ))}
    </fieldset>
  );
};

const severityBadge = (field: FormField): ReactNode => {
  const errors = field.diagnostics.filter((d) => d.severity === "error");
  const warnings = field.diagnostics.filter((d) => d.severity === "warning");
  if (errors.length === 0 && warnings.length === 0) return null;
  const first = (errors[0] ?? warnings[0])!;
  return (
    <Badge tone={errors.length > 0 ? "error" : "warn"} title={first.message}>
      {errors.length > 0
        ? t("form.badge.error").replace("{count}", String(errors.length))
        : t("form.badge.warning").replace("{count}", String(warnings.length))}
    </Badge>
  );
};

const FormFieldRow = ({
  section,
  field,
  transactions,
  catalogs,
  disabled,
}: {
  section: ObjectSection;
  field: FormField;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  disabled: boolean;
}) => {
  const id = useId();
  const [invalid, setInvalid] = useState<string | null>(null);
  const commit = (value: Scalar): void => {
    setInvalid(null);
    transactions.apply(
      fieldOperation(section, field, value),
      field.key,
      FORM_OWNER,
    );
  };
  const description = tOptional(field.descriptionKey ?? "");
  const control = (
    <FieldControl
      id={id}
      field={field}
      catalogs={catalogs}
      onCommit={commit}
      onInvalid={(reason) => setInvalid(t(`form.invalid.${reason}`))}
    />
  );
  const unset = unsetOperation(section, field);
  return (
    <div
      className="strategy-form__field"
      data-written={field.written}
      data-applicable={
        field.applicable === null ? "unknown" : String(field.applicable)
      }
    >
      <label htmlFor={id} title={field.templatePointer}>
        <code>{field.key}</code>
        {field.required ? <span aria-hidden="true"> *</span> : null}
        {(field.displayUnit ?? field.unit) ? (
          <span className="strategy-form__unit">
            {` · ${field.displayUnit ?? field.unit}`}
          </span>
        ) : null}
      </label>
      <div className="strategy-form__control">
        {control}
        {field.written && !field.required ? (
          <Button
            size="small"
            tone="ghost"
            onClick={() =>
              transactions.apply(resetOperation(field), field.key, FORM_OWNER)
            }
            aria-label={`${field.key} · ${t("form.field.reset")}`}
          >
            {t("form.field.reset")}
          </Button>
        ) : null}
        {unset !== null && field.value !== null ? (
          <Button
            size="small"
            tone="ghost"
            onClick={() => transactions.apply(unset, field.key, FORM_OWNER)}
            aria-label={`${field.key} · ${t("form.field.unset")}`}
          >
            {t("form.field.unset")}
          </Button>
        ) : null}
        {severityBadge(field)}
      </div>
      {invalid !== null ? (
        <p className="strategy-form__invalid" role="alert">
          {invalid}
        </p>
      ) : null}
      {field.applicable === false ? (
        <p className="strategy-form__hint">{t("form.field.inapplicable")}</p>
      ) : null}
      {!field.written && field.hasDefault ? (
        <p className="strategy-form__hint">
          {t("form.field.defaultHint").replace(
            "{value}",
            draftOf(field.defaultValue) || "null",
          )}
        </p>
      ) : null}
      {description !== null ? (
        <p className="strategy-form__description">{description}</p>
      ) : null}
      {disabled ? null : null}
    </div>
  );
};

type ControlProps = {
  id: string;
  field: FormField;
  catalogs: FormCatalogs;
  onCommit: (value: Scalar) => void;
  onInvalid: (reason: "number" | "integer" | "range" | "date") => void;
};

/** 컨트롤별 커밋 규칙: 텍스트류는 blur/Enter에서 바뀐 값만, 선택류는 변경 즉시. Escape는 입력 취소. */
const FieldControl = ({
  id,
  field,
  catalogs,
  onCommit,
  onInvalid,
}: ControlProps) => {
  const { control } = field;
  if (control.kind === "boolean")
    return (
      <input
        id={id}
        type="checkbox"
        checked={field.value === true}
        onChange={(event) => onCommit(event.target.checked)}
      />
    );
  if (
    control.kind === "enum" ||
    control.kind === "reference" ||
    control.kind === "catalog"
  ) {
    const current =
      typeof field.value === "string"
        ? field.value
        : field.value === null
          ? UNSET
          : "";
    const options =
      control.kind === "enum"
        ? control.values.map((value) => ({ value, label: value }))
        : control.kind === "reference"
          ? control.candidates.map((value) => ({ value, label: value }))
          : catalogOptions(control.catalog, catalogs);
    if (options === null)
      return (
        <TextualControl
          id={id}
          field={field}
          onCommit={onCommit}
          onInvalid={onInvalid}
        />
      );
    const known = options.some((option) => option.value === current);
    return (
      <select
        id={id}
        value={current}
        onChange={(event: ChangeEvent<HTMLSelectElement>) =>
          onCommit(event.target.value === UNSET ? null : event.target.value)
        }
      >
        {field.nullable ? (
          <option value={UNSET}>{t("form.field.unset")}</option>
        ) : null}
        {!known && current !== UNSET ? (
          <option value={current}>
            {current === "" ? t("form.field.unselected") : current}
          </option>
        ) : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }
  if (control.kind === "graph-link" || control.kind === "list-link")
    return (
      <span id={id} className="strategy-form__hint">
        {control.kind === "graph-link"
          ? t("form.field.graphLink")
          : t("form.field.listLink")}
      </span>
    );
  return (
    <TextualControl
      id={id}
      field={field}
      onCommit={onCommit}
      onInvalid={onInvalid}
    />
  );
};

/** 카탈로그 select 항목. 목록이 없는 카탈로그(universe·subgraph)는 null → 텍스트 입력. */
const catalogOptions = (
  catalog: "equity-field" | "universe" | "factor" | "subgraph",
  catalogs: FormCatalogs,
): { value: string; label: string }[] | null => {
  if (catalog === "equity-field" && catalogs.equityFields !== null)
    return catalogs.equityFields.map((profile) => ({
      value: profile.field_id,
      label: `${profile.field_id} · ${profile.label}`,
    }));
  if (catalog === "factor" && catalogs.factors !== null)
    return catalogs.factors.map((factor) => ({
      value: factor.factor_id,
      label: `${factor.factor_id} · ${factor.label}`,
    }));
  return null;
};

const TextualControl = ({
  id,
  field,
  onCommit,
  onInvalid,
}: Omit<ControlProps, "catalogs">) => {
  const committed = draftOf(field.value);
  const [draft, setDraft] = useState(committed);
  // 같은 입력을 Enter와 blur가 연달아 확정해도 트랜잭션은 한 번이다.
  const [submitted, setSubmitted] = useState<string | null>(null);
  // 트랜잭션이 적용되어 projection 값이 바뀌면 입력을 그 값으로 되돌린다(렌더 중 파생 상태 조정).
  const [seen, setSeen] = useState(committed);
  if (seen !== committed) {
    setSeen(committed);
    setDraft(committed);
    setSubmitted(null);
  }
  const { control } = field;
  const submit = (): void => {
    if (draft === committed || draft === submitted) return;
    const parsed = parseDraft(control, draft);
    if (parsed.status === "invalid") {
      onInvalid(parsed.reason);
      return;
    }
    setSubmitted(draft);
    onCommit(parsed.value);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      setDraft(committed);
    }
  };
  const numeric = control.kind === "number";
  return (
    <input
      id={id}
      type={control.kind === "date" ? "date" : numeric ? "number" : "text"}
      inputMode={numeric ? "decimal" : undefined}
      step={numeric ? (control.integer ? 1 : "any") : undefined}
      min={numeric && control.min?.inclusive ? control.min.value : undefined}
      max={numeric && control.max?.inclusive ? control.max.value : undefined}
      value={draft}
      placeholder={field.written ? undefined : draftOf(field.defaultValue)}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={submit}
      onKeyDown={onKeyDown}
    />
  );
};
