import {
  useCreateChartLine,
  type ChartAutoRecordBody,
  type ChartTarget,
  type Timeframe,
} from "../../../entities/chart";
import type { PreviewLine } from "./use-finder-results";

/**
 * 미리보기 후보를 자동 선으로 저장한다(새 줄로). 기준점은 응답의 `drawing.anchors`(화면 좌표), 자동 기록은 응답
 * `record`를 그대로 싣는다(설계 문서 6장) — 화면은 가격·기록을 다시 만들지 않는다. 수평 구간은 고른 띠마다 한 선이다.
 * 모두 저장되면 true, 하나라도 실패하면 false(실패는 변이 상태 `error`가 알린다).
 */
export const useAddCandidates = (target: ChartTarget, timeframe: Timeframe) => {
  const create = useCreateChartLine();
  const add = async (
    previews: readonly PreviewLine[],
    record: ChartAutoRecordBody,
  ): Promise<boolean> => {
    const results = await Promise.allSettled(
      previews.map((preview) =>
        create.mutateAsync({
          instrument_kind: target.kind,
          instrument_code: target.code,
          timeframe,
          kind: preview.kind,
          source: "auto",
          anchors: preview.drawing.anchors,
          levels: preview.levels === null ? null : [...preview.levels],
          extend_right: preview.extendRight,
          memo: "",
          locked: false,
          visible: true,
          auto: record,
        }),
      ),
    );
    return results.every((result) => result.status === "fulfilled");
  };
  return { add, pending: create.isPending, error: create.error };
};
