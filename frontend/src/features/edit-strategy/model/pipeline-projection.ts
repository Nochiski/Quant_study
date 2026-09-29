/**
 * 그래프 1수준(파이프라인) 투영(WORKFLOW P4-01, spec D9): runtime schema × parse tree × compile 진단 →
 * 표준 단계 모델과 전략 한 문장 요약. Form 투영의 필드 행과 목록(컨트롤·값·작성 여부·적용 여부·진단)을
 * 그대로 쓰고 단계와 카드로만 다시 묶는다. 순수 함수이며 편집기·React를 모른다.
 *
 * 필드 목록을 여기 적지 않는다. 필드의 단계는 스키마 `x-stage`·`x-applied-stage`(읽는 규칙의 정본은 정본
 * 대장 "그래프 표현 투영" 행), 카드 묶음은 같은 단계 안의 `x-applicable-when` 조건, 요약에 나올 필드는
 * 스키마 설명 키 아래 i18n 조각이 정한다(리드 결정 2026-09-30).
 */
import { t, tName, tOptional } from "../../../shared/config";
import type { ParsedSource } from "../../../shared/lib/yaml12";
import type { DocumentDiagnostic } from "./document-state";
import {
  projectForm,
  type FormField,
  type FormListItem,
  type FormListSection,
  type FormSection,
} from "./form-projection";
import type { ObjectSection } from "./form-transactions";
import {
  displayValue,
  formatContractValue,
  schemaAt,
  schemaFacts,
  type JsonSchema,
} from "./schema-navigator";

/** 카드의 행 하나: Form 필드 행과 그 필드를 담은 섹션(미작성 필드를 확정할 때의 부모, `fieldOperation`). */
export type PipelineRow = { field: FormField; section: ObjectSection };

/** 카드 = 앵커 행 하나 + 같은 단계 안에서 적용 조건이 그 카드를 가리키는 행들(스키마 순서). */
export type PipelineCard = { pointer: string; rows: PipelineRow[] };

export type PipelineStage = {
  /** `AppliedStage` 값(`eligibility`·`signal`·`portfolio`·`risk`). 이름은 i18n `strategy.stage.<값>`. */
  stage: string;
  /** 항목이 카드인 목록(`eligibility.rules`·`factors`). 항목 추가 자리와 목록 진단을 함께 가진다. */
  lists: FormListSection[];
  cards: PipelineCard[];
  /** 이 단계 섹션 자신의 진단(어느 행·목록도 흡수하지 않은 것). */
  diagnostics: DocumentDiagnostic[];
};

