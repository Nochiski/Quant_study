/**
 * Completion and hover for the source editor, driven by the backend runtime schema and
 * contract (WORKFLOW P3-03, editor ADR D2). Keys and enumerated values come from the schema;
 * identifier values come from the catalog or document namespace the schema names through
 * `x-catalog` / `x-reference`. No allowed-value list lives in the frontend, and nothing here
 * marks errors: required/type/unknown-key diagnostics are the backend's (P3-04).
 */
import type {
  DatasetFieldProfile,
  FactorDefinition,
  FieldContract,
} from "../../../shared/api";
import { t } from "../../../shared/config";
import {
  describeYamlCursor,
  loadYaml12Mapping,
  templatePointer,
} from "../../../shared/lib/yaml12";
import type {
  EditorCompletionOption,
  EditorCompletionSource,
  EditorHover,
  EditorHoverSource,
} from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  propertyOptions,
  schemaAt,
  typeLabel,
  unionKindsAt,
  valueOptions,
  type JsonSchema,
  type ResolvedSchema,
} from "./schema-navigator";

export type AssistCatalogs = {
  equityFields: readonly DatasetFieldProfile[];
  factors: readonly FactorDefinition[];
};

export type AssistDeps = {
  schema: JsonSchema | null;
  contract: readonly FieldContract[];
  catalogs: AssistCatalogs;
  /** Latest document state (a ref read at call time, never a stale closure). */
  getState: () => DocumentState;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The document tree for the text being completed: a fresh parse, else the last good one. */
const treeFor = (text: string, state: DocumentState): unknown => {
  try {
    return loadYaml12Mapping(text);
  } catch {
    return state.parse?.status === "ok" ? state.parse.tree : null;
  }
};

const valueAt = (tree: unknown, pointer: string): unknown => {
  let current = tree;
  for (const raw of pointer === "" ? [] : pointer.slice(1).split("/")) {
    const segment = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (Array.isArray(current)) current = current[Number(segment)];
    else if (isRecord(current)) current = current[segment];
    else return undefined;
  }
  return current;
};

const catalogLabel = (catalog: string): string => {
  switch (catalog) {
    case "equity-field":
      return t("assist.catalog.equityField");
    case "factor":
      return t("assist.catalog.factor");
    case "universe":
      return t("assist.catalog.universe");
    case "subgraph":
      return t("assist.catalog.subgraph");
    default:
      return catalog;
  }
};

const referenceLabel = (reference: string): string => {
  switch (reference) {
    case "node":
      return t("assist.reference.node");
    case "parameter":
      return t("assist.reference.parameter");
    default:
      return reference;
  }
};

/** Ids the document itself defines for a reference namespace, seen from `pointer`. */
const referenceIds = (
  reference: string,
  pointer: string,
  tree: unknown,
): string[] => {
  if (reference === "parameter") {
    const parameters = valueAt(tree, "/parameters");
    return Array.isArray(parameters)
      ? parameters
          .map((p) => (isRecord(p) ? p.parameter_id : undefined))
          .filter((id): id is string => typeof id === "string")
      : [];
  }
  if (reference === "node") {
    const nodesIndex = pointer.lastIndexOf("/nodes/");
    if (nodesIndex < 0) return [];
    const nodes = valueAt(tree, pointer.slice(0, nodesIndex + "/nodes".length));
    const self = pointer.slice(nodesIndex + "/nodes/".length).split("/")[0];
    return Array.isArray(nodes)
      ? nodes
          .map((node, index) =>
            isRecord(node) && String(index) !== self ? node.node_id : undefined,
          )
          .filter((id): id is string => typeof id === "string")
      : [];
  }
  return [];
};

const identifierOptions = (
  resolved: ResolvedSchema,
  pointer: string,
  tree: unknown,
  catalogs: AssistCatalogs,
): EditorCompletionOption[] | null => {
  const catalog = resolved.node["x-catalog"];
  if (typeof catalog === "string") {
    if (catalog === "equity-field") {
      return catalogs.equityFields.map((field) => ({
        label: field.field_id,
        detail: field.label,
        info: `${field.description} (${field.unit}, ${field.frequency})`,
        type: "value",
      }));
    }
    if (catalog === "factor") {
      return catalogs.factors.map((factor) => ({
        label: factor.factor_id,
        detail: factor.label,
        info: factor.description,
        type: "value",
      }));
    }
    return []; // universe / subgraph: no catalog endpoint yet, and never a guessed list
  }
  const reference = resolved.node["x-reference"];
  if (typeof reference === "string") {
    return referenceIds(reference, pointer, tree).map((id) => ({
      label: id,
      detail: referenceLabel(reference),
      type: "value",
    }));
  }
  return null;
};

export const buildCompletionSource =
  (deps: AssistDeps): EditorCompletionSource =>
  ({ text, offset }) => {
    const state = deps.getState();
    // Nothing during IME composition (editor ADR D2), nothing without the schema, YAML only.
    if (state.composing || deps.schema === null || state.format !== "yaml")
      return null;
    const cursor = describeYamlCursor(text, offset);
    if (!cursor) return null;
    const tree = treeFor(text, state);
    const resolved = schemaAt(deps.schema, cursor.pointer, tree);
    if (!resolved) return null;

    if (cursor.mode === "key") {
      const options = propertyOptions(deps.schema, resolved)
        .filter((option) => !cursor.siblings.includes(option.name))
        .map<EditorCompletionOption>((option) => ({
          label: option.name,
          detail: [
            typeLabel(option.schema),
            option.required ? t("assist.required") : null,
            option.branch ? `${t("assist.branch")}=${option.branch}` : null,
          ]
            .filter(Boolean)
            .join(" · "),
          type: "property",
          apply:
            option.schema.type === "object" ||
            option.schema.type === "array" ||
            option.schema.$ref !== undefined ||
            option.schema.oneOf !== undefined
              ? `${option.name}:`
              : `${option.name}: `,
        }));
      return options.length ? { from: cursor.from, options } : null;
    }

    if (cursor.key === "kind") {
      const owner = cursor.pointer.slice(0, cursor.pointer.lastIndexOf("/"));
      const kinds = unionKindsAt(deps.schema, owner, tree);
      if (kinds.length) {
        return {
          from: cursor.from,
          options: kinds.map((kind) => ({ label: kind, type: "enum" })),
        };
      }
    }
    const identifiers = identifierOptions(
      resolved,
      cursor.pointer,
      tree,
      deps.catalogs,
    );
    if (identifiers !== null) {
      return identifiers.length
        ? { from: cursor.from, options: identifiers }
        : null;
    }
    const values = valueOptions(resolved).map<EditorCompletionOption>(
      (value) => ({ label: value, type: "enum" }),
    );
    return values.length ? { from: cursor.from, options: values } : null;
  };

const contractFor = (
  contract: readonly FieldContract[],
  pointer: string,
  kind: string | null,
): FieldContract | undefined => {
  const template = templatePointer(pointer);
  const rows = contract.filter((row) => row.pointer === template);
  return (
    rows.find((row) => row.branch === kind) ??
    rows.find((row) => !row.branch) ??
    rows[0]
  );
};

const formatValue = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value);

