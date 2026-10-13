/**
 * 레시피 편집은 원문 연산만 만든다. 체인 판정은 projectRecipe, 노드 씨앗은 addNode,
 * 원문 보존·한 번의 undo는 planSourceOperations가 소유한다. 계산 의미는 backend가 판정한다.
 */
import {
  escapePointerSegment,
  valueAtPointer,
} from "../../../shared/lib/yaml12";
import {
  addNode,
  nodeSlotsByKind,
  setNodeField,
  suggestNodeId,
  type ChosenOperator,
} from "./graph-transactions";
import { projectRecipe, type RecipeLink } from "./recipe-projection";
import type { JsonSchema } from "./schema-navigator";
import {
  applyToTree,
  type Scalar,
  type SourceOperation,
} from "./source-transactions";

export type RecipeNodeSeed = {
  kind: string;
  chosen?: ChosenOperator;
  settings?: Readonly<Record<string, Scalar>>;
};
export type RecipeStepSeed = RecipeNodeSeed & {
  /** 다중 입력의 앞 단계 자리. 나머지 슬롯은 같은 순서의 새 잎으로 채운다. */
  previousInput?: string;
  operands?: readonly RecipeNodeSeed[];
};
export type RecipeOperation =
  | { kind: "insert"; index: number; seed: RecipeStepSeed }
  | { kind: "remove"; index: number }
  | { kind: "move"; index: number; to: number }
  | { kind: "replace"; index: number; seed: RecipeStepSeed }
  | { kind: "setting"; index: number; key: string; value: Scalar };
export type RecipeFailure =
  | "advanced"
  | "not-found"
  | "invalid-step"
  | "invalid-inputs"
  | "chain-required"
  | "unknown-kind"
  | "unsupported-schema";
export type RecipeTransaction =
  | { ops: SourceOperation[] }
  | { error: RecipeFailure };

type Node = Record<string, unknown>;
const record = (value: unknown): value is Node =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const at = (tree: unknown, pointer: string): unknown =>
  valueAtPointer(tree, pointer).value;
const nodeAt = (tree: unknown, pointer: string): Node =>
  at(tree, pointer) as Node;

