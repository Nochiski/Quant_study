/** RFC 6901 segment encoding shared by parser, cursor, schema and outline projections. */
export const escapePointerSegment = (segment: string): string =>
  segment.replace(/~/g, "~0").replace(/\//g, "~1");

export const decodePointerSegment = (segment: string): string =>
  segment.replace(/~1/g, "/").replace(/~0/g, "~");

export const pointerSegments = (pointer: string): string[] =>
  pointer === "" ? [] : pointer.slice(1).split("/").map(decodePointerSegment);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** tree에서 pointer 값을 읽는다. 경로가 없으면 `present: false`. 자기 소유 키만 본다(prototype 제외). */
export const valueAtPointer = (
  tree: unknown,
  pointer: string,
): { present: boolean; value: unknown } => {
  let current = tree;
  for (const segment of pointerSegments(pointer)) {
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(segment) || Number(segment) >= current.length)
        return { present: false, value: undefined };
      current = current[Number(segment)];
    } else if (isRecord(current) && Object.hasOwn(current, segment)) {
      current = current[segment];
    } else {
      return { present: false, value: undefined };
    }
  }
  return { present: tree !== undefined, value: current };
};

/** RFC 6901 JSON Pointer syntax. URI decoding remains the router's responsibility. */
export const isJsonPointer = (pointer: string): boolean =>
  pointer === "" || /^(?:\/(?:[^~]|~[01])*)+$/.test(pointer);
