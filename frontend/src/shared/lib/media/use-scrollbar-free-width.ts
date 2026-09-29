import { useLayoutEffect, useState, type RefObject } from "react";

/**
 * 페이지 세로 스크롤바가 없을 때 요소 content box가 받을 폭(px) — 지금 폭에 스크롤바가 차지한 폭을 더한다.
 * 배치를 뷰포트가 아니라 요소가 실제로 받는 폭으로 정할 때 쓴다(앱 셸 사이드바와 여백이 있어 요소 폭은
 * 뷰포트보다 좁다, #269). 재기 전과 `ResizeObserver`가 없는 환경(jsdom)에서는 null이다.
 *
 * 스크롤바를 빼지 않는 이유: 이 폭으로 정한 배치가 페이지 높이를 바꿔 스크롤바를 켜고 끄면, 스크롤바에
 * 흔들리는 폭은 판정을 다시 뒤집어 매 프레임 오간다(#290 리뷰 P1-1). 그래서 스크롤바가 생긴 채로 붙은
 * 배치는 스크롤바 폭만큼 덜 받을 수 있다.
 *
 * 첫 칠 전에 한 번 재어, 칠한 화면에는 폭을 모르는 배치가 보이지 않는다. 다만 첫 커밋은 폭을 모른 채
 * 하므로 그 커밋이 그린 요소는 같은 task 안에서 다시 마운트될 수 있다(#290 리뷰 P3-1).
 */
export const useScrollbarFreeWidth = (
  ref: RefObject<HTMLElement | null>,
): number | null => {
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const style = getComputedStyle(element);
      setWidth(
        element.clientWidth -
          parseFloat(style.paddingLeft) -
          parseFloat(style.paddingRight) +
          window.innerWidth -
          document.documentElement.clientWidth,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
};
