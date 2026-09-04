/**
 * Canonical StrategySpec snippets projected from the backend runtime schema and coherent factor
 * catalog. This module owns no field list, enum, default, graph shape or factor direction: those
 * values are copied from backend-owned contracts. The five category names are UI placement from
 * WORKFLOW P4-05, not a second validation model.
 */
import { stringify } from "yaml";

import type { FactorDefinition } from "../../../shared/api";
import {
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
  value: unknown;
};

export type SnippetCatalogSource = {
  schema: JsonSchema | null;
  factors: readonly FactorDefinition[];
  status: "loading" | "ready" | "unavailable";
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

/** Minimal parseable value whose keys/defaults are taken from one runtime schema node. */
const materializeSchemaValue = (
  root: JsonSchema,
  schemaNode: JsonSchema,
): unknown => {
  const node = resolveRef(root, schemaNode);
  if (owns(node, "default")) return node.default;
  if (owns(node, "const")) return node.const;

  if (Array.isArray(node.anyOf)) {
    const members = node.anyOf.filter(isRecord);
    const nonNull = members.find((member) => member.type !== "null");
    return nonNull ? materializeSchemaValue(root, nonNull) : null;
  }
  if (Array.isArray(node.oneOf)) {
    const member = node.oneOf.find(isRecord);
    return member ? materializeSchemaValue(root, member) : {};
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
    if (
      !required.has(key) &&
      !owns(candidate, "default") &&
      !owns(property, "default")
    )
      continue;
    value[key] = materializeSchemaValue(root, candidate);
  }
  return value;
};

const rootProperty = (schema: JsonSchema, key: string): JsonSchema | null => {
  const properties = isRecord(schema.properties) ? schema.properties : null;
  const property = properties?.[key];
  return isRecord(property) ? property : null;
};

const factorSignalSchema = (schema: JsonSchema): JsonSchema | null => {
  const factorStep = rootProperty(schema, "factors");
  if (factorStep === null) return null;
  const step = resolveRef(schema, factorStep);
  const properties = isRecord(step.properties) ? step.properties : null;
  const factors = properties?.factors;
  if (!isRecord(factors)) return null;
  const array = resolveRef(schema, factors);
  return isRecord(array.items) ? array.items : null;
};

const factorValue = (
  schema: JsonSchema,
  definition: FactorDefinition,
): unknown => {
  const signalSchema = factorSignalSchema(schema);
  const base = signalSchema ? materializeSchemaValue(schema, signalSchema) : {};
  const materialized = isRecord(base) ? base : {};
  return {
    ...materialized,
    factor_id: definition.factor_id,
    label: definition.label,
    direction: definition.preference,
    // Weight is an authoring starter value, not registry metadata or a validation rule.
    weight: 1,
    graph: definition.default_graph,
  };
};

/** Builds the five-area catalog without copying StrategySpec keys or defaults into frontend code. */
export const buildCanonicalSnippetCatalog = (
  schema: JsonSchema | null,
  factors: readonly FactorDefinition[],
): CanonicalSnippet[] => {
  if (schema === null) return [];
  const snippets: CanonicalSnippet[] = [];
  for (const category of SNIPPET_CATEGORIES) {
    if (category === "factor") continue;
    const property = rootProperty(schema, category);
    if (property === null) continue;
    snippets.push({
      id: `section:${category}`,
      category,
      label: category,
      kind: "section",
      sectionKey: category,
      value: materializeSchemaValue(schema, property),
    });
  }

  const available = factors.filter(
    (factor) =>
      factor.availability === "implemented" && factor.default_graph !== null,
  );
  if (available.length > 0) {
    for (const factor of available) {
      snippets.push({
        id: `factor:${factor.factor_id}`,
        category: "factor",
        label: factor.label,
        kind: "factor",
        sectionKey: "factors",
        value: factorValue(schema, factor),
      });
    }
  } else {
    const signalSchema = factorSignalSchema(schema);
    if (signalSchema !== null) {
      snippets.push({
        id: "factor:generic",
        category: "factor",
        label: "factor",
        kind: "factor",
        sectionKey: "factors",
        value: materializeSchemaValue(schema, signalSchema),
      });
    }
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

  if (prefix !== "" && !snippet.sectionKey.startsWith(prefix))
    return { status: "error", reason: "cursor-context" };
  if (pointer === "/factors/factors" && prefix === "")
    return { status: "ok", fragment: yamlFragment([snippet.value]) };
  if (pointer === "/factors" && !siblings.includes("factors")) {
    return {
      status: "ok",
      fragment: yamlFragment({ factors: [snippet.value] }),
    };
  }
  if (pointer === "") {
    if (siblings.includes("factors"))
      return { status: "error", reason: "duplicate" };
    return {
      status: "ok",
      fragment: yamlFragment({ factors: { factors: [snippet.value] } }),
    };
  }
  return { status: "error", reason: "cursor-context" };
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
  if (selection.from !== selection.to)
    return { status: "error", reason: "selection" };
  const cursor = selection.from;
  const context = describeYamlCursor(source, cursor);
  if (context === null || context.mode !== "key")
    return { status: "error", reason: "cursor-context" };

  const lineStart = source.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
  const foundLineEnd = source.indexOf("\n", cursor);
  const lineEnd = foundLineEnd < 0 ? source.length : foundLineEnd;
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
