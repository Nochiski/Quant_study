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
  /** Set when this property has no single safe schema until a union branch is selected. */
  propertyVariants: PropertyVariant[] | null;
  /** Requiredness shared by every applicable backend schema branch, else unknown. */
  propertyRequired: boolean | null;
};

export type PropertyVariant = {
  branch: string;
  schema: JsonSchema;
  required: boolean;
};

export type PropertyOption = {
  name: string;
  schema: JsonSchema;
  required: boolean | null;
  /** Union member this property belongs to when the union is unresolved. */
  branch: string | null;
  /** Branch contracts when availability, requiredness or schema differs. */
  variants: PropertyVariant[] | null;
};

export type DiscriminatorInfo = {
  propertyName: string;
  variants: string[];
  selected: string | null;
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * Resolves backend-owned, document-local references. Invalid, external, oversized, and cyclic
 * chains are deliberately unavailable: editor assistance must never invent a contract when the
 * runtime schema cannot be followed unambiguously.
 */
export const resolveRef = (
  root: JsonSchema,
  node: JsonSchema,
): JsonSchema | null => {
  let current = node;
  const visited = new Set<JsonSchema>();
  for (let hops = 0; typeof current.$ref === "string"; hops += 1) {
    if (hops >= 16 || visited.has(current)) return null;
    visited.add(current);
    const ref = current.$ref;
    if (!ref.startsWith("#/")) return null;
    let target: unknown = root;
    for (const segment of ref.slice(2).split("/")) {
      if (!isObject(target)) return null;
      target = target[decodePointerSegment(segment)];
    }
    if (!isObject(target)) return null;
    current = target;
  }
  return current;
};

const unwrapNullable = (
  root: JsonSchema,
  node: JsonSchema,
): { node: JsonSchema; nullable: boolean } | null => {
  const resolved = resolveRef(root, node);
  if (resolved === null) return null;
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
      const inner = resolveRef(root, nonNull[0]);
      if (inner === null) return null;
      return { node: { ...inner, ...outer }, nullable: true };
    }
  }
  return { node: resolved, nullable: false };
};

const kindOf = (root: JsonSchema, branch: JsonSchema): string | null => {
  const node = resolveRef(root, branch);
  if (node === null) return null;
  const properties = isObject(node.properties) ? node.properties : {};
  const kind = isObject(properties.kind) ? properties.kind : null;
  return kind && typeof kind.const === "string" ? kind.const : null;
};

/** Selects the union branch matching the document value's `kind`, or lists all branches. */
const resolveUnion = (
  root: JsonSchema,
  node: JsonSchema,
  value: unknown,
): ResolvedSchema | null => {
  const base = unwrapNullable(root, node);
  if (base === null) return null;
  const members = Array.isArray(base.node.oneOf)
    ? base.node.oneOf.filter(isObject)
    : null;
  if (!members)
    return {
      ...base,
      branches: null,
      propertyVariants: null,
      propertyRequired: null,
    };
  const branches: { kind: string; node: JsonSchema }[] = [];
  for (const member of members) {
    const branch = resolveRef(root, member);
    if (branch === null) return null;
    branches.push({ kind: kindOf(root, branch) ?? "", node: branch });
  }
  const kind =
    isObject(value) && typeof value.kind === "string" ? value.kind : null;
  const chosen =
    kind === null ? undefined : branches.find((b) => b.kind === kind);
  if (chosen)
    return {
      node: chosen.node,
      nullable: base.nullable,
      branches: null,
      propertyVariants: null,
      propertyRequired: null,
    };
  return {
    node: base.node,
    nullable: base.nullable,
    branches,
    propertyVariants: null,
    propertyRequired: null,
  };
};

/**
 * Schema at a JSON Pointer, using the document tree (when available) to resolve unions on the
 * way down. Returns null when the pointer leaves the schema (unknown key, scalar with children).
 */
