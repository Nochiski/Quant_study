import { useLayoutEffect, useReducer, useState } from "react";

import {
  useCreateChartLine,
  useUpdateChartLine,
  type ChartLineBody,
  type ChartLineEditBody,
  type ChartLinePreviewBody,
  type ChartPointBody,
  type ChartTarget,
  type LineKind,
  type Timeframe,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";
import { completedAnchors, drawingReducer, INITIAL_DRAWING } from "./drawing";
import { chartLineEdit } from "./line-edit";
import { useLinePreview } from "./use-line-preview";

/** 끄는 중인 선: 원래 선과 지금 손잡이로 옮긴 기준점. 놓은 뒤 저장 응답이 올 때까지 `committing`이다. */
export type LineDrag = {
  line: ChartLineBody;
  anchorIndex: number;
  anchors: readonly ChartPointBody[];
  committing: boolean;
};

const sameAnchors = (left: readonly ChartPointBody[], right: readonly ChartPointBody[]) =>
  left.length === right.length &&
  left.every((anchor, index) => anchor.bar_date === right[index].bar_date && anchor.price === right[index].price);

/**
 * 수동 그리기 한 벌: 도구·찍은 점·고른 선(리듀서), 포인터가 가리키는 점, 끄는 중인 선, 미리보기, 만들기·고치기.
 * 저장 선 자체는 query cache가 갖고 여기서는 id와 지금 움직이는 좌표만 둔다.
 *
 * 종목이나 주기가 바뀌면 찍은 점·고른 선·끌기를 비운다 — 점의 날짜는 그 주기 봉의 날짜라 다른 계열에서 뜻이 없다.
 */
export const useChartDrawing = (target: ChartTarget, timeframe: Timeframe) => {
  const [state, dispatch] = useReducer(drawingReducer, INITIAL_DRAWING);
  const [hover, setHover] = useState<ChartPointBody | null>(null);
  const [drag, setDrag] = useState<LineDrag | null>(null);
  const scopeKey = `${target.kind}|${target.code}|${timeframe}`;
  const [scope, setScope] = useState(scopeKey);
  if (scope !== scopeKey) {
    // 렌더 중 상태 맞추기: 다른 계열로 넘어가면 그리던 것을 버린다
    setScope(scopeKey);
    dispatch({ type: "cancel" });
    dispatch({ type: "select", lineId: null });
    setHover(null);
    setDrag(null);
  }
  const create = useCreateChartLine();
  const update = useUpdateChartLine();

  const draftOf = (
    kind: LineKind,
    anchors: readonly ChartPointBody[],
    levels: ChartLineBody["levels"] = null,
    lineTimeframe: Timeframe = timeframe,
  ): ChartLinePreviewBody => ({
    instrument_kind: target.kind,
    instrument_code: target.code,
    timeframe: lineTimeframe,
    kind,
    anchors: anchors.map(({ bar_date, price }) => ({ bar_date, price })),
    levels,
  });

  // 서버 미리보기가 필요한 순간: 되돌림 둘째 점·채널 폭 기준점을 고르는 중, 또는 선을 끄는 중.
  const tool = state.tool;
  const drawingDraft =
    hover !== null &&
    ((tool === "fib_retracement" && state.points.length === 1) ||
      (tool === "fib_channel" && state.points.length === 2))
      ? draftOf(tool, [...state.points, hover])
      : null;
  const draft =
    drag !== null
      ? draftOf(drag.line.kind, drag.anchors, drag.line.levels, drag.line.timeframe)
      : drawingDraft;
  const preview = useLinePreview(draft);

  const save = (kind: LineKind, anchors: readonly ChartPointBody[], onSaved?: (line: ChartLineBody) => void) =>
    create.mutate(
      {
        ...draftOf(kind, anchors),
        source: "manual",
      },
      {
        onSuccess: (line) => {
          dispatch({ type: "finished", lineId: line.line_id });
          onSaved?.(line);
        },
      },
    );

  /** 차트를 눌렀다: 점을 더하고, 선이 끝나면 저장한다. */
  const place = (point: ChartPointBody) => {
    if (create.isPending) return;
    const anchors = completedAnchors(state, point);
    if (state.tool === "select") return;
    if (anchors === null) dispatch({ type: "addPoint", point });
    else save(state.tool, anchors);
  };

  const edit = (line: ChartLineBody, patch: Partial<ChartLineEditBody>) =>
    update.mutate({ lineId: line.line_id, body: chartLineEdit(line, patch) });

  const startDrag = (line: ChartLineBody, anchorIndex: number) =>
    setDrag({ line, anchorIndex, anchors: line.anchors, committing: false });

  const moveDrag = (point: ChartPointBody) =>
    setDrag((current) =>
      current === null || current.committing
        ? current
        : {
            ...current,
            anchors: current.anchors.map((anchor, index) =>
              index === current.anchorIndex ? point : anchor,
            ),
          },
    );

  const endDrag = () => {
    if (drag === null || drag.committing) return;
    if (sameAnchors(drag.anchors, drag.line.anchors)) {
      setDrag(null);
      return;
    }
    setDrag({ ...drag, committing: true });
    update.mutate(
      { lineId: drag.line.line_id, body: chartLineEdit(drag.line, { anchors: [...drag.anchors] }) },
      { onSettled: () => setDrag(null) },
    );
  };

  const cancel = () => {
    dispatch({ type: "cancel" });
    setDrag((current) => (current?.committing ? current : null));
  };
  const cancelRef = useCommittedRef(cancel);
  useLayoutEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancelRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancelRef]);

  return {
    state,
    dispatch,
    hover,
    setHover,
    drag,
    preview,
    place,
    save,
    edit,
    startDrag,
    moveDrag,
    endDrag,
    saving: create.isPending || update.isPending,
    saveError: create.error ?? update.error,
    /** 이 선을 고치는 요청이 지금 나가 있는가(키보드 이동을 겹쳐 보내지 않는다). */
    editing: (lineId: string) => update.isPending && update.variables?.lineId === lineId,
  };
};

export type ChartDrawing = ReturnType<typeof useChartDrawing>;
