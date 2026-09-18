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
  schemaFacts,
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

/**
 * 노드 분기 스키마에서 다른 노드를 가리키는 필드 키(`input_node_id`, `left_node_id`, …). 판정은
 * `schemaFacts(...).reference === "node"` 하나다(SoT: 참조 사실의 owner는 `schemaFacts`; 리뷰 P1-1).
 * `addNode`의 참조 채우기와 P5-02의 입력 슬롯 목록·`rewireInput` 키 검증이 같은 함수를 쓴다.
 */
export const nodeReferenceKeys = (
  schema: JsonSchema,
  branch: JsonSchema,
): string[] => {
  const properties = isRecord(branch.properties) ? branch.properties : {};
  return Object.entries(properties).flatMap(([key, property]) => {
    const node = isRecord(property) ? resolveRef(schema, property) : null;
    return node !== null && schemaFacts(node).reference === "node" ? [key] : [];
  });
};

/** `nodePointer`가 가리키는 노드의 분기 스키마(문서의 `kind`로 해소). 못 찾으면 null. */
const nodeBranchAt = (
  schema: JsonSchema,
  tree: unknown,
  nodePointer: string,
): JsonSchema | null => {
  const resolved = schemaAt(schema, nodePointer, tree);
  return resolved === null || resolved.branches !== null ? null : resolved.node;
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
 * 노드 추가: `kind` 분기 스키마로 최소 항목을 materialize하고 `node_id`는 `suggestNodeId(kind)`. 참조
 * 슬롯(`nodeReferenceKeys`)이 **하나뿐인** 분기(unary·time_series·cross_sectional·group)는 그 슬롯을 그래프의
 * 마지막 노드 id로 채워 즉시 valid 가능하게 하고, 둘 이상인 분기(binary·comparison·conditional)는 같은
 * 노드를 여러 슬롯에 넣으면 `x op x`나 타입 불일치가 되므로 빈 문자열로 두어 사용자가 고르게 한다(리뷰
 * P2-3). `graph.nodes`가 없으면 키를 열면서 넣는다(P4-03이 만든 빈 팩터는 `nodes: []`라 `insert-item`).
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
  const value: Record<string, unknown> = { ...node, node_id: nodeId };
  const references = nodeReferenceKeys(schema, branch);
  if (references.length === 1 && last !== "") value[references[0]!] = last;
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
 * `self`, 같은 그래프에 없는 id면 `not-found`(사이클·타입은 backend가 판정). `schema`를 주면 `inputKey`가
 * 그 노드 분기의 참조 슬롯(`nodeReferenceKeys`)인지도 검사해 아니면 `not-found`(리뷰 P2-4 — 노드 분기는
 * `additionalProperties: false`라 모르는 키는 compile error가 된다).
 */
export const rewireInput = (
  tree: unknown,
  nodePointer: string,
  inputKey: string,
  targetNodeId: string,
  schema?: JsonSchema,
): SourceOperation | { error: "self" | "not-found" } => {
  const node = valueAt(tree, nodePointer);
  if (isRecord(node) && node.node_id === targetNodeId) return { error: "self" };
  const factorPointer = nodePointer.replace(/\/graph\/nodes\/\d+$/, "");
  if (factorPointer === nodePointer) return { error: "not-found" };
  if (!graphNodeIds(tree, factorPointer).includes(targetNodeId))
    return { error: "not-found" };
  if (schema !== undefined) {
    const branch = nodeBranchAt(schema, tree, nodePointer);
    if (branch === null || !nodeReferenceKeys(schema, branch).includes(inputKey))
      return { error: "not-found" };
  }
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
