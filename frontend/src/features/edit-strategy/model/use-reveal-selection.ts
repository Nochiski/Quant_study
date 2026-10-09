import { useEffect, useRef } from "react";

/**
 * 선택한 pointer의 카드·노드를 화면 안으로 끌어온다(WORKFLOW P1-01 리뷰 P2-2). 문제 목록이 탭과
 * 무관하게 보이면서 Form·Graph 탭에 머문 채 문제 행을 누를 수 있게 됐는데, 대상 카드가 스크롤
 * 밖이면 `aria-current`만 바뀌어 클릭이 먹지 않은 것처럼 보인다.
 *
 * **마지막 매치**를 고른다. Graph 탭은 읽기 전용 plan 노드와 그 아래 편집기 행에 같은 pointer로
 * `aria-current`를 동시에 붙이고, 편집기 section은 바깥 패널 section 안에 중첩돼 훅이 둘 걸린다.
 * 첫 매치를 쓰면 자식 effect가 편집기 행을 올린 뒤 부모 effect가 위쪽 plan 노드로 스크롤을
 * 되돌린다(2차 리뷰 R2-1). DOM 순서상 마지막이 가장 깊고 구체적인 선택이라 두 훅이 같은 요소로
 * 수렴한다.
 *
 * 스크롤은 DOM이라는 외부 시스템과의 동기화라 effect가 맞는 자리다 — commit 뒤에 불려
 * 새 `aria-current`가 이미 붙어 있다. jsdom에는 `scrollIntoView`가 없어 optional call로 부른다
 * (`shared/ui/command-palette.tsx`와 같은 방식).
 *
 * @param signal 같은 pointer를 다시 고른 것도 새 요청으로 보게 하는 값. 문제 행을 두 번 누르면
 *   URL은 그대로라 pointer가 안 바뀌므로, 탐색 훅이 클릭마다 올리는 카운터를 같이 넘긴다
 *   (2차 리뷰 R2-2).
 */
export const useRevealSelection = <El extends HTMLElement>(
  selectedPointer: string | undefined,
  signal?: number,
) => {
  const container = useRef<El>(null);
  useEffect(() => {
    if (selectedPointer === undefined) return;
    const found = container.current?.querySelectorAll('[aria-current="true"]');
    found?.[found.length - 1]?.scrollIntoView?.({ block: "nearest" });
  }, [selectedPointer, signal]);
  return container;
};
