/**
 * 새 전략 단계별 안내(RD-04-D)의 단계 모델. 파이프라인 투영(`projectPipeline`)을 한 단계씩 나눠 보일
 * 뿐이고 필드·단계 목록을 따로 적지 않는다: 단계는 스키마 `x-stage` 가 붙은 최상위 섹션 순서이고, 앞뒤에
 * 문서 머리(이름·의도)와 확인 단계를 둔다. 순수 함수이며 React·편집기를 모른다.
 *
 * 나누는 규칙: 한 단계(`x-stage`)에 최상위 목록 섹션(`factors`)이 있으면 그 목록이 단계 하나가 되고, 같은
 * 단계의 카드(신호 결합 칸)는 그 뒤 단계가 된다. 다른 목록(`eligibility.rules`)은 자기 단계 안에 남는다.
 */
import { errorCount, listErrorCount } from "./diagnostic-counts";
import type { DocumentDiagnostic } from "./document-state";
import type { FormField } from "./form-projection";
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
 * 단계 목록에 보일 단계 상태(사실만). `errors`는 그 단계 칸·목록·단계 섹션 자신(문서 머리면 루트 섹션)에 붙은
 * backend 오류 진단 수, `filled`는 그 단계에서 문서에 값을 하나라도 썼는가(목록이면 항목이 있는가),
 * `required`는 그 단계 칸 중 기본값 없는 스키마 필수 칸이 있는지와 모두 값이 있는지다.
 */
export type GuideStepStatus = {
  errors: number;
  filled: boolean;
  required: "none" | "met" | "missing";
};

export type GuideStepState = "error" | "done" | "todo";

/** 기본값이 아니라 문서에 실제 값을 적었는가. 빈 문자열(`title: ""`)과 null 은 적지 않은 것으로 본다. */
const holdsValue = (field: FormField): boolean =>
  field.written && field.value !== "" && field.value !== null;

const EMPTY: GuideStepStatus = { errors: 0, filled: false, required: "none" };

const fieldsStatus = (
  fields: readonly FormField[],
  sectionDiagnostics: readonly DocumentDiagnostic[] = [],
): GuideStepStatus => {
  // 스키마 기본값이 있는 필수 칸은 비워 둬도 backend 가 채우므로 충족된 것으로 본다.
  const required = fields.filter((field) => field.required && !field.hasDefault);
  return {
    errors:
      errorCount(sectionDiagnostics) +
      fields.reduce((sum, field) => sum + errorCount(field.diagnostics), 0),
    filled: fields.some(holdsValue),
    required:
      required.length === 0 ? "none" : required.every(holdsValue) ? "met" : "missing",
  };
};

const combine = (parts: readonly GuideStepStatus[]): GuideStepStatus => ({
  errors: parts.reduce((sum, part) => sum + part.errors, 0),
  filled: parts.some((part) => part.filled),
  required: parts.some((part) => part.required === "missing")
    ? "missing"
    : parts.some((part) => part.required === "met")
      ? "met"
      : "none",
});

/**
 * 단계마다의 상태. 단계 섹션 자신의 진단(`PipelineStage.diagnostics`)은 그 단계의 카드 단계가 받고, 목록만 있는
 * 단계(카드 단계가 따로 없음)면 목록 단계가 받는다 — 같은 진단을 두 단계가 세지 않는다.
 */
export const guideStepStatuses = (
  steps: readonly GuideStep[],
  pipeline: PipelineProjection | null,
): GuideStepStatus[] =>
  steps.map((step) => {
    if (pipeline === null || step.kind === "review") return EMPTY;
    if (step.kind === "document") {
      const root = pipelineDocumentProperties(pipeline);
      return fieldsStatus(root?.fields ?? [], root?.diagnostics ?? []);
    }
    const stage = pipeline.stages.find((candidate) => candidate.stage === step.stage);
    const ownsStage =
      !step.listOnly ||
      !steps.some(
        (other) => other.kind === "stage" && other.stage === step.stage && !other.listOnly,
      );
    const lists = (stage?.lists ?? []).filter((list) => step.listPointers.includes(list.pointer));
    return combine([
      fieldsStatus(
        [...step.primary, ...step.more].flatMap((card) => card.rows.map((row) => row.field)),
        ownsStage ? (stage?.diagnostics ?? []) : [],
      ),
      ...lists.map((list) => ({
        errors: listErrorCount(list),
        filled: list.items.length > 0,
        required: "none" as const,
      })),
    ]);
  });

/**
 * 단계 목록 표시. 오류(backend 진단)가 먼저다. 오류가 없을 때, 필수 칸이 있는 단계는 그 칸을 모두 채워야 완료이고,
 * 필수 칸이 없는 단계는 값을 썼거나 사용자가 한 번 지나갔으면(기본값을 받아들였다) 완료다. 의미 규칙(어떤 값이
 * 맞는가)은 다시 판정하지 않는다 — 그것은 backend 진단의 몫이다. 확인 단계는 고를 칸이 없어 늘 미작성이다.
 */
export const guideStepState = (
  step: GuideStep,
  status: GuideStepStatus,
  visited: boolean,
): GuideStepState => {
  if (status.errors > 0) return "error";
  if (step.kind === "review") return "todo";
  if (status.required === "missing") return "todo";
  if (status.required === "met") return "done";
  return status.filled || visited ? "done" : "todo";
};

/**
 * 안내 단계마다 AI 도우미 추천 질문의 문구 키(`guide.assist.suggest.<단계 id>.<n>`, 최대 3개). 단계 id 가 스키마에서
 * 나오므로 id 가 바뀌면 문구가 조용히 사라진다 — 모든 단계에 첫 문구가 있는지는 테스트가 고정한다.
 */
export const guideSuggestionKeys = (step: GuideStep): string[] =>
  [1, 2, 3].map((n) => `guide.assist.suggest.${step.id}.${n}`);
