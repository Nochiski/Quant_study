import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";

import {
  findSector,
  joWon,
  layoutLeaves,
  layoutSectorMap,
  marketSectorQuery,
  sectorMovers,
  sessionShort,
  toMapGroup,
  toMapParents,
  type MapGroup,
  type MapTile,
  type SectorCardBody,
  type SectorGroupBody,
  type SectorMapBody,
} from "../../../entities/market";
import { ApiRequestError, type MarketSectorBody } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { Change, SectorMap, type MapLayer } from "./sector-map";

/** 카드 폭이 이보다 좁으면 지도 대신 많이 오른·내린 업종 목록을 보인다. */
const MAP_MIN_WIDTH = 560;
/** 지도 높이 — 줌 전환이 칸을 카드 크기로 그대로 키우도록 모든 수준이 같다. */
const MAP_HEIGHT = 400;
/** 많이 오른·내린 목록에 넣는 세부 업종의 유동시총 하한(원) — 10조. */
const MOVER_FLOOR_KRW = 10_000_000_000_000;

type View = "map" | "table";

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

const StockTable = ({
  name,
  stocks,
}: {
  name: string;
  stocks: MarketSectorBody["securities"];
}) => {
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
                {stock.return_pct === null || stock.return_pct === undefined ? (
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
};

/** 지금 수준의 지도 칸 배치. 응답·폭이 그대로면 같은 값을 돌려줘야 지도가 층을 갈아 끼우지 않는다(useMemo). */
const mapLayerOf = (
  sectorMap: SectorMapBody,
  parentCode: string | null,
  stocks: MarketSectorBody["securities"] | null | undefined,
  width: number,
): MapLayer | null => {
  if (stocks === null) return null;
  if (stocks !== undefined)
    return {
      tiles: layoutLeaves(
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
        MAP_HEIGHT,
        null,
      ),
      heads: [],
      label: t("market.sector.stockMapLabel"),
      opens: (tile) => tile.kind === "others",
    };
  const parentBody = sectorMap.parents.find(
    (item) => item.group.code === parentCode,
  );
  if (parentBody !== undefined)
    return {
      tiles: layoutLeaves(
        parentBody.children.map(toMapGroup),
        width,
        MAP_HEIGHT,
        null,
      ),
      heads: [],
      label: t("market.sector.mapLabel"),
    };
  return {
    ...layoutSectorMap(toMapParents(sectorMap), width, MAP_HEIGHT),
    label: t("market.sector.mapLabel"),
  };
};

/**
 * 섹터맵 카드: 전체 시장 → 상위 업종 → 세부 업종(종목) 드릴다운. 들어간 자리는 주소(`?sector=`)가 갖고
 * 뒤로 가기로 돌아온다. 수준이 바뀌어도 지도(`SectorMap`)는 그대로 있고 칸만 줌 전환으로 바뀐다. 카드가 좁으면
 * 지도 대신 목록·표다.
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
  const queryClient = useQueryClient();
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
  // 세부 업종의 종목은 그 업종에 들어갈 때 읽는다.
  const detail = useQuery({
    ...marketSectorQuery(child?.code ?? ""),
    enabled: child !== null,
  });
  const stocks =
    child === null ? undefined : (detail.data?.securities ?? null);
  const showMap = view === "map" && width >= MAP_MIN_WIDTH;
  const layer = useMemo(
    () =>
      sectorMap && showMap
        ? mapLayerOf(sectorMap, parent?.code ?? null, stocks, width)
        : null,
    [sectorMap, showMap, parent?.code, stocks, width],
  );
  const open = (tile: MapTile) => {
    if (tile.target === null) setView("table");
    else onSector(tile.target);
  };
  // 세부 업종 칸을 가리키거나 초점을 주면 그 종목 응답을 미리 읽어 둔다(실패는 들어간 뒤 카드가 말한다).
  const prefetch = (tile: MapTile) => {
    if (tile.target === null || sectorMap === null || sectorMap === undefined)
      return;
    if (findSector(sectorMap, tile.target)?.child == null) return;
    queryClient
      .ensureQueryData(marketSectorQuery(tile.target))
      .catch(() => undefined);
  };
  const movers =
    sectorMap === null || sectorMap === undefined ? null : (
      <Movers
        groups={allChildren(sectorMap).map(({ group }) => toMapGroup(group))}
      />
    );

  // 지도 밖 내용: 지도 아래 덧말, 또는 지도를 그리지 않을 때의 표·목록.
  let rest;
  if (sectorMap === null || sectorMap === undefined) {
    rest = <p className="market-card__issue">{card.issue?.message}</p>;
  } else if (child !== null && detail.isError) {
    rest =
      detail.error instanceof ApiRequestError &&
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
  } else if (child !== null) {
    rest = showMap ? (
      <p className="market-card__muted">{t("market.sector.stocksNote")}</p>
    ) : detail.data ? (
      <StockTable name={child.name} stocks={detail.data.securities} />
    ) : (
      <p className="market-card__muted">{t("market.loading")}</p>
    );
  } else if (parent !== null) {
    rest = showMap ? null : (
      <GroupTable
        rows={(
          sectorMap.parents.find((item) => item.group.code === parent.code)
            ?.children ?? []
        ).map((group) => ({ parent: parent.name, group }))}
        label={t("market.sector.tableLabel")}
      />
    );
  } else if (showMap) {
    rest = (
      <>
        {layer?.tiles.some((tile) => tile.kind === "filler") ? (
          <p className="market-card__muted">{t("market.sector.fillerNote")}</p>
        ) : null}
        {movers}
      </>
    );
  } else if (view === "table") {
    rest = (
      <GroupTable
        rows={allChildren(sectorMap)}
        label={t("market.sector.tableLabel")}
      />
    );
  } else {
    rest = (
      <>
        {movers}
        <button
          type="button"
          className="market-link"
          onClick={() => setView("table")}
        >
          {t("market.sector.view.table")}
        </button>
      </>
    );
  }
  const mapShown =
    showMap && sectorMap !== null && sectorMap !== undefined && !detail.isError;

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
        {mapShown ? (
          <SectorMap
            sceneKey={child?.code ?? parent?.code ?? ""}
            path={[parent?.code, child?.code].filter(
              (code): code is string => code !== undefined,
            )}
            layer={layer}
            width={width}
            height={MAP_HEIGHT}
            onOpen={open}
            onIntent={prefetch}
          />
        ) : null}
        {rest}
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
