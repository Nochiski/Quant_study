import type { MetricDefinition, MetricValue } from "../../../shared/api";
import { t } from "../../../shared/config";
import { SignedValue } from "../../../shared/ui";
import { metricUnavailableCopy } from "../model/metric-copy";
import { formatMetric } from "../model/metric-format";
import "./backtest-run-detail.css";

/** 사용 불가 지표 칸에서 이유를 적은 데이터 경고로 가는 연결. */
export type MetricExplanation = { href: string; open: () => void };

export const MetricCell = ({
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
 * 방향이 있는 수익 지표(수익률·낙폭)를 부호·색과 함께 보인다. 값이 없으면 `MetricCell` 과 같이 사유를
 * 보인다. 어떤 지표에 쓸지는 호출자가 정한다.
 */
export const SignedMetricCell = ({
  metric,
  definition,
  explanation = null,
}: {
  metric: MetricValue;
  definition: MetricDefinition;
  explanation?: MetricExplanation | null;
}) =>
  metric.value === null ? (
    <MetricCell definition={definition} explanation={explanation} metric={metric} />
  ) : (
    <strong>
      <SignedValue
        value={metric.value}
        magnitude={formatMetric({ ...metric, value: Math.abs(metric.value) }, definition)}
      />
    </strong>
  );

/** 지표 계산은 서버에 두고 기존 단위·사용 불가 사유 표시를 함께 재사용한다. */
export const BacktestMetricTable = ({
  definitions: items,
  metrics,
  id,
  explanationFor = () => null,
}: {
  definitions: MetricDefinition[];
  metrics: MetricValue[];
  id?: string;
  explanationFor?: (metric: MetricValue) => MetricExplanation | null;
}) => {
  const definitions = new Map(items.map((item) => [item.metric_id, item]));
  return (
    <section className="raw-metric-panel">
      <header>
        <div>
          <h4>{t("backtest.result.metrics")}</h4>
          <p>{t("backtest.result.metrics.description")}</p>
        </div>
        <span>
          {metrics.length} {t("backtest.result.values")}
        </span>
      </header>
      <div
        className="result-table-wrap"
        id={id}
        role="region"
        tabIndex={0}
        aria-label={t("backtest.result.metrics")}
      >
        <table className="result-table">
          <caption className="sr-only">{t("backtest.result.metrics")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("backtest.result.column.scope")}</th>
              <th scope="col">{t("backtest.result.column.metric")}</th>
              <th scope="col">{t("backtest.result.column.category")}</th>
              <th scope="col">{t("backtest.result.column.value")}</th>
              <th scope="col">{t("backtest.result.column.samples")}</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric, index) => {
              const definition = definitions.get(metric.metric_id);
              if (definition === undefined) return null;
              return (
                <tr key={`${metric.scope}-${metric.metric_id}-${index}`}>
                  <td>
                    {metric.scope_label ?? t(`backtest.scope.${metric.scope}`)}
                  </td>
                  <td>
                    <code>{metric.metric_id}</code>
                  </td>
                  <td>{t(`backtest.category.${definition.category}`)}</td>
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
  );
};
