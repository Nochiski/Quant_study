import { BacktestMetricTable, MetricCell, type MetricExplanation } from "./backtest-metrics";
import { BacktestOrderEvents } from "./backtest-order-events";
import type {
  BacktestRunResult,
  MetricValue,
} from "../../../shared/api";
import { t, tOptional } from "../../../shared/config";
import { type ReactNode, useId, useRef } from "react";
import {
  EXPLAINING_WARNING_CODES,
  HIGHLIGHTED_METRIC_IDS,
  metricPlainCopy,
} from "../model/metric-copy";
import {
  runEnvironmentApplies,
  runEnvironmentLabel,
  runEnvironmentValueLabel,
  type RunEnvironmentField,
} from "../model/run-environment-fields";
import "./backtest-run-detail.css";
import { LineChart } from "./line-chart";

/**
 * 실행된 실행 설정의 행. 칸 목록·순서·이름·단위·enum 값 이름은 실행 설정 스키마에서 읽는다
 * (DEFECT-242-04). 스키마에 없는 기록 키(스키마를 아직 못 읽었거나, 그 뒤 스키마가 칸을 빼거나 이름을
 * 바꿨다)는 그 뒤에 키와 값을 그대로 붙인다 — run 기록은 실행 설정의 유일한 사본이다(#251).
 */
const environmentRows = (
  environment: BacktestRunResult["manifest"]["environment"],
  fields: readonly RunEnvironmentField[] | null,
): { key: string; label: string; value: string }[] => {
  const record = environment as unknown as Record<string, unknown>;
  const known = new Set(fields?.map((field) => field.name));
  return [
    ...(fields ?? []).map((field) => ({
      key: field.name,
      label: runEnvironmentLabel(field),
      value: runEnvironmentApplies(field, record)
        ? runEnvironmentValueLabel(field, record[field.name])
        : t("backtest.result.environment.notApplied"),
    })),
    ...Object.entries(record)
      .filter(([key]) => !known.has(key))
      .map(([key, value]) => ({
        key,
        label: key,
        value: value === null || value === undefined ? "—" : String(value),
      })),
  ];
};

export const BacktestRunDetail = ({
  result,
  environmentFields = null,
  accountReport,
  validationReport,
}: {
  result: BacktestRunResult;
  accountReport?: ReactNode;
  /** 현재 계열 검증 조회는 page가 소유하며 원본 결과와 별도 영역에 조합한다. */
  validationReport?: ReactNode;
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
  return (
    <article className="run-detail" aria-labelledby={titleId}>
      <header className="run-detail__header">
        <div>
          <span className="section-kicker">{t("backtest.result.kicker")}</span>
          <h3 id={titleId}>{t("backtest.result.title")}</h3>
          <p>
            {result.manifest.run_spec.core?.toUpperCase() ?? "—"} ·{" "}
            {t("backtest.result.registry")}{" "}
            <code>{result.manifest.metric_registry_version}</code>
          </p>
        </div>
        <span className="run-id">{result.manifest.run_id.slice(0, 12)}</span>
      </header>

      <nav
        className="run-detail__navigation"
        aria-label={t("backtest.result.navigation")}
      >
        {validationReport ? (
          <a href={`#${titleId}-validation`}>{t("backtest.validation.title")}</a>
        ) : null}
        {accountReport ? (
          <a href={`#${titleId}-account`}>{t("backtest.account.title")}</a>
        ) : null}
        <a href={`#${titleId}-highlights`}>{t("backtest.result.highlights")}</a>
        <a href={`#${titleId}-charts`}>{t("backtest.result.charts")}</a>
        <a href={`#${titleId}-metrics`}>{t("backtest.result.metrics")}</a>
        <a href={`#${titleId}-trades`}>{t("backtest.result.trades")}</a>
        <a
          href={`#${titleId}-manifest`}
          onClick={() => {
            if (drawer.current) drawer.current.open = true;
          }}
        >
          {t("backtest.result.manifest")}
        </a>
      </nav>

      {validationReport ? (
        <section
          id={`${titleId}-validation`}
          tabIndex={-1}
          aria-label={t("backtest.validation.title")}
        >
          {validationReport}
        </section>
      ) : null}

      {accountReport ? (
        <section
          id={`${titleId}-account`}
          tabIndex={-1}
          aria-label={t("backtest.account.title")}
        >
          {accountReport}
        </section>
      ) : null}

      <section
        id={`${titleId}-highlights`}
        tabIndex={-1}
        className="metric-highlights"
        aria-label={t("backtest.result.highlights")}
      >
        {HIGHLIGHTED_METRIC_IDS.map((metricId) => {
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

      <section
        className="result-chart-grid"
        id={`${titleId}-charts`}
        tabIndex={-1}
        aria-label={t("backtest.result.charts")}
      >
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
          // 창보다 짧은 실행만 창 길이로 이유를 말한다. 흔들림 0 등 다른 이유면 일반 문구다(#303).
          emptyText={
            result.series.rolling_sharpe_window_sessions != null &&
            result.series.rolling_sharpe.length <=
              result.series.rolling_sharpe_window_sessions
              ? t("backtest.result.chartEmpty.rollingSharpe").replace(
                  "{sessions}",
                  String(result.series.rolling_sharpe_window_sessions),
                )
              : undefined
          }
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
      </section>

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

      <BacktestMetricTable
        definitions={result.metric_definitions}
        metrics={result.metrics}
        id={`${titleId}-metrics`}
        explanationFor={explanationFor}
      />

      <section className="trade-panel" id={`${titleId}-trades`} tabIndex={-1}>
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
          <div
            className="result-table-wrap"
            role="region"
            tabIndex={0}
            aria-label={t("backtest.result.trades")}
          >
            <table className="result-table">
              <caption className="sr-only">
                {t("backtest.result.trades")}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("backtest.result.column.security")}</th>
                  <th scope="col">{t("backtest.result.column.side")}</th>
                  <th scope="col">{t("backtest.result.column.opened")}</th>
                  <th scope="col">{t("backtest.result.column.closed")}</th>
                  <th scope="col">{t("backtest.result.column.quantity")}</th>
                  <th scope="col">{t("backtest.result.column.pnl")}</th>
                  <th scope="col">{t("backtest.result.column.fees")}</th>
                  <th scope="col">{t("backtest.result.column.slippage")}</th>
                </tr>
              </thead>
              <tbody>
                {result.artifacts.trades.map((trade, index) => (
                  <tr key={`${trade.security_id}-${trade.closed_on}-${index}`}>
                    <td>
                      <code>{trade.security_id}</code>
                    </td>
                    <td>
                      {tOptional(`backtest.side.${trade.side}`) ?? trade.side}
                    </td>
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

      <BacktestOrderEvents artifacts={result.artifacts} />

      <details
        id={`${titleId}-manifest`}
        tabIndex={-1}
        className="manifest-drawer ui-disclosure"
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
                <dd>{result.manifest.run_spec.core?.toUpperCase() ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.initialCash")}</dt>
                <dd>
                  {result.manifest.run_spec.initial_cash?.toLocaleString(
                    "ko-KR",
                  ) ?? "—"}
                </dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.benchmark")}</dt>
                <dd>{result.manifest.run_spec.benchmark_security_id ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.annualizationDays")}</dt>
                <dd>{result.manifest.run_spec.annualization_days ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.result.manifest.metricWindows")}</dt>
                <dd>
                  {result.manifest.run_spec.metric_windows?.length
                    ? result.manifest.run_spec.metric_windows
                        .map(
                          (window) =>
                            `${t(`backtest.scope.${window.scope}`)}: ${window.start} → ${window.end}`,
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