/** 여러 연산의 pointer는 앞 연산을 반영한 tree 기준이다. 이 tree는 계획 중에만 존재한다. */
export const recipeTransaction = (
  tree: unknown,
  schema: JsonSchema,
  factorPointer: string,
  operation: RecipeOperation,
): RecipeTransaction => {
  if (!record(tree) || !record(at(tree, `${factorPointer}/graph`)))
    return { error: "not-found" };
  const recipe = projectRecipe(schema, tree, factorPointer);
  if (recipe.kind !== "chain") return { error: "advanced" };
  const slots = nodeSlotsByKind(schema);
  const nodesPointer = `${factorPointer}/graph/nodes`;
  const graphPointer = `${factorPointer}/graph`;
  let current = tree;
  const ops: SourceOperation[] = [];
  const emit = (op: SourceOperation): void => {
    const next = applyToTree(current, op);
    if (next === null)
      throw new Error("레시피 원문 연산의 pointer가 유효하지 않습니다");
    current = next;
    ops.push(op);
  };
  const set = (pointer: string, key: string, value: Scalar) => {
    // 연결이 그대로인 단계의 따옴표·주석은 재기록하지 않는다.
    if (at(current, `${pointer}/${escapePointerSegment(key)}`) !== value)
      emit(setNodeField(current, pointer, key, value));
  };
  const finish = (): RecipeTransaction =>
    projectRecipe(schema, current, factorPointer).kind === "chain"
      ? { ops }
      : { error: "chain-required" };
  const index = operation.index;
  const count = recipe.links.length;
  if (
    !Number.isInteger(index) ||
    index < 0 ||
    index >= count + (operation.kind === "insert" ? 1 : 0)
  )
    return { error: "not-found" };

  if (operation.kind === "setting") {
    const pointer = recipe.links[index]!.pointer;
    const node = nodeAt(current, pointer);
    if (
      !slots
        .get(String(node.kind))
        ?.settings.some(({ key }) => key === operation.key)
    )
      return { error: "invalid-step" };
    set(pointer, operation.key, operation.value);
    return finish();
  }

  // 이동·삭제는 단계와 그 전용 부가 잎을 한 묶음으로 취급한다. 다른 단계의 잎을 훔치지 않는다.
  const bundles = recipe.links.map((link) => ({
    node: nodeAt(tree, link.pointer),
    sides: link.operands.flatMap((pointer) =>
      pointer === null ? [] : [nodeAt(tree, pointer)],
    ),
    previousInput: previousSlot(link, nodeAt(tree, link.pointer)),
  }));
  function previousSlot(link: RecipeLink, node: Node): string | undefined {
    const inputs = slots.get(String(node.kind))!.inputs;
    return inputs.length === 1
      ? inputs[0]!.key
      : inputs[link.operands.indexOf(null)]?.key;
  }
  const removeBundle = (link: RecipeLink) => {
    const pointers = [
      link.pointer,
      ...link.operands.filter((pointer): pointer is string => pointer !== null),
    ];
    pointers.sort(
      (a, b) => Number(b.split("/").at(-1)) - Number(a.split("/").at(-1)),
    );
    for (const pointer of pointers) emit({ kind: "remove", pointer });
  };
  const rewire = (ordered: typeof bundles) => {
    const nodes = at(current, nodesPointer) as Node[];
    for (let i = 1; i < ordered.length; i++) {
      const bundle = ordered[i]!;
      const position = nodes.findIndex(
        (node) => node.node_id === bundle.node.node_id,
      );
      set(
        `${nodesPointer}/${position}`,
        bundle.previousInput!,
        String(ordered[i - 1]!.node.node_id),
      );
    }
    set(
      graphPointer,
      "output_node_id",
      String(ordered.at(-1)?.node.node_id ?? ""),
    );
  };

  if (operation.kind === "remove") {
    if (index === 0 && count > 1) return { error: "chain-required" };
    removeBundle(recipe.links[index]!);
    bundles.splice(index, 1);
    rewire(bundles);
    return finish();
  }
  if (operation.kind === "move") {
    if (
      !Number.isInteger(operation.to) ||
      operation.to < 0 ||
      operation.to >= count
    )
      return { error: "not-found" };
    if (index === operation.to) return { ops: [] };
    if (index === 0 || operation.to === 0) return { error: "chain-required" };
    const [bundle] = bundles.splice(index, 1);
    removeBundle(recipe.links[index]!);
    bundles.splice(operation.to, 0, bundle!);
    const before = bundles[operation.to + 1];
    const nodes = at(current, nodesPointer) as Node[];
    const beforeId = before?.sides[0]?.node_id ?? before?.node.node_id;
    let position =
      before === undefined
        ? nodes.length
        : nodes.findIndex((node) => node.node_id === beforeId);
    for (const node of [...bundle!.sides, bundle!.node])
      emit({
        kind: "insert-item",
        parentPointer: nodesPointer,
        index: position++,
        value: node,
      });
    rewire(bundles);
    return finish();
  }

  const seed = operation.seed;
  const ownSlots = slots.get(seed.kind);
  if (ownSlots === undefined) return { error: "unknown-kind" };
  const isHead = index === 0;
  if (
    (isHead && ownSlots.inputs.length !== 0) ||
    (!isHead && ownSlots.inputs.length === 0) ||
    (operation.kind === "insert" && isHead && count > 0)
  )
    return { error: "invalid-step" };
  const previousInput =
    ownSlots.inputs.length === 1 ? ownSlots.inputs[0]!.key : seed.previousInput;
  if (!isHead && !ownSlots.inputs.some(({ key }) => key === previousInput))
    return { error: "invalid-inputs" };
  if ((seed.operands?.length ?? 0) !== Math.max(0, ownSlots.inputs.length - 1))
    return { error: "invalid-inputs" };
  for (const operand of seed.operands ?? []) {
    if (slots.get(operand.kind)?.inputs.length !== 0)
      return { error: "invalid-inputs" };
  }
  if (operation.kind === "replace") removeBundle(recipe.links[index]!);
  const nextBundle = bundles[index + (operation.kind === "replace" ? 1 : 0)];
  const nodes = at(current, nodesPointer) as Node[];
  const beforeId = nextBundle?.sides[0]?.node_id ?? nextBundle?.node.node_id;
  let position =
    nextBundle === undefined
      ? nodes.length
      : nodes.findIndex((node) => node.node_id === beforeId);
  const create = (
    definition: RecipeNodeSeed,
  ): Node | { error: RecipeFailure } => {
    const own = slots.get(definition.kind);
    if (own === undefined) return { error: "unknown-kind" };
    if (
      Object.keys(definition.settings ?? {}).some(
        (key) => !own.settings.some((slot) => slot.key === key),
      )
    )
      return { error: "invalid-step" };
    const added = addNode(
      current,
      factorPointer,
      definition.kind,
      schema,
      definition.chosen ?? null,
    );
    if ("error" in added) return added;
    const insertion = added.ops.find((op) => op.kind === "insert-item");
    if (insertion?.kind !== "insert-item" || !record(insertion.value))
      return { error: "invalid-step" };
    const node = { ...insertion.value, ...definition.settings };
    // 잎의 필드 이름만 규칙이 다르다. 접미사 충돌 처리는 기존 그래프 owner를 공유한다.
    const field = definition.settings?.field_id;
    if (typeof field === "string")
      node.node_id = suggestNodeId(
        current,
        factorPointer,
        field.split(".").at(-1)!,
      );
    return node;
  };
  const sideNodes: Node[] = [];
  for (const operand of seed.operands ?? []) {
    const node = create(operand);
    if ("error" in node) return { error: node.error as RecipeFailure };
    emit({
      kind: "insert-item",
      parentPointer: nodesPointer,
      index: position++,
      value: node,
    });
    sideNodes.push(node);
  }
  const node = create(seed);
  if ("error" in node) return { error: node.error as RecipeFailure };
  let side = 0;
  for (const { key } of ownSlots.inputs)
    node[key] =
      key === previousInput
        ? bundles[index - 1]!.node.node_id
        : sideNodes[side++]!.node_id;
  emit({
    kind: "insert-item",
    parentPointer: nodesPointer,
    index: position,
    value: node,
  });
  bundles.splice(index, operation.kind === "replace" ? 1 : 0, {
    node,
    sides: sideNodes,
    previousInput,
  });
  rewire(bundles);
  return finish();
};
