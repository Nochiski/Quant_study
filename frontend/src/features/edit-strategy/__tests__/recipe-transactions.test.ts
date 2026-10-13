import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { parseSource, valueAtPointer } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { nodeSlotsByKind } from "../model/graph-transactions";
import { projectRecipe } from "../model/recipe-projection";
import {
  recipeTransaction,
  type RecipeOperation,
  type RecipeStepSeed,
} from "../model/recipe-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import {
  applyToTree,
  planSourceOperations,
  type Scalar,
} from "../model/source-transactions";

const schema = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const factor = "/factors/0";
const blank =
  '# 유지할 머리\nschema_version: "1.2"\ntitle: "주석과 따옴표"\nfactors:\n  - factor_id: alpha\n    graph:\n      nodes: []\n      output_node_id: ""\nportfolio:\n  selection_count: 7 # 유지할 꼬리\n';
const field: RecipeStepSeed = {
  kind: "field",
  settings: { field_id: "price.adj_close" },
};
const unary: RecipeStepSeed = {
  kind: "unary",
  chosen: { operator: "negate", params: [] },
};
const mean: RecipeStepSeed = {
  kind: "time_series",
  chosen: { operator: "mean", params: ["window", "lag"] },
  settings: { window: 20 },
};
const divide: RecipeStepSeed = {
  kind: "binary",
  chosen: { operator: "divide", params: [] },
  previousInput: "left_node_id",
  operands: [field],
};
const treeOf = (source: string) => {
  const parsed = parseSource(source, "yaml");
  if (parsed.status !== "ok") throw new Error("원문이 파싱되지 않습니다");
  return parsed.tree;
};
const nodesOf = (source: string) =>
  valueAtPointer(treeOf(source), `${factor}/graph/nodes`).value as Record<
    string,
    unknown
  >[];
const apply = (
  source: string,
  operation: RecipeOperation,
  pointer = factor,
): string => {
  const tree = treeOf(source);
  const frozen = JSON.stringify(tree);
  const result = recipeTransaction(tree, schema, pointer, operation);
  if ("error" in result) throw new Error(result.error);
  expect(JSON.stringify(tree)).toBe(frozen);
  if (result.ops.length === 0) return source;
  const plan = planSourceOperations(source, "yaml", result.ops);
  if (plan.status !== "ok") throw new Error(plan.reason);
  let expected = tree;
  for (const op of result.ops) expected = applyToTree(expected, op)!;
  expect(treeOf(plan.edit.nextSource)).toEqual(expected);
  expect(projectRecipe(schema, expected, pointer).kind).toBe("chain");
  expect(
    source.slice(0, plan.edit.from) +
      plan.edit.insert +
      source.slice(plan.edit.to),
  ).toBe(plan.edit.nextSource);
  return plan.edit.nextSource;
};
const insert = (source: string, index: number, seed: RecipeStepSeed) =>
  apply(source, { kind: "insert", index, seed });
const chain = () => insert(insert(insert(blank, 0, field), 1, mean), 2, divide);

