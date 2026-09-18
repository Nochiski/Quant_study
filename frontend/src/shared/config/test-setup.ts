import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// One test-infrastructure owner sets the async budget for CodeMirror route tests. The product
// deliberately debounces parse/compile requests, and cold CI workers also load the editor chunk;
// feature tests should wait on observable state instead of duplicating per-call timeouts.
// 10초: 54파일 병렬 실행에서 `app/__tests__/document-routes.test.tsx`(단독 케이스당 약 1초)가 5초 예산을
// 넘겨 다른 케이스가 번갈아 실패했다(#143 CI 재실행, #144·#147·#149 리뷰 1회차; Phase 5 backlog 15).
// 케이스 timeout(그 파일 15초)보다 짧게 둔다. 실패하는 테스트의 실패 지연이 그만큼 늘어나는 것이 비용이다.
configure({ asyncUtilTimeout: 10_000 });

// jsdom has no layout: CodeMirror needs these to measure the viewport, and any test that mounts
// a page with the source editor (router tests included) goes through this setup.
const rect = () => ({
  x: 0,
  y: 0,
  top: 0,
  left: 0,
  bottom: 0,
  right: 0,
  width: 0,
  height: 0,
  toJSON: () => ({}),
});
const rects = () =>
  ({
    length: 0,
    item: () => null,
    [Symbol.iterator]: [][Symbol.iterator],
  }) as unknown as DOMRectList;

Range.prototype.getClientRects = rects;
Range.prototype.getBoundingClientRect = rect as unknown as () => DOMRect;
document.createRange = () => {
  const range = new Range();
  range.getBoundingClientRect = rect as unknown as () => DOMRect;
  range.getClientRects = rects;
  return range;
};
