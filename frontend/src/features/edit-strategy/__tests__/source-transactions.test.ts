import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  applyToTree,
  planSourceOperation,
  type SourceOperation,
} from "../model/source-transactions";

const GOLDEN = readBackendFixture(
  "strategy_documents/quality_momentum.yaml",
).replace(/\r\n?/g, "\n");

const plan = (source: string, op: SourceOperation) =>
  planSourceOperation(source, "yaml", op);

const ok = (source: string, op: SourceOperation) => {
  const result = plan(source, op);
  if (result.status !== "ok")
    throw new Error(`expected ok, got ${result.reason}`);
  const reparsed = parseSource(result.edit.nextSource, "yaml");
  if (reparsed.status !== "ok") throw new Error("next source must parse");
  const tree = parseSource(source, "yaml");
  if (tree.status !== "ok") throw new Error("source must parse");
  expect(reparsed.tree).toEqual(applyToTree(tree.tree, op));
  return result.edit;
};

describe("replace-scalar", () => {
  it("replaces only the value range and quotes strings that would read back as another type", () => {
    const source =
      'schema_version: "1.1"\ntitle: 모멘텀   # 주석\nrisk:\n  max_name_weight: 0.05\n';
    const edit = ok(source, {
      kind: "replace-scalar",
      pointer: "/risk/max_name_weight",
      value: 0.1,
    });
    expect(edit.nextSource).toBe(
      'schema_version: "1.1"\ntitle: 모멘텀   # 주석\nrisk:\n  max_name_weight: 0.1\n',
    );
    expect(edit.selection).toEqual({ from: edit.from + 3, to: edit.from + 3 });
    // 문자열 "1.0"·"yes"·빈 문자열은 plain으로 쓰면 다른 타입/빈 값이 되므로 따옴표를 얻는다.
    for (const value of ["1.0", "", "a: b", "line\nbreak"]) {
      const next = ok(source, {
        kind: "replace-scalar",
        pointer: "/title",
        value,
      }).nextSource;
      expect(next.split("\n")[1]).toMatch(/^title: ["'].*["']\s+# 주석$/);
    }
    expect(
      ok(source, { kind: "replace-scalar", pointer: "/title", value: null })
        .nextSource,
    ).toContain("title: null   # 주석");
  });

  it("refuses non-scalar targets and unknown pointers", () => {
    expect(
      plan(GOLDEN, { kind: "replace-scalar", pointer: "/risk", value: 1 }),
    ).toEqual({ status: "error", reason: "not-scalar" });
    expect(
      plan(GOLDEN, { kind: "replace-scalar", pointer: "/nope", value: 1 }),
    ).toEqual({ status: "error", reason: "not-found" });
    expect(
      planSourceOperation("{}", "json", {
        kind: "replace-scalar",
        pointer: "/a",
        value: 1,
      }),
    ).toEqual({ status: "error", reason: "yaml-only" });
    expect(
      plan("title: [broken\n", {
        kind: "replace-scalar",
        pointer: "/title",
        value: 1,
      }),
    ).toEqual({ status: "error", reason: "parse" });
  });
});

describe("insert-key", () => {
  it("appends `key: value` after the parent's last line with the parent's child indentation", () => {
    const source =
      "risk:\n  max_name_weight: 0.05\n# 다음 섹션 설명\nexecution:\n  fee_bps: 15\n";
    const edit = ok(source, {
      kind: "insert-key",
      parentPointer: "/risk",
      key: "sector_neutral",
      value: true,
    });
    expect(edit.nextSource).toBe(
      "risk:\n  max_name_weight: 0.05\n  sector_neutral: true\n# 다음 섹션 설명\nexecution:\n  fee_bps: 15\n",
    );
  });

  it("turns a flow `{}` into a block mapping, appends at the root, and nests structured values", () => {
    const flow = "signal: {}\nrisk:\n  max_name_weight: 0.05\n";
    expect(
      ok(flow, {
        kind: "insert-key",
        parentPointer: "/signal",
        key: "score_threshold",
        value: 1.5,
      }).nextSource,
    ).toBe("signal:\n  score_threshold: 1.5\nrisk:\n  max_name_weight: 0.05\n");
    expect(
      ok("title: t", {
        kind: "insert-key",
        parentPointer: "",
        key: "risk",
        value: { max_name_weight: 0.1 },
      }).nextSource,
    ).toBe("title: t\nrisk:\n  max_name_weight: 0.1");
    expect(
      plan(flow, {
        kind: "insert-key",
        parentPointer: "/risk",
        key: "max_name_weight",
        value: 1,
      }),
    ).toEqual({ status: "error", reason: "exists" });
    expect(
      plan(flow, {
        kind: "insert-key",
        parentPointer: "/risk/max_name_weight",
        key: "x",
        value: 1,
      }),
    ).toEqual({ status: "error", reason: "not-mapping" });
  });
});

describe("insert-item", () => {
  it("appends or inserts `- item` at the sequence's item indentation and expands `[]`", () => {
    const source =
      "factors:\n  - factor_id: a\n    weight: 1\n  - factor_id: b\nparameters: []\n";
    expect(
      ok(source, {
        kind: "insert-item",
        parentPointer: "/factors",
        value: { factor_id: "c" },
      }).nextSource,
    ).toBe(
      "factors:\n  - factor_id: a\n    weight: 1\n  - factor_id: b\n  - factor_id: c\nparameters: []\n",
    );
    expect(
      ok(source, {
        kind: "insert-item",
        parentPointer: "/factors",
        value: { factor_id: "c" },
        index: 1,
      }).nextSource,
    ).toBe(
      "factors:\n  - factor_id: a\n    weight: 1\n  - factor_id: c\n  - factor_id: b\nparameters: []\n",
    );
    expect(
      ok(source, {
        kind: "insert-item",
        parentPointer: "/parameters",
        value: { parameter_id: "p" },
      }).nextSource,
    ).toBe(
      "factors:\n  - factor_id: a\n    weight: 1\n  - factor_id: b\nparameters:\n  - parameter_id: p\n",
    );
    expect(
      plan(source, {
        kind: "insert-item",
        parentPointer: "/factors/0",
        value: 1,
      }),
    ).toEqual({ status: "error", reason: "not-sequence" });
  });
});

describe("remove", () => {
  it("deletes the key line through the value's last line and keeps emptied containers as `{}`/`[]`", () => {
    const source =
      "title: t\nrisk:\n  max_name_weight: 0.05\n  sector_neutral: true\nfactors:\n  - factor_id: a\n";
    expect(
      ok(source, { kind: "remove", pointer: "/risk/sector_neutral" })
        .nextSource,
    ).toBe(
      "title: t\nrisk:\n  max_name_weight: 0.05\nfactors:\n  - factor_id: a\n",
    );
    expect(
      ok(source, { kind: "remove", pointer: "/factors/0" }).nextSource,
    ).toBe(
      "title: t\nrisk:\n  max_name_weight: 0.05\n  sector_neutral: true\nfactors: []\n",
    );
    const one = "title: t\nsignal:\n  score_threshold: 1\n";
    expect(
      ok(one, { kind: "remove", pointer: "/signal/score_threshold" })
        .nextSource,
    ).toBe("title: t\nsignal: {}\n");
    expect(ok("a: 1\nb: 2", { kind: "remove", pointer: "/b" }).nextSource).toBe(
      "a: 1",
    );
    expect(plan(source, { kind: "remove", pointer: "/risk/none" })).toEqual({
      status: "error",
      reason: "not-found",
    });
  });

  it("keeps CRLF documents CRLF", () => {
    const source = "title: t\r\nrisk:\r\n  max_name_weight: 0.05\r\n";
    const next = ok(source, {
      kind: "insert-key",
      parentPointer: "/risk",
      key: "sector_neutral",
      value: false,
    }).nextSource;
    expect(next).toBe(
      "title: t\r\nrisk:\r\n  max_name_weight: 0.05\r\n  sector_neutral: false\r\n",
    );
  });
});

describe("nested sequences and dash-line keys", () => {
  it("removes an inner item without eating the outer dash, and inserts at an inner index", () => {
    const source = "a:\n  - - x\n    - y\n  - - z\n";
    expect(ok(source, { kind: "remove", pointer: "/a/0/0" }).nextSource).toBe(
      "a:\n  - - y\n  - - z\n",
    );
    expect(ok(source, { kind: "remove", pointer: "/a/0/1" }).nextSource).toBe(
      "a:\n  - - x\n  - - z\n",
    );
    expect(
      ok(source, {
        kind: "insert-item",
        parentPointer: "/a/0",
        value: "w",
        index: 0,
      }).nextSource,
    ).toBe("a:\n  - - w\n    - x\n    - y\n  - - z\n");
    expect(ok(source, { kind: "remove", pointer: "/a/1/0" }).nextSource).toBe(
      "a:\n  - - x\n    - y\n  - []\n",
    );
  });

  it("removes the first key of a sequence-item mapping while keeping the dash", () => {
    const source =
      "factors:\n  - factor_id: a\n    weight: 1\n  - factor_id: b\n";
    expect(
      ok(source, { kind: "remove", pointer: "/factors/0/factor_id" })
        .nextSource,
    ).toBe("factors:\n  - weight: 1\n  - factor_id: b\n");
    expect(
      ok(source, { kind: "remove", pointer: "/factors/0/weight" }).nextSource,
    ).toBe("factors:\n  - factor_id: a\n  - factor_id: b\n");
    expect(
      ok(source, { kind: "remove", pointer: "/factors/1/factor_id" })
        .nextSource,
    ).toBe("factors:\n  - factor_id: a\n    weight: 1\n  - {}\n");
  });
});

describe("comments and block scalars (review P1-1·P1-2)", () => {
  it("keeps the comment that follows a block scalar when removing, inserting or replacing around it", () => {
    const source = "a: |\n  l1\n  l2\n# b 설명\nb: 2\n";
    expect(ok(source, { kind: "remove", pointer: "/a" }).nextSource).toBe(
      "# b 설명\nb: 2\n",
    );
    expect(
      ok(source, { kind: "insert-key", parentPointer: "", key: "c", value: 3 })
        .nextSource,
    ).toBe("a: |\n  l1\n  l2\n# b 설명\nb: 2\nc: 3\n");
    expect(
      ok(source, { kind: "replace-scalar", pointer: "/a", value: "x" })
        .nextSource,
    ).toBe("a: x\n# b 설명\nb: 2\n");
    const nested = "m:\n  a: 1\n  b: |\n    l1\n# z 설명\nz: 0\n";
    expect(
      ok(nested, {
        kind: "insert-key",
        parentPointer: "/m",
        key: "c",
        value: 3,
      }).nextSource,
    ).toBe("m:\n  a: 1\n  b: |\n    l1\n  c: 3\n# z 설명\nz: 0\n");
    expect(
      ok("a: |\n  l1\n", { kind: "replace-scalar", pointer: "/a", value: "x" })
        .nextSource,
    ).toBe("a: x\n");
  });

  it("removes a sequence item or a dash-line key without deleting the comment that describes the next one", () => {
    const items = "a:\n  - x\n  # y 설명\n  - y\nb: 1\n";
    expect(ok(items, { kind: "remove", pointer: "/a/0" }).nextSource).toBe(
      "a:\n  # y 설명\n  - y\nb: 1\n",
    );
    const keys = "a:\n  - k: 1\n    # m 설명\n    m: 2\n";
    expect(ok(keys, { kind: "remove", pointer: "/a/0/k" }).nextSource).toBe(
      "a:\n  - # m 설명\n    m: 2\n",
    );
    const nested = "a:\n  - - x\n  # 다음 설명\n  - - z\n";
    expect(ok(nested, { kind: "remove", pointer: "/a/0" }).nextSource).toBe(
      "a:\n  # 다음 설명\n  - - z\n",
    );
  });

  it("expands `- []` and `- {}` without leaving a trailing space", () => {
    expect(
      ok("a:\n  - []\n", {
        kind: "insert-item",
        parentPointer: "/a/0",
        value: 1,
      }).nextSource,
    ).toBe("a:\n  -\n    - 1\n");
    expect(
      ok("a:\n  - {}\n", {
        kind: "insert-key",
        parentPointer: "/a/0",
        key: "k",
        value: 1,
      }).nextSource,
    ).toBe("a:\n  -\n    k: 1\n");
  });
});

describe("golden document", () => {
  it("round-trips every scalar of the 1.1 golden through replace-scalar with the same value", () => {
    const parsed = parseSource(GOLDEN, "yaml");
    if (parsed.status !== "ok") throw new Error("golden must parse");
    for (const [pointer, range] of parsed.valueRanges) {
      const text = GOLDEN.slice(range.start.offset, range.end.offset);
      if (text.includes("\n") || text.startsWith("[") || text.startsWith("{"))
        continue;
      let current: unknown = parsed.tree;
      for (const segment of pointer.slice(1).split("/"))
        current = (current as Record<string, unknown>)[segment];
      if (typeof current === "object") continue;
      const edit = ok(GOLDEN, {
        kind: "replace-scalar",
        pointer,
        value: current as never,
      });
      expect(parseSource(edit.nextSource, "yaml").tree).toEqual(parsed.tree);
    }
  });
});
