import {
  barAtOrBefore,
  type ChartCandleBody,
  type ChartScale,
  type LineKind,
} from "../../../entities/chart";
import { t, type MessageKey } from "../../../shared/config";
import type { PreviewLine } from "../model/use-finder-results";
import "./find-chart-levels.css";

// 기준점 이름은 좌표 입력 칸과 같은 문구다. 수평 구간 띠는 기준점이 띠의 위·아래 가격이라 찍지 않는다.
const NAMES: Partial<Record<LineKind, readonly MessageKey[]>> = {
  fib_channel: ["chart.anchor.fib_channel.0", "chart.anchor.fib_channel.1", "chart.anchor.fib_channel.2"],
  fib_retracement: ["chart.anchor.fib_retracement.0", "chart.anchor.fib_retracement.1"],
};
const DOT_RADIUS = 5;
const LABEL_GAP = 12;

/**
 * 미리보기 후보(채널·되돌림)가 어느 점에서 그어졌는지 차트에 찍는다: 응답의 기준점(`drawing.anchors`)마다 점과 이름.
 * 이름은 그 봉의 고가 쪽 점이면 위에, 저가 쪽이면 아래에 둔다(그림 칸 끝에 닿으면 반대쪽). 위치만 놓고 가격은 응답 값이다.
 */
export const PreviewAnchors = ({
  previews,
  scale,
  candles,
}: {
  previews: readonly PreviewLine[];
  scale: ChartScale;
  candles: readonly ChartCandleBody[];
}) => {
  const { plot } = scale;
  return (
    <g className="preview-anchors" aria-hidden="true">
      {previews.flatMap((preview) => {
        const names = NAMES[preview.kind];
        if (names === undefined) return [];
        return preview.drawing.anchors.map((anchor, index) => {
          const x = scale.xOfDate(anchor.bar_date);
          const y = scale.yOfPrice(anchor.price);
          const candle = candles[barAtOrBefore(candles, anchor.bar_date)];
          const high = candle !== undefined && Math.abs(anchor.price - candle.high) <= Math.abs(anchor.price - candle.low);
          const above = high ? y - LABEL_GAP > plot.top + LABEL_GAP : y + LABEL_GAP * 2 > plot.bottom;
          return (
            <g key={`${preview.key}-${index}`} className={`preview-anchor preview-anchor--${preview.kind}`}>
              <circle cx={x} cy={y} r={DOT_RADIUS} />
              <text x={x} y={above ? y - LABEL_GAP : y + LABEL_GAP * 1.8} textAnchor="middle">
                {t(names[index] ?? names[0])}
              </text>
            </g>
          );
        });
      })}
    </g>
  );
};
