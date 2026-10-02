/**
 * 그래프 1수준(파이프라인) 캔버스(WORKFLOW P4-02, spec D9). `pipeline-projection.ts` 의 카드 모델을 그대로
 * 그린다: 맨 위 한 문장 요약, 단계 열, 카드 문장 안의 컨트롤, 규칙 추가·삭제, "5 실행" 안내. 컨트롤·확정·
 * 되돌리기·항목 추가·삭제는 Form 과 한 경로다(`FieldControl`·`useFieldCommit`·`FieldActions`·`listAddition`·
 * `useItemRemoval`, 연산은 `form-transactions.ts`). 어느 필드를 어떤 문장에 넣을지는 i18n 카드 틀
 * (`cardTemplate`)이 정하고, 카드는 YAML 식별자(스키마 키·pointer)를 보이지 않는다 — 카탈로그·참조 선택지도
 * 요약과 같은 이름 풀이(`pipelineNames`)로 이름만 보인다.
 */
import { useId, useMemo, type ReactNode } from "react";

import { t, tDescription, tName, tOptional } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import {
  catalogProfiles,
  type FormField,
  type FormListItem,
  type FormListSection,
} from "../model/form-projection";
import { coversPointer } from "../model/diagnostic-navigation";
import {
  draftOf,
  itemSection,
  listAddition,
  renameIdentity,
} from "../model/form-transactions";
import {
  cardTemplate,
  itemName,
  pipelineNames,
  placeName,
  projectPipeline,
  sentencePieces,
  strategySummary,
  unstagedNames,
  type CatalogNames,
  type PipelineRow,
  type PipelineStage,
  type SummaryNames,
} from "../model/pipeline-projection";
import { recipeSummary } from "../model/recipe-projection";
import type { JsonSchema } from "../model/schema-navigator";
import {
  NO_FOCUS,
  type CommitPlanner,
  useFieldCommit,
  useItemRemoval,
} from "../model/use-field-editing";
import type { FormProjectionState } from "../model/use-form-projection";
import {
  useRevealSelection,
  useRevealedOpen,
} from "../model/use-reveal-selection";
import type { SourceTransactions } from "../model/use-source-transactions";
import {
  DiagnosticNotes,
  FieldActions,
  FieldControl,
  FormFieldsEditor,
  SeverityBadge,
  type FormCatalogs,
  type SliderRange,
} from "./strategy-form-panel";
import { TransactionFeedbackNote } from "./transaction-feedback";
import "./pipeline-panel.css";

/** 캔버스의 feedback owner. Form(`form`)·Graph(`graph`)와 슬롯을 나눈다. */
const PIPELINE_OWNER = "pipeline";

type CardContext = {
  /** 카탈로그·참조 선택지의 이름 풀이(요약 띠와 같은 것). */
  names: SummaryNames;
  schema: JsonSchema;
  /** 항목 삭제 거부(참조) 판정이 읽는 parse tree. */
  tree: unknown;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  selectedPointer: string | undefined;
  revealSignal: number | undefined;
  onOpenGraph: ((pointer: string) => void) | undefined;
};

