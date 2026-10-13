import { useSyncExternalStore } from "react";

const subscribe = (onChange: () => void) => {
  window.addEventListener("resize", onChange);
  return () => window.removeEventListener("resize", onChange);
};

/** 창(레이아웃 뷰포트)의 높이(px). 창 크기가 바뀌면 다시 그린다. */
export const useViewportHeight = (): number =>
  useSyncExternalStore(subscribe, () => window.innerHeight);
