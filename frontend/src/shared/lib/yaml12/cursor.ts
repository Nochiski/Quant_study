/**
 * Cursor → JSON Pointer for block-style YAML (WORKFLOW P3-03).
 *
 * Completion has to work on text that does not parse yet (a half-typed key), so this does not
 * use the parser: it reads indentation and `- ` item markers on the lines above the cursor, the
 * way a human reads a verbose StrategySpec document. Flow style (`{}`/`[]`) and multi-line
 * scalars are out of scope and simply yield no context.
 */

export type YamlCursorContext = {
  /** "key": the cursor is typing a mapping key; "value": the scalar value of `key` (or of a sequence item). */
  mode: "key" | "value";
  /** JSON Pointer of the mapping being completed (key mode) or of the value (value mode). */
  pointer: string;
  /** Key whose value is being typed, null for a scalar sequence item. */
  key: string | null;
  /** Text typed so far for the current token, and where it starts. */
  prefix: string;
  from: number;
  /** Keys already present in the same mapping, excluding the cursor line. */
  siblings: string[];
};

type Line = {
  start: number;
  text: string;
  indent: number;
  item: boolean;
  /** Indent of the mapping the line's key belongs to (`indent + 2` for `- key:` lines). */
  innerIndent: number;
  key: string | null;
  blank: boolean;
};

const KEY_RE = /^([^\s#:"'-][^:#]*?|"[^"]*"|'[^']*'):(?:\s|$)/;

const escapePointer = (segment: string): string =>
  segment.replace(/~/g, "~0").replace(/\//g, "~1");

const unquote = (key: string): string =>
  (key.startsWith('"') && key.endsWith('"')) ||
  (key.startsWith("'") && key.endsWith("'"))
    ? key.slice(1, -1)
    : key.trim();

const parseLine = (start: number, text: string): Line => {
  const trimmed = text.replace(/\s+$/, "");
  const indent = text.length - text.replace(/^ */, "").length;
  const content = trimmed.slice(indent);
  const blank = content === "" || content.startsWith("#");
  const item = content === "-" || content.startsWith("- ");
  const inner = item ? content.slice(2).replace(/^ +/, "") : content;
  const innerIndent = item
    ? indent + 2 + (content.length - 2 - inner.length)
    : indent;
  const match = KEY_RE.exec(inner);
  return {
    start,
    text,
    indent,
    item,
    innerIndent,
    key: match ? unquote(match[1]) : null,
    blank,
  };
};

const splitLines = (text: string): Line[] => {
  const lines: Line[] = [];
  let start = 0;
  for (const raw of text.split("\n")) {
    lines.push(parseLine(start, raw));
    start += raw.length + 1;
  }
  return lines;
};

/** Index of the item line `at` among the `- ` lines of the same sequence. */
const itemIndex = (lines: Line[], at: number): number => {
  const indent = lines[at].indent;
  let index = 0;
  for (let i = at - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (line.blank) continue;
    if (line.indent < indent) break;
    if (line.indent === indent && line.item) index += 1;
  }
  return index;
};

/** Pointer of the container whose children sit at `need` spaces, reading upwards from `from`. */
const containerPointer = (
  lines: Line[],
  from: number,
  need: number,
): string => {
  const segments: string[] = [];
  let level = need;
  for (let i = from; i >= 0 && level > 0; i -= 1) {
    const line = lines[i];
    if (line.blank) continue;
    if (line.item && line.indent < level) {
      // `- key:` owns children deeper than its inner mapping; the item itself is one index.
      if (line.key !== null && line.innerIndent < level) {
        segments.unshift(escapePointer(line.key));
      }
      segments.unshift(String(itemIndex(lines, i)));
      level = line.indent;
    } else if (!line.item && line.indent < level && line.key !== null) {
      segments.unshift(escapePointer(line.key));
      level = line.indent;
    }
  }
  return segments.length === 0 ? "" : `/${segments.join("/")}`;
};

const siblingKeys = (lines: Line[], at: number, need: number): string[] => {
  const above: string[] = [];
  const current = lines[at];
  for (let i = at - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (line.blank) continue;
    if (line.indent < need) {
      // The `- key:` line that owns this mapping contributes its own key, unless the cursor
      // line is that item line itself (then there is no earlier sibling).
      if (line.item && line.innerIndent === need && !current.item && line.key)
        above.push(line.key);
      break;
    }
    if (line.indent === need && !line.item && line.key !== null)
      above.push(line.key);
  }
  const keys = above.reverse();
  for (let i = at + 1; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.blank) continue;
    if (line.indent < need) break;
    if (line.indent === need && !line.item && line.key !== null)
      keys.push(line.key);
  }
  return keys;
};

export const describeYamlCursor = (
  text: string,
  offset: number,
): YamlCursorContext | null => {
  const lines = splitLines(text);
  const at = lines.findIndex(
    (line, index) =>
      offset >= line.start &&
      (offset <= line.start + line.text.length || index === lines.length - 1),
  );
  if (at < 0) return null;
  const line = lines[at];
  const before = line.text.slice(0, offset - line.start);
  if (/(^|\s)#/.test(before)) return null;

  // Inside the indentation: a new key at the cursor column.
  if (before.trim() === "") {
    const need = before.length;
    const pointer = containerPointer(lines, at - 1, need);
    return {
      mode: "key",
      pointer,
      key: null,
      prefix: "",
      from: offset,
      siblings: siblingKeys(lines, at, need),
    };
  }

  const content = before.slice(line.indent);
  const item = content === "-" || content.startsWith("- ");
  const inner = item ? content.slice(2).replace(/^ +/, "") : content;
  const need = item
    ? line.indent + 2 + (content.length - 2 - inner.length)
    : line.indent;
  // An item line belongs to the sequence at its own indent; its keys live one level deeper.
  const container = item
    ? `${containerPointer(lines, at - 1, line.indent)}/${itemIndex(lines, at)}`
    : containerPointer(lines, at - 1, need);

  const valueMatch = /^([^\s#:"'-][^:#]*?|"[^"]*"|'[^']*'):(?:\s+(.*))?$/.exec(
    inner,
  );
  if (valueMatch && (inner.endsWith(":") || /:\s/.test(inner))) {
    const key = unquote(valueMatch[1]);
    let prefix = (valueMatch[2] ?? "").replace(/^\s+/, "");
    let from = offset - prefix.length;
    if (prefix.startsWith('"') || prefix.startsWith("'")) {
      prefix = prefix.slice(1);
      from += 1;
    }
    return {
      mode: "value",
      pointer: `${container}/${escapePointer(key)}`,
      key,
      prefix,
      from,
      siblings: [],
    };
  }
  if (item && content !== "-" && !/:/.test(inner)) {
    // Scalar sequence item: `- momentum`.
    const prefix = inner;
    return {
      mode: "value",
      pointer: container,
      key: null,
      prefix,
      from: offset - prefix.length,
      siblings: [],
    };
  }
  if (content === "-") return null; // `-` without a space is not an item yet
  const prefix = inner;
  return {
    mode: "key",
    pointer: container,
    key: null,
    prefix,
    from: offset - prefix.length,
    siblings: siblingKeys(lines, at, need),
  };
};

/** Array indices become `*` so a concrete pointer matches the contract's pointer templates. */
export const templatePointer = (pointer: string): string =>
  pointer.replace(/\/\d+(?=\/|$)/g, "/*");
