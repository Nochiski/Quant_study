/**
 * Canonical StrategySpec snippets projected from the backend runtime schema and coherent factor
 * catalog. This module owns no field list, enum, default, graph shape or factor direction: those
 * values are copied from backend-owned contracts. The five category names are UI placement from
 * WORKFLOW P4-05, not a second validation model.
 */
import { stringify } from "yaml";

import type { FactorDefinition } from "../../../shared/api";
import {
  escapePointerSegment,
  describeYamlCursor,
  parseSource,
  type SourceFormat,
} from "../../../shared/lib/yaml12";
import { resolveRef, type JsonSchema } from "./schema-navigator";

export const SNIPPET_CATEGORIES = [
  "data",
  "factor",
  "signal",
  "risk",
  "execution",
] as const;

export type SnippetCategory = (typeof SNIPPET_CATEGORIES)[number];

export type CanonicalSnippet = {
  id: string;
  category: SnippetCategory;
  /** Backend field name or factor label. */
  label: string;
  /** Section snippets add a root key; factor snippets can also add an item to factors.factors. */
  kind: "section" | "factor";
  sectionKey: string;
  collectionKey: string | null;
  identity: { field: string; value: unknown } | null;
  value: unknown;
};

export type SnippetCatalogSource = {
  schema: JsonSchema | null;
  factors: readonly FactorDefinition[];
  status: "loading" | "ready" | "unavailable" | "incompatible";
};

export type SnippetEdit = {
  from: number;
  to: number;
  insert: string;
  selection: { from: number };
  nextSource: string;
};

export type SnippetEditFailure =
  "yaml-only" | "selection" | "cursor-context" | "duplicate" | "parse";

export type SnippetEditResult =
  | { status: "ok"; edit: SnippetEdit }
  | { status: "error"; reason: SnippetEditFailure };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const owns = (value: object, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);

const scalarFallback = (node: JsonSchema): unknown => {
  if (owns(node, "const")) return node.const;
  if (Array.isArray(node.enum) && node.enum.length > 0) return node.enum[0];
  if (node.type === "boolean") return false;
  if (node.type === "integer" || node.type === "number") {
    if (typeof node.minimum === "number") return node.minimum;
    if (typeof node.exclusiveMinimum === "number")
      return node.type === "integer"
        ? Math.floor(node.exclusiveMinimum) + 1
        : node.exclusiveMinimum + Number.EPSILON;
    return 0;
  }
  return "";
};

class UnsupportedSnippetSchema extends Error {}

type MaterializeState = {
  ancestors: Set<JsonSchema>;
  budget: { remaining: number };
};

/** Minimal parseable value whose keys/defaults are taken from one runtime schema node. */
const materializeSchemaValue = (
  root: JsonSchema,
  schemaNode: JsonSchema,
  state: MaterializeState = {
    ancestors: new Set(),
    budget: { remaining: 256 },
  },
): unknown => {
  const node = resolveRef(root, schemaNode);
  if (node === null)
    throw new UnsupportedSnippetSchema("unresolvable schema reference");
  state.budget.remaining -= 1;
  if (state.budget.remaining < 0 || state.ancestors.has(node))
    throw new UnsupportedSnippetSchema("recursive or oversized schema");
  state.ancestors.add(node);
  try {
    if (owns(node, "default")) return node.default;
    if (owns(node, "const")) return node.const;
    if (Array.isArray(node.anyOf)) {
      const member = node.anyOf
        .filter(isRecord)
        .find((item) => item.type !== "null");
      return member ? materializeSchemaValue(root, member, state) : null;
    }
    if (Array.isArray(node.oneOf)) {
      const member = node.oneOf.find(isRecord);
      return member ? materializeSchemaValue(root, member, state) : {};
    }
    if (node.type === "array") return [];
    if (node.type !== "object" && !isRecord(node.properties))
      return scalarFallback(node);

    const properties = isRecord(node.properties) ? node.properties : {};
    const required = new Set(
      Array.isArray(node.required)
        ? node.required.filter((key): key is string => typeof key === "string")
        : [],
    );
    const value: Record<string, unknown> = {};
    for (const [key, candidate] of Object.entries(properties)) {
      if (!isRecord(candidate)) continue;
      const property = resolveRef(root, candidate);
      if (property === null)
        throw new UnsupportedSnippetSchema("unresolvable property reference");
      if (
        !required.has(key) &&
        !owns(candidate, "default") &&
        !owns(property, "default")
      )
        continue;
      value[key] = materializeSchemaValue(root, candidate, state);
    }
    return value;
  } finally {
    state.ancestors.delete(node);
  }
};

