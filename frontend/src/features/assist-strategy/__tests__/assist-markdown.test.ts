import { describe, expect, it } from "vitest";

import {
  parseInlines,
  parseMarkdown,
  type MarkdownInline,
} from "../model/assist-markdown";

/** 인라인 트리를 표기로 되돌려 읽기 쉽게 단언한다. */
const show = (inlines: readonly MarkdownInline[]): string =>
  inlines
    .map((inline) => {
      switch (inline.kind) {
        case "text":
          return inline.text;
        case "break":
          return "⏎";
        case "code":
          return `code(${inline.text})`;
        case "strong":
          return `strong(${show(inline.children)})`;
        case "em":
          return `em(${show(inline.children)})`;
        case "del":
          return `del(${show(inline.children)})`;
        case "link":
          return `link(${show(inline.children)}→${inline.url})`;
      }
    })
    .join("");

describe("parseInlines", () => {
  it.each([
    [
      "**보유 종목 교체 줄이기**: 거래비용",
      "strong(보유 종목 교체 줄이기): 거래비용",
    ],
    // 한글 조사가 바로 붙어도 굵게다.
    ["**모멘텀**은 오른다", "strong(모멘텀)은 오른다"],
    ["*기울임*과 ***둘 다***", "em(기울임)과 strong(em(둘 다))"],
    ["*a **b** c*", "em(a strong(b) c)"],
    ["~~취소~~", "del(취소)"],
    // 공백으로 둘러싼 별표는 곱셈 기호다.
    ["2 * 3 * 4", "2 * 3 * 4"],
    // 단어 안 밑줄은 기울임이 아니다.
    [
      "turnover_buffer_count와 regime_field_id",
      "turnover_buffer_count와 regime_field_id",
    ],
    ["_기울임_", "em(기울임)"],
    // 스트리밍 중 아직 닫히지 않은 표기는 글자다.
    ["**이어서 해볼", "**이어서 해볼"],
    ["`signal`의 `regime_field_id`", "code(signal)의 code(regime_field_id)"],
    // 코드 안의 표기는 해석하지 않는다.
    ["`**a**`", "code(**a**)"],
    ["``a ` b``", "code(a ` b)"],
    ["`닫히지 않은 코드", "`닫히지 않은 코드"],
    ["\\*별표\\*", "*별표*"],
    [
      "[KRX 자료](https://example.com/a?b=1) 참고",
      "link(KRX 자료→https://example.com/a?b=1) 참고",
    ],
    ['[제목](https://example.com "설명")', "link(제목→https://example.com)"],
    [
      "[위키](https://en.example.org/Momentum_(finance)).",
      "link(위키→https://en.example.org/Momentum_(finance)).",
    ],
    [
      "[**굵은** 링크](https://x.example)",
      "link(strong(굵은) 링크→https://x.example)",
    ],
    [
      "<https://example.com/x>",
      "link(https://example.com/x→https://example.com/x)",
    ],
    // 주소가 없는 대괄호는 글자다.
    ["[참고] 내용", "[참고] 내용"],
    ["첫 줄\n둘째 줄", "첫 줄⏎둘째 줄"],
    // 원시 HTML은 글자 그대로다.
    ["<b>굵게</b>", "<b>굵게</b>"],
  ])("%s", (source, expected) => {
    expect(show(parseInlines(source))).toBe(expected);
  });
});

