import { t } from "../../../shared/config";
import "./line-chart.css";

/** 같은 x 축(세션 순서)을 나눠 쓰는 선 하나. 값이 없는 칸은 선을 끊는다. */
export type ChartSeries = {
  label: string;
  color: string;
  values: Array<number | null>;
};

const chartPath = (
  values: Array<number | null>,
  minimum: number,
  span: number,
) => {
  const width = 620;
  const height = 180;
  const last = Math.max(values.length - 1, 1);
  return values
    .map((value, index) => {
      if (value === null) return null;
      const x = (index / last) * width;
      const y = height - ((value - minimum) / span) * height;
      const previous = values[index - 1];
      return `${index === 0 || previous === null ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .filter((value): value is string => value !== null)
    .join(" ");
};

/**
 * 결과 화면의 선 차트. 값은 서버가 준 곡선을 그대로 그리고 다시 계산하지 않는다(축 범위만 정한다).
 * 결과 상세와 검증 카드의 표본 밖 곡선이 같이 쓴다.
 */
export const LineChart = ({
  title,
  series,
  emptyText = t("backtest.result.chartEmpty"),
}: {
  title: string;
  series: ChartSeries[];
  emptyText?: string;
}) => {
  const finite = series.flatMap((item) =>
    item.values.filter((value): value is number => value !== null),
  );
  if (finite.length === 0) {
    return (
      <section className="result-chart">
        <header>
          <h4>{title}</h4>
        </header>
        <p className="inline-state">{emptyText}</p>
      </section>
    );
  }
  const minimum = Math.min(...finite);
  const maximum = Math.max(...finite);
  const span = Math.max(maximum - minimum, Math.abs(maximum) * 0.01, 1e-9);

  return (
    <section className="result-chart">
      <header>
        <h4>{title}</h4>
        <div className="chart-legend">
          {series.map((item) => (
            <span key={item.label} style={{ color: item.color }}>
              {item.label}
            </span>
          ))}
        </div>
      </header>
      <svg
        aria-label={`${title} ${t("backtest.result.chart")}`}
        preserveAspectRatio="none"
        role="img"
        viewBox="0 0 620 180"
      >
        <line x1="0" x2="620" y1="179" y2="179" />
        <line x1="0" x2="620" y1="90" y2="90" />
        {series.map((item) => (
          <path
            d={chartPath(item.values, minimum, span)}
            key={item.label}
            style={{ stroke: item.color }}
          />
        ))}
      </svg>
      <footer>
        <span>
          {minimum.toLocaleString(undefined, { maximumFractionDigits: 3 })}
        </span>
        <span>
          {maximum.toLocaleString(undefined, { maximumFractionDigits: 3 })}
        </span>
      </footer>
    </section>
  );
};
