import type { SourceRange } from "../../../shared/lib/yaml12";
import { escapePointerSegment } from "../../../shared/lib/yaml12";
import {
  propertyOptions,
  resolveRef,
  schemaAt,
  type JsonSchema,
  type PropertyOption,
} from "./schema-navigator";

export type StrategyOutlineNodeKind = "basics" | "object" | "array" | "scalar";

export type StrategyOutlineNode = {
  /** Render identity. Virtual groups do not pretend to be JSON Pointers. */
  id: string;
  /** Backend diagnostic / router selection contract (RFC 6901 JSON Pointer). */
  pointer: string;
  label: string;
  kind: StrategyOutlineNodeKind;
  present: boolean;
  range: SourceRange | null;
  children: StrategyOutlineNode[];
  arrayIndex: number | null;
  semanticIdentity: { namespace: string; value: string } | null;
};

export type StrategyOutlineSymbol = {
  id: string;
  pointer: string;
  label: string;
  description: string;
  keywords: readonly string[];
};

export type ValidParsedSource = {
  status: "ok";
  tree: Record<string, unknown>;
  valueRanges: Map<string, SourceRange>;
  keyRanges: Map<string, SourceRange>;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const rangeAt = (
  parsed: ValidParsedSource,
  pointer: string,
): SourceRange | null =>
  parsed.valueRanges.get(pointer) ?? parsed.keyRanges.get(pointer) ?? null;

const kindFrom = (
  value: unknown,
  schema: JsonSchema | null,
): Exclude<StrategyOutlineNodeKind, "basics"> => {
  if (Array.isArray(value)) return "array";
  if (isRecord(value)) return "object";
  const schemaType = schema?.type;
  if (schemaType === "array") return "array";
  if (schemaType === "object" || Array.isArray(schema?.oneOf)) return "object";
  return "scalar";
};

const identityFor = (
  item: unknown,
  arraySchema: JsonSchema | null,
): StrategyOutlineNode["semanticIdentity"] => {
  if (!isRecord(item)) return null;
  const defined = arraySchema?.["x-defines"];
  if (typeof defined === "string") {
    const value = item[`${defined}_id`];
    if (typeof value === "string") return { namespace: defined, value };
  }
  // A suffix is not semantics: field_id and universe_id are catalog references in several
  // collections. Only backend-owned runtime-schema metadata may declare item identity.
  return null;
};

const childSchema = (
  rootSchema: JsonSchema | null,
  pointer: string,
  tree: unknown,
): JsonSchema | null =>
  rootSchema ? (schemaAt(rootSchema, pointer, tree)?.node ?? null) : null;

const buildNode = (
  parsed: ValidParsedSource,
  rootSchema: JsonSchema | null,
  pointer: string,
  label: string,
  value: unknown,
  present: boolean,
  arrayIndex: number | null = null,
  arraySchema: JsonSchema | null = null,
): StrategyOutlineNode => {
  const schema = childSchema(rootSchema, pointer, parsed.tree);
  const kind = kindFrom(value, schema);
  let children: StrategyOutlineNode[] = [];
  if (Array.isArray(value)) {
    children = value.map((item, index) =>
      buildNode(
        parsed,
        rootSchema,
        `${pointer}/${index}`,
        `[${index}]`,
        item,
        true,
        index,
        schema,
      ),
    );
  } else if (isRecord(value)) {
    children = Object.entries(value).map(([key, child]) =>
      buildNode(
        parsed,
        rootSchema,
        `${pointer}/${escapePointerSegment(key)}`,
        key,
        child,
        true,
      ),
    );
  }
  return {
    id: pointer,
    pointer,
    label,
    kind,
    present,
    range: present ? rangeAt(parsed, pointer) : null,
    children,
    arrayIndex,
    semanticIdentity:
      arrayIndex === null ? null : identityFor(value, arraySchema),
  };
};

const rootOptions = (
  schema: JsonSchema | null,
  tree: Record<string, unknown>,
): PropertyOption[] => {
  if (schema === null) return [];
  const root = schemaAt(schema, "", tree);
  return root ? propertyOptions(schema, root) : [];
};

const isRootCollection = (
  rootSchema: JsonSchema | null,
  option: PropertyOption,
  value: unknown,
): boolean => {
  if (Array.isArray(value) || isRecord(value)) return true;
  if (rootSchema === null) return false;
  const resolved = resolveRef(rootSchema, option.schema);
  if (resolved === null) return false;
  return (
    resolved.type === "array" ||
    resolved.type === "object" ||
    Array.isArray(resolved.oneOf)
  );
};

/**
 * Read-only projection of the parser tree. Runtime-schema property order seeds absent root
 * sections; source order owns all present descendants. No StrategySpec value is reconstructed.
 */
export const projectStrategyOutline = (
  parsed: ValidParsedSource,
  schema: JsonSchema | null,
): StrategyOutlineNode[] => {
  const options = rootOptions(schema, parsed.tree);
  const byName = new Map(options.map((option) => [option.name, option]));
  for (const key of Object.keys(parsed.tree)) {
    if (!byName.has(key)) {
      byName.set(key, {
        name: key,
        schema: {},
        required: false,
        branch: null,
        variants: null,
      });
    }
  }

  const basics: StrategyOutlineNode[] = [];
  const sections: StrategyOutlineNode[] = [];
  for (const option of byName.values()) {
    const present = Object.prototype.hasOwnProperty.call(
      parsed.tree,
      option.name,
    );
    const value = parsed.tree[option.name];
    const pointer = `/${escapePointerSegment(option.name)}`;
    const node = buildNode(
      parsed,
      schema,
      pointer,
      option.name,
      value,
      present,
    );
    if (isRootCollection(schema, option, value)) sections.push(node);
    else basics.push(node);
  }

  const result = [...sections];
  if (basics.length > 0) {
    result.unshift({
      id: "@basics",
      pointer: "",
      label: "identity",
      kind: "basics",
      present: basics.some((node) => node.present),
      range: rangeAt(parsed, ""),
      children: basics,
      arrayIndex: null,
      semanticIdentity: null,
    });
  }
  return result;
};

export const findOutlineNode = (
  nodes: readonly StrategyOutlineNode[],
  pointer: string,
): StrategyOutlineNode | null => {
  let best: StrategyOutlineNode | null = null;
  const visit = (node: StrategyOutlineNode): void => {
    if (
      node.pointer === pointer ||
      (node.pointer !== "" && pointer.startsWith(`${node.pointer}/`))
    ) {
      if (best === null || node.pointer.length > best.pointer.length)
        best = node;
    }
    for (const child of node.children) visit(child);
  };
  for (const node of nodes) visit(node);
  if (best !== null) return best;
  return pointer === ""
    ? (nodes.find((node) => node.kind === "basics") ?? null)
    : null;
};

export const outlineAncestorIds = (
  nodes: readonly StrategyOutlineNode[],
  id: string,
): string[] => {
  const path: string[] = [];
  const visit = (node: StrategyOutlineNode): boolean => {
    if (node.id === id) return true;
    for (const child of node.children) {
      if (visit(child)) {
        path.unshift(node.id);
        return true;
      }
    }
    return false;
  };
  for (const node of nodes) if (visit(node)) break;
  return path;
};

/** Search projection only; source navigation still resolves through the parser-owned map. */
export const projectStrategyOutlineSymbols = (
  nodes: readonly StrategyOutlineNode[],
): StrategyOutlineSymbol[] => {
  const symbols: StrategyOutlineSymbol[] = [];
  const visit = (
    node: StrategyOutlineNode,
    parents: readonly string[],
  ): void => {
    const path = [...parents, node.label];
    if (node.present && node.range !== null) {
      const semantic = node.semanticIdentity;
      symbols.push({
        id: node.id,
        pointer: node.pointer,
        label: path.join(" › "),
        description: node.pointer === "" ? "/" : node.pointer,
        keywords: semantic
          ? [
              semantic.namespace,
              semantic.value,
              `${semantic.namespace}:${semantic.value}`,
            ]
          : [],
      });
    }
    for (const child of node.children) visit(child, path);
  };
  for (const node of nodes) visit(node, []);
  return symbols;
};
