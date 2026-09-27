import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { messages } from "../../../shared/config";
import { backendFixturePath } from "../../../shared/testing/backend-fixtures";
import {
  exclusionReasonLabel,
  type ExclusionReason,
} from "../model/exclusion-reason";

/** backend OpenAPI 의 `ExclusionReason` enum. 값 목록의 owner 는 backend 다(여기 손으로 적지 않는다). */
const OPENAPI_EXCLUSION_REASONS = (
  JSON.parse(
    readFileSync(backendFixturePath("../../openapi.json"), "utf8"),
  ) as {
    components: { schemas: { ExclusionReason: { enum: ExclusionReason[] } } };
  }
).components.schemas.ExclusionReason.enum;

const HANGUL = /[가-힣]/;

describe("exclusionReasonLabel (P3-01)", () => {
  it("names every exclusion reason the backend can send, in both locales", () => {
    // `Record` 타입이 생성 enum 누락을 typecheck 에서 막고, 이 테스트는 실행 시 계약 파일과 대조한다.
    expect(OPENAPI_EXCLUSION_REASONS.length).toBeGreaterThanOrEqual(13);
    const missing = OPENAPI_EXCLUSION_REASONS.flatMap((reason) => {
      const key = `debugger.exclusion.${reason}` as keyof (typeof messages)["en"];
      return [
        HANGUL.test(exclusionReasonLabel(reason)) ? null : `${reason} ko`,
        messages.en[key] === undefined ? `${reason} en` : null,
      ].filter((item): item is string => item !== null);
    });

    expect(missing).toEqual([]);
  });

  it("reads a cross-sectional rank cut differently from a broken eligibility rule", () => {
    // `top_percent`·`top_count`에서 잘린 종목은 규칙을 어긴 것이 아니다(P2-05). 같은 말로 보이면
    // "상위 20%에 못 들었다"가 "조건을 어겼다"로 읽힌다.
    expect(exclusionReasonLabel("eligibility_rank_cut")).toBe(
      "상위 비율·개수 밖(순위로 잘림)",
    );
    expect(exclusionReasonLabel("eligibility_failed")).toBe(
      "거르기 조건 불통과",
    );
  });
});
