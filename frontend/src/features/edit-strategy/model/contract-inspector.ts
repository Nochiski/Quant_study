/**
 * Read-only Contract Inspector projection.
 *
 * The backend runtime schema, field contract and catalogs are the only metadata owners. This
 * module joins those responses to one selected RFC 6901 path; it never re-declares constraints,
 * allowed values, field definitions or factor definitions in the frontend.
 */
import type {
  FactorCatalog,
  FieldContract,
  ResearchCatalog,
  StrategyDocumentContractResponse,
  StrategyDocumentSchema,
} from "../../../shared/api";
import {
  escapePointerSegment,
  templatePointer,
  valueAtPointer,
} from "../../../shared/lib/yaml12";
import {
  projectApplicability,
  type DefaultResolver,
  type FieldApplicability,
} from "./field-applicability";
import {
  discriminatorAt,
  schemaAt,
  schemaFacts,
  type Bound,
  type JsonSchema,
  type SchemaFacts,
} from "./schema-navigator";

export type ContractResourceState = "loading" | "ready" | "error";

/** Query-owned inputs exposed by useSchemaAssist; generated wire objects stay intact. */
export type ContractInspectorSource = {
  schema: StrategyDocumentSchema | null;
  contract: StrategyDocumentContractResponse | null;
  equityCatalog: ResearchCatalog | null;
  factorCatalog: FactorCatalog | null;
  state: {
    schema: ContractResourceState;
    contract: ContractResourceState;
    equityCatalog: ContractResourceState;
    factorCatalog: ContractResourceState;
  };
};

export type ContractBound = Bound;

export type ContractValue = {
  present: boolean;
  raw: unknown;
  formatted: string | null;
  display: string | null;
};

export type ContractDiscriminator = {
  ownerPointer: string;
  propertyName: string;
  variants: readonly string[];
  selected: string | null;
};

export type ContractFieldProjection = {
  pointer: string;
  templatePointer: string;
  shape: "root" | "object" | "array" | "scalar";
  type: string | null;
  nullable: boolean;
  required: boolean | null;
  enumValues: readonly string[];
  constValue: unknown;
  hasConst: boolean;
  defaultValue: unknown;
  hasDefault: boolean;
  minimum: ContractBound | null;
  maximum: ContractBound | null;
  format: string | null;
  unit: string | null;
  displayUnit: string | null;
  descriptionKey: string | null;
  example: unknown;
  hasExample: boolean;
  appliedStage: string | null;
  catalog: string | null;
  reference: string | null;
  value: ContractValue;
  discriminator: ContractDiscriminator | null;
  /** Branches that define this property differently; metadata is withheld until kind resolves. */
  unresolvedBranches: readonly string[] | null;
  /** backend 조건표 행(`applicable_when`/`x-applicable-when`)의 현재 문서 판정. 행이 없으면 null. */
  applicability: FieldApplicability | null;
};

export type ContractCatalogProjection =
  | {
      kind: "equity-field";
      status:
        | "loading"
        | "error"
        | "mismatch"
        | "unselected"
        | "not-loaded"
        | "not-found";
      id: string | null;
      expectedVersion: string;
      actualVersion: string | null;
    }
  | {
      kind: "equity-field";
      status: "ready";
      id: string;
      expectedVersion: string;
      actualVersion: string;
      field: ResearchCatalog["fields"][number];
      snapshot: ResearchCatalog["snapshot"];
    }
  | {
      kind: "factor";
      status:
        | "loading"
        | "error"
        | "mismatch"
        | "unselected"
        | "not-loaded"
        | "not-found";
      id: string | null;
      expectedVersion: string;
      actualVersion: string | null;
    }
  | {
      kind: "factor";
      status: "ready";
      id: string;
      expectedVersion: string;
      actualVersion: string;
      factor: FactorCatalog["factors"][number];
    }
  | {
      kind: "other";
      status: "unsupported";
      namespace: string;
      id: string | null;
    };

