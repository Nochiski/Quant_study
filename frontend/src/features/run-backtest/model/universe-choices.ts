import type { UniverseDefinition } from "../../../entities/dataset";

/**
 * 실행 설정 유니버스 칸이 지금 무엇을 그리는가. 선택지·표시명·설명의 owner 는 연결된 시장 데이터
 * 어댑터(`GET /api/v1/equity/universes`)이고, 여기서는 그 답을 칸의 모양으로만 옮긴다.
 *
 * - `loading`: 목록을 기다린다. 드롭다운 그대로 두고 불러오는 중임을 보인다(컨트롤이 바뀌지 않게).
 * - `typed`: 고를 목록이 없다(못 받음 `error`, 지금 시장에 맞는 유니버스 없음 `empty`). id 를 직접 적는다.
 * - `list`: 지금 시장의 유니버스 중에서 고른다. 저장된 값이 목록에 없으면 지우지 않고 `stored` 로 알린다.
 */
export type UniverseChoices =
  | { kind: "loading" }
  | { kind: "typed"; reason: "error" | "empty" }
  | {
      kind: "list";
      universes: readonly UniverseDefinition[];
      chosen: UniverseDefinition | undefined;
      /** 저장된 값이 목록 밖이면 그 까닭. 다른 시장의 유니버스(`other_market`)이거나 모르는 id(`unknown`)다. */
      stored: "other_market" | "unknown" | null;
    };

export type UniverseListState =
  | { status: "pending" }
  | { status: "error" }
  | { status: "success"; universes: readonly UniverseDefinition[] };

export const universeChoices = (
  list: UniverseListState,
  market: string,
  value: string,
): UniverseChoices => {
  if (list.status === "pending") return { kind: "loading" };
  if (list.status === "error") return { kind: "typed", reason: "error" };
  // 실행 설정에서 고른 시장의 유니버스만 보인다 — 다른 시장 유니버스로는 실행이 거절된다.
  const universes = list.universes.filter((item) => item.market === market);
  if (universes.length === 0) return { kind: "typed", reason: "empty" };
  const chosen = universes.find((item) => item.universe_id === value);
  const stored =
    value === "" || chosen !== undefined
      ? null
      : list.universes.some((item) => item.universe_id === value)
        ? "other_market"
        : "unknown";
  return { kind: "list", universes, chosen, stored };
};

/** 드롭다운 항목 문장. 표시명이 id 와 같으면(설명이 등록되지 않은 유니버스) 한 번만 쓴다. */
export const universeOptionLabel = (item: UniverseDefinition): string =>
  item.label === item.universe_id ? item.label : `${item.label} (${item.universe_id})`;

/** 요약 띠가 쓰는 유니버스 이름. 목록에 있으면 표시명, 없거나 아직 못 받았으면 id 그대로다. */
export const universeDisplayName = (
  universes: readonly UniverseDefinition[] | undefined,
  id: string,
): string => universes?.find((item) => item.universe_id === id)?.label ?? id;
