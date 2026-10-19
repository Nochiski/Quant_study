import type { ReactNode } from "react";

import type { AccountReport, MetricDefinition, MetricValue } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { SignedValue } from "../../../shared/ui";
import {
  metricPlainCopy,
  metricUnavailableCopy,
  type ResultLayoutMetricId,
} from "../model/metric-copy";
import { formatMetric } from "../model/metric-format";
import {
  MetricCell,
  SignedMetricCell,
  type MetricExplanation,
} from "./backtest-metrics";

/** 결과 화면 각 구역이 같은 전체 구간(FULL) 지표를 읽는 창구. 값은 서버 응답 그대로다. */
export type ResultMetrics = {
  metric: (metricId: ResultLayoutMetricId) => MetricValue | undefined;
  definition: (metricId: ResultLayoutMetricId) => MetricDefinition | undefined;
  explanationFor: (metric: MetricValue) => MetricExplanation | null;
};

const money = (value: number) =>
  `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}`;

/** 지표 카드 하나. 영어 이름은 registry `label`, 쉬운 이름·뜻은 i18n 이 소유한다(결과 설명 spec R4). */
const SummaryCard = ({
  metricId,
  metrics,
  signed,
  note,
  alsoExplains = [],
}: {
  metricId: ResultLayoutMetricId;
  metrics: ResultMetrics;
  signed: boolean;
  note?: ReactNode;
  /** 곁들여 보이는 지표(예: 샤프 옆의 표준오차)의 쉬운 이름·뜻도 같은 카드에 둔다. */
  alsoExplains?: readonly ResultLayoutMetricId[];
}) => {
  const metric = metrics.metric(metricId);
  const definition = metrics.definition(metricId);
  if (metric === undefined || definition === undefined) return null;
  const plain = metricPlainCopy(metricId);
  const Cell = signed ? SignedMetricCell : MetricCell;
  return (
    <div>
      <span>{definition.label}</span>
      <Cell
        definition={definition}
        explanation={metrics.explanationFor(metric)}
        metric={metric}
      />
      {note ? <p className="metric-highlights__note">{note}</p> : null}
      {[plain, ...alsoExplains.map(metricPlainCopy)].map((copy, index) =>
        copy === null ? null : (
          <p className="metric-highlights__plain" key={index}>
            <dfn>{copy.name}</dfn> {copy.description}
          </p>
        ),
      )}
    </div>
  );
};

/**
 * "결과 한눈에"(RD-07-A). 총수익률·벤치마크 대비·샤프·최대 낙폭·최종 평가액을 맨 위에 둔다.
 * 수익 방향이 있는 값만 부호·색을 붙이고, 곁들이는 값(벤치마크·오차·회복 기간)도 서버 지표를 그대로 쓴다.
 */
export const ResultSummary = ({
  id,
  metrics,
  account,
}: {
  id: string;
  metrics: ResultMetrics;
  /** 계좌 보고서의 요약. 아직 없거나 계산할 수 없으면 최종 평가액 카드를 두지 않는다. */
  account: AccountReport["summary"] | undefined;
}) => {
  const benchmark = metrics.metric("benchmark_return");
  const benchmarkDefinition = metrics.definition("benchmark_return");
  const error = metrics.metric("sharpe_standard_error");
  const errorDefinition = metrics.definition("sharpe_standard_error");
  const recovery = metrics.metric("max_drawdown_recovery_sessions");
  return (
    <section
      id={id}
      tabIndex={-1}
      className="result-summary"
      aria-label={t("backtest.summary.title")}
    >
      <h4>{t("backtest.summary.title")}</h4>
      <div className="metric-highlights">
        <SummaryCard
          metricId="total_return"
          metrics={metrics}
          signed
          note={
            benchmark?.value != null && benchmarkDefinition ? (
              <>
                {t("backtest.summary.benchmark")}{" "}
                <SignedValue
                  value={benchmark.value}
                  magnitude={formatMetric(
                    { ...benchmark, value: Math.abs(benchmark.value) },
                    benchmarkDefinition,
                  )}
                />
              </>
            ) : null
          }
        />
        <SummaryCard metricId="excess_return" metrics={metrics} signed />
        <SummaryCard
          metricId="sharpe"
          metrics={metrics}
          signed={false}
          alsoExplains={["sharpe_standard_error"]}
          note={
            error?.value != null && errorDefinition
              ? tFill("backtest.summary.sharpeError", {
                  value: formatMetric(error, errorDefinition),
                })
              : null
          }
        />
        <SummaryCard
          metricId="max_drawdown"
          metrics={metrics}
          signed
          note={
            recovery === undefined
              ? null
              : recovery.value !== null
                ? tFill("backtest.summary.recovery", {
                    sessions: recovery.value.toFixed(0),
                  })
                : recovery.unavailable_reason
                  ? metricUnavailableCopy(recovery.unavailable_reason)
                  : null
          }
        />
        {account ? (
          <div>
            <span>{t("backtest.summary.finalEquity")}</span>
            <strong>{money(account.final_equity)}</strong>
            <p className="metric-highlights__note">
              {tFill("backtest.summary.finalEquityNote", {
                initial: money(account.initial_cash),
              })}
            </p>
          </div>
        ) : null}
      </div>
    </section>
  );
};