const walk = (
  root: JsonSchema,
  pointer: string,
  tree: unknown,
): {
  node: JsonSchema;
  value: unknown;
  propertyVariants: PropertyVariant[] | null;
  propertyRequired: boolean | null;
} | null => {
  const segments = pointerSegments(pointer);
  let current: JsonSchema = root;
  let value: unknown = tree;
  let propertyRequired: boolean | null = null;
  for (const [index, segment] of segments.entries()) {
    const here = resolveUnion(root, current, value);
    if (here === null) return null;
    const node = here.node;
    if (node.type === "array" && isObject(node.items)) {
      if (!/^\d+$/.test(segment)) return null;
      current = node.items;
      value = Array.isArray(value) ? value[Number(segment)] : undefined;
      propertyRequired = null;
      continue;
    }
    const properties = collectProperties(root, here);
    const property = properties.find((p) => p.name === segment);
    if (!property) return null;
    const childValue = isObject(value) ? value[segment] : undefined;
    if (property.variants !== null) {
      // Traversing deeper would arbitrarily choose a branch-specific shape. The exact property
      // itself remains selectable so consumers can explain why `kind` must be chosen first.
      if (index !== segments.length - 1) return null;
      return {
        node: property.schema,
        value: childValue,
        propertyVariants: property.variants,
        propertyRequired: property.required,
      };
    }
    current = property.schema;
    value = childValue;
    propertyRequired = property.required;
  }
  return { node: current, value, propertyVariants: null, propertyRequired };
};

export const schemaAt = (
  root: JsonSchema,
  pointer: string,
  tree: unknown,
): ResolvedSchema | null => {
  const end = walk(root, pointer, tree);
  if (end === null) return null;
  if (end.propertyVariants !== null)
    return {
      node: end.node,
      nullable: false,
      branches: null,
      propertyVariants: end.propertyVariants,
      propertyRequired: end.propertyRequired,
    };
  const resolved = resolveUnion(root, end.node, end.value);
  if (resolved === null) return null;
  return {
    ...resolved,
    propertyRequired: end.propertyRequired,
  };
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
  if (base === null) return null;
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
      if (branch === null) return null;
      const properties = isObject(branch.properties) ? branch.properties : {};
      const discriminatorProperty = isObject(properties[propertyName])
        ? properties[propertyName]
        : null;
      return discriminatorProperty?.const;
    })
    .filter((value): value is string => typeof value === "string");
  if (variants.length === 0) return null;
  const documentKind =
    isObject(end.value) && typeof end.value[propertyName] === "string"
      ? end.value[propertyName]
      : null;
  // An unknown value does not select a branch. The raw document value is still available
  // through the field projection, while union metadata remains explicitly unresolved.
  const selected =
    documentKind !== null && variants.includes(documentKind)
      ? documentKind
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
      .flatMap(([name, schema]) => {
        const resolvedSchema = resolveRef(root, schema);
        return resolvedSchema === null
          ? []
          : [
              {
                name,
                schema,
                required: required.includes(name),
                branch,
                variants: null,
              },
            ];
      });
  };
  if (resolved.branches === null) return fromNode(resolved.node, null);
  const seen = new Map<
    string,
    {
      name: string;
      variants: PropertyVariant[];
    }
  >();
  for (const branch of resolved.branches) {
    for (const option of fromNode(branch.node, branch.kind)) {
      const existing = seen.get(option.name);
      const variant = {
        branch: branch.kind,
        schema: option.schema,
        required: option.required,
      };
      if (existing) existing.variants.push(variant);
      else seen.set(option.name, { name: option.name, variants: [variant] });
    }
  }
  const sameSchema = (left: JsonSchema, right: JsonSchema): boolean => {
    const resolvedLeft = resolveRef(root, left);
    const resolvedRight = resolveRef(root, right);
    return (
      resolvedLeft !== null &&
      resolvedRight !== null &&
      JSON.stringify(resolvedLeft) === JSON.stringify(resolvedRight)
    );
  };
  // A property is branch-independent only when every branch declares the same schema and
  // requiredness. Keeping the first schema for any other case would fabricate a contract.
  const total = resolved.branches.length;
  return [...seen.values()].map(({ name, variants }) => {
    const first = variants[0];
    const commonRequired =
      variants.length === total &&
      variants.every((variant) => variant.required === first.required)
        ? first.required
        : null;
    const shared =
      commonRequired !== null &&
      variants.every((variant) => sameSchema(variant.schema, first.schema));
    return {
      name,
      schema: first.schema,
      required: commonRequired,
      branch:
        variants.length === total
          ? null
          : variants.map((variant) => variant.branch).join("|"),
      variants: shared ? null : variants,
    };
  });
};

/** Properties a mapping at this schema node may contain (union → every branch, labelled). */
export const propertyOptions = (
  root: JsonSchema,
  resolved: ResolvedSchema,
): PropertyOption[] => collectProperties(root, resolved);

/** Scalar values the schema itself enumerates: enum, const, booleans, union kinds. */
export const valueOptions = (resolved: ResolvedSchema): string[] => {
  if (resolved.propertyVariants !== null) return [];
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
