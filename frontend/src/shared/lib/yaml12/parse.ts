/**
 * YAML 1.2 / JSON authoring source parser (frontend side of the parser ADR, P0-03 → P3-01).
 *
 * Same accept/reject policy as the backend codec, validated against the shared manifest
 * (`backend/tests/fixtures/strategy_documents/yaml12/manifest.json`). Produces the untyped
 * JSON-compatible tree plus a JSON Pointer → range source map so the document state machine
 * can attach backend diagnostics (P3-04) and outline selection (P4-01) to exact positions.
 *
 * Positions are 0-based line/column/offset in UTF-16 code units (JavaScript string indices).
 * Backend positions are Unicode code points; the wire boundary converts (P3-04).
 *
 * This parser only reports *syntax and policy* rejections (`yaml.<reason>`, `json.syntax`).
 * Structural and semantic diagnostics come from the backend compile API (editor ADR D2).
 */
import { CODEC_LIMITS } from "./limits";
import {
  Parser,
  isAlias,
  isMap,
  isScalar,
  isSeq,
  parseAllDocuments,
  visit,
  type Document,
  type Node,
  type Pair,
} from "yaml";

export type SourceFormat = "yaml" | "json";

export type SourcePosition = { line: number; column: number; offset: number };
export type SourceRange = { start: SourcePosition; end: SourcePosition };

export type ParseDiagnostic = {
  code: string;
  message: string;
  range: SourceRange | null;
};

export type ParsedSource =
  | {
      status: "ok";
      format: SourceFormat;
      tree: Record<string, unknown>;
      valueRanges: Map<string, SourceRange>;
      keyRanges: Map<string, SourceRange>;
      diagnostics: [];
    }
  | {
      status: "rejected";
      format: SourceFormat;
      tree: null;
      valueRanges: Map<string, SourceRange>;
      keyRanges: Map<string, SourceRange>;
      diagnostics: [ParseDiagnostic];
    };

export class Yaml12Rejected extends Error {
  constructor(
    readonly reason: string,
    detail: string,
    readonly range: SourceRange | null = null,
  ) {
    super(`yaml 1.2 document rejected — reason=${reason} detail=${detail}`);
  }
}

// ruamel.yaml 1.2 resolver number shapes (`_` separators, `0b`) vs the YAML 1.2 core schema. The
// difference is a silent-corrupt path (backend number, frontend string) and is rejected.
const RUAMEL_INT =
  /^[-+]?(?:0b[0-1_]+|0o?[0-7_]+|(?:0|[1-9][0-9_]*)|0x[0-9a-fA-F_]+)$/;
const RUAMEL_FLOAT =
  /^[-+]?(?:[0-9][0-9_]*\.[0-9_]*(?:[eE][-+]?[0-9]+)?|[0-9][0-9_]*[eE][-+]?[0-9]+|\.[0-9_]+(?:[eE][-+]?[0-9]+)?)$/;
const CORE_INT = /^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$/;
const CORE_FLOAT =
  /^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$/;
// ruamel's float regex misses an unsigned exponent after a leading `.` (`.5e3`); core accepts it.
const RUAMEL_MISSES_FLOAT = /^[-+]?\.[0-9]+[eE][0-9]+$/;
const DEFAULT_TAG_HANDLES: Record<string, string> = {
  "!!": "tag:yaml.org,2002:",
};

const escapePointer = (key: string) =>
  key.replace(/~/g, "~0").replace(/\//g, "~1");

class LineIndex {
  private readonly starts: number[] = [0];

  constructor(private readonly text: string) {
    for (let i = 0; i < text.length; i += 1) {
      if (text[i] === "\n") this.starts.push(i + 1);
    }
  }

  position(offset: number): SourcePosition {
    const clamped = Math.max(0, Math.min(offset, this.text.length));
    let low = 0;
    let high = this.starts.length - 1;
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if (this.starts[mid] <= clamped) low = mid;
      else high = mid - 1;
    }
    return { line: low, column: clamped - this.starts[low], offset: clamped };
  }

  range(start: number, end: number): SourceRange {
    return { start: this.position(start), end: this.position(end) };
  }
}

const rangeOf = (
  lines: LineIndex,
  node: Node | null | undefined,
): SourceRange | null =>
  node?.range ? lines.range(node.range[0], node.range[1]) : null;

