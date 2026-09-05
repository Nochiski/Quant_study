import { describe, expect, it } from "vitest";

import { lineDiff, lineDiffSummary } from "../text-diff";

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

  it("uses the bounded coarse summary beyond the quadratic table cap", () => {
    const before = Array.from(
      { length: 2_001 },
      (_, index) => `a${index}`,
    ).join("\n");
    const after = Array.from({ length: 2_001 }, (_, index) => `b${index}`).join(
      "\n",
    );
    expect(lineDiffSummary(before, after)).toEqual({
      added: 2_001,
      removed: 2_001,
      preview: [],
    });

    const duplicateBefore = Array.from({ length: 2_002 }, () => "x").join("\n");
    const duplicateAfter = Array.from({ length: 2_001 }, () => "x").join("\n");
    expect(lineDiff(duplicateBefore, duplicateAfter)).toMatchObject({
      added: 0,
      removed: 1,
      rows: [],
      truncated: true,
    });

    const reordered = [...Array.from({ length: 2_000 }, () => "x"), "a", "b"];
    const reorderedAfter = [
      ...Array.from({ length: 2_000 }, () => "x"),
      "b",
      "a",
    ];
    expect(
      lineDiff(reordered.join("\n"), reorderedAfter.join("\n")),
    ).toMatchObject({
      added: 2,
      removed: 2,
      truncated: true,
    });
  });

  it("reports an exact final-newline-only source change", () => {
    expect(lineDiff("a", "a\n")).toEqual({
      added: 1,
      removed: 0,
      rows: [
        {
          kind: "added",
          text: "",
          beforeLine: null,
          afterLine: 1,
          marker: "final-newline",
        },
      ],
      truncated: false,
    });
    expect(lineDiff("a\n", "a")).toMatchObject({
      added: 0,
      removed: 1,
      rows: [{ kind: "removed", marker: "final-newline", beforeLine: 1 }],
      truncated: false,
    });
    expect(lineDiffSummary("a", "a\n").preview).toEqual(["+↵ EOF"]);
  });

  it("projects bounded changed rows with exact side line numbers", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc\nd")).toEqual({
      added: 2,
      removed: 1,
      rows: [
        {
          kind: "removed",
          text: "b",
          beforeLine: 2,
          afterLine: null,
        },
        {
          kind: "added",
          text: "B",
          beforeLine: null,
          afterLine: 2,
        },
        {
          kind: "added",
          text: "d",
          beforeLine: null,
          afterLine: 4,
        },
      ],
      truncated: false,
    });
    expect(lineDiff("", "first\nsecond", 1)).toMatchObject({
      added: 2,
      removed: 0,
      truncated: true,
      rows: [{ kind: "added", text: "first", afterLine: 1 }],
    });
  });
});
