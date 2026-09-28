import { tDescription, tName, tOptional } from "../../../shared/config";

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

/**
 * 지표 사용 불가 사유(`MetricValue.unavailable_reason`)의 로케일 문구(이슈 #241).
 *
 * 키는 `backtest.metricUnavailable.<reason>`이고 사유 목록은 backend `MetricUnavailableReason`이
 * 소유한다. 문구가 없는 새 사유는 원문의 밑줄만 공백으로 바꿔 보인다 — 칸이 비지 않게 한다.
 */
export const metricUnavailableCopy = (reason: string): string =>
  tOptional(`backtest.metricUnavailable.${reason}`) ??
  reason.replaceAll("_", " ");

/**
 * 사용 불가 사유마다 그 이유를 적는 데이터 경고 코드. 실행에 이 경고가 있으면 지표 칸에서 그
 * 경고로 이어 준다. 벤치마크 지표는 창 시작이 첫 가격보다 앞설 때 비고, 그 이유는
 * `benchmark.no_bar_at_start`가 적는다(#229·#241).
 */
export const EXPLAINING_WARNING_CODES: Readonly<
  Record<string, readonly string[]>
> = {
  benchmark_not_available: ["benchmark.no_bar_at_start"],
};
