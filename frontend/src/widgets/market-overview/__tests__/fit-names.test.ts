import { describe, expect, it } from "vitest";

import { fitNames, nameOverflows, type NameMeasure } from "../model/fit-names";

// 실데이터 화면의 큰 칸(956×378)에서 잰 한 줄 이름: 16px 글자, 줄 높이 19.2px.
const oneLine: NameMeasure = {
  nameWidthOverflow: 0,
  nameHeight: 19.2,
  lineHeight: 19.2,
  contentHeight: 19.2 + 2 + 15.6,
  innerHeight: 374,
};

const tileWithName = (hidden: boolean) => {
  const root = document.createElement("div");
  const tile = document.createElement("button");
  tile.className = "market-map__tile market-map__tile--large";
  Object.defineProperty(tile, "clientWidth", { value: 956 });
  Object.defineProperty(tile, "clientHeight", { value: 378 });
  const name = document.createElement("span");
  name.className = "market-map__name";
  name.textContent = "반도체와반도체장비";
  name.hidden = hidden;
  tile.append(name);
  root.append(tile);
  return { root, tile, name };
};

describe("nameOverflows", () => {
  it("keeps a big tile's one- or two-line name, allowing sub-pixel layout", () => {
    expect(nameOverflows(oneLine)).toBe(false);
    expect(nameOverflows({ ...oneLine, nameHeight: 38.4, nameWidthOverflow: 1 })).toBe(false);
  });

  it("reads a third line, a word wider than the tile, or a too-short tile as overflowing", () => {
    expect(nameOverflows({ ...oneLine, nameHeight: 57.6 })).toBe(true);
    expect(nameOverflows({ ...oneLine, nameWidthOverflow: 26 })).toBe(true);
    expect(nameOverflows({ ...oneLine, innerHeight: 30 })).toBe(true);
  });
});

describe("fitNames", () => {
  it("shows the name of a big tile that fits", () => {
    const { root, tile, name } = tileWithName(false);
    fitNames(root, () => oneLine);
    expect(name.hidden).toBe(false);
    expect(tile.dataset.fit).toBeUndefined();
  });

  it("unhides a name hidden by an earlier measure once it fits again", () => {
    const { root, tile, name } = tileWithName(true);
    tile.dataset.fit = "regular";
    // 숨긴 채로 재면 상자가 0 이라 판정이 틀린다 — 측정은 언제나 숨김을 푼 뒤에 일어나야 한다
    fitNames(root, (_tile, measured) =>
      measured.hidden ? { ...oneLine, nameHeight: 0, innerHeight: 0, contentHeight: 99 } : oneLine,
    );
    expect(name.hidden).toBe(false);
    expect(tile.dataset.fit).toBeUndefined();
  });

  it("tries the regular size before hiding a large tile's name", () => {
    const { root, tile, name } = tileWithName(false);
    const wide = { ...oneLine, nameWidthOverflow: 20 };
    fitNames(root, (measured) => (measured.dataset.fit === "regular" ? oneLine : wide));
    expect(tile.dataset.fit).toBe("regular");
    expect(name.hidden).toBe(false);
    fitNames(root, () => wide);
    expect(name.hidden).toBe(true);
  });
});
