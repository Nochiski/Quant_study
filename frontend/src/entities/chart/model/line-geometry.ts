import type { ChartLevelPathBody } from "../../../shared/api";
import type { ChartScale } from "./chart-scale";

/**
 * 레벨 선을 그릴 점(서버 응답 `paths`) → px 선분. 설계 문서 6장 계약: 화면은 `start`·`end` 두 점을 날짜 축
 * 위에서 잇고 `extend_right`이면 **같은 기울기로** 그림 칸 오른쪽 끝까지 늘리기만 한다. 레벨 가격·채널 기울기는
 * 계산하지 않는다.
 */
export type LevelSegment = {
  /** 되돌림·채널 레벨 r. 수평선·띠는 null. */
  level: number | null;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** 기준 봉의 화면 가격(레벨 라벨 "r (가격)"의 가격). */
  startPrice: number;
  /** 마지막 완성 봉의 레벨 가격(오른쪽 축 배지 값). */
  endPrice: number;
  /** 마지막 완성 봉 위치의 y(배지가 가리키는 자리). */
  endY: number;
};

export const levelSegments = (
  paths: readonly ChartLevelPathBody[],
  extendRight: boolean,
  scale: Pick<ChartScale, "xOfDate" | "yOfPrice" | "plot">,
): LevelSegment[] =>
  paths.map((path) => {
    const x1 = scale.xOfDate(path.start.bar_date);
    const y1 = scale.yOfPrice(path.start.price);
    const xEnd = scale.xOfDate(path.end.bar_date);
    const yEnd = scale.yOfPrice(path.end.price);
    const slope = xEnd > x1 ? (yEnd - y1) / (xEnd - x1) : 0;
    const x2 = extendRight ? Math.max(scale.plot.right, xEnd) : xEnd;
    return {
      level: path.level,
      x1,
      y1,
      x2,
      y2: yEnd + slope * (x2 - xEnd),
      startPrice: path.start.price,
      endPrice: path.end.price,
      endY: yEnd,
    };
  });

/**
 * 레벨 사이 음영(설계 문서 6장): 0~1은 진하게, 1~1.618은 옅게. 그 두 레벨이 모두 그려질 때만 칠한다.
 * 두 선분은 같은 기준 봉·같은 끝에서 시작하고 끝나므로 사다리꼴(채널은 평행사변형) 하나다.
 */
export const SHADED_BANDS = [
  { from: 0, to: 1, tone: "main" },
  { from: 1, to: 1.618, tone: "extension" },
] as const;

export type ShadedBand = { tone: (typeof SHADED_BANDS)[number]["tone"]; d: string };

const point = (x: number, y: number) => `${x.toFixed(1)} ${y.toFixed(1)}`;

export const shadedBands = (segments: readonly LevelSegment[]): ShadedBand[] =>
  SHADED_BANDS.flatMap((band) => {
    const lower = segments.find((segment) => segment.level === band.from);
    const upper = segments.find((segment) => segment.level === band.to);
    if (lower === undefined || upper === undefined) return [];
    return [
      {
        tone: band.tone,
        d: `M${point(lower.x1, lower.y1)}L${point(lower.x2, lower.y2)}L${point(upper.x2, upper.y2)}L${point(upper.x1, upper.y1)}Z`,
      },
    ];
  });

export const segmentPath = (segment: LevelSegment): string =>
  `M${point(segment.x1, segment.y1)}L${point(segment.x2, segment.y2)}`;
