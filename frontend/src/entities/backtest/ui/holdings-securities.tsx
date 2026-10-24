import { Fragment, useState } from "react";

import type { HoldingsOverview } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Button, SignedValue } from "../../../shared/ui";
import { useBacktestSecurityTrades } from "../model/backtest-queries";
import { periodBar } from "../model/holdings-layout";
import { BacktestResultFailure } from "./backtest-run-failure";
import {
  changeText,
  holdingMoney,
  holdingPercent,
  holdingShares,
  securityName,
  sideLabel,
} from "./holdings-format";
import { SecurityName } from "./holdings-security-name";

type SecuritySummary = HoldingsOverview["securities"][number];
type SecurityFilter = "all" | "open" | "closed";

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
  const selectedId = own.picked ?? overview.securities[0]?.security.security_id ?? null;
  const shown = overview.securities.filter(
    (item) => filter === "all" || (filter === "open") === isOpen(item),
  );
  const selected = overview.securities.find((item) => item.security.security_id === selectedId);
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
        <div className="holdings__security-list" role="group" aria-label={t("backtest.holdings.securities.list")}>
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
          ) : null}
          {shown.map((item) => {
            const open = isOpen(item);
            const status = t(open ? "backtest.holdings.securities.open" : "backtest.holdings.securities.closed");
            return (
              <button
                key={item.security.security_id}
                type="button"
                className="holdings__security-row"
                aria-pressed={item.security.security_id === selectedId}
                aria-label={tFill("backtest.holdings.securities.rowLabel", {
                  name: securityName(item.security),
                  status,
                  count: item.fill_count,
                })}
                onClick={() => setPicked(item.security.security_id)}
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
                  {open ? (
                    item.final_unrealized_return === null ? "—" : (
                      <>
                        {t("backtest.holdings.securities.unrealized")}{" "}
                        <SignedValue
                          value={item.final_unrealized_return}
                          magnitude={holdingPercent(item.final_unrealized_return)}
                        />
                      </>
                    )
                  ) : (
                    <>
                      {t("backtest.holdings.securities.realized")}{" "}
                      <SignedValue value={item.realized_pnl} magnitude={holdingMoney(Math.abs(item.realized_pnl))} />
                    </>
                  )}
                </span>
              </button>
            );
          })}
        </div>
        {selected ? <SecurityTrades runId={runId} summary={selected} /> : null}
      </div>
    </div>
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
            <tbody>
              {report.sessions.map((session) => (
                <Fragment key={session.session}>
                  {session.fills.map((fill, index) => (
                    <tr key={fill.fill_id}>
                      {index === 0 ? (
                        <th scope="rowgroup" rowSpan={session.fills.length}>{session.session}</th>
                      ) : null}
                      <td>
                        <span className={`holdings-side holdings-side--${fill.side}`}>{sideLabel(fill.side)}</span>
                      </td>
                      <td className="is-number">{holdingShares(fill.quantity)}</td>
                      <td className="is-number">{holdingMoney(fill.price)}</td>
                      <td className="is-number">{holdingMoney(fill.notional)}</td>
                      {index === 0 ? (
                        <td rowSpan={session.fills.length}>
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
                      ) : null}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </aside>
  );
};
