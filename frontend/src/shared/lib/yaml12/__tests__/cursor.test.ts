import { describe, expect, it } from "vitest";

import { describeYamlCursor, templatePointer } from "../cursor";

const DOC = [
  'schema_version: "1.0"',
  "title: 퀄리티 모멘텀",
  "risk:",
  "  gross_exposure: 1",
  "  max_name_weight: 0.05",
  "factors:",
  "  factors:",
  "    - factor_id: momentum",
  "      graph:",
  "        nodes:",
  "          - node_id: px",
  "            kind: field",
  "          - node_id: mom",
  "            kind: time_series",
  "            input_node_id: px",
  "    - factor_id: quality",
  "      weight: 1",
  "parameters:",
  "  - parameter_id: lookback",
  "",
].join("\n");

const at = (line: number, column: number): number => {
  const lines = DOC.split("\n");
  return (
    lines.slice(0, line).reduce((sum, l) => sum + l.length + 1, 0) + column
  );
};

describe("describeYamlCursor", () => {
  it("names the root mapping and the keys already present for a new top-level key", () => {
    const text = `${DOC}sig`;
    const cursor = describeYamlCursor(text, text.length);
    expect(cursor).toMatchObject({
      mode: "key",
      pointer: "",
      prefix: "sig",
      from: text.length - 3,
    });
    expect(cursor?.siblings).toEqual([
      "schema_version",
      "title",
      "risk",
      "factors",
      "parameters",
    ]);
  });

  it("walks nested mappings and excludes the sibling keys of that mapping", () => {
    const cursor = describeYamlCursor(DOC, at(4, 2) + "max_".length);
    expect(cursor).toMatchObject({
      mode: "key",
      pointer: "/risk",
      prefix: "max_",
      siblings: ["gross_exposure"],
    });
  });

  it("reports value mode with the value pointer after `key: `", () => {
    const cursor = describeYamlCursor(
      DOC,
      at(4, "  max_name_weight: 0.0".length),
    );
    expect(cursor).toMatchObject({
      mode: "value",
      pointer: "/risk/max_name_weight",
      key: "max_name_weight",
      prefix: "0.0",
    });
  });

  it("counts sequence items and the keys owned by `- key:` lines", () => {
    // typing a key inside the second graph node
    const cursor = describeYamlCursor(DOC, at(14, 12) + "in".length);
    expect(cursor).toMatchObject({
      mode: "key",
      pointer: "/factors/factors/0/graph/nodes/1",
      prefix: "in",
    });
    expect(cursor?.siblings).toEqual(["node_id", "kind"]);
    // value of a key on the item line itself
    const first = describeYamlCursor(
      DOC,
      at(15, "    - factor_id: qua".length),
    );
    expect(first).toMatchObject({
      mode: "value",
      pointer: "/factors/factors/1/factor_id",
      prefix: "qua",
    });
  });

  it("treats the cursor inside the indentation as a new key at that column", () => {
    const text = `${DOC}    `;
    const cursor = describeYamlCursor(text, text.length);
    expect(cursor).toMatchObject({
      mode: "key",
      pointer: "/parameters/0",
      prefix: "",
    });
    expect(cursor?.siblings).toEqual(["parameter_id"]);
  });

  it("handles a scalar sequence item and quoted value prefixes", () => {
    const scalar = "tags:\n  - mom";
    expect(describeYamlCursor(scalar, scalar.length)).toMatchObject({
      mode: "value",
      pointer: "/tags/0",
      key: null,
      prefix: "mom",
    });
    const quoted = 'title: "퀄';
    expect(describeYamlCursor(quoted, quoted.length)).toMatchObject({
      mode: "value",
      pointer: "/title",
      prefix: "퀄",
      from: quoted.length - 1,
    });
  });

  it("resolves sequences whose items sit at the owner key's indent", () => {
    const zero = "parameters:\n- parameter_id: x\n  ki";
    expect(describeYamlCursor(zero, zero.length)).toMatchObject({
      mode: "key",
      pointer: "/parameters/0",
      prefix: "ki",
      siblings: ["parameter_id"],
    });
    const tags = "tags:\n- a\n- b";
    expect(describeYamlCursor(tags, tags.length)).toMatchObject({
      mode: "value",
      pointer: "/tags/1",
      prefix: "b",
    });
    const nested =
      "factors:\n  factors:\n  - factor_id: m\n    graph:\n      nodes:\n      - node_id: a\n      - node_id: b\n        ki";
    expect(describeYamlCursor(nested, nested.length)).toMatchObject({
      mode: "key",
      pointer: "/factors/factors/0/graph/nodes/1",
      prefix: "ki",
      siblings: ["node_id"],
    });
  });

  it("does not mistake a '#' inside quotes for a comment", () => {
    const quoted = 'title: "a # b';
    expect(describeYamlCursor(quoted, quoted.length)).toMatchObject({
      mode: "value",
      pointer: "/title",
      prefix: "a # b",
    });
  });

  it("yields nothing inside comments and for a bare dash", () => {
    const comment = "title: a # no";
    expect(describeYamlCursor(comment, comment.length)).toBeNull();
    const dash = "tags:\n  -";
    expect(describeYamlCursor(dash, dash.length)).toBeNull();
  });

  it("escapes pointer segments and templates array indices", () => {
    const text = "a/b:\n  ";
    expect(describeYamlCursor(text, text.length)?.pointer).toBe("/a~1b");
    expect(templatePointer("/factors/factors/12/graph/nodes/0/kind")).toBe(
      "/factors/factors/*/graph/nodes/*/kind",
    );
  });
});
