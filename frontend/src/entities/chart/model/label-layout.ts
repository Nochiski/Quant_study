/**
 * 오른쪽 축 배지 밀어내기: 겹치는 배지를 세로로 벌려 `gap`(px) 이상 떨어뜨리고 `[top, bottom]` 안에 둔다.
 * 돌려주는 위치는 입력 순서 그대로다. 원래 위치와 달라진 배지는 화면이 가는 선으로 원래 가격을 가리킨다.
 *
 * 위에서부터 아래로 겹친 만큼 내리고, 아래 끝을 넘으면 아래에서부터 위로 다시 올린다. 칸이 모자라 둘 다 지킬 수
 * 없으면 위 끝을 지킨다(배지가 칸 밖으로 나가지 않는다).
 */
export const spreadLabels = (
  ys: readonly number[],
  gap: number,
  top: number,
  bottom: number,
): number[] => {
  const order = ys.map((y, index) => ({ y, index })).sort((a, b) => a.y - b.y || a.index - b.index);
  const placed = order.map((item) => item.y);
  for (let k = 0; k < placed.length; k += 1) {
    placed[k] = Math.max(placed[k], k === 0 ? top : placed[k - 1] + gap);
  }
  for (let k = placed.length - 1; k >= 0; k -= 1) {
    placed[k] = Math.min(placed[k], k === placed.length - 1 ? bottom : placed[k + 1] - gap);
  }
  for (let k = 0; k < placed.length; k += 1) placed[k] = Math.max(placed[k], top);
  const result = new Array<number>(ys.length);
  order.forEach((item, k) => {
    result[item.index] = placed[k];
  });
  return result;
};
