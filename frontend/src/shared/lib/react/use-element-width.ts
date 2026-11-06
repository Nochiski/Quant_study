import { useLayoutEffect, useState, type RefObject } from "react";

/**
 * 요소의 지금 폭(px). 처음 그릴 때 한 번 재고 ResizeObserver로 따라간다 — px 좌표로 그리는 SVG(섹터맵·캔들
 * 차트)가 칸 폭에 맞춰 다시 그린다. ResizeObserver가 없는 환경(테스트)에서는 처음 잰 값에 머문다.
 */
export const useElementWidth = (ref: RefObject<HTMLElement | null>): number => {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    setWidth(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
};
