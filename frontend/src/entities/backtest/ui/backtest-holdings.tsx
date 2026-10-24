import { useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { HoldingsOverview } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Button, EmptyState, SignedValue, Tabs, panelId, tabId } from "../../../shared/ui";
import {
  useBacktestHoldings,
  useBacktestSessionHoldings,
} from "../model/backtest-queries";
import { useVirtualWindow } from "../../../shared/lib/virtual-window";
import { sessionPosition } from "../model/holdings-layout";
import { BacktestResultFailure } from "./backtest-run-failure";
import {
  changeText,
  holdingMoney,
  holdingPercent,
  holdingShares,
  holdingWeight,
  securityName,
  type HoldingRowView,
} from "./holdings-format";
import { SecurityName } from "./holdings-security-name";
import { HoldingsSecurities } from "./holdings-securities";
import "./backtest-holdings.css";

type HoldingsView = "sessions" | "securities";

/** 매매한 날 목록 한 줄의 높이(px). 목록은 이 높이로 가상 스크롤해 매매일이 수백 개여도 보이는 줄만 그린다. */
const DATE_ROW_HEIGHT = 56;
/** 목록 칸 높이를 아직 재지 못했을 때의 가정(px, 넓은 화면에서 CSS 의 목록 최대 높이). */
const DATE_LIST_FALLBACK_HEIGHT = 480;
/** 비중 막대·견본 색 단계 수(`backtest-holdings.css` 의 `holdings-swatch--N`). */
const SWATCHES = 5;

/** 완료 결과의 보유 종목과 매매. 값은 backend 투영(`GET …/holdings*`)을 표시만 한다. */
export const BacktestHoldings = ({ runId, enabled }: { runId: string; enabled: boolean }) => {
  const overview = useBacktestHoldings(runId, enabled);
  // 고른 보기는 그 실행의 것이다. 다른 실행으로 옮기면 처음 보기로 돌아간다.
  const [choice, setChoice] = useState<{ runId: string; view: HoldingsView }>({ runId, view: "sessions" });
  const view = choice.runId === runId ? choice.view : "sessions";
  const setView = (next: HoldingsView) => setChoice({ runId, view: next });
  const idBase = useId();
  const data = overview.data;
  return (
    <div className="holdings">
      <header className="holdings__header">
        <div className="holdings__intro">
          {data?.first_session && data.last_session ? (
            <span className="holdings__kicker">
              {tFill("backtest.holdings.period", { first: data.first_session, last: data.last_session })}
            </span>
          ) : null}
          <h4>{t("backtest.holdings.title")}</h4>
          <p>{t("backtest.holdings.description")}</p>
        </div>
        {data ? <HoldingsStats overview={data} /> : null}
      </header>
      {overview.isPending ? <p role="status">{t("backtest.holdings.loading")}</p> : null}
      {overview.error !== null ? (
        <div>
          <p>{t("backtest.holdings.failed")}</p>
          <BacktestResultFailure error={overview.error} />
          <Button onClick={() => { void overview.refetch(); }}>{t("backtest.holdings.retry")}</Button>
        </div>
      ) : null}
      {data && data.trade_sessions.length === 0 ? (
        <EmptyState title={t("backtest.holdings.empty")} />
      ) : null}
      {data && data.trade_sessions.length > 0 ? (
        <>
          <Tabs
            label={t("backtest.holdings.views")}
            idBase={idBase}
            value={view}
            onChange={setView}
            items={[
              { id: "sessions", label: t("backtest.holdings.view.sessions") },
              { id: "securities", label: t("backtest.holdings.view.securities") },
            ]}
          />
          {/* 고른 보기 하나만 그린다. 숨긴 보기의 조회·표를 만들지 않는다. */}
          <div role="tabpanel" id={panelId(idBase, view)} aria-labelledby={tabId(idBase, view)}>
            {view === "sessions" ? (
              <SessionHoldingsView
                runId={runId}
                overview={data}
                onShowSecurities={() => setView("securities")}
              />
            ) : (
              <HoldingsSecurities runId={runId} overview={data} />
            )}
          </div>
        </>
      ) : null}
    </div>
  );
};

