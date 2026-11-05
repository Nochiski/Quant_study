import {
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";

import {
  percentMagnitude,
  shortWon,
  type MapHead,
  type MapTile,
} from "../../../entities/market";
import { t, tFill } from "../../../shared/config";
import { useMediaQuery } from "../../../shared/lib/media";
import { SignedValue } from "../../../shared/ui";

/** 한 수준(전체 시장·상위 업종·종목)의 칸 배치. 배치 계산은 `entities/market` 의 treemap 이 소유한다. */
export type MapLayer = {
  tiles: readonly MapTile[];
  heads: readonly MapHead[];
  label: string;
  // 누를 수 있는 칸. 누를 수 없는 칸(종목 칸)은 버튼이 아니라 이름만 읽힌다(기본: 모든 칸).
  opens?: (tile: MapTile) => boolean;
};

type Rect = { x: number; y: number; w: number; h: number };

/** 줌 전환 길이와 곡선 — 짧은 ease-out 이라 누른 칸이 카드로 "열리는" 느낌만 남긴다. */
const ZOOM_MS = 300;
const SETTLE_MS = 200;
const EASE_OUT = "cubic-bezier(0.2, 0, 0, 1)";

const direction = (value: number | null): string =>
  value === null || value === 0
    ? ""
    : t(value > 0 ? "market.direction.up" : "market.direction.down");

/**
 * 칸 색 단계(발산 척도)의 경계 — 하루 등락 |%|. 0.5 미만은 중립, 그 위로 0.5·1·2·3 에서 한 단계씩 진해진다(3 이상이
 * 가장 진하다). 색은 크기 감을 주고 정확한 값은 칸 글자가 전한다. 색 값은 `tokens.css` 의 `--sector-*` 가 소유한다.
 */
const STEP_BOUNDS = [0.5, 1, 2, 3] as const;

const step = (value: number | null): string => {
  if (value === null) return "flat";
  const level = STEP_BOUNDS.filter((bound) => Math.abs(value) >= bound).length;
  return level === 0 ? "flat" : `${value > 0 ? "up" : "down"}-${level}`;
};

const LEGEND_STEPS = [
  ...STEP_BOUNDS.map((_, index) => `down-${STEP_BOUNDS.length - index}`),
  "flat",
  ...STEP_BOUNDS.map((_, index) => `up-${index + 1}`),
];
const LEGEND_TICKS = [
  ...[...STEP_BOUNDS].reverse().map((bound) => `−${bound}`),
  ...STEP_BOUNDS.map((bound) => `+${bound}`),
];

/** 범례: 칸 색 단계와 그 경계(척도), 작은 업종 묶음. 칸 글자가 값을 전하므로 장식이다. */
export const SectorLegend = () => (
  <span className="market-legend" aria-hidden="true">
    <span className="market-legend__scale">
      <span>{t("market.sector.legend.scale")}</span>
      <span className="market-legend__ramp">
        {LEGEND_STEPS.map((name) => (
          <span key={name} className={`market-legend__step market-legend__step--${name}`} />
        ))}
        {LEGEND_TICKS.map((tick, index) => (
          <span
            key={tick}
            className="market-legend__tick"
            style={{ left: `${((index + 1) / LEGEND_STEPS.length) * 100}%` }}
          >
            {tick}
          </span>
        ))}
      </span>
    </span>
    <span className="market-legend__item">
      <span className="market-legend__swatch market-legend__swatch--others" />
      {t("market.sector.legend.other")}
    </span>
  </span>
);

/** 큰 칸(px) — 이보다 넓고 높으면 큰 글자로 시작한다(이름이 넘치면 보통 글자로 다시 잰다). */
const LARGE_TILE = { w: 150, h: 80 } as const;

/**
 * 칸 이름 맞추기: 이름은 띄어쓰기 단위로만 줄을 바꾸고(CSS keep-all) 두 줄까지다. 그려진 뒤 실제 크기를 재서,
 * 큰 칸이 넘치면 보통 글자로 다시 재고, 그래도 두 줄을 넘거나 한 단어가 칸보다 넓거나 등락과 함께 칸 높이를
 * 넘으면 이름을 숨긴다(말줄임 없음 — 이름은 낭독 이름·툴팁에 남는다). React 가 다루지 않는 속성(`hidden`,
 * `data-fit`)만 바꾸므로 다시 그려도 지워지지 않고, 배치가 바뀔 때마다 처음부터 다시 잰다.
 */
const fitNames = (root: HTMLElement) => {
  for (const tile of root.querySelectorAll<HTMLElement>(".market-map__tile")) {
    const name = tile.querySelector<HTMLElement>(".market-map__name");
    if (name === null) continue;
    name.hidden = false;
    delete tile.dataset.fit;
    const overflows = () =>
      name.scrollWidth > name.clientWidth ||
      name.scrollHeight > name.clientHeight ||
      tile.scrollHeight > tile.clientHeight;
    if (tile.classList.contains("market-map__tile--large") && overflows())
      tile.dataset.fit = "regular";
    if (overflows()) name.hidden = true;
  }
};

export const Change = ({ value }: { value: number | null }) =>
  value === null ? (
    <span>—</span>
  ) : (
    <SignedValue value={value} magnitude={percentMagnitude(value)} />
  );

const tileLabel = (tile: MapTile): string =>
  tile.kind === "others"
    ? tFill("market.sector.others", { count: tile.members })
    : tile.name;

/**
 * 한 수준의 칸들. Tab 정지는 하나이고 방향키로 칸을 옮긴다(로빙 tabindex). Enter 는 칸의 기본 동작(들어가기)이다.
 * 이름이 들어가지 않는 빈 영역(`filler`)은 초점을 받지 않는다.
 */
const Tiles = ({
  layer,
  width,
  height,
  onOpen,
  onIntent,
  layerRef,
  leaving,
  zooming,
}: {
  layer: MapLayer;
  width: number;
  height: number;
  onOpen: (tile: MapTile) => void;
  // 누르기 전 신호(가리키기) — 들어갈 곳의 응답을 미리 읽는다.
  onIntent: (tile: MapTile) => void;
  layerRef: RefObject<HTMLDivElement | null>;
  // 전환 중 사라지는 수준: 보이기만 하고 읽히거나 눌리지 않는다.
  leaving: boolean;
  // 줌이 도는 동안: 늘어난 글자가 보이지 않게 칸 글자를 숨겼다가 끝나면 드러낸다.
  zooming: boolean;
}) => {
  const { tiles, heads, label, opens } = layer;
  const focusable = tiles.filter(
    (tile) => tile.kind !== "filler" && (opens?.(tile) ?? true),
  );
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.min(active, Math.max(0, focusable.length - 1));
  const move = (event: KeyboardEvent, index: number) => {
    const next =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? index + 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? index - 1
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? focusable.length - 1
              : null;
    if (next === null) return;
    event.preventDefault();
    const bounded = (next + focusable.length) % focusable.length;
    setActive(bounded);
    refs.current[bounded]?.focus();
  };
  const place = (rect: Rect) => ({
    left: `${(rect.x / width) * 100}%`,
    top: `${(rect.y / height) * 100}%`,
    width: `${(rect.w / width) * 100}%`,
    height: `${(rect.h / height) * 100}%`,
  });
  // 툴팁: 가리키거나 초점이 간 칸 하나. 낭독 이름이 같은 내용을 가지므로 화면 전용(aria-hidden)이다.
  const [tip, setTip] = useState<MapTile | null>(null);
  // 칸 크기가 정해진 뒤 칠하기 전에 이름이 들어가는지 잰다(그려진 크기 기준이라 어림과 어긋나지 않는다).
  useLayoutEffect(() => {
    if (layerRef.current !== null) fitNames(layerRef.current);
  }, [layerRef, layer, width, height]);
  const hover = (tile: MapTile) => ({
    onPointerEnter: () => setTip(tile),
    onPointerLeave: () => setTip((shown) => (shown === tile ? null : shown)),
  });
  return (
    <div
      ref={layerRef}
      className={`market-map__layer${zooming ? " market-map__layer--zooming" : ""}`}
      role="group"
      aria-label={label}
      aria-hidden={leaving || undefined}
      inert={leaving}
    >
      {tiles.map((tile) => {
        if (tile.kind === "filler")
          return (
            <div
              key={tile.key}
              className="market-map__tile market-map__tile--filler"
              style={place(tile)}
              aria-hidden="true"
            />
          );
        const index = focusable.indexOf(tile);
        const name = tileLabel(tile);
        const aria = tFill("market.sector.tile", {
          group: tile.parentName ?? "",
          name,
          direction: direction(tile.returnPct),
          change:
            tile.returnPct === null ? "—" : percentMagnitude(tile.returnPct),
        }).replace(/^ · /u, "");
        const large = tile.w >= LARGE_TILE.w && tile.h >= LARGE_TILE.h;
        const className = `market-map__tile market-map__tile--${
          tile.kind === "others" ? "others" : step(tile.returnPct)
        }${large ? " market-map__tile--large" : ""}`;
        const content = (
          <>
            <span className="market-map__name">{name}</span>
            <Change value={tile.returnPct} />
          </>
        );
        if (index < 0)
          return (
            <div
              key={tile.key}
              role="img"
              className={className}
              style={place(tile)}
              aria-label={aria}
              {...hover(tile)}
            >
              {content}
            </div>
          );
        return (
          <button
            key={tile.key}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            tabIndex={index === current ? 0 : -1}
            className={className}
            style={place(tile)}
            aria-label={aria}
            onFocus={() => {
              setActive(index);
              setTip(tile);
            }}
            onBlur={() => setTip(null)}
            onPointerEnter={() => {
              setTip(tile);
              onIntent(tile);
            }}
            onPointerLeave={hover(tile).onPointerLeave}
            onKeyDown={(event) => {
              if (event.key === "Escape") setTip(null);
              else move(event, index);
            }}
            onClick={() => onOpen(tile)}
          >
            {content}
          </button>
        );
      })}
      {heads.map((head) => (
        <div key={head.key} className="market-map__group" style={place(head)}>
          <span className="market-map__head">
            <span className="market-map__name">{head.name}</span>
            <Change value={head.returnPct} />
          </span>
        </div>
      ))}
      {tip !== null ? (
        <TileTip tile={tip} width={width} height={height} />
      ) : null}
    </div>
  );
};

const TIP_HALF_WIDTH = 96;
const TIP_HEIGHT = 64;

/** 칸 툴팁: 등락(값이 먼저) · 이름 · 유동시총. 칸 위에 자리가 없으면 아래, 그것도 없으면 칸 안 위쪽에 뜬다. */
const TileTip = ({
  tile,
  width,
  height,
}: {
  tile: MapTile;
  width: number;
  height: number;
}) => {
  const center = Math.min(
    Math.max(tile.x + tile.w / 2, TIP_HALF_WIDTH),
    width - TIP_HALF_WIDTH,
  );
  const vertical =
    tile.y >= TIP_HEIGHT
      ? { bottom: `${((height - tile.y + 4) / height) * 100}%` }
      : tile.y + tile.h + TIP_HEIGHT <= height
        ? { top: `${((tile.y + tile.h + 4) / height) * 100}%` }
        : { top: `${((tile.y + 8) / height) * 100}%` };
  const name = tileLabel(tile);
  return (
    <div
      className="market-map__tip"
      aria-hidden="true"
      style={{ left: `${(center / width) * 100}%`, ...vertical }}
    >
      <strong>
        <Change value={tile.returnPct} />
      </strong>
      <span>{tile.parentName ? `${tile.parentName} · ${name}` : name}</span>
      <span className="market-map__tip-size">
        {tFill("market.sector.tipSize", { size: shortWon(tile.sizeWeight) })}
      </span>
    </div>
  );
};

type Shown = { key: string; path: readonly string[]; layer: MapLayer };

type Leaving = {
  id: number;
  from: Shown;
  // in: 누른 칸이 카드로 커진다, out: 카드가 원래 칸으로 줄어든다, fade: 위치 관계가 없다(형제 이동 등).
  direction: "in" | "out" | "fade";
  anchor: Rect | null;
};

const startsWith = (path: readonly string[], prefix: readonly string[]) =>
  prefix.every((code, index) => path[index] === code);

/** 깊은 쪽부터 찾는다 — 종목 수준에서 전체로 나오면 세부 업종 칸, 없으면 그 상위 업종 띠로 줄어든다. */
const anchorIn = (layer: MapLayer, codes: readonly string[]): Rect | null => {
  for (const code of [...codes].reverse()) {
    const found =
      layer.heads.find((head) => head.target === code) ??
      layer.tiles.find((tile) => tile.target === code);
    if (found !== undefined) return found;
  }
  return null;
};

const plan = (
  from: Shown,
  path: readonly string[],
  layer: MapLayer | null,
): Pick<Leaving, "direction" | "anchor"> => {
  if (path.length > from.path.length && startsWith(path, from.path)) {
    const anchor = anchorIn(from.layer, path.slice(from.path.length));
    return anchor ? { direction: "in", anchor } : { direction: "fade", anchor };
  }
  if (from.path.length > path.length && startsWith(from.path, path)) {
    const anchor = layer ? anchorIn(layer, from.path.slice(path.length)) : null;
    return anchor ? { direction: "out", anchor } : { direction: "fade", anchor };
  }
  return { direction: "fade", anchor: null };
};

/** `rect` 가 지도 전체를 채우게 키운다(transform-origin 은 왼쪽 위). */
const zoomTo = (rect: Rect, width: number, height: number) =>
  `scale(${width / rect.w}, ${height / rect.h}) translate(${-rect.x}px, ${-rect.y}px)`;

/** 지도 전체를 `rect` 자리로 줄인다. */
const zoomFrom = (rect: Rect, width: number, height: number) =>
  `translate(${rect.x}px, ${rect.y}px) scale(${rect.w / width}, ${rect.h / height})`;

/**
 * 드릴다운 지도. 수준이 바뀌어도 이 컴포넌트와 지도 틀은 그대로 있고 칸 층만 갈아 끼운다 — 들어갈 때는 누른 칸이
 * 카드 전체로 커지며 다음 수준이 나타나고, 나올 때는 반대로 줄어든다. 다음 수준 응답이 아직 없으면(`layer`
 * null) 커진 칸을 그대로 둔 채 그 위에 불러오는 중을 보이고, 도착하면 겹쳐 바꾼다 — 빈 화면이 없다. 움직임
 * 줄이기를 켰거나 Web Animations 가 없으면 즉시 바꾼다.
 */
export const SectorMap = ({
  sceneKey,
  path,
  layer,
  width,
  height,
  onOpen,
  onIntent,
}: {
  // 지금 수준의 이름(주소의 `sector`, 전체 시장은 빈 문자열)과 전체 시장부터의 경로.
  sceneKey: string;
  path: readonly string[];
  // 렌더마다 새로 만들지 않은 값이어야 한다(바뀌면 층을 갈아 끼운다). null 이면 아직 불러오는 중.
  layer: MapLayer | null;
  width: number;
  height: number;
  onOpen: (tile: MapTile) => void;
  onIntent: (tile: MapTile) => void;
}) => {
  const motion =
    !useMediaQuery("(prefers-reduced-motion: reduce)") &&
    typeof HTMLElement.prototype.animate === "function";
  const [seen, setSeen] = useState({ key: sceneKey, path, layer, serial: 0 });
  const [leaving, setLeaving] = useState<Leaving | null>(null);
  if (seen.key !== sceneKey) {
    // 수준이 바뀌었다(렌더 중 상태 맞추기). 방금까지 보이던 층을 떠나는 층으로 남긴다.
    // 불러오는 중에 다시 움직이면 커진 채 남아 있던 층이 떠나는 층이다. 그 층으로 되돌아왔으면 떠날 것이 없다.
    const kept: Shown | null = seen.layer
      ? { key: seen.key, path: seen.path, layer: seen.layer }
      : (leaving?.from ?? null);
    const from = kept?.key === sceneKey ? null : kept;
    const serial = seen.serial + 1;
    setSeen({ key: sceneKey, path, layer, serial });
    setLeaving(
      from !== null && (motion || layer === null)
        ? { id: serial, from, ...plan(from, path, layer) }
        : null,
    );
  } else if (seen.layer !== layer) {
    setSeen({ ...seen, layer });
    // 움직임이 없으면 다음 층이 도착하는 즉시 떠나는 층을 치운다.
    if (!motion && layer !== null) setLeaving(null);
  }

  const outgoing = useRef<HTMLDivElement>(null);
  const incoming = useRef<HTMLDivElement>(null);
  const refocus = useRef(false);
  const heldId = useRef<number | null>(null);
  const ready = layer !== null;
  useLayoutEffect(() => {
    if (leaving === null) return;
    const out = outgoing.current;
    const next = incoming.current;
    // 키보드로 들어갔으면 초점을 새 층의 첫 칸으로 옮긴다(떠나는 층은 inert 라 초점이 사라진다).
    if (out?.contains(document.activeElement)) refocus.current = true;
    if (refocus.current && next !== null) {
      refocus.current = false;
      next.querySelector<HTMLElement>('[tabindex="0"]')?.focus({ preventScroll: true });
    }
    if (!motion || out === null) return;
    const { anchor } = leaving;
    const zoomed = anchor ? zoomTo(anchor, width, height) : "none";
    const shrunk = anchor ? zoomFrom(anchor, width, height) : "none";
    const timing = (duration: number): KeyframeAnimationOptions => ({
      duration,
      easing: EASE_OUT,
      fill: "both",
    });
    const animations: Animation[] = [];
    let finishing: Animation | null = null;
    if (next === null) {
      // 아직 불러오는 중: 누른 칸을 카드 크기로 키운 채 멈춘다(형제 이동·나오기는 그대로 흐리게).
      animations.push(
        leaving.direction === "in"
          ? out.animate([{ transform: "none" }, { transform: zoomed }], timing(ZOOM_MS))
          : out.animate([{ opacity: 1 }, { opacity: 0.4 }], timing(SETTLE_MS)),
      );
    } else if (leaving.direction === "in") {
      // 불러오는 동안 커진 채 멈춰 있었으면 그 자리에서 겹쳐 바꾸기만 한다.
      const held = heldId.current === leaving.id;
      finishing = out.animate(
        held
          ? [{ transform: zoomed, opacity: 1 }, { transform: zoomed, opacity: 0 }]
          : [{ transform: "none", opacity: 1 }, { transform: zoomed, opacity: 0 }],
        timing(held ? SETTLE_MS : ZOOM_MS),
      );
      animations.push(
        finishing,
        next.animate(
          held
            ? [{ opacity: 0 }, { opacity: 1 }]
            : [
                { transform: shrunk, opacity: 0 },
                { transform: "none", opacity: 1 },
              ],
          timing(held ? SETTLE_MS : ZOOM_MS),
        ),
      );
    } else {
      finishing = out.animate(
        leaving.direction === "out"
          ? [{ transform: "none", opacity: 1 }, { transform: shrunk, opacity: 0 }]
          : [{ opacity: 1 }, { opacity: 0 }],
        timing(leaving.direction === "out" ? ZOOM_MS : SETTLE_MS),
      );
      animations.push(
        finishing,
        next.animate(
          leaving.direction === "out"
            ? [
                { transform: zoomed, opacity: 0 },
                { transform: "none", opacity: 1 },
              ]
            : [{ opacity: 0 }, { opacity: 1 }],
          timing(leaving.direction === "out" ? ZOOM_MS : SETTLE_MS),
        ),
      );
    }
    const id = leaving.id;
    if (next === null) heldId.current = id;
    finishing?.finished.then(
      () => setLeaving((current) => (current?.id === id ? null : current)),
      // 다음 전환이 먼저 시작돼 취소됐다 — 그 전환이 층을 치운다.
      () => undefined,
    );
    return () => animations.forEach((animation) => animation.cancel());
  }, [leaving, ready, motion, width, height]);

  return (
    <div className="market-map" style={{ height }}>
      {leaving !== null ? (
        <Tiles
          key={leaving.from.key}
          layer={leaving.from.layer}
          width={width}
          height={height}
          onOpen={onOpen}
          onIntent={onIntent}
          layerRef={outgoing}
          leaving
          zooming
        />
      ) : null}
      {layer !== null ? (
        <Tiles
          key={sceneKey}
          layer={layer}
          width={width}
          height={height}
          onOpen={onOpen}
          onIntent={onIntent}
          layerRef={incoming}
          leaving={false}
          zooming={leaving !== null}
        />
      ) : (
        <p className="market-map__loading" role="status">
          <span>{t("market.loading")}</span>
        </p>
      )}
    </div>
  );
};
