import type { ChartLevelPathBody, InstrumentKind } from "../../../shared/api";
import { formatPrice } from "./chart-price";
import type { ChartScale, PlotBox } from "./chart-scale";
import { labelAnchor, placeLevelLabels, type LevelLabel } from "./label-layout";
import type { LineTone } from "./line-tone";

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

export type ShadedBand = { tone: (typeof SHADED_BANDS)[number]["tone"] | "zone"; d: string };

const point = (x: number, y: number) => `${x.toFixed(1)} ${y.toFixed(1)}`;

const between = (lower: LevelSegment, upper: LevelSegment) =>
  `M${point(lower.x1, lower.y1)}L${point(lower.x2, lower.y2)}L${point(upper.x2, upper.y2)}L${point(upper.x1, upper.y1)}Z`;

/** 음영. 수평 띠(레벨 없는 선분 둘 — 자동 수평 구간의 하단·상단)는 두 선 사이를 띠(`zone`)로 칠한다. */
export const shadedBands = (segments: readonly LevelSegment[]): ShadedBand[] => {
  const flat = segments.filter((segment) => segment.level === null);
  if (flat.length === 2) return [{ tone: "zone", d: between(flat[0], flat[1]) }];
  return SHADED_BANDS.flatMap((band) => {
    const lower = segments.find((segment) => segment.level === band.from);
    const upper = segments.find((segment) => segment.level === band.to);
    return lower === undefined || upper === undefined ? [] : [{ tone: band.tone, d: between(lower, upper) }];
  });
};

/**
 * 자동 선을 생긴 날(`created`) 자리 x에서 나눈다: 그 앞(그때는 없던 선)은 옅게, 뒤는 그대로 그린다(설계 문서 2장).
 * 나누는 점은 그려 둔 선분 위의 px 자리라 가격을 계산하지 않는다. 생긴 날이 선분 시작보다 앞이면 모두 뒤 칸이다.
 */
export const formedSegments = (
  segments: readonly LevelSegment[],
  formedX: number | undefined,
): { before: LevelSegment[]; after: LevelSegment[] } => {
  if (formedX === undefined) return { before: [], after: [...segments] };
  const before: LevelSegment[] = [];
  const after: LevelSegment[] = [];
  for (const segment of segments) {
    if (formedX <= segment.x1) after.push(segment);
    else if (formedX >= segment.x2) before.push(segment);
    else {
      const ratio = (formedX - segment.x1) / (segment.x2 - segment.x1);
      const y = segment.y1 + (segment.y2 - segment.y1) * ratio;
      before.push({ ...segment, x2: formedX, y2: y });
      after.push({ ...segment, x1: formedX, y1: y });
    }
  }
  return { before, after };
};

export const segmentPath = (segment: LevelSegment): string =>
  `M${point(segment.x1, segment.y1)}L${point(segment.x2, segment.y2)}`;

/** 레벨 r의 표기(0.618, 1.618 — 꼬리 자리를 떼고 소수 셋째 자리까지). 라벨·표·문장이 같은 표기를 쓴다. */
export const levelText = (level: number): string => String(Number(level.toFixed(3)));

/**
 * 선 하나의 레벨 라벨 자리(배치 전): 레벨 선은 "r (가격)", 수평선·띠는 메모(없으면 라벨 없음). 가격은 기준 봉의 화면
 * 가격(응답 `start.price`)이다. 그림 칸을 지나지 않는 선분은 라벨을 달지 않는다.
 */
export const segmentLabels = (
  owner: string,
  segments: readonly LevelSegment[],
  memo: string | undefined,
  instrumentKind: InstrumentKind,
  plot: PlotBox,
): LevelLabel[] =>
  segments.flatMap((segment, index) => {
    const text =
      segment.level === null
        ? memo || null
        : `${levelText(segment.level)} (${formatPrice(segment.startPrice, instrumentKind)})`;
    const at = text === null ? null : labelAnchor(segment, plot);
    return text === null || at === null
      ? []
      : [{ owner, key: `${owner}-${segment.level ?? "h"}-${index}`, text, x: at.x, y: at.y }];
  });

/**
 * 저장 선이 아닌 그림(아직 저장하지 않은 자동 후보 미리보기). 저장 선과 같은 그리기·라벨 배치를 거친다 — 누를 자리는
 * 없다.
 */
export type ChartOverlayLine = {
  owner: string;
  tone: LineTone;
  segments: readonly LevelSegment[];
  /** 생긴 날 자리 x(그 앞은 옅게). */
  formedX?: number;
};

/**
 * 선 여러 개(저장 선·미리보기)의 레벨 라벨을 한 벌로 배치해 선(`owner`)별로 묶는다 — 가까운 자리의 라벨은 어느 선의
 * 것이든 서로 밀어낸다.
 */
export const placeLineLabels = (
  lines: readonly { owner: string; segments: readonly LevelSegment[]; memo?: string }[],
  instrumentKind: InstrumentKind,
  plot: PlotBox,
): Map<string, LevelLabel[]> => {
  const byOwner = new Map<string, LevelLabel[]>();
  placeLevelLabels(
    lines.flatMap((line) => segmentLabels(line.owner, line.segments, line.memo, instrumentKind, plot)),
    plot,
  ).forEach((label) => byOwner.set(label.owner, [...(byOwner.get(label.owner) ?? []), label]));
  return byOwner;
};
