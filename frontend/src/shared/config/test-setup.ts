import "@testing-library/jest-dom/vitest";

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
