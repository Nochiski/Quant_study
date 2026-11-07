import { useRef, useState } from "react";

import {
  useCreateChartLine,
  type ChartAutoRecordBody,
  type ChartTarget,
  type Timeframe,
} from "../../../entities/chart";
import type { PreviewLine } from "./use-finder-results";

/** 한 번 추가의 결과: 저장한 수와 저장하지 못한 수. */
export type AddOutcome = { saved: number; failed: number };

/**
 * 미리보기 후보를 자동 선으로 저장한다(새 줄로). 기준점은 응답의 `drawing.anchors`(화면 좌표), 자동 기록은 응답
 * `record`를 그대로 싣는다(설계 문서 6장) — 화면은 가격·기록을 다시 만들지 않는다. 수평 구간은 고른 띠마다 한 선이다.
 *
 * 묶음 중 일부만 저장됐으면 결과(`lastOutcome`)로 알리고, 다시 누르면 같은 묶음을 다시 보낸다 — 이미 저장된 띠는
 * 서버가 같은 기준점의 자동 선을 새로 만들지 않고 돌려주므로(`create_line`) 저장 안 된 것만 새로 생긴다. 겹친 누르기는
 * 부르는 순간 세우는 ref로 막는다(변이 상태는 다음 렌더에야 바뀐다).
 */
export const useAddCandidates = (target: ChartTarget, timeframe: Timeframe) => {
  const create = useCreateChartLine();
  const busy = useRef(false);
  const [lastOutcome, setLastOutcome] = useState<AddOutcome | null>(null);
  const add = async (previews: readonly PreviewLine[], record: ChartAutoRecordBody): Promise<AddOutcome | null> => {
    if (busy.current || previews.length === 0) return null;
    busy.current = true;
    try {
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
      const failed = results.filter((result) => result.status === "rejected").length;
      const outcome = { saved: results.length - failed, failed };
      setLastOutcome(outcome);
      return outcome;
    } finally {
      busy.current = false;
    }
  };
  return { add, lastOutcome, error: create.error };
};
