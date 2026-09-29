/**
 * 그래프 1수준(파이프라인) 투영(WORKFLOW P4-01): 입력은 backend runtime schema fixture 와 `ideas/*.yaml` 뿐이다.
 * 단계·카드 묶음·요약 문장이 스키마 마커(`x-stage`·`x-applied-stage`·`x-applicable-when`)와 i18n 조각에서
 * 나오는지 본다.
 */
import { describe, expect, it } from "vitest";

import {
  messages,
  tDescription,
  tName,
  tOptional,
} from "../../../shared/config";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectForm } from "../model/form-projection";
import { nodeSlotsByKind } from "../model/graph-transactions";
import {
  projectPipeline,
  strategySummary,
  type PipelineProjection,
} from "../model/pipeline-projection";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const EMPTY = parseSource('schema_version: "1.2"\ntitle: ""\n', "yaml");
const idea = (name: string) =>
  parseSource(
    readBackendFixture(`strategy_documents/ideas/${name}.yaml`),
    "yaml",
  );

/** 데이터셋 카탈로그가 주는 필드 이름(`DatasetFieldProfile.label`) 자리. */
const FIELD_NAMES: Record<string, string> = {
  "price.adj_close": "수정 종가",
  "price.trading_value": "거래대금",
  "financial.book_equity": "자본총계",
};
const fieldNames = (_catalog: string, value: string) =>
  FIELD_NAMES[value] ?? null;

const diagnostic = (code: string, pointer: string): DocumentDiagnostic => ({
  code,
  kind: "semantic",
  severity: "warning",
  pointer,
  message: code,
  range: null,
});

/** 단계 → 목록 pointer 와 카드마다 행 pointer. */
const layout = (pipeline: PipelineProjection) =>
  pipeline.stages.map((stage) => ({
    stage: stage.stage,
    lists: stage.lists.map((list) => list.pointer),
    cards: stage.cards.map((card) => card.rows.map((row) => row.field.pointer)),
  }));

