/**
 * 연산자 팔레트 투영 (P1-04, spec D8). 목록의 정본은 연산자 카탈로그(`(kind, operator)`)와 runtime
 * schema의 노드 union이다 — 여기서도 연산자 이름을 손으로 적지 않고 fixture를 순회해 대조한다.
 */
import { describe, expect, it } from "vitest";

import type { OperatorDefinition } from "../../../shared/api";
import { tOptional } from "../../../shared/config";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  filterPalette,
  operatorPalette,
  type PaletteGroup,
} from "../model/operator-palette";
import { addNode, nodeKinds } from "../model/graph-transactions";
import {
  schemaAt,
  schemaFacts,
  type JsonSchema,
} from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const CATALOG = (
  JSON.parse(
    readBackendFixture("strategy_documents/operator-catalog.json"),
  ) as { operators: OperatorDefinition[] }
).operators;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");
const F0 = "/factors/0";

const treeOf = (source: string): unknown => {
  const parsed = parseSource(source, "yaml");
  if (parsed.status !== "ok") throw new Error("fixture must parse");
  return parsed.tree;
};

const entryIds = (groups: readonly PaletteGroup[]): string[] =>
  groups.flatMap((group) => group.entries.map((entry) => entry.id));

describe("연산자 팔레트 (P1-04)", () => {
  const tree = treeOf(VERBOSE);

  it("카탈로그의 (kind, operator) 전부를 노드 kind 순서로 묶는다", () => {
    const groups = operatorPalette(SCHEMA, tree, F0, CATALOG);

    // 그룹 순서는 스키마 union 순서(= 노드 kind 순서)다.
    expect(groups.map((group) => group.kind)).toEqual(
      nodeKinds(SCHEMA, tree, F0).map(([kind]) => kind),
    );
    // 카탈로그 항목이 하나도 빠지지 않는다.
    expect(
      entryIds(groups).filter((id) => id.includes(":")).sort(),
    ).toEqual(
      CATALOG.map(
        (definition) => `${definition.kind}:${definition.operator}`,
      ).sort(),
    );
  });

  it("연산자가 없는 kind(데이터 필드·상수·조건 분기)는 kind 자체가 한 항목이다", () => {
    const groups = operatorPalette(SCHEMA, tree, F0, CATALOG);
    const withOperators = new Set(
      CATALOG.map((definition) => definition.kind),
    );
    const plain = groups.filter((group) => !withOperators.has(group.kind));

    expect(plain.length).toBeGreaterThan(0);
    for (const group of plain) {
      expect(group.entries).toHaveLength(1);
      expect(group.entries[0]!.operator).toBeNull();
      expect(group.entries[0]!.id).toBe(group.kind);
    }
    const field = groups.find((group) => group.kind === "field");
    expect(field?.entries[0]!.name).toBe("데이터 필드");
  });

  it("항목은 이름·한 줄 설명·계산식·arity·파라미터를 카탈로그에서 읽는다", () => {
    const groups = operatorPalette(SCHEMA, tree, F0, CATALOG);
    const mean = groups
      .flatMap((group) => group.entries)
      .find((entry) => entry.id === "time_series:mean");

    expect(mean).toMatchObject({
      kind: "time_series",
      operator: "mean",
      name: "기간 평균",
      arity: 1,
      params: ["window", "lag"],
      unsupported: false,
    });
    // 계산식 문장은 i18n이 소유한다 — 여기 적으면 문구가 바뀔 때마다 이 테스트가 같이 깨진다.
    expect(mean?.formula).toBe(
      tOptional("strategy.operator.time_series.mean.formula"),
    );
    expect(mean?.formula).not.toBeNull();
    expect(mean?.description).toContain("평균");
  });

  it("정의 시점에 미지원인 연산자를 표시한다(판정은 P2-04)", () => {
    const groups = operatorPalette(SCHEMA, tree, F0, CATALOG);
    const unsupported = groups
      .flatMap((group) => group.entries)
      .filter((entry) => entry.unsupported)
      .map((entry) => entry.id);

    // 판정 기준은 "available이 아니다"이지 특정 값이 아니다(WORKFLOW P1-04 acceptance).
    expect(unsupported).toEqual(
      CATALOG.filter(
        (definition) => definition.availability !== "available",
      ).map((definition) => `${definition.kind}:${definition.operator}`),
    );
  });

  it("카탈로그를 못 받았으면 스키마의 노드 kind만으로 그린다(빈 팔레트로 막지 않는다)", () => {
    const groups = operatorPalette(SCHEMA, tree, F0, null);

    expect(groups.map((group) => group.kind)).toEqual(
      nodeKinds(SCHEMA, tree, F0).map(([kind]) => kind),
    );
    for (const group of groups) {
      expect(group.entries).toHaveLength(1);
      expect(group.entries[0]!.operator).toBeNull();
    }
  });

  it("검색은 이름·설명·계산식·연산자 값·그룹 이름을 함께 본다", () => {
    const groups = operatorPalette(SCHEMA, tree, F0, CATALOG);

    expect(entryIds(filterPalette(groups, "평균"))).toContain(
      "time_series:mean",
    );
    // 연산자 값(영문)으로도 찾는다.
    expect(entryIds(filterPalette(groups, "zscore"))).toEqual([
      "cross_sectional:zscore",
    ]);
    // 그룹(노드 kind) 이름으로 좁히면 그 그룹만 남는다.
    const byGroup = filterPalette(groups, "기간 집계");
    expect(byGroup.map((group) => group.kind)).toEqual(["time_series"]);
    // 낱말 여럿은 모두 만족해야 한다.
    expect(entryIds(filterPalette(groups, "기간 표준편차"))).toEqual([
      "time_series:std",
    ]);
    expect(filterPalette(groups, "없는연산자")).toEqual([]);
    // 빈 검색어는 **같은 배열을** 그대로 돌려준다(참조 동일성까지 — 렌더가 헛돌지 않게).
    expect(filterPalette(groups, "   ")).toBe(groups);
  });
});

