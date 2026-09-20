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
  resolveRef,
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
  /** 스키마가 `const`로 고정한 값(`schema_version`): 편집 컨트롤 없이 읽기 전용으로 보인다. */
  | { kind: "const"; value: unknown }
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
  /** `x-default-from`: 생략 시 backend가 이 형제 키의 값으로 채운다. placeholder 값은 패널이 tree에서 읽는다. */
  defaultFrom: string | null;
  /** 조건표 행이 있는가. `applicable === null`은 행이 없거나(`false`) 판정 불가(`true`)다. */
  hasApplicability: boolean;
  /** 조건표 판정(문서 또는 발행 기본값). 조건표 행이 없거나 판정 불가면 null(`hasApplicability`로 구분). */
  applicable: boolean | null;
  unit: string | null;
  displayUnit: string | null;
  descriptionKey: string | null;
  diagnostics: DocumentDiagnostic[];
};

export type FormListItem = {
  pointer: string;
  /** 항목을 한 줄로 부르는 이름(스키마 `x-authoring-identity` 값 → const가 아닌 첫 문자열 값 → 번호). */
  summary: string;
  fields: FormField[];
  /** 항목 스키마가 union인데 문서가 분기를 고르지 못했을 때의 `kind` 후보. 아니면 null. */
  branches: readonly string[] | null;
  /** 스키마가 `x-authoring-identity`로 표시한 필드 키(`factor_id`). 삭제 가드의 identity. 없으면 null. */
  identityKey: string | null;
  /** 항목 자신의 pointer와, 어느 필드도 흡수하지 않은 하위 pointer의 진단(`strategy.parameter.bounds` 등). */
  diagnostics: DocumentDiagnostic[];
};

/** 목록 섹션. 루트 배열(`factors`)과 object 섹션 안의 배열(`eligibility.rules`) 모두 이 모양이다. */
export type FormListSection = {
  kind: "list";
  pointer: string;
  key: string;
  /** 목록 키가 문서에 있는가. 없으면 항목 추가가 키를 열면서 넣는다(`insert-key`). */
  written: boolean;
  /** 목록 키를 담는 부모 pointer(루트 목록은 `""`)와 그 부모가 문서에 있는가(없으면 부모까지 연다). */
  parentPointer: string;
  parentWritten: boolean;
  /** 항목 스키마의 위치(`$ref`면 그 대상, 아니면 `/properties/<key>/items`). */
  itemSchemaPointer: string;
  items: FormListItem[];
  /** 섹션 자신의 pointer와, 어느 항목·필드도 흡수하지 않은 하위 pointer의 진단(`strategy.factor.required` 등). */
  diagnostics: DocumentDiagnostic[];
};

export type FormSection =
  | {
      kind: "object";
      pointer: string;
      key: string;
      written: boolean;
      fields: FormField[];
      /** 중첩 목록(`eligibility.rules`): object 섹션의 배열 property는 link 필드가 아니라 목록 섹션이다(P4-05). */
      lists: FormListSection[];
      /** 섹션 자신의 pointer와, 어느 필드도 흡수하지 않은 하위 pointer의 진단. 루트 섹션은 `""`도 받는다. */
      diagnostics: DocumentDiagnostic[];
    }
  | FormListSection;

export type FormProjection = {
  /** 루트 스칼라(`title` 등)는 pointer·key가 빈 문자열인 첫 object 섹션에 모인다. */
  sections: FormSection[];
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Form이 picker를 아는 `x-catalog` 값. runtime schema fixture의 값 집합과 같아야 한다(테스트가 고정). */
/**
 * Form이 후보 select를 아는 `x-catalog` 카탈로그. `universe`는 schema 1.2에서 전략 문서를 떠나
 * 실행 설정 스키마로 옮겨갔지만(P2-03), 실행 설정 패널이 같은 컨트롤을 쓰므로 남겨 둔다
 * (P3-02).
 */
export const CATALOGS = ["equity-field", "universe", "factor", "subgraph"] as const;
/** Form이 후보 select를 아는 `x-reference` 네임스페이스. runtime schema fixture의 값 집합과 같아야 한다(테스트가 고정). */
export const NAMESPACES = ["node", "parameter"] as const;

const controlFor = (
  root: JsonSchema,
  resolved: ResolvedSchema,
  pointer: string,
  tree: unknown,
): FormControl => {
  const facts = schemaFacts(resolved.node);
  if (facts.hasConst) return { kind: "const", value: facts.constValue };
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
        // 같은 id가 중복인 문서에서도 option key가 유일하도록 중복을 걷는다(P5-02 리뷰 DEFECT-132-04).
        candidates: [...new Set(referenceCandidates(root, pointer, namespace, tree))],
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
  const isListLink = facts.type === "array";
  // link 필드(중첩 object·배열)는 그 아래 pointer의 진단을 모두 받는다(목록·Graph 편집이 맡는 영역).
  const absorbsDescendants = isGraphLink || isListLink;
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
      : isListLink
        ? { kind: "list-link" }
        : controlFor(root, resolved, pointer, tree),
    nullable: resolved.nullable,
    required,
    written: found.present,
    value: found.present ? found.value : facts.defaultValue,
    defaultValue: facts.defaultValue,
    hasDefault: facts.hasDefault,
    defaultFrom: facts.defaultFrom,
    hasApplicability: applicability !== null,
    applicable: applicability === null ? null : applicability.applicable,
    unit: facts.unit,
    displayUnit: facts.displayUnit,
    descriptionKey: facts.descriptionKey,
    diagnostics: diagnostics.filter(
      (diagnostic) =>
        diagnostic.pointer === pointer ||
        (absorbsDescendants && diagnostic.pointer.startsWith(`${pointer}/`)),
    ),
  };
};

