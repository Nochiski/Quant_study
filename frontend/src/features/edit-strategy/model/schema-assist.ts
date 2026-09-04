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
} from "../../../shared/lib/yaml12";
import type {
  EditorCompletionOption,
  EditorCompletionSource,
  EditorHover,
  EditorHoverSource,
} from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  formatContractValue,
  projectContractField,
} from "./contract-inspector";
import {
  definingArrayFor,
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

/** Ids supplied by the nearest schema-declared namespace array, excluding the current item. */
const referenceIds = (
  schema: JsonSchema,
  reference: string,
  pointer: string,
  tree: unknown,
): string[] => {
  const defining = definingArrayFor(schema, pointer, reference, tree);
  if (!defining) return [];
  const selfPrefix = `${defining.pointer}/`;
  const self = pointer.startsWith(selfPrefix)
    ? pointer.slice(selfPrefix.length).split("/")[0]
    : null;
  return defining.items
    .map((item, index) =>
      isRecord(item) && String(index) !== self
        ? item[`${reference}_id`]
        : undefined,
    )
    .filter((id): id is string => typeof id === "string");
};

const identifierOptions = (
  schema: JsonSchema,
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
    return referenceIds(schema, reference, pointer, tree).map((id) => ({
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
      deps.schema,
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

/** Hover lines for a pointer: the contract row when there is one, else the schema node. */
export const describePointer = (
  deps: Pick<AssistDeps, "schema" | "contract">,
  pointer: string,
  tree: unknown,
): string[] | null => {
  if (deps.schema === null) return null;
  const field = projectContractField(deps.schema, deps.contract, pointer, tree);
  if (field === null) return null;
  const lines = [field.templatePointer];
  lines.push(
    `${t("assist.type")}: ${field.type}${field.nullable ? " | null" : ""}`,
  );
  if (field.required !== null)
    lines.push(field.required ? t("assist.required") : t("assist.optional"));
  if (field.hasDefault)
    lines.push(
      `${t("assist.default")}: ${formatContractValue(field.defaultValue) ?? "—"}`,
    );
  if (field.unit) {
    lines.push(
      `${t("assist.unit")}: ${field.unit}${field.displayUnit ? ` (${t("assist.displayUnit")} ${field.displayUnit})` : ""}`,
    );
  }
  if (field.appliedStage)
    lines.push(`${t("assist.stage")}: ${field.appliedStage}`);
  if (field.hasExample)
    lines.push(
      `${t("assist.example")}: ${formatContractValue(field.example) ?? "—"}`,
    );
  if (field.catalog)
    lines.push(`${t("assist.source")}: ${catalogLabel(field.catalog)}`);
  if (field.reference)
    lines.push(`${t("assist.source")}: ${referenceLabel(field.reference)}`);
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
