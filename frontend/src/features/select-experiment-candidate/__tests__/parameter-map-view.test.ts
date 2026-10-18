import { describe, expect, it } from "vitest";
import type { CellPlateau } from "../../../shared/api";
import { mapHeatBand, mapHeatScale } from "../model/parameter-map-view";

const cell = (score: number | null): CellPlateau => ({
  grid_index: [],
  verdict: "scored",
  score,
  plateau_score: null,
  sensitivity: null,
  sensitivity_cell: null,
});

describe("실험 전체의 표시용 상대색 범위", () => {
  it("0과 음수를 유효값으로 쓰고 결측과 비유한 값은 0으로 채우지 않는다", () => {
    const scale = mapHeatScale(
      [cell(-7), cell(0), cell(7), cell(null), cell(Number.NaN)],
      "score",
    );
    expect(scale).toEqual({ min: -7, max: 7 });
    expect(mapHeatBand(-7, scale)).toBe(1);
    expect(mapHeatBand(0, scale)).toBe(4);
    expect(mapHeatBand(7, scale)).toBe(7);
    expect(mapHeatBand(null, scale)).toBeNull();
    expect(mapHeatBand(Number.NaN, scale)).toBeNull();
  });
  it("모두 같은 실제 값은 중간 한 색으로 읽고 유효값이 없으면 색 범위도 없다", () => {
    expect(
      mapHeatBand(0.42, mapHeatScale([cell(0.42), cell(0.42)], "score")),
    ).toBe(4);
    expect(mapHeatScale([cell(null)], "score")).toBeNull();
  });
  it("고원과 칸 점수는 명시적으로 다른 범례이며 수치·판정을 변경하지 않는다", () => {
    const original = {
      ...cell(1.2),
      plateau_score: 0.01,
      verdict: "peak" as const,
    };
    expect(mapHeatScale([original], "score")).toEqual({ min: 1.2, max: 1.2 });
    expect(mapHeatScale([original], "plateau_score")).toEqual({
      min: 0.01,
      max: 0.01,
    });
    expect(original).toMatchObject({
      score: 1.2,
      plateau_score: 0.01,
      verdict: "peak",
    });
  });
});
