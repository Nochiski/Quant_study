import { useQuery } from "@tanstack/react-query";

import {
  chartChannelsQuery,
  chartRegimeQuery,
  chartRetracementQuery,
  chartZonesQuery,
  levelSegments,
  type ChartConfluenceLineBody,
  type ChartDrawingBody,
  type ChartOverlayLine,
  type ChartScale,
  type ChartTarget,
  type DateRange,
  type LineKind,
  type Timeframe,
  type TrendDirection,
} from "../../../entities/chart";
import type { FinderDraft, FinderPanel } from "./finder-panel";

/** 아직 저장하지 않은 후보 하나(메인 차트에 미리보기로 그리고, 중첩 요청에 싣는다). */
export type PreviewLine = {
  key: string;
  kind: LineKind;
  drawing: ChartDrawingBody;
  /** 생긴 날(채널·되돌림 `created`, 수평 구간 `formed`). 그 앞은 옅게 그린다. */
  created: string | null;
  extendRight: boolean;
  levels: readonly number[] | null;
};

/** 중첩 응답·차트 그림에서 미리보기 후보를 가리키는 이름 앞머리(저장 선 id와 겹치지 않는다). */
export const PREVIEW_REF = "preview:";

const seriesQuery = (target: ChartTarget, timeframe: Timeframe, draft: FinderDraft) => ({
  kind: target.kind,
  code: target.code,
  timeframe,
  ...(draft.window === null ? {} : { window_start: draft.window.start, window_end: draft.window.end }),
  ...(draft.candidateSensitivity === null ? {} : { candidate_sensitivity: draft.candidateSensitivity }),
  ...(draft.structureSensitivity === null ? {} : { structure_sensitivity: draft.structureSensitivity }),
});

/** 앞 입력의 응답을 잠깐 보이는 중이거나 아직 응답이 없다 — 그 결과로 고르거나 저장하지 않는다. */
const settled = (query: { data: unknown; isPlaceholderData: boolean; isFetching: boolean }) =>
  query.data !== undefined && !query.isPlaceholderData && !query.isFetching;

/**
 * 열린 생성 패널의 서버 조회 한 벌(국면 판정·채널 후보·되돌림 스윙·수평 구간)과 거기서 나온 미리보기 후보. 패널과
 * 차트(판정 구간 음영·미리보기 선)가 같은 결과를 읽는다 — 쿼리 캐시가 owner이고 여기서는 고르기만 한다.
 *
 * - 채널 방향은 "추천을 따름"이면 판정 응답의 `channel_direction` 하나가 정한다(화면이 판정하지 않는다). 구간을 바꾼
 *   뒤 새 판정이 오기 전(앞 응답을 잠깐 보이는 동안)에는 추천이 없다 — 앞 구간의 추천으로 새 구간 채널을 물으면
 *   서버가 방향 출처를 "직접 고름"으로 기록한다.
 * - 결과가 앞 입력의 것(placeholder)이거나 다시 받는 중이면 `ready`가 거짓이다. 그동안 후보 고르기·구간 빼기·추가를
 *   막는다 — 고른 자리(index)가 새 결과의 다른 항목에 붙지 않게.
 * - 판정 구간은 서버가 봉 시작일로 맞춘 응답 구간을 쓰고, 응답을 기다리는 동안만 고친 날짜를 쓴다.
 */
