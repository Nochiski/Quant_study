import type {
  ChartConfluenceGroupBody,
  ChartScale,
} from "../../../entities/chart";

const BRACKET_WIDTH = 8;
const MIN_HEIGHT = 4;

/**
 * 중첩 표시: 그림 칸 오른쪽 끝의 잉크 괄호(설계 문서 2장). 가격대는 서버 중첩 응답 그대로이고 보이는 가격 범위 밖의
 * 중첩은 그리지 않는다. 낭독은 중첩 패널이 맡는다(괄호는 그림).
 */
export const ConfluenceBrackets = ({
  groups,
  scale,
}: {
  groups: readonly ChartConfluenceGroupBody[];
  scale: ChartScale;
}) => {
  const { plot } = scale;
  const x = plot.right - 3;
  return (
    <g className="confluence-brackets" aria-hidden="true">
      {groups.map((group) => {
        if (group.high < scale.priceMin || group.low > scale.priceMax)
          return null;
        const top = Math.max(scale.yOfPrice(group.high), plot.top);
        const bottom = Math.min(scale.yOfPrice(group.low), plot.bottom);
        const middle = (top + bottom) / 2;
        const half = Math.max(bottom - top, MIN_HEIGHT) / 2;
        return (
          <path
            key={`${group.low}-${group.high}`}
            className="confluence-bracket"
            d={`M${x - BRACKET_WIDTH} ${(middle - half).toFixed(1)}H${x}V${(middle + half).toFixed(1)}H${x - BRACKET_WIDTH}`}
          />
        );
      })}
    </g>
  );
};