/** Hover lines for a pointer: the contract row when there is one, else the schema node. */
export const describePointer = (
  deps: Pick<AssistDeps, "schema" | "contract">,
  pointer: string,
  tree: unknown,
): string[] | null => {
  if (deps.schema === null) return null;
  const resolved = schemaAt(deps.schema, pointer, tree);
  if (!resolved) return null;
  const parentValue = valueAt(tree, pointer.slice(0, pointer.lastIndexOf("/")));
  const kind =
    isRecord(parentValue) && typeof parentValue.kind === "string"
      ? parentValue.kind
      : null;
  const row = contractFor(deps.contract, pointer, kind);
  const node = resolved.node;
  const lines = [templatePointer(pointer)];
  lines.push(
    `${t("assist.type")}: ${typeLabel(node)}${resolved.nullable ? " | null" : ""}`,
  );
  lines.push(
    (row?.required ?? false) ? t("assist.required") : t("assist.optional"),
  );
  if (row?.has_default)
    lines.push(`${t("assist.default")}: ${formatValue(row.default)}`);
  if (row?.unit) {
    lines.push(
      `${t("assist.unit")}: ${row.unit}${row.display_unit ? ` (${t("assist.displayUnit")} ${row.display_unit})` : ""}`,
    );
  }
  if (row?.applied_stage)
    lines.push(`${t("assist.stage")}: ${row.applied_stage}`);
  if (row?.example !== undefined && row.example !== null)
    lines.push(`${t("assist.example")}: ${formatValue(row.example)}`);
  const catalog = node["x-catalog"];
  if (typeof catalog === "string")
    lines.push(`${t("assist.source")}: ${catalogLabel(catalog)}`);
  const reference = node["x-reference"];
  if (typeof reference === "string")
    lines.push(`${t("assist.source")}: ${referenceLabel(reference)}`);
  return lines;
};

export const buildHoverSource =
  (deps: AssistDeps): EditorHoverSource =>
  (offset): EditorHover | null => {
    const state = deps.getState();
    if (
      state.parse?.status !== "ok" ||
      state.parsedVersion !== state.sourceVersion
    )
      return null;
    let best: { pointer: string; from: number; to: number } | null = null;
    const consider = (
      pointer: string,
      range: { start: { offset: number }; end: { offset: number } },
    ) => {
      if (offset < range.start.offset || offset > range.end.offset) return;
      const width = range.end.offset - range.start.offset;
      if (best === null || width < best.to - best.from) {
        best = { pointer, from: range.start.offset, to: range.end.offset };
      }
    };
    for (const [pointer, range] of state.parse.keyRanges)
      consider(pointer, range);
    for (const [pointer, range] of state.parse.valueRanges) {
      if (pointer !== "") consider(pointer, range);
    }
    if (best === null) return null;
    const hit: { pointer: string; from: number; to: number } = best;
    const lines = describePointer(deps, hit.pointer, state.parse.tree);
    return lines ? { from: hit.from, to: hit.to, lines } : null;
  };
