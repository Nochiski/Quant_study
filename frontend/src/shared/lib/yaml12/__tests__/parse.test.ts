import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { locateRange, parseSource } from "..";

const FIXTURE_RELATIVE = "backend/tests/fixtures/strategy_documents";
const findFixtures = (): string => {
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, FIXTURE_RELATIVE);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir)
      throw new Error(`fixtures not found — from=${process.cwd()}`);
    dir = parent;
  }
};
const read = (file: string) =>
  readFileSync(resolve(findFixtures(), file), "utf8");
const slice = (
  text: string,
  range: { start: { offset: number }; end: { offset: number } },
) => text.slice(range.start.offset, range.end.offset);

describe("parseSource", () => {
  it("maps every value and key of the golden YAML fixture to its exact text", () => {
    const text = read("quality_momentum.yaml");
    const parsed = parseSource(text, "yaml");
    expect(parsed.status).toBe("ok");
    if (parsed.status !== "ok") return;
    expect(slice(text, parsed.valueRanges.get("/risk/max_name_weight")!)).toBe(
      "0.05",
    );
    expect(slice(text, parsed.keyRanges.get("/risk/max_name_weight")!)).toBe(
      "max_name_weight",
    );
    expect(
      slice(
        text,
        parsed.valueRanges.get("/factors/factors/0/graph/nodes/1/window")!,
      ),
    ).toBe("252");
    expect(parsed.valueRanges.get("/risk/max_name_weight")!.start).toEqual({
      line: text
        .split("\n")
        .findIndex((line) => line.includes("max_name_weight")),
      column: 19,
      offset: text.indexOf("0.05"),
    });
    expect(parsed.tree.risk).toEqual({ max_name_weight: 0.05 });
  });

  it("gives the same tree for the JSON twin, taking values from JSON.parse", () => {
    const yaml = parseSource(read("quality_momentum.yaml"), "yaml");
    const json = parseSource(read("quality_momentum.json"), "json");
    expect(json.status).toBe("ok");
    if (yaml.status !== "ok" || json.status !== "ok") return;
    expect(json.tree.title).toBe(yaml.tree.title);
    expect(json.valueRanges.has("/risk/max_name_weight")).toBe(true);
  });

  it("locates a missing pointer at the nearest parent and an unknown key at its key", () => {
    const text = read("quality_momentum.unknown_key.yaml");
    const parsed = parseSource(text, "yaml");
    expect(parsed.status).toBe("ok");
    expect(slice(text, locateRange(parsed, "/risk/max_name_wieght")!)).toBe(
      "0.05",
    );
    expect(locateRange(parsed, "/data/end_date")).toEqual(
      parsed.valueRanges.get("/data"),
    );
    expect(locateRange(parsed, "/nowhere/deep")).toEqual(
      parsed.valueRanges.get(""),
    );
  });

  // Advisory positions (editor ADR D2): the `yaml` reader reports an unterminated quote where
  // the stream ends and a second document at its content; the backend points at the opener.
  it.each([
    ['title: "unterminated\ndata:\n', "yaml.syntax", 2],
    ["a: 1\na: 2\n", "yaml.duplicate_key", 1],
    ["base: &b [1]\nc: *b\n", "yaml.anchor_or_alias", 0],
    ["a: !custom 1\n", "yaml.tag", 0],
    ["%YAML 1.1\n---\na: 1\n", "yaml.directive", 0],
    ["a:\n  <<: {x: 1}\n", "yaml.merge_key", 1],
    ["window: 1_000\n", "yaml.non_core_number", 0],
    ["a: .nan\n", "yaml.non_finite_number", 0],
    ["a: 9007199254740993\n", "yaml.integer_out_of_range", 0],
    ["1: v\n", "yaml.non_string_key", 0],
    ["a: 1\n---\nb: 2\n", "yaml.multiple_documents", 2],
    ["- a\n", "yaml.not_a_mapping", 0],
  ])("rejects %j with %s and a line", (text, code, line) => {
    const parsed = parseSource(text, "yaml");
    expect(parsed.status).toBe("rejected");
    if (parsed.status !== "rejected") return;
    expect(parsed.diagnostics[0].code).toBe(code);
    expect(parsed.diagnostics[0].range?.start.line).toBe(line);
  });

  it("reports JSON syntax errors with their position and rejects NaN", () => {
    const parsed = parseSource('{"a": 1,\n "b": }', "json");
    expect(parsed.status).toBe("rejected");
    if (parsed.status !== "rejected") return;
    expect(parsed.diagnostics[0].code).toBe("json.syntax");
    expect(parsed.diagnostics[0].range?.start.line).toBe(1);
    expect(parseSource('{"a": NaN}', "json").diagnostics[0]?.code).toBe(
      "json.syntax",
    );
    expect(parseSource("[1, 2]", "json").diagnostics[0]?.code).toBe(
      "yaml.not_a_mapping",
    );
  });

  it("positions are UTF-16 code units so they match editor offsets", () => {
    const text = 'title: "😀"\nrisk: 1\n';
    const parsed = parseSource(text, "yaml");
    if (parsed.status !== "ok") throw new Error("expected ok");
    const risk = parsed.valueRanges.get("/risk")!;
    expect(text.slice(risk.start.offset, risk.end.offset)).toBe("1");
    expect(risk.start.line).toBe(1);
  });
});
