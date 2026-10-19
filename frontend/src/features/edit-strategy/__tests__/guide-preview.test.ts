import { describe, expect, it } from "vitest";

import { changedLines } from "../model/guide-preview";

describe("안내 미리보기의 바뀐 줄", () => {
  it("새로 들어간 줄만 표시하고 그대로인 줄은 표시하지 않는다", () => {
    const before = 'schema_version: "1.2"\ntitle: ""\n';
    const after =
      'schema_version: "1.2"\ntitle: ""\nfactors:\n  - factor_id: price.momentum_12_1\n';
    expect([...changedLines(before, after)]).toEqual([2, 3]);
  });

  it("값을 바꾼 줄은 그 줄만 표시하고, 같은 원문이면 아무것도 표시하지 않는다", () => {
    const before = 'schema_version: "1.2"\ntitle: ""\n';
    const after = 'schema_version: "1.2"\ntitle: 모멘텀\n';
    expect([...changedLines(before, after)]).toEqual([1]);
    expect(changedLines(after, after).size).toBe(0);
  });
});
