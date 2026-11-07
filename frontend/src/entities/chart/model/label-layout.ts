import type { PlotBox } from "./chart-scale";

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

/**
 * 선 위 레벨 라벨 하나: 어느 선(`owner`)의 무슨 글자를 어디에 두는가. 배치 전(`segmentLabels`)에는 `x`·`y`가 선 위
 * 점이고, 배치 뒤(`placeLevelLabels`)에는 글자 왼쪽 끝과 바닥선이다.
 */
export type LevelLabel = { owner: string; key: string; text: string; x: number; y: number };

// 12px 모노스페이스 글자 한 자의 폭과 라벨 줄 간격(px). 글자 폭은 겹침 판정에만 쓰는 어림값이다.
const CHAR_WIDTH = 7.4;
const LABEL_GAP = 14;
const LABEL_OFFSET_X = 6;
const LABEL_OFFSET_Y = 5;
const LABEL_ASCENT = 11;
const LABEL_DESCENT = 4;

/**
 * 선분 위 라벨 자리: 선의 시작점, 시작점이 그림 칸 왼쪽 밖이면 왼쪽 끝에서 선이 지나는 점. 선이 그림 칸을 지나지
 * 않거나 그 점이 위·아래 밖이면 null(라벨을 달지 않는다).
 */
export const labelAnchor = (
  segment: { x1: number; y1: number; x2: number; y2: number },
  plot: PlotBox,
): { x: number; y: number } | null => {
  if (segment.x2 < plot.left || segment.x1 > plot.right) return null;
  const x = Math.max(segment.x1, plot.left);
  const y =
    segment.x2 > segment.x1
      ? segment.y1 + ((segment.y2 - segment.y1) * (x - segment.x1)) / (segment.x2 - segment.x1)
      : segment.y1;
  return y < plot.top || y > plot.bottom ? null : { x, y };
};

/**
 * 레벨 라벨 배치: 글자는 붙인 점의 오른쪽 위에 두고 그림 칸 안으로 들인다(오른쪽 끝을 넘으면 왼쪽으로, 위·아래
 * 끝을 넘으면 안쪽으로). 가로로 겹치는 라벨끼리는 세로로 `spreadLabels`처럼 밀어내 줄 간격을 둔다. 돌려주는
 * 순서는 입력 순서 그대로다.
 */
export const placeLevelLabels = (labels: readonly LevelLabel[], plot: PlotBox): LevelLabel[] => {
  const top = plot.top + LABEL_ASCENT;
  const bottom = plot.bottom - LABEL_DESCENT;
  const boxes = labels.map((label) => {
    const width = label.text.length * CHAR_WIDTH;
    const left = Math.max(
      plot.left + 2,
      Math.min(label.x + LABEL_OFFSET_X, plot.right - width - LABEL_OFFSET_X),
    );
    return { left, right: left + width, y: Math.min(Math.max(label.y - LABEL_OFFSET_Y, top), bottom) };
  });
  // 가로로 겹치는 라벨을 한 무리로 묶는다(왼쪽 끝 순으로 훑으며 무리의 오른쪽 끝과 견준다)
  const order = boxes.map((_, index) => index).sort((a, b) => boxes[a].left - boxes[b].left || a - b);
  const placedY = boxes.map((box) => box.y);
  let group: number[] = [];
  let groupRight = -Infinity;
  const settle = () => {
    const spread = spreadLabels(
      group.map((index) => boxes[index].y),
      LABEL_GAP,
      top,
      bottom,
    );
    group.forEach((index, k) => {
      placedY[index] = spread[k];
    });
  };
  for (const index of order) {
    if (boxes[index].left >= groupRight && group.length > 0) {
      settle();
      group = [];
      groupRight = -Infinity;
    }
    group.push(index);
    groupRight = Math.max(groupRight, boxes[index].right);
  }
  if (group.length > 0) settle();
  return labels.map((label, index) => ({ ...label, x: boxes[index].left, y: placedY[index] }));
};