export type ContractInspectorProjection =
  | { status: "loading" }
  | { status: "unavailable" }
  | {
      status: "incompatible";
      schemaHash: string;
      contractSchemaHash: string;
      schemaVersion: string;
      contractSchemaVersion: string;
    }
  | { status: "unknown"; pointer: string }
  | {
      status: "ready";
      stale: boolean;
      field: ContractFieldProjection;
      catalog: ContractCatalogProjection | null;
      provenance: {
        schemaHash: string;
        schemaVersion: string;
        contractHash: string;
      };
    };

/** One compatibility decision shared by every schema + contract consumer. */
export const isSchemaContractCompatible = (
  schema: StrategyDocumentSchema | null | undefined,
  contract: StrategyDocumentContractResponse | null | undefined,
): boolean =>
  schema !== null &&
  schema !== undefined &&
  contract !== null &&
  contract !== undefined &&
  schema.schema_hash === contract.contract.schema_hash &&
  schema.schema_version === contract.contract.schema_version;

export const valueAt = valueAtPointer;

export const contractFor = (
  contract: readonly FieldContract[],
  pointer: string,
  branch: string | null,
): FieldContract | undefined => {
  const template = templatePointer(pointer);
  const rows = contract.filter((row) => row.pointer === template);
  return (
    rows.find((row) => row.branch === branch) ?? rows.find((row) => !row.branch)
  );
};

export const formatContractValue = (value: unknown): string | null => {
  if (value === undefined) return null;
  if (typeof value === "string") return value;
  const encoded = JSON.stringify(value);
  return encoded === undefined ? String(value) : encoded;
};

const stableNumber = (value: number): string =>
  Number(value.toPrecision(12)).toString();

const displayValue = (
  value: unknown,
  unit: string | null,
  displayUnit: string | null,
): string | null => {
  if (displayUnit === null) return null;
  if (typeof value === "number" && unit === "ratio" && displayUnit === "%")
    return `${stableNumber(value * 100)}%`;
  const formatted = formatContractValue(value);
  if (formatted === null) return null;
  return displayUnit === "%" ? `${formatted}%` : `${formatted} ${displayUnit}`;
};

/** typed contract 행이 우선하고, 같은 사실의 runtime schema 값은 그 다음이다. */
const lowerBound = (
  row: FieldContract | undefined,
  facts: SchemaFacts,
): ContractBound | null =>
  typeof row?.minimum === "number"
    ? { value: row.minimum, inclusive: !row.exclusive_minimum }
    : facts.minimum;

const upperBound = (
  row: FieldContract | undefined,
  facts: SchemaFacts,
): ContractBound | null =>
  typeof row?.maximum === "number"
    ? { value: row.maximum, inclusive: !row.exclusive_maximum }
    : facts.maximum;

const nearestDiscriminator = (
  schema: JsonSchema,
  pointer: string,
  tree: unknown,
): ContractDiscriminator | null => {
  const encodedSegments = pointer === "" ? [] : pointer.slice(1).split("/");
  for (let depth = encodedSegments.length; depth >= 0; depth -= 1) {
    const ownerPointer =
      depth === 0 ? "" : `/${encodedSegments.slice(0, depth).join("/")}`;
    const found = discriminatorAt(schema, ownerPointer, tree);
    if (found !== null) return { ownerPointer, ...found };
  }
  return null;
};

