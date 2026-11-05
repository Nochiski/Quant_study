import { useId } from "react";

import {
  sessionLabel,
  sessionShort,
  shortWon,
  type IndexCardBody,
} from "../../../entities/market";
import { t, tFill } from "../../../shared/config";
import { Badge, SignedValue } from "../../../shared/ui";

const number = (value: number): string =>
  value.toLocaleString("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

/** 지수 카드: 종가·전일 대비(부호·낭독)·3개월 종가 선·선 안의 최고·최저·오늘 거래대금. */
export const IndexCard = ({
  card,
  session,
}: {
  card: IndexCardBody;
  session: string | null;
}) => {
  const titleId = useId();
  const summary = card.summary;
  return (
    <article className="market-card" aria-labelledby={titleId}>
      <div className="market-card__head">
        <h3 id={titleId}>{card.index_name}</h3>
        {summary && session && summary.session !== session ? (
          <Badge tone="warn">
            {tFill("market.cardDate", { date: sessionShort(summary.session) })}
          </Badge>
        ) : null}
      </div>
      {summary === null || summary === undefined ? (
        <p className="market-card__issue">{card.issue?.message}</p>
      ) : (
        <>
          <div className="market-index__value">
            <span className="market-index__close">{number(summary.close)}</span>
            {summary.change !== null && summary.change !== undefined ? (
              <SignedValue
                className="market-index__change"
                value={summary.change}
                magnitude={`${number(Math.abs(summary.change))} (${Math.abs(
                  summary.change_pct ?? 0,
                ).toFixed(2)}%)`}
              />
            ) : null}
          </div>
          <Sparkline
            points={summary.history.map((point) => point.close)}
            label={sparkLabel(card)}
          />
          {summary.history.length > 0 ? (
            <div className="market-index__ends" aria-hidden="true">
              <span>{sessionShort(summary.history[0].session)}</span>
              <span>
                {sessionShort(
                  summary.history[summary.history.length - 1].session,
                )}
              </span>
            </div>
          ) : null}
          <dl className="market-index__meta">
            <div>
              <dt>{t("market.index.high")}</dt>
              <dd>{number(summary.high_close)}</dd>
            </div>
            <div>
              <dt>{t("market.index.low")}</dt>
              <dd>{number(summary.low_close)}</dd>
            </div>
            {summary.value_krw !== null && summary.value_krw !== undefined ? (
              <div>
                <dt>{t("market.index.value")}</dt>
                <dd>{shortWon(summary.value_krw)}</dd>
              </div>
            ) : null}
          </dl>
          <span className="sr-only">{sessionLabel(summary.session)}</span>
        </>
      )}
    </article>
  );
};

const sparkLabel = (card: IndexCardBody): string => {
  const summary = card.summary;
  if (!summary || summary.history.length === 0) return card.index_name;
  const first = summary.history[0];
  const last = summary.history[summary.history.length - 1];
  return tFill("market.index.spark", {
    name: card.index_name,
    start: sessionShort(first.session),
    first: number(first.close),
    end: sessionShort(last.session),
    last: number(last.close),
    high: number(summary.high_close),
    low: number(summary.low_close),
  });
};

/** 축 없는 작은 종가 선. 값은 응답의 종가 그대로이고 크기만 칸에 맞춘다. */
const Sparkline = ({
  points,
  label,
}: {
  points: readonly number[];
  label: string;
}) => {
  if (points.length < 2) return null;
  const high = Math.max(...points);
  const low = Math.min(...points);
  const span = high - low || 1;
  const path = points
    .map(
      (value, index) =>
        `${index},${(((high - value) / span) * 96 + 2).toFixed(2)}`,
    )
    .join(" ");
  return (
    <svg
      className="market-index__spark"
      viewBox={`0 0 ${points.length - 1} 100`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      <polyline points={path} vectorEffect="non-scaling-stroke" />
    </svg>
  );
};
