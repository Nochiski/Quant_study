import { useLayoutEffect, useReducer, useRef, useState } from "react";

import {
  useCreateChartLine,
  useUpdateChartLine,
  type ChartLineBody,
  type ChartLinePatch,
  type ChartLinePreviewBody,
  type ChartPointBody,
  type ChartTarget,
  type LineKind,
  type Timeframe,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";
import { completedAnchors, drawingReducer, INITIAL_DRAWING } from "./drawing";
import { isTextEntry } from "./line-edit";
import { useLinePreview } from "./use-line-preview";

/**
 * 끄는 중인 선: 원래 선과 지금 손잡이로 옮긴 기준점. 놓은 뒤 저장 응답이 올 때까지 `committing`이다. `serial`은
 * 끌기마다 새 번호라, 같은 손잡이를 다시 끌 때 앞 끌기의 미리보기를 잔상으로 보이지 않는다.
 */
export type LineDrag = {
  serial: number;
  line: ChartLineBody;
  anchorIndex: number;
  anchors: readonly ChartPointBody[];
  committing: boolean;
};

const sameAnchors = (left: readonly ChartPointBody[], right: readonly ChartPointBody[]) =>
  left.length === right.length &&
  left.every((anchor, index) => anchor.bar_date === right[index].bar_date && anchor.price === right[index].price);

/** 요청 실패는 변이 상태(`error`)가 화면에 알린다 — 약속 거절을 따로 퍼뜨리지 않는다. */
const settle = (promise: Promise<unknown>) => promise.catch(() => undefined);

/**
 * 수동 그리기 한 벌: 도구·찍은 점·고른 선(리듀서), 포인터가 가리키는 점, 끄는 중인 선, 미리보기, 만들기·고치기.
 * 저장 선 자체는 query cache가 갖고 여기서는 id와 지금 움직이는 좌표만 둔다.
 *
 * 종목이나 주기가 바뀌면 찍은 점·고른 선·끌기를 비운다 — 점의 날짜는 그 주기 봉의 날짜라 다른 계열에서 뜻이 없다.
 * 저장 결과 처리는 요청마다 붙이는 콜백이 아니라 `mutateAsync`의 약속으로 한다 — 한 훅의 `mutate`를 겹쳐 부르면
 * 앞 호출의 콜백이 버려진다.
 */
export const useChartDrawing = (target: ChartTarget, timeframe: Timeframe) => {
  const [state, dispatch] = useReducer(drawingReducer, INITIAL_DRAWING);
  const [hover, setHover] = useState<ChartPointBody | null>(null);
  const [drag, setDrag] = useState<LineDrag | null>(null);
  const dragSerial = useRef(0);
  // 포인터를 올린 저장 선(고른 선이 없을 때 오른쪽 축 배지가 이 선의 레벨을 보인다)
  const [hoverLineId, setHoverLineId] = useState<string | null>(null);
  const scopeKey = `${target.kind}|${target.code}|${timeframe}`;
  const [scope, setScope] = useState(scopeKey);
  if (scope !== scopeKey) {
    // 렌더 중 상태 맞추기: 다른 계열로 넘어가면 그리던 것을 버린다
    setScope(scopeKey);
    dispatch({ type: "reset" });
    setHover(null);
    setDrag(null);
    setHoverLineId(null);
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
  const gesture =
    drag !== null
      ? `drag:${drag.serial}`
      : `draw:${scopeKey}:${tool}:${state.points.map((point) => point.bar_date).join(",")}`;
  const preview = useLinePreview(draft, gesture);

  /** 새 선 저장. 저장된 선을 고르고 선택 도구로 돌아간다. 저장된 선(실패면 null)으로 끝나는 약속을 준다. */
  const save = async (kind: LineKind, anchors: readonly ChartPointBody[]) => {
    try {
      const line = await create.mutateAsync({ ...draftOf(kind, anchors), source: "manual" });
      dispatch({ type: "finished", lineId: line.line_id });
      return line;
    } catch {
      return null;
    }
  };

  /** 차트를 눌렀다: 점을 더하고, 선이 끝나면 저장한다. */
  const place = (point: ChartPointBody) => {
    if (state.tool === "select" || create.isPending) return;
    const anchors = completedAnchors(state, point);
    if (anchors === null) dispatch({ type: "addPoint", point });
    else void save(state.tool, anchors);
  };

  /** 저장 선 고치기. 요청은 한 줄로 차례대로 가고 본문은 차례가 왔을 때의 최신 선에서 만든다. */
  const edit = (line: ChartLineBody, patch: ChartLinePatch) =>
    settle(update.mutateAsync({ line, patch }));

  const startDrag = (line: ChartLineBody, anchorIndex: number) => {
    dragSerial.current += 1;
    setDrag({ serial: dragSerial.current, line, anchorIndex, anchors: line.anchors, committing: false });
  };

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

  /** 놓았다: 바뀌었으면 저장하고, 응답(성공·실패 모두) 뒤에 끌기를 푼다. */
  const endDrag = () => {
    if (drag === null || drag.committing) return;
    if (sameAnchors(drag.anchors, drag.line.anchors)) {
      setDrag(null);
      return;
    }
    setDrag({ ...drag, committing: true });
    void edit(drag.line, { anchors: [...drag.anchors] }).finally(() => setDrag(null));
  };

  /** 끌기를 놓지 못하고 잃었다(포인터 취소·캡처 잃음) — 저장하지 않고 원래 자리로 돌린다. */
  const abandonDrag = () => setDrag((current) => (current?.committing ? current : null));

  const cancel = () => {
    dispatch({ type: "cancel" });
    abandonDrag();
  };
  const cancelRef = useCommittedRef(cancel);
  useLayoutEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isTextEntry(event.target)) cancelRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancelRef]);

  return {
    state,
    dispatch,
    hover,
    setHover,
    hoverLineId,
    setHoverLineId,
    drag,
    preview,
    place,
    save,
    edit,
    startDrag,
    moveDrag,
    endDrag,
    abandonDrag,
    saving: create.isPending || update.isPending,
    saveError: create.error ?? update.error,
  };
};

export type ChartDrawing = ReturnType<typeof useChartDrawing>;
