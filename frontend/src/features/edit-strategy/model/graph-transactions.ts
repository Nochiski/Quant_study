/**
 * 팩터 그래프 편집 → source 연산 번역(WORKFLOW P5-01, spec D7). 순수 모듈이다: tree(parse 결과)와 runtime
 * schema만 읽고 `SourceOperation` 하나를 돌려준다. 텍스트는 만들지 않는다(정본은 `planSourceOperation`).
 *
 * 노드 id는 그래프 스코프다(`graph.nodes`의 `x-defines: node`): 다른 팩터가 같은 `node_id`를 써도 합법이라
 * 참조 검사는 그 팩터의 `graph` 아래로 좁힌다(Phase 4 감사 DEFECT-P4X-002).
 */
import { escapePointerSegment } from "../../../shared/lib/yaml12";
import { findReferences } from "./document-references";
import { branchKind } from "./form-transactions";
import {
  materializeSchemaValue,
  resolveRef,
  schemaAt,
  UnsupportedSchemaShape,
  type JsonSchema,
} from "./schema-navigator";
import type { Scalar, SourceOperation } from "./source-transactions";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const valueAt = (tree: unknown, pointer: string): unknown => {
  let current: unknown = tree;
  for (const segment of pointer.split("/").slice(1)) {
    const key = segment.replaceAll("~1", "/").replaceAll("~0", "~");
    if (Array.isArray(current)) current = current[Number(key)];
    else if (isRecord(current)) current = current[key];
    else return undefined;
  }
  return current;
};

const graphPointer = (factorPointer: string): string => `${factorPointer}/graph`;
const nodesPointer = (factorPointer: string): string =>
  `${graphPointer(factorPointer)}/nodes`;

/** 그래프의 노드 배열(없으면 빈 배열). */
const nodesOf = (tree: unknown, factorPointer: string): unknown[] => {
  const nodes = valueAt(tree, nodesPointer(factorPointer));
  return Array.isArray(nodes) ? nodes : [];
};

/** 그래프의 노드 id 목록(문자열 `node_id`만, 문서 순서). */
export const graphNodeIds = (tree: unknown, factorPointer: string): string[] =>
  nodesOf(tree, factorPointer).flatMap((node) =>
    isRecord(node) && typeof node.node_id === "string" ? [node.node_id] : [],
  );

/** `nodeId`를 가진 노드의 pointer(`/factors/N/graph/nodes/M`). 없으면 null. */
export const nodePointerOf = (
  tree: unknown,
  factorPointer: string,
  nodeId: string,
): string | null => {
  const index = nodesOf(tree, factorPointer).findIndex(
    (node) => isRecord(node) && node.node_id === nodeId,
  );
  return index < 0 ? null : `${nodesPointer(factorPointer)}/${index}`;
};

/** 스칼라 필드 확정: 키가 있으면 `replace-scalar`, 없으면 그 mapping에 `insert-key`. */
const setScalar = (
  tree: unknown,
  parentPointer: string,
  key: string,
  value: Scalar,
): SourceOperation => {
  const pointer = `${parentPointer}/${escapePointerSegment(key)}`;
  const parent = valueAt(tree, parentPointer);
  return isRecord(parent) && key in parent
    ? { kind: "replace-scalar", pointer, value }
    : { kind: "insert-key", parentPointer, key, value };
};

/** 그래프 노드 union의 분기 목록(`kind` → 분기 스키마). 스키마를 따라갈 수 없으면 빈 배열. */
export const nodeKinds = (
  schema: JsonSchema,
  tree: unknown,
  factorPointer: string,
): readonly [string, JsonSchema][] => {
  const resolved = schemaAt(schema, nodesPointer(factorPointer), tree);
  const items = isRecord(resolved?.node.items)
    ? resolveRef(schema, resolved!.node.items)
    : null;
  if (items === null || !Array.isArray(items.oneOf)) return [];
  return items.oneOf.flatMap((member) => {
    const entry = branchKind(schema, member);
    return entry === null ? [] : [entry];
  });
};

