/**
 * Form projection(WORKFLOW P4-01, spec D6): runtime schema × parse tree × compile 진단 → 섹션/필드
 * 목록. 순수 함수이며 편집기·React를 모른다.
 *
 * 프론트에 필드 이름·enum·범위·기본값을 적지 않는다. 섹션은 `schema.properties` 순서, 필드는 각
 * 섹션 스키마의 `properties` 순서이고, 컨트롤·범위·단위·카탈로그는 `schemaFacts`(Contract Inspector와
 * 같은 해석)로만 정한다. `applicable`은 스키마 조건표를 문서(또는 발행 기본값)로 판정한 값이고, 경고
 * 배지의 근거는 따로 `diagnostics`에 담긴 backend 진단이다(P2-03 DEFECT-118-01: 두 사실은 다르다).
 */
import {
  escapePointerSegment,
  templatePointer,
  valueAtPointer,
  type ParsedSource,
} from "../../../shared/lib/yaml12";
import type { DocumentDiagnostic } from "./document-state";
import {
  projectApplicability,
  type DefaultResolver,
} from "./field-applicability";
import {
  schemaAt,
  schemaFacts,
  referenceCandidates,
  unionKindsAt,
  type Bound,
  type JsonSchema,
  type ResolvedSchema,
} from "./schema-navigator";

export type FormControl =
  | { kind: "text" }
  | { kind: "number"; min: Bound | null; max: Bound | null; integer: boolean }
  | { kind: "date" }
  | { kind: "boolean" }
  | { kind: "enum"; values: readonly string[] }
  | {
      kind: "catalog";
      catalog: "equity-field" | "universe" | "factor" | "subgraph";
    }
  | {
      kind: "reference";
      namespace: "node" | "parameter";
      candidates: readonly string[];
    }
  /** 팩터 항목의 `graph`: Form은 표시만 하고 편집은 Graph 화면에 넘긴다(spec D6). */
  | { kind: "graph-link" }
  /** object 섹션 안의 배열 필드(`eligibility.rules`): 목록 편집(P4-03)이 맡고 스칼라 컨트롤을 두지 않는다. */
  | { kind: "list-link" };

export type FormField = {
  pointer: string;
  templatePointer: string;
  key: string;
  control: FormControl;
  nullable: boolean;
  required: boolean;
  /** parse tree에 키가 있는가. 없으면 `value`는 `defaultValue`다. */
  written: boolean;
  value: unknown;
  defaultValue: unknown;
  hasDefault: boolean;
  /** 조건표 판정(문서 또는 발행 기본값). 조건표 행이 없으면 null. */
  applicable: boolean | null;
  unit: string | null;
  displayUnit: string | null;
  descriptionKey: string | null;
  diagnostics: DocumentDiagnostic[];
};

export type FormListItem = {
  pointer: string;
  /** 항목을 한 줄로 부르는 이름(스키마 `x-authoring-identity` 값 → 첫 문자열 값 → 번호). */
  summary: string;
  fields: FormField[];
  /** 항목 스키마가 union인데 문서가 분기를 고르지 못했을 때의 `kind` 후보. 아니면 null. */
  branches: readonly string[] | null;
};

export type FormSection =
  | {
      kind: "object";
      pointer: string;
      key: string;
      written: boolean;
      fields: FormField[];
    }
  | {
      kind: "list";
      pointer: string;
      key: string;
      /** 항목 스키마의 위치(`$ref`면 그 대상, 아니면 `/properties/<key>/items`). */
      itemSchemaPointer: string;
      items: FormListItem[];
    };

