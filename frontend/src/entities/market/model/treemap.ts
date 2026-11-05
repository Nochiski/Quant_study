/**
 * 섹터맵 칸 배치(squarified treemap, Bruls·Huizing·van Wijk 2000)와 작은 칸 합치기.
 *
 * 칸 크기 비율·등락은 backend 가 준 값이다. 여기서는 그 값을 카드의 실제 크기(px)에 놓기만 한다 —
 * 칸이 작아 이름과 부호 등락이 들어가지 않으면 같은 업종 안에서 "그 밖 N개"로 묶는다. 묶음조차 들어가지
 * 않으면 그 자리를 이름 없는 빈 영역(`filler`, 초점을 받지 않는다)으로 남기고 화면이 "표에서 읽어요"라고
 * 알린다 — 크기를 부풀리지 않는다. 그래서 초점을 받는 모든 칸이 이름 한 줄과 부호 등락이 들어갈 크기를
 * 가진다(색만으로 방향을 전하지 않는다 — 이름이 길어 넘치면 화면이 글자를 줄이고 줄바꿈·말줄임으로 맞춘다). 묶음의
 * 등락은 backend 와 같은 가중(`return_weight`)으로 다시 묶는다.
 */

export type Rect = { x: number; y: number; w: number; h: number };

/** 칸 하나가 될 수 있는 묶음. 크기·등락 가중은 같은 단위(원)다. */
export type MapGroup = {
  code: string;
  name: string;
  sizeWeight: number;
  returnWeight: number;
  returnPct: number | null;
};

export type MapParent = { group: MapGroup; children: readonly MapGroup[] };

export type TileKind = "leaf" | "parent" | "others" | "filler";

export type MapTile = Rect & {
  key: string;
  kind: TileKind;
  /** 칸 이름. `others` 는 묶인 수(`members`)로 화면이 문장을 만든다. */
  name: string;
  returnPct: number | null;
  /** 칸 크기 가중(유동시총, 원) — 툴팁이 읽는다. 묶음은 합이다. */
  sizeWeight: number;
  /** 누르면 들어갈 곳. `leaf`·`parent` 는 자기 코드, 업종 안 `others` 는 그 업종 코드. */
  target: string | null;
  members: number;
  /** 칸이 속한 상위 업종 이름(낭독용). */
  parentName: string | null;
};

export type MapHead = Rect & {
  key: string;
  name: string;
  returnPct: number | null;
  target: string;
};

export type MapLayout = { tiles: MapTile[]; heads: MapHead[] };

/** 이름 한 줄 + 부호 등락 한 줄이 들어가는 칸의 최소 크기(px). */
export const TILE_MIN_WIDTH = 64;
export const TILE_MIN_HEIGHT = 36;
/** 상위 업종 이름 띠 높이(px). */
export const HEAD_HEIGHT = 20;

const worst = (row: readonly number[], side: number): number => {
  const sum = row.reduce((total, area) => total + area, 0);
  return Math.max(
    ...row.map((area) =>
      Math.max(
        (side * side * area) / (sum * sum),
        (sum * sum) / (side * side * area),
      ),
    ),
  );
};

/** 값(내림차순이 보기 좋다)마다 직사각형 하나. 순서는 입력 순서 그대로다. */
export const squarify = (values: readonly number[], rect: Rect): Rect[] => {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0 || rect.w <= 0 || rect.h <= 0)
    return values.map(() => ({ ...rect, w: 0, h: 0 }));
  const scale = (rect.w * rect.h) / total;
  const areas = values.map((value) => value * scale);
  const out: Rect[] = [];
  let { x, y, w, h } = rect;
  let index = 0;
  while (index < areas.length) {
    const side = Math.min(w, h);
    const row = [areas[index]];
    index += 1;
    while (
      index < areas.length &&
      worst([...row, areas[index]], side) <= worst(row, side)
    ) {
      row.push(areas[index]);
      index += 1;
    }
    const sum = row.reduce((total, area) => total + area, 0);
    if (w >= h) {
      const width = sum / h;
      let cursor = y;
      for (const area of row) {
        out.push({ x, y: cursor, w: width, h: area / width });
        cursor += area / width;
      }
      x += width;
      w -= width;
    } else {
      const height = sum / w;
      let cursor = x;
      for (const area of row) {
        out.push({ x: cursor, y, w: area / height, h: height });
        cursor += area / height;
      }
      y += height;
      h -= height;
    }
  }
  return out;
};

/** 여러 묶음을 하나로 — 크기는 더하고 등락은 등락 가중 평균(가중이 없으면 없음). */
export const mergeGroups = (
  groups: readonly MapGroup[],
  code: string,
  name: string,
): MapGroup => {
  const sizeWeight = groups.reduce((sum, group) => sum + group.sizeWeight, 0);
  const priced = groups.filter(
    (group) => group.returnPct !== null && group.returnWeight > 0,
  );
  const returnWeight = priced.reduce(
    (sum, group) => sum + group.returnWeight,
    0,
  );
  return {
    code,
    name,
    sizeWeight,
    returnWeight,
    returnPct:
      returnWeight > 0
        ? priced.reduce(
            (sum, group) => sum + group.returnWeight * (group.returnPct ?? 0),
            0,
          ) / returnWeight
        : null,
  };
};

const fits = (rect: Rect, extra = 0): boolean =>
  rect.w >= TILE_MIN_WIDTH && rect.h >= TILE_MIN_HEIGHT + extra;

const bySize = (groups: readonly MapGroup[]): MapGroup[] =>
  [...groups].sort(
    (a, b) => b.sizeWeight - a.sizeWeight || a.code.localeCompare(b.code),
  );

