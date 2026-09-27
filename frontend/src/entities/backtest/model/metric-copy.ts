import { tDescription, tName } from "../../../shared/config";

/** 지표 하나의 쉬운 이름과 한 줄 뜻. */
export type MetricPlainCopy = { name: string; description: string };

/**
 * backend Metric Registry의 `metric_id`로 쉬운 이름·뜻을 찾는다(결과 설명 spec R4).
 *
 * 키 stem은 `backtest.metric.<metric_id>`다. 지표의 공식·방향·단위는 registry가, 로케일 문장은
 * i18n이 소유하므로 여기서는 두 키를 읽기만 한다. 문구가 없는 지표는 null이라 화면은 영어 이름만
 * 보인다 — 키 문자열을 본문으로 찍지 않는다.
 */
export const metricPlainCopy = (metricId: string): MetricPlainCopy | null => {
  const stem = `backtest.metric.${metricId}`;
  const name = tName(stem);
  const description = tDescription(stem);
  return name === null || description === null ? null : { name, description };
};
