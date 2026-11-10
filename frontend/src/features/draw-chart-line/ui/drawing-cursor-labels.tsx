import { formatPrice, type ChartScale, type InstrumentKind } from "../../../entities/chart";
import type { ChartDrawing } from "../model/use-chart-drawing";

const LABEL_HEIGHT = 18;
const PRICE_WIDTH = 80;
const DATE_WIDTH = 84;

/**
 * 그리기 도구의 십자선 축 라벨: 오른쪽 가격 축에 찍힐 가격, 아래 날짜 축에 찍힐 봉 날짜(트레이딩뷰 관례). 값은
 * 포인터가 가리키는 점(`drawing.hover` — 마그넷을 거친 그 점)이라 누르면 저장될 기준점과 같다. 그림 칸 밖(축)에
 * 그리므로 위젯이 그림 칸 자르기 밖에 둔다.
 */
export const DrawingCursorLabels = ({
  drawing,
  scale,
  instrumentKind,
}: {
  drawing: ChartDrawing;
  scale: ChartScale;
  instrumentKind: InstrumentKind;
}) => {
  const { hover, state } = drawing;
  if (state.tool === "select" || hover === null) return null;
  const { plot } = scale;
  const y = scale.yOfPrice(hover.price);
  // 날짜 라벨은 그림 칸 폭 안에 머문다(가장자리 봉에서 축 밖으로 나가지 않는다)
  const x = Math.min(Math.max(scale.xOfDate(hover.bar_date), plot.left + DATE_WIDTH / 2), plot.right - DATE_WIDTH / 2);
  return (
    <g className="drawing-cursor-labels" aria-hidden="true">
      <rect x={plot.right + 1} y={y - LABEL_HEIGHT / 2} width={PRICE_WIDTH} height={LABEL_HEIGHT} rx={3} />
      <text x={plot.right + 5} y={y + 4}>
        {formatPrice(hover.price, instrumentKind)}
      </text>
      <rect x={x - DATE_WIDTH / 2} y={plot.bottom + 4} width={DATE_WIDTH} height={LABEL_HEIGHT} rx={3} />
      <text x={x} y={plot.bottom + 17} textAnchor="middle">
        {hover.bar_date}
      </text>
    </g>
  );
};
