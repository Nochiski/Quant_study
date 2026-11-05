import { useQuery } from "@tanstack/react-query";
import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";

import {
  findSector,
  joWon,
  layoutLeaves,
  layoutSectorMap,
  marketSectorQuery,
  percentMagnitude,
  sectorMovers,
  sessionShort,
  toMapGroup,
  toMapParents,
  type MapGroup,
  type MapHead,
  type MapTile,
  type SectorCardBody,
  type SectorGroupBody,
  type SectorMapBody,
} from "../../../entities/market";
import { ApiRequestError } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Button, SignedValue } from "../../../shared/ui";

/** 카드 폭이 이보다 좁으면 지도 대신 많이 오른·내린 업종 목록을 보인다. */
const MAP_MIN_WIDTH = 560;
const MAP_HEIGHT = 320;
const DRILL_HEIGHT = 400;
/** 많이 오른·내린 목록에 넣는 세부 업종의 유동시총 하한(원) — 10조. */
const MOVER_FLOOR_KRW = 10_000_000_000_000;

type View = "map" | "table";

const direction = (value: number | null): string =>
  value === null || value === 0
    ? ""
    : t(value > 0 ? "market.direction.up" : "market.direction.down");

const tone = (value: number | null): string =>
  value === null || value === 0 ? "flat" : value > 0 ? "up" : "down";

const Change = ({ value }: { value: number | null }) =>
  value === null ? (
    <span>—</span>
  ) : (
    <SignedValue value={value} magnitude={percentMagnitude(value)} />
  );

const useWidth = (ref: RefObject<HTMLElement | null>): number => {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    setWidth(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
};

const tileLabel = (tile: MapTile): string =>
  tile.kind === "others"
    ? tFill("market.sector.others", { count: tile.members })
    : tile.name;

/**
 * 칸들. Tab 정지는 하나이고 방향키로 칸을 옮긴다(로빙 tabindex). Enter 는 칸의 기본 동작(들어가기)이다.
 * 이름이 들어가지 않는 빈 영역(`filler`)은 초점을 받지 않는다.
 */
const Tiles = ({
  tiles,
  heads,
  width,
  height,
  label,
  onOpen,
  opens,
}: {
  tiles: readonly MapTile[];
  heads: readonly MapHead[];
  width: number;
  height: number;
  label: string;
  onOpen: (tile: MapTile) => void;
  // 누를 수 있는 칸. 누를 수 없는 칸(종목 칸)은 버튼이 아니라 이름만 읽힌다(기본: 모든 칸).
  opens?: (tile: MapTile) => boolean;
}) => {
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
  const place = (rect: { x: number; y: number; w: number; h: number }) => ({
    left: `${(rect.x / width) * 100}%`,
    top: `${(rect.y / height) * 100}%`,
    width: `${(rect.w / width) * 100}%`,
    height: `${(rect.h / height) * 100}%`,
  });
  return (
    <div
      className="market-map"
      role="group"
      aria-label={label}
      style={{ height }}
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
        const className = `market-map__tile market-map__tile--${tone(tile.returnPct)}${
          tile.kind === "others" ? " market-map__tile--others" : ""
        }`;
        if (index < 0)
          return (
            <div
              key={tile.key}
              role="img"
              className={className}
              style={place(tile)}
              aria-label={aria}
              title={aria}
            >
              <span className="market-map__name">{name}</span>
              <Change value={tile.returnPct} />
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
            title={aria}
            onFocus={() => setActive(index)}
            onKeyDown={(event) => move(event, index)}
            onClick={() => onOpen(tile)}
          >
            <span className="market-map__name">{name}</span>
            <Change value={tile.returnPct} />
          </button>
        );
      })}
      {heads.map((head) => (
        <div key={head.key} className="market-map__group" style={place(head)}>
          <span className="market-map__head">
            <span className="market-map__name">{head.name}</span>{" "}
            <Change value={head.returnPct} />
          </span>
        </div>
      ))}
    </div>
  );
};

