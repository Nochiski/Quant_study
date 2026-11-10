import { steppedPrice, type ChartLineBody, type ChartPointBody, type TickedInstrument } from "../../../entities/chart";

/**
 * 키보드로 옮긴 수평선 기준점. ↑/↓ = 한 호가, Shift = 1%(호가 눈금에 맞춤). 수평선만 옮긴다(되돌림·채널은
 * 기준점이 여럿이라 카드에서 고친다). 잠긴 선·자동 선(`source = auto`, 기준점 고정)이거나 더 옮길 수 없으면
 * (0 이하) null이다.
 */
export const nudgedAnchors = (
  line: Pick<ChartLineBody, "kind" | "anchors" | "locked" | "source">,
  instrument: TickedInstrument,
  direction: 1 | -1,
  large: boolean,
): ChartPointBody[] | null => {
  if (line.kind !== "hline" || line.locked || line.source === "auto") return null;
  const moved = line.anchors.map((anchor) => ({
    bar_date: anchor.bar_date,
    price: steppedPrice(instrument, anchor.price, direction, large ? 1 : undefined),
  }));
  return moved.every((anchor, index) => anchor.price === line.anchors[index].price) ? null : moved;
};

/** 끌 수 있는 선인가: 잠기지 않았고, 자동 선이 아니고, 지금 보는 주기로 만든 선이다(다른 주기 기준점은 그 주기 화면에서). */
export const isDraggable = (
  line: Pick<ChartLineBody, "locked" | "source" | "timeframe">,
  viewTimeframe: ChartLineBody["timeframe"],
): boolean => !line.locked && line.source !== "auto" && line.timeframe === viewTimeframe;

