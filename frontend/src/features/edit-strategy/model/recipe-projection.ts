/**
 * 레시피(그래프 2수준) 투영(WORKFLOW P4-01·P5-01, spec D2·D9): 팩터 하나의 `graph.nodes`가 체인이면 순서
 * 목록, 아니면 고급이다. P4-01은 체인 판정과 팩터 카드 요약 문장이고, P5-01이 같은 파일에 편집 연산을 더한다.
 *
 * 체인 판정의 정본은 spec D2이고 순서는 `graph.nodes` 의 문서 순서다(backend fixture 검사와 같은 해석). 잎은
 * 입력 칸(스키마 `x-reference: node`, `nodeSlotsByKind`)이 없는 노드(`field`·`constant`·`parameter`)다. 머리는
 * 첫 노드이고 잎이어야 한다. 잎이 아닌 노드가 문서 순서대로 단계이고, 단계마다 직전 단계(첫 단계는 머리)를
 * 정확히 한 번 읽으며 나머지 입력은 아직 쓰이지 않은 체인 밖 잎이다. 출력은 마지막 단계이고 머리 말고 모든
 * 잎이 부가 입력으로 한 번씩 쓰인다 — 분기·머리 재참조·잎 공유·안 쓰인 노드는 고급이다.
 */
import { t, tName } from "../../../shared/config";
import { valueAtPointer } from "../../../shared/lib/yaml12";
import { projectObjectSection } from "./form-projection";
import {
  nodeSlotsByKind,
  settingShown,
  type NodeSlots,
} from "./graph-transactions";
import {
  fieldFragment,
  fieldText,
  type CatalogNames,
  type SummaryNames,
} from "./pipeline-projection";
import type { JsonSchema } from "./schema-navigator";

export type RecipeLink = {
  /** 노드 pointer(`/factors/N/graph/nodes/M`). */
  pointer: string;
  /**
   * 다중 입력 단계의 입력 칸 순서대로: 앞 단계 자리는 null, 나머지는 체인 밖 잎의 pointer. 머리와 입력이 하나인
   * 단계는 빈 배열이다.
   */
  operands: readonly (string | null)[];
};

export type Recipe =
  /** 머리(소스 잎)부터 출력 노드까지. 노드가 없는 그래프는 빈 체인이다. */
  | { kind: "chain"; links: RecipeLink[] }
  | { kind: "advanced"; nodeCount: number };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** kind → 칸 표를 받아 판정한다. 요약이 같은 표로 단계를 그리므로 표를 두 번 읽지 않는다. */
const recipeOf = (
  slotsByKind: ReadonlyMap<string, NodeSlots>,
  tree: unknown,
  factorPointer: string,
): Recipe => {
  const graph = valueAtPointer(tree, `${factorPointer}/graph`).value;
  const nodes: unknown[] =
    isRecord(graph) && Array.isArray(graph.nodes) ? graph.nodes : [];
  if (nodes.length === 0) return { kind: "chain", links: [] };
  const advanced: Recipe = { kind: "advanced", nodeCount: nodes.length };
  const ids = nodes.map((node) => (isRecord(node) ? node.node_id : undefined));
  // 이름이 없거나 겹친 문서는 편집 대상의 정체성이 모호하므로 backend 진단을 기다린다.
  if (
    ids.some((id) => typeof id !== "string" || id === "") ||
    new Set(ids).size !== ids.length
  )
    return advanced;
  // 노드마다 입력 칸 순서대로 가리키는 노드의 index(없는 id 는 -1). 모르는 kind 면 고급이다.
  const inputs: number[][] = [];
  for (const node of nodes) {
    const record = isRecord(node) ? node : {};
    const slots = slotsByKind.get(String(record.kind));
    if (slots === undefined) return advanced;
    inputs.push(
      slots.inputs.map(({ key }) =>
        typeof record[key] === "string" ? ids.indexOf(record[key]) : -1,
      ),
    );
  }
  const isLeaf = (index: number) => inputs[index].length === 0;
  if (!isLeaf(0)) return advanced;
  const pointerOf = (index: number) => `${factorPointer}/graph/nodes/${index}`;
  const links: RecipeLink[] = [{ pointer: pointerOf(0), operands: [] }];
  const sides = new Set<number>();
  let previous = 0;
  for (const [index, own] of inputs.entries()) {
    if (own.length === 0) continue;
    const others = own.filter((ref) => ref !== previous);
    // 직전 단계를 정확히 한 번 읽고, 나머지는 머리(0)·없는 노드(-1)가 아닌 새 잎이다.
    if (own.length - others.length !== 1) return advanced;
    if (others.some((ref) => ref <= 0 || !isLeaf(ref) || sides.has(ref)))
      return advanced;
    for (const ref of others) sides.add(ref);
    links.push({
      pointer: pointerOf(index),
      operands:
        own.length > 1
          ? own.map((ref) => (ref === previous ? null : pointerOf(ref)))
          : [],
    });
    previous = index;
  }
  const leaves = inputs.filter((own) => own.length === 0).length;
  return isRecord(graph) &&
    ids.indexOf(graph.output_node_id) === previous &&
    sides.size === leaves - 1
    ? { kind: "chain", links }
    : advanced;
};

