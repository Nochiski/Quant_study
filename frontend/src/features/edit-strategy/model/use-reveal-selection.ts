import { useEffect, useRef } from "react";

/**
 * 선택한 pointer의 카드·노드를 화면 안으로 끌어온다(WORKFLOW P1-01 리뷰 P2-2). 문제 목록이 탭과
 * 무관하게 보이면서 Form·Graph 탭에 머문 채 문제 행을 누를 수 있게 됐는데, 대상 카드가 스크롤
 * 밖이면 `aria-current`만 바뀌어 클릭이 먹지 않은 것처럼 보인다.
 *
 * 스크롤은 DOM이라는 외부 시스템과의 동기화라 effect가 맞는 자리다 — commit 뒤에 불려
 * 새 `aria-current`가 이미 붙어 있다. jsdom에는 `scrollIntoView`가 없어 optional call로 부른다
 * (`shared/ui/command-palette.tsx`와 같은 방식).
 */
export const useRevealSelection = <Element extends HTMLElement>(
  selectedPointer: string | undefined,
) => {
  const container = useRef<Element>(null);
  useEffect(() => {
    if (selectedPointer === undefined) return;
    container.current
      ?.querySelector('[aria-current="true"]')
      ?.scrollIntoView?.({ block: "nearest" });
  }, [selectedPointer]);
  return container;
};