const rejectAnchorOrTag = (lines: LineIndex, node: Node): void => {
  if (node.anchor !== undefined) {
    throw new Yaml12Rejected(
      "anchor_or_alias",
      `anchor=${node.anchor}`,
      rangeOf(lines, node),
    );
  }
  if (node.tag !== undefined) {
    throw new Yaml12Rejected("tag", `tag=${node.tag}`, rangeOf(lines, node));
  }
};

const errorRange = (
  lines: LineIndex,
  error: { pos: [number, number] },
): SourceRange => lines.range(error.pos[0], error.pos[1]);

type Budget = { nodes: number };

/** Depth is a streamed scanner guard in the backend, so it precedes all deferred policies. */
const rejectDepth = (node: Node | null, lines: LineIndex, depth = 0): void => {
  if (!node) return;
  // The streamed guard counts collection-opening tokens only. Scalar depth is checked later by
  // the ordered tree walk, after deferred anchor/tag/number scanner policies have won.
  if (!isMap(node) && !isSeq(node)) return;
  if (depth > CODEC_LIMITS.maxDepth) {
    throw new Yaml12Rejected(
      "too_deep",
      `depth=${depth} max_depth=${CODEC_LIMITS.maxDepth}`,
      rangeOf(lines, node),
    );
  }
  if (isMap(node)) {
    for (const pair of node.items as Pair<Node, Node | null>[]) {
      rejectDepth(pair.key, lines, depth + 1);
      rejectDepth(pair.value, lines, depth + 1);
    }
  } else {
    for (const item of node.items as (Node | null)[]) {
      rejectDepth(item, lines, depth + 1);
    }
  }
};

/** Mirror the backend codec's node walk after all scanner policies have completed. */
const rejectTreePolicy = (
  node: Node | null,
  lines: LineIndex,
  pointer = "",
  depth = 0,
  budget: Budget = { nodes: 0 },
): void => {
  if (!node) return;
  budget.nodes += 1;
  if (budget.nodes > CODEC_LIMITS.maxNodes) {
    throw new Yaml12Rejected(
      "too_many_nodes",
      `nodes>${CODEC_LIMITS.maxNodes}`,
      rangeOf(lines, node),
    );
  }
  if (depth > CODEC_LIMITS.maxDepth) {
    throw new Yaml12Rejected(
      "too_deep",
      `depth=${depth} max_depth=${CODEC_LIMITS.maxDepth}`,
      rangeOf(lines, node),
    );
  }
  if (isMap(node)) {
    const seen = new Set<string>();
    for (const pair of node.items as Pair<Node, Node | null>[]) {
      if (!isScalar(pair.key) || typeof pair.key.value !== "string") {
        throw new Yaml12Rejected(
          "non_string_key",
          `key=${String(pair.key)}`,
          rangeOf(lines, pair.key as Node),
        );
      }
      const key = pair.key.value;
      const child = `${pointer}/${escapePointer(key)}`;
      if (LONE_SURROGATE.test(key)) {
        throw new Yaml12Rejected(
          "syntax",
          `key contains an unpaired surrogate escape — pointer=${child}`,
          rangeOf(lines, pair.key),
        );
      }
      if (seen.has(key)) {
        throw new Yaml12Rejected(
          "duplicate_key",
          `duplicate key — key=${key}`,
          rangeOf(lines, pair.key),
        );
      }
      seen.add(key);
      rejectTreePolicy(pair.value, lines, child, depth + 1, budget);
    }
    return;
  }
  if (isSeq(node)) {
    (node.items as (Node | null)[]).forEach((item, index) =>
      rejectTreePolicy(item, lines, `${pointer}/${index}`, depth + 1, budget),
    );
    return;
  }
  if (!isScalar(node)) return;
  if (typeof node.value === "string" && LONE_SURROGATE.test(node.value)) {
    throw new Yaml12Rejected(
      "syntax",
      `string contains an unpaired surrogate escape — pointer=${pointer}`,
      rangeOf(lines, node),
    );
  }
  if (typeof node.value === "number" && !Number.isFinite(node.value)) {
    throw new Yaml12Rejected(
      "non_finite_number",
      `value=${String(node.value)}`,
      rangeOf(lines, node),
    );
  }
  if (
    typeof node.value === "number" &&
    node.source !== undefined &&
    node.type === "PLAIN" &&
    CORE_INT.test(node.source) &&
    !Number.isSafeInteger(node.value)
  ) {
    throw new Yaml12Rejected(
      "integer_out_of_range",
      `value=${String(node.value)}`,
      rangeOf(lines, node),
    );
  }
};