describe("parseMarkdown", () => {
  it("문단·제목·구분선을 블록으로 나눈다", () => {
    const blocks = parseMarkdown(
      "## 요약\n\n첫 문단\n이어지는 줄\n\n---\n\n둘째 문단",
    );
    expect(blocks.map((block) => block.kind)).toEqual([
      "heading",
      "paragraph",
      "rule",
      "paragraph",
    ]);
    expect(blocks[0]).toMatchObject({ kind: "heading", level: 2 });
    expect(blocks[1]).toMatchObject({
      inlines: [
        { kind: "text", text: "첫 문단" },
        { kind: "break" },
        { kind: "text", text: "이어지는 줄" },
      ],
    });
  });

  it("빈 줄 없이 문단 바로 뒤에 온 목록도 목록이다", () => {
    const blocks = parseMarkdown(
      "**이어서 해볼 것**\n- 하나\n- 둘\n\n원하시는 방향을 말씀해 주세요.",
    );
    expect(blocks.map((block) => block.kind)).toEqual([
      "paragraph",
      "list",
      "paragraph",
    ]);
    expect(blocks[1]).toMatchObject({ ordered: false, items: [[{}], [{}]] });
  });

  it("번호 목록은 시작 번호를 지키고, 1이 아닌 번호 줄은 문단을 끊지 않는다", () => {
    expect(parseMarkdown("3. 셋\n4. 넷")[0]).toMatchObject({
      kind: "list",
      ordered: true,
      start: 3,
    });
    expect(parseMarkdown("기간은\n2026. 10. 25까지")).toHaveLength(1);
    expect(parseMarkdown("단계\n1. 첫째").map((block) => block.kind)).toEqual([
      "paragraph",
      "list",
    ]);
  });

  it("들여 쓴 항목은 중첩 목록이고, 빈 줄로 띄운 항목도 같은 목록이다", () => {
    const [list] = parseMarkdown(
      "1. 바깥\n   - 안쪽 하나\n   - 안쪽 둘\n\n2. 다음 바깥\n이어진 줄",
    );
    expect(list).toMatchObject({ kind: "list", ordered: true });
    if (list.kind !== "list") throw new Error("목록이 아니다");
    expect(list.items).toHaveLength(2);
    expect(list.items[0].map((block) => block.kind)).toEqual([
      "paragraph",
      "list",
    ]);
    expect(list.items[0][1]).toMatchObject({
      ordered: false,
      items: [[{}], [{}]],
    });
    // 들여 쓰지 않은 다음 줄은 항목 문단을 잇는다.
    expect(list.items[1][0]).toMatchObject({
      inlines: [
        { text: "다음 바깥" },
        { kind: "break" },
        { text: "이어진 줄" },
      ],
    });
  });

  it("종류가 다른 표기는 다른 목록이다", () => {
    expect(
      parseMarkdown("- 점\n1. 번호").map((block) =>
        block.kind === "list" ? block.ordered : null,
      ),
    ).toEqual([false, true]);
  });

  it("코드 블록 안은 해석하지 않고, 닫는 줄이 아직 없으면 끝까지 코드다", () => {
    expect(parseMarkdown("```yaml\nweight: **1**\n```\n뒤 문단")).toEqual([
      { kind: "code", language: "yaml", text: "weight: **1**" },
      { kind: "paragraph", inlines: [{ kind: "text", text: "뒤 문단" }] },
    ]);
    expect(parseMarkdown("```\n- 목록 아님\n\n# 제목 아님")).toEqual([
      { kind: "code", language: "", text: "- 목록 아님\n\n# 제목 아님" },
    ]);
  });

  it("인용은 안쪽을 다시 블록으로 읽는다", () => {
    expect(parseMarkdown("> **주의**\n> - 항목")[0]).toMatchObject({
      kind: "quote",
      blocks: [{ kind: "paragraph" }, { kind: "list" }],
    });
  });

  it("표는 정렬을 읽고 칸 수를 머리 줄에 맞춘다", () => {
    const [table] = parseMarkdown(
      "| 지표 | 값 |\n|:--|--:|\n| 샤프 | 1.2 |\n| `CAGR` |\n| a | b | 남는 칸 |",
    );
    expect(table).toMatchObject({ kind: "table", align: ["left", "right"] });
    if (table.kind !== "table") throw new Error("표가 아니다");
    expect(table.rows.map((row) => row.map(show))).toEqual([
      ["샤프", "1.2"],
      ["code(CAGR)", ""],
      ["a", "b"],
    ]);
  });

  it("구분 줄이 없으면 세로 막대가 있어도 표가 아니다", () => {
    expect(parseMarkdown("a | b\nc | d")[0].kind).toBe("paragraph");
  });

  it("CRLF 줄바꿈도 같은 블록이 된다", () => {
    expect(parseMarkdown("- 하나\r\n- 둘")).toEqual(
      parseMarkdown("- 하나\n- 둘"),
    );
  });
});
