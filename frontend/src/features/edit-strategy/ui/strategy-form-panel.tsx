import {
  useId,
  useRef,
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
  FormControl,
  FormField,
  FormListItem,
  FormProjection,
  FormSection,
} from "../model/form-projection";
import type { CanonicalSnippet } from "../model/canonical-snippets";
import type { DocumentReference } from "../model/document-references";
import type { DocumentDiagnostic } from "../model/document-state";
import {
  addItemOperation,
  addPresetItemOperation,
  draftOf,
  fieldOperation,
  itemKinds,
  itemSection,
  parseDraft,
  removalBlockers,
  removeItemOperation,
  resetOperation,
  unsetOperation,
  type ListSection,
  type ObjectSection,
} from "../model/form-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import type { Scalar } from "../model/source-transactions";
import type { SourceTransactions } from "../model/use-source-transactions";
import { TransactionFeedbackNote } from "./transaction-feedback";
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
  /** 목록 항목 추가가 materialize할 runtime schema(projection과 같은 출처). 없으면 추가 버튼 비활성. */
  schema?: JsonSchema | null;
  /** 현재 parse tree(삭제 가드의 참조 탐색용). 없으면 참조 없음으로 본다. */
  tree?: unknown;
  /** 팩터 카탈로그 preset(스니펫 카탈로그의 factor 항목) — "카탈로그에서 추가" 메뉴. */
  catalogSnippets?: readonly CanonicalSnippet[];
  /** 팩터 항목의 graph를 Graph 화면에서 열기(view=graph, pointer 선택). 없으면 버튼을 그리지 않는다. */
  onOpenGraph?: (pointer: string) => void;
  /** 현재 텍스트가 parse되지 않아 마지막 유효 parse로 그렸다(P4-04). */
  stale?: boolean;
  /** URL `path`(Graph "Form에서 열기" 등). 그 pointer 아래의 목록 항목을 `aria-current`로 강조한다(P5-03). */
  selectedPointer?: string;
};

const UNSET = "__unset__";

const FORM_OWNER = "form";
/** Form 컨트롤이 포커스를 가진 채 적용한다: 편집기로 포커스를 옮기면 컨트롤 blur가 같은 값을 다시 확정한다. */
const NO_FOCUS = { focusEditor: false } as const;

/**
 * 편집 가능한 Form 패널(WORKFLOW P4-02). 모든 변경은 `SourceTransactions.apply` 한 번이고 YAML source에
 * 바로 반영되며 편집기 undo로 되돌린다. 컨트롤 종류·범위·기본값은 projection(=runtime schema)이 정한다.
 * 목록 섹션(factors·rules·parameters)의 편집은 P4-03이 더한다.
 */
export const StrategyFormPanel = ({
  projection,
  transactions,
  catalogs,
  schema = null,
  tree = {},
  catalogSnippets = [],
  onOpenGraph,
  selectedPointer,
  stale = false,
}: StrategyFormPanelProps) => {
  const disabled = transactions.disabled;
  return (
    <section className="strategy-form" aria-label={t("form.panel.label")}>
      <header className="strategy-form__header">
        <div>
          <strong>{t("form.panel.label")}</strong>
          <span>{t("form.panel.notice")}</span>
        </div>
        <span className="strategy-form__badges">
          {stale ? (
            <Badge tone="warn">{t("form.panel.staleBadge")}</Badge>
          ) : null}
          {disabled !== null ? (
            <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
          ) : (
            <Badge tone="ok">{t("form.panel.enabled")}</Badge>
          )}
        </span>
      </header>
      {/* 안내 문단은 role 없음: 실시간 알림(role=status)은 feedback 하나뿐이다(리뷰 DEFECT-P404-010). */}
      {stale ? (
        <p className="strategy-form__state">{t("form.panel.stale")}</p>
      ) : null}
      {disabled === "json" ? (
        <p className="strategy-form__state">
          {t("form.panel.jsonHint")}
        </p>
      ) : null}
      <TransactionFeedbackNote
        feedback={transactions.feedbackFor(FORM_OWNER)}
        owner={FORM_OWNER}
      />
      {projection === null ? (
        <p className="strategy-form__state" role="status">
          {t("form.panel.loading")}
        </p>
      ) : (
        projection.sections.map((section) => (
          <FormSectionView
            key={section.pointer === "" ? "root" : section.pointer}
            section={section}
            schema={schema}
            tree={tree}
            transactions={transactions}
            catalogs={catalogs}
            catalogSnippets={catalogSnippets}
            onOpenGraph={onOpenGraph}
            selectedPointer={selectedPointer}
            disabled={disabled !== null}
          />
        ))
      )}
    </section>
  );
};

