/**
 * 화면 어휘 커버리지 (P1-03, spec D8): backend가 발행한 설명 키는 전부 번역이 있다.
 *
 * 키 목록을 여기 손으로 적지 않는다 — runtime schema fixture와 연산자 카탈로그 fixture를 순회해
 * 누락을 찾는다. 노드 kind·연산자·property가 늘어나면 fixture가 먼저 바뀌고 이 테스트가 깨진다.
 * 이름(`<stem>`)과 한 줄 설명(`<stem>.description`)을 모두 요구한다: 라벨은 이름을 쓰고
 * Contract Inspector는 설명을 쓰므로 한쪽만 있으면 화면에 구멍이 난다.
 */

import { describe, expect, it } from "vitest";

import {
  messages,
  tDescription,
  tName,
  tOptional,
} from "../../../shared/config";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";

type Json = Record<string, unknown>;

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as Json;

const CATALOG = JSON.parse(
  readBackendFixture("strategy_documents/operator-catalog.json"),
) as {
  catalog_hash: string;
  operators: {
    kind: string;
    operator: string;
    params: { property_name: string; required: boolean }[];
    description_key: string;
    formula_key: string;
  }[];
};

const HANGUL = /[가-힣]/;

/** 스키마 어디에 있든 `x-description-key` 전부. 객체 stem과 property stem을 함께 모은다. */
const descriptionStems = (node: unknown, found: Set<string>): Set<string> => {
  if (Array.isArray(node)) {
    for (const item of node) descriptionStems(item, found);
    return found;
  }
  if (node === null || typeof node !== "object") return found;
  const record = node as Json;
  const stem = record["x-description-key"];
  if (typeof stem === "string") found.add(stem);
  for (const value of Object.values(record)) descriptionStems(value, found);
  return found;
};

/** 노드 `operator` property가 발행한 `x-operator`의 값(연산자 설명 키 stem) 전부. */
const operatorStems = (node: unknown, found: Set<string>): Set<string> => {
  if (Array.isArray(node)) {
    for (const item of node) operatorStems(item, found);
    return found;
  }
  if (node === null || typeof node !== "object") return found;
  const record = node as Json;
  const keys = record["x-operator"];
  if (keys !== null && typeof keys === "object") {
    for (const value of Object.values(keys as Json))
      if (typeof value === "string") found.add(value);
  }
  for (const value of Object.values(record)) operatorStems(value, found);
  return found;
};

describe("화면 어휘 커버리지", () => {
  it("runtime schema가 발행한 설명 키에 이름과 한 줄 설명이 있다", () => {
    const stems = [...descriptionStems(SCHEMA, new Set())].sort();

    // 순회가 실제로 키를 찾았는지 보는 하한이다. schema 1.2(P2-03)에서 `data`·`execution` 절이
    // 빠져 1.1 의 100여 개보다 줄었다.
    expect(stems.length).toBeGreaterThan(80);
    const missing = stems.filter(
      (stem) => tName(stem) === null || tDescription(stem) === null,
    );
    expect(missing).toEqual([]);
  });

  it("`$defs`는 파이썬 클래스명을 title로 내보내지 않는다", () => {
    const defs = SCHEMA.$defs as Record<string, Json>;

    expect(Object.keys(defs).length).toBeGreaterThan(0);
    for (const [name, definition] of Object.entries(defs)) {
      expect(definition.title, name).toBeUndefined();
      expect(typeof definition["x-description-key"], name).toBe("string");
    }
  });

  it("연산자 카탈로그의 이름·설명·계산식이 전부 번역되어 있다", () => {
    expect(CATALOG.operators.length).toBeGreaterThan(0);

    const missing = CATALOG.operators.flatMap((definition) => {
      const label = `${definition.kind}.${definition.operator}`;
      return [
        tName(definition.description_key) === null ? `${label} name` : null,
        tDescription(definition.description_key) === null
          ? `${label} description`
          : null,
        tOptional(definition.formula_key) === null ? `${label} formula` : null,
      ].filter((item): item is string => item !== null);
    });

    expect(missing).toEqual([]);
  });

  it("en 로케일에 한글이 남지 않고 ko 로케일에 빈 문장이 없다", () => {
    // `satisfies Record<MessageKey, string>`는 키 존재만 강제하고 값의 언어는 보지 않는다.
    // `tOptional`류는 ko 고정이라 en 값은 사전을 직접 순회해야 검사된다(P1-03 리뷰 P2-1).
    const korean = Object.entries(messages.en).filter(([, value]) =>
      HANGUL.test(value),
    );
    const blank = Object.entries(messages.ko).filter(
      ([, value]) => value.trim() === "",
    );

    expect(korean.map(([key]) => key)).toEqual([]);
    expect(blank.map(([key]) => key)).toEqual([]);
  });

  it("연산자 계산식이 자기 파라미터 이름을 전부 말한다", () => {
    // 시간축 연산자의 계산식이 `lag`를 빠뜨려 엔진의 창과 어긋났다(P1-03 리뷰 P2-2).
    // 파라미터를 말하지 않는 계산식은 그 파라미터가 결과에 어떻게 들어가는지 숨긴다.
    const missing = CATALOG.operators.flatMap((definition) =>
      definition.params.flatMap((parameter) =>
        (["ko", "en"] as const)
          .filter(
            (locale) =>
              !(
                messages[locale][
                  definition.formula_key as keyof (typeof messages)["ko"]
                ] ?? ""
              ).includes(parameter.property_name),
          )
          .map(
            (locale) =>
              `${definition.kind}.${definition.operator} ${locale} ${parameter.property_name}`,
          ),
      ),
    );

    expect(missing).toEqual([]);
  });

  it("스키마의 `x-operator`와 카탈로그가 같은 키를 가리킨다", () => {
    const fromSchema = [...operatorStems(SCHEMA, new Set())].sort();
    const fromCatalog = CATALOG.operators
      .map((definition) => definition.description_key)
      .sort();

    expect(fromSchema).toEqual(fromCatalog);
  });
});