type Placed = {
  group: MapGroup;
  rect: Rect;
  merged: number;
  others: boolean;
  filler: boolean;
};

/**
 * 묶음을 `rect` 에 놓되, 남긴 칸이 모두 최소 크기를 넘을 때까지 작은 것부터 "그 밖"으로 합친다. 가장 많이
 * 남길 수 있는 경우를 고르고, "그 밖" 칸이 최소 크기를 못 넘으면 빈 영역으로 둔다. 가장 큰 하나도 안
 * 들어가면 null(호출자가 상위를 한 칸으로 그린다).
 */
const placeWithMerging = (
  groups: readonly MapGroup[],
  rect: Rect,
  othersCode: string,
  extra = 0,
): Placed[] | null => {
  const sorted = bySize(groups);
  for (let keep = sorted.length; keep >= 1; keep -= 1) {
    const kept = sorted.slice(0, keep);
    const rest = sorted.slice(keep);
    const items = kept.map((group) => ({ group, merged: 1, others: false }));
    if (rest.length > 0) {
      items.push({
        group: mergeGroups(rest, othersCode, ""),
        merged: rest.length,
        others: true,
      });
    }
    const rects = squarify(
      items.map((item) => item.group.sizeWeight),
      rect,
    );
    if (
      rects.slice(0, kept.length).every((candidate) => fits(candidate, extra))
    ) {
      return items.map((item, index) => ({
        ...item,
        rect: rects[index],
        filler: item.others && !fits(rects[index], extra),
      }));
    }
  }
  return null;
};

const othersKind = (placed: Placed): TileKind =>
  placed.filler ? "filler" : "others";

/** 시장 전체 지도: 상위 업종 띠 + 세부 업종 칸. 들어가지 않는 상위 업종은 함께 묶는다. */
export const layoutSectorMap = (
  parents: readonly MapParent[],
  width: number,
  height: number,
): MapLayout => {
  const bounds: Rect = { x: 0, y: 0, w: width, h: height };
  const tiles: MapTile[] = [];
  const heads: MapHead[] = [];
  const placed =
    placeWithMerging(
      parents.map((parent) => parent.group),
      bounds,
      "__other_parents__",
      HEAD_HEIGHT,
    ) ?? [];
  const byCode = new Map(parents.map((parent) => [parent.group.code, parent]));
  for (const item of placed) {
    const { group, rect, merged } = item;
    const parent = byCode.get(group.code);
    if (parent === undefined) {
      tiles.push({
        ...rect,
        key: group.code,
        kind: othersKind(item),
        name: "",
        returnPct: group.returnPct,
        sizeWeight: group.sizeWeight,
        target: null,
        members: merged,
        parentName: null,
      });
      continue;
    }
    const inner: Rect = {
      x: rect.x,
      y: rect.y + HEAD_HEIGHT,
      w: rect.w,
      h: rect.h - HEAD_HEIGHT,
    };
    const children =
      parent.children.length > 1
        ? placeWithMerging(parent.children, inner, `${group.code}:others`)
        : null;
    if (children === null) {
      // 세부 업종이 하나뿐이거나 다 넣을 수 없다 — 상위 업종을 한 칸으로 그린다.
      tiles.push({
        ...rect,
        key: group.code,
        kind: "parent",
        name: group.name,
        returnPct: group.returnPct,
        sizeWeight: group.sizeWeight,
        target: group.code,
        members: 1,
        parentName: null,
      });
      continue;
    }
    heads.push({
      ...rect,
      key: group.code,
      name: group.name,
      returnPct: group.returnPct,
      target: group.code,
    });
    for (const child of children) {
      const others = child.others;
      tiles.push({
        ...child.rect,
        key: child.group.code,
        kind: others ? othersKind(child) : "leaf",
        name: others ? "" : child.group.name,
        returnPct: child.group.returnPct,
        sizeWeight: child.group.sizeWeight,
        target: others ? group.code : child.group.code,
        members: child.merged,
        parentName: group.name,
      });
    }
  }
  return { tiles, heads };
};

/** 한 묶음 안의 칸들(상위 업종 드릴다운, 종목 지도). 띠 없이 칸만 놓는다. */
export const layoutLeaves = (
  groups: readonly MapGroup[],
  width: number,
  height: number,
  othersTarget: string | null,
): MapTile[] => {
  const placed =
    placeWithMerging(
      groups,
      { x: 0, y: 0, w: width, h: height },
      "__others__",
    ) ?? [];
  return placed.map((item) => {
    const { group, rect, merged, others } = item;
    return {
      ...rect,
      key: group.code,
      kind: others ? othersKind(item) : "leaf",
      name: others ? "" : group.name,
      returnPct: group.returnPct,
      sizeWeight: group.sizeWeight,
      target: others ? othersTarget : group.code,
      members: merged,
      parentName: null,
    };
  });
};

/** 많이 오른·내린 세부 업종 — 아주 작은 업종이 끼지 않게 크기 하한 이상만, 각 `count` 개. */
export const sectorMovers = (
  groups: readonly MapGroup[],
  minimumSize: number,
  count: number,
): { risers: MapGroup[]; fallers: MapGroup[] } => {
  const eligible = groups.filter(
    (group) => group.returnPct !== null && group.sizeWeight >= minimumSize,
  );
  const ordered = [...eligible].sort(
    (a, b) => (b.returnPct ?? 0) - (a.returnPct ?? 0),
  );
  return {
    risers: ordered
      .filter((group) => (group.returnPct ?? 0) > 0)
      .slice(0, count),
    fallers: ordered
      .filter((group) => (group.returnPct ?? 0) < 0)
      .slice(-count)
      .reverse(),
  };
};
