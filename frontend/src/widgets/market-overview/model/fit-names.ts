/**
 * 섹터맵 칸 이름 맞추기의 owner. 이름은 띄어쓰기 단위로만 줄을 바꾸고(CSS keep-all) 두 줄까지다. 그려진 뒤 실제
 * 크기를 재서, 큰 칸이 넘치면 보통 글자로 다시 재고, 그래도 두 줄을 넘거나 한 단어가 칸보다 넓거나 등락과 함께 칸
 * 높이를 넘으면 이름을 숨긴다(말줄임 없음 — 이름은 낭독 이름·툴팁에 남는다).
 *
 * 줄 수와 높이는 상자 높이(getBoundingClientRect)로 잰다. scrollHeight 는 정수 올림인 데다 한글 글꼴의 글자 윗·아랫
 * 넘침까지 더해, 딱 한 줄인 이름도 "넘친다"로 읽혔다(011cf377 회귀: 12px 이름 15 > 14, 16px 이름 21 > 19).
 */

/** 이름 상자와 칸의 측정값(px). */
export type NameMeasure = {
  /** 이름의 scrollWidth − clientWidth — 한 단어가 칸보다 넓으면 커진다. */
  nameWidthOverflow: number;
  /** 이름 상자 높이와 그 줄 높이. 높이 ÷ 줄 높이가 줄 수다. */
  nameHeight: number;
  lineHeight: number;
  /** 이름 + 등락 + 간격의 높이와 칸 안쪽(여백을 뺀) 높이. */
  contentHeight: number;
  innerHeight: number;
};

const MAX_LINES = 2;
// 정수 반올림·소수 배치 오차(px). 실제 넘침(한 줄, 한 글자)은 이보다 훨씬 크다.
const ROUNDING_PX = 1;

export const nameOverflows = (measure: NameMeasure): boolean =>
  measure.nameWidthOverflow > ROUNDING_PX ||
  Math.round(measure.nameHeight / measure.lineHeight) > MAX_LINES ||
  measure.contentHeight - measure.innerHeight > ROUNDING_PX;

const px = (value: string): number => Number.parseFloat(value) || 0;

const measureOf = (tile: HTMLElement, name: HTMLElement): NameMeasure => {
  const tileStyle = getComputedStyle(tile);
  const nameHeight = name.getBoundingClientRect().height;
  const rest = [...tile.children]
    .filter((child) => child !== name)
    .reduce((sum, child) => sum + child.getBoundingClientRect().height, 0);
  return {
    nameWidthOverflow: name.scrollWidth - name.clientWidth,
    nameHeight,
    lineHeight: px(getComputedStyle(name).lineHeight) || nameHeight,
    contentHeight:
      nameHeight + rest + px(tileStyle.rowGap) * (tile.children.length - 1),
    innerHeight:
      tile.clientHeight - px(tileStyle.paddingTop) - px(tileStyle.paddingBottom),
  };
};

/**
 * `root` 안의 칸 이름을 모두 다시 잰다. 매번 숨김·보통 글자 표시를 먼저 풀고 잰다 — 앞선 판정이 다음 측정에 남지
 * 않는다. React 가 다루지 않는 속성(`hidden`, `data-fit`)만 바꾸므로 다시 그려도 지워지지 않는다.
 */
export const fitNames = (
  root: HTMLElement,
  measure: (tile: HTMLElement, name: HTMLElement) => NameMeasure = measureOf,
): void => {
  for (const tile of root.querySelectorAll<HTMLElement>(".market-map__tile")) {
    const name = tile.querySelector<HTMLElement>(".market-map__name");
    if (name === null) continue;
    name.hidden = false;
    delete tile.dataset.fit;
    // 칸이 아직 크기를 갖지 않았으면(폭 0 컨테이너 등) 판정하지 않는다 — 크기가 생기면 다시 잰다.
    if (tile.clientWidth === 0 || tile.clientHeight === 0) continue;
    if (tile.classList.contains("market-map__tile--large") && nameOverflows(measure(tile, name)))
      tile.dataset.fit = "regular";
    if (nameOverflows(measure(tile, name))) name.hidden = true;
  }
};
