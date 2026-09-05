/**
 * Line-level diff summary (longest common subsequence). Enough to tell a user how far a
 * recovered draft is from the server original; not a merge tool.
 */
export type LineDiffSummary = {
  added: number;
  removed: number;
  /** Up to `limit` changed lines, prefixed with `+` / `-`, in order. */
  preview: string[];
};

export type TextDiffRow = {
  kind: "added" | "removed";
  text: string;
  beforeLine: number | null;
  afterLine: number | null;
};

export type TextDiff = {
  added: number;
  removed: number;
  rows: TextDiffRow[];
  truncated: boolean;
};

type DiffCounts = { added: number; removed: number; coarse: boolean };

const splitLines = (value: string): string[] => {
  if (value.length === 0) return [];
  const lines = value.split("\n");
  // A final newline terminates the preceding line; it is not an additional empty line.
  // Preserve any earlier empty entries so intentional blank lines still participate in the diff.
  if (lines.at(-1) === "") lines.pop();
  return lines;
};

const visitLineDiff = (
  before: string,
  after: string,
  visit: (row: TextDiffRow) => void,
): DiffCounts => {
  const a = splitLines(before);
  const b = splitLines(after);
  const rows = a.length + 1;
  const cols = b.length + 1;
  // P6-04 owns large-document diff virtualization. Until then, never allocate an unbounded
  // quadratic table: the UI still reports deterministic coarse counts and says rows are omitted.
  if (a.length * b.length > 4_000_000) {
    const beforeLines = new Set(a);
    const afterLines = new Set(b);
    return {
      added: b.filter((line) => !beforeLines.has(line)).length,
      removed: a.filter((line) => !afterLines.has(line)).length,
      coarse: true,
    };
  }
  const table = new Uint32Array(rows * cols);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * cols + j] =
        a[i] === b[j]
          ? table[(i + 1) * cols + j + 1] + 1
          : Math.max(table[(i + 1) * cols + j], table[i * cols + j + 1]);
    }
  }
  let added = 0;
  let removed = 0;
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      i += 1;
      j += 1;
    } else if (table[(i + 1) * cols + j] >= table[i * cols + j + 1]) {
      removed += 1;
      visit({
        kind: "removed",
        text: a[i],
        beforeLine: i + 1,
        afterLine: null,
      });
      i += 1;
    } else {
      added += 1;
      visit({ kind: "added", text: b[j], beforeLine: null, afterLine: j + 1 });
      j += 1;
    }
  }
  for (; i < a.length; i += 1) {
    removed += 1;
    visit({ kind: "removed", text: a[i], beforeLine: i + 1, afterLine: null });
  }
  for (; j < b.length; j += 1) {
    added += 1;
    visit({ kind: "added", text: b[j], beforeLine: null, afterLine: j + 1 });
  }
  return { added, removed, coarse: false };
};

/** Bounded changed-line projection for a source diff view. */
export const lineDiff = (
  before: string,
  after: string,
  rowLimit = 1_000,
): TextDiff => {
  const rows: TextDiffRow[] = [];
  const counts = visitLineDiff(before, after, (row) => {
    if (rows.length < rowLimit) rows.push(row);
  });
  return {
    added: counts.added,
    removed: counts.removed,
    rows,
    truncated: counts.coarse || counts.added + counts.removed > rows.length,
  };
};

export const lineDiffSummary = (
  before: string,
  after: string,
  limit = 6,
): LineDiffSummary => {
  const preview: string[] = [];
  const counts = visitLineDiff(before, after, (row) => {
    if (preview.length < limit) {
      preview.push(`${row.kind === "added" ? "+" : "-"}${row.text}`);
    }
  });
  return { added: counts.added, removed: counts.removed, preview };
};
