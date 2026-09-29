/**
 * 레시피(그래프 2수준) 투영(WORKFLOW P4-01·P5-01, spec D2·D9): 팩터 하나의 `graph.nodes`가 체인이면 순서
 * 목록, 아니면 고급이다. P4-01은 체인 판정과 팩터 카드 요약 문장이고, P5-01이 같은 파일에 편집 연산을 더한다.
 *
 * 체인 판정의 정본은 spec D2다. 입력 칸은 스키마 `x-reference: node`(`nodeSlotsByKind`)이고 잎은 입력 칸이 없는
 * 노드(`field`·`constant`·`parameter`)다. 출력 노드에서 거꾸로 걸어, 단계마다 잎이 아닌 입력이 하나면 그것이
 * 앞 단계이고(나머지 입력은 체인 밖 잎) 없으면 문서 순서 첫 잎이 머리다. 모든 노드가 한 번씩 쓰이고 출력
 * 말고는 정확히 한 번 참조될 때만 체인이다 — 분기·머리 재참조·안 쓰인 노드는 고급이다.
 */
import { t, tName } from "../../../shared/config";
import { valueAtPointer } from "../../../shared/lib/yaml12";
import { projectObjectSection } from "./form-projection";
import { nodeSlotsByKind } from "./graph-transactions";
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

export const projectRecipe = (
  schema: JsonSchema,
  tree: unknown,
  factorPointer: string,
): Recipe => {
  const nodesPointer = `${factorPointer}/graph/nodes`;
  const graph = valueAtPointer(tree, `${factorPointer}/graph`).value;
  const nodes: unknown[] =
    isRecord(graph) && Array.isArray(graph.nodes) ? graph.nodes : [];
  const advanced: Recipe = { kind: "advanced", nodeCount: nodes.length };
  if (nodes.length === 0) return { kind: "chain", links: [] };
  const indexById = new Map<string, number>();
  for (const [index, node] of nodes.entries()) {
    const id = isRecord(node) ? node.node_id : undefined;
    if (typeof id !== "string" || indexById.has(id)) return advanced;
    indexById.set(id, index);
  }
  const slotsByKind = nodeSlotsByKind(schema);
  // 노드마다 입력 칸 순서대로 가리키는 노드의 index. 모르는 kind·없는 노드를 가리키면 고급이다.
  const inputs: number[][] = [];
  for (const node of nodes) {
    if (!isRecord(node)) return advanced;
    const refs = slotsByKind
      .get(String(node.kind))
      ?.inputs.map(({ key }) => indexById.get(String(node[key])));
    if (refs === undefined || refs.includes(undefined)) return advanced;
    inputs.push(refs as number[]);
  }
  const output = isRecord(graph)
    ? indexById.get(String(graph.output_node_id))
    : undefined;
  if (output === undefined) return advanced;
  const referenced = nodes.map(() => 0);
  for (const index of inputs.flat()) referenced[index] += 1;
  if (referenced.some((count, index) => count !== (index === output ? 0 : 1)))
    return advanced;
  const pointerOf = (index: number) => `${nodesPointer}/${index}`;
  const links: RecipeLink[] = [];
  const used = new Set<number>();
  for (let current = output; !used.has(current);) {
    used.add(current);
    const own = inputs[current];
    if (own.length === 0) {
      links.unshift({ pointer: pointerOf(current), operands: [] });
      return used.size === nodes.length ? { kind: "chain", links } : advanced;
    }
    const tails = own.filter((index) => inputs[index].length > 0);
    if (tails.length > 1) return advanced;
    const previous = tails[0] ?? Math.min(...own);
    for (const index of own) if (index !== previous) used.add(index);
    links.unshift({
      pointer: pointerOf(current),
      operands:
        own.length > 1
          ? own.map((index) => (index === previous ? null : pointerOf(index)))
          : [],
    });
    current = previous;
  }
  return advanced;
};

/**
 * 팩터 카드 요약 문장(WORKFLOW P4-01): 체인이면 "수정 종가 → 기간 수익률(252일, 최근 21일 제외)", 아니면
 * "노드 N개 · 고급". 단계 이름은 연산 칸 값의 이름(`x-operator` 키), 연산 칸이 없으면 노드 종류 이름이다.
 * 다중 입력 단계는 입력 칸 순서대로 앞 단계와 체인 밖 잎을 보이고, 설정 칸은 적은 값 중 기본값과 다른 것만
 * 필드 조각(`fieldFragment`)으로 보인다. 잎은 설정 칸의 값(카탈로그 필드는 이름)이다. 노드가 없으면 null.
 */
export const recipeSummary = (
  schema: JsonSchema,
  tree: unknown,
  factorPointer: string,
  catalog: CatalogNames,
): string | null => {
  const recipe = projectRecipe(schema, tree, factorPointer);
  if (recipe.kind === "advanced")
    return t("recipe.summary.advanced").replace(
      "{count}",
      String(recipe.nodeCount),
    );
  if (recipe.links.length === 0) return null;
  const names: SummaryNames = { catalog, reference: () => null };
  const slotsByKind = nodeSlotsByKind(schema);
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
          .filter(
            (field) => field.written && field.value !== field.defaultValue,
          )
          .flatMap(
            (field) => fieldFragment(field, section.fields, names) ?? [],
          ),
      ];
      return parts.length === 0 ? name : `${name}(${parts.join(", ")})`;
    }),
  ].join(" → ");
};
