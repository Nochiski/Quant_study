/**
 * 레시피 투영의 체인 판정(spec D2)과 팩터 카드 요약 문장(WORKFLOW P4-01). 입력은 backend runtime schema
 * fixture 와 `ideas/*.yaml`(레시피 빌더 산출 형태)이다.
 */
import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { projectRecipe, recipeSummary } from "../model/recipe-projection";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const treeOf = (source: string): unknown => {
  const parse = parseSource(source, "yaml");
  if (parse.status !== "ok") throw new Error("fixture must parse");
  return parse.tree;
};
const idea = (name: string) =>
  treeOf(readBackendFixture(`strategy_documents/ideas/${name}.yaml`));

/** 데이터셋 카탈로그가 주는 필드 이름(`DatasetFieldProfile.label`) 자리. */
const FIELD_NAMES: Record<string, string> = {
  "price.adj_close": "수정 종가",
  "price.market_cap": "시가총액",
  "financial.book_equity": "자본총계",
  "financial.net_income": "당기순이익",
};
const fieldNames = (_catalog: string, value: string) =>
  FIELD_NAMES[value] ?? null;

/** 노드 목록 하나로 팩터 한 개짜리 문서를 만든다(노드는 flow mapping 한 줄씩). */
const graphTree = (nodes: string[], output: string): unknown =>
  treeOf(
    [
      'schema_version: "1.2"',
      "title: 레시피",
      "factors:",
      "  - factor_id: factor",
      "    direction: high",
      "    graph:",
      nodes.length === 0 ? "      nodes: []" : "      nodes:",
      ...nodes.map((node) => `        - ${node}`),
      `      output_node_id: ${output}`,
      "",
    ].join("\n"),
  );

const CLOSE = "{ kind: field, node_id: close, field_id: price.adj_close }";
const MEAN =
  "{ kind: time_series, node_id: mean, operator: mean, input_node_id: close, window: 20 }";
const STD =
  "{ kind: time_series, node_id: std, operator: std, input_node_id: mean, window: 60 }";
/** 안 쓰인 잎. 판정 갈래 하나만 따로 고정하려고 "잎이 하나 남는다" 검사를 채워 준다. */
const ONE = "{ kind: constant, node_id: one, value: 1 }";

describe("projectRecipe", () => {
  it("다중 입력 단계는 앞 단계와 체인 밖 잎을 입력 칸 순서로 가진다", () => {
    expect(projectRecipe(SCHEMA, idea("ma20_breakout"), "/factors/0")).toEqual({
      kind: "chain",
      links: [
        { pointer: "/factors/0/graph/nodes/0", operands: [] },
        { pointer: "/factors/0/graph/nodes/1", operands: [] },
        {
          pointer: "/factors/0/graph/nodes/3",
          operands: ["/factors/0/graph/nodes/2", null],
        },
      ],
    });
  });

  it("아이디어 fixture 의 팩터는 전부 체인이다", () => {
    for (const name of [
      "momentum_12_1",
      "low_pbr_high_roe",
      "ma20_breakout",
      "inverse_volatility",
      "top_trading_value",
    ]) {
      const tree = idea(name) as { factors: unknown[] };
      tree.factors.forEach((_, index) =>
        expect(
          projectRecipe(SCHEMA, tree, `/factors/${index}`).kind,
          `${name} #${index}`,
        ).toBe("chain"),
      );
    }
  });

  it("머리 재참조·분기·안 쓰인 노드·두 체인의 합류는 고급이다", () => {
    const cases: [string, string[], string][] = [
      [
        "머리 재참조",
        [
          CLOSE,
          MEAN,
          ONE,
          "{ kind: comparison, node_id: gt, operator: gt, left_node_id: close, right_node_id: mean }",
        ],
        "gt",
      ],
      [
        "분기",
        [
          CLOSE,
          MEAN,
          "{ kind: time_series, node_id: std, operator: std, input_node_id: close, window: 20 }",
        ],
        "mean",
      ],
      [
        "안 쓰인 노드",
        [CLOSE, MEAN, ONE],
        "mean",
      ],
      [
        "두 체인의 합류",
        [
          CLOSE,
          MEAN,
          "{ kind: field, node_id: cap, field_id: price.market_cap }",
          "{ kind: cross_sectional, node_id: rank, operator: rank, input_node_id: cap }",
          "{ kind: binary, node_id: add, operator: add, left_node_id: mean, right_node_id: rank }",
        ],
        "add",
      ],
      ["머리가 잎이 아님(문서 순서가 체인과 다름)", [STD, MEAN, CLOSE], "std"],
      [
        "첫 노드가 제 자신을 읽음(순환)",
        [
          "{ kind: binary, node_id: loop, operator: add, left_node_id: loop, right_node_id: close }",
          CLOSE,
          ONE,
        ],
        "loop",
      ],
      [
        "없는 잎을 가리킴",
        [
          CLOSE,
          "{ kind: comparison, node_id: gt, operator: gt, left_node_id: ghost, right_node_id: close }",
        ],
        "gt",
      ],
      [
        "같은 노드를 두 칸에서 읽음(x op x)",
        [
          "{ kind: field, node_id: book, field_id: financial.book_equity }",
          "{ kind: binary, node_id: add, operator: add, left_node_id: book, right_node_id: book }",
        ],
        "add",
      ],
      [
        "부가 잎 공유((a/b)/b)",
        [
          CLOSE,
          "{ kind: field, node_id: book, field_id: financial.book_equity }",
          "{ kind: binary, node_id: d1, operator: divide, left_node_id: close, right_node_id: book }",
          "{ kind: binary, node_id: d2, operator: divide, left_node_id: d1, right_node_id: book }",
        ],
        "d2",
      ],
      [
        "앞선 단계를 건너뛰어 다시 읽음",
        [
          CLOSE,
          MEAN,
          STD,
          ONE,
          "{ kind: binary, node_id: sub, operator: subtract, left_node_id: std, right_node_id: mean }",
        ],
        "sub",
      ],
      ["출력이 마지막 단계가 아님", [CLOSE, MEAN], "close"],
      [
        "모르는 kind",
        [CLOSE, MEAN, "{ kind: mystery, node_id: m }"],
        "mean",
      ],
      [
        "겹친 node_id",
        [CLOSE, CLOSE.replace("price.adj_close", "price.close")],
        "close",
      ],
    ];
    for (const [label, nodes, output] of cases)
      expect(
        projectRecipe(SCHEMA, graphTree(nodes, output), "/factors/0"),
        label,
      ).toEqual({ kind: "advanced", nodeCount: nodes.length });
  });

  it("노드가 없는 그래프는 빈 체인이다", () => {
    expect(projectRecipe(SCHEMA, graphTree([], "''"), "/factors/0")).toEqual({
      kind: "chain",
      links: [],
    });
    expect(
      recipeSummary(SCHEMA, graphTree([], "''"), "/factors/0", fieldNames),
    ).toBeNull();
  });
});

