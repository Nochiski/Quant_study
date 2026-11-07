import { useId } from "react";

import type { ChartCandleBody, InstrumentKind } from "../../../shared/api";
import { formatPrice } from "../model/chart-price";
import { dateTicks, priceTicks, type ChartScale } from "../model/chart-scale";
import "./chart.css";

type CandlePlotProps = {
  candles: readonly ChartCandleBody[];
  scale: ChartScale;
  instrumentKind: InstrumentKind;
  height: number;
  /** 현재가(응답 머리 `context.current_price`). 점선을 이 가격에 긋고, 없으면 긋지 않는다. */
  current: number | null;
};

const DATE_TICK_GAP = 64;
const PRICE_TICK_SPACING = 56;

/**
 * 캔들과 축. 상승(종가 ≥ 시가)은 `--return-up`, 하락은 `--return-down`이고 진행 중 봉(`in_progress`)은 속을
 * 비워 끝나지 않은 봉임을 보인다. 봉은 방향·진행 여부별로 path 하나씩 묶어 그린다(봉 수천 개도 요소 몇 개).
 * 보기 창(줌·이동)에 걸친 봉만 path로 만들고, 가장자리에 걸친 봉·현재가 선은 그림 칸으로 잘라 축을 덮지 않는다.
 */
export const CandlePlot = ({ candles, scale, instrumentKind, height, current }: CandlePlotProps) => {
  const { plot } = scale;
  const clipId = useId();
  const body = Math.max(1, Math.min(scale.step * 0.6, 14));
  const groups = { up: ["", ""], down: ["", ""], live: ["", ""] };
  for (let index = scale.first; index <= scale.last; index += 1) {
    const candle = candles[index];
    const x = scale.xOfIndex(index);
    const group = candle.in_progress ? "live" : candle.close >= candle.open ? "up" : "down";
    const top = scale.yOfPrice(Math.max(candle.open, candle.close));
    const bottom = scale.yOfPrice(Math.min(candle.open, candle.close));
    groups[group][0] += `M${x.toFixed(1)} ${scale.yOfPrice(candle.high).toFixed(1)}V${scale.yOfPrice(candle.low).toFixed(1)}`;
    groups[group][1] += `M${(x - body / 2).toFixed(1)} ${top.toFixed(1)}h${body.toFixed(1)}v${Math.max(bottom - top, 1).toFixed(1)}h${(-body).toFixed(1)}Z`;
  }
  const live = candles[scale.last]?.in_progress
    ? candles[scale.last].close >= candles[scale.last].open
      ? "up"
      : "down"
    : null;
  const ticks = priceTicks(
    scale.priceMin,
    scale.priceMax,
    Math.max(2, Math.floor((plot.bottom - plot.top) / PRICE_TICK_SPACING)),
  );
  return (
    <g className="chart-plot" aria-hidden="true">
      <defs>
        <clipPath id={clipId}>
          <rect x={plot.left} y={plot.top} width={plot.right - plot.left} height={plot.bottom - plot.top} />
        </clipPath>
      </defs>
      <rect
        className="chart-plot__future"
        x={Math.max(scale.futureLeft, plot.left)}
        y={plot.top}
        width={Math.max(plot.right - Math.max(scale.futureLeft, plot.left), 0)}
        height={plot.bottom - plot.top}
      />
      <path
        className="chart-plot__grid"
        d={ticks.map((tick) => `M${plot.left} ${scale.yOfPrice(tick).toFixed(1)}H${plot.right}`).join("")}
      />
      <path
        className="chart-plot__axis"
        d={`M${plot.right} ${plot.top}V${plot.bottom}M${plot.left} ${plot.bottom}H${plot.right}`}
      />
      <g className="chart-plot__tick">
        {ticks.map((tick) => (
          <text key={tick} x={plot.right + 6} y={scale.yOfPrice(tick) + 4}>
            {formatPrice(tick, instrumentKind)}
          </text>
        ))}
      </g>
      <g className="chart-plot__tick chart-plot__tick--date">
        {dateTicks(candles, scale, DATE_TICK_GAP).map((tick) => (
          <text key={tick.index} x={tick.x} y={height - 8}>
            {tick.label}
          </text>
        ))}
      </g>
      <g clipPath={`url(#${clipId})`}>
        <path className="chart-plot__wick chart-plot__wick--up" d={groups.up[0]} />
        <path className="chart-plot__body chart-plot__body--up" d={groups.up[1]} />
        <path className="chart-plot__wick chart-plot__wick--down" d={groups.down[0]} />
        <path className="chart-plot__body chart-plot__body--down" d={groups.down[1]} />
        {live === null ? null : (
          <>
            <path className={`chart-plot__wick chart-plot__wick--${live}`} d={groups.live[0]} />
            <path
              className={`chart-plot__body chart-plot__body--live chart-plot__body--live-${live}`}
              d={groups.live[1]}
            />
          </>
        )}
        {current === null ? null : (
          <path
            className="chart-plot__current"
            d={`M${plot.left} ${scale.yOfPrice(current).toFixed(1)}H${plot.right}`}
          />
        )}
      </g>
    </g>
  );
};
