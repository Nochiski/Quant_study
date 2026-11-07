import { useState, type KeyboardEvent, type PointerEvent } from "react";

import type { ChartCandleBody, ChartScale, DateRange } from "../../../entities/chart";
import { t } from "../../../shared/config";
import {
  barWindowOf,
  moveWindowEdge,
  nudgeWindowEdge,
  windowDates,
  type BarWindow,
  type WindowEdge,
} from "../model/analysis-window";

type AnalysisWindowOverlayProps = {
  scale: ChartScale;
  /** 지금 주기의 완성 봉 전부(구간은 이 안에서만 고른다). */
  completed: readonly ChartCandleBody[];
  /** 지금 구간(서버가 맞춘 응답 구간, 기다리는 동안은 고친 날짜). 없으면 그리지 않는다. */
  window: DateRange | null;
  onChange: (window: DateRange) => void;
  /** 테두리에 초점이 있을 때 Esc — 패널의 Esc와 같이 패널을 닫는다. */
  onEscape: () => void;
};

const EDGES: readonly WindowEdge[] = ["start", "end"];
const GRIP_HEIGHT = 28;
// 테두리 손잡이를 누를 수 있는 폭(px) — 포인터 목표 크기 24px 이상
const HIT_WIDTH = 24;

/**
 * 메인 차트 위 판정·분석 구간: 음영과 좌우 테두리 손잡이(role=slider). 끌면 봉 단위로 붙고 끄는 동안 날짜 툴팁이 뜨며,
 * 놓는 순간 구간을 바꿔 다시 판정한다. 초점에서 ←/→ 한 봉, Shift는 열 봉, Home/End는 끝까지, Esc는 패널 닫기. 차트
 * 줌·이동 키·끌기로 번지지 않게 멈춘다. 낭독·툴팁 날짜는 날짜 칸과 같은 봉 시작일이다.
 */
export const AnalysisWindowOverlay = ({ scale, completed, window, onChange, onEscape }: AnalysisWindowOverlayProps) => {
  const [drag, setDrag] = useState<{ edge: WindowEdge; window: BarWindow; from: BarWindow } | null>(null);
  const resting = window === null ? null : barWindowOf(completed, window);
  const shown = drag?.window ?? resting;
  if (shown === null || resting === null) return null;
  const { plot, step } = scale;
  const count = completed.length;
  const dates = windowDates(completed, shown);
  const edgeX = (edge: WindowEdge, bars: BarWindow) =>
    edge === "start" ? scale.xOfIndex(bars.start) - step / 2 : scale.xOfIndex(bars.end) + step / 2;
  const commit = (next: BarWindow, before: BarWindow) => {
    if (next.start !== before.start || next.end !== before.end) onChange(windowDates(completed, next));
  };

  const pointerIndex = (event: PointerEvent<SVGGElement>, edge: WindowEdge) => {
    const svg = event.currentTarget.ownerSVGElement;
    const x = event.clientX - (svg?.getBoundingClientRect().left ?? 0);
    // 테두리는 봉 칸 사이에 있다 — 시작 테두리는 그 오른쪽 봉, 끝 테두리는 그 왼쪽 봉이다
    return scale.indexAtX(edge === "start" ? x + step / 2 : x - step / 2);
  };

  const left = edgeX("start", shown);
  const right = edgeX("end", shown);
  return (
    <g className="analysis-window">
      <rect
        className="analysis-window__band"
        x={left}
        y={plot.top}
        width={Math.max(right - left, 0)}
        height={plot.bottom - plot.top}
      />
      {EDGES.map((edge) => {
        const x = edgeX(edge, shown);
        const middle = (plot.top + plot.bottom) / 2;
        return (
          <g
            key={edge}
            className="analysis-window__edge"
            role="slider"
            tabIndex={0}
            aria-label={t(edge === "start" ? "chart.finder.window.startEdge" : "chart.finder.window.endEdge")}
            aria-orientation="horizontal"
            aria-valuemin={0}
            aria-valuemax={count - 1}
            aria-valuenow={shown[edge]}
            aria-valuetext={dates[edge]}
            data-active={drag?.edge === edge ? "true" : undefined}
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              // 차트 끌기 이동·선 고르기로 번지지 않는다
              event.stopPropagation();
              event.currentTarget.setPointerCapture(event.pointerId);
              setDrag({ edge, window: resting, from: resting });
            }}
            onPointerMove={(event) => {
              if (drag === null || drag.edge !== edge) return;
              const next = moveWindowEdge(drag.window, edge, pointerIndex(event, edge), count);
              if (next.start !== drag.window.start || next.end !== drag.window.end) setDrag({ ...drag, window: next });
            }}
            onPointerUp={(event) => {
              if (drag === null) return;
              event.stopPropagation();
              commit(drag.window, drag.from);
              setDrag(null);
            }}
            onPointerCancel={() => setDrag(null)}
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={(event) => event.stopPropagation()}
            onKeyDown={(event: KeyboardEvent<SVGGElement>) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                onEscape();
                return;
              }
              const next = nudgeWindowEdge(resting, edge, event.key, event.shiftKey, count);
              if (next === null) return;
              event.preventDefault();
              event.stopPropagation();
              commit(next, resting);
            }}
          >
            <rect
              className="analysis-window__hit"
              x={x - HIT_WIDTH / 2}
              y={plot.top}
              width={HIT_WIDTH}
              height={plot.bottom - plot.top}
            />
            <path className="analysis-window__line" d={`M${x.toFixed(1)} ${plot.top}V${plot.bottom}`} />
            <rect
              className="analysis-window__grip"
              x={x - 4}
              y={middle - GRIP_HEIGHT / 2}
              width={8}
              height={GRIP_HEIGHT}
              rx={3}
            />
            <text className="analysis-window__tip" x={x} y={plot.top + 14} textAnchor="middle">
              {dates[edge]}
            </text>
          </g>
        );
      })}
    </g>
  );
};