export const useFinderResults = (target: ChartTarget, timeframe: Timeframe, panel: FinderPanel) => {
  const draft = panel.view === "create" ? panel.draft : null;
  const tool = panel.view === "create" ? panel.tool : null;
  const base = draft === null ? null : seriesQuery(target, timeframe, draft);

  const regime = useQuery(chartRegimeQuery(tool === "fib_channel" && base !== null ? base : null));
  const judged = settled(regime) ? regime.data : undefined;
  const recommended = judged?.channel_direction ?? null;
  const direction: TrendDirection | null =
    draft === null || draft.direction === "none"
      ? null
      : draft.direction === "recommended"
        ? recommended
        : draft.direction;
  const channels = useQuery(
    chartChannelsQuery(
      tool === "fib_channel" && base !== null && draft !== null && direction !== null
        ? { ...base, direction, ...(draft.firstAnchor === null ? {} : { first_anchor: draft.firstAnchor }) }
        : null,
    ),
  );
  const retracement = useQuery(
    chartRetracementQuery(
      tool === "fib_retracement" && base !== null && draft !== null
        ? { ...base, swing: draft.swing, ...(draft.levels === null ? {} : { levels: [...draft.levels] }) }
        : null,
    ),
  );
  const zones = useQuery(
    chartZonesQuery(
      tool === "hline" && base !== null && draft !== null
        ? {
            ...base,
            ...(draft.widthAtr === null ? {} : { width_atr: draft.widthAtr }),
            ...(draft.minTouches === null ? {} : { min_touches: draft.minTouches }),
          }
        : null,
    ),
  );

  const previews: PreviewLine[] = [];
  let answered: DateRange | null = null;
  let ready = false;
  if (tool === "fib_channel" && draft !== null) {
    answered = regime.data?.window ?? null;
    ready = judged !== undefined && direction !== null && settled(channels);
    const picked = direction === null ? undefined : channels.data?.candidates[draft.candidate];
    if (picked !== undefined)
      previews.push({ key: "channel", kind: "fib_channel", drawing: picked.drawing, created: picked.created, extendRight: true, levels: null });
  } else if (tool === "fib_retracement" && draft !== null) {
    answered = retracement.data?.window ?? null;
    ready = settled(retracement);
    const drawing = retracement.data?.drawing ?? null;
    if (drawing !== null)
      previews.push({
        key: "retracement",
        kind: "fib_retracement",
        drawing,
        created: retracement.data?.created ?? null,
        extendRight: draft.extendRight,
        levels: draft.levels,
      });
  } else if (tool === "hline" && draft !== null) {
    answered = zones.data?.window ?? null;
    ready = settled(zones);
    zones.data?.zones.forEach((zone, index) => {
      if (!draft.excludedZones.includes(index))
        previews.push({ key: `zone-${index}`, kind: "hline", drawing: zone.drawing, created: zone.formed, extendRight: true, levels: null });
    });
  }
  const anyQuery = tool === "fib_channel" ? regime : tool === "fib_retracement" ? retracement : zones;
  // 응답이 지금 입력의 것이면 서버가 맞춘 구간, 아니면(기다리는 중) 고친 날짜
  const window: DateRange | null =
    answered !== null && (draft?.window === null || settled(anyQuery))
      ? { start: answered.start, end: answered.end }
      : (draft?.window ?? answered);

  /** 중첩 요청에 실을 미리보기 후보(저장하지 않은 보이는 선). */
  const inlineLines: ChartConfluenceLineBody[] = previews.map((preview) => ({
    ref: `${PREVIEW_REF}${preview.key}`,
    timeframe,
    kind: preview.kind,
    anchors: preview.drawing.anchors,
    levels: preview.levels === null ? null : [...preview.levels],
  }));

  return { draft, tool, regime, judged, channels, retracement, zones, direction, recommended, window, ready, previews, inlineLines };
};

export type FinderResults = ReturnType<typeof useFinderResults>;

/** 미리보기 후보 → 차트 그림(저장 선과 같은 그리기·라벨 배치에 넣는다). */
export const previewOverlays = (previews: readonly PreviewLine[], scale: ChartScale): ChartOverlayLine[] =>
  previews.map((preview) => ({
    owner: `${PREVIEW_REF}${preview.key}`,
    tone: `auto-${preview.kind}`,
    segments: levelSegments(preview.drawing.paths, preview.extendRight, scale),
    formedX: preview.created === null ? undefined : scale.xOfDate(preview.created),
  }));
