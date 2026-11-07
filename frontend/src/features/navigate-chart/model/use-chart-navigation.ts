import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from "react";

import {
  chartScale,
  defaultViewport,
  panViewport,
  viewportRange,
  wheelZoomFactor,
  zoomViewport,
  type ChartCandleBody,
  type ChartScale,
  type ChartViewport,
  type DateRange,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";

/** 보기를 바꾸고 이만큼 멈추면 보이는 날짜 구간을 주소·날짜 칸에 쓴다(ms). */
const RANGE_WRITE_DELAY_MS = 300;
/** 이만큼(px) 움직여야 끌기로 본다 — 그보다 짧으면 누르기(선 고르기·빈 곳 누르기)다. */
const DRAG_THRESHOLD = 3;
/** 키보드 한 번의 확대 배율과 이동 비율(보이는 봉 칸 수에 대한). */
const KEY_ZOOM = 1.25;
const KEY_PAN_RATIO = 0.1;
// 휠 `deltaMode`가 줄·쪽 단위일 때 px로 바꾸는 값(브라우저 관례)
const LINE_PX = 16;

type ChartNavigationInput = {
  candles: readonly ChartCandleBody[] | undefined;
  /** 계열 이름(종목·주기). 바뀌면 보기를 새로 만든다. */
  series: string;
  /** 주소가 정한 표시 기간(기간 버튼·날짜 칸·앞서 쓴 줌 결과). 캔들을 받기 전에는 null. */
  range: DateRange | null;
  /** 되돌릴 기본 보기의 기간(고른 기간 버튼, 없으면 기본 기간). */
  baseRange: DateRange | null;
  /** 차트 SVG의 크기(px). 보기 창과 함께 좌표 변환(`chartScale`)을 만들고, 끌기·스크롤의 px를 그 봉 폭으로 바꾼다. */
  width: number;
  height: number;
  /** 빈 곳 끌기로 옮길 수 있는가(선택 도구일 때). 그리기 도구 중에는 끌기가 점 찍기와 겹치므로 끈다. */
  canDrag: boolean;
  /** 보기가 멈춘 뒤의 날짜 구간. null은 기본 보기로 되돌렸다는 뜻이다(주소의 날짜를 지운다). */
  onRange: (range: DateRange | null) => void;
};

type ViewState = {
  /** 이 보기를 만든 주소 기간(계열 포함). 주소가 다른 기간으로 바뀌면 보기를 새로 만든다. */
  key: string;
  viewport: ChartViewport | null;
  /** 이 보기가 주소에 써 둔 기간의 키 — 그 주소가 돌아와도 보기를 새로 만들지 않는다. */
  written: string | null;
  /** 바깥(기간 버튼·뒤로 가기·더블클릭)에서 보기를 새로 만든 횟수. 그 전에 걸어 둔 주소 쓰기는 버린다. */
  epoch: number;
};

const keyOf = (series: string, range: DateRange) => `${series}|${range.start}|${range.end}`;

/**
 * TradingView식 줌·이동 한 벌. 보기 창(`ChartViewport`)은 화면 상태이고 계산은 `entities/chart`의 순수 함수가
 * 한다. 캔들은 계열 전부를 한 번 받아 둔 것(쿼리 캐시)이라 줌·이동 중에 서버를 다시 부르지 않는다.
 *
 * - 휠 = 커서 자리 중심 확대·축소, 가로 스크롤·Shift+휠 = 이동, 선택 도구에서 빈 곳 끌기 = 이동, 더블클릭 = 기본
 *   보기. 차트에 초점이 있으면 +/=·− 확대·축소, ←/→ 이동, Home/0 기본 보기.
 * - 휠·끌기는 rAF 한 번으로 묶어 상태를 바꾼다. 그림 칸과 봉 폭은 `chartScale` 하나에서 읽는다(다시 재지 않는다).
 * - 보기가 멈추면(`RANGE_WRITE_DELAY_MS`) 보이는 날짜 구간을 `onRange`로 넘긴다 — 주소(replace)와 날짜 칸이 그
 *   값을 갖고, 그 주소가 돌아와도 보기를 다시 만들지 않는다. 기간 버튼·날짜 칸·뒤로 가기처럼 주소가 다른 기간으로
 *   바뀌면 그 기간의 기본 보기를 새로 만든다.
 */
export const useChartNavigation = ({
  candles,
  series,
  range,
  baseRange,
  width,
  height,
  canDrag,
  onRange,
}: ChartNavigationInput) => {
  const [view, setView] = useState<ViewState | null>(null);
  if (candles !== undefined && range !== null) {
    const key = keyOf(series, range);
    if (view === null || view.key !== key) {
      // 렌더 중 상태 맞추기: 주소의 기간이 바뀌었다 — 이 보기가 써 둔 기간이면 보기를 그대로 두고, 바깥에서
      // 바꾼 기간이면 그 기간의 기본 보기를 새로 만든다
      setView(
        view !== null && view.written === key
          ? { ...view, key, written: null }
          : {
              key,
              viewport: defaultViewport(candles, range),
              written: null,
              epoch: (view?.epoch ?? 0) + 1,
            },
      );
    }
  }
  const viewport = view?.viewport ?? null;
  const count = candles?.length ?? 0;
  const scale: ChartScale | null =
    candles !== undefined && viewport !== null && width > 0
      ? chartScale({ candles, view: viewport, width, height })
      : null;

  // 타이머·rAF·native 리스너는 React 밖에서 불린다 — 그때의 값은 commit과 같은 시점의 ref로 읽는다
  const latest = useCommittedRef({ view, candles, series, baseRange, scale, onRange });
  const writeTimer = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const wheel = useRef({ dx: 0, zoom: 0, anchor: 1 });
  const drag = useRef<{ pointerId: number; x: number; viewport: ChartViewport; moved: boolean } | null>(null);
  const dragX = useRef<number | null>(null);
  const suppressClick = useRef(false);
  const [panning, setPanning] = useState(false);

  useEffect(
    () => () => {
      if (writeTimer.current !== null) window.clearTimeout(writeTimer.current);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  /** 보기를 바꾸고, 멈추면 그 날짜 구간을 주소에 쓰도록 건다(같은 보기에서 다시 부르면 다시 센다). */
  const move = (change: (current: ChartViewport, count: number) => ChartViewport) => {
    const { view: current, candles: all } = latest.current;
    if (current === null || current.viewport === null || all === undefined) return;
    const next = change(current.viewport, all.length);
    const epoch = current.epoch;
    setView((state) => (state === null || state.epoch !== epoch ? state : { ...state, viewport: next }));
    if (writeTimer.current !== null) window.clearTimeout(writeTimer.current);
    writeTimer.current = window.setTimeout(() => {
      writeTimer.current = null;
      const now = latest.current;
      // 그사이 바깥에서 보기를 새로 만들었으면(기간 버튼 등) 앞 보기의 구간을 쓰지 않는다
      if (now.view === null || now.view.epoch !== epoch || now.candles === undefined) return;
      const written = viewportRange(next, now.candles);
      if (written === null) return;
      const key = keyOf(now.series, written);
      if (key === now.view.key) return;
      setView((state) => (state === null || state.epoch !== epoch ? state : { ...state, written: key }));
      now.onRange(written);
    }, RANGE_WRITE_DELAY_MS);
  };
  const moveRef = useCommittedRef(move);

  /** 기본 보기(고른 기간 버튼의 기간)로 되돌린다. 걸어 둔 주소 쓰기는 버리고 주소의 날짜를 지운다. */
  const reset = () => {
    const { view: current, candles: all, baseRange: base } = latest.current;
    if (current === null || all === undefined || base === null) return;
    if (writeTimer.current !== null) window.clearTimeout(writeTimer.current);
    writeTimer.current = null;
    setView({
      key: current.key,
      viewport: defaultViewport(all, base),
      written: keyOf(latest.current.series, base),
      epoch: current.epoch + 1,
    });
    latest.current.onRange(null);
  };

  const stepPx = () => latest.current.scale?.step ?? 1;

  // 한 프레임에 모인 끌기·가로 스크롤·휠 확대를 한 번에 적용한다(따로 부르면 뒤 것이 앞 것을 덮는다)
  const flushFrame = () => {
    frame.current = null;
    const pending = wheel.current;
    wheel.current = { dx: 0, zoom: 0, anchor: pending.anchor };
    const dragging = drag.current;
    const dragBars =
      dragging !== null && dragging.moved && dragX.current !== null
        ? -(dragX.current - dragging.x) / stepPx()
        : null;
    if (dragBars === null && pending.dx === 0 && pending.zoom === 0) return;
    const scrollBars = pending.dx / stepPx();
    moveRef.current((current, total) => {
      let next = dragging !== null && dragBars !== null ? panViewport(dragging.viewport, dragBars, total) : current;
      if (scrollBars !== 0) next = panViewport(next, scrollBars, total);
      if (pending.zoom !== 0) next = zoomViewport(next, wheelZoomFactor(pending.zoom), pending.anchor, total);
      // 끄는 중이면 기준을 이 보기·이 자리로 옮긴다 — 끄는 동안 휠로 확대한 것이 다음 끌기 계산에 덮이지 않는다
      if (dragging !== null && dragX.current !== null) {
        dragging.viewport = next;
        dragging.x = dragX.current;
      }
      return next;
    });
  };
  const schedule = () => {
    if (frame.current === null) frame.current = requestAnimationFrame(flushFrame);
  };

  // 휠: 페이지 스크롤 대신 차트를 확대·축소·이동한다. 페이지 스크롤을 막아야 해서 passive가 아닌 native 리스너를
  // 차트 SVG가 붙을 때 한 번 달고(ref 콜백의 정리 함수로 뗀다), 판정은 commit 시점의 ref 함수가 한다.
  const onWheel = (event: WheelEvent, element: SVGSVGElement) => {
    if (scale === null) return;
    event.preventDefault();
    const { plot } = scale;
    const unit = event.deltaMode === 1 ? LINE_PX : event.deltaMode === 2 ? plot.right - plot.left : 1;
    const dx = event.deltaX * unit;
    const dy = event.deltaY * unit;
    const pending = wheel.current;
    if (event.shiftKey && dx === 0) pending.dx += dy;
    else if (Math.abs(dx) > Math.abs(dy)) pending.dx += dx;
    else {
      pending.zoom += dy;
      const x = event.clientX - element.getBoundingClientRect().left;
      pending.anchor = (x - plot.left) / (plot.right - plot.left);
    }
    schedule();
  };
  const onWheelRef = useCommittedRef(onWheel);
  const wheelRef = useCallback(
    (element: SVGSVGElement | null) => {
      if (element === null) return;
      const listener = (event: WheelEvent) => onWheelRef.current(event, element);
      element.addEventListener("wheel", listener, { passive: false });
      return () => element.removeEventListener("wheel", listener);
    },
    [onWheelRef],
  );

  const onPointerDown = (event: PointerEvent<SVGSVGElement>) => {
    // 앞 끌기 끝에 삼킬 누르기가 오지 않았으면(칸 밖에서 놓음) 새 누르기에서 잊는다
    suppressClick.current = false;
    // 손잡이는 누르기를 멈춰(stopPropagation) 여기까지 오지 않는다 — 손잡이 끌기가 먼저다
    if (!canDrag || event.button !== 0 || viewport === null) return;
    drag.current = { pointerId: event.pointerId, x: event.clientX, viewport, moved: false };
  };
  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const dragging = drag.current;
    if (dragging === null || dragging.pointerId !== event.pointerId) return;
    // 단추를 놓았는데 놓기(pointerup)를 못 받았다(칸 밖에서 놓음 등) — 끌기를 끝낸다
    if (event.buttons === 0) {
      endPan(event);
      return;
    }
    if (!dragging.moved) {
      if (Math.abs(event.clientX - dragging.x) < DRAG_THRESHOLD) return;
      dragging.moved = true;
      // 끌기로 보이고 나서야 포인터를 붙잡는다 — 그 전에 붙잡으면 누르기(click)가 선에 가지 않는다
      event.currentTarget.setPointerCapture(event.pointerId);
      setPanning(true);
    }
    dragX.current = event.clientX;
    schedule();
  };
  const endPan = (event: PointerEvent<SVGSVGElement>) => {
    const dragging = drag.current;
    if (dragging === null || dragging.pointerId !== event.pointerId) return;
    drag.current = null;
    dragX.current = null;
    if (dragging.moved) {
      // 끌기 끝의 누르기는 선 고르기·고른 것 풀기로 번지지 않게 한 번 삼킨다
      suppressClick.current = true;
      setPanning(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || viewport === null) return;
    const span = viewport.to - viewport.from;
    // 키보드 확대는 최신 봉이 보이면 오른쪽 끝을 붙들고(오른쪽 정렬 유지), 아니면 가운데를 붙든다
    const anchor = viewport.to >= count - 0.5 ? 1 : 0.5;
    const shift = Math.max(1, Math.round(span * KEY_PAN_RATIO));
    if (event.key === "+" || event.key === "=") move((current, total) => zoomViewport(current, 1 / KEY_ZOOM, anchor, total));
    else if (event.key === "-" || event.key === "_") move((current, total) => zoomViewport(current, KEY_ZOOM, anchor, total));
    else if (event.key === "ArrowLeft") move((current, total) => panViewport(current, -shift, total));
    else if (event.key === "ArrowRight") move((current, total) => panViewport(current, shift, total));
    else if (event.key === "Home" || event.key === "0") reset();
    else return;
    event.preventDefault();
  };

  return {
    /** 지금 보기의 좌표 변환. 보일 봉이 없거나(빈 기간) 폭을 재기 전이면 null. */
    scale,
    /** 보기 창이 없다 — 표시 기간에 걸친 봉이 없다(상장 전 구간 등). */
    empty: candles !== undefined && range !== null && viewport === null,
    /** 고른 기간의 기본 보기로 되돌린다(빈 기간 안내의 되돌리기 버튼도 쓴다). */
    reset,
    /** 끌어서 옮기는 중(커서 모양). */
    panning,
    svgProps: {
      ref: wheelRef,
      onPointerDown,
      onPointerMove,
      onPointerUp: endPan,
      onPointerCancel: endPan,
      onClickCapture: (event: MouseEvent<SVGSVGElement>) => {
        if (!suppressClick.current) return;
        suppressClick.current = false;
        event.stopPropagation();
      },
      // 선·손잡이의 더블클릭은 그쪽에서 멈춘다(stopPropagation) — 여기 오는 것은 빈 곳이다
      onDoubleClick: () => {
        if (canDrag) reset();
      },
      onKeyDown,
    },
  };
};