describe("레시피 원문 트랜잭션", () => {
  it("중간 삽입·삭제가 다음 입력과 출력을 함께 잇고 부가 잎을 보존한다", () => {
    const original = chain();
    const added = insert(original, 1, unary);
    expect(nodesOf(added).map((n) => n.node_id)).toEqual([
      "adj_close",
      "negate",
      "mean",
      "adj_close_2",
      "divide",
    ]);
    expect(nodesOf(added)[2]!.input_node_id).toBe("negate");
    const removed = apply(added, { kind: "remove", index: 2 });
    expect(nodesOf(removed).at(-1)).toMatchObject({
      left_node_id: "negate",
      right_node_id: "adj_close_2",
    });
    const endRemoved = apply(removed, { kind: "remove", index: 2 });
    expect(nodesOf(endRemoved).map((n) => n.node_id)).toEqual([
      "adj_close",
      "negate",
    ]);
    expect(
      valueAtPointer(treeOf(endRemoved), `${factor}/graph/output_node_id`)
        .value,
    ).toBe("negate");
  });
  it("영향 없는 연결의 원문 따옴표와 주석을 보존한다", () => {
    const source = chain().replace(
      "input_node_id: adj_close",
      'input_node_id: "adj_close" # 기존 연결',
    );
    const added = insert(source, 3, unary);
    expect(added).toContain('input_node_id: "adj_close" # 기존 연결');
  });
  it("이동은 부가 잎을 단계와 같이 옮기고 문서 순서를 체인 순서로 유지한다", () => {
    const moved = apply(chain(), { kind: "move", index: 2, to: 1 });
    expect(nodesOf(moved).map((n) => n.node_id)).toEqual([
      "adj_close",
      "adj_close_2",
      "divide",
      "mean",
    ]);
    expect(nodesOf(moved)[2]).toMatchObject({
      left_node_id: "adj_close",
      right_node_id: "adj_close_2",
    });
    expect(nodesOf(moved)[3]).toMatchObject({ input_node_id: "divide" });
    expect(nodesOf(apply(moved, { kind: "move", index: 1, to: 2 }))).toEqual(
      nodesOf(chain()),
    );
  });
  it("연산자 교체는 kind와 부가 잎도 바꾸고 파라미터는 스칼라 연산으로 바꾼다", () => {
    const replaced = apply(chain(), { kind: "replace", index: 2, seed: unary });
    expect(nodesOf(replaced).map((n) => n.node_id)).toEqual([
      "adj_close",
      "mean",
      "negate",
    ]);
    const changed = apply(replaced, {
      kind: "setting",
      index: 1,
      key: "window",
      value: 60,
    });
    expect(nodesOf(changed)[1]!.window).toBe(60);
    const head = apply(changed, {
      kind: "replace",
      index: 0,
      seed: { kind: "field", settings: { field_id: "price.volume" } },
    });
    expect(nodesOf(head)[1]!.input_node_id).toBe("volume");
  });
  it("다른 팩터의 같은 이름과 CRLF 원문을 보존한다", () => {
    const source = readBackendFixture(
      "strategy_documents/ideas/low_pbr_high_roe.yaml",
    ).replaceAll("\n", "\r\n");
    const prefix = source.slice(0, source.indexOf("  - factor_id: roe"));
    const changed = apply(
      source,
      { kind: "insert", index: 1, seed: mean },
      "/factors/1",
    );
    expect(changed.startsWith(prefix)).toBe(true);
    expect(changed.replaceAll("\r\n", "")).not.toContain("\n");
    expect(valueAtPointer(treeOf(changed), "/factors/0").value).toEqual(
      valueAtPointer(treeOf(source), "/factors/0").value,
    );
  });
  it("겹치거나 빈 이름은 원문을 고치지 않고 고급 편집으로 보낸다", () => {
    const source = chain();
    for (const invalid of [
      source.replace("node_id: mean", "node_id: adj_close"),
      source.replace("node_id: mean", 'node_id: ""'),
    ]) {
      expect(
        recipeTransaction(treeOf(invalid), schema, factor, {
          kind: "remove",
          index: 1,
        }),
      ).toEqual({ error: "advanced" });
    }
  });
  it("마지막 잎을 지우면 빈 그래프로 돌아온다", () => {
    const empty = apply(insert(blank, 0, field), { kind: "remove", index: 0 });
    expect(nodesOf(empty)).toEqual([]);
    expect(
      valueAtPointer(treeOf(empty), `${factor}/graph/output_node_id`).value,
    ).toBe("");
  });
  it("머리 삭제·이동, 잘못된 슬롯·설정 및 범위는 거부한다", () => {
    const tree = treeOf(chain());
    for (const operation of [
      { kind: "remove", index: 0 },
      { kind: "move", index: 1, to: 0 },
      { kind: "move", index: 1, to: 99 },
      { kind: "remove", index: -1 },
      { kind: "setting", index: 1, key: "input_node_id", value: "bad" },
      { kind: "insert", index: 1, seed: field },
      { kind: "insert", index: 1, seed: { ...divide, previousInput: "bad" } },
      { kind: "insert", index: 1, seed: { ...divide, operands: [mean] } },
      {
        kind: "replace",
        index: 1,
        seed: { ...unary, settings: { node_id: "bad" } },
      },
    ] as RecipeOperation[])
      expect(recipeTransaction(tree, schema, factor, operation)).toHaveProperty(
        "error",
      );
    expect(
      recipeTransaction(
        treeOf(
          chain().replace("left_node_id: mean", "left_node_id: adj_close"),
        ),
        schema,
        factor,
        { kind: "remove", index: 1 },
      ),
    ).toEqual({ error: "advanced" });
  });
  it("다중 입력 슬롯 순서는 호출자가 명시하고 부가 잎은 소비 노드 직전에 넣는다", () => {
    const gt = insert(insert(insert(blank, 0, field), 1, mean), 2, {
      kind: "comparison",
      chosen: { operator: "gt", params: [] },
      previousInput: "right_node_id",
      operands: [field],
    });
    expect(nodesOf(gt).map((n) => n.node_id)).toEqual([
      "adj_close",
      "mean",
      "adj_close_2",
      "gt",
    ]);
    expect(nodesOf(gt)[3]).toMatchObject({
      left_node_id: "adj_close_2",
      right_node_id: "mean",
    });
  });
  for (const name of [
    "momentum_12_1",
    "low_pbr_high_roe",
    "ma20_breakout",
    "top_trading_value",
    "inverse_volatility",
  ]) {
    it(`${name}: 실제 fixture 연산 순서로 지은 node_id·입력·출력이 일치한다`, () => {
      const fixture = treeOf(
        readBackendFixture(`strategy_documents/ideas/${name}.yaml`),
      );
      const factors = valueAtPointer(fixture, "/factors").value as unknown[];
      factors.forEach((_, factorIndex) => {
        const pointer = `/factors/${factorIndex}`;
        const recipe = projectRecipe(schema, fixture, pointer);
        if (recipe.kind !== "chain")
          throw new Error("fixture가 체인이 아닙니다");
        let source = blank;
        const seedOf = (p: string): RecipeStepSeed => {
          const node = valueAtPointer(fixture, p).value as Record<
            string,
            Scalar
          >;
          const kind = String(node.kind);
          const slots = nodeSlotsByKind(schema).get(kind)!;
          const settings = Object.fromEntries(
            slots.settings
              .filter(({ key }) => key in node)
              .map(({ key }) => [key, node[key]!]),
          );
          return {
            kind,
            settings,
            ...(slots.operator === null
              ? {}
              : {
                  chosen: {
                    operator: String(node[slots.operator]),
                    params: [],
                  },
                }),
          };
        };
        recipe.links.forEach((link, index) => {
          const seed = seedOf(link.pointer);
          const inputs = nodeSlotsByKind(schema).get(seed.kind)!.inputs;
          source = insert(source, index, {
            ...seed,
            previousInput: inputs[link.operands.indexOf(null)]?.key,
            operands: link.operands.flatMap((p) =>
              p === null ? [] : [seedOf(p)],
            ),
          });
        });
        const original = valueAtPointer(fixture, `${pointer}/graph/nodes`)
          .value as Record<string, unknown>[];
        expect(nodesOf(source).map((n) => n.node_id)).toEqual(
          original.map((n) => n.node_id),
        );
        original.forEach((node, index) => {
          const inputs = nodeSlotsByKind(schema).get(String(node.kind))!.inputs;
          for (const { key } of inputs)
            expect(nodesOf(source)[index]![key]).toBe(node[key]);
        });
        expect(
          valueAtPointer(treeOf(source), `${factor}/graph/output_node_id`)
            .value,
        ).toBe(
          valueAtPointer(fixture, `${pointer}/graph/output_node_id`).value,
        );
      });
    });
  }
  it("임의 체인과 연산열에서도 tree·체인·범위 밖 바이트를 보존한다", () => {
    fc.assert(
      fc.property(
        fc.array(fc.tuple(fc.integer({ min: 0, max: 4 }), fc.nat(30)), {
          minLength: 1,
          maxLength: 12,
        }),
        (actions) => {
          let source = insert(blank, 0, field);
          for (const [action, choice] of actions) {
            const recipe = projectRecipe(schema, treeOf(source), factor);
            if (recipe.kind !== "chain") throw new Error("체인 소실");
            const count = recipe.links.length;
            const index = count < 2 ? 0 : 1 + (choice % (count - 1));
            const operation: RecipeOperation =
              count < 2 || action === 0
                ? {
                    kind: "insert",
                    index: 1 + (choice % count),
                    seed: choice % 2 ? unary : divide,
                  }
                : action === 1
                  ? { kind: "remove", index }
                  : action === 2
                    ? {
                        kind: "move",
                        index,
                        to: 1 + ((choice + 1) % (count - 1)),
                      }
                    : action === 3
                      ? {
                          kind: "replace",
                          index,
                          seed: choice % 2 ? unary : divide,
                        }
                      : {
                          kind: "setting",
                          index: 0,
                          key: "field_id",
                          value: "price.volume",
                        };
            source = apply(source, operation);
            expect(source.startsWith(blank.split("      nodes:")[0]!)).toBe(
              true,
            );
            expect(
              source.endsWith(
                "portfolio:\n  selection_count: 7 # 유지할 꼬리\n",
              ),
            ).toBe(true);
          }
        },
      ),
      {
        numRuns: 60,
        ...(process.env.FC_SEED ? { seed: Number(process.env.FC_SEED) } : {}),
      },
    );
  });
});
