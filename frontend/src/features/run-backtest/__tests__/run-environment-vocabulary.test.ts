/**
 * 실행 설정 어휘 커버리지(P3-02, BACKLOG-013): 실행 설정 스키마가 발행한 설명 키는 전부 번역이 있다.
 *
 * 전략 문서 스키마의 `screen-vocabulary.test.ts` 와 같은 규칙이다 — 키는 backend 가 발행하고 문장은
 * frontend i18n 이 갖는다. 키 목록을 손으로 적지 않고 backend 가 만든 스키마 사본을 순회한다. 이름은
 * 패널 라벨, 한 줄 뜻은 칸 아래 설명(US-SM-10), enum 값 이름은 선택지가 쓴다.
 */
import { describe, expect, it } from "vitest";

import { messages, tDescription, tName } from "../../../shared/config";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { runEnvironmentFields } from "../model/run-environment";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/run-environment-schema.json"),
) as Record<string, unknown>;

const HANGUL = /[가-힣]/;
const FIELDS = runEnvironmentFields(SCHEMA);

describe("실행 설정 어휘 커버리지", () => {
  it("스키마의 모든 칸이 설명 키를 발행하고 그 키에 이름과 한 줄 뜻이 있다", () => {
    expect(FIELDS.length).toBeGreaterThan(0);
    const missing = FIELDS.flatMap((field) =>
      field.descriptionKey === null
        ? [`${field.name} key`]
        : [
            tName(field.descriptionKey) === null ? `${field.name} name` : null,
            tDescription(field.descriptionKey) === null
              ? `${field.name} description`
              : null,
          ].filter((item): item is string => item !== null),
    );
    expect(missing).toEqual([]);
  });

  it("enum 값마다 한국어·영어 이름이 있다", () => {
    const keys = FIELDS.flatMap((field) =>
      field.options.map((value) => `${field.descriptionKey}.value.${value}`),
    );
    expect(keys.length).toBeGreaterThan(0);
    const missing = keys.flatMap((key) =>
      (["ko", "en"] as const)
        .filter(
          (locale) =>
            (messages[locale] as Record<string, string | undefined>)[key] ===
            undefined,
        )
        .map((locale) => `${key} ${locale}`),
    );
    expect(missing).toEqual([]);
  });

  it("한국어 이름과 뜻은 한글 문장이다", () => {
    const untranslated = FIELDS.flatMap((field) =>
      [tName(field.descriptionKey), tDescription(field.descriptionKey)].filter(
        (text) => text !== null && !HANGUL.test(text),
      ),
    );
    expect(untranslated).toEqual([]);
  });
});
