import { spreadLabels } from "../model/label-layout";
import type { LineTone } from "../model/line-tone";
import type { PlotBox } from "../model/chart-scale";

export type AxisBadge = {
  key: string;
  /** 가리키는 가격의 y. */
  y: number;
  text: string;
  /** `current` = 현재가(흰 배지), 나머지는 선의 색 역할(`lineTone`) — 내 선은 잉크 배지, 자동 선은 옅은 배지. */
  tone: "current" | LineTone;
};

const BADGE_HEIGHT = 18;
const BADGE_GAP = 20;
const BADGE_WIDTH = 80;

/**
 * 오른쪽 축 배지. 겹치면 세로로 밀어내고(`spreadLabels`) 원래 가격 자리를 가는 선으로 가리킨다. 숫자는 축 글자와
 * 같은 12px 이상이다. 보이는 가격 범위 밖의 배지는 그리지 않는다.
 */
export const AxisBadges = ({ badges: all, plot }: { badges: readonly AxisBadge[]; plot: PlotBox }) => {
  // 그림 칸 위·아래 밖의 가격은 배지를 달지 않는다 — 칸 끝에 붙여 두면 가는 선이 칸 밖을 가리킨다
  const badges = all.filter((badge) => badge.y >= plot.top && badge.y <= plot.bottom);
  const placed = spreadLabels(
    badges.map((badge) => badge.y),
    BADGE_GAP,
    plot.top + BADGE_HEIGHT / 2,
    plot.bottom - BADGE_HEIGHT / 2,
  );
  return (
    <g className="chart-badges" aria-hidden="true">
      {badges.map((badge, index) => {
        const y = placed[index];
        const moved = Math.abs(y - badge.y) > 0.5;
        return (
          <g
            key={badge.key}
            className={`chart-badge chart-badge--${badge.tone === "current" ? "current" : badge.tone === "mine" ? "mine" : "auto"}`}
          >
            {moved ? (
              <path
                className="chart-badge__leader"
                d={`M${plot.right - 6} ${badge.y.toFixed(1)}L${plot.right + 1} ${y.toFixed(1)}`}
              />
            ) : null}
            <rect
              className="chart-badge__box"
              x={plot.right + 1}
              y={y - BADGE_HEIGHT / 2}
              width={BADGE_WIDTH}
              height={BADGE_HEIGHT}
              rx={3}
            />
            <text className="chart-badge__text" x={plot.right + 5} y={y + 4}>
              {badge.text}
            </text>
          </g>
        );
      })}
    </g>
  );
};
