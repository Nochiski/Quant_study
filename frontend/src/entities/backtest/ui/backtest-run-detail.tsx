import type {
  BacktestRunResult,
  MetricDefinition,
  MetricValue,
} from "../../../shared/api";
import { t, tOptional } from "../../../shared/config";
import { useId, useRef } from "react";
import {
  EXPLAINING_WARNING_CODES,
  metricPlainCopy,
  metricUnavailableCopy,
} from "../model/metric-copy";
import {
  runEnvironmentLabel,
  runEnvironmentValueLabel,
  type RunEnvironmentField,
} from "../model/run-environment-fields";
import "./backtest-run-detail.css";

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

/** 사용 불가 지표 칸에서 이유를 적은 데이터 경고로 가는 연결. */
type MetricExplanation = { href: string; open: () => void };

const MetricCell = ({
  metric,
  definition,
  explanation = null,
}: {
  metric: MetricValue;
  definition: MetricDefinition;
  explanation?: MetricExplanation | null;
}) => (
  <>
    <strong className={metric.value === null ? "is-unavailable" : ""}>
      {formatMetric(metric, definition)}
    </strong>
    {metric.value === null && metric.unavailable_reason && (
      <small>
        {metricUnavailableCopy(metric.unavailable_reason)}
        {explanation === null ? null : (
          <>
            {" "}
            {/* 접힌 manifest 를 먼저 펼친 뒤 기본 이동으로 그 경고까지 스크롤한다. */}
            <a href={explanation.href} onClick={explanation.open}>
              {t("backtest.result.metricUnavailable.explain")}
            </a>
          </>
        )}
      </small>
    )}
  </>
);

/**
 * 실행된 실행 설정의 행. 칸 목록·순서·이름·단위·enum 값 이름은 실행 설정 스키마에서 읽는다
 * (DEFECT-242-04). 스키마를 아직 못 읽었으면 기록된 키와 값을 그대로 보인다.
 */
const environmentRows = (
  environment: BacktestRunResult["manifest"]["environment"],
  fields: readonly RunEnvironmentField[] | null,
): { key: string; label: string; value: string }[] => {
  const record = environment as unknown as Record<string, unknown>;
  if (fields === null)
    return Object.entries(record).map(([key, value]) => ({
      key,
      label: key,
      value: value === null || value === undefined ? "—" : String(value),
    }));
  return fields.map((field) => ({
    key: field.name,
    label: runEnvironmentLabel(field),
    value: runEnvironmentValueLabel(field, record[field.name]),
  }));
};