export const PipelinePanel = ({
  form,
  schema,
  transactions,
  catalogs,
  onOpenGraph,
  selectedPointer,
  revealSignal,
}: {
  /** Form 투영(`useFormProjection`): stale parse 와 진단 규칙을 한 번 판정한 결과. */
  form: FormProjectionState;
  /** runtime schema. null 이면 아직 못 받았다. */
  schema: JsonSchema | null;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  /** 팩터의 그래프를 고급 편집기에서 연다(pointer 선택). 없으면 버튼을 그리지 않는다. */
  onOpenGraph?: (pointer: string) => void;
  selectedPointer?: string;
  revealSignal?: number;
}) => {
  const container = useRevealSelection<HTMLElement>(
    selectedPointer,
    revealSignal,
  );
  const pipeline = useMemo(
    () =>
      schema === null || form.projection === null
        ? null
        : projectPipeline(schema, form.projection, form.tree),
    [schema, form],
  );
  const catalogName: CatalogNames = (catalog, value) =>
    catalogProfiles(catalogs, catalog)?.find(
      (profile) => profile.field_id === value,
    )?.label ?? null;
  const disabled = transactions.disabled;
  const context: CardContext | null =
    pipeline === null || schema === null
      ? null
      : {
          names: pipelineNames(pipeline, catalogName),
          schema,
          tree: form.tree,
          transactions,
          catalogs,
          selectedPointer,
          revealSignal,
          onOpenGraph,
        };
  // 단계 없는 루트 스칼라는 문서 속성이다. 단계 마커를 추가하거나 다른 설정의 owner를 바꾸지 않는다.
  const documentProperties = pipeline?.unstaged.find(
    (section) => section.kind === "object" && section.pointer === "",
  );
  const documentSection =
    documentProperties?.kind === "object"
      ? {
          ...documentProperties,
          fields: documentProperties.fields.filter(
            (field) => field.control.kind !== "const",
          ),
        }
      : null;
  return (
    <section
      ref={container}
      className="pipeline"
      aria-label={t("graph.pipeline.label")}
    >
      <header className="pipeline__header">
        <p className="pipeline__summary">
          <strong>{t("graph.pipeline.summary")}</strong>{" "}
          {pipeline === null
            ? t(
                form.firstParsePending
                  ? "form.panel.firstParsePending"
                  : "form.panel.loading",
              )
            : strategySummary(pipeline, catalogName)}
        </p>
        {form.stale ? (
          <Badge tone="warn">{t("form.panel.staleBadge")}</Badge>
        ) : null}
        {disabled !== null ? (
          <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
        ) : null}
      </header>
      {documentSection === null || context === null ? null : (
        <fieldset className="pipeline__properties" disabled={disabled !== null}>
          <legend>{t("graph.pipeline.documentProperties")}</legend>
          <FormFieldsEditor
            section={documentSection}
            transactions={transactions}
            catalogs={catalogs}
            owner={PIPELINE_OWNER}
            selectedPointer={selectedPointer}
            names={context.names}
            showIdentifiers={false}
          />
        </fieldset>
      )}
      <TransactionFeedbackNote
        feedback={transactions.feedbackFor(PIPELINE_OWNER)}
        owner={PIPELINE_OWNER}
      />
      {pipeline === null || context === null ? null : (
        <fieldset
          className="pipeline__canvas"
          disabled={disabled !== null}
          aria-label={t("graph.pipeline.stages")}
        >
          <ol className="pipeline__stages">
            {pipeline.stages.map((stage, index) => (
              <StageColumn
                key={stage.stage}
                stage={stage}
                number={index + 1}
                context={context}
              />
            ))}
            <ExecutionGuide
              number={pipeline.stages.length + 1}
              outside={unstagedNames({
                ...pipeline,
                unstaged: pipeline.unstaged.filter(
                  (section) => section !== documentProperties,
                ),
              })}
            />
          </ol>
        </fieldset>
      )}
    </section>
  );
};

const StageColumn = ({
  stage,
  number,
  context,
}: {
  stage: PipelineStage;
  number: number;
  context: CardContext;
}) => {
  const headingId = useId();
  const notesId = useId();
  const stem = `strategy.stage.${stage.stage}`;
  return (
    <li className="pipeline__stage">
      <div role="group" aria-labelledby={headingId}>
        <h3 id={headingId} className="pipeline__stage-title">
          <span className="pipeline__stage-number">{number}</span> {tName(stem)}{" "}
          <span className="pipeline__stage-term" lang="en">
            {tOptional(`${stem}.term`)}
          </span>
        </h3>
        <p className="pipeline__stage-description">{tDescription(stem)}</p>
        <DiagnosticNotes id={notesId} diagnostics={stage.diagnostics} />
        {stage.lists.map((list) => (
          <StageList key={list.pointer} list={list} context={context} />
        ))}
        {stage.cards.map((card) => (
          <SentenceCard
            key={card.pointer}
            label={tName(card.rows[0]!.field.descriptionKey) ?? ""}
            rows={card.rows}
            template={cardTemplate(
              card.rows.map((row) => row.field),
              card.rows[0]!.field.descriptionKey,
            )}
            context={context}
          />
        ))}
      </div>
    </li>
  );
};

/**
 * "5 실행" 안내(WORKFLOW P4-02 결정): 실행 설정은 전략 문서 밖이라 캔버스가 아니라 화면 위 실행 설정 띠에서
 * 고른다. 이 캔버스에 없는 것 — 단계 없는 섹션(`unstagedNames`, 스키마가 정한다)과 팩터 계산식 — 이 어디 있는지도
 * 말한다.
 */
