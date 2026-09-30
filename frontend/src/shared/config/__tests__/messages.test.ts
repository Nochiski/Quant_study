import { describe, expect, it } from "vitest";

import { messages, type MessageKey } from "../messages";

const placeholders = (message: string): string[] =>
  [...message.matchAll(/\{([A-Za-z0-9_.-]+)\}/g)]
    .map((match) => match[1])
    .sort();

describe("message catalog contract", () => {
  it("keeps the Korean and English catalogs complete and non-empty", () => {
    expect(Object.keys(messages.en)).toEqual(Object.keys(messages.ko));
    for (const key of Object.keys(messages.ko) as MessageKey[]) {
      expect(messages.ko[key].trim(), `${key} (ko)`).not.toBe("");
      expect(messages.en[key].trim(), `${key} (en)`).not.toBe("");
    }
  });

  it("keeps interpolation placeholders identical across locales", () => {
    for (const key of Object.keys(messages.ko) as MessageKey[]) {
      expect(placeholders(messages.en[key]), key).toEqual(
        placeholders(messages.ko[key]),
      );
    }
  });

  it("keeps Hangul out of the English catalog", () => {
    // ko 문장을 en 자리에 복사해 두면 키 집합·빈 문자열 검사로는 잡히지 않는다(B-04 리뷰 P2).
    const hangul = /[ㄱ-ㆎ가-힣]/u;
    const leaked = (Object.keys(messages.en) as MessageKey[]).filter((key) =>
      hangul.test(messages.en[key]),
    );
    expect(leaked).toEqual([]);
    // 가드가 헛돌지 않는지: 같은 정규식이 ko 카탈로그에서는 걸린다.
    expect(
      (Object.keys(messages.ko) as MessageKey[]).some((key) =>
        hangul.test(messages.ko[key]),
      ),
    ).toBe(true);
  });

  it("leaves per-field meaning to the backend field description", () => {
    // 필드마다의 뜻·한계는 어댑터 필드 설명 한 곳이 싣는다. 사전 문장이 field_id 를 들어 원장·평가
    // 규칙을 옮기면 규칙이 바뀔 때 이 사본만 옛 약속을 한다(#373 DR-A-05).
    const fieldId =
      /\b(?:price|financial|consensus|flow|short|credit|event|classification|benchmark)\.[a-z_]+/u;
    const leaked = (Object.keys(messages.ko) as MessageKey[]).filter(
      (key) => fieldId.test(messages.ko[key]) || fieldId.test(messages.en[key]),
    );
    expect(leaked).toEqual([]);
  });

  it("does not ship obsolete phase placeholder copy", () => {
    const allCopy = [
      ...Object.values(messages.ko),
      ...Object.values(messages.en),
    ].join("\n");
    expect(allCopy).not.toMatch(
      /Phase\s+\d|later phases?|이후 단계|추후 단계/i,
    );
  });
});
