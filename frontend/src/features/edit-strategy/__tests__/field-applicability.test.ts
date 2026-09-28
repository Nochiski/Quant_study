import { describe, expect, it } from "vitest";

import type { ApplicableWhen } from "../../../shared/api";
import { tOptional } from "../../../shared/config";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  isApplicableWhen,
  projectApplicability,
  type DefaultResolver,
} from "../model/field-applicability";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

/** backend가 발행한 조건 행을 그대로 읽는다(frontend 복제본 없음). */
const whenOf = (definition: string, property: string): ApplicableWhen => {
  const defs = SCHEMA.$defs as Record<string, JsonSchema>;
  const properties = defs[definition]!.properties as Record<string, JsonSchema>;
  const value = properties[property]!["x-applicable-when"];
  if (!isApplicableWhen(value))
    throw new Error(`no x-applicable-when: ${property}`);
  return value;
};

/** 발행된 기본값 조회: runtime schema property `default`(PortfolioStep 한정, 테스트용). */
const schemaDefaults: DefaultResolver = (pointer) => {
  const [section, key] = pointer.slice(1).split("/");
  const defs = SCHEMA.$defs as Record<string, JsonSchema>;
  const definition = section === "portfolio" ? "PortfolioStep" : "SignalStep";
  const property = (defs[definition]!.properties as Record<string, JsonSchema>)[
    key!
  ];
  return property && Object.hasOwn(property, "default")
    ? { has: true, value: property.default }
    : { has: false, value: undefined };
};

describe("field applicability projection", () => {
  it("reads the AND conditions from the backend row and judges them on the written tree", () => {
    const when = whenOf("PortfolioStep", "short_selection_count");
    expect(when.all_of.map((c) => c.pointer)).toEqual([
      "/portfolio/side",
      "/portfolio/selection_method",
    ]);

    const longShortTopN = projectApplicability(when, {
      portfolio: { side: "long_short", selection_method: "top_n" },
    });
    expect(longShortTopN.applicable).toBe(true);
    expect(longShortTopN.conditions.map((c) => c.path)).toEqual([
      "portfolio.side",
      "portfolio.selection_method",
    ]);

    const percentile = projectApplicability(when, {
      portfolio: { side: "long_short", selection_method: "percentile" },
    });
    expect(percentile.applicable).toBe(false);
    expect(percentile.conditions.map((c) => c.holds)).toEqual([true, false]);
  });

  it("is undecided without a written value or a published default, and never invents one", () => {
    const when = whenOf("PortfolioStep", "short_selection_count");
    expect(projectApplicability(when, { portfolio: {} }).applicable).toBeNull();
    expect(projectApplicability(when, undefined).applicable).toBeNull();
    expect(
      projectApplicability(when, { portfolio: { side: "long_only" } })
        .applicable,
    ).toBe(false);
    // not_null도 같은 규칙: 문서에 없고 발행 기본값도 없으면 판정 불가(P2-03 리뷰 118-02).
    const liquidity = whenOf("PortfolioStep", "minimum_liquidity");
    expect(
      projectApplicability(liquidity, { portfolio: {} }).applicable,
    ).toBeNull();
  });

  it("judges unwritten condition fields on the backend-published default when a resolver is given", () => {
    const when = whenOf("PortfolioStep", "short_selection_count");
    const decided = projectApplicability(
      when,
      { portfolio: {} },
      schemaDefaults,
    );
    // PortfolioStep 기본값: side long_only, selection_method top_n → long_short 조건이 거짓.
    expect(decided.applicable).toBe(false);
    expect(decided.conditions.map((c) => c.fromDefault)).toEqual([true, true]);
    const liquidity = whenOf("PortfolioStep", "minimum_liquidity");
    expect(liquidity.owned_by_error).toBe("strategy.portfolio.liquidity_field");
    expect(
      projectApplicability(liquidity, { portfolio: {} }, schemaDefaults)
        .applicable,
    ).toBe(false); // 기본값 null → not_null 거짓
    expect(
      projectApplicability(
        liquidity,
        { portfolio: { liquidity_field_id: "price.trading_value" } },
        schemaDefaults,
      ).applicable,
    ).toBe(true);
  });

  it("rejects malformed schema rows at the boundary", () => {
    expect(isApplicableWhen({ all_of: [], description_key: "k" })).toBe(false);
    expect(
      isApplicableWhen({
        all_of: [{ pointer: "/a", equals: 1, not_null: false }],
        description_key: "k",
        owned_by_error: null,
      }),
    ).toBe(false);
  });

  it("has a translation for every description key the backend publishes (P2-03 리뷰 118-07)", () => {
    const defs = SCHEMA.$defs as Record<string, JsonSchema>;
    const keys = Object.values(defs)
      .flatMap((definition) =>
        Object.values(
          (definition.properties ?? {}) as Record<string, JsonSchema>,
        ),
      )
      .map((property) => property["x-applicable-when"])
      .filter(isApplicableWhen)
      .map((when) => when.description_key);
    expect(keys).toHaveLength(9);
    for (const key of keys) expect(tOptional(key), key).not.toBeNull();
    // 같은 구멍이 필드 설명 키에도 있다(Phase 2 감사 DEFECT-P2X-004): schema가 발행한 키는 전부 번역된다.
    const descriptionKeys = Object.values(defs)
      .flatMap((definition) =>
        Object.values(
          (definition.properties ?? {}) as Record<string, JsonSchema>,
        ),
      )
      .map((property) => property["x-description-key"])
      .filter((key): key is string => typeof key === "string");
    expect(descriptionKeys.length).toBeGreaterThan(0);
    for (const key of descriptionKeys)
      expect(tOptional(key), key).not.toBeNull();
  });
});
