import {
  ChartLineShape,
  levelSegments,
  placeLevelLabels,
  segmentLabels,
  type ChartScale,
  type InstrumentKind,
  type LevelLabel,
} from "../../../entities/chart";
import type { PreviewLine } from "../model/use-finder-results";

/**
 * 아직 저장하지 않은 자동 후보를 메인 차트에 미리보기로 그린다(패널을 열면 차트가 미리보기 상태가 된다). 레벨 선을 그릴
 * 점은 응답 `drawing.paths` 그대로이고 생긴 날 앞은 옅게 그린다.
 */
export const PreviewLines = ({
  previews,
  scale,
  instrumentKind,
}: {
  previews: readonly PreviewLine[];
  scale: ChartScale;
  instrumentKind: InstrumentKind;
}) => {
  const shapes = previews.map((preview) => ({
    preview,
    segments: levelSegments(preview.drawing.paths, preview.extendRight, scale),
  }));
  const labels = new Map<string, LevelLabel[]>();
  placeLevelLabels(
    shapes.flatMap(({ preview, segments }) =>
      segmentLabels(
        preview.key,
        segments,
        undefined,
        instrumentKind,
        scale.plot,
      ),
    ),
    scale.plot,
  ).forEach((label) =>
    labels.set(label.owner, [...(labels.get(label.owner) ?? []), label]),
  );
  return (
    <g className="finder-preview" aria-hidden="true">
      {shapes.map(({ preview, segments }) => (
        <ChartLineShape
          key={preview.key}
          segments={segments}
          tone={`auto-${preview.kind}`}
          labels={labels.get(preview.key) ?? []}
          preview
          formedX={
            preview.created === null
              ? undefined
              : scale.xOfDate(preview.created)
          }
        />
      ))}
    </g>
  );
};
