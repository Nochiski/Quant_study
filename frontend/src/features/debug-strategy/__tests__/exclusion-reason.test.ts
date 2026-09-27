import { describe, expect, it } from "vitest";

import { exclusionReasonLabel } from "../model/exclusion-reason";

describe("exclusionReasonLabel (P3-01)", () => {
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
