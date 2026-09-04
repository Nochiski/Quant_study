import type {
  BacktestRunResult,
  MetricDefinition,
  MetricValue,
} from "../../../shared/api";

type ChartSeries = {
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

const LineChart = ({
  title,
  series,
}: {
  title: string;
  series: ChartSeries[];
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
        <p className="inline-state">이 구간에서 산출 가능한 값이 없습니다.</p>
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
        aria-label={`${title} chart`}
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

const formatMetric = (
  metric: MetricValue,
  definition: MetricDefinition,
): string => {
  if (metric.value === null) return "N/A";
  const precision = definition.precision ?? 4;
  if (definition.unit === "percent") {
    return `${(metric.value * 100).toFixed(Math.min(precision, 2))}%`;
  }
  if (definition.unit === "currency") {
    return `₩${metric.value.toLocaleString("ko-KR", {
      maximumFractionDigits: precision,
    })}`;
  }
  if (definition.unit === "count" || definition.unit === "sessions") {
    return metric.value.toFixed(0);
  }
  return metric.value.toFixed(precision);
};

const MetricCell = ({
  metric,
  definition,
}: {
  metric: MetricValue;
  definition: MetricDefinition;
}) => (
  <>
    <strong className={metric.value === null ? "is-unavailable" : ""}>
      {formatMetric(metric, definition)}
    </strong>
    {metric.value === null && (
      <small>{metric.unavailable_reason?.replaceAll("_", " ")}</small>
    )}
  </>
);

export const BacktestRunDetail = ({
  result,
}: {
  result: BacktestRunResult;
}) => {
  const definitions = new Map(
    result.metric_definitions.map((item) => [item.metric_id, item]),
  );
  const fullMetrics = new Map(
    result.metrics
      .filter((item) => item.scope === "full")
      .map((item) => [item.metric_id, item]),
  );
  const highlights = [
    "total_return",
    "sharpe",
    "max_drawdown",
    "calmar",
    "turnover",
    "trade_count",
  ];

  return (
    <article className="run-detail">
      <header className="run-detail__header">
        <div>
          <span className="section-kicker">PROFESSIONAL RESULT</span>
          <h3>백테스트 결과</h3>
          <p>
            {result.manifest.engine_core.toUpperCase()} core · registry{" "}
            <code>{result.manifest.metric_registry_version}</code>
          </p>
        </div>
        <span className="run-id">{result.manifest.run_id.slice(0, 12)}</span>
      </header>

      <section className="metric-highlights" aria-label="핵심 성과 지표">
        {highlights.map((metricId) => {
          const metric = fullMetrics.get(metricId);
          const definition = definitions.get(metricId);
          if (metric === undefined || definition === undefined) return null;
          return (
            <div key={metricId}>
              <span>{definition.label}</span>
              <MetricCell definition={definition} metric={metric} />
            </div>
          );
        })}
      </section>

      <div className="result-chart-grid">
        <LineChart
          series={[
            {
              label: "Strategy",
              color: "var(--chart-series-1)",
              values: result.series.equity.map((item) => item.equity),
            },
            {
              label: "Benchmark",
              color: "var(--chart-series-2)",
              values: result.series.equity.map((item) => item.benchmark_equity),
            },
          ]}
          title="Equity curve"
        />
        <LineChart
          series={[
            {
              label: "Drawdown",
              color: "var(--chart-series-3)",
              values: result.series.drawdown.map((item) => item.drawdown),
            },
          ]}
          title="Drawdown"
        />
        <LineChart
          series={[
            {
              label: "Rolling Sharpe",
              color: "var(--chart-series-4)",
              values: result.series.rolling_sharpe.map((item) => item.value),
            },
          ]}
          title="Rolling Sharpe"
        />
        <LineChart
          series={[
            {
              label: "Gross",
              color: "var(--chart-series-5)",
              values: result.artifacts.snapshots.map(
                (item) => item.gross_exposure,
              ),
            },
            {
              label: "Net",
              color: "var(--chart-series-6)",
              values: result.artifacts.snapshots.map(
                (item) => item.net_exposure,
              ),
            },
          ]}
          title="Exposure"
        />
      </div>

      <section className="monthly-panel">
        <header>
          <h4>Monthly returns</h4>
          <span>서버 산출 월별 성과</span>
        </header>
        <div className="monthly-grid">
          {result.series.monthly_returns.map((item) => (
            <div
              className={item.value >= 0 ? "is-positive" : "is-negative"}
              key={`${item.year}-${item.month}`}
            >
              <span>
                {item.year}.{String(item.month).padStart(2, "0")}
              </span>
              <strong>{(item.value * 100).toFixed(2)}%</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="raw-metric-panel">
        <header>
          <div>
            <h4>Raw metric table</h4>
            <p>
              None과 0을 구분하고 Full·IS·Validation·OOS scope를 보존합니다.
            </p>
          </div>
          <span>{result.metrics.length} values</span>
        </header>
        <div className="result-table-wrap">
          <table className="result-table">
            <thead>
              <tr>
                <th>Scope</th>
                <th>Metric</th>
                <th>Category</th>
                <th>Value</th>
                <th>Samples</th>
              </tr>
            </thead>
            <tbody>
              {result.metrics.map((metric, index) => {
                const definition = definitions.get(metric.metric_id);
                if (definition === undefined) return null;
                return (
                  <tr key={`${metric.scope}-${metric.metric_id}-${index}`}>
                    <td>
                      {metric.scope_label ?? metric.scope.replaceAll("_", " ")}
                    </td>
                    <td>
                      <code>{metric.metric_id}</code>
                    </td>
                    <td>{definition.category.replaceAll("_", " ")}</td>
                    <td>
                      <MetricCell definition={definition} metric={metric} />
                    </td>
                    <td>{metric.sample_count}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="trade-panel">
        <header>
          <h4>Closed trades</h4>
          <span>
            orders {result.artifacts.orders.length} · fills{" "}
            {result.artifacts.fills.length} · positions{" "}
            {result.artifacts.positions.length}
          </span>
        </header>
        {result.artifacts.trades.length === 0 ? (
          <p className="inline-state">
            이 실행에는 청산 완료된 거래가 없습니다.
          </p>
        ) : (
          <div className="result-table-wrap">
            <table className="result-table">
              <thead>
                <tr>
                  <th>Security</th>
                  <th>Side</th>
                  <th>Opened</th>
                  <th>Closed</th>
                  <th>Qty</th>
                  <th>P&amp;L</th>
                  <th>Fees</th>
                  <th>Slippage</th>
                </tr>
              </thead>
              <tbody>
                {result.artifacts.trades.map((trade, index) => (
                  <tr key={`${trade.security_id}-${trade.closed_on}-${index}`}>
                    <td>
                      <code>{trade.security_id}</code>
                    </td>
                    <td>{trade.side}</td>
                    <td>{trade.opened_on}</td>
                    <td>{trade.closed_on}</td>
                    <td>{trade.quantity}</td>
                    <td className={trade.pnl >= 0 ? "is-profit" : "is-loss"}>
                      {trade.pnl.toLocaleString("ko-KR", {
                        maximumFractionDigits: 0,
                      })}
                    </td>
                    <td>{trade.fees.toFixed(2)}</td>
                    <td>{trade.slippage_cost.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <details className="manifest-drawer">
        <summary>Manifest · 데이터 경고 · 재현성 정보</summary>
        <div className="manifest-grid">
          <dl>
            <div>
              <dt>Schema</dt>
              <dd>{result.manifest.schema_version}</dd>
            </div>
            <div>
              <dt>Engine</dt>
              <dd>{result.manifest.engine_version}</dd>
            </div>
            <div>
              <dt>Run fingerprint</dt>
              <dd title={result.manifest.run_fingerprint}>
                {result.manifest.run_fingerprint.slice(0, 16)}…
              </dd>
            </div>
            <div>
              <dt>Strategy</dt>
              <dd>{result.manifest.run_spec.strategy?.title ?? "—"}</dd>
            </div>
            <div>
              <dt>Strategy source</dt>
              <dd title={result.manifest.strategy_provenance.spec_hash}>
                {result.manifest.strategy_provenance.kind === "saved_revision"
                  ? `${result.manifest.strategy_provenance.strategy_id} r${result.manifest.strategy_provenance.revision}`
                  : "inline draft"}
              </dd>
            </div>
            <div>
              <dt>Strategy hash</dt>
              <dd title={result.manifest.strategy_hash}>
                {result.manifest.strategy_hash.slice(0, 16)}…
              </dd>
            </div>
            <div>
              <dt>TargetTape hash</dt>
              <dd title={result.manifest.target_tape_hash}>
                {result.manifest.target_tape_hash.slice(0, 16)}…
              </dd>
            </div>
            <div>
              <dt>Data snapshot</dt>
              <dd>{result.manifest.data_snapshot_id}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>
                {new Date(result.manifest.completed_at).toLocaleString("ko-KR")}
              </dd>
            </div>
          </dl>
          <div className="manifest-warnings">
            <h5>Data warnings</h5>
            {(result.manifest.warnings ?? []).length === 0 ? (
              <p>경고 없음</p>
            ) : (
              (result.manifest.warnings ?? []).map((warning) => (
                <p key={warning.code}>
                  <strong>{warning.code}</strong>
                  {warning.message}
                </p>
              ))
            )}
          </div>
        </div>
      </details>
    </article>
  );
};
