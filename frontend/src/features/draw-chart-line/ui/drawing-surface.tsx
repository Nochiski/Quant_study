import { useEffect, useRef, type MouseEvent, type PointerEvent } from "react";

import {
  ChartLineShape,
  levelSegments,
  lineTone,
  placeLineLabels,
  roundPrice,
  snapToCandle,
  type ChartCandleBody,
  type ChartLineBody,
  type ChartOverlayLine,
  type ChartPointBody,
  type ChartScale,
  type InstrumentKind,
  type Timeframe,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";
import { completedAnchors } from "../model/drawing";
import { isDraggable } from "../model/line-edit";
import type { ChartDrawing } from "../model/use-chart-drawing";

// 그리는 중인 미리보기 선의 라벨 주인 이름(저장 선 id와 겹치지 않는다)
const DRAFT = "draft";
// 누른 자리에서 이만큼(px) 넘게 움직여 놓으면 끌어서 그린 것이다 — 손떨림은 클릭으로 본다
const DRAG_DRAW_PX = 4;

/** 그림 칸 기준 포인터 자리와, 이번 움직임에서 마그넷을 반대로 쓰는가(Ctrl·⌘을 누르는 중). */
type Pointer = { x: number; y: number; flipMagnet: boolean };

type DrawingSurfaceProps = {
  drawing: ChartDrawing;
  scale: ChartScale;
  candles: readonly ChartCandleBody[];
  /** 완성 봉 수(`completedCandles`). 기준점은 그 안에서만 고른다. */
  completedCount: number;
  instrumentKind: InstrumentKind;
  timeframe: Timeframe;
  /** 보이는 저장 선(`visible`). */
  lines: readonly ChartLineBody[];
  /** 저장 선이 아닌 그림(자동 후보 미리보기). 누를 수 없고, 라벨은 저장 선과 함께 배치한다. */
  overlays?: readonly ChartOverlayLine[];
};

/**
 * 차트 위 그리기 칸: 포인터가 가리키는 봉·가격(마그넷이면 그 봉의 시·고·저·종가 중 가장 가까운 값, Ctrl·⌘을 누르는
 * 동안은 반대), 도구별 점 찍기(클릭을 거듭하거나 누른 채 끌어 놓기), 저장 선 고르기와 손잡이·수평선 몸통 끌기.
 * 미리보기는 서버 응답(`drawing.preview`)의 두 점을 그대로 잇는다.
 *
 * 포인터 움직임은 rAF 한 번으로 묶어 상태를 바꾼다 — 서버 미리보기 호출은 그 위에서 다시 잠깐 멈춘 초안만 간다.
 * 레벨 라벨은 보이는 선 전부를 한 번에 배치한다 — 가까운 자리의 라벨끼리 밀어내고 그림 칸 안으로 들인다.
 */
export const DrawingSurface = ({
  drawing,
  scale,
  candles,
  completedCount,
  instrumentKind,
  timeframe,
  lines,
  overlays = [],
}: DrawingSurfaceProps) => {
  const { state, dispatch, hover, drag, preview } = drawing;
  const frame = useRef<number | null>(null);
  const pending = useRef<Pointer | null>(null);
  // 그리기 도구로 누른 자리(client 좌표)와 그 누르기가 선을 끝냈는가. 끌어서 놓으면 놓은 자리가 다음 점이다.
  const press = useRef<{ x: number; y: number; finished: boolean } | null>(null);
  const { plot } = scale;
  // 기준점을 둘 수 있는 마지막 봉(진행 중 봉은 기준점이 될 수 없다 — 서버도 거절한다)
  const anchorLimit = Math.min(completedCount - 1, scale.last);

  const pointAt = ({ x, y, flipMagnet }: Pointer): ChartPointBody | null => {
    if (anchorLimit < scale.first) return null;
    const index = Math.min(Math.max(scale.indexAtX(x), scale.first), anchorLimit);
    const candle = candles[index];
    const raw = scale.priceAtY(Math.min(Math.max(y, plot.top), plot.bottom));
    const price =
      state.magnet !== flipMagnet ? snapToCandle(candle, raw).price : roundPrice(raw, instrumentKind);
    return { bar_date: candle.start, price };
  };

  // Ctrl(⌘)을 누르는 동안은 마그넷을 반대로 쓴다(트레이딩뷰 관례)
  const pointer = (event: MouseEvent<SVGElement>): Pointer => {
    const box = (event.currentTarget.ownerSVGElement ?? event.currentTarget).getBoundingClientRect();
    return {
      x: event.clientX - box.left,
      y: event.clientY - box.top,
      flipMagnet: event.ctrlKey || event.metaKey,
    };
  };

  // rAF 콜백은 React 밖에서 불린다 — 그때의 도구·끌기 상태는 commit과 같은 시점의 ref로 읽는다.
  const moveTo = useCommittedRef((at: Pointer) => {
    const point = pointAt(at);
    if (point === null) return;
    if (drag !== null) drawing.moveDrag(point);
    else if (state.tool !== "select") drawing.setHover(point);
  });
  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  const onPointerMove = (event: MouseEvent<SVGGElement>) => {
    pending.current = pointer(event);
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const at = pending.current;
      if (at !== null) moveTo.current(at);
    });
  };

  // 그리기 도구는 누르는 순간 점을 찍는다 — 눌렀다 놓기(클릭)를 거듭해도, 누른 채 끌어 놓아도 같은 선이 된다.
  const onPointerDown = (event: PointerEvent<SVGGElement>) => {
    if (state.tool === "select" || drag !== null || event.button !== 0) return;
    const point = pointAt(pointer(event));
    if (point === null) return;
    // 칸 밖에서 놓아도 놓기를 받는다
    event.currentTarget.setPointerCapture(event.pointerId);
    press.current = { x: event.clientX, y: event.clientY, finished: completedAnchors(state, point) !== null };
    drawing.place(point);
  };

  const onPointerUp = (event: PointerEvent<SVGGElement>) => {
    if (drag !== null) {
      drawing.endDrag();
      return;
    }
    const pressed = press.current;
    press.current = null;
    // 끌었다 놓았다: 누른 점이 선을 끝내지 않았으면 놓은 자리가 다음 점이다(Esc로 그만둔 뒤면 찍은 점이 없다)
    if (
      pressed === null ||
      pressed.finished ||
      state.points.length === 0 ||
      Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) < DRAG_DRAW_PX
    )
      return;
    const point = pointAt(pointer(event));
    if (point !== null) drawing.place(point);
  };

  const onClick = () => {
    if (drag === null && state.tool === "select") dispatch({ type: "select", lineId: null });
  };

  const selected = lines.find((line) => line.line_id === state.selectedId) ?? null;
  const handles =
    state.tool === "select" && selected !== null && isDraggable(selected, timeframe)
      ? (drag?.line.line_id === selected.line_id ? drag.anchors : selected.anchors)
      : [];
  const marker = (point: ChartPointBody) => ({
    x: scale.xOfDate(point.bar_date),
    y: scale.yOfPrice(point.price),
  });
  const hoverAt = state.tool !== "select" && hover !== null ? marker(hover) : null;
  const placed = state.points.map(marker);
  const lastPlaced = placed[placed.length - 1];

  // 그릴 선분(끄는 선은 미리보기 응답의 점) → 선마다의 라벨 자리 → 함께 배치
  const shapes = lines.map((line) => {
    const dragging = drag?.line.line_id === line.line_id;
    const paths = dragging && preview.drawing !== null ? preview.drawing.paths : line.paths;
    return { line, dragging, segments: levelSegments(paths, line.extend_right, scale) };
  });
  const draft =
    drag === null && preview.drawing !== null && state.tool !== "select"
      ? levelSegments(preview.drawing.paths, true, scale)
      : null;
  const labelsByOwner = placeLineLabels(
    [
      ...shapes.map(({ line, segments }) => ({ owner: line.line_id, segments, memo: line.memo })),
      ...overlays,
      ...(draft === null ? [] : [{ owner: DRAFT, segments: draft }]),
    ],
    instrumentKind,
    plot,
  );

  return (
    <g
      className={`drawing-surface drawing-surface--${state.tool === "select" ? "select" : "draw"}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={drawing.abandonDrag}
      onLostPointerCapture={() => {
        // 놓기(pointerup)가 먼저 와서 저장 중이면 그대로 둔다 — 놓지 못하고 잃은 끌기만 되돌린다
        if (drag !== null && !drag.committing) drawing.abandonDrag();
      }}
      onPointerLeave={() => (state.tool === "select" ? undefined : drawing.setHover(null))}
      onClick={onClick}
    >
      <rect
        className="drawing-surface__capture"
        x={plot.left}
        y={plot.top}
        width={plot.right - plot.left}
        height={plot.bottom - plot.top}
      />
      {shapes.map(({ line, dragging, segments }) => (
        <ChartLineShape
          key={line.line_id}
          segments={segments}
          tone={lineTone(line)}
          labels={labelsByOwner.get(line.line_id) ?? []}
          selected={line.line_id === state.selectedId}
          preview={dragging}
          formedX={line.created === null ? undefined : scale.xOfDate(line.created)}
          onSelect={
            state.tool === "select"
              ? () => dispatch({ type: "select", lineId: line.line_id })
              : undefined
          }
          // 수평선은 몸통 어디를 잡아도 가격을 옮긴다 — 기준점 손잡이는 선의 왼쪽 끝이라 화면 밖일 수 있다
          onGrab={
            state.tool === "select" && line.kind === "hline" && isDraggable(line, timeframe)
              ? (event) => {
                  event.stopPropagation();
                  event.currentTarget.setPointerCapture(event.pointerId);
                  drawing.startDrag(line, "body");
                }
              : undefined
          }
          onHover={(over) =>
            drawing.setHoverLineId((current) =>
              over ? line.line_id : current === line.line_id ? null : current,
            )
          }
        />
      ))}
      {overlays.map((overlay) => (
        <ChartLineShape
          key={overlay.owner}
          segments={overlay.segments}
          tone={overlay.tone}
          labels={labelsByOwner.get(overlay.owner) ?? []}
          preview
          formedX={overlay.formedX}
        />
      ))}
      {draft === null ? null : (
        <ChartLineShape segments={draft} tone="mine" labels={labelsByOwner.get(DRAFT) ?? []} preview />
      )}
      {hoverAt !== null ? (
        <g className="drawing-surface__cursor" aria-hidden="true">
          <path d={`M${plot.left} ${hoverAt.y.toFixed(1)}H${plot.right}M${hoverAt.x.toFixed(1)} ${plot.top}V${plot.bottom}`} />
          {lastPlaced !== undefined ? (
            <path
              className="drawing-surface__rubber"
              d={`M${lastPlaced.x.toFixed(1)} ${lastPlaced.y.toFixed(1)}L${hoverAt.x.toFixed(1)} ${hoverAt.y.toFixed(1)}`}
            />
          ) : null}
          <circle className="drawing-surface__dot" cx={hoverAt.x} cy={hoverAt.y} r={4} />
        </g>
      ) : null}
      {placed.map((point, index) => (
        <circle key={index} className="drawing-surface__dot drawing-surface__dot--placed" cx={point.x} cy={point.y} r={4} aria-hidden="true" />
      ))}
      {selected !== null
        ? handles.map((anchor, index) => {
            const at = marker(anchor);
            return (
              <circle
                key={index}
                className="drawing-surface__handle"
                cx={at.x}
                cy={at.y}
                r={6}
                aria-hidden="true"
                onPointerDown={(event) => {
                  event.stopPropagation();
                  event.currentTarget.setPointerCapture(event.pointerId);
                  drawing.startDrag(selected, index);
                }}
                onClick={(event) => event.stopPropagation()}
                onDoubleClick={(event) => event.stopPropagation()}
              />
            );
          })
        : null}
    </g>
  );
};
