import type { ChartInstrumentBody, InstrumentKind } from "../../../shared/api";

/**
 * 화면 가격의 표시 자릿수와 키보드 이동 — 가격 자체(레벨 가격·×k)와 한 호가 표는 서버 응답이고 여기서는
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

/** 한 호가 표를 실은 대상(응답 `ChartInstrumentBody`). 표의 owner는 backend domain `_price_tick.py`다. */
export type TickedInstrument = Pick<ChartInstrumentBody, "kind" | "tick_steps">;

/** 그 가격의 한 호가 — 응답 표(`tick_steps`)에서 읽기만 한다. 표가 비면 표시 자릿수의 한 칸이다. */
export const priceTick = (instrument: TickedInstrument, price: number): number =>
  instrument.tick_steps.find((step) => step.below === null || price < step.below)?.tick ??
  (instrument.kind === "index" ? 0.01 : 1);

/** 호가 눈금 위로 맞춘 값(`units`는 가격 ÷ 호가). 눈금 위의 값이 부동소수 오차로 0.9999…가 되지 않게 자른다. */
const gridUnits = (price: number, tick: number): number => Number((price / tick).toFixed(6));

/**
 * 키보드로 옮긴 가격. `percent`가 없으면 한 호가: 호가 눈금에 맞춰 위·아래 다음 눈금으로 간다. 아래로 갈 때는
 * 바로 아래 가격 구간의 호가를 쓴다(2,000 → 1,999). `percent`면 그 비율만큼 옮긴 뒤 가는 쪽으로 호가 눈금에
 * 맞추고, 적어도 한 호가는 움직인다. 0 이하로 내려가는 이동은 하지 않고 지금 가격을 돌려준다.
 */
export const steppedPrice = (
  instrument: TickedInstrument,
  price: number,
  direction: 1 | -1,
  percent?: number,
): number => {
  // 보이는 값에서 센다 — 마그넷이 붙인 봉 값처럼 눈금 밖의 가격(2,726.705)도 사용자가 보는 2,726.71에서 한
  // 호가 움직인다(눈금에 맞추기만 하고 보이는 값이 그대로인 이동이 없다)
  const base = roundPrice(price, instrument.kind);
  const tickAt = (value: number) => priceTick(instrument, direction > 0 ? value : value - 1e-9);
  const tick = tickAt(base);
  const oneTick =
    direction > 0
      ? (Math.floor(gridUnits(base, tick)) + 1) * tick
      : (Math.ceil(gridUnits(base, tick)) - 1) * tick;
  let next = oneTick;
  if (percent !== undefined) {
    const moved = base * (1 + (direction * percent) / 100);
    const movedTick = tickAt(moved);
    const snapped =
      direction > 0
        ? Math.ceil(gridUnits(moved, movedTick)) * movedTick
        : Math.floor(gridUnits(moved, movedTick)) * movedTick;
    next = direction > 0 ? Math.max(snapped, oneTick) : Math.min(snapped, oneTick);
  }
  const rounded = roundPrice(next, instrument.kind);
  return rounded > 0 ? rounded : price;
};
