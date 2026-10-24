/**
 * 새 전략 단계별 안내(RD-04-D)의 단계 모델. 파이프라인 투영(`projectPipeline`)을 한 단계씩 나눠 보일
 * 뿐이고 필드·단계 목록을 따로 적지 않는다: 단계는 스키마 `x-stage` 가 붙은 최상위 섹션 순서이고, 앞뒤에
 * 문서 머리(이름·의도)와 확인 단계를 둔다. 순수 함수이며 React·편집기를 모른다.
 *
 * 나누는 규칙: 한 단계(`x-stage`)에 최상위 목록 섹션(`factors`)이 있으면 그 목록이 단계 하나가 되고, 같은
 * 단계의 카드(신호 결합 칸)는 그 뒤 단계가 된다. 다른 목록(`eligibility.rules`)은 자기 단계 안에 남는다.
 */
import type { DocumentDiagnostic } from "./document-state";
import type { FormField, FormListSection } from "./form-projection";
import type { PipelineCard, PipelineProjection } from "./pipeline-projection";
import { cardTemplate, pipelineDocumentProperties } from "./pipeline-projection";

export type GuideStep =
  | { kind: "document"; id: "document" }
  | {
      kind: "stage";
      /** 화면 id: 단계 값(`signal`), 단계에서 떼어 낸 최상위 목록이면 `signal.factors`. */
      id: string;
      stage: string;
      /** 최상위 목록만 담은 단계인가(팩터 담기). */
      listOnly: boolean;
      /** 같은 단계에서 목록을 떼어 낸 나머지 카드 단계인가(신호 결합). 이름을 따로 고른다. */
      split: boolean;
      listPointers: readonly string[];
      /** 처음부터 보이는 카드와 "더 설정하기"에 접는 카드(`guideCards`). */
      primary: readonly PipelineCard[];
      more: readonly PipelineCard[];
    }
  | { kind: "review"; id: "review" };

/** 최상위 목록(`/factors`)인가. 다른 섹션 안 목록(`/eligibility/rules`)은 그 단계 카드와 함께 둔다. */
const isRootList = (pointer: string): boolean =>
  /^\/[^/]+$/u.test(pointer);

/** 카드 앵커 필드의 값이 꺼져 있는 기능인가: 문서에 없고 기본값도 없음(null)이다. */
const offByDefault = (field: FormField): boolean =>
  !field.written && (!field.hasDefault || field.defaultValue === null);

/**
 * 처음 보일 카드와 접을 카드를 가른다. 카드 문장 틀(`.card`)이 있고, 기본으로 꺼져 있는 기능(점수 하한·
 * 레짐·유동성·보조 순위처럼 기본값이 없는 칸)이 아니면 핵심 칸이다. 이미 문서에 쓴 칸은 접지 않는다.
 */
export const guideCards = (
  cards: readonly PipelineCard[],
): { primary: PipelineCard[]; more: PipelineCard[] } => {
  const primary: PipelineCard[] = [];
  const more: PipelineCard[] = [];
  for (const card of cards) {
    const fields = card.rows.map((row) => row.field);
    const anchor = fields[0]!;
    const core =
      fields.some((field) => field.written) ||
      (cardTemplate(fields, anchor.descriptionKey) !== null &&
        !offByDefault(anchor));
    (core ? primary : more).push(card);
  }
  return { primary, more };
};

/** 파이프라인 투영을 안내 단계로 나눈다. 단계가 없는 문서(스키마 전)면 머리와 확인만 남는다. */
export const guideSteps = (pipeline: PipelineProjection | null): GuideStep[] => {
  const steps: GuideStep[] = [{ kind: "document", id: "document" }];
  for (const stage of pipeline?.stages ?? []) {
    const rootLists = stage.lists.filter((list) => isRootList(list.pointer));
    const innerLists = stage.lists.filter((list) => !isRootList(list.pointer));
    const { primary, more } = guideCards(stage.cards);
    if (rootLists.length > 0)
      steps.push({
        kind: "stage",
        id: `${stage.stage}.${rootLists.map((list) => list.key).join("+")}`,
        stage: stage.stage,
        listOnly: true,
        split: false,
        listPointers: rootLists.map((list) => list.pointer),
        primary: [],
        more: [],
      });
    if (innerLists.length > 0 || stage.cards.length > 0 || rootLists.length === 0)
      steps.push({
        kind: "stage",
        id: stage.stage,
        stage: stage.stage,
        listOnly: false,
        split: rootLists.length > 0,
        listPointers: innerLists.map((list) => list.pointer),
        primary,
        more,
      });
  }
  steps.push({ kind: "review", id: "review" });
  return steps;
};

/**
 * 단계 목록에 보일 단계 상태. `errors`는 그 단계 칸·목록에 붙은 backend 오류 진단 수이고, `filled`는 그
 * 단계에서 문서에 값을 하나라도 썼는가(목록이면 항목이 있는가)다. 화면은 오류가 있으면 오류, 오류 없이
 * 채웠으면 완료, 아니면 미작성으로 그린다. 확인 단계는 고를 칸이 없어 늘 미작성이다.
 */
export type GuideStepStatus = { errors: number; filled: boolean };

const errorCount = (diagnostics: readonly DocumentDiagnostic[]): number =>
  diagnostics.filter((diagnostic) => diagnostic.severity === "error").length;

/** 기본값이 아니라 문서에 실제 값을 적었는가. 빈 문자열(`title: ""`)과 null 은 적지 않은 것으로 본다. */
const holdsValue = (field: FormField): boolean =>
  field.written && field.value !== "" && field.value !== null;

const listStatus = (list: FormListSection): GuideStepStatus => ({
  errors:
    errorCount(list.diagnostics) +
    list.items.reduce(
      (sum, item) =>
        sum +
        errorCount(item.diagnostics) +
        item.fields.reduce((inner, field) => inner + errorCount(field.diagnostics), 0),
      0,
    ),
  filled: list.items.length > 0,
});

const fieldsStatus = (fields: readonly FormField[]): GuideStepStatus => ({
  errors: fields.reduce((sum, field) => sum + errorCount(field.diagnostics), 0),
  filled: fields.some(holdsValue),
});

export const guideStepStatus = (
  step: GuideStep,
  pipeline: PipelineProjection | null,
): GuideStepStatus => {
  if (pipeline === null || step.kind === "review") return { errors: 0, filled: false };
  if (step.kind === "document")
    return fieldsStatus(pipelineDocumentProperties(pipeline)?.fields ?? []);
  const stage = pipeline.stages.find((candidate) => candidate.stage === step.stage);
  const parts = [
    fieldsStatus(
      [...step.primary, ...step.more].flatMap((card) => card.rows.map((row) => row.field)),
    ),
    ...(stage?.lists ?? [])
      .filter((list) => step.listPointers.includes(list.pointer))
      .map(listStatus),
  ];
  return {
    errors: parts.reduce((sum, part) => sum + part.errors, 0),
    filled: parts.some((part) => part.filled),
  };
};