const rootProperty = (schema: JsonSchema, key: string): JsonSchema | null => {
  const properties = isRecord(schema.properties) ? schema.properties : null;
  const property = properties?.[key];
  return isRecord(property) ? property : null;
};

type FactorAuthoringContract = {
  sectionKey: string;
  collectionKey: string;
  item: JsonSchema;
};

const factorAuthoringContract = (
  schema: JsonSchema,
): FactorAuthoringContract | null => {
  const root = isRecord(schema.properties) ? schema.properties : {};
  for (const [sectionKey, candidate] of Object.entries(root)) {
    if (!isRecord(candidate)) continue;
    const section = resolveRef(schema, candidate);
    if (section === null) continue;
    const properties = isRecord(section.properties) ? section.properties : {};
    for (const [collectionKey, collectionCandidate] of Object.entries(
      properties,
    )) {
      if (!isRecord(collectionCandidate)) continue;
      const collection = resolveRef(schema, collectionCandidate);
      if (collection === null) continue;
      if (collection.type !== "array" || !isRecord(collection.items)) continue;
      const item = resolveRef(schema, collection.items);
      if (item === null) continue;
      const itemProperties = isRecord(item.properties) ? item.properties : {};
      const required = Array.isArray(item.required)
        ? item.required.filter((key): key is string => typeof key === "string")
        : [];
      const mapped = required.every((key) => {
        const property = itemProperties[key];
        return (
          isRecord(property) &&
          (typeof property["x-authoring-source"] === "string" ||
            owns(property, "x-authoring-default"))
        );
      });
      if (
        mapped &&
        Object.values(itemProperties).some(
          (property) =>
            isRecord(property) && property["x-authoring-identity"] === true,
        )
      )
        return { sectionKey, collectionKey, item };
    }
  }
  return null;
};

const factorValue = (
  contract: FactorAuthoringContract,
  definition: FactorDefinition,
): {
  value: Record<string, unknown>;
  identity: { field: string; value: unknown };
} | null => {
  const properties = isRecord(contract.item.properties)
    ? contract.item.properties
    : {};
  const catalog = definition as unknown as Record<string, unknown>;
  const value: Record<string, unknown> = {};
  let identity: { field: string; value: unknown } | null = null;
  for (const [field, property] of Object.entries(properties)) {
    if (!isRecord(property)) continue;
    const source = property["x-authoring-source"];
    const candidate =
      typeof source === "string" && owns(catalog, source)
        ? catalog[source]
        : owns(property, "x-authoring-default")
          ? property["x-authoring-default"]
          : undefined;
    if (candidate === undefined || candidate === null) return null;
    value[field] = candidate;
    if (property["x-authoring-identity"] === true) {
      if (identity !== null) return null;
      identity = { field, value: candidate };
    }
  }
  return identity === null ? null : { value, identity };
};

/** Builds the five-area catalog without copying StrategySpec keys or defaults into frontend code. */
export const buildCanonicalSnippetCatalog = (
  source: SnippetCatalogSource,
): CanonicalSnippet[] => {
  if (source.status !== "ready" || source.schema === null) return [];
  const { schema, factors } = source;
  const snippets: CanonicalSnippet[] = [];
  for (const category of SNIPPET_CATEGORIES) {
    if (category === "factor") continue;
    const property = rootProperty(schema, category);
    if (property === null) continue;
    try {
      snippets.push({
        id: `section:${category}`,
        category,
        label: category,
        kind: "section",
        sectionKey: category,
        collectionKey: null,
        identity: null,
        value: materializeSchemaValue(schema, property),
      });
    } catch (error) {
      if (!(error instanceof UnsupportedSnippetSchema)) throw error;
    }
  }

  const contract = factorAuthoringContract(schema);
  if (contract === null) return snippets;
  for (const factor of factors.filter(
    (candidate) => candidate.availability === "implemented",
  )) {
    const preset = factorValue(contract, factor);
    if (preset === null) continue;
    snippets.push({
      id: `factor:${String(preset.identity.value)}`,
      category: "factor",
      label: factor.label,
      kind: "factor",
      sectionKey: contract.sectionKey,
      collectionKey: contract.collectionKey,
      identity: preset.identity,
      value: preset.value,
    });
  }
  return snippets;
};

const yamlFragment = (value: unknown): string =>
  stringify(value, { lineWidth: 0 }).replace(/\n$/, "");

const indentFragment = (
  fragment: string,
  indent: string,
  eol: "\n" | "\r\n",
): string => fragment.split("\n").join(`${eol}${indent}`);

