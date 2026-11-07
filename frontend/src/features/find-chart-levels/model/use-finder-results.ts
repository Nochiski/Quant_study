import { useQuery } from "@tanstack/react-query";

import {
  chartChannelsQuery,
  chartRegimeQuery,
  chartRetracementQuery,
  chartZonesQuery,
  type ChartConfluenceLineBody,
  type ChartDrawingBody,
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

/** 중첩 응답에서 미리보기 후보를 가리키는 ref 앞머리(저장 선 id와 겹치지 않는다). */
export const PREVIEW_REF = "preview:";

const seriesQuery = (
  target: ChartTarget,
  timeframe: Timeframe,
  draft: FinderDraft,
) => ({
  kind: target.kind,
  code: target.code,
  timeframe,
  ...(draft.window === null
    ? {}
    : { window_start: draft.window.start, window_end: draft.window.end }),
  ...(draft.candidateSensitivity === null
    ? {}
    : { candidate_sensitivity: draft.candidateSensitivity }),
  ...(draft.structureSensitivity === null
    ? {}
    : { structure_sensitivity: draft.structureSensitivity }),
});

/**
 * 열린 생성 패널의 서버 조회 한 벌(국면 판정·채널 후보·되돌림 스윙·수평 구간)과 거기서 나온 미리보기 후보. 패널과
 * 차트(판정 구간 음영·미리보기 선)가 같은 결과를 읽는다 — 쿼리 캐시가 owner이고 여기서는 고르기만 한다.
 *
 * 채널 방향은 "추천을 따름"이면 판정 응답의 `channel_direction` 하나가 정한다(화면이 판정하지 않는다).
 */
export const useFinderResults = (
  target: ChartTarget,
  timeframe: Timeframe,
  panel: FinderPanel,
) => {
  const draft = panel.view === "create" ? panel.draft : null;
  const tool = panel.view === "create" ? panel.tool : null;
  const base = draft === null ? null : seriesQuery(target, timeframe, draft);

  const regime = useQuery(
    chartRegimeQuery(tool === "fib_channel" && base !== null ? base : null),
  );
  const recommended = regime.data?.channel_direction ?? null;
  const direction: TrendDirection | null =
    draft === null || draft.direction === "none"
      ? null
      : draft.direction === "recommended"
        ? recommended
        : draft.direction;
  const channels = useQuery(
    chartChannelsQuery(
      tool === "fib_channel" &&
        base !== null &&
        draft !== null &&
        direction !== null
        ? {
            ...base,
            direction,
            ...(draft.firstAnchor === null
              ? {}
              : { first_anchor: draft.firstAnchor }),
          }
        : null,
    ),
  );
  const retracement = useQuery(
    chartRetracementQuery(
      tool === "fib_retracement" && base !== null && draft !== null
        ? {
            ...base,
            swing: draft.swing,
            ...(draft.levels === null ? {} : { levels: [...draft.levels] }),
          }
        : null,
    ),
  );
  const zones = useQuery(
    chartZonesQuery(
      tool === "hline" && base !== null && draft !== null
        ? {
            ...base,
            ...(draft.widthAtr === null ? {} : { width_atr: draft.widthAtr }),
            ...(draft.minTouches === null
              ? {}
              : { min_touches: draft.minTouches }),
          }
        : null,
    ),
  );

  const previews: PreviewLine[] = [];
  let window: DateRange | null = draft?.window ?? null;
  if (tool === "fib_channel" && draft !== null) {
    window ??= regime.data
      ? { start: regime.data.window.start, end: regime.data.window.end }
      : null;
    const picked =
      direction === null
        ? undefined
        : channels.data?.candidates[draft.candidate];
    if (picked !== undefined)
      previews.push({
        key: "channel",
        kind: "fib_channel",
        drawing: picked.drawing,
        created: picked.created,
        extendRight: true,
        levels: null,
      });
  } else if (tool === "fib_retracement" && draft !== null) {
    window ??= retracement.data
      ? {
          start: retracement.data.window.start,
          end: retracement.data.window.end,
        }
      : null;
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
    window ??= zones.data
      ? { start: zones.data.window.start, end: zones.data.window.end }
      : null;
    zones.data?.zones.forEach((zone, index) => {
      if (!draft.excludedZones.includes(index))
        previews.push({
          key: `zone-${index}`,
          kind: "hline",
          drawing: zone.drawing,
          created: zone.formed,
          extendRight: true,
          levels: null,
        });
    });
  }

  /** 중첩 요청에 실을 미리보기 후보(저장하지 않은 보이는 선). */
  const inlineLines: ChartConfluenceLineBody[] = previews.map((preview) => ({
    ref: `${PREVIEW_REF}${preview.key}`,
    timeframe,
    kind: preview.kind,
    anchors: preview.drawing.anchors,
    levels: preview.levels === null ? null : [...preview.levels],
  }));

  return {
    draft,
    tool,
    regime,
    channels,
    retracement,
    zones,
    direction,
    recommended,
    window,
    previews,
    inlineLines,
  };
};

export type FinderResults = ReturnType<typeof useFinderResults>;
