import type { ChartInstrumentBody, InstrumentKind } from "../../../shared/api";

/**
 * 화면 가격의 표시 자릿수와 한 호가 — 표시·키보드 이동용이다. 가격 자체(레벨 가격·×k)는 서버 응답이고 여기서는
 * 만들지 않는다.
 *
 * 서버의 화면 가격은 저장 좌표 × k라 마지막 자리(1ulp) 꼬리가 있을 수 있다(설계 문서 6장). 종목은 원 단위,
 * 지수는 소수 둘째 자리로 반올림해 보이고 입력 칸도 그 값으로 채운다.
 */
const precision = (kind: InstrumentKind): number => (kind === "index" ? 2 : 0);

export const roundPrice = (price: number, kind: InstrumentKind): number =>
  Number(price.toFixed(precision(kind)));

const formatters = new Map<number, Intl.NumberFormat>();

export const formatPrice = (price: number, kind: InstrumentKind): string => {
  const digits = precision(kind);
  let formatter = formatters.get(digits);
  if (formatter === undefined) {
    formatter = new Intl.NumberFormat("ko-KR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    formatters.set(digits, formatter);
  }
  return formatter.format(price);
};

/** 입력 칸(`type="number"`)에 넣을 값 — 묶음 쉼표 없이 표시 자릿수로. */
export const priceInputValue = (price: number, kind: InstrumentKind): string =>
  price.toFixed(precision(kind));

// 유가증권·코스닥 주식 호가가격단위(2023-01-25 개편 이후). [이 가격 미만, 호가].
const STOCK_TICKS: readonly (readonly [number, number])[] = [
  [2_000, 1],
  [5_000, 5],
  [20_000, 10],
  [50_000, 50],
  [200_000, 100],
  [500_000, 500],
];
const FUND_GROUPS = new Set(["etf", "etn"]);

/** 그 가격의 한 호가. 지수는 0.01, ETF·ETN은 2,000원 미만 1원·이상 5원, 주식은 가격 구간표. */
export const priceTick = (
  instrument: Pick<ChartInstrumentBody, "kind" | "group">,
  price: number,
): number => {
  if (instrument.kind === "index") return 0.01;
  if (FUND_GROUPS.has(instrument.group ?? "")) return price < 2_000 ? 1 : 5;
  return STOCK_TICKS.find(([below]) => price < below)?.[1] ?? 1_000;
};

/**
 * 키보드로 옮긴 가격. 한 호가(`percent` 없음)는 호가 눈금에 맞춰 위·아래 다음 눈금으로, `percent`면 그 비율만큼
 * 옮긴 뒤 표시 자릿수로 반올림한다. 아래로 갈 때는 바로 아래 가격 구간의 호가를 쓴다(2,000 → 1,999).
 */
export const steppedPrice = (
  instrument: Pick<ChartInstrumentBody, "kind" | "group">,
  price: number,
  direction: 1 | -1,
  percent?: number,
): number => {
  if (percent !== undefined)
    return roundPrice(price * (1 + (direction * percent) / 100), instrument.kind);
  const tick = priceTick(instrument, direction > 0 ? price : price - 1e-9);
  const units = price / tick;
  // 눈금 위의 값은 부동소수 오차로 0.9999…가 될 수 있어 자리를 잘라 본다
  const onGrid = Number(units.toFixed(6));
  const next =
    direction > 0 ? (Math.floor(onGrid) + 1) * tick : (Math.ceil(onGrid) - 1) * tick;
  return roundPrice(next, instrument.kind);
};