const FormSectionView = ({
  section,
  schema,
  tree,
  transactions,
  catalogs,
  catalogSnippets,
  onOpenGraph,
  selectedPointer,
  disabled,
}: {
  section: FormSection;
  schema: JsonSchema | null;
  tree: unknown;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  catalogSnippets: readonly CanonicalSnippet[];
  onOpenGraph: ((pointer: string) => void) | undefined;
  selectedPointer: string | undefined;
  disabled: boolean;
}) => {
  const title = section.key === "" ? t("form.section.root") : section.key;
  const [open, setOpen] = useState(true);
  if (section.kind === "list") {
    return (
      <FormListSectionView
        section={section}
        schema={schema}
        tree={tree}
        transactions={transactions}
        catalogs={catalogs}
        catalogSnippets={catalogSnippets}
        onOpenGraph={onOpenGraph}
        selectedPointer={selectedPointer}
        disabled={disabled}
      />
    );
  }
  return (
    <fieldset className="strategy-form__section" disabled={disabled}>
      <legend>
        <SectionToggle
          title={title}
          open={open}
          onToggle={() => setOpen((value) => !value)}
        />
        {section.written ? null : (
          <span className="strategy-form__hint">
            {` · ${t("form.section.omitted")}`}
          </span>
        )}
        {severityBadge(section)}
      </legend>
      <div hidden={!open}>
        {section.fields.map((field) => (
          <FormFieldRow
            key={field.pointer}
            section={section}
            field={field}
            transactions={transactions}
            catalogs={catalogs}
          />
        ))}
        {/* 중첩 목록(`eligibility.rules`)은 루트 목록과 같은 뷰로 편집한다(P4-05). */}
        {section.lists.map((list) => (
          <FormListSectionView
            key={list.pointer}
            section={list}
            schema={schema}
            tree={tree}
            transactions={transactions}
            catalogs={catalogs}
            catalogSnippets={catalogSnippets}
            onOpenGraph={onOpenGraph}
            selectedPointer={selectedPointer}
            disabled={disabled}
          />
        ))}
      </div>
    </fieldset>
  );
};

/** 섹션 제목 = 접기/펼치기 버튼(키보드: Enter/Space). 접힌 섹션은 DOM에 남긴다(`hidden`). */
const SectionToggle = ({
  title,
  open,
  onToggle,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
}) => (
  <button
    type="button"
    className="strategy-form__toggle"
    aria-expanded={open}
    onClick={onToggle}
  >
    <span aria-hidden="true">{open ? "▾" : "▸"}</span> <code>{title}</code>
  </button>
);

/**
 * 목록 섹션(P4-03): 항목 추가(스키마 materialize, union이면 `kind` 선택), 팩터 카탈로그 preset 추가,
 * 삭제(다른 곳이 참조하면 목록을 보여주고 거부), 항목 필드는 P4-02 컨트롤 재사용.
 */