const ExecutionGuide = ({
  number,
  outside,
}: {
  number: number;
  outside: readonly string[];
}) => {
  const headingId = useId();
  return (
    <li className="pipeline__stage pipeline__stage--guide">
      <div role="group" aria-labelledby={headingId}>
        <h3 id={headingId} className="pipeline__stage-title">
          <span className="pipeline__stage-number">{number}</span>{" "}
          {t("graph.pipeline.execution")}{" "}
          <span className="pipeline__stage-term" lang="en">
            {t("graph.pipeline.execution.term")}
          </span>
        </h3>
        <p className="pipeline__stage-description">
          {t("graph.pipeline.execution.description")}
        </p>
        <p className="pipeline__list-title">{t("graph.pipeline.notHere")}</p>
        <ul className="pipeline__guide">
          {outside.length === 0 ? null : (
            <li>
              {t("graph.pipeline.notHere.document").replace(
                "{names}",
                outside.join("·"),
              )}
            </li>
          )}
          <li>{t("graph.pipeline.notHere.formula")}</li>
        </ul>
      </div>
    </li>
  );
};

/**
 * 목록(규칙·팩터). 항목은 문장 카드(틀 `<목록 설명 키>.card`)이고 추가·삭제는 Form 목록과 같은 연산이다.
 */
const StageList = ({
  list,
  context,
}: {
  list: FormListSection;
  context: CardContext;
}) => {
  const notesId = useId();
  const addReasonId = `${notesId}-add`;
  const title = tName(list.descriptionKey) ?? "";
  // 캔버스 목록(규칙·팩터) 항목은 union 이 아니라 `kind` 가 없다.
  const addition = listAddition(
    context.schema,
    list,
    null,
    context.transactions.settling,
  );
  return (
    <div
      role="group"
      aria-label={title}
      className="pipeline__list"
      aria-current={
        coversPointer(list.pointer, context.selectedPointer)
          ? "true"
          : undefined
      }
    >
      <p className="pipeline__list-title">{title}</p>
      <DiagnosticNotes id={notesId} diagnostics={list.diagnostics} />
      {list.items.length === 0 ? (
        <p className="pipeline__hint">{t("form.list.empty")}</p>
      ) : null}
      {list.items.map((item, index) => (
        <ListItemCard
          key={item.pointer}
          list={list}
          item={item}
          label={
            itemName(item) ??
            t("graph.pipeline.item").replace("{index}", String(index + 1))
          }
          context={context}
        />
      ))}
      <div className="pipeline__actions">
        <Button
          size="small"
          disabled={addition.blocked !== null}
          aria-describedby={addition.blocked === null ? undefined : addReasonId}
          onClick={() => {
            if (addition.operation !== null)
              context.transactions.apply(
                addition.operation,
                title,
                PIPELINE_OWNER,
                NO_FOCUS,
              );
          }}
          aria-label={`${title} · ${t("form.list.add")}`}
        >
          {t("form.list.add")}
        </Button>
        {addition.blocked === null ? null : (
          <span id={addReasonId} className="pipeline__hint">
            {addition.blocked}
          </span>
        )}
      </div>
    </div>
  );
};

/**
 * 목록 항목 카드: 항목 필드로 문장을 채우고 삭제를 둔다(Form 항목과 같은 `useItemRemoval`). identity(`factor_id`)는
 * 기본 카드 이름은 `label` 이 맡고 identity 편집은 접힌 영역에서 기존 참조 rename을 쓴다.
 * 팩터의 계산 그래프는 레시피 줄(`RecipeLine`)로 보인다. 삭제 거부는 참조 자리를 이름으로 말한다(결정 3).
 */
