/**
 * 모델 답변의 마크다운을 블록·인라인 구조로 읽는다.
 *
 * 결과는 HTML 문자열이 아니라 값 트리다. 화면은 이 트리를 React 요소로만 그리므로 모델이 쓴 `<script>`나
 * `<img onerror>` 같은 원시 HTML은 글자 그대로 보인다(spec D7 렌더 안전). 링크 주소는 여기서 고르지 않고
 * 그리는 쪽이 `safeExternalLink`로 `http`·`https`만 링크로 만든다.
 *
 * 모델 답변에 실제로 나오는 문법만 읽는다: 문단·제목·목록(중첩 포함)·코드 블록·인용·구분선·표(GFM),
 * 인라인 코드·굵게·기울임·취소선·링크. 그 밖의 표기는 글자 그대로 남긴다. 스트리밍 중에는 닫히지 않은
 * 표기(`**`만 온 굵게, 닫는 줄이 아직 없는 코드 블록)가 흔하다 — 굵게는 닫힐 때까지 글자로 두고, 코드
 * 블록은 끝까지를 코드로 본다. 델타마다 처음부터 다시 읽어도 되는 크기다.
 */

export type MarkdownInline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "strong"; children: MarkdownInline[] }
  | { kind: "em"; children: MarkdownInline[] }
  | { kind: "del"; children: MarkdownInline[] }
  | { kind: "link"; url: string; children: MarkdownInline[] }
  | { kind: "break" };

export type MarkdownAlign = "left" | "center" | "right" | null;

export type MarkdownBlock =
  | { kind: "paragraph"; inlines: MarkdownInline[] }
  | { kind: "heading"; level: number; inlines: MarkdownInline[] }
  | {
      kind: "list";
      ordered: boolean;
      start: number;
      items: MarkdownBlock[][];
    }
  | { kind: "code"; language: string; text: string }
  | { kind: "quote"; blocks: MarkdownBlock[] }
  | {
      kind: "table";
      align: MarkdownAlign[];
      header: MarkdownInline[][];
      rows: MarkdownInline[][][];
    }
  | { kind: "rule" };

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)/;
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}> ?/;
const LIST_ITEM = /^( *)([-*+]|\d{1,9}[.)])([ \t]+|$)/;
const TABLE_DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

type ListMarker = {
  indent: number;
  ordered: boolean;
  start: number;
  /** 항목 본문이 시작하는 열. 그보다 깊게 들여 쓴 줄은 그 항목의 본문이다. */
  contentIndent: number;
  rest: string;
};

const isBlank = (line: string): boolean => line.trim() === "";

const indentOf = (line: string): number =>
  line.length - line.trimStart().length;

const listMarker = (line: string): ListMarker | null => {
  const match = LIST_ITEM.exec(line);
  if (match === null) return null;
  const [whole, spaces, bullet] = match;
  const ordered = /\d/.test(bullet[0]);
  return {
    indent: spaces.length,
    ordered,
    start: ordered ? Number.parseInt(bullet, 10) : 1,
    contentIndent:
      whole.length === line.length
        ? spaces.length + bullet.length + 1
        : whole.length,
    rest: line.slice(whole.length),
  };
};

const startsBlock = (line: string): boolean =>
  FENCE.test(line) ||
  HEADING.test(line) ||
  RULE.test(line) ||
  QUOTE.test(line) ||
  listMarker(line) !== null;

/**
 * 문단 중간 줄이 새 블록을 여는가. 번호 목록은 1로 시작할 때만 문단을 끊는다 — "2026. 10. 25" 같은
 * 날짜 줄이 문단을 목록으로 쪼개지 않게 한다(CommonMark와 같은 규칙).
 */
const interruptsParagraph = (line: string): boolean => {
  const marker = listMarker(line);
  if (marker !== null) return !marker.ordered || marker.start === 1;
  return startsBlock(line);
};

const isTableStart = (lines: readonly string[], index: number): boolean =>
  index + 1 < lines.length &&
  lines[index].includes("|") &&
  TABLE_DELIMITER.test(lines[index + 1]) &&
  lines[index + 1].includes("-");

