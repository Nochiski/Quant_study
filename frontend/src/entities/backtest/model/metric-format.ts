import type { MetricDefinition, MetricValue } from "../../../shared/api";
import { t } from "../../../shared/config";

/** registry 단위·자릿수대로 값을 적는다. 값은 서버가 계산한 그대로다. */
export const formatMetric = (
  metric: MetricValue,
  definition: MetricDefinition,
): string => {
  if (metric.value === null) return t("backtest.result.unavailable");
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
