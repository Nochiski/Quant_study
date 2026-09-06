import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(
  resolve(process.cwd(), "src/entities/backtest/ui/backtest-run-detail.css"),
  "utf8",
);

describe("백테스트 결과 CSS 소유권", () => {
  it("간격·글꼴·모서리 값을 semantic token으로만 지정한다", () => {
    const ownedProperties =
      /^(?:gap|padding(?:-[a-z-]+)?|margin(?:-[a-z-]+)?|font-size|letter-spacing|border-radius|min-height|max-height|height|grid-template-columns):/u;
    const rawUnit = /[0-9]*\.?[0-9]+(?:px|rem|em)/u;
    const declarations = css.match(/^\s*[a-z-]+:[^;]+;/gmu) ?? [];
    const violations = declarations
      .map((declaration) => declaration.trim())
      .filter(
        (declaration) =>
          ownedProperties.test(declaration) && rawUnit.test(declaration),
      );

    expect(violations).toEqual([]);
  });
});