/** `|`로 칸을 나눈다. `\|`는 칸 안의 글자다. 줄 양 끝의 `|`는 칸이 아니다. */
const splitRow = (line: string): string[] => {
  let body = line.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|") && !body.endsWith("\\|")) body = body.slice(0, -1);
  const cells: string[] = [];
  let cell = "";
  for (let index = 0; index < body.length; index += 1) {
    const char = body[index];
    if (char === "\\" && body[index + 1] === "|") {
      cell += "|";
      index += 1;
    } else if (char === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell.trim());
  return cells;
};

const alignOf = (cell: string): MarkdownAlign => {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return null;
};

const stripIndent = (line: string, width: number): string =>
  line.slice(Math.min(width, indentOf(line)));

/**
 * 같은 들여쓰기·같은 종류(순서 있음/없음)의 항목을 한 목록으로 묶는다. 항목보다 깊게 들여 쓴 줄과 빈
 * 줄 뒤에 이어지는 깊은 줄은 그 항목의 본문이고, 본문은 다시 블록으로 읽어 중첩 목록이 된다.
 */
const parseList = (
  lines: readonly string[],
  from: number,
  first: ListMarker,
): { block: MarkdownBlock; next: number } => {
  const items: MarkdownBlock[][] = [];
  let index = from;
  while (index < lines.length) {
    const marker = listMarker(lines[index]);
    if (
      marker === null ||
      marker.indent !== first.indent ||
      marker.ordered !== first.ordered
    ) {
      break;
    }
    const body = [marker.rest];
    index += 1;
    while (index < lines.length) {
      const line = lines[index];
      if (isBlank(line)) {
        // 빈 줄 뒤에 항목 본문 깊이의 줄이 오면 같은 항목이 이어진다.
        let peek = index + 1;
        while (peek < lines.length && isBlank(lines[peek])) peek += 1;
        if (peek < lines.length && indentOf(lines[peek]) > first.indent) {
          body.push("");
          index += 1;
          continue;
        }
        break;
      }
      if (indentOf(line) > first.indent) {
        body.push(stripIndent(line, marker.contentIndent));
        index += 1;
        continue;
      }
      // 들여 쓰지 않은 줄은 블록을 시작하지 않을 때만 문단을 잇는 줄이다(lazy continuation). 목록
      // 안에서는 번호가 무엇이든 항목 표기가 다음 항목을 연다.
      if (startsBlock(line) || isTableStart(lines, index)) break;
      body.push(line);
      index += 1;
    }
    items.push(parseBlockLines(body));
    // 항목 사이의 빈 줄은 같은 목록 안이다.
    let peek = index;
    while (peek < lines.length && isBlank(lines[peek])) peek += 1;
    const nextMarker = peek < lines.length ? listMarker(lines[peek]) : null;
    if (
      nextMarker !== null &&
      nextMarker.indent === first.indent &&
      nextMarker.ordered === first.ordered
    ) {
      index = peek;
    }
  }
  return {
    block: {
      kind: "list",
      ordered: first.ordered,
      start: first.start,
      items,
    },
    next: index,
  };
};

const parseBlockLines = (lines: readonly string[]): MarkdownBlock[] => {
  const blocks: MarkdownBlock[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (isBlank(line)) {
      index += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence !== null) {
      const [, marker, language] = fence;
      const body: string[] = [];
      index += 1;
      // 닫는 줄이 아직 오지 않았으면(스트리밍 중) 끝까지 코드다.
      while (index < lines.length) {
        const close = lines[index].trim();
        if (
          close.startsWith(marker[0].repeat(marker.length)) &&
          close.replaceAll(marker[0], "") === ""
        ) {
          index += 1;
          break;
        }
        body.push(lines[index]);
        index += 1;
      }
      blocks.push({ kind: "code", language, text: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading !== null) {
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        inlines: parseInlines(heading[2] ?? ""),
      });
      index += 1;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ kind: "rule" });
      index += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (index < lines.length && QUOTE.test(lines[index])) {
        body.push(lines[index].replace(QUOTE, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", blocks: parseBlockLines(body) });
      continue;
    }

    const marker = listMarker(line);
    if (marker !== null) {
      const { block, next } = parseList(lines, index, marker);
      blocks.push(block);
      index = next;
      continue;
    }

    if (isTableStart(lines, index)) {
      const header = splitRow(line);
      const align = splitRow(lines[index + 1]).map(alignOf);
      index += 2;
      const rows: MarkdownInline[][][] = [];
      while (
        index < lines.length &&
        !isBlank(lines[index]) &&
        lines[index].includes("|")
      ) {
        const cells = splitRow(lines[index]);
        // 칸 수는 머리 줄에 맞춘다. 모자라면 빈 칸, 남으면 버린다(GFM).
        rows.push(header.map((_, column) => parseInlines(cells[column] ?? "")));
        index += 1;
      }
      blocks.push({
        kind: "table",
        align: header.map((_, column) => align[column] ?? null),
        header: header.map(parseInlines),
        rows,
      });
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (
      index < lines.length &&
      !isBlank(lines[index]) &&
      !interruptsParagraph(lines[index]) &&
      !isTableStart(lines, index)
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push({
      kind: "paragraph",
      inlines: parseInlines(paragraph.join("\n")),
    });
  }
  return blocks;
};

export const parseMarkdown = (source: string): MarkdownBlock[] =>
  parseBlockLines(source.replace(/\r\n?/g, "\n").split("\n"));

const ESCAPABLE = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;
const WORD = /[\p{L}\p{N}]/u;
const AUTOLINK = /^<(https?:\/\/[^\s<>]+)>/;

/**
 * 여는 표기 `delimiter`(i 위치)의 짝을 찾는다. 여는 쪽 바로 뒤와 닫는 쪽 바로 앞은 공백이 아니어야 한다
 * — `2 * 3 * 4`의 별표는 기울임이 아니다. 밑줄은 단어 안에서 열고 닫지 않는다(`turnover_buffer_count`).
 */
const closingIndex = (
  text: string,
  from: number,
  delimiter: string,
): number => {
  const opener = text[from + delimiter.length];
  if (opener === undefined || /\s/.test(opener)) return -1;
  const underscore = delimiter[0] === "_";
  if (underscore && from > 0 && WORD.test(text[from - 1])) return -1;
  let search = from + delimiter.length + 1;
  while (search <= text.length) {
    let close = text.indexOf(delimiter, search);
    if (close === -1) return -1;
    let run = delimiter.length;
    while (text[close + run] === delimiter[0]) run += 1;
    // 기울임(한 글자)의 짝은 굵게(두 글자) 표기가 아니다 — `*a **b** c*`의 `**`에서 닫지 않는다.
    if (delimiter.length === 1 && run === 2) {
      search = close + run;
      continue;
    }
    // `***굵은 기울임***`처럼 표기 글자가 더 이어지면 가장 바깥 짝을 쓴다.
    close += run - delimiter.length;
    const before = text[close - 1];
    const after = text[close + delimiter.length];
    const flanked = !/\s/.test(before);
    const boundary = !underscore || after === undefined || !WORD.test(after);
    if (flanked && boundary) return close;
    search = close + 1;
  }
  return -1;
};

export const parseInlines = (text: string): MarkdownInline[] => {
  const out: MarkdownInline[] = [];
  let buffer = "";
  const flush = () => {
    if (buffer !== "") out.push({ kind: "text", text: buffer });
    buffer = "";
  };

  let index = 0;
  while (index < text.length) {
    const char = text[index];

    if (char === "\\" && ESCAPABLE.test(text[index + 1] ?? "")) {
      buffer += text[index + 1];
      index += 2;
      continue;
    }

    if (char === "\n") {
      flush();
      out.push({ kind: "break" });
      index += 1;
      continue;
    }

    if (char === "`") {
      let run = 1;
      while (text[index + run] === "`") run += 1;
      const fence = "`".repeat(run);
      let close = text.indexOf(fence, index + run);
      // 더 긴 백틱 묶음의 일부는 닫는 짝이 아니다.
      while (close !== -1 && text[close + run] === "`") {
        let skip = close;
        while (text[skip] === "`") skip += 1;
        close = text.indexOf(fence, skip);
      }
      if (close === -1) {
        buffer += fence;
        index += run;
        continue;
      }
      let code = text.slice(index + run, close).replaceAll("\n", " ");
      if (code.length > 2 && code.startsWith(" ") && code.endsWith(" ")) {
        code = code.slice(1, -1);
      }
      flush();
      out.push({ kind: "code", text: code });
      index = close + run;
      continue;
    }

    if (char === "<") {
      const autolink = AUTOLINK.exec(text.slice(index));
      if (autolink !== null) {
        flush();
        out.push({
          kind: "link",
          url: autolink[1],
          children: [{ kind: "text", text: autolink[1] }],
        });
        index += autolink[0].length;
        continue;
      }
    }

    if (char === "[") {
      const link = linkAt(text, index);
      if (link !== null) {
        flush();
        out.push({
          kind: "link",
          url: link.url,
          children: parseInlines(link.label),
        });
        index = link.end;
        continue;
      }
    }

    if (char === "~" && text[index + 1] === "~") {
      const close = closingIndex(text, index, "~~");
      if (close !== -1) {
        flush();
        out.push({
          kind: "del",
          children: parseInlines(text.slice(index + 2, close)),
        });
        index = close + 2;
        continue;
      }
    }

    if (char === "*" || char === "_") {
      const double = char.repeat(2);
      if (text.startsWith(double, index)) {
        const close = closingIndex(text, index, double);
        if (close !== -1) {
          flush();
          out.push({
            kind: "strong",
            children: parseInlines(text.slice(index + 2, close)),
          });
          index = close + 2;
          continue;
        }
      }
      const close = closingIndex(text, index, char);
      if (close !== -1) {
        flush();
        out.push({
          kind: "em",
          children: parseInlines(text.slice(index + 1, close)),
        });
        index = close + 1;
        continue;
      }
      // 짝이 없는 표기 글자 묶음은 통째로 글자다(뒤 글자가 다시 여는 표기로 읽히지 않게).
      let run = 1;
      while (text[index + run] === char) run += 1;
      buffer += char.repeat(run);
      index += run;
      continue;
    }

    buffer += char;
    index += 1;
  }
  flush();
  return out;
};

/** `[label](url)` 또는 `[label](url "title")`. 대괄호 중첩을 세고 주소에는 공백이 없다. */
const linkAt = (
  text: string,
  from: number,
): { label: string; url: string; end: number } | null => {
  let depth = 0;
  let close = -1;
  for (let index = from; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\\") {
      index += 1;
    } else if (char === "[") {
      depth += 1;
    } else if (char === "]") {
      depth -= 1;
      if (depth === 0) {
        close = index;
        break;
      }
    } else if (char === "\n" && text[index + 1] === "\n") {
      return null;
    }
  }
  if (close === -1 || text[close + 1] !== "(") return null;
  // 주소는 공백 없이 이어지고 괄호는 짝이 맞는 만큼 주소 안이다(`…/Momentum_(finance)`).
  let index = close + 2;
  while (text[index] === " ") index += 1;
  const urlStart = index;
  let parens = 0;
  while (index < text.length && !/\s/.test(text[index])) {
    if (text[index] === "(") parens += 1;
    if (text[index] === ")") {
      if (parens === 0) break;
      parens -= 1;
    }
    index += 1;
  }
  const url = text.slice(urlStart, index);
  const tail = /^(?:\s+"[^"]*")?\s*\)/.exec(text.slice(index));
  if (url === "" || tail === null) return null;
  return {
    label: text.slice(from + 1, close),
    url,
    end: index + tail[0].length,
  };
};
