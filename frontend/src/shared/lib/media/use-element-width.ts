import { useLayoutEffect, useState, type RefObject } from "react";

/**
 * 요소 content box의 폭(px). 배치를 뷰포트가 아니라 요소가 실제로 받은 폭으로 정할 때 쓴다 — 앱 셸
 * 사이드바와 여백이 있어 요소 폭은 뷰포트보다 좁다(#269). 재기 전과 `ResizeObserver`가 없는
 * 환경(jsdom)에서는 null이다.
 */
export const useElementWidth = (
  ref: RefObject<HTMLElement | null>,
): number | null => {
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
};
