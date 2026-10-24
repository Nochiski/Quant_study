/**
 * 새 전략 단계별 안내(RD-04-D, 보드 04b). 전략 문서·YAML·편집기는 그대로 두고 만드는 과정만 한 단계씩
 * 보인다. 단계는 파이프라인 투영을 나눈 것(`guideSteps`)이고, 칸은 캔버스와 같은 문장 카드·목록
 * (`SentenceCard`·`StageList`)이며, 모든 선택은 같은 source 트랜잭션으로 문서에 들어간다 — 안내용 상태나
 * 직렬화 모델이 없다. 기본값을 고르지 않으면 문서에 아무것도 쓰지 않는다(카드 컨트롤이 기본값을 쓰지 않는다).
 */
import { useId, useMemo, useState, type ReactNode } from "react";

import type { FactorDefinition } from "../../../shared/api";
import { t, tCode, tDescription, tFill, tName, tOptional } from "../../../shared/config";
import { Badge, Button, panelId, tabId, Tabs } from "../../../shared/ui";
import { buildCanonicalSnippetCatalog, type SnippetCatalogSource } from "../model/canonical-snippets";
import {
  catalogProfiles,
  type FormListSection,
  type FormProjection,
} from "../model/form-projection";
import { factorAddition, groupFactors, type FactorCategory } from "../model/guide-factors";
import { changedLines } from "../model/guide-preview";
import {
  guideStepState,
  guideStepStatuses,
  guideSteps,
  type GuideStep,
} from "../model/guide-steps";
import {
  pipelineDocumentProperties,
  pipelineNames,
  projectPipeline,
  strategySummary,
  type CatalogNames,
  type PipelineCard,
  type PipelineProjection,
} from "../model/pipeline-projection";
import type { JsonSchema } from "../model/schema-navigator";
import { NO_FOCUS } from "../model/use-field-editing";
import type { FormProjectionState } from "../model/use-form-projection";
import type { SourceTransactions } from "../model/use-source-transactions";
import { cardTemplate } from "../model/pipeline-projection";
import { type CardContext, SentenceCard, StageList } from "./pipeline-panel";
import { LibraryFactorPicker } from "./library-factor-picker";
import { FormFieldsEditor, type FormCatalogs } from "./strategy-form-panel";
import { TransactionFeedbackNote } from "./transaction-feedback";
import "./strategy-guide.css";

/** 안내의 feedback owner. 캔버스·Form·Graph와 슬롯을 나눈다. */
const GUIDE_OWNER = "guide";

/** 오른쪽 칸의 탭: AI 도우미와 문서 미리보기. 어느 탭인지는 화면(URL)이 소유한다. */
export type GuidePanel = "assistant" | "preview";

const stepName = (step: GuideStep): string => {
  if (step.kind === "document") return t("guide.step.document");
  if (step.kind === "review") return t("guide.step.review");
  const stem = `strategy.stage.${step.stage}`;
  return (step.split ? tOptional(`guide.step.${step.stage}`) : null) ?? tName(stem) ?? step.stage;
};

