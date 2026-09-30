/**
 * 그래프 1수준(파이프라인) 캔버스(WORKFLOW P4-02, spec D9). `pipeline-projection.ts` 의 카드 모델을 그대로
 * 그린다: 맨 위 한 문장 요약, 단계 열, 카드 문장 안의 컨트롤. 컨트롤·확정·되돌리기는 Form 필드 행과 한
 * 경로다(`FieldControl`·`useFieldCommit`·`FieldActions`, 연산은 `form-transactions.ts`). 어느 필드를 어떤
 * 문장에 넣을지는 i18n 카드 틀(`cardTemplate`)이 정하고, 카드는 YAML 식별자(스키마 키·pointer)를 보이지 않는다.
 */
import { useId, useMemo } from "react";

import { t, tDescription, tName, tOptional } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import {
  catalogProfiles,
  type FormListItem,
  type FormListSection,
} from "../model/form-projection";
import { itemSection, placeholderOf } from "../model/form-transactions";
import {
  cardTemplate,
  itemName,
  projectPipeline,
  sentencePieces,
  strategySummary,
  type PipelineRow,
  type PipelineStage,
} from "../model/pipeline-projection";
import type { JsonSchema } from "../model/schema-navigator";
import { useFieldCommit } from "../model/use-field-editing";
import type { FormProjectionState } from "../model/use-form-projection";
import { useRevealSelection } from "../model/use-reveal-selection";
import type { SourceTransactions } from "../model/use-source-transactions";
import {
  DiagnosticNotes,
  FieldActions,
  FieldControl,
  type FormCatalogs,
} from "./strategy-form-panel";
import { TransactionFeedbackNote } from "./transaction-feedback";
import "./pipeline-panel.css";

/** 캔버스의 feedback owner. Form(`form`)·Graph(`graph`)와 슬롯을 나눈다. */
const PIPELINE_OWNER = "pipeline";

type CardContext = {
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  selectedPointer: string | undefined;
  onOpenGraph: ((pointer: string) => void) | undefined;
};

const covers = (owner: string, pointer: string | undefined): boolean =>
  pointer !== undefined &&
  (pointer === owner || pointer.startsWith(`${owner}/`));

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
  const disabled = transactions.disabled;
  const context: CardContext = {
    transactions,
    catalogs,
    selectedPointer,
    onOpenGraph,
  };
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
            ? t("form.panel.loading")
            : strategySummary(
                pipeline,
                (catalog, value) =>
                  catalogProfiles(catalogs, catalog)?.find(
                    (profile) => profile.field_id === value,
                  )?.label ?? null,
              )}
        </p>
        {form.stale ? (
          <Badge tone="warn">{t("form.panel.staleBadge")}</Badge>
        ) : null}
        {disabled !== null ? (
          <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
        ) : null}
      </header>
      <TransactionFeedbackNote
        feedback={transactions.feedbackFor(PIPELINE_OWNER)}
        owner={PIPELINE_OWNER}
      />
      {pipeline === null ? null : (
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
 * 목록. 문장 틀(`<목록 설명 키>.card`)이 있는 목록은 항목을 문장 카드로 그리고, 없는 목록(팩터 — 카드는
 * P4-03)은 항목 이름과 "그래프에서 열기"만 보인다.
 */
const StageList = ({
  list,
  context,
}: {
  list: FormListSection;
  context: CardContext;
}) => {
  const notesId = useId();
  const title = tName(list.descriptionKey) ?? "";
  const sentences =
    list.descriptionKey !== null &&
    tOptional(`${list.descriptionKey}.card`) !== null;
  return (
    <div role="group" aria-label={title} className="pipeline__list">
      <p className="pipeline__list-title">{title}</p>
      <DiagnosticNotes id={notesId} diagnostics={list.diagnostics} />
      {list.items.length === 0 ? (
        <p className="pipeline__hint">{t("form.list.empty")}</p>
      ) : null}
      {list.items.map((item, index) =>
        sentences ? (
          <SentenceCard
            key={item.pointer}
            label={t("graph.pipeline.item").replace(
              "{index}",
              String(index + 1),
            )}
            rows={item.fields.map((field) => ({
              field,
              section: itemSection(list, item),
            }))}
            template={cardTemplate(item.fields, list.descriptionKey)}
            context={context}
            notes={item.diagnostics}
            owner={item.pointer}
          />
        ) : (
          <NamedItem key={item.pointer} item={item} context={context} />
        ),
      )}
    </div>
  );
};

const NamedItem = ({
  item,
  context,
}: {
  item: FormListItem;
  context: CardContext;
}) => {
  const notesId = useId();
  const name = itemName(item) ?? t("graph.pipeline.unnamed");
  const graph = item.fields.find(
    (field) => field.control.kind === "graph-link",
  );
  return (
    <div
      role="group"
      aria-label={name}
      className="pipeline__card"
      aria-current={
        covers(item.pointer, context.selectedPointer) ? "true" : undefined
      }
    >
      <p className="pipeline__sentence">
        <strong>{name}</strong>
      </p>
      {graph !== undefined && context.onOpenGraph !== undefined ? (
        <Button
          size="small"
          tone="ghost"
          onClick={() => context.onOpenGraph?.(graph.pointer)}
          aria-label={`${name} · ${t("form.field.openGraph")}`}
        >
          {t("form.field.openGraph")}
        </Button>
      ) : null}
      <DiagnosticNotes
        id={notesId}
        diagnostics={[
          ...item.diagnostics,
          ...item.fields.flatMap((field) => field.diagnostics),
        ]}
      />
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
}: {
  label: string;
  rows: readonly PipelineRow[];
  template: string | null;
  context: CardContext;
  /** 어느 행도 흡수하지 않은 카드(목록 항목) 자신의 진단. */
  notes?: PipelineRow["field"]["diagnostics"];
  /** 선택 강조의 기준 pointer(목록 항목). 없으면 행 pointer 로 본다. */
  owner?: string;
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
      ? covers(owner, context.selectedPointer)
      : rows.some((row) => row.field.pointer === context.selectedPointer);
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
    </div>
  );
};

/** 문장 안의 컨트롤 하나. 확정·무효 안내는 Form 행과 같은 훅이고, 이름은 `aria-label`(필드 이름)이다. */
const InlineField = ({
  row,
  notesId,
  context,
}: {
  row: PipelineRow;
  notesId: string;
  context: CardContext;
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
        placeholderValue={placeholderOf(section, field)}
        onCommit={editing.commit}
        onValid={editing.onValid}
        onInvalid={editing.onInvalid}
      />
      {field.applicable === false ? (
        <span id={hintId} className="sr-only">
          {t("form.field.inapplicable")}
        </span>
      ) : null}
      {editing.invalid === null ? null : (
        <span id={invalidId} className="pipeline__invalid" role="alert">
          {editing.invalid}
        </span>
      )}
    </span>
  );
};
