/** 원문 노드와 스키마 포트를 캔버스로 투영한다. 계산 계획·유효성·좌표는 소유하지 않는다. */
import { t, tName } from "../../../shared/config";
import { valueAtPointer } from "../../../shared/lib/yaml12";
import { projectObjectSection } from "./form-projection";
import {
  nodeKinds,
  nodeSlotsByKind,
  suggestNodeId,
} from "./graph-transactions";
import { fieldText, type SummaryNames } from "./pipeline-projection";
import { schemaFacts, type JsonSchema } from "./schema-navigator";
import type { SourceOperation } from "./source-transactions";

export type CanvasNode = {
  key: string;
  pointer: string;
  nodeId: string | null;
  connectable: boolean;
  label: string;
  inputs: { key: string; label: string; sourceId: string | null }[];
};
export type CanvasProjection = {
  nodes: CanvasNode[];
  edges: { from: string; to: string; input: string }[];
};
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export const canvasProjection = (
  tree: unknown,
  schema: JsonSchema,
  factorIndex: number,
  names: SummaryNames,
): CanvasProjection => {
  const factor = `/factors/${factorIndex}`;
  const section = projectObjectSection(schema, tree, [], `${factor}/graph`, "");
  const items =
    section?.lists.find((list) => list.key === "nodes")?.items ?? [];
  const slots = nodeSlotsByKind(schema);
  const kinds = new Map(
    nodeKinds(schema, tree, factor).map(([key, branch]) => [
      key,
      tName(schemaFacts(branch).descriptionKey),
    ]),
  );
  const ids = items.map((item) => {
    const raw = valueAtPointer(tree, item.pointer).value;
    return record(raw) && typeof raw.node_id === "string" && raw.node_id !== ""
      ? raw.node_id
      : null;
  });
  const nodes: CanvasNode[] = items.map((item, index) => {
    const raw = valueAtPointer(tree, item.pointer).value;
    const kind = record(raw) && typeof raw.kind === "string" ? raw.kind : "";
    const ownSlots = slots.get(kind);
    const titleField =
      item.fields.find((field) => field.key === ownSlots?.operator) ??
      item.fields.find((field) => field.control.kind === "catalog");
    const title =
      titleField?.control.kind === "catalog" &&
      typeof titleField.value === "string"
        ? names.catalog(titleField.control.catalog, titleField.value)
        : titleField === undefined
          ? null
          : fieldText(titleField, item.fields, names);
    const nodeId = ids[index];
    const connectable =
      nodeId !== null && ids.filter((id) => id === nodeId).length === 1;
    return {
      key: connectable ? `id:${nodeId}` : `pointer:${item.pointer}`,
      pointer: item.pointer,
      nodeId,
      connectable,
      label: `${index + 1}. ${title ?? kinds.get(kind) ?? t("graph.nodesTitle")}`,
      inputs: (ownSlots?.inputs ?? []).map((slot) => {
        const value = record(raw) ? raw[slot.key] : null;
        return {
          key: slot.key,
          label: tName(slot.facts.descriptionKey) ?? t("graph.nodesTitle"),
          sourceId: typeof value === "string" ? value : null,
        };
      }),
    };
  });
  const edges = nodes.flatMap((node) =>
    node.inputs.flatMap((input) => {
      const from = nodes.find(
        (candidate) =>
          candidate.connectable && candidate.nodeId === input.sourceId,
      );
      return from === undefined
        ? []
        : [{ from: from.key, to: node.key, input: input.key }];
    }),
  );
  return { nodes, edges };
};

/** 복제도 기존 insert-item planner를 쓴다. 다른 팩터 pointer를 실수로 복제하지 않는다. */
export const copyCanvasNode = (
  tree: unknown,
  factorPointer: string,
  pointer: string,
): SourceOperation | null => {
  const prefix = `${factorPointer}/graph/nodes/`;
  if (
    !pointer.startsWith(prefix) ||
    !/^\d+$/u.test(pointer.slice(prefix.length))
  )
    return null;
  const raw = valueAtPointer(tree, pointer).value;
  if (!record(raw)) return null;
  const nodeId = suggestNodeId(
    tree,
    factorPointer,
    typeof raw.node_id === "string" && raw.node_id ? raw.node_id : "node",
  );
  return {
    kind: "insert-item",
    parentPointer: `${factorPointer}/graph/nodes`,
    value: { ...raw, node_id: nodeId },
  };
};
