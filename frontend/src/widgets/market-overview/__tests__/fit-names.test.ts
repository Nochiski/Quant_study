import { describe, expect, it } from "vitest";

import {
  chooseFit,
  fitNames,
  nameOverflows,
  type FitStep,
  type NameMeasure,
} from "../model/fit-names";

// 실데이터 화면의 큰 칸(956×378)에서 잰 한 줄 이름: 16px 글자, 줄 높이 19.2px.
const oneLine: NameMeasure = {
  nameWidthOverflow: 0,
  nameHeight: 19.2,
  lineHeight: 19.2,
  changeHeight: 15.6,
  gap: 2,
  innerHeight: 374,
};

// 좁은 칸(81×58, 안쪽 높이 54): 작은 글자 줄 높이 13.2px, 등락 14.4px, 간격 1px.
const narrow = (step: FitStep): NameMeasure => ({
  nameWidthOverflow: step === "break" ? 0 : 26,
  nameHeight: step === "break" ? 26.4 : 13.2,
  lineHeight: step === "large" ? 19.2 : step === "regular" ? 14.4 : 13.2,
  changeHeight: 14.4,
  gap: 1,
  innerHeight: 54,
});

describe("nameOverflows", () => {
  it("keeps a one- or two-line name with sub-pixel layout, and limits only word-wrapped steps to two lines", () => {
    expect(nameOverflows(oneLine, "large")).toBe(false);
    expect(nameOverflows({ ...oneLine, nameHeight: 38.4, nameWidthOverflow: 1 }, "regular")).toBe(false);
    expect(nameOverflows({ ...oneLine, nameHeight: 57.6 }, "regular")).toBe(true);
    expect(nameOverflows({ ...oneLine, nameHeight: 57.6 }, "break")).toBe(false);
  });

  it("reads a word wider than the tile or a too-short tile as overflowing", () => {
    expect(nameOverflows({ ...oneLine, nameWidthOverflow: 26 }, "small")).toBe(true);
    expect(nameOverflows({ ...oneLine, innerHeight: 30 }, "break")).toBe(true);
  });
});

describe("chooseFit", () => {
  it("keeps the large size when the name fits a big tile", () => {
    expect(chooseFit(true, () => oneLine)).toEqual({ step: "large", lines: null, hideChange: false });
  });

  it("steps down through regular and small sizes before breaking inside a word", () => {
    const tried: FitStep[] = [];
    const fit = chooseFit(false, (step) => {
      tried.push(step);
      return narrow(step);
    });
    expect(tried).toEqual(["regular", "small", "break"]);
    expect(fit).toEqual({ step: "break", lines: null, hideChange: false });
  });

  it("clamps to the lines that fit beside the change when even syllable breaks overflow", () => {
    // 음절 줄바꿈으로 네 줄(52.8px)이 되는 이름 — 등락과 함께면 안쪽 54px 에 세 줄까지
    const tall = (step: FitStep) => ({ ...narrow(step), nameWidthOverflow: 0, nameHeight: 52.8 });
    expect(chooseFit(false, tall)).toEqual({ step: "clamp", lines: 3, hideChange: false });
  });

  it("puts the name first and hides the change when a tile cannot hold one line of each", () => {
    // 안쪽 높이 20px: 이름 한 줄(13.2) + 간격 + 등락(14.4) 은 안 들어간다
    const low = (step: FitStep) => ({ ...narrow(step), innerHeight: 20 });
    expect(chooseFit(false, low)).toEqual({ step: "clamp", lines: 1, hideChange: true });
  });
});

describe("fitNames", () => {
  const tileWithName = (large: boolean) => {
    const root = document.createElement("div");
    const tile = document.createElement("button");
    tile.className = `market-map__tile${large ? " market-map__tile--large" : ""}`;
    Object.defineProperty(tile, "clientWidth", { value: 81 });
    Object.defineProperty(tile, "clientHeight", { value: 58 });
    const name = document.createElement("span");
    name.className = "market-map__name";
    name.textContent = "소프트웨어와서비스";
    const change = document.createElement("span");
    change.textContent = "+0.24%";
    tile.append(name, change);
    root.append(tile);
    return { root, tile, name, change };
  };

  it("never hides the name and undoes an earlier fit before measuring again", () => {
    const { root, tile, name, change } = tileWithName(false);
    fitNames(root, () => ({ ...narrow("break"), innerHeight: 20 }));
    expect(name.hidden).toBe(false);
    expect(change.hidden).toBe(true);
    expect(tile.dataset.fit).toBe("clamp");
    expect(name.style.getPropertyValue("--name-lines")).toBe("1");
    // 칸이 넉넉해지면 앞선 말줄임·등락 숨김이 남지 않는다
    fitNames(root, () => oneLine);
    expect(change.hidden).toBe(false);
    expect(tile.dataset.fit).toBe("regular");
    expect(name.style.getPropertyValue("--name-lines")).toBe("");
  });

  it("measures each step with that step applied", () => {
    const { root, tile } = tileWithName(true);
    const seen: (string | undefined)[] = [];
    fitNames(root, (measured) => {
      seen.push(measured.dataset.fit);
      return measured.dataset.fit === "small" ? oneLine : { ...oneLine, nameWidthOverflow: 20 };
    });
    expect(seen).toEqual([undefined, "regular", "small"]);
    expect(tile.dataset.fit).toBe("small");
  });
});
