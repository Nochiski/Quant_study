/**
 * 연산자 팔레트 투영(WORKFLOW P1-04, spec D8). "무엇을 계산할지"를 먼저 고르게 하려면 화면은 노드
 * `kind`가 아니라 연산자를 보여야 한다. 목록의 정본은 둘이다.
 *
 * - `GET /api/v1/strategy-documents/operators`의 `(kind, operator)` 카탈로그 — 이름·한 줄 설명·
 *   계산식·arity·파라미터·가용성.
 * - runtime schema의 노드 union — 어떤 kind를 이 문서에 넣을 수 있는가, 그리고 연산자가 없는
 *   kind(`field`·`constant`·`conditional` …)의 이름.
 *
 * 둘 다 backend가 소유한다. 연산자 목록·그룹·설명을 여기에 손으로 적지 않는다. 그룹은 노드 kind이고
 * 그룹 이름은 분기 스키마의 `x-description-key`가 낸다 — 데이터 필드·기간 집계·종목 간 비교·조건
 * 비교처럼 화면 어휘가 이미 kind 단위로 발행되어 있어 별도 분류표가 필요 없다.
 */
import type { OperatorDefinition } from "../../../shared/api";
import { t, tDescription, tName, tOptional } from "../../../shared/config";
import { nodeKinds } from "./graph-transactions";
import { schemaFacts, type JsonSchema } from "./schema-navigator";

/** 연산자 카탈로그 요청의 상태. 화면이 "없다"와 "아직 모른다"를 구분해 말하게 한다(P1-04). */
export type OperatorCatalogState =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "ready"; definitions: readonly OperatorDefinition[] };

/** 카탈로그가 준비되지 않았을 때 팔레트에 붙일 한 줄. 준비됐으면 null. */
export const catalogNote = (state: OperatorCatalogState): string | null =>
  state.status === "ready"
    ? null
    : t(
        state.status === "loading"
          ? "graph.palette.catalogLoading"
          : "graph.palette.catalogUnavailable",
      );

export type PaletteEntry = {
  /** `<kind>:<operator>`, 연산자가 없는 kind는 `<kind>`. React key이자 선택 식별자다. */
  id: string;
  kind: string;
  /** null이면 이 kind에는 연산자 property가 없다(데이터 필드·상수·조건 분기 …). */
  operator: string | null;
  name: string;
  description: string | null;
  formula: string | null;
  /** 입력 노드 개수. kind 항목은 스키마가 정하므로 null. */
  arity: number | null;
  params: readonly string[];
  /**
   * 정의 시점 가용성이 `available`이 아닌가. 어댑터 capability 판정은 P2-04이다. 조건이 "available이
   * 아니다"인 것은 WORKFLOW P1-04 acceptance 그대로다 — backend가 상태를 늘릴 때 새 값이 기본
   * "정상"으로 흘러가면 화면 어휘의 owner가 backend라는 규칙과 반대가 된다(리뷰 P3).
   */
  unsupported: boolean;
};

export type PaletteGroup = {
  kind: string;
  name: string;
  description: string | null;
  entries: PaletteEntry[];
};

const entryOf = (
  kind: string,
  definition: OperatorDefinition,
): PaletteEntry => ({
  id: `${kind}:${definition.operator}`,
  kind,
  operator: definition.operator,
  name: tName(definition.description_key) ?? definition.operator,
  description: tDescription(definition.description_key),
  formula: tOptional(definition.formula_key),
  arity: definition.arity,
  params: definition.params.map((parameter) => parameter.property_name),
  unsupported: definition.availability !== "available",
});

/**
 * 이 팩터 그래프에 넣을 수 있는 항목 전부, 노드 kind 순서(= 스키마 union 순서)로 묶은 것.
 *
 * `catalog`가 null이면(아직 못 받았거나 요청이 실패) kind 항목만으로 그린다. 팔레트를 비워 노드
 * 추가 자체를 막지 않기 위해서다 — kind도 같은 backend 스키마가 낸 사실이고, 연산자를 고르지 않은
 * 노드는 스키마 기본 연산자로 만들어진 뒤 속성 편집에서 바꿀 수 있다.
 */
export const operatorPalette = (
  schema: JsonSchema,
  tree: unknown,
  factorPointer: string,
  catalog: readonly OperatorDefinition[] | null,
): PaletteGroup[] =>
  nodeKinds(schema, tree, factorPointer).map(([kind, branch]) => {
    const facts = schemaFacts(branch);
    const name = tName(facts.descriptionKey) ?? kind;
    const description = tDescription(facts.descriptionKey);
    const operators = (catalog ?? []).filter(
      (definition) => definition.kind === kind,
    );
    return {
      kind,
      name,
      description,
      entries:
        operators.length === 0
          ? [
              {
                id: kind,
                kind,
                operator: null,
                name,
                description,
                formula: null,
                arity: null,
                params: [],
                unsupported: false,
              },
            ]
          : operators.map((definition) => entryOf(kind, definition)),
    };
  });

const haystack = (group: PaletteGroup, entry: PaletteEntry): string =>
  [
    group.name,
    group.kind,
    entry.name,
    entry.description ?? "",
    entry.formula ?? "",
    entry.operator ?? "",
    ...entry.params,
  ]
    .join(" ")
    .toLowerCase();

/**
 * 검색어로 좁힌 팔레트. 낱말을 공백으로 나눠 **전부** 포함하는 항목만 남기고, 항목이 남은 그룹만
 * 돌려준다. 빈 검색어는 원본을 그대로 돌려준다(참조 동일성까지 — 렌더가 헛돌지 않게).
 */
export const filterPalette = (
  groups: readonly PaletteGroup[],
  query: string,
): readonly PaletteGroup[] => {
  const words = query.trim().toLowerCase().split(/\s+/u).filter(Boolean);
  if (words.length === 0) return groups;
  return groups.flatMap((group) => {
    const entries = group.entries.filter((entry) => {
      const text = haystack(group, entry);
      return words.every((word) => text.includes(word));
    });
    return entries.length === 0 ? [] : [{ ...group, entries }];
  });
};

/** 항목 한 줄의 보조 설명(arity·파라미터). 없으면 null. */
export const entrySignature = (entry: PaletteEntry): string | null => {
  const parts: string[] = [];
  if (entry.arity !== null)
    parts.push(t("graph.palette.arity").replace("{count}", String(entry.arity)));
  if (entry.params.length > 0)
    parts.push(
      t("graph.palette.params").replace("{params}", entry.params.join(", ")),
    );
  return parts.length === 0 ? null : parts.join(" · ");
};