const ListItemCard = ({
  list,
  item,
  label,
  context,
}: {
  list: FormListSection;
  item: FormListItem;
  label: string;
  context: CardContext;
}) => {
  const { blocked, remove } = useItemRemoval(
    item,
    context.tree,
    context.transactions,
    PIPELINE_OWNER,
    label,
  );
  const section = itemSection(list, item);
  const graph = item.fields.find(
    (field) => field.control.kind === "graph-link",
  );
  const identity = item.fields.find((field) => field.key === item.identityKey);
  const [identifiers, setIdentifiers] = useRevealedOpen(
    identity !== undefined && context.selectedPointer === identity.pointer
      ? `${identity.pointer}:${context.revealSignal ?? 0}`
      : null,
    false,
  );
  const planIdentity: CommitPlanner = (field, value) =>
    field.key === item.identityKey
      ? renameIdentity(context.tree, list, item, draftOf(value))
      : null;
  const shown = item.fields.filter(
    (field) => field !== graph && field !== identity,
  );
  return (
    <SentenceCard
      label={label}
      rows={shown.map((field) => ({ field, section }))}
      template={cardTemplate(item.fields, list.descriptionKey)}
      context={context}
      // 칸이 없는 identity 의 진단은 카드 본문으로 남긴다.
      notes={[
        ...item.diagnostics,
        ...(identifiers ? [] : (identity?.diagnostics ?? [])),
      ]}
      owner={item.pointer}
    >
      {graph === undefined ? null : (
        <RecipeLine item={item} graph={graph} label={label} context={context} />
      )}
      {identity === undefined ? null : (
        <div className="pipeline__identity">
          <Button
            size="small"
            tone="ghost"
            aria-expanded={identifiers}
            onClick={() => setIdentifiers(!identifiers)}
          >
            {t("graph.pipeline.identifiers")}
          </Button>
          {identifiers ? (
            <FormFieldsEditor
              section={{ ...section, fields: [identity] }}
              transactions={context.transactions}
              catalogs={context.catalogs}
              owner={PIPELINE_OWNER}
              planCommit={planIdentity}
              selectedPointer={context.selectedPointer}
              names={context.names}
              showIdentifiers={false}
              sectionDiagnostics={false}
            />
          ) : null}
        </div>
      )}
      <div className="pipeline__actions">
        <Button
          size="small"
          tone="danger"
          onClick={remove}
          disabled={context.transactions.settling}
          aria-label={`${label} · ${t("form.list.remove")}`}
        >
          {t("form.list.remove")}
        </Button>
      </div>
      {blocked === null ? null : (
        <p className="strategy-form__invalid" role="alert">
          {t("graph.pipeline.removeBlocked").replace(
            "{places}",
            [
              ...new Set(
                blocked.map((reference) =>
                  placeName(context.schema, context.tree, reference.pointer),
                ),
              ),
            ].join(", "),
          )}
        </p>
      )}
    </SentenceCard>
  );
};

/**
 * 팩터 카드의 레시피 줄: 계산 그래프 요약 문장(`recipeSummary`)과 "레시피 열기"(P5 전까지 고급 편집기). 그래프 안
 * 진단은 수 배지만 두고 본문은 고급 편집기가 노드에 붙인다(결정 7). 단계가 없는 그래프는 요약 대신 backend
 * 진단 문장("첫 단계를 추가하세요.")을 그대로 보인다.
 */
const RecipeLine = ({
  item,
  graph,
  label,
  context,
}: {
  item: FormListItem;
  graph: FormField;
  label: string;
  context: CardContext;
}) => {
  const notesId = useId();
  const summary = recipeSummary(
    context.schema,
    context.tree,
    item.pointer,
    context.names.catalog,
  );
  return (
    <div className="pipeline__actions">
      {summary === null ? (
        <DiagnosticNotes id={notesId} diagnostics={graph.diagnostics} />
      ) : (
        <p className="pipeline__hint">
          {summary} <SeverityBadge diagnostics={graph.diagnostics} />
        </p>
      )}
      {context.onOpenGraph === undefined ? null : (
        <Button
          size="small"
          tone="ghost"
          onClick={() => context.onOpenGraph?.(graph.pointer)}
          aria-label={`${label} · ${t("graph.pipeline.openRecipe")}`}
        >
          {t("graph.pipeline.openRecipe")}
        </Button>
      )}
    </div>
  );
};

/**
 * 문장 카드: 틀의 글자와 `{<키>}` 자리(그 행의 컨트롤)를 잇는다. 틀 밖의 행은 이름 라벨 행으로 그리되,
 * 지금 쓰이지 않는 행(적용 조건 거짓)은 문서에 적혀 있을 때만 보인다(경고를 읽고 지울 수 있게).
 */
