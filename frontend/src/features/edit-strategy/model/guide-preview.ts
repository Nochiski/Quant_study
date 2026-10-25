/**
 * 안내 오른쪽 YAML 미리보기의 "방금 바뀐 줄"(RD-04-D). 직전 원문과 지금 원문을 줄 단위 최장 공통 부분열로
 * 맞추고, 지금 원문에서 짝이 없는 줄을 바뀐 줄로 본다. 표시 전용이며 문서·편집 이력과 무관하다.
 */

/** 이보다 긴 문서는 비교하지 않는다(줄 수의 곱만큼 계산한다). 안내로 만드는 문서는 훨씬 짧다. */
const MAX_LINES = 2000;

export const changedLines = (previous: string, current: string): Set<number> => {
  if (previous === current) return new Set();
  const before = previous.split("\n");
  const after = current.split("\n");
  if (before.length > MAX_LINES || after.length > MAX_LINES) return new Set();
  // lengths[i][j] = before[i..]·after[j..] 의 최장 공통 부분열 길이.
  const lengths = Array.from({ length: before.length + 1 }, () =>
    new Array<number>(after.length + 1).fill(0),
  );
  for (let i = before.length - 1; i >= 0; i -= 1)
    for (let j = after.length - 1; j >= 0; j -= 1)
      lengths[i]![j] =
        before[i] === after[j]
          ? lengths[i + 1]![j + 1]! + 1
          : Math.max(lengths[i + 1]![j]!, lengths[i]![j + 1]!);
  const changed = new Set<number>();
  let i = 0;
  let j = 0;
  while (j < after.length) {
    if (i < before.length && before[i] === after[j]) {
      i += 1;
      j += 1;
    } else if (i < before.length && lengths[i + 1]![j]! >= lengths[i]![j + 1]!) {
      i += 1;
    } else {
      changed.add(j);
      j += 1;
    }
  }
  return changed;
};
