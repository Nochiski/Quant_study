import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { messages, tOptional } from "../../config";
import { backendFixturePath } from "../../testing/backend-fixtures";
import type {
  BacktestRunState,
  StartBacktestErrors,
} from "../generated/types.gen";

type StartBacktest422 = StartBacktestErrors[422];

const classify = (response: StartBacktest422): string => {
  const { detail } = response;
  switch (detail.code) {
    case "backtest.run.invalid":
      return `run:${detail.message}`;
    // 본문 검증 실패도 코드화된 detail 로 온다(이슈 #260). FastAPI 기본 배열 형식은 이 계약에 없다.
    case "backtest.run.field_invalid":
      return `field:${detail.field ?? "-"}`;
    case "portfolio.strategy.invalid":
      return `strategy:${detail.validation.valid}`;
    // `portfolio.data.unavailable` · `portfolio.raw_observation.invalid` 는 시작 요청이 데이터를
    // 읽지 않게 되면서(이슈 #158) 이 경로의 계약에서 빠졌다. 그 실패는 run 상태 `error` 로 온다.
    case "backtest.strategy.requires_upgrade":
      return `upgrade:${detail.message}`;
    // schema 1.2 문서는 실행 설정을 담지 않으므로 시작 요청이 반드시 실어야 한다(P2-03).
    case "backtest.run.environment_required":
      return `environment:${detail.message}`;
    // 봉인 구간을 측정하는 요청은 서버가 거절한다(검증 랩 spec D1).
    case "backtest.run.research_window_violation":
      return `research_window:${detail.message}`;
    default: {
      const exhaustive: never = detail;
      return exhaustive;
    }
  }
};

type RunFailureCode = NonNullable<BacktestRunState["error_code"]>;

// backend `RunFailureCode` 어휘(OpenAPI enum → 생성 타입)와 run 페이지 번역 키의 동기화. 코드가 늘면 이 표가
// 타입 오류로 먼저 깨지고, 번역이 빠지면 아래 단언이 깨진다(이슈 #158).
const RUN_FAILURE_CODES: Record<RunFailureCode, true> = {
  "portfolio.strategy.invalid": true,
  "portfolio.data.unavailable": true,
  "portfolio.raw_observation.invalid": true,
  "backtest.run.invalid": true,
  "backtest.run.internal": true,
};

type Schema = {
  $ref?: string;
  const?: string;
  enum?: string[];
  properties?: Record<string, Schema>;
  anyOf?: Schema[];
  oneOf?: Schema[];
  discriminator?: { mapping?: Record<string, string> };
};

type OpenApi = {
  paths: Record<
    string,
    Record<
      string,
      {
        responses: Record<
          string,
          { content?: { "application/json"?: { schema: Schema } } }
        >;
      }
    >
  >;
  components: { schemas: Record<string, Schema> };
};

/** 응답 스키마에서 닿는 detail 의 `code` 상수를 모두 모은다. 손으로 목록을 적지 않는다. */
const reachableCodes = (openapi: OpenApi, root: Schema): Set<string> => {
  const codes = new Set<string>();
  const seen = new Set<string>();
  const visit = (schema: Schema | undefined): void => {
    if (schema === undefined) return;
    if (schema.$ref !== undefined) {
      if (seen.has(schema.$ref)) return;
      seen.add(schema.$ref);
      visit(openapi.components.schemas[schema.$ref.split("/").at(-1)!]);
      return;
    }
    const code = schema.properties?.code;
    if (code?.const !== undefined) codes.add(code.const);
    for (const value of code?.enum ?? []) codes.add(value);
    for (const ref of Object.values(schema.discriminator?.mapping ?? {}))
      visit({ $ref: ref });
    for (const child of [
      ...Object.values(schema.properties ?? {}),
      ...(schema.anyOf ?? []),
      ...(schema.oneOf ?? []),
    ])
      visit(child);
  };
  visit(root);
  return codes;
};

describe("backtest run failure code vocabulary", () => {
  it("has a translated recovery message for every run failure code", () => {
    for (const code of Object.keys(RUN_FAILURE_CODES)) {
      expect(tOptional(`backtest.run.error.${code}`), code).not.toBeNull();
    }
  });

  // 이슈 #260: 툴바는 시작 거절을 `backtest.error.<code>` 번역으로 보인다. 키가 빠지면 일반 문구로 떨어져
  // 무엇을 고칠지 말하지 못한다. 실행 시 계약 파일(backend `openapi.json`)과 대조한다.
  it("translates every coded startBacktest rejection in both locales", () => {
    const openapi = JSON.parse(
      readFileSync(backendFixturePath("../../openapi.json"), "utf8"),
    ) as OpenApi;
    const responses = openapi.paths["/api/v1/backtests"]!.post!.responses;
    const codes = new Set(
      Object.entries(responses)
        .filter(([status]) => !status.startsWith("2"))
        .flatMap(([, response]) => [
          ...reachableCodes(
            openapi,
            response.content?.["application/json"]?.schema ?? {},
          ),
        ]),
    );

    expect([...codes].sort()).toEqual([
      "backtest.run.environment_required",
      "backtest.run.field_invalid",
      "backtest.run.invalid",
      "backtest.run.research_window_violation",
      "backtest.strategy.not_found",
      "backtest.strategy.requires_upgrade",
      "backtest.strategy.stale",
      "portfolio.strategy.invalid",
    ]);
    const missing = [...codes].flatMap((code) => {
      const key = `backtest.error.${code}` as keyof (typeof messages)["en"];
      return [
        messages.ko[key] === undefined ? `${code} ko` : null,
        messages.en[key] === undefined ? `${code} en` : null,
      ].filter((item): item is string => item !== null);
    });
    expect(missing).toEqual([]);
  });
});

describe("generated startBacktest error contract", () => {
  it("narrows every coded 422 response without a handwritten DTO", () => {
    expect(
      classify({
        detail: { code: "backtest.run.invalid", message: "missing strategy" },
      }),
    ).toBe("run:missing strategy");
    expect(
      classify({
        detail: {
          code: "backtest.run.field_invalid",
          field: "initial_cash",
          message: "initial_cash must be positive",
        },
      }),
    ).toBe("field:initial_cash");
    expect(
      classify({
        detail: {
          code: "portfolio.strategy.invalid",
          validation: { valid: false, issues: [] },
        },
      }),
    ).toBe("strategy:false");
    expect(
      classify({
        detail: {
          code: "backtest.strategy.requires_upgrade",
          message: "schema 1.0 revision",
        },
      }),
    ).toBe("upgrade:schema 1.0 revision");
  });
});