export type FormProjection = {
  /** 루트 스칼라(`title` 등)는 pointer·key가 빈 문자열인 첫 object 섹션에 모인다. */
  sections: FormSection[];
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const CATALOGS = ["equity-field", "universe", "factor", "subgraph"] as const;
const NAMESPACES = ["node", "parameter"] as const;

const controlFor = (
  root: JsonSchema,
  resolved: ResolvedSchema,
  pointer: string,
  tree: unknown,
): FormControl => {
  const facts = schemaFacts(resolved.node);
  if (facts.catalog !== null) {
    const catalog = CATALOGS.find((known) => known === facts.catalog);
    if (catalog !== undefined) return { kind: "catalog", catalog };
  }
  if (facts.reference !== null) {
    const namespace = NAMESPACES.find((known) => known === facts.reference);
    if (namespace !== undefined)
      return {
        kind: "reference",
        namespace,
        candidates: referenceCandidates(root, pointer, namespace, tree),
      };
  }
  if (facts.enumValues.length > 0)
    return { kind: "enum", values: facts.enumValues };
  if (facts.type === "boolean") return { kind: "boolean" };
  if (facts.type === "integer" || facts.type === "number")
    return {
      kind: "number",
      min: facts.minimum,
      max: facts.maximum,
      integer: facts.type === "integer",
    };
  if (facts.type === "string" && facts.format === "date")
    return { kind: "date" };
  return { kind: "text" };
};

/** 조건 필드가 문서에 없으면 runtime schema의 `default`로 판정한다(Inspector와 같은 규칙). */
const defaultResolverFor =
  (root: JsonSchema, tree: unknown): DefaultResolver =>
  (pointer) => {
    const node = schemaAt(root, pointer, tree)?.node;
    if (node === undefined) return { has: false, value: undefined };
    const facts = schemaFacts(node);
    return facts.hasDefault
      ? { has: true, value: facts.defaultValue }
      : { has: false, value: undefined };
  };

const projectField = (
  root: JsonSchema,
  tree: unknown,
  diagnostics: readonly DocumentDiagnostic[],
  pointer: string,
  key: string,
  required: boolean,
): FormField | null => {
  const resolved = schemaAt(root, pointer, tree);
  if (resolved === null) return null;
  const facts = schemaFacts(resolved.node);
  const found = valueAtPointer(tree, pointer);
  const isGraphLink =
    facts.type === "object" && isRecord(resolved.node.properties);
  const applicability =
    facts.applicableWhen === null
      ? null
      : projectApplicability(
          facts.applicableWhen,
          tree,
          defaultResolverFor(root, tree),
        );
  return {
    pointer,
    templatePointer: templatePointer(pointer),
    key,
    control: isGraphLink
      ? { kind: "graph-link" }
      : facts.type === "array"
        ? { kind: "list-link" }
        : controlFor(root, resolved, pointer, tree),
    nullable: resolved.nullable,
    required,
    written: found.present,
    value: found.present ? found.value : facts.defaultValue,
    defaultValue: facts.defaultValue,
    hasDefault: facts.hasDefault,
    applicable: applicability === null ? null : applicability.applicable,
    unit: facts.unit,
    displayUnit: facts.displayUnit,
    descriptionKey: facts.descriptionKey,
    diagnostics: diagnostics.filter(
      (diagnostic) =>
        diagnostic.pointer === pointer ||
        (isGraphLink && diagnostic.pointer.startsWith(`${pointer}/`)),
    ),
  };
};

const requiredKeys = (node: JsonSchema): ReadonlySet<string> =>
  new Set(
    Array.isArray(node.required)
      ? node.required.filter((key): key is string => typeof key === "string")
      : [],
  );

/** 스키마 노드의 `properties`를 순서대로 필드로 만든다. 스키마가 따라가지 못하는 키는 뺀다. */
const projectFields = (
  root: JsonSchema,
  tree: unknown,
  diagnostics: readonly DocumentDiagnostic[],
  parentPointer: string,
  node: JsonSchema,
): FormField[] => {
  const properties = isRecord(node.properties) ? node.properties : {};
  const required = requiredKeys(node);
  const fields: FormField[] = [];
  for (const key of Object.keys(properties)) {
    const field = projectField(
      root,
      tree,
      diagnostics,
      `${parentPointer}/${escapePointerSegment(key)}`,
      key,
      required.has(key),
    );
    if (field !== null) fields.push(field);
  }
  return fields;
};

/** 항목 한 줄 이름: `x-authoring-identity` 값 → 스키마 순서 첫 문자열 값 → 문서 첫 문자열 값 → 번호. */
const summarize = (
  itemNode: JsonSchema,
  item: unknown,
  index: number,
): string => {
  if (isRecord(item)) {
    const properties = isRecord(itemNode.properties) ? itemNode.properties : {};
    for (const [key, property] of Object.entries(properties)) {
      if (
        isRecord(property) &&
        property["x-authoring-identity"] === true &&
        typeof item[key] === "string"
      )
        return item[key];
    }
    for (const key of Object.keys(properties)) {
      if (typeof item[key] === "string") return item[key];
    }
    // union 항목처럼 분기가 정해지지 않아 스키마 속성이 없으면 문서의 첫 문자열 값을 쓴다.
    for (const value of Object.values(item)) {
      if (typeof value === "string") return value;
    }
  }
  return `#${index + 1}`;
};

const projectListSection = (
  root: JsonSchema,
  tree: unknown,
  diagnostics: readonly DocumentDiagnostic[],
  key: string,
  pointer: string,
  arrayNode: JsonSchema,
): FormSection => {
  const itemSchema = isRecord(arrayNode.items) ? arrayNode.items : {};
  const itemSchemaPointer =
    typeof itemSchema.$ref === "string" && itemSchema.$ref.startsWith("#")
      ? itemSchema.$ref.slice(1)
      : `/properties/${escapePointerSegment(key)}/items`;
  const value = valueAtPointer(tree, pointer).value;
  const items = Array.isArray(value) ? value : [];
  return {
    kind: "list",
    pointer,
    key,
    itemSchemaPointer,
    items: items.map((item, index): FormListItem => {
      const itemPointer = `${pointer}/${index}`;
      const resolved = schemaAt(root, itemPointer, tree);
      const unresolved = resolved?.branches !== null && resolved !== null;
      return {
        pointer: itemPointer,
        summary: summarize(resolved?.node ?? {}, item, index),
        fields:
          resolved === null || unresolved
            ? []
            : projectFields(
                root,
                tree,
                diagnostics,
                itemPointer,
                resolved.node,
              ),
        branches: unresolved ? unionKindsAt(root, itemPointer, tree) : null,
      };
    }),
  };
};

export const projectForm = (
  schema: JsonSchema,
  parse: ParsedSource | null,
  diagnostics: readonly DocumentDiagnostic[],
): FormProjection => {
  const tree: Record<string, unknown> =
    parse !== null && parse.status === "ok" ? parse.tree : {};
  const rootProperties = isRecord(schema.properties) ? schema.properties : {};
  const rootRequired = requiredKeys(schema);
  const scalarFields: FormField[] = [];
  const sections: FormSection[] = [];
  for (const key of Object.keys(rootProperties)) {
    const pointer = `/${escapePointerSegment(key)}`;
    const resolved = schemaAt(schema, pointer, tree);
    if (resolved === null) continue;
    const facts = schemaFacts(resolved.node);
    if (facts.type === "array") {
      sections.push(
        projectListSection(
          schema,
          tree,
          diagnostics,
          key,
          pointer,
          resolved.node,
        ),
      );
    } else if (facts.type === "object" && isRecord(resolved.node.properties)) {
      sections.push({
        kind: "object",
        pointer,
        key,
        written: valueAtPointer(tree, pointer).present,
        fields: projectFields(
          schema,
          tree,
          diagnostics,
          pointer,
          resolved.node,
        ),
      });
    } else {
      const field = projectField(
        schema,
        tree,
        diagnostics,
        pointer,
        key,
        rootRequired.has(key),
      );
      if (field !== null) scalarFields.push(field);
    }
  }
  return {
    sections: [
      ...(scalarFields.length > 0
        ? [
            {
              kind: "object" as const,
              pointer: "",
              key: "",
              written: true,
              fields: scalarFields,
            },
          ]
        : []),
      ...sections,
    ],
  };
};