const HoldingsStats = ({ overview }: { overview: HoldingsOverview }) => (
  <dl className="holdings__stats">
    <div>
      <dt>{t("backtest.holdings.stats.tradeSessions")}</dt>
      <dd>{tFill("backtest.holdings.stats.days", { count: overview.trade_sessions.length })}</dd>
    </div>
    <div>
      <dt>{t("backtest.holdings.stats.securities")}</dt>
      <dd>{tFill("backtest.holdings.stats.count", { count: overview.securities.length })}</dd>
    </div>
    <div>
      <dt>{t("backtest.holdings.stats.fills")}</dt>
      <dd>{tFill("backtest.holdings.stats.fillCount", { count: overview.fill_count })}</dd>
    </div>
    <div>
      <dt>{t("backtest.holdings.stats.finalHoldings")}</dt>
      <dd>{tFill("backtest.holdings.stats.holdingCount", { count: overview.final_holding_count })}</dd>
    </div>
  </dl>
);

const SessionHoldingsView = ({
  runId,
  overview,
  onShowSecurities,
}: {
  runId: string;
  overview: HoldingsOverview;
  onShowSecurities: () => void;
}) => {
  const sessions = useMemo(
    () => overview.trade_sessions.map((item) => item.session),
    [overview.trade_sessions],
  );
  // 고른 날이 없으면 마지막 매매일이다. 고른 날은 매매일이 아니어도 된다(아무 날 보기). 다른 실행에서 고른
  // 날은 쓰지 않는다.
  const [choice, setChoice] = useState<{ runId: string; day: string } | null>(null);
  const day = choice?.runId === runId ? choice.day : sessions[sessions.length - 1];
  const setPicked = (next: string) => setChoice({ runId, day: next });
  const position = sessionPosition(sessions, day);
  const report = useBacktestSessionHoldings(runId, day);
  // 다른 실행의 직전 답(placeholder)은 보이지 않는다.
  const current = report.data?.run_id === runId ? report.data : undefined;
  const holdings = current?.holdings ?? null;

  return (
    <div className="holdings__sessions">
      <SessionList
        sessions={overview.trade_sessions}
        position={position}
        day={day}
        first={overview.first_session}
        last={overview.last_session}
        onPick={setPicked}
      />

      {report.isPending && current === undefined ? (
        <p role="status">{t("backtest.holdings.loading")}</p>
      ) : null}
      {report.error !== null ? (
        <div>
          <BacktestResultFailure error={report.error} />
          <Button onClick={() => { void report.refetch(); }}>{t("backtest.holdings.retry")}</Button>
        </div>
      ) : null}
      {current && current.status !== "available" ? (
        <p className="holdings__notice">{current.detail}</p>
      ) : null}
      {holdings ? (
        <div
          className="holdings__session-body"
          aria-busy={report.isPlaceholderData || undefined}
        >
          <div className="holdings__session-main">
            <div className="holdings__session-heading">
              <h5>
                {tFill("backtest.holdings.session.heading", {
                  session: holdings.session,
                  count: holdings.holdings.length,
                })}
              </h5>
              <span>
                {tFill("backtest.holdings.session.account", {
                  equity: holdingMoney(holdings.equity),
                  cash:
                    holdings.cash_weight === null
                      ? holdingMoney(holdings.cash)
                      : holdingWeight(holdings.cash_weight),
                })}
              </span>
            </div>
            <WeightBar rows={holdings.holdings} cashWeight={holdings.cash_weight} />
            {holdings.holdings.length === 0 ? (
              <p className="holdings__notice">{t("backtest.holdings.session.emptyHoldings")}</p>
            ) : (
              <div className="holdings__table-wrap" role="region" tabIndex={0} aria-label={t("backtest.holdings.table")}>
                <table className="holdings__table">
                  <thead>
                    <tr>
                      <th scope="col">{t("backtest.holdings.column.security")}</th>
                      <th scope="col">{t("backtest.holdings.column.change")}</th>
                      <th scope="col" className="is-number">{t("backtest.holdings.column.quantity")}</th>
                      <th scope="col" className="is-number">{t("backtest.holdings.column.weight")}</th>
                      <th scope="col" className="is-number">{t("backtest.holdings.column.marketValue")}</th>
                      <th scope="col" className="is-number">{t("backtest.holdings.column.unrealizedReturn")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {holdings.holdings.map((row, index) => (
                      <tr key={row.security.security_id}>
                        <th scope="row">
                          <span className={`holdings-swatch holdings-swatch--${index % SWATCHES}`} aria-hidden="true" />
                          <SecurityName security={row.security} />
                        </th>
                        <td>
                          <span className={`holdings-change holdings-change--${row.change}`}>
                            {changeText(row.change, row.filled_quantity)}
                          </span>
                        </td>
                        <td className="is-number">{holdingShares(row.quantity)}</td>
                        <td className="is-number">{row.weight === null ? "—" : holdingWeight(row.weight)}</td>
                        <td className="is-number">{holdingMoney(row.market_value)}</td>
                        <td className="is-number">
                          {row.unrealized_return === null ? "—" : (
                            <SignedValue value={row.unrealized_return} magnitude={holdingPercent(row.unrealized_return)} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {holdings.closed.length > 0 ? (
              <div className="holdings__closed">
                <h5>{tFill("backtest.holdings.closed.title", { count: holdings.closed.length })}</h5>
                <div className="holdings__table-wrap" role="region" tabIndex={0} aria-label={t("backtest.holdings.closed.table")}>
                  <table className="holdings__table">
                    <thead>
                      <tr>
                        <th scope="col">{t("backtest.holdings.column.security")}</th>
                        <th scope="col">{t("backtest.holdings.column.change")}</th>
                        <th scope="col" className="is-number">{t("backtest.holdings.column.previousQuantity")}</th>
                        <th scope="col" className="is-number">{t("backtest.holdings.column.realizedPnl")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {holdings.closed.map((item) => (
                        <tr key={item.security.security_id}>
                          <th scope="row"><SecurityName security={item.security} /></th>
                          <td>
                            <span className={`holdings-change holdings-change--${item.change}`}>
                              {changeText(item.change, null)}
                            </span>
                          </td>
                          <td className="is-number">{holdingShares(item.previous_quantity)}</td>
                          <td className="is-number">
                            {item.realized_pnl === null ? "—" : (
                              <SignedValue value={item.realized_pnl} magnitude={holdingMoney(Math.abs(item.realized_pnl))} />
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </div>
          <aside className="holdings__totals" aria-label={t("backtest.holdings.totals.title")}>
            <h5>{t("backtest.holdings.totals.title")}</h5>
            <dl>
              <div>
                <dt>{t("backtest.holdings.totals.buy")}</dt>
                <dd>
                  {tFill("backtest.holdings.totals.count", {
                    count: holdings.totals.buy_count,
                    amount: holdingMoney(holdings.totals.buy_notional),
                  })}
                </dd>
              </div>
              <div>
                <dt>{t("backtest.holdings.totals.sell")}</dt>
                <dd>
                  {tFill("backtest.holdings.totals.count", {
                    count: holdings.totals.sell_count,
                    amount: holdingMoney(holdings.totals.sell_notional),
                  })}
                </dd>
              </div>
              <div>
                <dt>{t("backtest.holdings.totals.fees")}</dt>
                <dd>{holdingMoney(holdings.totals.fees)}</dd>
              </div>
              <div>
                <dt>{t("backtest.holdings.totals.taxes")}</dt>
                <dd>{holdingMoney(holdings.totals.taxes)}</dd>
              </div>
            </dl>
            <Button tone="ghost" size="small" onClick={onShowSecurities}>
              {t("backtest.holdings.totals.toSecurities")}
            </Button>
          </aside>
        </div>
      ) : null}
    </div>
  );
};

const WeightBar = ({
  rows,
  cashWeight,
}: {
  rows: readonly HoldingRowView[];
  cashWeight: number | null;
}) => {
  // 숏(음수 비중)은 막대에 그리지 않는다. 표의 비중 칸이 "−" 부호와 함께 보인다.
  const longs = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => row.weight !== null && row.weight > 0);
  if (longs.length === 0) return null;
  const items = [
    ...longs.map(({ row }) => `${securityName(row.security)} ${holdingWeight(row.weight ?? 0)}`),
    ...(cashWeight !== null && cashWeight > 0
      ? [`${t("backtest.holdings.session.cash")} ${holdingWeight(cashWeight)}`]
      : []),
  ].join(", ");
  return (
    <div className="holdings__bar" role="img" aria-label={tFill("backtest.holdings.session.bar", { items })}>
      {longs.map(({ row, index }) => (
        <span
          key={row.security.security_id}
          className={`holdings__bar-segment holdings-swatch--${index % SWATCHES}`}
          style={{ flexGrow: row.weight ?? 0 }}
        />
      ))}
      {cashWeight !== null && cashWeight > 0 ? (
        <span className="holdings__bar-segment holdings__bar-segment--cash" style={{ flexGrow: cashWeight }} />
      ) : null}
    </div>
  );
};

type TradeSession = HoldingsOverview["trade_sessions"][number];

/**
 * 매매한 날 목록. 칸 높이를 넘으면 세로로 스크롤하고, 고른 날이 바뀌면 그 줄이 보이게 스크롤을 맞춘다.
 * 줄에서 ↑·↓ 로 앞뒤 매매일을 고른다.
 */
const SessionList = ({
  sessions,
  position,
  day,
  first,
  last,
  onPick,
}: {
  sessions: readonly TradeSession[];
  position: number;
  day: string;
  first: string | null;
  last: string | null;
  onPick: (session: string) => void;
}) => {
  const viewport = useRef<HTMLDivElement>(null);
  const dateInputId = useId();
  const listId = useId();
  const range = useVirtualWindow(viewport, {
    itemCount: sessions.length,
    itemHeight: DATE_ROW_HEIGHT,
    fallbackViewportHeight: DATE_LIST_FALLBACK_HEIGHT,
  });
  const selectedIsTradeDay = sessions[position]?.session === day;
  // 날짜 입력 중의 값. 키보드로 연도를 한 자리씩 치는 동안의 덜 찬 값을 고른 날로 되돌리지 않는다.
  // 고른 날이 바뀌면(목록·화살표·완성된 입력) 그 날을 보인다.
  const [draft, setDraft] = useState<{ day: string; value: string } | null>(null);
  const inputValue = draft !== null && draft.day === day ? draft.value : day;

  // 고른 날(목록 클릭·화살표·날짜 입력)의 줄이 칸 밖이면 가장 가까운 끝으로 스크롤한다. 칸 안이면 그대로 둔다.
  useLayoutEffect(() => {
    const element = viewport.current;
    if (element === null) return;
    const top = position * DATE_ROW_HEIGHT;
    const height = element.clientHeight || DATE_LIST_FALLBACK_HEIGHT;
    const before = element.scrollTop;
    if (top < before) element.scrollTop = top;
    else if (top + DATE_ROW_HEIGHT > before + height) element.scrollTop = top + DATE_ROW_HEIGHT - height;
    // 브라우저의 scroll 이벤트는 다음 프레임에 온다. 그 사이 빈 줄이 비치지 않게 그려질 범위를 바로 다시 잰다.
    if (element.scrollTop !== before) element.dispatchEvent(new Event("scroll"));
  }, [position]);

  // 화살표로 고른 줄로 초점을 옮긴다. 가상 스크롤이라 그 줄은 스크롤 측정이 반영된 뒤에야 그려질 수 있어,
  // 그려진 범위가 바뀔 때마다 다시 찾는다.
  const focusPending = useRef(false);
  useLayoutEffect(() => {
    if (!focusPending.current) return;
    const row = viewport.current?.querySelector<HTMLButtonElement>(
      `[data-session="${sessions[position]?.session}"]`,
    );
    if (row) {
      row.focus();
      focusPending.current = false;
    }
  }, [position, range.start, range.end, sessions]);

  // 클릭·날짜 입력으로 고르면 초점을 옮기지 않는다(화살표로 고를 때만).
  const pick = (session: string) => {
    focusPending.current = false;
    setDraft(null);
    onPick(session);
  };

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const delta = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (delta === 0 || !(event.target instanceof HTMLButtonElement)) {
      range.onKeyDown(event);
      return;
    }
    event.preventDefault();
    // 고른 날이 매매일이 아니면 그 날의 바로 앞·뒤 매매일로 간다.
    const later = sessions.findIndex((item) => item.session > day);
    const next =
      delta < 0
        ? selectedIsTradeDay || sessions[position].session > day
          ? position - 1
          : position
        : selectedIsTradeDay
          ? position + 1
          : later;
    if (next < 0 || next >= sessions.length) return;
    setDraft(null);
    focusPending.current = true;
    onPick(sessions[next].session);
  };

  return (
    <div className="holdings__date-panel">
      <div className="holdings__date-head">
        <h5 id={listId}>
          {t("backtest.holdings.sessions.title")}{" "}
          <span>{tFill("backtest.holdings.stats.days", { count: sessions.length })}</span>
        </h5>
        <label className="holdings__any-day" htmlFor={dateInputId}>
          {t("backtest.holdings.sessions.anyDay")}
          <input
            id={dateInputId}
            type="date"
            value={inputValue}
            min={first ?? undefined}
            max={last ?? undefined}
            onChange={(event) => {
              const value = event.target.value;
              // 중간 값(예: 0002-…)과 실행 기간 밖의 날은 묻지 않는다. 입력 칸은 친 값을 그대로 둔다.
              if (value.length === 10 && (first === null || value >= first) && (last === null || value <= last))
                pick(value);
              else setDraft({ day, value });
            }}
          />
        </label>
      </div>
      <div
        ref={viewport}
        className="holdings__date-list"
        role="group"
        aria-labelledby={listId}
        // 고른 줄이 가상 창 밖으로 스크롤돼도 Tab 으로 목록에 닿게 한다. 칸에서는 ↑·↓·Page·Home·End 로 스크롤한다.
        tabIndex={0}
        onScroll={range.onScroll}
        onKeyDown={move}
      >
        <div style={{ height: range.paddingBefore }} aria-hidden="true" />
        {sessions.slice(range.start, range.end).map((item) => {
          const selected = selectedIsTradeDay && item.session === day;
          return (
            <button
              key={item.session}
              type="button"
              data-session={item.session}
              className="holdings__date-row"
              style={{ height: DATE_ROW_HEIGHT }}
              aria-pressed={selected}
              // 목록 안 초점은 고른 줄 하나만 Tab 으로 닿는다. 나머지는 화살표로 옮긴다.
              tabIndex={item.session === sessions[position]?.session ? 0 : -1}
              onClick={() => pick(item.session)}
            >
              <span className="holdings__date-value">{item.session}</span>
              <span className="holdings__date-delta">
                {tFill("backtest.holdings.sessions.delta", {
                  opened: item.opened_count,
                  closed: item.closed_count,
                })}
                {" · "}
                {tFill("backtest.holdings.stats.holdingCount", { count: item.holding_count })}
              </span>
            </button>
          );
        })}
        <div style={{ height: range.paddingAfter }} aria-hidden="true" />
      </div>
      <p className="holdings__date-hint">{t("backtest.holdings.sessions.keys")}</p>
    </div>
  );
};
