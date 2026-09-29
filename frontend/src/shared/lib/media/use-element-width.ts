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
    // 첫 칠 전에 한 번 잰다. 관찰 결과는 칠한 뒤에야 반영되므로, 그것만 기다리면 폭을 모르는 배치로
    // 한 프레임을 그렸다가 바꾼다(열어 둔 AI 사이드바가 붙었다가 오버레이로 옮겨 두 번 마운트된다).
    const style = getComputedStyle(element);
    setWidth(
      element.clientWidth -
        parseFloat(style.paddingLeft) -
        parseFloat(style.paddingRight),
    );
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
};
