import type { ParsedSource, SourceRange } from "./parse";

/** Deepest JSON Pointer containing a cursor offset; key hits win equal-width value hits. */
export const locatePointer = (
  parsed: Pick<ParsedSource, "valueRanges" | "keyRanges">,
  offset: number,
): string | null => {
  const hits: { pointer: string; width: number; key: boolean }[] = [];
  const consider = (
    pointer: string,
    range: SourceRange,
    key: boolean,
  ): void => {
    if (offset < range.start.offset || offset > range.end.offset) return;
    const width = range.end.offset - range.start.offset;
    hits.push({ pointer, width, key });
  };
  for (const [pointer, range] of parsed.valueRanges)
    consider(pointer, range, false);
  for (const [pointer, range] of parsed.keyRanges)
    consider(pointer, range, true);
  hits.sort((left, right) =>
    left.width === right.width
      ? Number(right.key) - Number(left.key)
      : left.width - right.width,
  );
  return hits[0]?.pointer ?? null;
};
