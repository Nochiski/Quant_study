import type { SectorGroupBody, SectorMapBody } from "../../../shared/api";

import type { MapGroup, MapParent } from "./treemap";

export const toMapGroup = (group: SectorGroupBody): MapGroup => ({
  code: group.code,
  name: group.name,
  sizeWeight: group.size_weight_krw,
  returnWeight: group.return_weight_krw,
  returnPct: group.return_pct ?? null,
});

export const toMapParents = (sectorMap: SectorMapBody): MapParent[] =>
  sectorMap.parents.map((parent) => ({
    group: toMapGroup(parent.group),
    children: parent.children.map(toMapGroup),
  }));

/** 드릴다운 경로의 코드가 상위 업종인지 세부 업종인지 응답에서 찾는다. 없으면 null. */
export const findSector = (
  sectorMap: SectorMapBody,
  code: string,
): { parent: SectorGroupBody; child: SectorGroupBody | null } | null => {
  for (const parent of sectorMap.parents) {
    if (parent.group.code === code)
      return { parent: parent.group, child: null };
    const child = parent.children.find((candidate) => candidate.code === code);
    if (child !== undefined) return { parent: parent.group, child };
  }
  return null;
};