const rejectPolicy = (doc: Document, lines: LineIndex): void => {
  // Same order as the backend: syntax → directive → tag → tree policy (anchor/alias, merge
  // key, non-string key, number shapes) → duplicate key. Duplicates are the *last* check so a
  // document that also breaks an earlier rule reports that rule, as the backend does.
  for (const error of doc.errors) {
    if (error.code !== "DUPLICATE_KEY") {
      throw new Yaml12Rejected(
        "syntax",
        error.message,
        errorRange(lines, error),
      );
    }
  }
  if (doc.directives?.yaml.explicit) {
    throw new Yaml12Rejected(
      "directive",
      `yaml=${doc.directives.yaml.version}`,
      lines.range(0, 0),
    );
  }
  const tagHandles = Object.entries(doc.directives?.tags ?? {});
  if (
    tagHandles.some(
      ([handle, prefix]) => DEFAULT_TAG_HANDLES[handle] !== prefix,
    )
  ) {
    throw new Yaml12Rejected(
      "directive",
      `tag handles=${tagHandles.map(([h]) => h).join(",")}`,
      lines.range(0, 0),
    );
  }
  visit(doc, {
    Alias(_key, node) {
      throw new Yaml12Rejected(
        "anchor_or_alias",
        `alias=${node.source}`,
        rangeOf(lines, node),
      );
    },
    Collection(_key, node) {
      rejectAnchorOrTag(lines, node);
    },
    Pair(_key, pair) {
      if (
        isScalar(pair.key) &&
        pair.key.type === "PLAIN" &&
        pair.key.value === "<<"
      ) {
        throw new Yaml12Rejected(
          "merge_key",
          "key=<<",
          rangeOf(lines, pair.key),
        );
      }
    },
    Scalar(_key, node) {
      rejectAnchorOrTag(lines, node);
      const source = node.source;
      if (node.type === "PLAIN" && source === "<<") {
        throw new Yaml12Rejected(
          "merge_key",
          "scalar=<<",
          rangeOf(lines, node),
        );
      }
      if (source !== undefined && node.type === "PLAIN") {
        const ruamelNumber =
          RUAMEL_INT.test(source) || RUAMEL_FLOAT.test(source);
        const coreNumber = CORE_INT.test(source) || CORE_FLOAT.test(source);
        if ((ruamelNumber && !coreNumber) || RUAMEL_MISSES_FLOAT.test(source)) {
          throw new Yaml12Rejected(
            "non_core_number",
            `scalar=${source}`,
            rangeOf(lines, node),
          );
        }
      }
    },
  });
  rejectTreePolicy(doc.contents as Node | null, lines);
};

const collectRanges = (
  node: Node | null,
  pointer: string,
  lines: LineIndex,
  valueRanges: Map<string, SourceRange>,
  keyRanges: Map<string, SourceRange>,
  depth = 0,
  budget: Budget = { nodes: 0 },
): void => {
  if (!node) return;
  const range = rangeOf(lines, node);
  if (depth > CODEC_LIMITS.maxDepth) {
    throw new Yaml12Rejected(
      "too_deep",
      `depth=${depth} max_depth=${CODEC_LIMITS.maxDepth}`,
      range,
    );
  }
  budget.nodes += 1;
  if (budget.nodes > CODEC_LIMITS.maxNodes) {
    throw new Yaml12Rejected(
      "too_many_nodes",
      `nodes>${CODEC_LIMITS.maxNodes}`,
      range,
    );
  }
  if (range) valueRanges.set(pointer, range);
  if (isMap(node)) {
    for (const pair of node.items as Pair<Node, Node | null>[]) {
      if (!isScalar(pair.key) || typeof pair.key.value !== "string") continue;
      const child = `${pointer}/${escapePointer(pair.key.value)}`;
      const keyRange = rangeOf(lines, pair.key);
      if (keyRange) keyRanges.set(child, keyRange);
      collectRanges(
        pair.value,
        child,
        lines,
        valueRanges,
        keyRanges,
        depth + 1,
        budget,
      );
    }
  } else if (isSeq(node)) {
    (node.items as (Node | null)[]).forEach((item, index) => {
      collectRanges(
        item,
        `${pointer}/${index}`,
        lines,
        valueRanges,
        keyRanges,
        depth + 1,
        budget,
      );
    });
  }
};

