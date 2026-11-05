/**
 * 섹터맵 칸 이름 맞추기의 owner. 이름은 숨기지 않는다 — 그려진 뒤 실제 크기를 재서 들어갈 때까지 단계를 내린다.
 *
 * 1. 큰 글자(큰 칸만), 띄어쓰기에서만 줄바꿈, 두 줄까지
 * 2. 보통 글자, 띄어쓰기에서만 줄바꿈, 두 줄까지
 * 3. 작은 글자, 띄어쓰기에서만 줄바꿈, 두 줄까지
 * 4. 작은 글자, 음절 단위 줄바꿈, 칸 높이가 허용하는 만큼 여러 줄
 * 5. 그래도 넘치면 들어가는 줄 수에서 마지막 줄을 말줄임으로 자른다(최소 한 줄)
 * 6. 이름 한 줄과 등락을 함께 못 넣으면 이름을 우선하고 등락을 숨긴다(등락은 툴팁·색·낭독 이름에 남는다)
 *
 * 줄 수와 높이는 상자 높이(getBoundingClientRect)로 잰다. scrollHeight 는 정수 올림인 데다 한글 글꼴의 글자 윗·아랫
 * 넘침까지 더해 딱 한 줄인 이름도 넘친다로 읽힌다(011cf377 회귀).
 */

/** 이름 상자와 칸의 측정값(px). */
export type NameMeasure = {
  /** 이름의 scrollWidth − clientWidth — 한 단어가 칸보다 넓으면 커진다. */
  nameWidthOverflow: number;
  /** 이름 상자 높이와 그 줄 높이. 높이 ÷ 줄 높이가 줄 수다. */
  nameHeight: number;
  lineHeight: number;
  /** 등락 높이와 이름·등락 사이 간격. */
  changeHeight: number;
  gap: number;
  /** 칸 안쪽(여백을 뺀) 높이. */
  innerHeight: number;
};

/** 1~4단계. `large` 는 큰 칸에서만 시작점이다. */
export type FitStep = "large" | "regular" | "small" | "break";

/** 고른 맞춤: 단계(5단계는 `clamp` + 줄 수)와 등락 숨김(6단계). */
export type NameFit = {
  step: FitStep | "clamp";
  lines: number | null;
  hideChange: boolean;
};

const KEEP_ALL_LINES = 2;
// 정수 반올림·소수 배치 오차(px). 실제 넘침(한 줄, 한 글자)은 이보다 훨씬 크다.
const ROUNDING_PX = 1;

/** 그 단계에서 이름이 넘치는가. 띄어쓰기 줄바꿈 단계는 두 줄까지, 음절 줄바꿈 단계는 칸 높이까지만 본다. */
export const nameOverflows = (measure: NameMeasure, step: FitStep): boolean =>
  measure.nameWidthOverflow > ROUNDING_PX ||
  (step !== "break" &&
    Math.round(measure.nameHeight / measure.lineHeight) > KEEP_ALL_LINES) ||
  measure.nameHeight + measure.gap + measure.changeHeight - measure.innerHeight >
    ROUNDING_PX;

const linesIn = (height: number, lineHeight: number) =>
  Math.floor((height + ROUNDING_PX) / lineHeight);

/** 단계를 차례로 재 보고(`measureAt` 이 그 단계를 적용한 뒤 잰다) 처음 들어가는 맞춤을 고른다. */
export const chooseFit = (
  large: boolean,
  measureAt: (step: FitStep) => NameMeasure,
): NameFit => {
  const steps: FitStep[] = large
    ? ["large", "regular", "small", "break"]
    : ["regular", "small", "break"];
  let last: NameMeasure | null = null;
  for (const step of steps) {
    last = measureAt(step);
    if (!nameOverflows(last, step)) return { step, lines: null, hideChange: false };
  }
  const measure = last as NameMeasure;
  const withChange = linesIn(
    measure.innerHeight - measure.changeHeight - measure.gap,
    measure.lineHeight,
  );
  if (withChange >= 1) return { step: "clamp", lines: withChange, hideChange: false };
  return {
    step: "clamp",
    lines: Math.max(1, linesIn(measure.innerHeight, measure.lineHeight)),
    hideChange: true,
  };
};

const px = (value: string): number => Number.parseFloat(value) || 0;

const measureOf = (
  tile: HTMLElement,
  name: HTMLElement,
  change: HTMLElement | null,
): NameMeasure => {
  const tileStyle = getComputedStyle(tile);
  const nameHeight = name.getBoundingClientRect().height;
  return {
    nameWidthOverflow: name.scrollWidth - name.clientWidth,
    nameHeight,
    lineHeight: px(getComputedStyle(name).lineHeight) || nameHeight,
    changeHeight: change?.getBoundingClientRect().height ?? 0,
    gap: change === null ? 0 : px(tileStyle.rowGap),
    innerHeight:
      tile.clientHeight - px(tileStyle.paddingTop) - px(tileStyle.paddingBottom),
  };
};

/**
 * `root` 안의 칸 이름을 모두 다시 맞춘다. 매번 앞선 맞춤(`data-fit`, 줄 수, 등락 숨김)을 풀고 처음부터 잰다. React 가
 * 다루지 않는 속성(`data-fit`, `--name-lines`, 등락의 `hidden`)만 바꾸므로 다시 그려도 지워지지 않는다.
 */
export const fitNames = (
  root: HTMLElement,
  measure: (
    tile: HTMLElement,
    name: HTMLElement,
    change: HTMLElement | null,
  ) => NameMeasure = measureOf,
): void => {
  for (const tile of root.querySelectorAll<HTMLElement>(".market-map__tile")) {
    const name = tile.querySelector<HTMLElement>(".market-map__name");
    if (name === null) continue;
    const change =
      tile.lastElementChild instanceof HTMLElement && tile.lastElementChild !== name
        ? tile.lastElementChild
        : null;
    delete tile.dataset.fit;
    name.style.removeProperty("--name-lines");
    if (change !== null) change.hidden = false;
    // 칸이 아직 크기를 갖지 않았으면(폭 0 컨테이너 등) 판정하지 않는다 — 크기가 생기면 다시 잰다.
    if (tile.clientWidth === 0 || tile.clientHeight === 0) continue;
    const apply = (step: FitStep | "clamp") => {
      if (step === "large") delete tile.dataset.fit;
      else tile.dataset.fit = step;
    };
    const fit = chooseFit(
      tile.classList.contains("market-map__tile--large"),
      (step) => {
        apply(step);
        return measure(tile, name, change);
      },
    );
    apply(fit.step);
    if (fit.lines !== null) name.style.setProperty("--name-lines", String(fit.lines));
    if (change !== null) change.hidden = fit.hideChange;
  }
};
