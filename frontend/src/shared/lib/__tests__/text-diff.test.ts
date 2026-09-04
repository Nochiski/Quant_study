import { describe, expect, it } from "vitest";

import { lineDiffSummary } from "../text-diff";

describe("lineDiffSummary", () => {
  it("counts added and removed lines and previews them in order", () => {
    const before = "a\nb\nc\n";
    const after = "a\nB\nc\nd\n";
    expect(lineDiffSummary(before, after)).toEqual({
      added: 2,
      removed: 1,
      preview: ["-b", "+B", "+d"],
    });
  });

  it("reports no changes for identical texts and caps the preview", () => {
    expect(lineDiffSummary("x\ny", "x\ny")).toEqual({
      added: 0,
      removed: 0,
      preview: [],
    });
    const many = lineDiffSummary(
      "",
      Array.from({ length: 10 }, (_, i) => `l${i}`).join("\n"),
      3,
    );
    expect(many.added).toBe(10);
    expect(many.preview).toHaveLength(3);
  });
});
