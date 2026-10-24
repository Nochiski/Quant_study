import { useMemo, useRef, useState } from "react";

import type { HoldingsOverview } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { useVirtualWindow } from "../../../shared/lib/virtual-window";
import { Button, SignedValue } from "../../../shared/ui";
import { useBacktestSecurityTrades } from "../model/backtest-queries";
import { periodBar } from "../model/holdings-layout";
import { BacktestResultFailure } from "./backtest-run-failure";
import {
  changeText,
  holdingMoney,
  holdingPercent,
  holdingPrice,
  holdingShares,
  securityName,
  sideLabel,
} from "./holdings-format";
import { SecurityName } from "./holdings-security-name";

type SecuritySummary = HoldingsOverview["securities"][number];
type SecurityFilter = "all" | "open" | "closed";

/** 종목 목록 한 줄의 높이(px). 거래한 종목이 수백 개여도 보이는 줄만 그린다. */
const SECURITY_ROW_HEIGHT = 60;
/** 목록 칸 높이를 아직 재지 못했을 때의 가정(px, CSS 의 목록 최대 높이). */
const SECURITY_LIST_FALLBACK_HEIGHT = 520;

const isOpen = (item: SecuritySummary) => item.final_quantity !== null;

/** 종목별 보유 기간과, 고른 종목 하나의 체결 기록. 체결 기록은 고른 종목만 읽는다. */
export const HoldingsSecurities = ({
  runId,
  overview,
}: {
  runId: string;
  overview: HoldingsOverview;
}) => {
  // 거르기·고른 종목은 그 실행의 것이다. 다른 실행에서 고른 값은 쓰지 않는다.
  const [choice, setChoice] = useState<{ runId: string; filter: SecurityFilter; picked: string | null }>(
    { runId, filter: "all", picked: null },
  );
  const own = choice.runId === runId ? choice : { runId, filter: "all" as const, picked: null };
  const filter = own.filter;
  const setFilter = (next: SecurityFilter) => setChoice({ ...own, filter: next });
  const setPicked = (next: string) => setChoice({ ...own, picked: next });
  const shown = useMemo(
    () =>
      overview.securities.filter(
        (item) => filter === "all" || (filter === "open") === isOpen(item),
      ),
    [overview.securities, filter],
  );
  // 거르기로 고른 종목이 목록에서 빠지면 상세도 목록의 첫 종목으로 옮긴다.
  const selected =
    shown.find((item) => item.security.security_id === own.picked) ?? shown[0];
  const first = overview.first_session;
  const last = overview.last_session;
  const filters: { id: SecurityFilter; label: string }[] = [
    { id: "all", label: tFill("backtest.holdings.securities.filter.all", { count: overview.securities.length }) },
    { id: "open", label: t("backtest.holdings.securities.filter.open") },
    { id: "closed", label: t("backtest.holdings.securities.filter.closed") },
  ];

  return (
    <div className="holdings__securities">
      <div className="holdings__securities-head">
        <div className="holdings__filters" role="group" aria-label={t("backtest.holdings.securities.filters")}>
          {filters.map((item) => (
            <button
              key={item.id}
              type="button"
              className="holdings__filter"
              aria-pressed={filter === item.id}
              onClick={() => setFilter(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="holdings__hint">{t("backtest.holdings.securities.hint")}</p>
      </div>
      <div className="holdings__securities-body">
        <div className="holdings__security-panel">
          {first && last ? (
            <div className="holdings__axis" aria-hidden="true">
              <span className="holdings__axis-range">
                <span>{first}</span>
                <span>{last}</span>
              </span>
            </div>
          ) : null}
          {shown.length === 0 ? (
            <p className="holdings__notice">{t("backtest.holdings.securities.emptyFilter")}</p>
          ) : (
            <SecurityList
              items={shown}
              selectedId={selected?.security.security_id ?? null}
              first={first}
              last={last}
              onPick={setPicked}
            />
          )}
        </div>
        {selected ? <SecurityTrades runId={runId} summary={selected} /> : null}
      </div>
    </div>
  );
};

const SecurityList = ({
  items,
  selectedId,
  first,
  last,
  onPick,
}: {
  items: readonly SecuritySummary[];
  selectedId: string | null;
  first: string | null;
  last: string | null;
  onPick: (securityId: string) => void;
}) => {
  const viewport = useRef<HTMLDivElement>(null);
  const range = useVirtualWindow(viewport, {
    itemCount: items.length,
    itemHeight: SECURITY_ROW_HEIGHT,
    fallbackViewportHeight: SECURITY_LIST_FALLBACK_HEIGHT,
  });
  return (
    <div
      ref={viewport}
      className="holdings__security-list"
      role="group"
      aria-label={t("backtest.holdings.securities.list")}
      tabIndex={0}
      onScroll={range.onScroll}
      onKeyDown={range.onKeyDown}
    >
      <div style={{ height: range.paddingBefore }} aria-hidden="true" />
      {items.slice(range.start, range.end).map((item) => {
        const open = isOpen(item);
        const status = t(open ? "backtest.holdings.securities.open" : "backtest.holdings.securities.closed");
        return (
          <button
            key={item.security.security_id}
            type="button"
            className="holdings__security-row"
            style={{ height: SECURITY_ROW_HEIGHT }}
            aria-pressed={item.security.security_id === selectedId}
            aria-label={tFill("backtest.holdings.securities.rowLabel", {
              name: securityName(item.security),
              status,
              count: item.fill_count,
            })}
            onClick={() => onPick(item.security.security_id)}
          >
            <span className="holdings__security-cell">
              <SecurityName security={item.security} />
              <span className={`holdings__security-status${open ? " is-open" : ""}`}>
                {tFill("backtest.holdings.securities.status", { status, count: item.fill_count })}
              </span>
            </span>
            <span className="holdings__track" aria-hidden="true">
              {first && last
                ? item.holding_periods.map((period) => {
                    const bar = periodBar(period, first, last);
                    return (
                      <span
                        key={period.start}
                        className={`holdings__track-bar${period.open ? " is-open" : ""}`}
                        style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                      />
                    );
                  })
                : null}
            </span>
            <span className="holdings__security-pnl">
              <SecurityPnl item={item} />
            </span>
          </button>
        );
      })}
      <div style={{ height: range.paddingAfter }} aria-hidden="true" />
    </div>
  );
};

/** 보유 중이면 평가 수익률, 정리했으면 실현 손익. 청산 거래가 없으면(체결 없이 빠짐) 값이 없다. */
const SecurityPnl = ({ item }: { item: SecuritySummary }) => {
  if (isOpen(item)) {
    return item.final_unrealized_return === null ? "—" : (
      <>
        {t("backtest.holdings.securities.unrealized")}{" "}
        <SignedValue value={item.final_unrealized_return} magnitude={holdingPercent(item.final_unrealized_return)} />
      </>
    );
  }
  return item.realized_pnl === null ? "—" : (
    <>
      {t("backtest.holdings.securities.realized")}{" "}
      <SignedValue value={item.realized_pnl} magnitude={holdingMoney(Math.abs(item.realized_pnl))} />
    </>
  );
};

const SecurityTrades = ({ runId, summary }: { runId: string; summary: SecuritySummary }) => {
  const securityId = summary.security.security_id;
  const query = useBacktestSecurityTrades(runId, securityId);
  // 직전 종목의 답(placeholder)은 이 종목의 기록으로 보이지 않는다.
  const report =
    query.data?.run_id === runId && query.data.security.security_id === securityId ? query.data : undefined;
  const periods =
    summary.holding_periods.length === 0
      ? t("backtest.holdings.security.noPeriod")
      : summary.holding_periods
          .map((period) =>
            period.open
              ? tFill("backtest.holdings.security.periodOpen", { start: period.start })
              : tFill("backtest.holdings.security.periodClosed", { start: period.start, end: period.end }),
          )
          .join(", ");
  return (
    <aside className="holdings__security-detail" aria-label={securityName(summary.security)}>
      <h5>
        <SecurityName security={summary.security} />
      </h5>
      <dl>
        <div>
          <dt>{t("backtest.holdings.security.periods")}</dt>
          <dd>{periods}</dd>
        </div>
        <div>
          <dt>{t("backtest.holdings.security.finalQuantity")}</dt>
          <dd>
            {summary.final_quantity === null
              ? t("backtest.holdings.security.none")
              : holdingShares(summary.final_quantity)}
          </dd>
        </div>
      </dl>
      {query.isFetching && report === undefined ? (
        <p role="status">{t("backtest.holdings.security.loading")}</p>
      ) : null}
      {query.error !== null ? (
        <div>
          <BacktestResultFailure error={query.error} />
          <Button onClick={() => { void query.refetch(); }}>{t("backtest.holdings.retry")}</Button>
        </div>
      ) : null}
      {report && report.status !== "available" ? <p className="holdings__notice">{report.detail}</p> : null}
      {report && report.sessions.length > 0 ? (
        <div className="holdings__table-wrap" role="region" tabIndex={0} aria-label={t("backtest.holdings.security.fills")}>
          <table className="holdings__table">
            <thead>
              <tr>
                <th scope="col">{t("backtest.holdings.column.session")}</th>
                <th scope="col">{t("backtest.holdings.column.side")}</th>
                <th scope="col" className="is-number">{t("backtest.holdings.column.quantity")}</th>
                <th scope="col" className="is-number">{t("backtest.holdings.column.price")}</th>
                <th scope="col" className="is-number">{t("backtest.holdings.column.notional")}</th>
                <th scope="col">{t("backtest.holdings.column.change")}</th>
              </tr>
            </thead>
            {/* 세션 하나가 행 묶음 하나다. 체결 없이 장부에서 빠진 날은 체결 칸이 빈 한 줄이다. */}
            {report.sessions.map((session) => {
              const rows = Math.max(1, session.fills.length);
              const change = (
                <td rowSpan={rows}>
                  <span className={`holdings-change holdings-change--${session.change}`}>
                    {changeText(session.change, null)}
                  </span>
                  {session.realized_pnl === null ? null : (
                    <span className="holdings__realized">
                      {t("backtest.holdings.securities.realized")}{" "}
                      <SignedValue
                        value={session.realized_pnl}
                        magnitude={holdingMoney(Math.abs(session.realized_pnl))}
                      />
                    </span>
                  )}
                </td>
              );
              const date = (
                <th scope="rowgroup" rowSpan={rows}>
                  {session.session}
                </th>
              );
              return (
                <tbody key={session.session}>
                  {session.fills.length === 0 ? (
                    <tr>
                      {date}
                      <td colSpan={4}>—</td>
                      {change}
                    </tr>
                  ) : (
                    session.fills.map((fill, index) => (
                      <tr key={fill.fill_id}>
                        {index === 0 ? date : null}
                        <td>
                          <span className={`holdings-side holdings-side--${fill.side}`}>{sideLabel(fill.side)}</span>
                        </td>
                        <td className="is-number">{holdingShares(fill.quantity)}</td>
                        <td className="is-number">{holdingPrice(fill.price)}</td>
                        <td className="is-number">{holdingMoney(fill.notional)}</td>
                        {index === 0 ? change : null}
                      </tr>
                    ))
                  )}
                </tbody>
              );
            })}
          </table>
        </div>
      ) : null}
    </aside>
  );
};