describe("projectPipeline", () => {
  it("단계는 스키마 x-stage·x-applied-stage 이고 카드는 같은 단계 안의 적용 조건이 묶는다", () => {
    // 유동성 필터는 1단계, 역가중 원천은 3단계 비중 카드(리드 결정 2026-09-30). 섹터 중립의 조건
    // (`portfolio.side`)은 다른 단계라 자기 카드다.
    expect(layout(projectPipeline(SCHEMA, EMPTY, []))).toEqual([
      {
        stage: "eligibility",
        lists: ["/eligibility/rules"],
        cards: [
          ["/portfolio/liquidity_field_id", "/portfolio/minimum_liquidity"],
        ],
      },
      {
        stage: "signal",
        lists: ["/factors"],
        cards: [
          ["/signal/normalization"],
          ["/signal/score_threshold"],
          ["/signal/regime_field_id", "/signal/regime_minimum"],
        ],
      },
      {
        stage: "portfolio",
        lists: [],
        cards: [
          ["/portfolio/side", "/portfolio/short_selection_count"],
          [
            "/portfolio/weighting",
            "/risk/risk_field_id",
            "/risk/risk_factor_id",
          ],
          ["/portfolio/rebalance", "/portfolio/rebalance_every_n_sessions"],
          [
            "/portfolio/selection_method",
            "/portfolio/selection_count",
            "/portfolio/selection_percentile",
          ],
          ["/portfolio/turnover_buffer_count"],
          ["/portfolio/minimum_trade_weight"],
        ],
      },
      {
        stage: "risk",
        lists: [],
        cards: [
          ["/risk/gross_exposure"],
          ["/risk/net_exposure"],
          ["/risk/max_name_weight"],
          ["/risk/max_sector_weight"],
          ["/risk/sector_neutral"],
        ],
      },
    ]);
  });

  it("적용 조건이 두 단으로 이어지면 사슬 끝 행의 카드에 붙는다", () => {
    // 지금 스키마에는 두 단 사슬이 없다. 결정 문구("조건이 가리키는 필드의 카드")대로 전이하는지 합성
    // 스키마로 고정한다(#367 리뷰 P3-1 C02).
    const schema = structuredClone(SCHEMA) as {
      $defs: Record<
        string,
        { properties: Record<string, Record<string, unknown>> }
      >;
    };
    schema.$defs.PortfolioStep.properties.turnover_buffer_count[
      "x-applicable-when"
    ] = {
      all_of: [
        {
          pointer: "/portfolio/rebalance_every_n_sessions",
          equals: null,
          not_null: true,
        },
      ],
      description_key:
        "strategy.contract.applicable.rebalance_every_n_sessions",
      owned_by_error: null,
    };
    const portfolio = layout(
      projectPipeline(schema as unknown as JsonSchema, EMPTY, []),
    ).find((stage) => stage.stage === "portfolio");
    expect(portfolio?.cards).toContainEqual([
      "/portfolio/rebalance",
      "/portfolio/rebalance_every_n_sessions",
      "/portfolio/turnover_buffer_count",
    ]);
  });

  it("Form 행을 잃거나 겹치지 않고, 행은 미작성 필드를 열 자기 섹션을 가진다", () => {
    const source = readBackendFixture(
      "strategy_documents/quality_momentum.yaml",
    );
    const parse = parseSource(source, "yaml");
    const pipeline = projectPipeline(SCHEMA, parse, []);
    const formPointers = projectForm(SCHEMA, parse, []).sections.flatMap(
      (section) =>
        section.kind === "list"
          ? [section.pointer]
          : [
              ...section.fields.map((field) => field.pointer),
              ...section.lists.map((list) => list.pointer),
            ],
    );
    const placed = [
      ...pipeline.stages.flatMap((stage) => [
        ...stage.cards.flatMap((card) =>
          card.rows.map((row) => row.field.pointer),
        ),
        ...stage.lists.map((list) => list.pointer),
      ]),
      ...pipeline.unstaged.flatMap((section) =>
        section.kind === "list"
          ? [section.pointer]
          : section.fields.map((field) => field.pointer),
      ),
    ];
    expect([...placed].sort()).toEqual([...formPointers].sort());

    const rows = pipeline.stages.flatMap((stage) =>
      stage.cards.flatMap((card) => card.rows),
    );
    const sectionOf = (pointer: string) =>
      rows.find((row) => row.field.pointer === pointer)?.section.pointer;
    expect(sectionOf("/risk/risk_factor_id")).toBe("/risk");
    expect(sectionOf("/portfolio/minimum_liquidity")).toBe("/portfolio");
  });

  it("단계가 없는 섹션과 진단은 제자리에 둔다", () => {
    const pipeline = projectPipeline(SCHEMA, EMPTY, [
      diagnostic("strategy.document", ""),
      diagnostic("strategy.portfolio.section", "/portfolio"),
      diagnostic("strategy.risk.risk_field", "/risk/risk_field_id"),
    ]);
    expect(
      pipeline.unstaged.map((section) =>
        section.kind === "list"
          ? section.pointer
          : section.fields.map((field) => field.pointer),
      ),
    ).toEqual([["/schema_version", "/title", "/description"], "/parameters"]);
    expect(pipeline.unstaged[0]!.diagnostics.map((item) => item.code)).toEqual([
      "strategy.document",
    ]);
    const portfolio = pipeline.stages.find(
      (stage) => stage.stage === "portfolio",
    )!;
    expect(portfolio.diagnostics.map((item) => item.code)).toEqual([
      "strategy.portfolio.section",
    ]);
    // 다른 섹션의 행도 자기 진단을 들고 카드에 붙는다.
    expect(
      portfolio.cards[1]!.rows[1]!.field.diagnostics.map((item) => item.code),
    ).toEqual(["strategy.risk.risk_field"]);
  });

  it("스키마가 말하는 단계마다 이름·설명·요약 틀이 있다", () => {
    const stages = projectPipeline(SCHEMA, EMPTY, []).stages.map(
      (stage) => stage.stage,
    );
    const missing = stages.filter(
      (stage) =>
        tName(`strategy.stage.${stage}`) === null ||
        tDescription(`strategy.stage.${stage}`) === null ||
        tOptional(`strategy.summary.stage.${stage}`) === null,
    );
    expect(stages.length).toBeGreaterThan(0);
    expect(missing).toEqual([]);
  });
});

