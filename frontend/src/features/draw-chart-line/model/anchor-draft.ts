import {
  priceInputValue,
  type CandleField,
  type ChartCandleBody,
  type ChartPointBody,
  type InstrumentKind,
  type LineKind,
} from "../../../entities/chart";
import { t, type MessageKey } from "../../../shared/config";
import { CLICKS_PER_LINE } from "./drawing";

/**
 * 좌표 입력 칸 한 줄(기준점 하나): 봉 날짜, 붙일 값(시·고·저·종가), 가격 글자. 날짜나 붙일 값을 고르면 그 봉의
 * 값으로 가격을 채우고(마그넷과 같은 "봉 값에서 고르기"), 가격은 직접 고칠 수 있다.
 */
export type AnchorDraft = { barDate: string; field: CandleField; price: string };

const candleOn = (candles: readonly ChartCandleBody[], barDate: string) =>
  candles.find((candle) => candle.start === barDate);

/** 날짜·붙일 값을 바꾼 줄. 그 날짜의 봉이 있으면 가격을 그 봉 값으로 채운다. */
export const withBar = (
  draft: AnchorDraft,
  candles: readonly ChartCandleBody[],
  instrumentKind: InstrumentKind,
  change: Partial<Pick<AnchorDraft, "barDate" | "field">>,
): AnchorDraft => {
  const next = { ...draft, ...change };
  const candle = candleOn(candles, next.barDate);
  return candle === undefined
    ? next
    : { ...next, price: priceInputValue(candle[next.field], instrumentKind) };
};

/** 저장 선의 기준점 → 입력 줄. 기준점 가격이 그 봉의 어느 값과 같으면 그 값을, 아니면 종가를 붙일 값으로 보인다. */
export const draftsOf = (
  anchors: readonly ChartPointBody[],
  candles: readonly ChartCandleBody[],
  instrumentKind: InstrumentKind,
): AnchorDraft[] =>
  anchors.map((anchor) => {
    const candle = candleOn(candles, anchor.bar_date);
    const field =
      (candle &&
        (["high", "low", "open", "close"] as const).find(
          (name) =>
            priceInputValue(candle[name], instrumentKind) ===
            priceInputValue(anchor.price, instrumentKind),
        )) ||
      "close";
    return {
      barDate: anchor.bar_date,
      field,
      price: priceInputValue(anchor.price, instrumentKind),
    };
  });

/**
 * 새 선 좌표의 처음 값: 보이는 완성 봉을 고르게 나눈 날짜(첫 봉 → 마지막 봉), 종가. 수평선은 날짜 없이 가격만
 * 받으므로 마지막 봉 종가로 채운다(저장 날짜는 `hlineAnchorDate`).
 */
export const initialDrafts = (
  kind: LineKind,
  visible: readonly ChartCandleBody[],
  instrumentKind: InstrumentKind,
): AnchorDraft[] => {
  if (visible.length === 0) return [];
  const count = CLICKS_PER_LINE[kind];
  return Array.from({ length: count }, (_, index) => {
    const position =
      kind === "hline" ? visible.length - 1 : Math.round((index * (visible.length - 1)) / Math.max(count - 1, 1));
    const candle = visible[position];
    return {
      barDate: candle.start,
      field: "close" as const,
      price: priceInputValue(candle.close, instrumentKind),
    };
  });
};

/** 입력 줄 → 저장 기준점. 가격이 0보다 큰 수가 아닌 줄이 있으면 null(빠른 확인 — 최종 판정은 서버). */
export const anchorsOf = (drafts: readonly AnchorDraft[]): ChartPointBody[] | null => {
  const anchors = drafts.map((draft) => ({ bar_date: draft.barDate, price: Number(draft.price) }));
  return anchors.every((anchor) => Number.isFinite(anchor.price) && anchor.price > 0) &&
    drafts.every((draft) => draft.price.trim() !== "")
    ? anchors
    : null;
};

const ANCHOR_NAMES: Record<LineKind, readonly MessageKey[]> = {
  hline: ["chart.anchor.hline.0"],
  fib_retracement: ["chart.anchor.fib_retracement.0", "chart.anchor.fib_retracement.1"],
  fib_channel: ["chart.anchor.fib_channel.0", "chart.anchor.fib_channel.1", "chart.anchor.fib_channel.2"],
};

/** 기준점 줄 이름: 되돌림은 먼저 점(1)·나중 점(0), 채널은 기준선 두 점과 폭 기준점. */
export const anchorName = (kind: LineKind, index: number): string =>
  t(ANCHOR_NAMES[kind][index] ?? ANCHOR_NAMES[kind][0]);
