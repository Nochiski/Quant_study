import { describe, expect, it } from "vitest";

import { messages } from "../../../shared/config";
import {
  readOpenApi,
  rejectionCodes,
} from "../../../shared/testing/openapi-codes";
import {
  CONTINUOUS_ACCOUNT_ERROR_KEYS,
  continuousAccountErrorKey,
} from "../model/continuous-account-copy";

describe("연속 계좌 오류 문구 owner", () => {
  it("전용 응답 schema의 모든 오류를 UI와 같은 연결표로 두 언어에 번역한다", () => {
    const openapi = readOpenApi();
    const codes = new Set(
      Object.keys(openapi.paths).flatMap((path) => [
        ...rejectionCodes(openapi, path, {
          responseSchema: "ContinuousAccountErrorResponse",
        }),
      ]),
    );
    expect(codes.size).toBeGreaterThan(0);
    expect([...codes].sort()).toEqual(
      Object.keys(CONTINUOUS_ACCOUNT_ERROR_KEYS).sort(),
    );
    for (const code of codes) {
      const key = continuousAccountErrorKey(code);
      expect(key).toBeDefined();
      expect(messages.ko[key!]).toBeTruthy();
      expect(messages.en[key!]).toBeTruthy();
    }
  });
});
