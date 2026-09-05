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
