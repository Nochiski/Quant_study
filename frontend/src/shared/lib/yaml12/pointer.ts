/** RFC 6901 segment encoding shared by parser, cursor, schema and outline projections. */
export const escapePointerSegment = (segment: string): string =>
  segment.replace(/~/g, "~0").replace(/\//g, "~1");

export const decodePointerSegment = (segment: string): string =>
  segment.replace(/~1/g, "/").replace(/~0/g, "~");

export const pointerSegments = (pointer: string): string[] =>
  pointer === "" ? [] : pointer.slice(1).split("/").map(decodePointerSegment);