/** Schema + contract join shared by hover help and the persistent inspector. */
export const projectContractField = (
  schema: JsonSchema,
  contract: readonly FieldContract[],
  pointer: string,
  tree: unknown,
): ContractFieldProjection | null => {
  const resolved = schemaAt(schema, pointer, tree);
  if (resolved === null) return null;
  const selectedValue = valueAt(tree, pointer);
  const discriminator = nearestDiscriminator(schema, pointer, tree);
  const branch = discriminator?.selected ?? null;
  const node = resolved.node;
  const discriminatorPointer = discriminator
    ? `${discriminator.ownerPointer}/${escapePointerSegment(discriminator.propertyName)}`
    : null;
  const unresolvedDiscriminatorProperty =
    discriminator?.selected === null && pointer === discriminatorPointer;
  const branchDependent =
    resolved.propertyVariants !== null && !unresolvedDiscriminatorProperty;
  const unresolvedBranches = branchDependent
    ? [...new Set(resolved.propertyVariants!.map((variant) => variant.branch))]
    : null;
  const row = branchDependent
    ? undefined
    : contractFor(contract, pointer, branch);
  const facts = schemaFacts(node);
  const type = branchDependent ? null : (row?.type ?? facts.type);
  const unit = branchDependent ? null : (row?.unit ?? facts.unit);
  const displayUnit = branchDependent
    ? null
    : (row?.display_unit ?? facts.displayUnit);
  const hasDefault =
    !branchDependent && (row?.has_default === true || facts.hasDefault);
  const defaultValue = branchDependent
    ? undefined
    : row?.has_default === true
      ? row.default
      : facts.defaultValue;
  // The backend serializes an absent optional example as null. Keep null/undefined absent;
  // false, zero and an empty string remain valid examples.
  const contractExample = branchDependent ? undefined : row?.example;
  const hasExample =
    contractExample != null || (!branchDependent && facts.hasExample);
  const example = contractExample != null ? contractExample : facts.example;
  // When no valid union branch is selected, the first branch's `kind` const is only a
  // traversal artifact. The discriminator variants are the actual contract at this point.
  const enumValues = branchDependent
    ? []
    : unresolvedDiscriminatorProperty
      ? discriminator.variants
      : (row?.enum ?? facts.enumValues);
  const hasConst =
    !branchDependent &&
    !unresolvedDiscriminatorProperty &&
    (row?.const != null || facts.hasConst);
  const constValue =
    branchDependent || unresolvedDiscriminatorProperty
      ? undefined
      : (row?.const ?? facts.constValue);
  // 조건 필드가 문서에 없으면 backend가 발행한 기본값(contract 행 → schema node)으로 판정한다.
  const resolveDefault: DefaultResolver = (conditionPointer) => {
    const conditionRow = contractFor(contract, conditionPointer, null);
    if (conditionRow?.has_default === true)
      return { has: true, value: conditionRow.default };
    const conditionNode = schemaAt(schema, conditionPointer, tree)?.node;
    const conditionFacts =
      conditionNode === undefined ? null : schemaFacts(conditionNode);
    return conditionFacts?.hasDefault
      ? { has: true, value: conditionFacts.defaultValue }
      : { has: false, value: undefined };
  };
  // typed contract 행을 우선하고, 같은 모양의 runtime schema 마커는 경계 검사를 거쳐 받는다.
  const applicableWhen = branchDependent
    ? null
    : (row?.applicable_when ?? facts.applicableWhen);
  const shape =
    pointer === ""
      ? "root"
      : type === "object"
        ? "object"
        : type === "array"
          ? "array"
          : "scalar";
  return {
    pointer,
    templatePointer: templatePointer(pointer),
    shape,
    type,
    nullable: branchDependent ? false : (row?.nullable ?? resolved.nullable),
    required: branchDependent
      ? null
      : row
        ? row.required
        : resolved.propertyRequired,
    enumValues,
    constValue,
    hasConst,
    defaultValue,
    hasDefault,
    minimum: branchDependent ? null : lowerBound(row, facts),
    maximum: branchDependent ? null : upperBound(row, facts),
    format: branchDependent ? null : (row?.format ?? facts.format),
    unit,
    displayUnit,
    descriptionKey: branchDependent
      ? null
      : (row?.description_key ?? facts.descriptionKey),
    example,
    hasExample,
    appliedStage: branchDependent
      ? null
      : (row?.applied_stage ?? facts.appliedStage),
    catalog: branchDependent ? null : (row?.catalog ?? facts.catalog),
    reference: branchDependent ? null : (row?.reference ?? facts.reference),
    value: {
      present: selectedValue.present,
      raw: selectedValue.value,
      formatted: selectedValue.present
        ? formatContractValue(selectedValue.value)
        : null,
      display: selectedValue.present
        ? displayValue(selectedValue.value, unit, displayUnit)
        : null,
    },
    discriminator,
    unresolvedBranches,
    applicability:
      applicableWhen === null
        ? null
        : projectApplicability(applicableWhen, tree, resolveDefault),
  };
};

const selectedId = (field: ContractFieldProjection): string | null =>
  field.value.present && typeof field.value.raw === "string"
    ? field.value.raw
    : null;

