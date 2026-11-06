import { useEffect, useRef, type MouseEvent } from "react";

import {
  ChartLineShape,
  levelSegments,
  lineTone,
  roundPrice,
  snapToCandle,
  type ChartCandleBody,
  type ChartLineBody,
  type ChartPointBody,
  type ChartScale,
  type InstrumentKind,
  type Timeframe,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";
import { isDraggable } from "../model/line-edit";
import type { ChartDrawing } from "../model/use-chart-drawing";

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
};

/**
 * 차트 위 그리기 칸: 포인터가 가리키는 봉·가격(마그넷이면 그 봉의 시·고·저·종가 중 가장 가까운 값), 도구별 점
 * 찍기, 저장 선 고르기와 손잡이 끌기. 미리보기는 서버 응답(`drawing.preview`)의 두 점을 그대로 잇는다.
 *
 * 포인터 움직임은 rAF 한 번으로 묶어 상태를 바꾼다 — 서버 미리보기 호출은 그 위에서 다시 잠깐 멈춘 초안만 간다.
 */
export const DrawingSurface = ({
  drawing,
  scale,
  candles,
  completedCount,
  instrumentKind,
  timeframe,
  lines,
}: DrawingSurfaceProps) => {
  const { state, dispatch, hover, drag, preview } = drawing;
  const frame = useRef<number | null>(null);
  const pending = useRef<{ x: number; y: number } | null>(null);
  const { plot } = scale;
  // 기준점을 둘 수 있는 마지막 봉(진행 중 봉은 기준점이 될 수 없다 — 서버도 거절한다)
  const anchorLimit = Math.min(completedCount - 1, scale.last);

  const pointAt = (x: number, y: number): ChartPointBody | null => {
    if (anchorLimit < scale.first) return null;
    const index = Math.min(Math.max(scale.indexAtX(x), scale.first), anchorLimit);
    const candle = candles[index];
    const raw = scale.priceAtY(Math.min(Math.max(y, plot.top), plot.bottom));
    const price = state.magnet ? snapToCandle(candle, raw).price : roundPrice(raw, instrumentKind);
    return { bar_date: candle.start, price };
  };

  const local = (event: MouseEvent<SVGElement>) => {
    const box = (event.currentTarget.ownerSVGElement ?? event.currentTarget).getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  };

  // rAF 콜백은 React 밖에서 불린다 — 그때의 도구·끌기 상태는 commit과 같은 시점의 ref로 읽는다.
  const moveTo = useCommittedRef((x: number, y: number) => {
    const point = pointAt(x, y);
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
    pending.current = local(event);
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const at = pending.current;
      if (at !== null) moveTo.current(at.x, at.y);
    });
  };

  const onPointerUp = () => {
    if (drag !== null) drawing.endDrag();
  };

  const onClick = (event: MouseEvent<SVGGElement>) => {
    if (drag !== null) return;
    if (state.tool === "select") {
      dispatch({ type: "select", lineId: null });
      return;
    }
    const at = local(event);
    const point = pointAt(at.x, at.y);
    if (point !== null) drawing.place(point);
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

  return (
    <g
      className={`drawing-surface drawing-surface--${state.tool === "select" ? "select" : "draw"}`}
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
      {lines.map((line) => {
        const dragging = drag?.line.line_id === line.line_id;
        const paths = dragging && preview.drawing !== null ? preview.drawing.paths : line.paths;
        return (
          <ChartLineShape
            key={line.line_id}
            segments={levelSegments(paths, line.extend_right, scale)}
            tone={lineTone(line)}
            instrumentKind={instrumentKind}
            selected={line.line_id === state.selectedId}
            preview={dragging}
            memo={line.memo}
            onSelect={
              state.tool === "select"
                ? () => dispatch({ type: "select", lineId: line.line_id })
                : undefined
            }
          />
        );
      })}
      {drag === null && preview.drawing !== null && state.tool !== "select" ? (
        <ChartLineShape
          segments={levelSegments(preview.drawing.paths, true, scale)}
          tone="mine"
          instrumentKind={instrumentKind}
          preview
        />
      ) : null}
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
              />
            );
          })
        : null}
    </g>
  );
};