/** `base`, `base_2`, `base_3` … 중 그래프에 없는 첫 id. */
export const suggestNodeId = (
  tree: unknown,
  factorPointer: string,
  base: string,
): string => {
  const taken = new Set(graphNodeIds(tree, factorPointer));
  if (!taken.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base}_${n}`;
    if (!taken.has(candidate)) return candidate;
  }
};

/**
 * 노드 추가: `kind` 분기 스키마로 최소 항목을 materialize하고 `node_id`는 `suggestNodeId(kind)`,
 * `x-reference: node` 필드는 빈 문자열 대신 그래프의 마지막 노드 id(즉시 valid 가능). `graph.nodes`가 없으면
 * 키를 열면서 넣는다(P4-03이 만든 빈 팩터는 `nodes: []`라 `insert-item`).
 */
export const addNode = (
  tree: unknown,
  factorPointer: string,
  kind: string,
  schema: JsonSchema,
): { op: SourceOperation; nodeId: string } | { error: "unknown-kind" } => {
  const branch = nodeKinds(schema, tree, factorPointer).find(
    ([name]) => name === kind,
  )?.[1];
  if (branch === undefined) return { error: "unknown-kind" };
  let node: unknown;
  try {
    node = materializeSchemaValue(schema, branch);
  } catch (error) {
    if (error instanceof UnsupportedSchemaShape) return { error: "unknown-kind" };
    throw error;
  }
  if (!isRecord(node)) return { error: "unknown-kind" };
  const ids = graphNodeIds(tree, factorPointer);
  const nodeId = suggestNodeId(tree, factorPointer, kind);
  const last = ids[ids.length - 1] ?? "";
  const properties = isRecord(branch.properties) ? branch.properties : {};
  const value: Record<string, unknown> = { ...node, node_id: nodeId };
  for (const [key, property] of Object.entries(properties)) {
    const resolvedProperty = isRecord(property)
      ? resolveRef(schema, property)
      : null;
    if (resolvedProperty?.["x-reference"] === "node" && last !== "")
      value[key] = last;
  }
  const graph = valueAt(tree, graphPointer(factorPointer));
  const nodes = valueAt(tree, nodesPointer(factorPointer));
  if (Array.isArray(nodes))
    return {
      op: { kind: "insert-item", parentPointer: nodesPointer(factorPointer), value },
      nodeId,
    };
  if (isRecord(graph))
    return {
      op: {
        kind: "insert-key",
        parentPointer: graphPointer(factorPointer),
        key: "nodes",
        value: [value],
      },
      nodeId,
    };
  return {
    op: {
      kind: "insert-key",
      parentPointer: factorPointer,
      key: "graph",
      value: { nodes: [value], output_node_id: nodeId },
    },
    nodeId,
  };
};

/** 노드 필드 확정(`operator`·`window`·`field_id` 등). 값 검증은 backend compile이 한다. */
export const setNodeField = (
  tree: unknown,
  nodePointer: string,
  key: string,
  value: Scalar,
): SourceOperation => setScalar(tree, nodePointer, key, value);

/**
 * 입력 슬롯 재연결: `inputKey`(`input_node_id`·`left_node_id` …)를 `targetNodeId`로. 자기 자신이면
 * `self`, 같은 그래프에 없는 id면 `not-found`(사이클·타입은 backend가 판정).
 */
export const rewireInput = (
  tree: unknown,
  nodePointer: string,
  inputKey: string,
  targetNodeId: string,
): SourceOperation | { error: "self" | "not-found" } => {
  const node = valueAt(tree, nodePointer);
  if (isRecord(node) && node.node_id === targetNodeId) return { error: "self" };
  const factorPointer = nodePointer.replace(/\/graph\/nodes\/\d+$/, "");
  if (factorPointer === nodePointer) return { error: "not-found" };
  if (!graphNodeIds(tree, factorPointer).includes(targetNodeId))
    return { error: "not-found" };
  return setScalar(tree, nodePointer, inputKey, targetNodeId);
};

/** 출력 노드 지정. 그래프에 없는 id면 `not-found`. */
export const setOutput = (
  tree: unknown,
  factorPointer: string,
  nodeId: string,
): SourceOperation | { error: "not-found" } =>
  graphNodeIds(tree, factorPointer).includes(nodeId)
    ? setScalar(tree, graphPointer(factorPointer), "output_node_id", nodeId)
    : { error: "not-found" };

/**
 * 노드 삭제 가드(D7): 같은 그래프 안에서 `*_node_id`·`output_node_id`가 이 노드를 가리키면 거부하고 참조
 * pointer를 돌려준다. 탐색은 그 팩터의 `graph` 아래로 스코프한다(다른 팩터의 같은 id는 참조가 아니다).
 */
export const removeNode = (
  tree: unknown,
  factorPointer: string,
  nodeId: string,
): SourceOperation | { error: "referenced"; by: string[] } | { error: "not-found" } => {
  const pointer = nodePointerOf(tree, factorPointer, nodeId);
  if (pointer === null) return { error: "not-found" };
  const by = findReferences(tree, "node", nodeId, pointer, {
    within: graphPointer(factorPointer),
  }).map((reference) => reference.pointer);
  if (by.length > 0) return { error: "referenced", by };
  return { kind: "remove", pointer };
};

/** `missing_policy` 확정. */
export const setMissingPolicy = (
  tree: unknown,
  factorPointer: string,
  policy: string,
): SourceOperation =>
  setScalar(tree, graphPointer(factorPointer), "missing_policy", policy);