const fragmentFor = (
  snippet: CanonicalSnippet,
  pointer: string,
  siblings: readonly string[],
  prefix: string,
):
  | { status: "ok"; fragment: string }
  | { status: "error"; reason: SnippetEditFailure } => {
  if (snippet.kind === "section") {
    if (pointer !== "" || !snippet.sectionKey.startsWith(prefix))
      return { status: "error", reason: "cursor-context" };
    if (siblings.includes(snippet.sectionKey))
      return { status: "error", reason: "duplicate" };
    return {
      status: "ok",
      fragment: yamlFragment({ [snippet.sectionKey]: snippet.value }),
    };
  }

  if (snippet.collectionKey === null)
    return { status: "error", reason: "cursor-context" };
  const sectionPointer = `/${escapePointerSegment(snippet.sectionKey)}`;
  const collectionPointer = `${sectionPointer}/${escapePointerSegment(snippet.collectionKey)}`;
  if (prefix !== "" && !snippet.sectionKey.startsWith(prefix))
    return { status: "error", reason: "cursor-context" };
  if (pointer === collectionPointer && prefix === "")
    return { status: "ok", fragment: yamlFragment([snippet.value]) };
  if (pointer === sectionPointer && !siblings.includes(snippet.collectionKey)) {
    return {
      status: "ok",
      fragment: yamlFragment({ [snippet.collectionKey]: [snippet.value] }),
    };
  }
  if (pointer === "") {
    if (siblings.includes("factors"))
      return { status: "error", reason: "duplicate" };
    return {
      status: "ok",
      fragment: yamlFragment({
        [snippet.sectionKey]: { [snippet.collectionKey]: [snippet.value] },
      }),
    };
  }
  return { status: "error", reason: "cursor-context" };
};

const containsSnippetIdentity = (
  tree: Record<string, unknown>,
  snippet: CanonicalSnippet,
): boolean => {
  if (
    snippet.kind !== "factor" ||
    snippet.collectionKey === null ||
    snippet.identity === null
  )
    return false;
  const section = tree[snippet.sectionKey];
  const collection = isRecord(section) ? section[snippet.collectionKey] : null;
  return (
    Array.isArray(collection) &&
    collection.some(
      (item) =>
        isRecord(item) &&
        owns(item, snippet.identity!.field) &&
        Object.is(item[snippet.identity!.field], snippet.identity!.value),
    )
  );
};

/**
 * Plans one cursor-local insertion and preflights the complete next document with the shared
 * YAML 1.2 parser. The caller applies only the returned range edit, never a whole-source append.
 */
export const planSnippetEdit = (
  source: string,
  format: SourceFormat,
  selection: { from: number; to: number },
  snippet: CanonicalSnippet,
): SnippetEditResult => {
  if (format !== "yaml") return { status: "error", reason: "yaml-only" };
  if (
    !Number.isInteger(selection.from) ||
    !Number.isInteger(selection.to) ||
    selection.from < 0 ||
    selection.to < 0 ||
    selection.from > source.length ||
    selection.to > source.length ||
    selection.from !== selection.to
  )
    return { status: "error", reason: "selection" };
  const current = parseSource(source, "yaml");
  if (current.status === "ok" && containsSnippetIdentity(current.tree, snippet))
    return { status: "error", reason: "duplicate" };
  const cursor = selection.from;
  const context = describeYamlCursor(source, cursor);
  if (context === null || context.mode !== "key")
    return { status: "error", reason: "cursor-context" };

  const lineStart = source.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
  const foundLineEnd = source.indexOf("\n", cursor);
  const lineEnd =
    foundLineEnd < 0
      ? source.length
      : foundLineEnd > 0 && source[foundLineEnd - 1] === "\r"
        ? foundLineEnd - 1
        : foundLineEnd;
  if (source.slice(cursor, lineEnd).trim() !== "")
    return { status: "error", reason: "cursor-context" };
  const indent = source.slice(lineStart, context.from);
  if (!/^ *$/.test(indent))
    return { status: "error", reason: "cursor-context" };

  const planned = fragmentFor(
    snippet,
    context.pointer,
    context.siblings,
    context.prefix,
  );
  if (planned.status === "error") return planned;
  const eol = source.includes("\r\n") ? "\r\n" : "\n";
  const insert = indentFragment(planned.fragment, indent, eol);
  const nextSource = `${source.slice(0, context.from)}${insert}${source.slice(lineEnd)}`;
  if (parseSource(nextSource, "yaml").status !== "ok")
    return { status: "error", reason: "parse" };
  const selectionFrom = context.from + insert.length;
  return {
    status: "ok",
    edit: {
      from: context.from,
      to: lineEnd,
      insert,
      selection: { from: selectionFrom },
      nextSource,
    },
  };
};
