import { useId, useState } from "react";

import {
  eokWon,
  sessionShort,
  shortWon,
  type FlowCardBody,
  type FlowMarket,
  type FlowPeriod,
  type Investor,
  type InvestorAmountBody,
  type InvestorGroup,
} from "../../../entities/market";
import { t, tFill } from "../../../shared/config";
import { Badge, SignedValue } from "../../../shared/ui";

const PERIODS: readonly FlowPeriod[] = [
  "day",
  "five_sessions",
  "twenty_sessions",
];

// 주체를 어느 묶음에 두는지는 응답의 `group` 이 정한다(순서도 응답 그대로).
const ofGroup = (
  amounts: readonly InvestorAmountBody[],
  group: InvestorGroup,
): InvestorAmountBody[] => amounts.filter((item) => item.group === group);

const amountOf = (
  amounts: readonly InvestorAmountBody[],
  investor: Investor,
): number =>
  amounts.find((item) => item.investor === investor)?.amount_krw ?? 0;

const Amount = ({ value }: { value: number }) => (
  <SignedValue
    kind="flow"
    value={value}
    magnitude={shortWon(value)}
    title={tFill("market.money.eok", { value: eokWon(value) })}
  />
);

/** 투자자별 순매수: 시장·기간을 고르면 네 주체 줄·한 줄 요약·기관 세부·날짜별 표가 그 값으로 바뀐다. */
export const FlowCard = ({
  cards,
  session,
}: {
  cards: readonly FlowCardBody[];
  session: string | null;
}) => {
  const titleId = useId();
  const [market, setMarket] = useState<FlowMarket>("KOSPI");
  const [period, setPeriod] = useState<FlowPeriod>("day");
  const card = cards.find((item) => item.market === market) ?? cards[0];
  const marketName = t(`market.market.${card?.market ?? market}`);
  const total = card?.totals.find((item) => item.period === period);
  const coverage = card?.coverage;
  return (
    <article className="market-card market-flow" aria-labelledby={titleId}>
      <div className="market-card__head">
        <h3 id={titleId}>{t("market.flow.title")}</h3>
        <div className="market-flow__controls">
          <div
            className="market-segment"
            role="group"
            aria-label={t("market.flow.market")}
          >
            {cards.map((item) => (
              <button
                key={item.market}
                type="button"
                aria-pressed={item.market === market}
                onClick={() => setMarket(item.market)}
              >
                {t(`market.market.${item.market}`)}
              </button>
            ))}
          </div>
          <div
            className="market-segment"
            role="group"
            aria-label={t("market.flow.period")}
          >
            {PERIODS.map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={item === period}
                onClick={() => setPeriod(item)}
              >
                {t(`market.flow.period.${item}`)}
              </button>
            ))}
          </div>
        </div>
      </div>
      {coverage && session && coverage.session !== session ? (
        <Badge tone="warn">
          {tFill("market.cardDate", { date: sessionShort(coverage.session) })}
        </Badge>
      ) : null}
      {total === undefined ? (
        <p className="market-card__issue">{card?.issue?.message}</p>
      ) : (
        <>
          <p className="market-flow__summary">{total.summary}</p>
          <p className="market-card__muted">
            {total.sessions === 1
              ? tFill("market.flow.caption.day", {
                  market: marketName,
                  date: sessionShort(total.end),
                })
              : tFill("market.flow.caption.range", {
                  market: marketName,
                  start: sessionShort(total.start),
                  end: sessionShort(total.end),
                  sessions: total.sessions,
                })}
            {coverage
              ? ` · ${tFill("market.flow.scope", {
                  measured:
                    coverage.measured_securities.toLocaleString("ko-KR"),
                  grid: coverage.grid_securities.toLocaleString("ko-KR"),
                  date: sessionShort(coverage.session),
                })}`
              : null}
          </p>
          <Bars amounts={total.amounts} />
          <p className="market-card__muted">
            {tFill("market.flow.zeroSum", {
              amount: `${amountOf(total.amounts, "resident_foreign") < 0 ? "−" : "+"}${shortWon(
                amountOf(total.amounts, "resident_foreign"),
              )}`,
            })}
          </p>
          <details className="ui-disclosure">
            <summary>{t("market.flow.institution")}</summary>
            <dl className="market-flow__parts">
              {ofGroup(total.amounts, "institution_part").map((item) => (
                <div key={item.investor}>
                  <dt>{t(`market.investor.${item.investor}`)}</dt>
                  <dd>
                    <Amount value={item.amount_krw} />
                  </dd>
                </div>
              ))}
            </dl>
          </details>
          <details className="ui-disclosure">
            <summary>{t("market.flow.byDay")}</summary>
            <ByDay
              card={card}
              start={total.start}
              investors={ofGroup(total.amounts, "main").map(
                (item) => item.investor,
              )}
              marketName={marketName}
            />
          </details>
          <details className="ui-disclosure">
            <summary>{t("market.flow.method")}</summary>
            <p className="market-card__muted">{t("market.flow.methodBody")}</p>
          </details>
        </>
      )}
    </article>
  );
};

/** 네 주체 막대. 막대 끝 = 이 시장·기간에서 가장 큰 값. 방향은 옆 숫자가 부호·낭독으로 전한다. */
const Bars = ({ amounts }: { amounts: readonly InvestorAmountBody[] }) => {
  const main = ofGroup(amounts, "main");
  const scale = Math.max(...main.map((item) => Math.abs(item.amount_krw)), 1);
  return (
    <div className="market-flow__bars">
      {main.map(({ investor, amount_krw: value }) => {
        const half = (Math.abs(value) / scale) * 50;
        return (
          <div key={investor} className="market-flow__row">
            <span className="market-flow__label">
              {t(`market.investor.${investor}`)}
            </span>
            <span className="market-flow__track" aria-hidden="true">
              <span
                className={`market-flow__bar market-flow__bar--${value >= 0 ? "up" : "down"}`}
                style={{
                  left: `${value >= 0 ? 50 : 50 - half}%`,
                  width: `${half}%`,
                }}
              />
            </span>
            <Amount value={value} />
          </div>
        );
      })}
      <p className="market-card__muted market-flow__scale">
        {tFill("market.flow.scale", { amount: shortWon(scale) })}
      </p>
    </div>
  );
};

/** 고른 기간(합의 첫날부터)의 날짜별 값. 기간 길이를 화면이 다시 세지 않는다. */
const ByDay = ({
  card,
  start,
  investors,
  marketName,
}: {
  card: FlowCardBody;
  start: string;
  investors: readonly Investor[];
  marketName: string;
}) => {
  const days = card.days.filter((day) => day.session >= start).reverse();
  return (
    <div
      className="market-table-scroll"
      tabIndex={0}
      aria-label={tFill("market.flow.byDayTable", { market: marketName })}
    >
      <table className="data-list-page__table">
        <caption className="sr-only">
          {tFill("market.flow.byDayTable", { market: marketName })}
        </caption>
        <thead>
          <tr>
            <th scope="col">{t("market.flow.date")}</th>
            {investors.map((investor) => (
              <th key={investor} scope="col">
                {t(`market.investor.${investor}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {days.map((day) => (
            <tr key={day.session}>
              <td>{sessionShort(day.session)}</td>
              {investors.map((investor) => {
                const value = amountOf(day.amounts, investor);
                return (
                  <td key={investor}>
                    <SignedValue
                      kind="flow"
                      value={value}
                      magnitude={eokWon(value)}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
