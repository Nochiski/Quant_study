import type { DiffEntry } from "../../../shared/api";
import { lineDiff, type TextDiff } from "../../../shared/lib/text-diff";
import { parseSource } from "../../../shared/lib/yaml12";
import { currentCompile, type DocumentState } from "./document-state";

export type DraftSemanticDiff =
  | { status: "no-base" }
  | { status: "base-pending" }
  | { status: "current-unavailable" }
  | { status: "incoherent" }
  | {
      status: "ready";
      baseSpecHash: string;
      currentSpecHash: string;
      changes: DiffEntry[];
    };

export type DraftDiffProjection = {
  source: TextDiff;
  sourceBase: "saved-revision" | "empty-draft";
  semantic: DraftSemanticDiff;
};

const pointerToken = (value: string): string =>
  value.replaceAll("~", "~0").replaceAll("/", "~1");

const childPointer = (parent: string, key: string | number): string =>
  `${parent}/${pointerToken(String(key))}`;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const MISSING = Symbol("missing");

const walkCanonical = (
  before: unknown | typeof MISSING,
  after: unknown | typeof MISSING,
  pointer: string,
  entries: DiffEntry[],
): void => {
  if (before === MISSING) {
    entries.push({ pointer, kind: "added", before: null, after });
    return;
  }
  if (after === MISSING) {
    entries.push({ pointer, kind: "removed", before, after: null });
    return;
  }
  if (isObject(before) && isObject(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const key of [...keys].sort()) {
      walkCanonical(
        Object.hasOwn(before, key) ? before[key] : MISSING,
        Object.hasOwn(after, key) ? after[key] : MISSING,
        childPointer(pointer, key),
        entries,
      );
    }
    return;
  }
  if (Array.isArray(before) && Array.isArray(after)) {
    const length = Math.max(before.length, after.length);
    for (let index = 0; index < length; index += 1) {
      walkCanonical(
        index < before.length ? before[index] : MISSING,
        index < after.length ? after[index] : MISSING,
        childPointer(pointer, index),
        entries,
      );
    }
    return;
  }
  if (!Object.is(before, after)) {
    entries.push({ pointer, kind: "changed", before, after });
  }
};

const recordAt = (value: unknown, key: string): unknown =>
  isObject(value) ? value[key] : undefined;

/**
 * compile 이 팩터 그래프 끝에 붙인 노드(boolean 출력 승격, P2-07)를 걷어 낸 canonical payload
 * (BACKLOG-014). 붙인 노드는 문서 원문(parse tree)에 없는 노드로만 판정한다 — 승격 규칙·이름을
 * frontend 에 적지 않는다. 그래프 출력이 붙인 노드면 문서의 출력으로 되돌린다. 사용자가 노드 하나를
 * 더하면 붙인 노드 셋이 한 칸씩 밀려 "바뀐 것"으로 보이던 행이 사라지고, 사용자가 쓴 변경만 남는다.
 * 승격은 문서 그래프의 함수라 걷어 낸 두 payload 가 같으면 원래 payload 도 같다(spec_hash 판정은 그대로).
 */
const withoutCompileAddedNodes = (payload: unknown, tree: unknown): unknown => {
  const factors = recordAt(payload, "factors");
  const documentFactors = recordAt(tree, "factors");
  if (!Array.isArray(factors) || !Array.isArray(documentFactors))
    return payload;
  return {
    ...(payload as Record<string, unknown>),
    factors: factors.map((factor: unknown, index) => {
      const graph = recordAt(factor, "graph");
      const nodes = recordAt(graph, "nodes");
      const documentGraph = recordAt(documentFactors[index], "graph");
      const documentNodes = recordAt(documentGraph, "nodes");
      if (!Array.isArray(nodes) || !Array.isArray(documentNodes)) return factor;
      const authored = new Set(
        documentNodes.map((node: unknown) => recordAt(node, "node_id")),
      );
      const output = recordAt(graph, "output_node_id");
      const documentOutput = recordAt(documentGraph, "output_node_id");
      return {
        ...(factor as Record<string, unknown>),
        graph: {
          ...(graph as Record<string, unknown>),
          nodes: nodes.filter((node: unknown) =>
            authored.has(recordAt(node, "node_id")),
          ),
          output_node_id:
            authored.has(output) || typeof documentOutput !== "string"
              ? output
              : documentOutput,
        },
      };
    }),
  };
};

/**
 * Presentation-only structural diff over two backend-owned canonical JSON payloads. It knows no
 * normalization rules: identity/comment/number semantics remain owned by the backend
 * canonicalizer and its spec hash. 문서 원문 tree 를 주면 compile 이 붙인 노드를 걷고 비교한다.
 */
export const diffCanonicalJson = (
  beforeCanonicalJson: string,
  afterCanonicalJson: string,
  documents: { before: unknown; after: unknown } | null = null,
): DiffEntry[] | null => {
  try {
    const before = JSON.parse(beforeCanonicalJson) as unknown;
    const after = JSON.parse(afterCanonicalJson) as unknown;
    const entries: DiffEntry[] = [];
    walkCanonical(
      documents === null
        ? before
        : withoutCompileAddedNodes(before, documents.before),
      documents === null
        ? after
        : withoutCompileAddedNodes(after, documents.after),
      "",
      entries,
    );
    return entries;
  } catch {
    return null;
  }
};

/** 원문의 parse tree. 읽지 못하면 null — 그 쪽은 붙인 노드를 걷지 않는다. */
const treeOf = (source: string, format: DocumentState["format"]): unknown => {
  const parsed = parseSource(source, format);
  return parsed.status === "ok" ? parsed.tree : null;
};

/** Draft-vs-base projection. Invalid current source always keeps its exact text diff. */
export const projectDraftDiff = (state: DocumentState): DraftDiffProjection => {
  const sourceBase =
    state.savedSource === null ? "empty-draft" : "saved-revision";
  const source = lineDiff(state.savedSource ?? "", state.source);
  if (
    state.baseRevision === null ||
    state.baseSpecHash === null ||
    state.savedSource === null
  ) {
    return { source, sourceBase, semantic: { status: "no-base" } };
  }
  if (state.savedCanonicalJson === null) {
    return { source, sourceBase, semantic: { status: "base-pending" } };
  }
  const current = currentCompile(state);
  if (current === null) {
    return { source, sourceBase, semantic: { status: "current-unavailable" } };
  }
  if (state.baseSpecHash === current.specHash) {
    return {
      source,
      sourceBase,
      semantic: {
        status: "ready",
        baseSpecHash: state.baseSpecHash,
        currentSpecHash: current.specHash,
        changes: [],
      },
    };
  }
  const changes = diffCanonicalJson(
    state.savedCanonicalJson,
    current.canonicalJson,
    {
      before: treeOf(state.savedSource, state.format),
      after:
        state.parse?.status === "ok" &&
        state.parsedVersion === state.sourceVersion
          ? state.parse.tree
          : treeOf(state.source, state.format),
    },
  );
  if (changes === null || changes.length === 0) {
    return { source, sourceBase, semantic: { status: "incoherent" } };
  }
  return {
    source,
    sourceBase,
    semantic: {
      status: "ready",
      baseSpecHash: state.baseSpecHash,
      currentSpecHash: current.specHash,
      changes,
    },
  };
};