const composeDocument = (text: string, lines: LineIndex): Document => {
  const docs = parseAllDocuments(text, {
    version: "1.2",
    schema: "core",
    merge: false,
    uniqueKeys: true,
  });
  if (docs.length === 0) {
    throw new Yaml12Rejected("not_a_mapping", "root=empty");
  }
  // The backend scanner sees every document and enforces depth before composition decides that
  // the stream contains more than one document.
  for (const candidate of docs) {
    rejectDepth(candidate.contents as Node | null, lines);
  }
  if (docs.length > 1) {
    throw new Yaml12Rejected(
      "multiple_documents",
      `documents=${docs.length}`,
      rangeOf(lines, docs[1].contents as Node | null) ?? lines.range(0, 0),
    );
  }
  const doc = docs[0];
  if (isAlias(doc.contents)) {
    throw new Yaml12Rejected(
      "anchor_or_alias",
      "root alias",
      rangeOf(lines, doc.contents),
    );
  }
  rejectPolicy(doc, lines);
  if (!isMap(doc.contents)) {
    throw new Yaml12Rejected(
      "not_a_mapping",
      `root=${doc.contents?.constructor.name ?? "null"}`,
      rangeOf(lines, doc.contents as Node | null) ?? lines.range(0, 0),
    );
  }
  return doc;
};

const JSON_POSITION = /position (\d+)/;
const JSON_PROBE_LIMIT = 32;
const JSON_PROBE_MAX_LENGTH = 64 * 1024;

/** Where the JSON text stops being valid: V8 says so in the message; otherwise the YAML reader
 * of the same text (JSON is YAML) locates the syntax error. */
const JSON_TOKEN = /Unexpected token '(.)'/;

/** Where the JSON text stops being valid. V8 only names the offending token, so the first
 * occurrence whose prefix is merely *incomplete* JSON is the error position. */
const jsonErrorRange = (
  text: string,
  message: string,
  lines: LineIndex,
): SourceRange => {
  const position = JSON_POSITION.exec(message);
  if (position) {
    const offset = Number(position[1]);
    return lines.range(offset, offset);
  }
  const token = JSON_TOKEN.exec(message)?.[1];
  // Each probe re-parses a prefix, so the search is bounded: at most JSON_PROBE_LIMIT candidates
  // and only for texts under JSON_PROBE_MAX_LENGTH; beyond that the position is "end of text".
  if (token !== undefined && text.length <= JSON_PROBE_MAX_LENGTH) {
    let probes = 0;
    for (
      let index = text.indexOf(token);
      index >= 0 && probes < JSON_PROBE_LIMIT;
      index = text.indexOf(token, index + 1), probes += 1
    ) {
      try {
        JSON.parse(text.slice(0, index));
      } catch (error) {
        if (error instanceof Error && /Unexpected end/.test(error.message)) {
          return lines.range(index, index);
        }
      }
    }
  }
  return lines.range(text.length, text.length);
};

const parseJsonValues = (text: string, lines: LineIndex): unknown => {
  try {
    return JSON.parse(text);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Yaml12Rejected(
      "json.syntax",
      message,
      jsonErrorRange(text, message, lines),
    );
  }
};

/** Tree only, throws `Yaml12Rejected`: used where a best-effort tree of a draft is enough. */
export const loadYaml12Mapping = (text: string): Record<string, unknown> => {
  const lines = new LineIndex(text);
  rejectText(text, lines, "yaml");
  const tree = composeDocument(text, lines).toJS() as Record<string, unknown>;
  rejectDecodedSurrogates(tree, "", lines);
  return tree;
};

const LONE_SURROGATE =
  /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

const rejectDecodedSurrogates = (
  value: unknown,
  pointer: string,
  lines: LineIndex,
  valueRanges?: Map<string, SourceRange>,
  keyRanges?: Map<string, SourceRange>,
): void => {
  if (typeof value === "string") {
    if (LONE_SURROGATE.test(value)) {
      throw new Yaml12Rejected(
        "syntax",
        `string contains an unpaired surrogate escape — pointer=${pointer}`,
        valueRanges?.get(pointer) ?? lines.range(0, 0),
      );
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      rejectDecodedSurrogates(
        item,
        `${pointer}/${index}`,
        lines,
        valueRanges,
        keyRanges,
      ),
    );
    return;
  }
  if (typeof value !== "object" || value === null) return;
  for (const [key, item] of Object.entries(value)) {
    const child = `${pointer}/${escapePointer(key)}`;
    if (LONE_SURROGATE.test(key)) {
      throw new Yaml12Rejected(
        "syntax",
        `key contains an unpaired surrogate escape — pointer=${child}`,
        keyRanges?.get(child) ?? lines.range(0, 0),
      );
    }
    rejectDecodedSurrogates(item, child, lines, valueRanges, keyRanges);
  }
};
/**
 * The `yaml` package accepts tabs in separation whitespace while ruamel's scanner rejects them.
 * Inspect CST whitespace tokens instead of the source text: tabs inside quoted or block scalars
 * belong to scalar tokens and are valid YAML content, not indentation/separation.
 */
