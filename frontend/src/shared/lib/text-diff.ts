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

export const lineDiffSummary = (
  before: string,
  after: string,
  limit = 6,
): LineDiffSummary => {
  const a = before.split("\n");
  const b = after.split("\n");
  const rows = a.length + 1;
  const cols = b.length + 1;
  // Cap the quadratic table for very large texts: report counts from a coarse comparison.
  if (a.length * b.length > 4_000_000) {
    const same = new Set(a);
    const added = b.filter((line) => !same.has(line)).length;
    const removed = a.filter((line) => !new Set(b).has(line)).length;
    return { added, removed, preview: [] };
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
  const preview: string[] = [];
  let added = 0;
  let removed = 0;
  let i = 0;
  let j = 0;
  const push = (line: string) => {
    if (preview.length < limit) preview.push(line);
  };
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      i += 1;
      j += 1;
    } else if (table[(i + 1) * cols + j] >= table[i * cols + j + 1]) {
      removed += 1;
      push(`-${a[i]}`);
      i += 1;
    } else {
      added += 1;
      push(`+${b[j]}`);
      j += 1;
    }
  }
  for (; i < a.length; i += 1) {
    removed += 1;
    push(`-${a[i]}`);
  }
  for (; j < b.length; j += 1) {
    added += 1;
    push(`+${b[j]}`);
  }
  return { added, removed, preview };
};
