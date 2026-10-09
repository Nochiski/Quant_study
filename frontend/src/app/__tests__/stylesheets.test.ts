import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * 스타일시트 블록 괄호 균형 가드. Vite 번들은 모든 CSS를 한 파일로 잇고 esbuild는 닫히지 않은 블록을
 * 파일 끝까지의 **중첩 규칙**으로 읽는다 — 한 파일에서 `}` 하나가 빠지면 그 뒤 모듈의 규칙 전부가
 * 그 선택자 아래로 들어가 앱 대부분의 스타일이 사라진다(P4-03 e2e에서 실측: 백테스트 결과 배경이
 * 투명, 네비게이션 무스타일). 타입체크·Vitest·lint 어디도 CSS 문법을 보지 않으므로 여기서 잡는다.
 * 괄호 **개수 균형만** 본다: 누락과 잉여가 같은 파일에서 상쇄되면 통과하고, 따옴표 없는 `url()` 안의
 * 괄호는 오탐한다(리뷰 P2-4). 실제로 났던 결함 형태(블록 안에서 다음 선택자 시작)는 잡는다.
 */
const SRC = path.resolve(__dirname, "..", "..");

const stylesheets = (): string[] =>
  readdirSync(SRC, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".css"))
    .map((entry) => path.join(entry.parentPath, entry.name))
    .sort();

/** 주석·문자열을 제거한 뒤 `{`/`}` 깊이를 따라간다. 음수가 되거나 0으로 끝나지 않으면 위치를 돌려준다. */
const braceImbalance = (text: string): string | null => {
  const stripped = text
    .replace(/\/\*[\s\S]*?\*\//gu, (comment) => comment.replace(/[^\n]/gu, " "))
    .replace(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/gu, (literal) =>
      " ".repeat(literal.length),
    );
  let depth = 0;
  let line = 1;
  for (const char of stripped) {
    if (char === "\n") line += 1;
    else if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth < 0) return `line ${line}: 여는 괄호 없이 닫힘`;
    }
  }
  return depth === 0 ? null : `파일 끝: 닫히지 않은 블록 ${depth}개`;
};

describe("stylesheets", () => {
  it("finds the feature stylesheets", () => {
    expect(stylesheets().length).toBeGreaterThan(0);
  });

  it.each(stylesheets().map((file) => [path.relative(SRC, file), file]))(
    "%s has balanced blocks",
    (_name, file) => {
      expect(braceImbalance(readFileSync(file, "utf8"))).toBeNull();
    },
  );

  it("rejects an unclosed block and an unmatched close", () => {
    expect(braceImbalance(".a {\n  color: red;\n.b {\n}\n")).toBe(
      "파일 끝: 닫히지 않은 블록 1개",
    );
    expect(braceImbalance(".a { }\n}\n")).toBe("line 2: 여는 괄호 없이 닫힘");
    expect(braceImbalance('.a { content: "}"; /* { */ }\n')).toBeNull();
  });
});