/** 진단을 흡수한 쪽(필드·항목·섹션)의 공통 모양. 소유권 집계는 이것만 본다. */
type DiagnosticOwner = { readonly diagnostics: readonly DocumentDiagnostic[] };

/** 목록 섹션이 흡수한 진단 소유자 전부(항목 필드·항목·섹션). */
const listOwners = (list: FormListSection): DiagnosticOwner[] => [
  ...list.items.flatMap((item) => [
    ...item.fields,
    { diagnostics: item.diagnostics },
  ]),
  { diagnostics: list.diagnostics },
];

/** `owner` pointer 자신 + 그 아래 pointer 중 `owners`가 흡수하지 않은 진단. */
const unabsorbedDiagnostics = (
  diagnostics: readonly DocumentDiagnostic[],
  owner: string,
  owners: readonly DiagnosticOwner[],
): DocumentDiagnostic[] => {
  const absorbed = new Set(
    owners.flatMap((o) => o.diagnostics.map((d) => d.pointer)),
  );
  return diagnostics.filter(
    (diagnostic) =>
      !absorbed.has(diagnostic.pointer) &&
      (diagnostic.pointer === owner ||
        (owner !== "" && diagnostic.pointer.startsWith(`${owner}/`))),
  );
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

/** 항목 스키마에서 `x-authoring-identity: true`인 속성 키(`$ref` 해소). 없으면 null. */
const identityKeyOf = (root: JsonSchema, itemNode: JsonSchema): string | null => {
  const properties = isRecord(itemNode.properties) ? itemNode.properties : {};
  for (const [key, property] of Object.entries(properties)) {
    const node = isRecord(property) ? resolveRef(root, property) : null;
    if (node !== null && node["x-authoring-identity"] === true) return key;
  }
  return null;
};

/**
 * 항목 한 줄 이름: `x-authoring-identity` 값 → 스키마 순서 첫 문자열 값 → 문서 첫 문자열 값 → 번호.
 * 속성 노드는 `$ref`를 풀고 본다(backend가 `kind`를 정의 참조로 바꿔도 const 판정이 유지된다).
 */
const summarize = (
  root: JsonSchema,
  itemNode: JsonSchema,
  item: unknown,
  index: number,
): string => {
  if (isRecord(item)) {
    const properties = isRecord(itemNode.properties) ? itemNode.properties : {};
    const resolved = Object.entries(properties).flatMap(([key, property]) => {
      const node = isRecord(property) ? resolveRef(root, property) : null;
      return node === null ? [] : [[key, node] as const];
    });
    for (const [key, property] of resolved) {
      if (
        property["x-authoring-identity"] === true &&
        typeof item[key] === "string"
      )
        return item[key];
    }
    // `kind`처럼 const로 고정된 값은 항목을 구분하지 못한다(P4-01 리뷰 DEFECT-121-01).
    for (const [key, property] of resolved) {
      if (schemaFacts(property).hasConst) continue;
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
  parentPointer = "",
  parentWritten = true,
): FormListSection => {
  const itemSchema = isRecord(arrayNode.items) ? arrayNode.items : {};
  const itemSchemaPointer =
    typeof itemSchema.$ref === "string" && itemSchema.$ref.startsWith("#")
      ? itemSchema.$ref.slice(1)
      : `/properties/${escapePointerSegment(key)}/items`;
  const found = valueAtPointer(tree, pointer);
  const items = Array.isArray(found.value) ? found.value : [];
  const projectedItems = items.map((item, index): FormListItem => {
    const itemPointer = `${pointer}/${index}`;
    const resolved = schemaAt(root, itemPointer, tree);
    const unresolved = resolved?.branches !== null && resolved !== null;
    const fields =
      resolved === null || unresolved
        ? []
        : projectFields(root, tree, diagnostics, itemPointer, resolved.node);
    return {
      pointer: itemPointer,
      summary: summarize(root, resolved?.node ?? {}, item, index),
      fields,
      branches: unresolved ? unionKindsAt(root, itemPointer, tree) : null,
      identityKey:
        resolved === null || unresolved
          ? null
          : identityKeyOf(root, resolved.node),
      diagnostics: unabsorbedDiagnostics(diagnostics, itemPointer, fields),
    };
  });
  const itemFields = projectedItems.flatMap((item) => [
    ...item.fields,
    // 항목이 흡수한 진단도 섹션 몫에서 뺀다.
    { diagnostics: item.diagnostics },
  ]);
  return {
    kind: "list",
    pointer,
    key,
    written: found.present,
    parentPointer,
    parentWritten,
    itemSchemaPointer,
    items: projectedItems,
    diagnostics: unabsorbedDiagnostics(diagnostics, pointer, itemFields),
  };
};

/** mapping pointer 하나를 object 섹션으로: 스칼라 필드 + 중첩 목록(`lists`) + 진단 소유권. */
const objectSectionAt = (
  root: JsonSchema,
  tree: unknown,
  diagnostics: readonly DocumentDiagnostic[],
  key: string,
  pointer: string,
  node: JsonSchema,
): Extract<FormSection, { kind: "object" }> => {
  const written = valueAtPointer(tree, pointer).present;
  const projected = projectFields(root, tree, diagnostics, pointer, node);
  // 배열 property는 link 필드 대신 중첩 목록 섹션으로 편집한다(P4-05, 감사 DEFECT-P4X-001).
  const fields = projected.filter((field) => field.control.kind !== "list-link");
  const lists = projected
    .filter((field) => field.control.kind === "list-link")
    .map((field) =>
      projectListSection(
        root,
        tree,
        diagnostics,
        field.key,
        field.pointer,
        // `projectField`가 같은 pointer를 이미 해소해 list-link를 냈으므로 여기서 null일 수 없다(P4-05 리뷰 P2-4).
        schemaAt(root, field.pointer, tree)!.node,
        pointer,
        written,
      ),
    );
  return {
    kind: "object",
    pointer,
    key,
    written,
    fields,
    lists,
    diagnostics: unabsorbedDiagnostics(diagnostics, pointer, [
      ...fields,
      ...lists.flatMap(listOwners),
    ]),
  };
};

/**
 * 임의의 mapping pointer(`/factors/N/graph`, `/factors/N/graph/nodes/M`)를 object 섹션으로 projection한다
 * (P5-02 Graph 편집기가 P4-02 필드 컨트롤을 재사용하는 입력). union 항목은 문서의 `kind`로 분기를 고른다.
 * 스키마를 따라갈 수 없으면 null.
 */
export const projectObjectSection = (
  schema: JsonSchema,
  tree: unknown,
  diagnostics: readonly DocumentDiagnostic[],
  pointer: string,
  key: string,
): Extract<FormSection, { kind: "object" }> | null => {
  const resolved = schemaAt(schema, pointer, tree);
  if (resolved === null || resolved.branches !== null) return null;
  if (!isRecord(resolved.node.properties)) return null;
  return objectSectionAt(schema, tree, diagnostics, key, pointer, resolved.node);
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
      sections.push(
        objectSectionAt(schema, tree, diagnostics, key, pointer, resolved.node),
      );
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
  // 문서 전체(`""`) 진단과 어느 섹션에도 닿지 않은 루트 직속 진단은 루트 섹션이 받는다.
  const claimed = new Set(
    [
      ...scalarFields,
      ...sections.flatMap((section) =>
        section.kind === "object"
          ? [
              ...section.fields,
              ...section.lists.flatMap(listOwners),
              { diagnostics: section.diagnostics },
            ]
          : listOwners(section),
      ),
    ].flatMap((field) => field.diagnostics.map((d) => d.pointer)),
  );
  const rootDiagnostics = diagnostics.filter(
    (diagnostic) => !claimed.has(diagnostic.pointer),
  );
  return {
    sections: [
      {
        kind: "object" as const,
        pointer: "",
        key: "",
        written: true,
        fields: scalarFields,
        // 루트 배열은 자기 목록 섹션이 된다 — 루트 스칼라 섹션에 중첩 목록은 없다.
        lists: [],
        diagnostics: rootDiagnostics,
      },
      ...sections,
    ],
  };
};