describe("recipeSummary", () => {
  const summary = (tree: unknown, index = 0) =>
    recipeSummary(SCHEMA, tree, `/factors/${index}`, fieldNames);

  it("체인은 연산자 이름과 적은 설정 값으로 한 줄에 보인다", () => {
    expect(summary(idea("momentum_12_1"))).toBe(
      "수정 종가 → 기간 수익률(252일, 최근 21일 제외)",
    );
    expect(summary(idea("low_pbr_high_roe"), 0)).toBe(
      "시가총액 → 나누기(앞 단계, 자본총계)",
    );
    expect(summary(idea("low_pbr_high_roe"), 1)).toBe(
      "당기순이익 → 나누기(앞 단계, 자본총계)",
    );
    // 비교의 왼쪽이 체인 밖 잎이고 오른쪽이 앞 단계(이평)다: 종가 > 20일 평균.
    expect(summary(idea("ma20_breakout"))).toBe(
      "수정 종가 → 기간 평균(20일) → 초과(수정 종가, 앞 단계)",
    );
    expect(summary(idea("inverse_volatility"), 1)).toBe(
      "수정 종가 → 기간 수익률(2일) → 기간 표준편차(60일)",
    );
  });

  it("기본값과 같은 설정 값은 보이지 않고, 고급 그래프는 노드 수만 말한다", () => {
    expect(
      summary(
        graphTree(
          [
            CLOSE,
            "{ kind: time_series, node_id: momentum, operator: momentum, input_node_id: close, window: 60, lag: 0 }",
          ],
          "momentum",
        ),
      ),
    ).toBe("수정 종가 → 기간 수익률(60일)");
    expect(
      summary(
        graphTree(
          [
            CLOSE,
            MEAN,
            "{ kind: comparison, node_id: gt, operator: gt, left_node_id: close, right_node_id: mean }",
          ],
          "gt",
        ),
      ),
    ).toBe("노드 3개 · 고급");
    // 연산 칸이 없는 노드(조건 분기)는 노드 종류 이름으로 부른다(리뷰 돌연변이 RS05).
    expect(
      summary(
        graphTree(
          [
            CLOSE,
            "{ kind: constant, node_id: one, value: 1 }",
            "{ kind: constant, node_id: minus, value: -1 }",
            "{ kind: conditional, node_id: sign, predicate_node_id: close, true_node_id: one, false_node_id: minus }",
          ],
          "sign",
        ),
      ),
    ).toBe("수정 종가 → 조건 분기(앞 단계, 1, -1)");
  });
});