export const BacktestRunDetail = ({
  result,
  environmentFields = null,
}: {
  result: BacktestRunResult;
  /** 실행 설정 스키마의 칸. page 가 스키마 query 에서 넘긴다. 없으면 기록된 키 그대로 보인다. */
  environmentFields?: readonly RunEnvironmentField[] | null;
}) => {
  const titleId = useId();
  const drawer = useRef<HTMLDetailsElement>(null);
  const warnings = result.manifest.warnings ?? [];
  const warningId = (index: number) => `${titleId}-warning-${index}`;
  // 사용 불가 사유를 적은 경고로 가는 연결(이슈 #241). 이유 경고가 없으면 사유 문구만 보인다.
  const explanationFor = (metric: MetricValue): MetricExplanation | null => {
    if (metric.value !== null || !metric.unavailable_reason) return null;
    const codes = EXPLAINING_WARNING_CODES[metric.unavailable_reason] ?? [];
    const index = warnings.findIndex((warning) => codes.includes(warning.code));
    if (index < 0) return null;
    return {
      href: `#${warningId(index)}`,
      open: () => {
        if (drawer.current !== null) drawer.current.open = true;
      },
    };
  };
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
    "sharpe_standard_error",
    "max_drawdown",
    "calmar",
    "turnover",
    "trade_count",
  ];

  return (
    <article className="run-detail" aria-labelledby={titleId}>
      <header className="run-detail__header">
        <div>
          <span className="section-kicker">{t("backtest.result.kicker")}</span>
          <h3 id={titleId}>{t("backtest.result.title")}</h3>
          <p>
            {result.manifest.engine_core.toUpperCase()} core · registry{" "}
            <code>{result.manifest.metric_registry_version}</code>
          </p>
        </div>
        <span className="run-id">{result.manifest.run_id.slice(0, 12)}</span>
      </header>

      <section
        className="metric-highlights"
        aria-label={t("backtest.result.highlights")}
      >
        {highlights.map((metricId) => {
          const metric = fullMetrics.get(metricId);
          const definition = definitions.get(metricId);
          if (metric === undefined || definition === undefined) return null;
          // 영어 이름은 registry `label` 그대로, 쉬운 이름·뜻은 i18n이 소유한다(결과 설명 spec R4).
          const plain = metricPlainCopy(metricId);
          return (
            <div key={metricId}>
              <span>{definition.label}</span>
              <MetricCell
                definition={definition}
                explanation={explanationFor(metric)}
                metric={metric}
              />
              {plain === null ? null : (
                <p className="metric-highlights__plain">
                  <dfn>{plain.name}</dfn> {plain.description}
                </p>
              )}
            </div>
          );
        })}
      </section>

      <div className="result-chart-grid">
        <LineChart
          series={[
            {
              label: t("backtest.result.series.strategy"),
              color: "var(--chart-series-1)",
              values: result.series.equity.map((item) => item.equity),
            },
            {
              label: t("backtest.result.series.benchmark"),
              color: "var(--chart-series-2)",
              values: result.series.equity.map((item) => item.benchmark_equity),
            },
          ]}
          title={t("backtest.result.chart.equity")}
        />
        <LineChart
          series={[
            {
              label: t("backtest.result.series.drawdown"),
              color: "var(--chart-series-3)",
              values: result.series.drawdown.map((item) => item.drawdown),
            },
          ]}
          title={t("backtest.result.chart.drawdown")}
        />
        <LineChart
          series={[
            {
              label: t("backtest.result.series.rollingSharpe"),
              color: "var(--chart-series-4)",
              values: result.series.rolling_sharpe.map((item) => item.value),
            },
          ]}
          title={t("backtest.result.chart.rollingSharpe")}
          emptyText={t("backtest.result.chartEmpty.rollingSharpe").replace(
            "{sessions}",
            String(result.series.rolling_sharpe_window_sessions),
          )}
        />
        <LineChart
          series={[
            {
              label: t("backtest.result.series.gross"),
              color: "var(--chart-series-5)",
              values: result.artifacts.snapshots.map(
                (item) => item.gross_exposure,
              ),
            },
            {
              label: t("backtest.result.series.net"),
              color: "var(--chart-series-6)",
              values: result.artifacts.snapshots.map(
                (item) => item.net_exposure,
              ),
            },
          ]}
          title={t("backtest.result.chart.exposure")}
        />
      </div>

      <section className="monthly-panel">
        <header>
          <h4>{t("backtest.result.monthly")}</h4>
          <span>{t("backtest.result.monthly.description")}</span>
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
            <h4>{t("backtest.result.metrics")}</h4>
            <p>{t("backtest.result.metrics.description")}</p>
          </div>
          <span>
            {result.metrics.length} {t("backtest.result.values")}
          </span>
        </header>
        <div className="result-table-wrap">
          <table className="result-table">
            <thead>
              <tr>
                <th>{t("backtest.result.column.scope")}</th>
                <th>{t("backtest.result.column.metric")}</th>
                <th>{t("backtest.result.column.category")}</th>
                <th>{t("backtest.result.column.value")}</th>
                <th>{t("backtest.result.column.samples")}</th>
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
                      <MetricCell
                        definition={definition}
                        explanation={explanationFor(metric)}
                        metric={metric}
                      />
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
          <h4>{t("backtest.result.trades")}</h4>
          <span>
            {t("backtest.result.orders")} {result.artifacts.orders.length} ·{" "}
            {t("backtest.result.fills")} {result.artifacts.fills.length} ·{" "}
            {t("backtest.result.positions")} {result.artifacts.positions.length}
          </span>
        </header>
        {result.artifacts.trades.length === 0 ? (
          <p className="inline-state">{t("backtest.result.trades.empty")}</p>
        ) : (
          <div className="result-table-wrap">
            <table className="result-table">
              <thead>
                <tr>
                  <th>{t("backtest.result.column.security")}</th>
                  <th>{t("backtest.result.column.side")}</th>
                  <th>{t("backtest.result.column.opened")}</th>
                  <th>{t("backtest.result.column.closed")}</th>
                  <th>{t("backtest.result.column.quantity")}</th>
                  <th>{t("backtest.result.column.pnl")}</th>
                  <th>{t("backtest.result.column.fees")}</th>
                  <th>{t("backtest.result.column.slippage")}</th>
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

      <details
        className="manifest-drawer"
        aria-label={t("backtest.result.manifest")}
        ref={drawer}
      >
        <summary>{t("backtest.result.manifest")}</summary>
        <div className="manifest-grid">
          <div className="manifest-records">
            <dl>
              <div>
                <dt>{t("backtest.result.manifest.schema")}</dt>
                <dd>{result.manifest.schema_version}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.engine")}</dt>
                <dd>{result.manifest.engine_version}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.core")}</dt>
                <dd>{result.manifest.engine_core.toUpperCase()}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.initialCash")}</dt>
                <dd>{result.manifest.initial_cash.toLocaleString("ko-KR")}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.benchmark")}</dt>
                <dd>{result.manifest.run_spec.benchmark_security_id ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.annualizationDays")}</dt>
                <dd>{result.manifest.annualization_days}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.metricWindows")}</dt>
                <dd>
                  {result.manifest.run_spec.metric_windows?.length
                    ? result.manifest.run_spec.metric_windows
                        .map(
                          (window) =>
                            `${window.scope}: ${window.start} → ${window.end}`,
                        )
                        .join(" · ")
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.fingerprint")}</dt>
                <dd title={result.manifest.run_fingerprint}>
                  {result.manifest.run_fingerprint.slice(0, 16)}…
                </dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.strategy")}</dt>
                <dd>{result.manifest.run_spec.strategy?.title ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.source")}</dt>
                <dd title={result.manifest.strategy_provenance.spec_hash}>
                  {result.manifest.strategy_provenance.kind === "saved_revision"
                    ? `${result.manifest.strategy_provenance.strategy_id} r${result.manifest.strategy_provenance.revision}`
                    : t("backtest.result.manifest.inline")}
                </dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.strategyHash")}</dt>
                <dd title={result.manifest.strategy_hash}>
                  {result.manifest.strategy_hash.slice(0, 16)}…
                </dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.targetHash")}</dt>
                <dd title={result.manifest.target_tape_hash}>
                  {result.manifest.target_tape_hash.slice(0, 16)}…
                </dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.snapshot")}</dt>
                <dd>{result.manifest.data_snapshot_id}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.completed")}</dt>
                <dd>
                  {new Date(result.manifest.completed_at).toLocaleString(
                    "ko-KR",
                  )}
                </dd>
              </div>
            </dl>
            {/* 실행 설정은 1.2 부터 전략 문서 밖에 있고 이 기록이 그 값의 유일한 사본이다(Phase 2 감사
              #16). 같은 전략을 다른 기간으로 돌리면 strategy hash 는 같고 environment hash 만 갈린다. */}
            <div
              className="manifest-environment"
              role="group"
              aria-label={t("backtest.result.manifest.environment")}
            >
              <h5>{t("backtest.result.manifest.environment")}</h5>
              <dl>
                {environmentRows(
                  result.manifest.environment,
                  environmentFields,
                ).map((row) => (
                  <div key={row.key}>
                    <dt>{row.label}</dt>
                    <dd title={row.value}>{row.value}</dd>
                  </div>
                ))}
                <div>
                  <dt>{t("backtest.result.manifest.environment.hash")}</dt>
                  <dd title={result.manifest.environment_hash}>
                    {result.manifest.environment_hash.slice(0, 16)}…
                  </dd>
                </div>
              </dl>
            </div>
          </div>
          <div className="manifest-warnings">
            <h5>{t("backtest.result.warnings")}</h5>
            {warnings.length === 0 ? (
              <p>{t("backtest.result.warnings.empty")}</p>
            ) : (
              warnings.map((warning, index) => {
                // 제목은 경고 코드로 고른다. 문장(message)은 서버가 완성한 진단이라 그대로 두고, 제목이
                // 없는 새 코드는 코드를 제목으로 보인다 — 서버가 코드를 늘려도 화면이 비지 않는다.
                const title = tOptional(`backtest.warning.${warning.code}`);
                return (
                  <p id={warningId(index)} key={`${warning.code}:${index}`}>
                    <strong>{title ?? warning.code}</strong>
                    <span>{warning.message}</span>
                    {title === null ? null : (
                      <code className="manifest-warnings__code">
                        {warning.code}
                      </code>
                    )}
                  </p>
                );
              })
            )}
          </div>
        </div>
      </details>
    </article>
  );
};
