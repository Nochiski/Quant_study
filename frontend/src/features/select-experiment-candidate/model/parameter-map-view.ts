import type { SearchAxis, CellPlateau } from "../../../shared/api";

export type HeatMetric = "score" | "plateau_score";

/** 색 영역만 투영한다. 실험 전체의 유한값을 쓰며 판정과 금융 통계를 바꾸지 않는다. */
export const mapHeatScale = (
  cells: readonly CellPlateau[],
  metric: HeatMetric,
) => {
  const values = cells
    .map((cell) => cell[metric])
    .filter(
      (value): value is number => value !== null && Number.isFinite(value),
    );
  return values.length === 0
    ? null
    : { min: Math.min(...values), max: Math.max(...values) };
};
export const mapHeatBand = (
  value: number | null,
  scale: ReturnType<typeof mapHeatScale>,
): number | null => {
  if (scale === null || value === null || !Number.isFinite(value)) return null;
  if (scale.min === scale.max) return 4;
  return (
    mapHeatBounds(scale)
      .slice(1)
      .findIndex((upper, index) => value < upper || index === 6) + 1
  );
};

/** 색 칠하기와 원수치 범례가 같은 경계를 읽어 부동소수점 끝자리 차이도 갈라지지 않게 한다. */
export const mapHeatBounds = (
  scale: NonNullable<ReturnType<typeof mapHeatScale>>,
) =>
  Array.from({ length: 8 }, (_, index) =>
    index === 0
      ? scale.min
      : index === 7
        ? scale.max
        : scale.min * ((7 - index) / 7) + scale.max * (index / 7),
  );

/** 페이지 분할과 좌표 표시는 UI 투영이며 점수·추천·파라미터 해소를 계산하지 않는다. */
export const MAP_PAGE_SIZE = 6;
export const coordinateKey = (indices: readonly number[]) =>
  JSON.stringify(indices);
export const coordinateLabel = (
  axes: readonly SearchAxis[],
  indices: readonly number[],
) =>
  axes
    .map(
      (axis, index) =>
        `${axis.parameter_id} ${String(axis.values[indices[index]])}`,
    )
    .join(" · ");

export type MapView = {
  owner: string;
  x: number;
  y: number;
  fixed: number[];
  xPage: number;
  yPage: number;
  selected: string | null;
  trial: string;
  reason: string;
  metric: HeatMetric;
};

export const initialMapView = (
  owner: string,
  axes: readonly SearchAxis[],
): MapView => ({
  owner,
  x: axes.length > 0 ? 0 : -1,
  y: axes.length > 1 ? 1 : -1,
  fixed: axes.map(() => 0),
  xPage: 0,
  yPage: 0,
  selected: null,
  trial: "",
  reason: "",
  metric: "plateau_score",
});

export const visibleAxisIndices = (
  axis: SearchAxis | undefined,
  page: number,
) =>
  axis === undefined
    ? [0]
    : axis.values
        .slice(page * MAP_PAGE_SIZE, (page + 1) * MAP_PAGE_SIZE)
        .map((_, index) => page * MAP_PAGE_SIZE + index);