describe("strategySummary", () => {
  const summary = (
    parse: ReturnType<typeof parseSource>,
    diagnostics: DocumentDiagnostic[] = [],
  ) => strategySummary(projectPipeline(SCHEMA, parse, diagnostics), fieldNames);

  it("아이디어 문서를 단계 순서의 한 문장으로 요약한다(백분율 몫 없음)", () => {
    expect(summary(idea("momentum_12_1"))).toBe(
      "높은 12-1 모멘텀 순으로, 같은 비중으로, 매월, 상위 20종목을, 종목당 최대 5%, 섹터당 최대 30% 한도 안에서 골라 보유한다.",
    );
    // PBR 은 작을수록 좋은 팩터다(`direction: low`). 이름만 보이면 "PBR 이 높은" 으로 읽힌다(#367 리뷰 P2-1).
    expect(summary(idea("low_pbr_high_roe"))).toBe(
      "자본총계 0 초과인 종목 중에서, 낮은 PBR·높은 ROE 순으로, 같은 비중으로, 매월, 상위 20종목을, 종목당 최대 5%, 섹터당 최대 30% 한도 안에서 골라 보유한다.",
    );
    expect(summary(idea("top_trading_value"))).toBe(
      "거래대금 상위 20%인 종목 중에서, 높은 60일 모멘텀 순으로, 같은 비중으로, 매월, 상위 20종목을, 종목당 최대 5%, 섹터당 최대 30% 한도 안에서 골라 보유한다.",
    );
  });

  it("표시 이름을 생략한 팩터는 backend 가 채우는 factor_id 로 부른다", () => {
    const source = [
      'schema_version: "1.2"',
      "title: 라벨 없음",
      "factors:",
      "  - { factor_id: mom, direction: high, graph: { nodes: [], output_node_id: '' } }",
      "",
    ].join("\n");
    expect(summary(parseSource(source, "yaml"))).toBe(
      "높은 mom 순으로, 같은 비중으로, 매월, 상위 20종목을, 종목당 최대 10%, 섹터당 최대 30% 한도 안에서 골라 보유한다.",
    );
  });

  it("역가중 원천 팩터는 backend 진단으로 알고 알파 목록 대신 비중 조각에 둔다", () => {
    expect(
      summary(idea("inverse_volatility"), [
        diagnostic(
          "strategy.risk.risk_factor_excluded",
          "/risk/risk_factor_id",
        ),
      ]),
    ).toBe(
      "높은 12-1 모멘텀 순으로, 60일 변동성 값이 낮을수록 큰 비중으로, 매월, 상위 20종목을, 종목당 최대 10%, 섹터당 최대 30% 한도 안에서 골라 보유한다.",
    );
  });

  it("가중치는 서로 다를 때만, 적용 조건이 붙은 필드는 적용될 때만 보인다", () => {
    const source = [
      'schema_version: "1.2"',
      "title: 변형",
      "factors:",
      "  - { factor_id: momentum, label: 모멘텀, direction: high, weight: 0.6, graph: { nodes: [], output_node_id: '' } }",
      "  - { factor_id: value, label: 가치, direction: low, weight: 0.4, graph: { nodes: [], output_node_id: '' } }",
      "portfolio:",
      "  side: long_short",
      "  weighting: factor_score",
      "  rebalance: every_n_sessions",
      "  rebalance_every_n_sessions: 5",
      "  selection_method: percentile",
      "  selection_percentile: 0.1",
      "  liquidity_field_id: price.trading_value",
      "  minimum_liquidity: 1000000000",
      "",
    ].join("\n");
    expect(summary(parseSource(source, "yaml"))).toBe(
      "거래대금 1000000000 이상인 종목 중에서, 높은 모멘텀 (가중치 0.6)·낮은 가치 (가중치 0.4) 순으로, 하위 종목은 공매도하고, 점수 차이에 비례한 비중으로, 5거래일마다, 상위 10%를, 종목당 최대 10%, 섹터당 최대 30% 한도 안에서 골라 보유한다.",
    );
  });
});

describe("요약 조각 구조", () => {
  it("모든 조각의 자리표시가 같은 카드(노드) 필드로 풀리고, 사전의 요약 키는 모두 쓰일 자리가 있다", () => {
    // 자리표시가 풀리지 않으면 조각이 소리 없이 빠진다(#367 리뷰 P3-4). 문장 예시 대신 조각 전체를 구조로 본다:
    // 카드·목록 항목(규칙 하나·팩터 둘인 문서)과 노드 설정 칸에서 조각 키 → 그 카드의 필드 키를 모은다.
    const pipeline = projectPipeline(SCHEMA, idea("low_pbr_high_roe"), []);
    const fragments = new Map<string, readonly string[]>();
    for (const fields of pipeline.stages.flatMap((stage) => [
      ...stage.cards.map((card) => card.rows.map((row) => row.field)),
      ...stage.lists.flatMap((list) => list.items.map((item) => item.fields)),
    ]))
      for (const field of fields)
        for (const stem of field.control.kind === "enum"
          ? Object.values(field.control.labelKeys ?? {})
          : [field.descriptionKey])
          if (stem !== null)
            fragments.set(
              `${stem}.summary`,
              fields.map((item) => item.key),
            );
    for (const slots of nodeSlotsByKind(SCHEMA).values())
      for (const { facts } of slots.settings)
        if (facts.descriptionKey !== null)
          fragments.set(
            `${facts.descriptionKey}.summary`,
            slots.settings.map(({ key }) => key),
          );
    const unresolved = [...fragments].flatMap(([key, keys]) =>
      [...(tOptional(key) ?? "").matchAll(/\{([a-z_]+)(?:\.percent)?\}/g)]
        .map((match) => match[1]!)
        .filter((name) => !keys.includes(name))
        .map((name) => `${key} {${name}}`),
    );
    expect(unresolved).toEqual([]);
    const frames = new Set(
      pipeline.stages.flatMap((stage) =>
        stage.lists.map((list) => `${list.descriptionKey}.summary`),
      ),
    );
    // 전략 문서 필드의 조각(`strategy.*.summary`)만 본다. `problems.summary` 같은 다른 화면 키는 대상이 아니다.
    const orphans = Object.keys(messages.ko).filter(
      (key) =>
        key.startsWith("strategy.") &&
        key.endsWith(".summary") &&
        !fragments.has(key) &&
        !frames.has(key),
    );
    expect(orphans).toEqual([]);
    // 순회가 헛돌지 않는지: 번역이 있는 조각이 실제로 모였다.
    expect(
      [...fragments.keys()].filter((key) => tOptional(key) !== null).length,
    ).toBeGreaterThan(30);
  });
});
