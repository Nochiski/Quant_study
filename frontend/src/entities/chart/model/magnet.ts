import type { ChartCandleBody } from "../../../shared/api";

/** 봉의 네 값 중 하나. 마그넷과 좌표 입력의 "붙일 값" 선택이 같은 어휘를 쓴다. */
export type CandleField = "open" | "high" | "low" | "close";

export const CANDLE_FIELDS: readonly CandleField[] = ["open", "high", "low", "close"];

/**
 * 마그넷: 그 봉의 시가·고가·저가·종가 중 `price`에 가장 가까운 값을 고른다. 응답의 봉 값에서 고르기만 하고
 * 가격을 만들지 않는다. 거리가 같으면 시·고·저·종 순서로 앞의 것이다.
 */
export const snapToCandle = (
  candle: ChartCandleBody,
  price: number,
): { field: CandleField; price: number } => {
  let best: CandleField = "open";
  for (const field of CANDLE_FIELDS) {
    if (Math.abs(candle[field] - price) < Math.abs(candle[best] - price)) best = field;
  }
  return { field: best, price: candle[best] };
};