const FormListSectionView = ({
  section,
  schema,
  tree,
  transactions,
  catalogs,
  catalogSnippets,
  onOpenGraph,
  selectedPointer,
  disabled,
}: {
  section: ListSection;
  schema: JsonSchema | null;
  tree: unknown;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  catalogSnippets: readonly CanonicalSnippet[];
  onOpenGraph: ((pointer: string) => void) | undefined;
  selectedPointer: string | undefined;
  disabled: boolean;
}) => {
  const kinds = schema === null ? null : itemKinds(schema, section);
  const [kind, setKind] = useState<string>("");
  const chosenKind = kinds === null ? null : kind || (kinds[0] ?? "");
  const addOperation =
    schema === null ? null : addItemOperation(schema, section, chosenKind);
  const presets = catalogSnippets.filter(
    (snippet) =>
      snippet.kind === "factor" && snippet.sectionKey === section.key,
  );
  const presetExists = (snippet: CanonicalSnippet): boolean =>
    snippet.identity !== null &&
    section.items.some((item) =>
      item.fields.some(
        (field) =>
          field.key === snippet.identity!.field &&
          field.written &&
          Object.is(field.value, snippet.identity!.value),
      ),
    );
  const [open, setOpen] = useState(true);
  // 추가·preset·삭제(위치 pointer 연산)만 직전 편집의 parse가 따라올 때까지 잠근다(P5-03 리뷰 DEFECT-133-01;
  // 항목 필드·Graph 열기는 열어 둔다 — 3차 P2). 구조 변경 직후의 스칼라 확정은 훅이 pending으로 보류한다.
  const settling = transactions.settling;
  return (
    <fieldset className="strategy-form__section" disabled={disabled}>
      <legend>
        <SectionToggle
          title={section.key}
          open={open}
          onToggle={() => setOpen((value) => !value)}
        />
        <span className="strategy-form__hint">
          {` · ${t("form.list.count").replace("{count}", String(section.items.length))}`}
        </span>
        {severityBadge(section)}
      </legend>
      <div hidden={!open}>
        <div className="strategy-form__control">
          {kinds !== null ? (
            <select
              aria-label={`${section.key} · ${t("form.list.kind")}`}
              value={chosenKind ?? ""}
              onChange={(event) => setKind(event.target.value)}
            >
              {kinds.map((candidate) => (
                <option key={candidate} value={candidate}>
                  {candidate}
                </option>
              ))}
            </select>
          ) : null}
          <Button
            size="small"
            disabled={addOperation === null || settling}
            onClick={() => {
              if (addOperation !== null)
                transactions.apply(
                  addOperation,
                  section.key,
                  FORM_OWNER,
                  NO_FOCUS,
                );
            }}
            aria-label={`${section.key} · ${t("form.list.add")}`}
          >
            {t("form.list.add")}
          </Button>
          {presets.length > 0 ? (
            <select
              aria-label={`${section.key} · ${t("form.list.addFromCatalog")}`}
              value=""
              disabled={settling}
              onChange={(event) => {
                const preset = presets.find(
                  (snippet) => snippet.id === event.target.value,
                );
                if (preset !== undefined)
                  transactions.apply(
                    addPresetItemOperation(section, preset.value),
                    preset.label,
                    FORM_OWNER,
                    NO_FOCUS,
                  );
              }}
            >
              <option value="">{t("form.list.addFromCatalog")}</option>
              {presets.map((snippet) => (
                <option
                  key={snippet.id}
                  value={snippet.id}
                  disabled={presetExists(snippet)}
                >
                  {presetExists(snippet)
                    ? `${snippet.label} · ${t("form.list.presetExists")}`
                    : snippet.label}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        {section.items.length === 0 ? (
          <p className="strategy-form__state">{t("form.list.empty")}</p>
        ) : null}
        {section.items.map((item) => (
          <FormListItemView
            key={item.pointer}
            section={section}
            item={item}
            tree={tree}
            transactions={transactions}
            catalogs={catalogs}
            onOpenGraph={onOpenGraph}
            selectedPointer={selectedPointer}
          />
        ))}
      </div>
    </fieldset>
  );
};

const FormListItemView = ({
  section,
  item,
  tree,
  transactions,
  catalogs,
  onOpenGraph,
  selectedPointer,
}: {
  section: ListSection;
  item: FormListItem;
  tree: unknown;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  onOpenGraph: ((pointer: string) => void) | undefined;
  selectedPointer: string | undefined;
}) => {
  // 삭제 거부 안내는 그 판정을 낸 문서(tree)에만 붙는다. 문서가 바뀌면(재색인 포함) 렌더 중 파생으로
  // 사라진다 — React key가 pointer(인덱스)라 인스턴스가 다른 항목에 재사용될 수 있다(리뷰 P2-2).
  const [blockers, setBlockers] = useState<{
    tree: unknown;
    references: DocumentReference[];
  } | null>(null);
  const blocked = blockers !== null && blockers.tree === tree ? blockers.references : null;
  const asSection = itemSection(section, item);
  const graphField = item.fields.find(
    (field) => field.control.kind === "graph-link",
  );
  const remove = (): void => {
    const references = removalBlockers(tree, item);
    if (references.length > 0) {
      setBlockers({ tree, references });
      return;
    }
    setBlockers(null);
    transactions.apply(
      removeItemOperation(item),
      item.summary,
      FORM_OWNER,
      NO_FOCUS,
    );
  };
  const selected =
    selectedPointer !== undefined &&
    (selectedPointer === item.pointer ||
      selectedPointer.startsWith(`${item.pointer}/`));
  return (
    <section
      className="strategy-form__item"
      aria-label={`${section.key} · ${item.summary}`}
      aria-current={selected ? "true" : undefined}
    >
      <header className="strategy-form__item-header">
        <strong>
          <code>{item.summary}</code>
        </strong>
        {severityBadge(item)}
        {item.branches !== null ? (
          <span className="strategy-form__hint">
            {t("form.list.branchNeeded").replace(
              "{kinds}",
              item.branches.join(", "),
            )}
          </span>
        ) : null}
        {graphField !== undefined && onOpenGraph !== undefined ? (
          <Button
            size="small"
            tone="ghost"
            onClick={() => onOpenGraph(graphField.pointer)}
            aria-label={`${item.summary} · ${t("form.field.openGraph")}`}
          >
            {t("form.field.openGraph")}
          </Button>
        ) : null}
        <Button
          size="small"
          tone="danger"
          onClick={remove}
          disabled={transactions.settling}
          aria-label={`${item.summary} · ${t("form.list.remove")}`}
        >
          {t("form.list.remove")}
        </Button>
      </header>
      {blocked !== null ? (
        <p className="strategy-form__invalid" role="alert">
          {t("form.list.blocked").replace(
            "{pointers}",
            blocked.map((reference) => reference.pointer).join(", "),
          )}
        </p>
      ) : null}
      {item.fields.map((field) => (
        <FormFieldRow
          key={field.pointer}
          section={asSection}
          field={field}
          transactions={transactions}
          catalogs={catalogs}
        />
      ))}
    </section>
  );
};

const severityBadge = (owner: {
  diagnostics: DocumentDiagnostic[];
}): ReactNode => {
  const errors = owner.diagnostics.filter((d) => d.severity === "error");
  const warnings = owner.diagnostics.filter((d) => d.severity === "warning");
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

/** 편집 컨트롤이 없는 행(링크·const)은 `<label for>` 대신 `aria-labelledby`로 이름을 잇는다(리뷰 DEFECT-P402-004). */
const isPassiveControl = (control: FormControl): boolean =>
  control.kind === "graph-link" ||
  control.kind === "list-link" ||
  control.kind === "const";

/**
 * object 섹션의 필드 행 묶음. Graph 편집기(P5-02)가 노드 속성·그래프 설정에 같은 컨트롤을 쓴다 —
 * `owner`로 feedback 슬롯을 나눈다.
 */
export const FormFieldsEditor = ({
  section,
  transactions,
  catalogs,
  owner = FORM_OWNER,
}: {
  section: ObjectSection;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  owner?: string;
}) => (
  <>
    {section.fields.map((field) => (
      <FormFieldRow
        key={field.pointer}
        section={section}
        field={field}
        transactions={transactions}
        catalogs={catalogs}
        owner={owner}
      />
    ))}
  </>
);

const FormFieldRow = ({
  section,
  field,
  transactions,
  catalogs,
  owner = FORM_OWNER,
}: {
  section: ObjectSection;
  field: FormField;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  owner?: string;
}) => {
  const id = useId();
  const labelId = `${id}-label`;
  const [invalid, setInvalid] = useState<string | null>(null);
  const passive = isPassiveControl(field.control);
  // `x-default-from`: 생략하면 backend가 형제 키의 값으로 채운다 → placeholder도 그 값(P4-01 DEFECT-121-06).
  const defaultFromValue =
    field.defaultFrom === null
      ? undefined
      : section.fields.find((sibling) => sibling.key === field.defaultFrom)
          ?.value;
  // 적용 여부를 컨트롤에 돌려준다: 실패한 확정의 재시도 판정은 컨트롤 로컬이다(P4-02 리뷰 009/012 —
  // 공유 feedback 슬롯은 다른 필드가 덮고, label은 목록 항목끼리 겹친다).
  const commit = (value: Scalar): boolean => {
    setInvalid(null);
    return transactions.apply(
      fieldOperation(section, field, value),
      field.key,
      owner,
      NO_FOCUS,
    );
  };
  const description = tOptional(field.descriptionKey ?? "");
  const control = (
    <FieldControl
      id={id}
      labelId={labelId}
      field={field}
      catalogs={catalogs}
      placeholderValue={
        field.written
          ? undefined
          : field.hasDefault
            ? field.defaultValue
            : defaultFromValue
      }
      onCommit={commit}
      onValid={() => setInvalid(null)}
      onInvalid={(reason) => setInvalid(t(`form.invalid.${reason}`))}
    />
  );
  const unset = unsetOperation(section, field);
  // 라벨 내용은 passive 행(링크·const)도 같다: 필수 별표·단위(P4-02 리뷰 010).
  const labelBody = (
    <>
      <code>{field.key}</code>
      {field.required ? <span aria-hidden="true"> *</span> : null}
      {(field.displayUnit ?? field.unit) ? (
        <span className="strategy-form__unit">
          {` · ${field.displayUnit ?? field.unit}`}
        </span>
      ) : null}
    </>
  );
  return (
    <div
      className="strategy-form__field"
      data-written={field.written}
      data-applicable={
        field.applicable === null ? "unknown" : String(field.applicable)
      }
    >
      {passive ? (
        <span
          id={labelId}
          className="strategy-form__label"
          title={field.templatePointer}
        >
          {labelBody}
        </span>
      ) : (
        <label htmlFor={id} title={field.templatePointer}>
          {labelBody}
        </label>
      )}
      <div className="strategy-form__control">
        {control}
        {!passive && field.written && !field.required ? (
          <Button
            size="small"
            tone="ghost"
            onClick={() =>
              transactions.apply(
                resetOperation(field),
                field.key,
                owner,
                NO_FOCUS,
              )
            }
            aria-label={`${field.key} · ${t("form.field.reset")}`}
          >
            {t("form.field.reset")}
          </Button>
        ) : null}
        {!passive && unset !== null && field.value !== null ? (
          <Button
            size="small"
            tone="ghost"
            onClick={() =>
              transactions.apply(unset, field.key, owner, NO_FOCUS)
            }
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
      ) : !field.written && field.defaultFrom !== null ? (
        <p className="strategy-form__hint">
          {t("form.field.defaultFromHint")
            .replace("{key}", field.defaultFrom)
            .replace("{value}", draftOf(defaultFromValue) || "null")}
        </p>
      ) : null}
      {description !== null ? (
        <p className="strategy-form__description">{description}</p>
      ) : null}
    </div>
  );
};

type ControlProps = {
  id: string;
  labelId: string;
  field: FormField;
  catalogs: FormCatalogs;
  /** 미작성 필드의 placeholder 값(runtime schema default 또는 `x-default-from` 형제 값). */
  placeholderValue: unknown;
  /** 값을 트랜잭션으로 넘긴다. 적용됐으면 true(`SourceTransactions.apply`와 같다). */
  onCommit: (value: Scalar) => boolean;
  onValid: () => void;
  onInvalid: (reason: "number" | "integer" | "range" | "date") => void;
};

/** 컨트롤별 커밋 규칙: 텍스트류는 blur/Enter에서 바뀐 값만, 선택류는 변경 즉시. Escape는 입력 취소. */
const FieldControl = (props: ControlProps) => {
  const { id, labelId, field, catalogs, onCommit } = props;
  const { control } = field;
  if (control.kind === "const")
    return (
      <code id={id} aria-labelledby={labelId} className="strategy-form__const">
        {draftOf(control.value)}
      </code>
    );
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
    if (options === null) return <TextualControl {...props} />;
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
      <p id={id} aria-labelledby={labelId} className="strategy-form__hint">
        {control.kind === "graph-link"
          ? t("form.field.graphLink")
          : t("form.field.listLink")}
      </p>
    );
  return <TextualControl {...props} />;
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
  placeholderValue,
  onCommit,
  onValid,
  onInvalid,
}: ControlProps) => {
  const committed = draftOf(field.value);
  const [draft, setDraft] = useState(committed);
  // 같은 입력을 Enter와 blur가 연달아 확정해도 트랜잭션은 한 번이다.
  // 마지막으로 확정한 (draft, committed) 쌍과 그 결과. 같은 committed 값에서 같은 draft를 다시 확정하지
  // 않는다(Enter 뒤 같은 이벤트 안에서 오는 blur까지 동기적으로 막는다). 확정이 실패했으면(`failed`)
  // Enter로만 다시 시도한다 — blur마다 같은 값을 다시 계획하지 않는다(P4-02 리뷰 009/011).
  const submitted = useRef<{
    draft: string;
    committed: string;
    failed: boolean;
  } | null>(null);
  // 트랜잭션이 적용되어 projection 값이 바뀌면 입력을 그 값으로 되돌린다(렌더 중 파생 상태 조정). 단
  // 사용자가 손대지 않은 draft(직전 projection 값 그대로, 또는 방금 확정에 성공한 값)일 때만이다 — 확정 직후
  // 150ms 안에 같은 필드를 지우고 다시 치는 중이면 되돌리지 않는다. 되돌리면 지운 값이 되살아나 키 입력이
  // 뒤에 붙어 `100101` 같은 오값이 확정됐다(Phase 5 감사 DEFECT-P5X-001).
  // `confirmed`는 마지막으로 확정에 성공한 draft(렌더에서 읽으므로 ref가 아니라 state).
  const [seen, setSeen] = useState(committed);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  if (seen !== committed) {
    setSeen(committed);
    if (draft === seen || draft === confirmed) setDraft(committed);
  }
  const { control } = field;
  const submit = (explicit: boolean): void => {
    // 값을 되돌리거나 다시 확정하면 이전 무효 안내는 사라진다(DEFECT-P402-003).
    onValid();
    if (draft === committed) return;
    const last = submitted.current;
    if (
      last !== null &&
      last.draft === draft &&
      last.committed === committed &&
      (!last.failed || !explicit)
    )
      return;
    const parsed = parseDraft(control, draft);
    if (parsed.status === "invalid") {
      onInvalid(parsed.reason);
      return;
    }
    submitted.current = { draft, committed, failed: false };
    const applied = onCommit(parsed.value) !== false;
    if (!applied) submitted.current = { draft, committed, failed: true };
    setConfirmed(applied ? draft : null);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit(true);
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
      placeholder={
        placeholderValue === undefined ? undefined : draftOf(placeholderValue)
      }
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => submit(false)}
      onKeyDown={onKeyDown}
    />
  );
};