export const StrategyGuide = ({
  form,
  schema,
  transactions,
  catalogs,
  snippetSource,
  source,
  step: stepId,
  onStep,
  status,
  assistant,
  panel = "preview",
  onPanel,
  onOpenGraph,
  onOpenEditor,
  onUndo,
  canUndo,
  review,
}: {
  form: FormProjectionState;
  schema: JsonSchema | null;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  /** 팩터 카탈로그와 스키마(스니펫 preset 의 재료). */
  snippetSource: SnippetCatalogSource;
  /** 지금 문서 원문. 오른쪽 미리보기가 읽기만 한다. */
  source: string;
  /** URL 이 가진 단계 id. 없거나 모르면 첫 단계다. */
  step: string | undefined;
  onStep: (id: string) => void;
  /** 팩터 계산식을 레시피 편집기에서 연다(직접 만들기). */
  onOpenGraph: (pointer: string) => void;
  /** 안내를 닫고 같은 초안을 편집기 그래프 보기로 연다. */
  onOpenEditor: () => void;
  onUndo: () => void;
  canUndo: boolean;
  /** 확인 단계에 붙는 화면 몫(진단·선정 미리보기·저장). */
  review: ReactNode;
  /** 머리 띠 오른쪽의 초안 저장 상태(서버 초안 동기화 문장). */
  status?: ReactNode;
  /**
   * 오른쪽 칸의 AI 도우미. 지금 단계를 받아 그 단계에 맞는 추천 질문을 고른다. 없으면 오른쪽 칸은 문서
   * 미리보기 하나다. AI 패널은 탭을 바꿔도 마운트를 유지한다 — 치던 문장·진행 중 답변을 잃지 않는다.
   */
  assistant?: (step: GuideStep) => ReactNode;
  /** 열린 탭. null 이면 아직 정하지 못했다(공급자 연결 여부를 기다리는 중) — 오른쪽 칸은 불러오는 중이다. */
  panel?: GuidePanel | null;
  onPanel?: (panel: GuidePanel) => void;
}) => {
  const titleId = useId();
  const pipeline: PipelineProjection | null = useMemo(
    () =>
      schema === null || form.projection === null
        ? null
        : projectPipeline(schema, form.projection, form.tree),
    [schema, form],
  );
  const steps = useMemo(() => guideSteps(pipeline), [pipeline]);
  const index = Math.max(
    0,
    steps.findIndex((candidate) => candidate.id === stepId),
  );
  const current = steps[index]!;
  const catalogName: CatalogNames = (catalog, value) =>
    catalogProfiles(catalogs, catalog)?.find(
      (profile) => profile.field_id === value,
    )?.label ?? null;
  const context: CardContext | null =
    pipeline === null || schema === null
      ? null
      : {
          names: pipelineNames(pipeline, catalogName),
          schema,
          tree: form.tree,
          transactions,
          catalogs,
          selectedPointer: undefined,
          revealSignal: undefined,
          onOpenGraph,
          onOpenAdvanced: undefined,
          librarySave: true,
          itemHeads: true,
        };
  const disabled = transactions.disabled;
  const statuses = useMemo(() => guideStepStatuses(steps, pipeline), [steps, pipeline]);
  // 사용자가 떠난 단계. 필수 칸이 없는 단계는 지나가면 기본값을 받아들인 것으로 본다(`guideStepState`).
  // 안내 화면의 UI 상태라 문서·URL 에 두지 않는다.
  const [visited, setVisited] = useState<ReadonlySet<string>>(() => new Set());
  const states = steps.map((candidate, position) =>
    guideStepState(candidate, statuses[position]!, visited.has(candidate.id)),
  );
  const done = states.filter(
    (state, position) => steps[position]!.kind !== "review" && state === "done",
  ).length;
  const go = (id: string) => {
    if (id !== current.id) setVisited((previous) => new Set(previous).add(current.id));
    onStep(id);
  };
  const next = steps[index + 1];
  const changed = useChangedLines(source);
  const sideId = useId();
  const preview = <YamlPreview source={source} changed={changed} />;
  return (
    <section className="strategy-guide" aria-labelledby={titleId}>
      <header className="strategy-guide__header">
        <div className="strategy-guide__heading">
          <h2 id={titleId}>{t("guide.title")}</h2>
          <p>{t("guide.description")}</p>
        </div>
        <div className="strategy-guide__header-actions">
          {status}
          <Button size="small" tone="ghost" onClick={onUndo} disabled={!canUndo}>
            {t("guide.undo")}
          </Button>
          <Button size="small" tone="secondary" onClick={onOpenEditor}>
            {t("guide.openEditor")}
          </Button>
        </div>
      </header>
      <nav className="strategy-guide__steps" aria-label={t("guide.steps")}>
        <ol>
          {steps.map((candidate, position) => {
            const state = states[position]!;
            return (
              <li key={candidate.id}>
                <button
                  type="button"
                  aria-current={position === index ? "step" : undefined}
                  data-state={state}
                  onClick={() => go(candidate.id)}
                >
                  {/* 번호·체크는 목록 순서와 상태 문구가 이미 말한다. 버튼 이름은 단계 이름과 상태다. */}
                  <span className="strategy-guide__step-number" aria-hidden="true">
                    {state === "done" ? <CheckIcon /> : position + 1}
                  </span>
                  <span className="strategy-guide__step-name">{stepName(candidate)}</span>
                  {state === "done" ? (
                    <span className="sr-only">{t("guide.stepState.done")}</span>
                  ) : null}
                  {state === "error" ? (
                    <span className="strategy-guide__step-tag">
                      {tFill("guide.stepState.error", {
                        count: statuses[position]!.errors,
                      })}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
        <p className="strategy-guide__progress">
          {tFill("guide.progress", {
            done,
            total: steps.filter((candidate) => candidate.kind !== "review").length,
          })}
        </p>
      </nav>
      <div className="strategy-guide__body">
        <section
          className="strategy-guide__step"
          aria-labelledby={`${titleId}-step`}
        >
          <header className="strategy-guide__step-head">
            <p className="strategy-guide__step-position">
              {tFill("guide.stepPosition", { current: index + 1, total: steps.length })}
            </p>
            <h3 id={`${titleId}-step`}>{stepName(current)}</h3>
            {disabled !== null ? (
              <Badge tone="warn">{t(`form.disabled.${disabled}`)}</Badge>
            ) : null}
          </header>
          <div className="strategy-guide__step-body">
            {context === null || pipeline === null ? (
              <p className="strategy-guide__hint" role="status">
                {t(form.firstParsePending ? "form.panel.firstParsePending" : "form.panel.loading")}
              </p>
            ) : current.kind === "document" ? (
              <DocumentStep pipeline={pipeline} context={context} />
            ) : current.kind === "stage" ? (
              <StageStep
                step={current}
                pipeline={pipeline}
                context={context}
                snippetSource={snippetSource}
                projection={form.projection}
              />
            ) : (
              <>
                <p className="strategy-guide__summary">
                  <strong>{t("graph.pipeline.summary")}</strong>{" "}
                  {strategySummary(pipeline, catalogName)}
                </p>
                {review}
              </>
            )}
            <TransactionFeedbackNote
              feedback={transactions.feedbackFor(GUIDE_OWNER)}
              owner={GUIDE_OWNER}
            />
            <TransactionFeedbackNote
              feedback={transactions.feedbackFor("pipeline")}
              owner="pipeline"
            />
            <TransactionFeedbackNote
              feedback={transactions.feedbackFor("library")}
              owner="library"
            />
          </div>
          <footer className="strategy-guide__footer">
            <Button
              tone="ghost"
              disabled={index === 0}
              onClick={() => go(steps[index - 1]!.id)}
            >
              {t("guide.previous")}
            </Button>
            {next === undefined ? null : (
              <div className="strategy-guide__next">
                <span className="strategy-guide__hint" aria-hidden="true">
                  {tFill("guide.nextStep", { name: stepName(next) })}
                </span>
                <Button tone="primary" onClick={() => go(next.id)}>
                  {t("guide.next")}
                </Button>
              </div>
            )}
          </footer>
        </section>
        {assistant === undefined ? (
          <div className="strategy-guide__side">{preview}</div>
        ) : panel === null ? (
          // 기본 탭은 공급자 연결 여부가 정한다. 정해지기 전에 한쪽을 보였다가 바꾸면 화면이 뛴다.
          <div className="strategy-guide__side">
            <p className="strategy-guide__side-pending" role="status">
              {t("page.loading")}
            </p>
          </div>
        ) : (
          <div className="strategy-guide__side">
            <Tabs
              className="strategy-guide__side-tabs"
              label={t("guide.side")}
              idBase={sideId}
              value={panel}
              onChange={(nextPanel) => onPanel?.(nextPanel)}
              items={[
                { id: "assistant", label: t("guide.side.assistant") },
                {
                  id: "preview",
                  label: t("guide.preview"),
                  badge:
                    changed.size === 0
                      ? undefined
                      : tFill("guide.preview.changedCount", { count: changed.size }),
                },
              ]}
            />
            <div
              role="tabpanel"
              id={panelId(sideId, "assistant")}
              aria-labelledby={tabId(sideId, "assistant")}
              className="strategy-guide__side-panel strategy-guide__side-panel--assistant"
              hidden={panel !== "assistant"}
            >
              {assistant(current)}
            </div>
            <div
              role="tabpanel"
              id={panelId(sideId, "preview")}
              aria-labelledby={tabId(sideId, "preview")}
              className="strategy-guide__side-panel"
              hidden={panel !== "preview"}
            >
              {preview}
            </div>
          </div>
        )}
      </div>
    </section>
  );
};

const CheckIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
    <path d="M5 12l5 5L20 7" />
  </svg>
);

const DocumentStep = ({
  pipeline,
  context,
}: {
  pipeline: PipelineProjection;
  context: CardContext;
}) => {
  const section = pipelineDocumentProperties(pipeline);
  return (
    <>
      <p className="strategy-guide__hint">{t("guide.document.description")}</p>
      {section === null ? null : (
        <fieldset
          className="strategy-guide__properties"
          disabled={context.transactions.disabled !== null}
        >
          <legend className="sr-only">{t("guide.step.document")}</legend>
          <FormFieldsEditor
            section={section}
            transactions={context.transactions}
            catalogs={context.catalogs}
            owner={GUIDE_OWNER}
            names={context.names}
            showIdentifiers={false}
            requirementTags
          />
        </fieldset>
      )}
    </>
  );
};

const StageStep = ({
  step,
  pipeline,
  context,
  snippetSource,
  projection,
}: {
  step: Extract<GuideStep, { kind: "stage" }>;
  pipeline: PipelineProjection;
  context: CardContext;
  snippetSource: SnippetCatalogSource;
  projection: FormProjection | null;
}) => {
  const stage = pipeline.stages.find((candidate) => candidate.stage === step.stage);
  const lists = (stage?.lists ?? []).filter((list) =>
    step.listPointers.includes(list.pointer),
  );
  const describe = step.split
    ? tOptional(`guide.step.${step.stage}.description`)
    : tDescription(`strategy.stage.${step.stage}`);
  return (
    <fieldset
      className="strategy-guide__stage"
      disabled={context.transactions.disabled !== null}
    >
      <legend className="sr-only">{tName(`strategy.stage.${step.stage}`)}</legend>
      {describe === null ? null : <p className="strategy-guide__hint">{describe}</p>}
      {step.listOnly
        ? lists.map((list) => (
            <FactorSources
              key={list.pointer}
              list={list}
              context={context}
              snippetSource={snippetSource}
              projection={projection}
            />
          ))
        : null}
      {lists.map((list) => (
        <StageList
          key={list.pointer}
          list={list}
          context={context}
          // 목록 설명(거르기 규칙이면 "모두 만족하는 종목만 남습니다.")은 스키마 설명 키가 정본이다.
          note={tDescription(list.descriptionKey)}
        />
      ))}
      <Cards cards={step.primary} context={context} />
      {step.more.length === 0 ? null : (
        <details className="ui-disclosure strategy-guide__more">
          <summary>
            {tFill("guide.more", { count: step.more.length })}
          </summary>
          <Cards cards={step.more} context={context} />
        </details>
      )}
      {!step.listOnly && step.primary.length === 0 && step.more.length === 0 && lists.length === 0 ? (
        <p className="strategy-guide__hint">{t("guide.stage.empty")}</p>
      ) : null}
    </fieldset>
  );
};

const Cards = ({
  cards,
  context,
}: {
  cards: readonly PipelineCard[];
  context: CardContext;
}) => (
  <>
    {cards.map((card) => (
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
  </>
);

/** 팩터를 고르는 곳: 카탈로그(backend 레지스트리)와 내 팩터(라이브러리, US-CS-08). */
const FactorSources = ({
  list,
  context,
  snippetSource,
  projection,
}: {
  list: FormListSection;
  context: CardContext;
  snippetSource: SnippetCatalogSource;
  projection: FormProjection | null;
}) => {
  const [source, setSource] = useState<"catalog" | "mine">("catalog");
  return (
    <>
      <div className="strategy-guide__chips" role="group" aria-label={t("library.tabs")}>
        {(["catalog", "mine"] as const).map((candidate) => (
          <button
            key={candidate}
            type="button"
            className="strategy-guide__chip"
            aria-pressed={source === candidate}
            onClick={() => setSource(candidate)}
          >
            {t(`library.tab.${candidate}`)}
          </button>
        ))}
      </div>
      {source === "catalog" ? (
        <FactorPicker list={list} context={context} snippetSource={snippetSource} />
      ) : (
        <LibraryFactorPicker context={context} projection={projection} />
      )}
    </>
  );
};

/**
 * 분류 칩과 팩터 목록. 바로 쓸 수 있는(`implemented`) 팩터만 담기가 켜지고, 준비 중·데이터 없음은 이유와
 * 함께 막는다. 담으면 아래 목록 카드에 항목이 생긴다.
 */
const FactorPicker = ({
  list,
  context,
  snippetSource,
}: {
  list: FormListSection;
  context: CardContext;
  snippetSource: SnippetCatalogSource;
}) => {
  const listId = useId();
  const snippets = useMemo(
    () => buildCanonicalSnippetCatalog(snippetSource),
    [snippetSource],
  );
  const groups = useMemo(
    () => groupFactors(snippetSource.factors),
    [snippetSource.factors],
  );
  const [chosen, setChosen] = useState<FactorCategory | null>(null);
  const category = chosen ?? groups[0]?.category ?? null;
  const shown = groups.find((group) => group.category === category)?.factors ?? [];
  if (snippetSource.status !== "ready")
    return (
      <p className="strategy-guide__hint" role="status">
        {t(
          snippetSource.status === "loading"
            ? "guide.factors.loading"
            : "guide.factors.unavailable",
        )}
      </p>
    );
  return (
    <div className="strategy-guide__factors">
      <div
        className="strategy-guide__chips"
        role="group"
        aria-label={t("guide.factors.categories")}
      >
        {groups.map((group) => (
          <button
            key={group.category}
            type="button"
            className="strategy-guide__chip"
            aria-pressed={group.category === category}
            aria-controls={listId}
            onClick={() => setChosen(group.category)}
          >
            {tCode(`guide.factors.category.${group.category}`, group.category)}{" "}
            <span className="strategy-guide__count">{group.factors.length}</span>
          </button>
        ))}
      </div>
      <ul id={listId} className="strategy-guide__factor-list">
        {shown.map((factor) => (
          <FactorRow
            key={factor.factor_id}
            factor={factor}
            list={list}
            context={context}
            snippets={snippets}
          />
        ))}
      </ul>
      <p className="strategy-guide__hint">{t("guide.factors.custom")}</p>
    </div>
  );
};

const FactorRow = ({
  factor,
  list,
  context,
  snippets,
}: {
  factor: FactorDefinition;
  list: FormListSection;
  context: CardContext;
  snippets: ReturnType<typeof buildCanonicalSnippetCatalog>;
}) => {
  const reasonId = useId();
  const addition = factorAddition(factor, snippets, list);
  return (
    <li className="strategy-guide__factor">
      <div>
        <strong>{factor.label}</strong>{" "}
        <Badge tone={factor.availability === "implemented" ? "ok" : "neutral"}>
          {tCode(`guide.factors.availability.${factor.availability}`, factor.availability)}
        </Badge>
        <p>{factor.description}</p>
      </div>
      <div className="strategy-guide__factor-action">
        <Button
          size="small"
          disabled={addition.status !== "ready" || context.transactions.settling}
          aria-describedby={addition.status === "ready" ? undefined : reasonId}
          aria-label={`${factor.label} · ${t("guide.factors.add")}`}
          onClick={() => {
            if (addition.status === "ready")
              context.transactions.apply(
                addition.operation,
                factor.label,
                GUIDE_OWNER,
                NO_FOCUS,
              );
          }}
        >
          {t("guide.factors.add")}
        </Button>
        {addition.status === "ready" ? null : (
          <span id={reasonId} className="strategy-guide__hint">
            {t(`guide.factors.blocked.${addition.reason}`)}
          </span>
        )}
      </div>
    </li>
  );
};

/**
 * 방금 바뀐 줄. 원문이 바뀌면 렌더 중에 직전 원문을 갈아 끼운다(effect 로 미루면 한 프레임 옛 강조가 남는다).
 * 미리보기 본문과 미리보기 탭의 "N줄 바뀜" 표식이 같은 집합을 읽는다.
 */
const useChangedLines = (source: string): ReadonlySet<number> => {
  const [tracked, setTracked] = useState({ previous: source, current: source });
  if (tracked.current !== source)
    setTracked({ previous: tracked.current, current: source });
  const { previous, current } = tracked;
  return useMemo(() => changedLines(previous, current), [previous, current]);
};

/** 만들어지는 YAML 을 읽기 전용으로 보이고, 방금 바뀐 줄을 표시한다. */
const YamlPreview = ({
  source,
  changed,
}: {
  source: string;
  changed: ReadonlySet<number>;
}) => {
  const lines = source.split("\n");
  return (
    <aside className="strategy-guide__preview" aria-label={t("guide.preview")}>
      <h3>{t("guide.preview")}</h3>
      <p className="strategy-guide__hint">{t("guide.preview.description")}</p>
      <pre tabIndex={0} aria-label={t("guide.preview.source")}>
        {lines.map((line, index) => (
          <span
            key={index}
            className={changed.has(index) ? "strategy-guide__line strategy-guide__line--changed" : "strategy-guide__line"}
          >
            {changed.has(index) ? (
              <span className="sr-only">{t("guide.preview.changed")} </span>
            ) : null}
            {line}
            {"\n"}
          </span>
        ))}
      </pre>
    </aside>
  );
};
