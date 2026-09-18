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

  it("removes the standalone comments directly above the removed item or key, and stops at a blank line (audit R3)", () => {
    // 삭제 대상 위의 주석은 대상을 설명한다(삽입 앵커 규칙의 짝) → 함께 지운다. 다음 항목의 주석은 남는다.
    const items = "a:\n  # x 설명\n  # x 둘째 줄\n  - x\n  # y 설명\n  - y\n";
    expect(ok(items, { kind: "remove", pointer: "/a/0" }).nextSource).toBe(
      "a:\n  # y 설명\n  - y\n",
    );
    expect(ok(items, { kind: "remove", pointer: "/a/1" }).nextSource).toBe(
      "a:\n  # x 설명\n  # x 둘째 줄\n  - x\n",
    );
    const keys = "a: 1\n# b 설명\nb: 2\nc: 3\n";
    expect(ok(keys, { kind: "remove", pointer: "/b" }).nextSource).toBe(
      "a: 1\nc: 3\n",
    );
    // 빈 줄로 떨어진 주석은 대상의 것이 아니다.
    const spaced = "a: 1\n# 섹션 설명\n\nb: 2\nc: 3\n";
    expect(ok(spaced, { kind: "remove", pointer: "/b" }).nextSource).toBe(
      "a: 1\n# 섹션 설명\n\nc: 3\n",
    );
    // CRLF에서도 같다.
    const crlf = "a:\r\n  # x 설명\r\n  - x\r\n  - y\r\n";
    expect(ok(crlf, { kind: "remove", pointer: "/a/0" }).nextSource).toBe(
      "a:\r\n  - y\r\n",
    );
    // 문서 첫 줄부터 시작하는 주석 블록은 파일 헤더라 첫 루트 키를 지워도 남는다(P5-01 리뷰 P2-2).
    const header = "# 전략 문서 v1.1\n# 작성자: 팀\nschema_version: '1.1'\nname: q\n";
    expect(ok(header, { kind: "remove", pointer: "/schema_version" }).nextSource).toBe(
      "# 전략 문서 v1.1\n# 작성자: 팀\nname: q\n",
    );
  });

  it("empties an inner sequence whose only item is a quoted scalar containing `#` (review P2-1)", () => {
    const source = 'zq:\n  l5:\n    - - "#tag"\n';
    expect(ok(source, { kind: "remove", pointer: "/zq/l5/0/0" }).nextSource).toBe(
      "zq:\n  l5:\n    - []\n",
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

describe("nested inner first item removal keeps the comment between items (P3-01 2nd review P2-R1)", () => {
  it("pulls the next line's content up to the outer dash, comment included", () => {
    expect(
      ok("a:\n  - - x\n    # y 설명\n    - y\n", {
        kind: "remove",
        pointer: "/a/0/0",
      }).nextSource,
    ).toBe("a:\n  - # y 설명\n    - y\n");
    expect(
      ok("a:\n  - - x\n    - y\n", { kind: "remove", pointer: "/a/0/0" })
        .nextSource,
    ).toBe("a:\n  - - y\n");
  });
});

describe("insert-key before a sibling, empty values and empty documents (P3-02)", () => {
  it("inserts after the previous sibling so the comment above `before` keeps describing it", () => {
    const source = "a: 1\n# b를 설명\nb: 2\nc: 3\n";
    expect(
      ok(source, {
        kind: "insert-key",
        parentPointer: "",
        key: "x",
        value: 0,
        before: "b",
      }).nextSource,
    ).toBe("a: 1\nx: 0\n# b를 설명\nb: 2\nc: 3\n");
    expect(
      ok(source, {
        kind: "insert-key",
        parentPointer: "",
        key: "x",
        value: 0,
        before: "a",
      }).nextSource,
    ).toBe("x: 0\na: 1\n# b를 설명\nb: 2\nc: 3\n");
    const nested = "m:\n  p: 1\n  q: 2\nz: 0\n";
    expect(
      ok(nested, {
        kind: "insert-key",
        parentPointer: "/m",
        key: "x",
        value: 0,
        before: "p",
      }).nextSource,
    ).toBe("m:\n  x: 0\n  p: 1\n  q: 2\nz: 0\n");
    expect(
      ok(nested, {
        kind: "insert-key",
        parentPointer: "/m",
        key: "x",
        value: 0,
        before: "q",
      }).nextSource,
    ).toBe("m:\n  p: 1\n  x: 0\n  q: 2\nz: 0\n");
    expect(
      plan(nested, {
        kind: "insert-key",
        parentPointer: "/m",
        key: "x",
        value: 0,
        before: "nope",
      }),
    ).toEqual({ status: "error", reason: "not-found" });
  });

  it("takes the dash position when `before` is the first key of a sequence item", () => {
    const source = "a:\n  - p: 1\n    q: 2\n";
    expect(
      ok(source, {
        kind: "insert-key",
        parentPointer: "/a/0",
        key: "x",
        value: 0,
        before: "p",
      }).nextSource,
    ).toBe("a:\n  - x: 0\n    p: 1\n    q: 2\n");
  });

  it("opens a block container under a value-less key, keeping its end-of-line comment", () => {
    expect(
      ok("factors:\nz: 1\n", {
        kind: "insert-item",
        parentPointer: "/factors",
        value: { id: "a" },
      }).nextSource,
    ).toBe("factors:\n  - id: a\nz: 1\n");
    expect(
      ok("risk: # 비움\nz: 1\n", {
        kind: "insert-key",
        parentPointer: "/risk",
        key: "k",
        value: 1,
      }).nextSource,
    ).toBe("risk: # 비움\n  k: 1\nz: 1\n");
    expect(
      ok("a:\n  - b:\n", {
        kind: "insert-item",
        parentPointer: "/a/0/b",
        value: 1,
      }).nextSource,
    ).toBe("a:\n  - b:\n      - 1\n");
    expect(
      plan("factors:\n", {
        kind: "insert-item",
        parentPointer: "/factors",
        value: 1,
        index: 1,
      }),
    ).toEqual({ status: "error", reason: "not-found" });
  });

  it("treats a blank or comment-only document as an empty root mapping for a root insert-key", () => {
    const op: SourceOperation = {
      kind: "insert-key",
      parentPointer: "",
      key: "signal",
      value: { a: 1 },
    };
    for (const [source, expected] of [
      ["", "signal:\n  a: 1"],
      ["  \n", "signal:\n  a: 1"],
      ["# 머리말\n", "# 머리말\nsignal:\n  a: 1"],
      ["{}", "signal:\n  a: 1"],
    ] as const) {
      const result = plan(source, op);
      expect(result.status).toBe("ok");
      if (result.status === "ok") expect(result.edit.nextSource).toBe(expected);
    }
    expect(
      plan("", { kind: "insert-item", parentPointer: "", value: 1 }),
    ).toEqual({
      status: "error",
      reason: "parse",
    });
    expect(
      plan("", { kind: "insert-key", parentPointer: "/x", key: "k", value: 1 }),
    ).toEqual({
      status: "error",
      reason: "parse",
    });
  });
});

describe("review follow-up: item anchors, anchor option, parent line comments (P3-02)", () => {
  it("inserts an item after the previous one so the comment above the target keeps describing it", () => {
    expect(
      ok("a:\n  # about 0\n  - x\n  - y\n", {
        kind: "insert-item",
        parentPointer: "/a",
        value: "n",
        index: 0,
      }).nextSource,
    ).toBe("a:\n  - n\n  # about 0\n  - x\n  - y\n");
    expect(
      ok("a:\n  - x\n  # about 1\n  - y\n", {
        kind: "insert-item",
        parentPointer: "/a",
        value: "n",
        index: 1,
      }).nextSource,
    ).toBe("a:\n  - x\n  - n\n  # about 1\n  - y\n");
    expect(
      ok("m:\n  # about p\n  p: 1\nz: 0\n", {
        kind: "insert-key",
        parentPointer: "/m",
        key: "x",
        value: 0,
        before: "p",
      }).nextSource,
    ).toBe("m:\n  x: 0\n  # about p\n  p: 1\nz: 0\n");
  });

  it("honours an anchor offset inside the sibling window and ignores one outside it", () => {
    const source = "a: 1\n# 머리말\n\nb: 2\n";
    const at = source.indexOf("\nb: 2") + 1; // 빈 줄 뒤, `b` 줄 시작
    expect(
      planSourceOperation(
        source,
        "yaml",
        {
          kind: "insert-key",
          parentPointer: "",
          key: "x",
          value: 0,
          before: "b",
        },
        { anchor: at },
      ),
    ).toMatchObject({
      status: "ok",
      edit: { nextSource: "a: 1\n# 머리말\n\nx: 0\nb: 2\n" },
    });
    // `b` 뒤는 `before: b`의 허용 구간 밖 → 기본 앵커(앞 형제 `a` 줄 끝 뒤).
    expect(
      planSourceOperation(
        source,
        "yaml",
        {
          kind: "insert-key",
          parentPointer: "",
          key: "x",
          value: 0,
          before: "b",
        },
        { anchor: source.length },
      ),
    ).toMatchObject({
      status: "ok",
      edit: { nextSource: "a: 1\nx: 0\n# 머리말\n\nb: 2\n" },
    });
    // 문서 끝(EOL 없음)에 앵커: 줄바꿈을 앞에 붙여 붙인다.
    expect(
      planSourceOperation(
        "a: 1",
        "yaml",
        { kind: "insert-key", parentPointer: "", key: "x", value: 0 },
        { anchor: 4 },
      ),
    ).toMatchObject({ status: "ok", edit: { nextSource: "a: 1\nx: 0" } });
    expect(
      planSourceOperation(
        "s:\n  - 1\n  # 둘째 설명\n\n  - 2\n",
        "yaml",
        { kind: "insert-item", parentPointer: "/s", value: 9, index: 1 },
        { anchor: "s:\n  - 1\n  # 둘째 설명\n".length },
      ),
    ).toMatchObject({
      status: "ok",
      edit: { nextSource: "s:\n  - 1\n  # 둘째 설명\n  - 9\n\n  - 2\n" },
    });
  });

  it("keeps the parent line's end-of-line comment when the last child is removed", () => {
    expect(
      ok("a: # note\n  b: 1\nz: 2\n", { kind: "remove", pointer: "/a/b" })
        .nextSource,
    ).toBe("a: # note\n  {}\nz: 2\n");
    expect(
      ok("a: # note\n  - 1\nz: 2\n", { kind: "remove", pointer: "/a/0" })
        .nextSource,
    ).toBe("a: # note\n  []\nz: 2\n");
    expect(
      ok("a:\n  b: 1\nz: 2\n", { kind: "remove", pointer: "/a/b" }).nextSource,
    ).toBe("a: {}\nz: 2\n");
  });

  it("rejects `before` on a value-less parent in the tree operation as the text side does", () => {
    expect(
      applyToTree(
        { a: null },
        {
          kind: "insert-key",
          parentPointer: "/a",
          key: "x",
          value: 9,
          before: "q",
        },
      ),
    ).toBeNull();
    expect(
      applyToTree(
        { a: null },
        { kind: "insert-key", parentPointer: "/a", key: "x", value: 9 },
      ),
    ).toEqual({
      a: { x: 9 },
    });
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