const catalogProjection = (
  source: ContractInspectorSource,
  field: ContractFieldProjection,
): ContractCatalogProjection | null => {
  if (field.catalog === null) return null;
  const id = selectedId(field);
  if (field.catalog === "equity-field") {
    const expectedVersion = source.contract!.contract.dataset_snapshot_id;
    const actualVersion = source.equityCatalog?.snapshot?.snapshot_id ?? null;
    if (source.state.equityCatalog !== "ready" || source.equityCatalog === null)
      return {
        kind: "equity-field",
        status:
          source.state.equityCatalog === "ready"
            ? "error"
            : source.state.equityCatalog,
        id,
        expectedVersion,
        actualVersion,
      };
    if (actualVersion !== expectedVersion)
      return {
        kind: "equity-field",
        status: "mismatch",
        id,
        expectedVersion,
        actualVersion,
      };
    if (id === null)
      return {
        kind: "equity-field",
        status: "unselected",
        id,
        expectedVersion,
        actualVersion,
      };
    const match = source.equityCatalog!.fields.find(
      (candidate) => candidate.field_id === id,
    );
    if (!match && source.equityCatalog!.page_count > 1)
      return {
        kind: "equity-field",
        status: "not-loaded",
        id,
        expectedVersion,
        actualVersion,
      };
    return match
      ? {
          kind: "equity-field",
          status: "ready",
          id,
          expectedVersion,
          actualVersion,
          field: match,
          snapshot: source.equityCatalog!.snapshot,
        }
      : {
          kind: "equity-field",
          status: "not-found",
          id,
          expectedVersion,
          actualVersion,
        };
  }
  if (field.catalog === "factor") {
    const expectedVersion = source.contract!.contract.factor_registry_version;
    const actualVersion = source.factorCatalog?.registry_version ?? null;
    if (source.state.factorCatalog !== "ready" || source.factorCatalog === null)
      return {
        kind: "factor",
        status:
          source.state.factorCatalog === "ready"
            ? "error"
            : source.state.factorCatalog,
        id,
        expectedVersion,
        actualVersion,
      };
    if (actualVersion !== expectedVersion)
      return {
        kind: "factor",
        status: "mismatch",
        id,
        expectedVersion,
        actualVersion,
      };
    if (id === null)
      return {
        kind: "factor",
        status: "unselected",
        id,
        expectedVersion,
        actualVersion,
      };
    const match = source.factorCatalog!.factors.find(
      (candidate) => candidate.factor_id === id,
    );
    if (!match && source.factorCatalog!.page_count > 1)
      return {
        kind: "factor",
        status: "not-loaded",
        id,
        expectedVersion,
        actualVersion,
      };
    return match
      ? {
          kind: "factor",
          status: "ready",
          id,
          expectedVersion,
          actualVersion,
          factor: match,
        }
      : {
          kind: "factor",
          status: "not-found",
          id,
          expectedVersion,
          actualVersion,
        };
  }
  return {
    kind: "other",
    status: "unsupported",
    namespace: field.catalog,
    id,
  };
};

export const projectContractInspector = (
  source: ContractInspectorSource,
  pointer: string | undefined,
  tree: unknown,
  stale: boolean,
): ContractInspectorProjection => {
  if (source.schema === null || source.contract === null) {
    return source.state.schema === "loading" ||
      source.state.contract === "loading"
      ? { status: "loading" }
      : { status: "unavailable" };
  }
  if (!isSchemaContractCompatible(source.schema, source.contract)) {
    return {
      status: "incompatible",
      schemaHash: source.schema.schema_hash,
      contractSchemaHash: source.contract.contract.schema_hash,
      schemaVersion: source.schema.schema_version,
      contractSchemaVersion: source.contract.contract.schema_version,
    };
  }
  const selectedPointer = pointer ?? "";
  const field = projectContractField(
    source.schema.schema as JsonSchema,
    source.contract.contract.fields,
    selectedPointer,
    tree,
  );
  if (field === null) return { status: "unknown", pointer: selectedPointer };
  return {
    status: "ready",
    stale,
    field,
    catalog: catalogProjection(source, field),
    provenance: {
      schemaHash: source.schema.schema_hash,
      schemaVersion: source.schema.schema_version,
      contractHash: source.contract.contract.contract_hash,
    },
  };
};