/**
 * 팔레트로 만든 노드가 "만들자마자 거부되지" 않는지 본다 (P1-04 리뷰 차단 1).
 *
 * 카탈로그가 `params`로 선언한 property는 그 연산자가 **요구하는** 값이다. 그런 자리에 null이나
 * 하한 미만이 들어가면 backend가 곧바로 거부한다 — `window: 0`(수정 전)과 `periods: null`이 같은
 * 모양이었다. 항목 목록은 카탈로그에서 오고 기대값은 runtime schema에서 읽는다(손 목록 금지).
 *
 * 참조 슬롯(`*_node_id`)과 카탈로그 id(`field_id`·`factor_id` …)는 여기서 보지 않는다. 그 둘은
 * 설계상 사용자가 고르는 자리라 빈 값으로 두고(리뷰 P2-3), 문서가 유효해지는 시점이 다르다.
 * "발행된 씨앗을 backend가 받아들인다"는 짝은 `backend/tests/domain/test_node_parameter_bounds.py`가
 * 카탈로그를 순회해 고정한다.
 */
describe("팔레트가 만든 노드의 파라미터 씨앗 (P1-04)", () => {
  const tree = treeOf(VERBOSE);
  const entries = operatorPalette(SCHEMA, tree, F0, CATALOG).flatMap(
    (group) => group.entries,
  );
  const withParameters = entries.filter((entry) => entry.params.length > 0);

  it("파라미터를 가진 항목이 카탈로그에 있다", () => {
    expect(withParameters.length).toBeGreaterThan(0);
    expect(withParameters.map((entry) => entry.id)).toContain("unary:lag");
  });

  it.each(withParameters.map((entry) => [entry.id, entry] as const))(
    "%s 항목은 선언한 파라미터를 스키마 씨앗으로 채운다",
    (_id, entry) => {
      const added = addNode(tree, F0, entry.kind, SCHEMA, entry.operator);
      if ("error" in added) throw new Error(added.error);
      const value = (added.ops[0] as { value: Record<string, unknown> }).value;

      for (const name of entry.params) {
        const property = schemaAt(SCHEMA, `${F0}/graph/nodes/0/${name}`, {
          factors: [{ graph: { nodes: [{ kind: entry.kind }] } }],
        });
        expect(property, name).not.toBeNull();
        const seeded = value[name];
        // 요구되는 파라미터는 비어 있지 않다.
        expect(seeded, name).not.toBeNull();
        expect(seeded, name).not.toBeUndefined();
        // 하한을 발행한 파라미터는 그 값 이상이다.
        const bound = schemaFacts(property!.node).minimum;
        if (bound !== null) {
          expect(typeof seeded, name).toBe("number");
          expect(seeded as number, name).toBeGreaterThanOrEqual(bound.value);
        }
      }
    },
  );
});