export type PipelineProjection = {
  /** 스키마 최상위 property 순서로 처음 나온 단계부터. */
  stages: PipelineStage[];
  /** 단계가 없는 섹션(문서 머리·`parameters`)과 문서 전체 진단. 배치는 P4-02·P4-04가 정한다. */
  unstaged: FormSection[];
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** 단계에 배정된 행과 그 행의 적용 조건이 가리키는 pointer(`x-applicable-when`, 조건표 순서). */
type StagedRow = { row: PipelineRow; conditions: readonly string[] };

/**
 * 같은 단계의 행을 카드로 묶는다: 행의 적용 조건 중 같은 단계 행을 가리키는 첫 조건의 카드에 붙고, 없으면
 * 자기 카드를 연다. 묶음의 정본은 backend 적용 조건표다.
 */
const cardsOf = (staged: readonly StagedRow[]): PipelineCard[] => {
  const byPointer = new Map(
    staged.map((entry) => [entry.row.field.pointer, entry]),
  );
  const anchorOf = (entry: StagedRow): string => {
    const seen = new Set<string>();
    let current = entry;
    for (;;) {
      seen.add(current.row.field.pointer);
      const target = current.conditions.find(
        (pointer) => byPointer.has(pointer) && !seen.has(pointer),
      );
      if (target === undefined) return current.row.field.pointer;
      current = byPointer.get(target)!;
    }
  };
  const anchors = staged.map(anchorOf);
  const cards = new Map<string, PipelineCard>();
  staged.forEach(({ row }, index) => {
    if (anchors[index] === row.field.pointer)
      cards.set(row.field.pointer, { pointer: row.field.pointer, rows: [row] });
  });
  staged.forEach(({ row }, index) => {
    if (anchors[index] !== row.field.pointer)
      cards.get(anchors[index])!.rows.push(row);
  });
  return [...cards.values()];
};

export const projectPipeline = (
  schema: JsonSchema,
  parse: ParsedSource | null,
  diagnostics: readonly DocumentDiagnostic[],
): PipelineProjection => {
  const tree: unknown =
    parse !== null && parse.status === "ok" ? parse.tree : {};
  const rootProperties = isRecord(schema.properties) ? schema.properties : {};
  type Entry = {
    lists: FormListSection[];
    staged: StagedRow[];
    diagnostics: DocumentDiagnostic[];
  };
  const stages = new Map<string, Entry>();
  const entryOf = (stage: string): Entry => {
    const entry = stages.get(stage) ?? {
      lists: [],
      staged: [],
      diagnostics: [],
    };
    stages.set(stage, entry);
    return entry;
  };
  const factsAt = (pointer: string) => {
    const node = schemaAt(schema, pointer, tree)?.node;
    return node === undefined ? null : schemaFacts(node);
  };
  const unstaged: FormSection[] = [];
  for (const section of projectForm(schema, parse, diagnostics).sections) {
    // 섹션의 `x-stage`는 `$ref`를 풀기 전의 최상위 property에 있다(Form 섹션 이름과 같은 자리).
    const property = rootProperties[section.key];
    const sectionStage = isRecord(property)
      ? schemaFacts(property).stage
      : null;
    if (section.kind === "list") {
      if (sectionStage === null) unstaged.push(section);
      else entryOf(sectionStage).lists.push(section);
      continue;
    }
    // 행·목록의 단계: 자기 `x-stage`·`x-applied-stage`, 없으면 섹션의 단계(정본 대장 "그래프 표현 투영" 행).
    const fields = section.fields.filter((field) => {
      const facts = factsAt(field.pointer);
      const stage = facts?.stage ?? facts?.appliedStage ?? sectionStage;
      if (stage === null) return true;
      entryOf(stage).staged.push({
        row: { field, section },
        conditions: (facts?.applicableWhen?.all_of ?? []).map(
          (condition) => condition.pointer,
        ),
      });
      return false;
    });
    const lists = section.lists.filter((list) => {
      const facts = factsAt(list.pointer);
      const stage = facts?.stage ?? facts?.appliedStage ?? sectionStage;
      if (stage !== null) entryOf(stage).lists.push(list);
      return stage === null;
    });
    if (sectionStage === null) unstaged.push({ ...section, fields, lists });
    else entryOf(sectionStage).diagnostics.push(...section.diagnostics);
  }
  return {
    stages: [...stages].map(([stage, entry]) => ({
      stage,
      lists: entry.lists,
      cards: cardsOf(entry.staged),
      diagnostics: entry.diagnostics,
    })),
    unstaged,
  };
};

/** 카탈로그 값(`price.trading_value`)의 화면 이름. 모르면 null이고 값을 그대로 보인다. */
export type CatalogNames = (catalog: string, value: string) => string | null;

/** 요약 문장의 이름 풀이: 카탈로그 값과, 참조 id(`risk_factor_id`)가 가리키는 문서 항목의 이름. */
export type SummaryNames = {
  catalog: CatalogNames;
  reference: (id: string) => string | null;
};

/** 필드의 값. 생략했고 `x-default-from`이 있으면 backend가 채우는 형제 필드 값이다(`label` ← `factor_id`). */
const valueOf = (field: FormField, card: readonly FormField[]): unknown =>
  field.value ??
  (field.defaultFrom === null
    ? undefined
    : card.find((sibling) => sibling.key === field.defaultFrom)?.value);

/**
 * 필드 값을 문장에 넣을 글자로: enum은 값 이름, 카탈로그·참조는 이름, 비율은 표시 단위(`displayValue`),
 * `percent`면 비율을 %로. 값이 없으면 null.
 */
export const fieldText = (
  field: FormField,
  card: readonly FormField[],
  names: SummaryNames,
  percent = false,
): string | null => {
  const value = valueOf(field, card);
  if (value === null || value === undefined || value === "") return null;
  const control = field.control;
  if (percent && typeof value === "number")
    return displayValue(value, "ratio", "%");
  if (typeof value === "string") {
    if (control.kind === "enum")
      return tName(control.labelKeys?.[value]) ?? value;
    if (control.kind === "catalog")
      return names.catalog(control.catalog, value) ?? value;
    if (control.kind === "reference") return names.reference(value) ?? value;
  }
  return (
    displayValue(value, field.unit, field.displayUnit) ??
    formatContractValue(value)
  );
};

/** 조각 자리 `{<키>}`·`{<키>.percent}`: 같은 카드 필드의 글자(`.percent`는 비율을 %로). */
const PLACEHOLDER = /\{([a-z_]+)(\.percent)?\}/g;

/**
 * 필드 하나의 요약 조각: 설명 키 아래 `.summary`(enum은 고른 값의 이름 키 아래 `.summary`)에 같은 카드 필드의
 * 글자를 끼운다. 조각 키가 없거나, 적용되지 않거나, 값이 거짓이거나, 끼울 값이 없으면 null.
 */
export const fieldFragment = (
  field: FormField,
  card: readonly FormField[],
  names: SummaryNames,
): string | null => {
  if (field.applicable === false || field.value === false) return null;
  const control = field.control;
  const key =
    control.kind !== "enum"
      ? field.descriptionKey
      : typeof field.value === "string"
        ? (control.labelKeys?.[field.value] ?? null)
        : null;
  const template = key === null ? null : tOptional(`${key}.summary`);
  if (template === null) return null;
  let missing = false;
  const filled = template.replace(
    PLACEHOLDER,
    (_, name: string, percent?: string) => {
      const source = card.find((sibling) => sibling.key === name);
      const text =
        source === undefined
          ? null
          : fieldText(source, card, names, percent !== undefined);
      missing ||= text === null;
      return text ?? "";
    },
  );
  return missing ? null : filled;
};

/** 합성 점수에서 빠진 팩터(역가중 원천, P2-06)를 알리는 backend 진단. 제외 규칙을 여기서 다시 판정하지 않는다. */
const EXCLUDED_FACTOR = "strategy.risk.risk_factor_excluded";

/**
 * 전략 한 문장 요약(spec D9, 리드 결정 2026-09-30). 단계 순서대로 카드·목록 항목의 조각(`fieldFragment`)을
 * 이어 단계 틀 `strategy.summary.stage.<단계>`에 넣고 문장 틀 `strategy.summary.sentence`로 닫는다. 목록은
 * 자기 틀(`<목록 설명 키>.summary`)이 항목을 먼저 묶는다. 팩터는 방향을 붙인 이름("낮은 PBR")으로 보이고
 * 백분율 몫(합성 분모)은 계산하지 않는다(리드 결정 4, #367 리뷰 P2-1 로 방향까지 넓힘).
 */
export const strategySummary = (
  pipeline: PipelineProjection,
  catalog: CatalogNames,
): string => {
  const items = pipeline.stages.flatMap((stage) =>
    stage.lists.flatMap((list) => list.items),
  );
  const identity = (item: FormListItem): unknown =>
    item.fields.find((field) => field.key === item.identityKey)?.value;
  const names: SummaryNames = {
    catalog,
    // 참조가 가리키는 항목의 이름: identity를 기본값으로 삼는 필드(`label` ← `factor_id`), 없으면 id.
    reference: (id) => {
      const item = items.find((candidate) => identity(candidate) === id);
      const named = item?.fields.find(
        (field) =>
          field.defaultFrom !== null && field.defaultFrom === item.identityKey,
      );
      return item === undefined || named === undefined
        ? null
        : fieldText(named, item.fields, names);
    },
  };
  const cardPart = (
    card: readonly FormField[],
    shown: (field: FormField) => boolean = () => true,
  ): string =>
    card
      .filter(shown)
      .flatMap((field) => fieldFragment(field, card, names) ?? [])
      .join(" ");
  const excluded = new Set(
    pipeline.stages
      .flatMap((stage) => stage.cards.flatMap((card) => card.rows))
      .filter(({ field }) =>
        field.diagnostics.some(
          (diagnostic) => diagnostic.code === EXCLUDED_FACTOR,
        ),
      )
      .map(({ field }) => field.value),
  );
  const listParts = (list: FormListSection): string[] => {
    const shownItems = list.items.filter(
      (item) => !excluded.has(identity(item)),
    );
    // 숫자 조각(팩터 가중치)은 항목끼리 값이 다를 때만 보인다 — 모두 같으면 몫을 가르지 않는다.
    const uniform = new Set(
      (shownItems[0]?.fields ?? [])
        .filter(
          (field) =>
            field.control.kind === "number" &&
            shownItems.every(
              (item) =>
                item.fields.find((other) => other.key === field.key)?.value ===
                field.value,
            ),
        )
        .map((field) => field.key),
    );
    const parts = shownItems
      .map((item) => cardPart(item.fields, (field) => !uniform.has(field.key)))
      .filter((part) => part !== "");
    const frame =
      list.descriptionKey === null
        ? null
        : tOptional(`${list.descriptionKey}.summary`);
    if (parts.length === 0 || frame === null) return parts;
    return [frame.replace("{items}", parts.join("·"))];
  };
  const clauses = pipeline.stages.flatMap((stage) => {
    const parts = [
      ...stage.lists.flatMap(listParts),
      ...stage.cards
        .map((card) => cardPart(card.rows.map((row) => row.field)))
        .filter((part) => part !== ""),
    ];
    const frame = tOptional(`strategy.summary.stage.${stage.stage}`);
    return parts.length === 0 || frame === null
      ? []
      : [frame.replace("{parts}", parts.join(", "))];
  });
  return clauses.length === 0
    ? ""
    : t("strategy.summary.sentence").replace("{stages}", clauses.join(", "));
};
