import { useId, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { metricPlainCopy, type ResultLayoutMetricId } from "../model/metric-copy";
import { MetricCell } from "./backtest-metrics";
import type { ResultMetrics } from "./result-summary";

/** 카드 안 지표 한 줄. 이름은 쉬운 이름이 있으면 그것을, 없으면 registry `label` 을 쓴다. */
const MetricRow = ({
  metricId,
  metrics,
  definitionText,
}: {
  metricId: ResultLayoutMetricId;
  metrics: ResultMetrics;
  definitionText?: string;
}) => {
  const metric = metrics.metric(metricId);
  const definition = metrics.definition(metricId);
  if (metric === undefined || definition === undefined) return null;
  return (
    <div>
      <dt>
        {metricPlainCopy(metricId)?.name ?? definition.label}
        {definitionText ? <small>{definitionText}</small> : null}
      </dt>
      <dd>
        <MetricCell
          definition={definition}
          explanation={metrics.explanationFor(metric)}
          metric={metric}
        />
      </dd>
    </div>
  );
};

const AxisCard = ({
  kicker,
  title,
  note,
  children,
}: {
  kicker: string;
  title: string;
  note: string;
  children: ReactNode;
}) => {
  const titleId = useId();
  return (
    <section className="axis-card" aria-labelledby={titleId}>
      <span className="section-kicker">{kicker}</span>
      <h5 id={titleId}>{title}</h5>
      {children}
      <p className="axis-card__note">{note}</p>
    </section>
  );
};

/**
 * "종목 선정이 효과가 있었나"와 "손익 구조는 어땠나"를 같은 크기로 나란히 둔다(RD-07-C). 두 카드는
 * 지표 레지스트리 값만 꺼내 보이고, 손익 구조는 청산 거래의 실현 통계라는 한계를 함께 적는다.
 */
export const ResultAxes = ({
  id,
  metrics,
  regression,
}: {
  id: string;
  metrics: ResultMetrics;
  /** 팩터 회귀 패널. 버튼을 누를 때 조회한다. */
  regression?: ReactNode;
}) => (
  <section
    id={id}
    tabIndex={-1}
    className="result-axes"
    aria-label={t("backtest.axes.title")}
  >
    <AxisCard
      kicker={t("backtest.axes.selection.kicker")}
      title={t("backtest.axes.selection.title")}
      note={t("backtest.axes.selection.note")}
    >
      <dl className="axis-card__metrics">
        <MetricRow metricId="turnover" metrics={metrics} />
        <MetricRow metricId="average_gross_exposure" metrics={metrics} />
      </dl>
      {regression}
    </AxisCard>
    <AxisCard
      kicker={t("backtest.axes.pnl.kicker")}
      title={t("backtest.axes.pnl.title")}
      note={t("backtest.axes.pnl.note")}
    >
      <dl className="axis-card__metrics">
        <MetricRow metricId="win_rate" metrics={metrics} />
        <MetricRow
          metricId="profit_factor"
          metrics={metrics}
          definitionText={t("backtest.axes.pnl.profitFactor")}
        />
        <MetricRow metricId="trade_count" metrics={metrics} />
        <MetricRow metricId="total_fees" metrics={metrics} />
        <MetricRow metricId="total_taxes" metrics={metrics} />
        <MetricRow metricId="total_slippage_cost" metrics={metrics} />
      </dl>
    </AxisCard>
  </section>
);
