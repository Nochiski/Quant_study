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

/** Graph 편집기의 feedback owner. Form(`form`)·스니펫(`snippet`)과 슬롯을 나눈다(Phase 4 감사 R2). */
export const GRAPH_OWNER = "graph";

/** 문서(parse tree)의 팩터 목록 — plan projection이 없어도 편집 대상은 여기서 온다(Phase 4 감사 R4). */
export const authoredFactors = (
  tree: unknown,
): { index: number; factorId: string; label: string }[] => {
  const factors =
    isRecord(tree) && Array.isArray(tree.factors) ? tree.factors : [];
  return factors.map((factor, index) => ({
    index,
    factorId:
      isRecord(factor) && typeof factor.factor_id === "string"
        ? factor.factor_id
        : `#${index + 1}`,
    label:
      isRecord(factor) && typeof factor.label === "string" ? factor.label : "",
  }));
};

const NODE_POINTER = /^(\/factors\/\d+\/graph\/nodes\/\d+)(?:\/|$)/;

/** 선택 pointer가 이 팩터의 노드(또는 그 아래 필드)를 가리키면 노드 pointer, 아니면 null(pointer 규약 R6). */
export const selectedNodePointer = (
  selectedPointer: string | undefined,
  factorPointer: string,
): string | null => {
  const match = NODE_POINTER.exec(selectedPointer ?? "");
  return match !== null &&
    match[1]!.startsWith(`${factorPointer}/graph/nodes/`)
    ? match[1]!
    : null;
};

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
 * `addNode`의 참조 채우기와 P5-02의 입력 슬롯 목록이 같은 함수를 쓴다(`rewireInput` 키 검증은 P5X-008
 * 정리로 함께 제거).
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
 * `output_node_id`가 아직 정해지지 않았는가(없음·빈 문자열). `null`은 제외한다 — `output_node_id:`처럼 값 자리가
 * 비어 있는 표기는 `replace-scalar`가 parse 단계에서 실패해 노드 추가 전체(all-or-nothing)가 막힌다(#144 재검토).
 */
const outputUnset = (graph: unknown): boolean => {
  const output = isRecord(graph) ? graph.output_node_id : undefined;
  return output === undefined || output === "";
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
): { ops: SourceOperation[]; nodeId: string } | { error: "unknown-kind" } => {
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
  if (Array.isArray(nodes)) {
    const ops: SourceOperation[] = [
      { kind: "insert-item", parentPointer: nodesPointer(factorPointer), value },
    ];
    // 첫 노드는 출력 노드도 된다(`graph`를 새로 여는 경로와 같은 모양, Phase 5 감사 backlog 13). 출력이
    // 이미 다른 값이면 두지 않는다. 두 연산은 훅이 한 트랜잭션으로 합친다.
    if (nodes.length === 0 && outputUnset(graph))
      ops.push(
        setScalar(tree, graphPointer(factorPointer), "output_node_id", nodeId),
      );
    return { ops, nodeId };
  }
  if (isRecord(graph)) {
    // `nodes` 키가 없는 그래프도 첫 노드라 같은 규칙(리뷰 P2-5).
    const ops: SourceOperation[] = [
      {
        kind: "insert-key",
        parentPointer: graphPointer(factorPointer),
        key: "nodes",
        value: [value],
      },
    ];
    if (outputUnset(graph))
      ops.push(
        setScalar(tree, graphPointer(factorPointer), "output_node_id", nodeId),
      );
    return { ops, nodeId };
  }
  return {
    ops: [
      {
        kind: "insert-key",
        parentPointer: factorPointer,
        key: "graph",
        value: { nodes: [value], output_node_id: nodeId },
      },
    ],
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
 * 노드 삭제 가드(D7), pointer 기준(리뷰 DEFECT-132-01). 화면 표시 이름이 아니라 문서 위치가 정본이다 —
 * `node_id`가 중복이거나 없는 노드에서 id 기반 삭제는 첫 일치 노드를 지워 다른 노드가 사라진다. 같은 그래프
 * 안에서 `*_node_id`·`output_node_id`가 이 노드를 가리키면 거부하고 참조 pointer를 돌려준다. 탐색은 그
 * 팩터의 `graph` 아래로 스코프한다(다른 팩터의 같은 id는 참조가 아니다). id 기반 `removeNode`와 `rewireInput`·
 * `setOutput`·`setMissingPolicy`는 UI가 부르지 않아 정리했다(Phase 5 감사 P5X-008) — 속성·입력·출력·정책은
 * Form과 같은 필드 컨트롤이 `setNodeField`와 같은 연산을 만든다.
 */
export const removeNodeAt = (
  tree: unknown,
  factorPointer: string,
  nodePointer: string,
): SourceOperation | { error: "referenced"; by: string[] } | { error: "not-found" } => {
  const node = valueAt(tree, nodePointer);
  if (!isRecord(node)) return { error: "not-found" };
  const nodeId = typeof node.node_id === "string" ? node.node_id : "";
  const by =
    nodeId === ""
      ? []
      : findReferences(tree, "node", nodeId, nodePointer, {
          within: graphPointer(factorPointer),
        }).map((reference) => reference.pointer);
  return by.length > 0
    ? { error: "referenced", by }
    : { kind: "remove", pointer: nodePointer };
};

/**
 * `node_id` 변경(Phase 5 감사 backlog 4, P5X-009). 같은 그래프에 이미 있는 id면 `duplicate`, 빈 문자열이면
 * `empty`. 아니면 정의 자리와 같은 그래프 안의 참조(`*_node_id`·`output_node_id`)를 함께 바꾸는 연산 목록을
 * 돌려주고 훅이 한 트랜잭션(undo 1회)으로 합친다. 다른 팩터의 같은 id는 참조가 아니라 건드리지 않는다.
 * 판정은 문서 tree 기준이라 YAML에서 손으로 만든 중복은 여전히 compile이 알린다(fail-closed).
 */
export const renameNode = (
  tree: unknown,
  factorPointer: string,
  nodePointer: string,
  nextId: string,
): SourceOperation[] | { error: "duplicate" | "empty" | "not-found" } => {
  const node = valueAt(tree, nodePointer);
  if (!isRecord(node)) return { error: "not-found" };
  if (nextId === "") return { error: "empty" };
  const taken = nodesOf(tree, factorPointer).some(
    (item, index) =>
      `${nodesPointer(factorPointer)}/${index}` !== nodePointer &&
      isRecord(item) &&
      item.node_id === nextId,
  );
  if (taken) return { error: "duplicate" };
  const currentId = typeof node.node_id === "string" ? node.node_id : "";
  const definition = setScalar(tree, nodePointer, "node_id", nextId);
  if (currentId === "" || currentId === nextId) return [definition];
  const references = findReferences(tree, "node", currentId, nodePointer, {
    within: graphPointer(factorPointer),
  });
  return [
    definition,
    ...references.map(
      (reference): SourceOperation => ({
        kind: "replace-scalar",
        pointer: reference.pointer,
        value: nextId,
      }),
    ),
  ];
};
