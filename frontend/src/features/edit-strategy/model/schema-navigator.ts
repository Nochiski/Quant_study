/**
 * Navigation over the backend runtime schema (P1-05, JSON Schema 2020-12) for the editor
 * (WORKFLOW P3-03). Nothing here knows a property name, an enum value or a catalog: it only
 * follows `properties`, `items`, `$ref`, nullable `anyOf` and `oneOf` discriminated by the
 * sibling `kind` value in the document, so a `kind` change re-selects the allowed fields.
 */

import {
  decodePointerSegment,
  pointerSegments,
} from "../../../shared/lib/yaml12";

export type JsonSchema = Record<string, unknown>;

export type ResolvedSchema = {
  node: JsonSchema;
  nullable: boolean;
  /** Set when the node is a discriminated union the document does not resolve (no `kind`). */
  branches: { kind: string; node: JsonSchema }[] | null;
};

export type PropertyOption = {
  name: string;
  schema: JsonSchema;
  required: boolean;
  /** Union member this property belongs to when the union is unresolved. */
  branch: string | null;
};

export type DiscriminatorInfo = {
  propertyName: string;
  variants: string[];
  selected: string | null;
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const resolveRef = (root: JsonSchema, node: JsonSchema): JsonSchema => {
  let current = node;
  for (let hops = 0; hops < 16 && typeof current.$ref === "string"; hops += 1) {
    const ref = current.$ref;
    if (!ref.startsWith("#/")) return current;
    let target: unknown = root;
    for (const segment of ref.slice(2).split("/")) {
      if (!isObject(target)) return current;
      target = target[decodePointerSegment(segment)];
    }
    if (!isObject(target)) return current;
    current = target;
  }
  return current;
};

const unwrapNullable = (
  root: JsonSchema,
  node: JsonSchema,
): { node: JsonSchema; nullable: boolean } => {
  const resolved = resolveRef(root, node);
  if (Array.isArray(resolved.anyOf)) {
    const members = resolved.anyOf.filter(isObject);
    const nonNull = members.filter((m) => m.type !== "null");
    if (nonNull.length === 1 && members.length === 2) {
      // Markers and defaults sit on the outer property schema; keep them with the inner node.
      const outer = Object.fromEntries(
        Object.entries(resolved).filter(
          ([key]) =>
            key.startsWith("x-") || key === "default" || key === "examples",
        ),
      );
      return {
        node: { ...resolveRef(root, nonNull[0]), ...outer },
        nullable: true,
      };
    }
  }
  return { node: resolved, nullable: false };
};

const kindOf = (root: JsonSchema, branch: JsonSchema): string | null => {
  const node = resolveRef(root, branch);
  const properties = isObject(node.properties) ? node.properties : {};
  const kind = isObject(properties.kind) ? properties.kind : null;
  return kind && typeof kind.const === "string" ? kind.const : null;
};

/** Selects the union branch matching the document value's `kind`, or lists all branches. */
const resolveUnion = (
  root: JsonSchema,
  node: JsonSchema,
  value: unknown,
): ResolvedSchema => {
  const base = unwrapNullable(root, node);
  const members = Array.isArray(base.node.oneOf)
    ? base.node.oneOf.filter(isObject)
    : null;
  if (!members) return { ...base, branches: null };
  const branches = members.map((member) => ({
    kind: kindOf(root, member) ?? "",
    node: resolveRef(root, member),
  }));
  const kind =
    isObject(value) && typeof value.kind === "string" ? value.kind : null;
  const chosen =
    kind === null ? undefined : branches.find((b) => b.kind === kind);
  if (chosen)
    return { node: chosen.node, nullable: base.nullable, branches: null };
  return { node: base.node, nullable: base.nullable, branches };
};

/**
 * Schema at a JSON Pointer, using the document tree (when available) to resolve unions on the
 * way down. Returns null when the pointer leaves the schema (unknown key, scalar with children).
 */
const walk = (
  root: JsonSchema,
  pointer: string,
  tree: unknown,
): { node: JsonSchema; value: unknown } | null => {
  const segments = pointerSegments(pointer);
  let current: JsonSchema = root;
  let value: unknown = tree;
  for (const segment of segments) {
    const here = resolveUnion(root, current, value);
    const node = here.node;
    if (node.type === "array" && isObject(node.items)) {
      if (!/^\d+$/.test(segment)) return null;
      current = node.items;
      value = Array.isArray(value) ? value[Number(segment)] : undefined;
      continue;
    }
    const properties = collectProperties(root, here);
    const property = properties.find((p) => p.name === segment);
    if (!property) return null;
    current = property.schema;
    value = isObject(value) ? value[segment] : undefined;
  }
  return { node: current, value };
};

export const schemaAt = (
  root: JsonSchema,
  pointer: string,
  tree: unknown,
): ResolvedSchema | null => {
  const end = walk(root, pointer, tree);
  return end ? resolveUnion(root, end.node, end.value) : null;
};

/** Every `kind` a union at `pointer` accepts, ignoring the kind the document currently holds. */
export const unionKindsAt = (
  root: JsonSchema,
  pointer: string,
  tree: unknown,
): string[] => {
  const end = walk(root, pointer, tree);
  const resolved = end ? resolveUnion(root, end.node, undefined) : null;
  return resolved?.branches
    ? resolved.branches.map((b) => b.kind).filter((k) => k !== "")
    : [];
};

/** Discriminator contract at a path, including the active document branch when present. */
export const discriminatorAt = (
  root: JsonSchema,
  pointer: string,
  tree: unknown,
): DiscriminatorInfo | null => {
  const end = walk(root, pointer, tree);
  if (end === null) return null;
  const base = unwrapNullable(root, end.node);
  if (!Array.isArray(base.node.oneOf)) return null;
  const discriminator = isObject(base.node.discriminator)
    ? base.node.discriminator
    : null;
  if (!discriminator || typeof discriminator.propertyName !== "string")
    return null;
  const propertyName = discriminator.propertyName;
  const variants = base.node.oneOf
    .filter(isObject)
    .map((member) => {
      const branch = resolveRef(root, member);
      const properties = isObject(branch.properties) ? branch.properties : {};
      const discriminatorProperty = isObject(properties[propertyName])
        ? properties[propertyName]
        : null;
      return discriminatorProperty?.const;
    })
    .filter((value): value is string => typeof value === "string");
  if (variants.length === 0) return null;
  const selected =
    isObject(end.value) && typeof end.value[propertyName] === "string"
      ? end.value[propertyName]
      : null;
  return { propertyName, variants, selected };
};

const collectProperties = (
  root: JsonSchema,
  resolved: ResolvedSchema,
): PropertyOption[] => {
  const fromNode = (node: JsonSchema, branch: string | null) => {
    const properties = isObject(node.properties) ? node.properties : {};
    const required = Array.isArray(node.required) ? node.required : [];
    return Object.entries(properties)
      .filter((entry): entry is [string, JsonSchema] => isObject(entry[1]))
      .map(([name, schema]) => ({
        name,
        schema,
        required: required.includes(name),
        branch,
      }));
  };
  if (resolved.branches === null) return fromNode(resolved.node, null);
  const seen = new Map<string, PropertyOption & { kinds: string[] }>();
  for (const branch of resolved.branches) {
    for (const option of fromNode(resolveRef(root, branch.node), branch.kind)) {
      const existing = seen.get(option.name);
      if (existing) existing.kinds.push(branch.kind);
      else seen.set(option.name, { ...option, kinds: [branch.kind] });
    }
  }
  // A property every branch shares (node_id, the `kind` discriminator) is not branch-specific.
  const total = resolved.branches.length;
  return [...seen.values()].map(({ kinds, ...option }) => ({
    ...option,
    branch: kinds.length === total ? null : kinds.join("|"),
  }));
};

/** Properties a mapping at this schema node may contain (union → every branch, labelled). */
export const propertyOptions = (
  root: JsonSchema,
  resolved: ResolvedSchema,
): PropertyOption[] => collectProperties(root, resolved);

/** Scalar values the schema itself enumerates: enum, const, booleans, union kinds. */
export const valueOptions = (resolved: ResolvedSchema): string[] => {
  const node = resolved.node;
  if (Array.isArray(node.enum)) return node.enum.map(String);
  if (node.const !== undefined) return [String(node.const)];
  if (node.type === "boolean") return ["true", "false"];
  if (resolved.branches) {
    return resolved.branches.map((b) => b.kind).filter((k) => k !== "");
  }
  return [];
};

/** The nearest ancestor array whose schema declares `x-defines: namespace`. */
export const definingArrayFor = (
  root: JsonSchema,
  pointer: string,
  namespace: string,
  tree: unknown,
): { pointer: string; items: unknown[] } | null => {
  const segments = pointer === "" ? [] : pointer.slice(1).split("/");
  for (let depth = segments.length - 1; depth >= 0; depth -= 1) {
    const ancestor =
      depth === 0 ? "" : `/${segments.slice(0, depth).join("/")}`;
    const resolved = schemaAt(root, ancestor, tree);
    if (!resolved) continue;
    for (const option of collectProperties(root, resolved)) {
      if (option.schema["x-defines"] !== namespace) continue;
      const arrayPointer = `${ancestor}/${option.name}`;
      let value: unknown = tree;
      for (const segment of pointerSegments(arrayPointer)) {
        if (Array.isArray(value)) value = value[Number(segment)];
        else if (isObject(value)) value = value[segment];
        else {
          value = undefined;
          break;
        }
      }
      return {
        pointer: arrayPointer,
        items: Array.isArray(value) ? value : [],
      };
    }
  }
  return null;
};

/** Short type label for completion details: `string`, `number ≥0 ≤1`, `enum(a|b)`, `object`. */
export const typeLabel = (node: JsonSchema): string => {
  if (Array.isArray(node.enum))
    return `enum(${node.enum.map(String).join("|")})`;
  if (node.const !== undefined) return `const ${String(node.const)}`;
  if (Array.isArray(node.oneOf)) return "object(kind)";
  const type = Array.isArray(node.type)
    ? node.type.join("|")
    : typeof node.type === "string"
      ? node.type
      : "object";
  const bounds: string[] = [];
  if (typeof node.minimum === "number") bounds.push(`≥${node.minimum}`);
  if (typeof node.exclusiveMinimum === "number")
    bounds.push(`>${node.exclusiveMinimum}`);
  if (typeof node.maximum === "number") bounds.push(`≤${node.maximum}`);
  if (typeof node.exclusiveMaximum === "number")
    bounds.push(`<${node.exclusiveMaximum}`);
  return bounds.length ? `${type} ${bounds.join(" ")}` : type;
};
