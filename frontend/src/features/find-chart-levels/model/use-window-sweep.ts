import { useState, type PointerEvent } from "react";

import { DRAG_THRESHOLD_PX, type ChartCandleBody, type ChartScale, type DateRange } from "../../../entities/chart";
import { sweptWindow, windowDates, type BarWindow } from "./analysis-window";

type Sweep = { pointerId: number; x: number; from: number; window: BarWindow | null };

/**
 * Shift를 누른 채 차트를 끌어 판정·분석 구간을 새로 긋는다 — 누른 봉부터 놓은 봉까지(트레이딩뷰의 Shift+끌기 범위
 * 고르기와 같은 손짓). 생성 패널이 열려 있을 때(`enabled`)만 받는다. 차트(SVG)의 캡처 단계에서 받아 멈추므로 차트
 * 이동·선 그리기·선 고르기로 번지지 않는다. 문턱(`DRAG_THRESHOLD_PX`)을 넘기 전의 Shift+누르기는 구간을 바꾸지 않고,
 * 놓는 순간 구간을 바꿔 다시 판정한다. 끄는 동안의 구간은 `window`로 내보내 음영이 따라오게 한다.
 */
export const useWindowSweep = ({
  scale,
  completed,
  enabled,
  onChange,
}: {
  scale: ChartScale | null;
  /** 지금 주기의 완성 봉 전부(구간은 이 안에서만 고른다). */
  completed: readonly ChartCandleBody[];
  enabled: boolean;
  onChange: (window: DateRange) => void;
}) => {
  const [sweep, setSweep] = useState<Sweep | null>(null);
  const indexAt = (event: PointerEvent<SVGSVGElement>) =>
    scale === null ? 0 : scale.indexAtX(event.clientX - event.currentTarget.getBoundingClientRect().left);
  const owns = (event: PointerEvent<SVGSVGElement>) => sweep !== null && sweep.pointerId === event.pointerId;

  return {
    /** 끄는 중인 구간(문턱을 넘고 폭이 되면). 없으면 null. */
    window: sweep?.window ?? null,
    svgProps: {
      onPointerDownCapture: (event: PointerEvent<SVGSVGElement>) => {
        if (!enabled || scale === null || !event.shiftKey || event.button !== 0 || completed.length === 0) return;
        event.stopPropagation();
        // 글자 고르기·초점 옮기기 같은 기본 동작도 막는다
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        setSweep({ pointerId: event.pointerId, x: event.clientX, from: indexAt(event), window: null });
      },
      onPointerMoveCapture: (event: PointerEvent<SVGSVGElement>) => {
        if (sweep === null || !owns(event)) return;
        event.stopPropagation();
        if (sweep.window === null && Math.abs(event.clientX - sweep.x) < DRAG_THRESHOLD_PX) return;
        const next = sweptWindow(sweep.from, indexAt(event), completed.length);
        if (next?.start !== sweep.window?.start || next?.end !== sweep.window?.end) setSweep({ ...sweep, window: next });
      },
      onPointerUpCapture: (event: PointerEvent<SVGSVGElement>) => {
        if (sweep === null || !owns(event)) return;
        event.stopPropagation();
        if (sweep.window !== null) onChange(windowDates(completed, sweep.window));
        setSweep(null);
      },
      onPointerCancelCapture: (event: PointerEvent<SVGSVGElement>) => {
        if (owns(event)) setSweep(null);
      },
    },
  };
};
