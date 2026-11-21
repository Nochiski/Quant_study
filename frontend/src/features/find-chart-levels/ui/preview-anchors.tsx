import type { ChartPointBody, ChartScale } from "../../../entities/chart";
import { t } from "../../../shared/config";
import { anchorRoles } from "../model/line-evidence";
import type { PreviewLine } from "../model/use-finder-results";
import "./find-chart-levels.css";

const DOT_RADIUS = 5;
const LABEL_GAP = 12;

/**
 * 기준점이 고점 쪽인가(이름을 위에 둔다). 채널은 방향이 정한다 — 상승은 기준선 두 점이 저점·폭 점이 고점, 하락은 반대.
 * 되돌림은 두 점 중 높은 쪽이다.
 */
const isHigh = (preview: PreviewLine, index: number, anchors: readonly ChartPointBody[]) =>
  preview.kind === "fib_channel"
    ? (index === 2) === (preview.direction === "up")
    : anchors[index].price >= anchors[1 - index].price;

/**
 * 미리보기 후보(채널·되돌림)가 어느 점에서 그어졌는지 차트에 찍는다: 응답의 기준점(`drawing.anchors`)마다 점과 이름(근거
 * 패널과 같은 `anchorRoles`). 이름은 고점 쪽 점이면 위, 저점 쪽이면 아래에 두고 그림 칸 끝에 닿으면 반대쪽에 둔다.
 * 수평 구간 띠는 기준점이 띠의 하단·상단이라 찍지 않는다. 위치만 놓고 가격은 응답 값이다.
 */
export const PreviewAnchors = ({ previews, scale }: { previews: readonly PreviewLine[]; scale: ChartScale }) => {
  const { plot } = scale;
  return (
    <g className="preview-anchors" aria-hidden="true">
      {previews.flatMap((preview) => {
        if (preview.kind === "hline") return [];
        const roles = anchorRoles(preview.kind, preview.direction);
        const anchors = preview.drawing.anchors;
        return anchors.map((anchor, index) => {
          const x = scale.xOfDate(anchor.bar_date);
          const y = scale.yOfPrice(anchor.price);
          const high = isHigh(preview, index, anchors);
          const above = high ? y - LABEL_GAP > plot.top + LABEL_GAP : y + LABEL_GAP * 2 > plot.bottom;
          return (
            // 색은 그 선의 색 역할(`chart-line--auto-*`가 정하는 --line-ink)을 그대로 쓴다
            <g key={`${preview.key}-${index}`} className={`preview-anchor chart-line--auto-${preview.kind}`}>
              <circle cx={x} cy={y} r={DOT_RADIUS} />
              <text x={x} y={above ? y - LABEL_GAP : y + LABEL_GAP * 1.8} textAnchor="middle">
                {t(roles[index])}
              </text>
            </g>
          );
        });
      })}
    </g>
  );
};
