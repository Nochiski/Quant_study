import { useId } from "react";

import type { BacktestRunResult } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import "./performance-chart.css";

const WIDTH = 620;
const EQUITY_HEIGHT = 200;
const DRAWDOWN_HEIGHT = 110;
const TICK_COUNT = 4;

const compactMoney = new Intl.NumberFormat("ko-KR", {
  notation: "compact",
  maximumFractionDigits: 2,
});
const money = (value: number) =>
  `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}`;
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

type Scale = { ticks: number[]; y: (value: number) => number };

/** 값 범위를 그릴 높이에 맞춘다. 축 범위만 정하고 서버 값을 다시 계산하지 않는다. */
const scale = (values: readonly number[], height: number): Scale => {
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = Math.max(maximum - minimum, Math.abs(maximum) * 0.01, 1e-9);
  return {
    ticks: Array.from(
      { length: TICK_COUNT },
      (_, index) => maximum - (span * index) / (TICK_COUNT - 1),
    ),
    y: (value) => height - ((value - minimum) / span) * height,
  };
};

const linePath = (
  points: ReadonlyArray<readonly [number, number | null]>,
  x: (index: number) => number,
  y: (value: number) => number,
) => {
  let open = false;
  return points
    .map(([index, value]) => {
      if (value === null) {
        open = false;
        return "";
      }
      const command = open ? "L" : "M";
      open = true;
      return `${command}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
    })
    .filter(Boolean)
    .join(" ");
};

/** 해마다 첫 세션에 연도 눈금을 둔다. */
const yearTicks = (sessions: readonly string[]) =>
  sessions.flatMap((session, index) =>
    index === 0 || session.slice(0, 4) !== sessions[index - 1]!.slice(0, 4)
      ? [{ index, label: session.slice(0, 4) }]
      : [],
  );

const GridLines = ({ height }: { height: number }) => (
  <>
    {Array.from({ length: TICK_COUNT }, (_, index) => {
      const y = (height * index) / (TICK_COUNT - 1);
      return <line key={index} x1="0" x2={WIDTH} y1={y} y2={y} />;
    })}
  </>
);

/**
 * 자산 곡선과 낙폭을 같은 가로축으로 위아래에 붙인다(RD-07-D). 범례와 눈금은 그래프 밖에 두고,
 * 벤치마크는 점선이다. 그래프마다 화면 읽기용 한 문장 요약을 단다.
 */
export const PerformanceChart = ({
  id,
  series,
  benchmarkId,
}: {
  id: string;
  series: BacktestRunResult["series"];
  benchmarkId: string | null | undefined;
}) => {
  const titleId = useId();
  const equity = series.equity;
  if (equity.length === 0) {
    return (
      <section id={id} tabIndex={-1} className="performance-chart" aria-labelledby={titleId}>
        <h4 id={titleId}>{t("backtest.chart.title")}</h4>
        <p className="inline-state">{t("backtest.result.chartEmpty")}</p>
      </section>
    );
  }
  const sessions = equity.map((point) => point.session);
  const position = new Map(sessions.map((session, index) => [session, index]));
  const last = Math.max(sessions.length - 1, 1);
  const x = (index: number) => (index / last) * WIDTH;

  const benchmarkValues = equity
    .map((point) => point.benchmark_equity)
    .filter((value): value is number => value !== null);
  const hasBenchmark = benchmarkValues.length > 0;
  const equityScale = scale(
    [...equity.map((point) => point.equity), ...benchmarkValues],
    EQUITY_HEIGHT,
  );
  const drawdown = series.drawdown.filter((point) => position.has(point.session));
  const drawdownScale = scale(
    [0, ...drawdown.map((point) => point.drawdown)],
    DRAWDOWN_HEIGHT,
  );
  const deepest = drawdown.reduce<(typeof drawdown)[number] | null>(
    (lowest, point) =>
      lowest === null || point.drawdown < lowest.drawdown ? point : lowest,
    null,
  );

  const benchmarkLabel = tFill("backtest.chart.benchmark", {
    id: benchmarkId ?? "",
  }).trim();
  const equitySummary = [
    tFill("backtest.chart.equitySummary", {
      start: sessions[0],
      end: sessions.at(-1),
      from: money(equity[0]!.equity),
      to: money(equity.at(-1)!.equity),
    }),
    hasBenchmark
      ? tFill("backtest.chart.benchmarkSummary", {
          from: money(benchmarkValues[0]!),
          to: money(benchmarkValues.at(-1)!),
        })
      : null,
  ]
    .filter(Boolean)
    .join(" ");
  const drawdownSummary =
    deepest === null
      ? t("backtest.result.chartEmpty")
      : tFill("backtest.chart.drawdownSummary", {
          date: deepest.session,
          value: percent(deepest.drawdown),
        });

  return (
    <section id={id} tabIndex={-1} className="performance-chart" aria-labelledby={titleId}>
      <header>
        <h4 id={titleId}>{t("backtest.chart.title")}</h4>
        <ul className="performance-chart__legend">
          <li>
            <svg aria-hidden="true" viewBox="0 0 24 8">
              <line className="performance-chart__strategy" x1="0" x2="24" y1="4" y2="4" />
            </svg>
            {t("backtest.chart.strategy")}
          </li>
          {hasBenchmark ? (
            <li>
              <svg aria-hidden="true" viewBox="0 0 24 8">
                <line className="performance-chart__benchmark" x1="0" x2="24" y1="4" y2="4" />
              </svg>
              {benchmarkLabel}
            </li>
          ) : null}
        </ul>
      </header>
      <div className="performance-chart__panel">
        <h5>{t("backtest.chart.equity")}</h5>
        <div className="performance-chart__plot">
          <ol className="performance-chart__ticks" aria-hidden="true">
            {equityScale.ticks.map((value) => (
              <li key={value}>{compactMoney.format(value)}</li>
            ))}
          </ol>
          <svg
            className="performance-chart__equity"
            role="img"
            aria-label={equitySummary}
            preserveAspectRatio="none"
            viewBox={`0 0 ${WIDTH} ${EQUITY_HEIGHT}`}
          >
            <GridLines height={EQUITY_HEIGHT} />
            {hasBenchmark ? (
              <path
                className="performance-chart__benchmark"
                d={linePath(
                  equity.map((point, index) => [index, point.benchmark_equity] as const),
                  x,
                  equityScale.y,
                )}
              />
            ) : null}
            <path
              className="performance-chart__strategy"
              d={linePath(
                equity.map((point, index) => [index, point.equity] as const),
                x,
                equityScale.y,
              )}
            />
          </svg>
        </div>
      </div>
      <div className="performance-chart__panel">
        <h5>
          {t("backtest.chart.drawdown")}{" "}
          <small>{t("backtest.chart.drawdownMeaning")}</small>
        </h5>
        <div className="performance-chart__plot">
          <ol className="performance-chart__ticks" aria-hidden="true">
            {drawdownScale.ticks.map((value) => (
              <li key={value}>{percent(value)}</li>
            ))}
          </ol>
          <svg
            className="performance-chart__drawdown"
            role="img"
            aria-label={drawdownSummary}
            preserveAspectRatio="none"
            viewBox={`0 0 ${WIDTH} ${DRAWDOWN_HEIGHT}`}
          >
            <GridLines height={DRAWDOWN_HEIGHT} />
            <path
              className="performance-chart__loss"
              d={linePath(
                drawdown.map((point) => [position.get(point.session)!, point.drawdown] as const),
                x,
                drawdownScale.y,
              )}
            />
          </svg>
        </div>
      </div>
      <ol className="performance-chart__years" aria-hidden="true">
        {yearTicks(sessions).map((tick) => (
          <li key={tick.label} style={{ left: `${(tick.index / last) * 100}%` }}>
            {tick.label}
          </li>
        ))}
      </ol>
    </section>
  );
};
