import type { MetricValue } from "../../../shared/api";
import { tCode, tDescription, tName } from "../../../shared/config";

/** 지표 하나의 쉬운 이름과 한 줄 뜻. */
export type MetricPlainCopy = { name: string; description: string };

/**
 * 결과 화면 맨 위에 강조하는 지표(registry `metric_id`, 화면 순서). registry 에 없는 id 는 강조 칸에서
 * 조용히 빠지므로 backend 골든(`metric_ids.json`)의 부분집합임을 테스트가 본다(#362 DR-B-09).
 */
export const HIGHLIGHTED_METRIC_IDS = [
  "total_return",
  "sharpe",
  "sharpe_standard_error",
  "max_drawdown",
  "calmar",
  "turnover",
  "trade_count",
] as const;

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
 * 사유 목록은 backend `MetricUnavailableReason`이 소유하고 생성 SDK의 유니온으로 온다. 문구 키
 * `backtest.metricUnavailable.<reason>`의 누락은 `tCode`가 typecheck에서 막는다(#293).
 */
export const metricUnavailableCopy = (
  reason: NonNullable<MetricValue["unavailable_reason"]>,
): string => tCode(`backtest.metricUnavailable.${reason}`, reason);

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