const SentenceCard = ({
  label,
  rows,
  template,
  context,
  notes = [],
  owner,
  children,
}: {
  label: string;
  rows: readonly PipelineRow[];
  template: string | null;
  context: CardContext;
  /** 어느 행도 흡수하지 않은 카드(목록 항목) 자신의 진단. */
  notes?: PipelineRow["field"]["diagnostics"];
  /** 선택 강조의 기준 pointer(목록 항목). 없으면 행 pointer 로 본다. */
  owner?: string;
  /** 카드 끝에 붙는 항목 동작(삭제). */
  children?: ReactNode;
}) => {
  const cardId = useId();
  const pieces = template === null ? [] : sentencePieces(template);
  const byKey = new Map(rows.map((row) => [row.field.key, row]));
  const placed = new Set(
    pieces.flatMap((piece) =>
      "key" in piece && byKey.has(piece.key) ? [piece.key] : [],
    ),
  );
  const rest = rows.filter(
    (row) =>
      !placed.has(row.field.key) &&
      (row.field.applicable !== false || row.field.written),
  );
  const notesIdOf = (row: PipelineRow) => `${cardId}-${row.field.key}-notes`;
  const selected =
    owner !== undefined
      ? coversPointer(owner, context.selectedPointer)
      : rows.some((row) =>
          coversPointer(row.field.pointer, context.selectedPointer),
        );
  return (
    <div
      role="group"
      aria-label={label}
      className="pipeline__card"
      aria-current={selected ? "true" : undefined}
    >
      {pieces.length === 0 ? null : (
        <p className="pipeline__sentence">
          {pieces.map((piece, index) =>
            "text" in piece ? (
              piece.text
            ) : byKey.has(piece.key) ? (
              <InlineField
                key={`${piece.key}:${index}`}
                row={byKey.get(piece.key)!}
                notesId={notesIdOf(byKey.get(piece.key)!)}
                context={context}
                slider={piece.slider}
              />
            ) : null,
          )}
        </p>
      )}
      {rest.map((row) => (
        <p key={row.field.pointer} className="pipeline__row">
          <span className="pipeline__row-name">
            {tName(row.field.descriptionKey)}
          </span>{" "}
          <InlineField row={row} notesId={notesIdOf(row)} context={context} />
        </p>
      ))}
      <div className="pipeline__actions">
        {rows.map((row) => (
          <FieldActions
            key={row.field.pointer}
            section={row.section}
            field={row.field}
            transactions={context.transactions}
            owner={PIPELINE_OWNER}
            label={tName(row.field.descriptionKey) ?? ""}
            prefix={
              <span className="pipeline__action-name">
                {tName(row.field.descriptionKey)}
              </span>
            }
          />
        ))}
      </div>
      {rows.map((row) => (
        <DiagnosticNotes
          key={row.field.pointer}
          id={notesIdOf(row)}
          diagnostics={row.field.diagnostics}
        />
      ))}
      <DiagnosticNotes id={`${cardId}-notes`} diagnostics={notes} />
      {children}
    </div>
  );
};

/**
 * 캔버스 막대의 범위(WORKFLOW P4-03 결정 2): 스키마 범위가 있으면 그 값, 없으면 표시 전용 하한 0·상한 3이고 단계는
 * 0.1(정수 칸은 1)이다. 범위 밖 값은 옆 숫자 칸으로 넣고, 값 검증은 스키마·backend 몫이다.
 */
const sliderRange = (control: FormField["control"]): SliderRange | undefined =>
  control.kind !== "number"
    ? undefined
    : {
        min: control.min?.value ?? 0,
        max: control.max?.value ?? 3,
        step: control.integer ? 1 : 0.1,
      };

/** 문장 안의 컨트롤 하나. 확정·무효 안내는 Form 행과 같은 훅이고, 이름은 `aria-label`(필드 이름)이다. */
const InlineField = ({
  row,
  notesId,
  context,
  slider = false,
}: {
  row: PipelineRow;
  notesId: string;
  context: CardContext;
  /** 틀 자리 `{<키>.slider}`: 숫자 칸 옆에 끄는 막대를 둔다. */
  slider?: boolean;
}) => {
  const id = useId();
  const invalidId = `${id}-invalid`;
  const hintId = `${id}-hint`;
  const { field, section } = row;
  const name = tName(field.descriptionKey) ?? "";
  const editing = useFieldCommit({
    section,
    field,
    transactions: context.transactions,
    owner: PIPELINE_OWNER,
    label: name,
  });
  const describedBy = [
    editing.invalid === null ? null : invalidId,
    field.applicable === false ? hintId : null,
    field.diagnostics.length === 0 ? null : notesId,
  ].filter((value): value is string => value !== null);
  return (
    <span
      className="pipeline__field"
      aria-current={
        coversPointer(field.pointer, context.selectedPointer)
          ? "true"
          : undefined
      }
      data-written={field.written}
      data-applicable={
        field.applicable === null ? "unknown" : String(field.applicable)
      }
    >
      <FieldControl
        id={id}
        labelId={`${id}-label`}
        ariaLabel={name}
        describedBy={
          describedBy.length === 0 ? undefined : describedBy.join(" ")
        }
        field={field}
        catalogs={context.catalogs}
        names={context.names}
        slider={slider ? sliderRange(field.control) : undefined}
        {...editing.control}
      />
      {field.applicable === false ? (
        <span id={hintId} className="sr-only">
          {t("form.field.inapplicable")}
        </span>
      ) : null}
      {editing.invalid === null ? null : (
        <span id={invalidId} className="strategy-form__invalid" role="alert">
          {editing.invalid}
        </span>
      )}
    </span>
  );
};