const GroupTable = ({
  rows,
  label,
}: {
  rows: readonly { parent: string; group: SectorGroupBody }[];
  label: string;
}) => (
  <div
    className="market-table-scroll market-table-scroll--tall"
    tabIndex={0}
    aria-label={label}
  >
    <table className="data-list-page__table">
      <caption className="sr-only">{label}</caption>
      <thead>
        <tr>
          <th scope="col">{t("market.sector.l1")}</th>
          <th scope="col">{t("market.sector.l2")}</th>
          <th scope="col">{t("market.sector.size")}</th>
          <th scope="col">{t("market.sector.change")}</th>
          <th scope="col">{t("market.sector.count")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ parent, group }) => (
          <tr key={group.code}>
            <td>{parent}</td>
            <td>{group.name}</td>
            <td>{joWon(group.size_weight_krw)}</td>
            <td>
              <Change value={group.return_pct ?? null} />
            </td>
            <td>{group.securities.toLocaleString("ko-KR")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const Movers = ({ groups }: { groups: readonly MapGroup[] }) => {
  const { risers, fallers } = sectorMovers(groups, MOVER_FLOOR_KRW, 3);
  const list = (title: string, items: readonly MapGroup[]) => (
    <div>
      <p className="market-card__muted market-movers__title">
        {title}{" "}
        <span>
          ({tFill("market.sector.moverFloor", { amount: joWon(MOVER_FLOOR_KRW) })})
        </span>
      </p>
      <ol className="market-movers__list">
        {items.map((item) => (
          <li key={item.code}>
            <span>{item.name}</span> <Change value={item.returnPct} />
          </li>
        ))}
      </ol>
    </div>
  );
  return (
    <div className="market-movers">
      {list(t("market.sector.risers"), risers)}
      {list(t("market.sector.fallers"), fallers)}
    </div>
  );
};

const allChildren = (sectorMap: SectorMapBody) =>
  sectorMap.parents.flatMap((parent) =>
    parent.children.map((group) => ({ parent: parent.group.name, group })),
  );

/** 세부 업종 하나의 종목 지도·표(응답은 drill 할 때 읽는다). */
const StockLevel = ({
  code,
  name,
  view,
  width,
  onTable,
}: {
  code: string;
  name: string;
  view: View;
  width: number;
  onTable: () => void;
}) => {
  const detail = useQuery(marketSectorQuery(code));
  if (detail.isPending)
    return <p className="market-card__muted">{t("market.loading")}</p>;
  if (detail.isError)
    return detail.error instanceof ApiRequestError &&
      detail.error.code === "market.sector.not_found" ? (
      <p className="market-card__issue">{t("market.sector.unknown")}</p>
    ) : (
      <div className="market-card__issue" role="alert">
        <p>{t("market.error")}</p>
        <Button size="small" onClick={() => void detail.refetch()}>
          {t("market.retry")}
        </Button>
      </div>
    );
  const stocks = detail.data.securities;
  if (view === "table" || width < MAP_MIN_WIDTH) {
    const label = tFill("market.sector.stockTableLabel", { name });
    return (
      <div
        className="market-table-scroll market-table-scroll--tall"
        tabIndex={0}
        aria-label={label}
      >
        <table className="data-list-page__table">
          <caption className="sr-only">{label}</caption>
          <thead>
            <tr>
              <th scope="col">{t("market.sector.stock")}</th>
              <th scope="col">{t("market.sector.size")}</th>
              <th scope="col">{t("market.sector.change")}</th>
            </tr>
          </thead>
          <tbody>
            {stocks.map((stock) => (
              <tr key={stock.ticker}>
                <td>{stock.name}</td>
                <td>{joWon(stock.size_weight_krw)}</td>
                <td>
                  {stock.return_pct === null ||
                  stock.return_pct === undefined ? (
                    t("market.sector.untraded")
                  ) : (
                    <Change value={stock.return_pct} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  const tiles = layoutLeaves(
    stocks.map((stock) => ({
      code: stock.ticker,
      name: stock.name,
      sizeWeight: stock.size_weight_krw,
      returnWeight:
        stock.return_pct === null || stock.return_pct === undefined
          ? 0
          : stock.size_weight_krw,
      returnPct: stock.return_pct ?? null,
    })),
    width,
    DRILL_HEIGHT,
    null,
  );
  return (
    <>
      <Tiles
        tiles={tiles}
        heads={[]}
        width={width}
        height={DRILL_HEIGHT}
        label={t("market.sector.stockMapLabel")}
        onOpen={onTable}
        opens={(tile) => tile.kind === "others"}
      />
      <p className="market-card__muted">{t("market.sector.stocksNote")}</p>
    </>
  );
};

/**
 * 섹터맵 카드: 전체 시장 → 상위 업종 → 세부 업종(종목) 드릴다운. 들어간 자리는 주소(`?sector=`)가 갖고
 * 뒤로 가기로 돌아온다. 카드가 좁으면 지도 대신 목록이다.
 */
export const SectorCard = ({
  card,
  session,
  sector,
  onSector,
}: {
  card: SectorCardBody;
  session: string | null;
  sector: string | undefined;
  onSector: (code: string | undefined) => void;
}) => {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [view, setView] = useState<View>("map");
  const [shownSector, setShownSector] = useState(sector);
  if (shownSector !== sector) {
    // 다른 자리로 들어가거나(뒤로 가기 포함) 나오면 지도부터 보인다(렌더 중 상태 맞추기).
    setShownSector(sector);
    setView("map");
  }
  const sectorMap = card.sector_map;
  const location = sectorMap && sector ? findSector(sectorMap, sector) : null;
  const parent = location?.parent ?? null;
  const child = location?.child ?? null;
  const parentBody = parent
    ? sectorMap?.parents.find((item) => item.group.code === parent.code)
    : undefined;
  const narrow = width > 0 && width < MAP_MIN_WIDTH;
  const open = (tile: MapTile) => {
    if (tile.target === null) setView("table");
    else onSector(tile.target);
  };
  const fillers = (tiles: readonly MapTile[]) =>
    tiles.some((tile) => tile.kind === "filler");

  let body;
  if (sectorMap === null || sectorMap === undefined) {
    body = <p className="market-card__issue">{card.issue?.message}</p>;
  } else if (child !== null && parent !== null) {
    body = (
      <StockLevel
        code={child.code}
        name={child.name}
        view={view}
        width={width}
        onTable={() => setView("table")}
      />
    );
  } else if (parentBody !== undefined) {
    const rows = parentBody.children.map((group) => ({
      parent: parentBody.group.name,
      group,
    }));
    if (view === "table" || narrow) {
      body = <GroupTable rows={rows} label={t("market.sector.tableLabel")} />;
    } else {
      const tiles = layoutLeaves(
        parentBody.children.map(toMapGroup),
        width,
        DRILL_HEIGHT,
        null,
      );
      body = (
        <Tiles
          tiles={tiles}
          heads={[]}
          width={width}
          height={DRILL_HEIGHT}
          label={t("market.sector.mapLabel")}
          onOpen={open}
        />
      );
    }
  } else if (view === "table") {
    body = (
      <GroupTable
        rows={allChildren(sectorMap)}
        label={t("market.sector.tableLabel")}
      />
    );
  } else if (narrow || width === 0) {
    body = (
      <>
        <Movers
          groups={allChildren(sectorMap).map(({ group }) => toMapGroup(group))}
        />
        <button
          type="button"
          className="market-link"
          onClick={() => setView("table")}
        >
          {t("market.sector.view.table")}
        </button>
      </>
    );
  } else {
    const layout = layoutSectorMap(toMapParents(sectorMap), width, MAP_HEIGHT);
    body = (
      <>
        <Tiles
          tiles={layout.tiles}
          heads={layout.heads}
          width={width}
          height={MAP_HEIGHT}
          label={t("market.sector.mapLabel")}
          onOpen={open}
        />
        {fillers(layout.tiles) ? (
          <p className="market-card__muted">{t("market.sector.fillerNote")}</p>
        ) : null}
        <Movers
          groups={allChildren(sectorMap).map(({ group }) => toMapGroup(group))}
        />
      </>
    );
  }

  return (
    <article className="market-card market-sector" aria-labelledby={titleId}>
      <div className="market-card__head">
        <div className="market-sector__title">
          {parent !== null ? (
            <nav
              aria-label={t("market.sector.location")}
              className="market-sector__crumbs"
            >
              <button
                type="button"
                className="market-link"
                onClick={() => onSector(undefined)}
              >
                {t("market.sector.all")}
              </button>
              {" › "}
              {child !== null ? (
                <button
                  type="button"
                  className="market-link"
                  onClick={() => onSector(parent.code)}
                >
                  {parent.name}
                </button>
              ) : (
                <span aria-current="page">{parent.name}</span>
              )}
              {child !== null ? (
                <>
                  {" › "}
                  <span aria-current="page">{child.name}</span>
                </>
              ) : null}
            </nav>
          ) : null}
          <h3 id={titleId}>
            {child?.name ?? parent?.name ?? t("market.sector.title")}{" "}
            {(child ?? parent) !== null ? (
              <Change value={(child ?? parent)?.return_pct ?? null} />
            ) : null}
          </h3>
          {sectorMap ? (
            <p className="market-card__muted">
              <strong>
                {tFill("market.sector.scope", {
                  date: sessionShort(sectorMap.session),
                })}
              </strong>
              {" · "}
              {(child ?? parent) !== null
                ? tFill("market.sector.detail", {
                    count: (child ?? parent)?.securities ?? 0,
                    size: tFill("market.money.jo", {
                      value: joWon((child ?? parent)?.size_weight_krw ?? 0),
                    }),
                  })
                : tFill("market.sector.basis", {
                    snapshot: sessionShort(sectorMap.snapshot_date),
                  })}
            </p>
          ) : null}
        </div>
        <div className="market-sector__tools">
          <span className="market-legend" aria-hidden="true">
            <span className="market-legend__item">
              <span className="market-legend__swatch market-legend__swatch--up" />
              {t("market.sector.legend.up")}
            </span>
            <span className="market-legend__item">
              <span className="market-legend__swatch market-legend__swatch--down" />
              {t("market.sector.legend.down")}
            </span>
            <span className="market-legend__item">
              <span className="market-legend__swatch market-legend__swatch--others" />
              {t("market.sector.legend.other")}
            </span>
          </span>
          <div
            className="market-segment"
            role="group"
            aria-label={t("market.sector.view")}
          >
            <button
              type="button"
              aria-pressed={view === "map"}
              onClick={() => setView("map")}
            >
              {t("market.sector.view.map")}
            </button>
            <button
              type="button"
              aria-pressed={view === "table"}
              onClick={() => setView("table")}
            >
              {t("market.sector.view.table")}
            </button>
          </div>
        </div>
      </div>
      <div ref={box} className="market-sector__body">
        {body}
      </div>
      {sectorMap && session && sectorMap.session !== session ? (
        <p className="market-card__muted">
          {tFill("market.cardDate", { date: sessionShort(sectorMap.session) })}
        </p>
      ) : null}
      {sectorMap && sectorMap.untraded > 0 ? (
        <p className="market-card__muted">
          {tFill("market.sector.untradedNote", { count: sectorMap.untraded })}
        </p>
      ) : null}
      <details className="ui-disclosure">
        <summary>{t("market.sector.method")}</summary>
        <p className="market-card__muted">{t("market.sector.methodBody")}</p>
      </details>
    </article>
  );
};