export const projectRecipe = (
  schema: JsonSchema,
  tree: unknown,
  factorPointer: string,
): Recipe => recipeOf(nodeSlotsByKind(schema), tree, factorPointer);

/**
 * 팩터 카드 요약 문장(WORKFLOW P4-01): 체인이면 "수정 종가 → 기간 수익률(252일, 최근 21일 제외)", 아니면
 * "노드 N개 · 고급". 단계 이름은 연산 칸 값의 이름(`x-operator` 키), 연산 칸이 없으면 노드 종류 이름이다.
 * 다중 입력 단계는 입력 칸 순서대로 앞 단계와 체인 밖 잎을 보인다. 설정 칸은 실행 계획 카드와 같은 규칙
 * (`settingShown`: 값이 있고 기본값과 다른 것)에 맞는 값만 필드 조각(`fieldFragment`)으로 보인다. 잎은 설정 칸의
 * 값(카탈로그 필드는 이름)이다. 노드가 없으면 null.
 */
export const recipeSummary = (
  schema: JsonSchema,
  tree: unknown,
  factorPointer: string,
  catalog: CatalogNames,
): string | null => {
  const slotsByKind = nodeSlotsByKind(schema);
  const recipe = recipeOf(slotsByKind, tree, factorPointer);
  if (recipe.kind === "advanced")
    return t("recipe.summary.advanced").replace(
      "{count}",
      String(recipe.nodeCount),
    );
  if (recipe.links.length === 0) return null;
  const names: SummaryNames = { catalog, reference: () => null };
  // 체인의 노드는 스키마가 아는 kind 다(`projectRecipe`가 확인했다).
  const describe = (pointer: string) => {
    const section = projectObjectSection(schema, tree, [], pointer, "")!;
    const kind = valueAtPointer(tree, `${pointer}/kind`).value;
    const slots = slotsByKind.get(String(kind))!;
    const settings = section.fields.filter((field) =>
      slots.settings.some(({ key }) => key === field.key),
    );
    const operator = section.fields.find(
      (field) => field.key === slots.operator,
    );
    return { section, settings, operator };
  };
  const leafText = (pointer: string): string =>
    describe(pointer)
      .settings.flatMap((field) => fieldText(field, [field], names) ?? [])
      .join(" ");
  const [head, ...steps] = recipe.links;
  return [
    leafText(head.pointer),
    ...steps.map(({ pointer, operands }) => {
      const { section, settings, operator } = describe(pointer);
      const name =
        (operator === undefined
          ? null
          : fieldText(operator, section.fields, names)) ??
        tName(section.descriptionKey) ??
        pointer;
      const parts = [
        ...operands.map((operand) =>
          operand === null ? t("recipe.summary.previous") : leafText(operand),
        ),
        ...settings
          .filter((field) => settingShown(field.value, field.defaultValue))
          .flatMap(
            (field) => fieldFragment(field, section.fields, names) ?? [],
          ),
      ];
      return parts.length === 0 ? name : `${name}(${parts.join(", ")})`;
    }),
  ].join(" → ");
};
