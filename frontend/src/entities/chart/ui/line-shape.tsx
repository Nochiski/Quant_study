import type { InstrumentKind } from "../../../shared/api";
import { formatPrice } from "../model/chart-price";
import type { LineTone } from "../model/line-tone";
import { segmentPath, shadedBands, type LevelSegment } from "../model/line-geometry";


type ChartLineShapeProps = {
  segments: readonly LevelSegment[];
  tone: LineTone;
  instrumentKind: InstrumentKind;
  /** 고른 선은 두꺼운 반투명 테두리로 강조한다. */
  selected?: boolean;
  /** 미리보기(그리는 중·끄는 중)는 점선 없이 옅게 그린다. */
  preview?: boolean;
  /** 수평선 라벨(메모). 레벨 선은 "r (가격)"을 쓴다. */
  memo?: string;
  /**
   * 누르면 고른다(선택 도구일 때만 넘긴다). 넓은 투명 선이 포인터로 누를 자리이고, 키보드로는 레이어 칸 목록의 같은
   * 선 이름 버튼으로 고른다 — 그래서 누를 자리는 낭독에서 뺀다.
   */
  onSelect?: () => void;
};

const levelText = (level: number): string => String(Number(level.toFixed(3)));

/**
 * 저장 선·미리보기 하나. 레벨 0(기준선)은 굵게, 0~1·1~1.618 사이는 음영, 각 레벨 라벨 "r (가격)"은 기준 봉 자리에
 * 둔다(설계 문서 6장 채널 표시 계약 — 되돌림도 같다). 가격은 모두 응답 값이다.
 */
export const ChartLineShape = ({
  segments,
  tone,
  instrumentKind,
  selected = false,
  preview = false,
  memo,
  onSelect,
}: ChartLineShapeProps) => {
  const className = [
    "chart-line",
    `chart-line--${tone}`,
    selected ? "chart-line--selected" : "",
    preview ? "chart-line--preview" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const all = segments.map(segmentPath).join("");
  return (
    <g className={className}>
      {shadedBands(segments).map((band) => (
        <path
          key={band.tone}
          className={`chart-line__band chart-line__band--${band.tone}`}
          d={band.d}
        />
      ))}
      {selected ? <path className="chart-line__halo" d={all} /> : null}
      {segments.map((segment, index) => (
        <path
          key={`${segment.level ?? "h"}-${index}`}
          className={
            segment.level === 0 ? "chart-line__level chart-line__level--base" : "chart-line__level"
          }
          d={segmentPath(segment)}
        />
      ))}
      <g className="chart-line__labels">
        {segments.map((segment, index) =>
          segment.level === null ? (
            memo ? (
              <text key={`memo-${index}`} x={segment.x1 + 6} y={segment.y1 - 5}>
                {memo}
              </text>
            ) : null
          ) : (
            <text key={`label-${segment.level}`} x={segment.x1 + 6} y={segment.y1 - 5}>
              {`${levelText(segment.level)} (${formatPrice(segment.startPrice, instrumentKind)})`}
            </text>
          ),
        )}
      </g>
      {onSelect === undefined ? null : (
        <path
          className="chart-line__hit"
          d={all}
          aria-hidden="true"
          onClick={(event) => {
            // 차트 빈 곳 누르기(고른 것 풀기)로 번지지 않게 한다
            event.stopPropagation();
            onSelect();
          }}
        />
      )}
    </g>
  );
};
