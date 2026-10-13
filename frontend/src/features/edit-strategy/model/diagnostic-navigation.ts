import type {
  FormListSection,
  FormProjection,
  FormSection,
} from "./form-projection";
import { authoredFactors } from "./graph-transactions";
import {
  projectPipeline,
  pipelineDocumentProperties,
  type PipelineProjection,
} from "./pipeline-projection";
import type { JsonSchema } from "./schema-navigator";
import type { StrategyView } from "./strategy-views";
import { factorGraphPointer, factorIndexAtPointer } from "./use-execution-plans";

/**
 * 문제 행을 눌렀을 때 갈 곳(WORKFLOW P1-01). 문제 목록은 모든 탭에서 보이므로, 클릭이 지금 탭에서
 * 끝나는지 원문 탭으로 돌아가야 하는지를 먼저 판정한다.
 */
export type DiagnosticDestination =
  /** 지금 탭이 그 pointer의 카드·노드를 그린다 — 탭을 지키고 선택만 옮긴다. */
  | "current-view"
  /** 지금 탭이 그리지 못한다 — 원문 탭(YAML)에서 그 줄로 간다. */
  | "source";

/**
 * `owner`가 `pointer` 자신이거나 그 조상인가. 루트(`""`)는 문서 전체라 아무것도 소유하지 않는다. 문제 행
 * 목적지 판정과 Form 항목·파이프라인 카드의 선택 강조가 같은 규칙을 쓴다.
 */
export const coversPointer = (
  owner: string,
  pointer: string | undefined,
): boolean =>
  pointer !== undefined &&
  owner !== "" &&
  (pointer === owner || pointer.startsWith(`${owner}/`));

const listOwners = (section: FormListSection): string[] => [
  section.pointer,
  ...section.items.flatMap((item) => [
    item.pointer,
    ...item.fields.map((field) => field.pointer),
  ]),
];

const sectionOwners = (section: FormSection): string[] =>
  section.kind === "list"
    ? listOwners(section)
    : [
        section.pointer,
        ...section.fields.map((field) => field.pointer),
        ...section.lists.flatMap(listOwners),
      ];

/**
 * Form 투영이 그 pointer를 담은 섹션·항목·필드를 실제로 그리는가. 카드가 없으면 Form 탭에 머물러도
 * 보여 줄 곳이 없으므로 원문 탭으로 보낸다.
 */
export const formCoversPointer = (
  projection: FormProjection | null,
  pointer: string,
): boolean =>
  projection !== null &&
  projection.sections.some((section) =>
    sectionOwners(section).some((owner) => coversPointer(owner, pointer)),
  );

/**
 * Graph 편집 표면이 그 pointer의 팩터 그래프를 그리는가. 표면은 backend plan이 없어도 문서의 팩터로
 * 그리므로(Phase 4 감사 R4) plan이 아니라 parse tree로 판정한다. 팩터 카드(`/factors/0/factor_id`)는
 * Form 소유라 여기 들어오지 않는다.
 */
export const graphCoversPointer = (tree: unknown, pointer: string): boolean => {
  const index = factorIndexAtPointer(pointer);
  if (index === null) return false;
  if (!coversPointer(factorGraphPointer(index), pointer)) return false;
  return index < authoredFactors(tree).length;
};

/**
 * Graph 탭의 문서 속성과 파이프라인 카드·목록 항목이 그 pointer를 그리는가.
 * 문서 속성의 필드 집합은 렌더링과 공유한다. 미표시 파라미터·버전 상수는 원문에서 고친다.
 */
export const pipelineCoversPointer = (
  pipeline: PipelineProjection,
  pointer: string,
): boolean =>
  (pipelineDocumentProperties(pipeline)?.fields.some((field) => coversPointer(field.pointer, pointer)) ?? false) ||
  pipeline.stages.some((stage) =>
    [
      ...stage.cards.flatMap((card) =>
        card.rows.map((row) => row.field.pointer),
      ),
      ...stage.lists.flatMap(listOwners),
    ].some((owner) => coversPointer(owner, pointer)),
  );

export type DiagnosticDestinationInput = {
  /** 지금 선택된 탭. */
  view: StrategyView;
  /** 편집기가 사는 탭(저장된 문서 포맷). */
  sourceView: "yaml" | "json";
  /** 진단이 가리키는 JSON Pointer. 문서 전체면 `""`. */
  pointer: string;
  /** Form 투영(runtime schema가 없으면 null). */
  form: FormProjection | null;
  /** Form·Graph가 함께 읽는 parse tree. */
  tree: unknown;
  /**
   * runtime schema(아직 없으면 null). Form·Graph 편집 표면은 둘 다 schema 없이는 렌더되지 않고
   * (`factor-graph-panel.tsx`의 `editing.schema === null → null`), 파이프라인 캔버스의 단계도 schema가
   * 정한다. `form !== null`로 대신 보면 Graph 판정이 Form 투영의 구현 세부에 묶이므로 page가 명시로
   * 넘긴다(2차 리뷰 R2-4).
   */
  schema: JsonSchema | null;
};

export const resolveDiagnosticDestination = ({
  view,
  sourceView,
  pointer,
  form,
  tree,
  schema,
}: DiagnosticDestinationInput): DiagnosticDestination => {
  // 문서 전체를 가리키는 진단(구문 오류 등)은 어느 카드에도 속하지 않는다.
  if (pointer === "") return "source";
  if (view === sourceView) return "source";
  // runtime schema가 아직 없으면 그래프 편집 표면 자체가 렌더되지 않아 그릴 카드가 없다
  // (P1-01 리뷰 P2-2 변형). 있으면 위 파이프라인 캔버스와 아래 고급 편집기가 함께 그린다(P4-02).
  if (view === "graph") {
    if (schema === null) return "source";
    const drawn =
      graphCoversPointer(tree, pointer) ||
      (form !== null &&
        pipelineCoversPointer(projectPipeline(schema, form, tree), pointer));
    return drawn ? "current-view" : "source";
  }
  // 그려지는 카드가 없으면 원문에서 선택한다.
  return "source";
};