const yamlWhitespaceTabOffset = (text: string): number | null => {
  const pending: unknown[] = [...new Parser().parse(text)];
  const seen = new WeakSet<object>();
  while (pending.length > 0) {
    const candidate = pending.pop();
    if (typeof candidate !== "object" || candidate === null) continue;
    if (seen.has(candidate)) continue;
    seen.add(candidate);
    const token = candidate as Record<string, unknown>;
    if (
      (token.type === "space" || token.type === "scalar") &&
      typeof token.source === "string"
    ) {
      const index = token.source.indexOf("\t");
      if (index >= 0 && typeof token.offset === "number") {
        return token.offset + index;
      }
    }
    for (const value of Object.values(token)) {
      if (Array.isArray(value)) pending.push(...value);
      else if (typeof value === "object" && value !== null) pending.push(value);
    }
  }
  return null;
};

/** Text-level policy the backend applies before parsing (`_check_encodable`, `CodecLimits`). */
const rejectText = (
  text: string,
  lines: LineIndex,
  format: SourceFormat,
): void => {
  const surrogate = LONE_SURROGATE.exec(text);
  if (surrogate) {
    throw new Yaml12Rejected(
      "syntax",
      `source is not valid UTF-8 text — unpaired surrogate at offset ${surrogate.index}`,
      lines.range(surrogate.index, surrogate.index + 1),
    );
  }
  const bytes = new TextEncoder().encode(text).length;
  if (bytes > CODEC_LIMITS.maxBytes) {
    throw new Yaml12Rejected(
      "too_large",
      `bytes=${bytes} max_bytes=${CODEC_LIMITS.maxBytes}`,
      lines.range(0, 0),
    );
  }
  const tabOffset = format === "yaml" ? yamlWhitespaceTabOffset(text) : null;
  if (tabOffset !== null) {
    throw new Yaml12Rejected(
      "syntax",
      "found character '\\t' that cannot start any token",
      lines.range(tabOffset, tabOffset + 1),
    );
  }
};

export const parseSource = (
  text: string,
  format: SourceFormat,
): ParsedSource => {
  const lines = new LineIndex(text);
  const valueRanges = new Map<string, SourceRange>();
  const keyRanges = new Map<string, SourceRange>();
  try {
    rejectText(text, lines, format);
    // JSON values come from JSON.parse (never from the YAML reading of the same text); the YAML
    // parse of the JSON text only contributes the source map, as in the backend codec.
    const jsonTree =
      format === "json" ? parseJsonValues(text, lines) : undefined;
    const doc = composeDocument(text, lines);
    collectRanges(
      doc.contents as Node | null,
      "",
      lines,
      valueRanges,
      keyRanges,
    );
    const tree = format === "json" ? jsonTree : doc.toJS();
    rejectDecodedSurrogates(tree, "", lines, valueRanges, keyRanges);
    if (typeof tree !== "object" || tree === null || Array.isArray(tree)) {
      throw new Yaml12Rejected(
        "not_a_mapping",
        `root=${Array.isArray(tree) ? "array" : typeof tree}`,
        valueRanges.get("") ?? lines.range(0, 0),
      );
    }
    return {
      status: "ok",
      format,
      tree: tree as Record<string, unknown>,
      valueRanges,
      keyRanges,
      diagnostics: [],
    };
  } catch (error) {
    if (!(error instanceof Yaml12Rejected)) throw error;
    const code =
      error.reason === "json.syntax" ? "json.syntax" : `yaml.${error.reason}`;
    return {
      status: "rejected",
      format,
      tree: null,
      valueRanges,
      keyRanges,
      diagnostics: [{ code, message: error.message, range: error.range }],
    };
  }
};

/** Best range for a JSON Pointer: value, then key, then the nearest ancestor value (P1-02). */
export const locateRange = (
  parsed: Pick<ParsedSource, "valueRanges" | "keyRanges">,
  pointer: string,
): SourceRange | null => {
  const direct =
    parsed.valueRanges.get(pointer) ?? parsed.keyRanges.get(pointer);
  if (direct) return direct;
  let current = pointer;
  while (current) {
    current = current.slice(0, current.lastIndexOf("/"));
    const parent = parsed.valueRanges.get(current);
    if (parent) return parent;
  }
  return parsed.valueRanges.get("") ?? null;
};
