import { describe, expect, expectTypeOf, it } from "vitest";

import type { MetricValue } from "../../../shared/api";
import { messages, type MessageKey } from "../../../shared/config";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  HIGHLIGHTED_METRIC_IDS,
  metricPlainCopy,
  metricUnavailableCopy,
} from "../model/metric-copy";

/**
 * backend Metric Registry의 id 목록(결과 설명 spec R4). backend 테스트가 이 파일과 registry가
 * 같은지 지키고, 여기서는 id마다 ko·en 쉬운 이름·뜻이 있는지 본다. 지표가 늘면 두 테스트가 차례로
 * 깨져 문구를 쓰라고 알린다. 지표 id는 wire에서 문자열이라 생성 SDK가 목록을 주지 않는다(#293).
 */
const registryMetricIds = (): string[] =>
  JSON.parse(readBackendFixture("analytics/metric_ids.json")) as string[];

describe("지표 쉬운 이름·뜻", () => {
  const ids = registryMetricIds();

  it("registry 목록이 비어 있지 않다", () => {
    // 목록이 비면 아래 검사가 공허하게 통과한다. 그 통과가 이 가드의 유일한 실패 모드다.
    expect(ids.length).toBeGreaterThan(0);
  });

  it("결과 화면 강조 지표는 모두 registry id 다", () => {
    // 없는 id 는 강조 칸에서 조용히 빠진다 — registry id 가 바뀌면 여기서 깨진다(#362 DR-B-09)
    expect(HIGHLIGHTED_METRIC_IDS.filter((id) => !ids.includes(id))).toEqual(
      [],
    );
  });

  it.each(ids)("%s는 ko 쉬운 이름과 한 줄 뜻이 있다", (id) => {
    const copy = metricPlainCopy(id);
    expect(copy).not.toBeNull();
    expect(copy?.name.trim()).not.toBe("");
    expect(copy?.description.trim()).not.toBe("");
  });

  it.each(ids)("%s는 en 이름과 뜻도 있다", (id) => {
    const stem = `backtest.metric.${id}`;
    const en: Record<string, string> = messages.en;
    expect(en[stem]?.trim()).toBeTruthy();
    expect(en[`${stem}.description`]?.trim()).toBeTruthy();
  });

  it("registry에 없는 지표의 문구를 남겨 두지 않는다", () => {
    const known = new Set(
      ids.flatMap((id) => [
        `backtest.metric.${id}`,
        `backtest.metric.${id}.description`,
      ]),
    );
    const stale = (Object.keys(messages.ko) as MessageKey[]).filter(
      (key) => key.startsWith("backtest.metric.") && !known.has(key),
    );
    expect(stale).toEqual([]);
  });

  it("모르는 지표는 null이라 키 문자열이 화면에 나가지 않는다", () => {
    expect(metricPlainCopy("no_such_metric")).toBeNull();
  });
});

describe("지표 사용 불가 사유 문구", () => {
  // 사유마다 ko·en 문구가 있는지는 타입이 강제한다(이슈 #293) — `metricUnavailableCopy`가 키를
  // `tCode`에 `MessageKey`로 넘기고, en 표는 `satisfies Record<MessageKey, string>`이다. 빈 문장은
  // 메시지 표 테스트가 본다. 반대로 SDK에 없는 사유의 문구가 남으면 아래 타입 검사가 typecheck에서 막는다.
  it("사유 문구를 ko 표에서 찾는다", () => {
    expect(metricUnavailableCopy("benchmark_not_available")).toBe(
      messages.ko["backtest.metricUnavailable.benchmark_not_available"],
    );
  });

  it("SDK에 없는 사유의 문구를 남겨 두지 않는다", () => {
    type Reason = NonNullable<MetricValue["unavailable_reason"]>;
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `backtest.metricUnavailable.${string}`>,
        `backtest.metricUnavailable.${Reason}`
      >
    >().toBeNever();
  });

  it("생성 SDK보다 새 사유는 원문을 읽기 쉽게만 바꿔 보인다", () => {
    // @ts-expect-error 생성 SDK에 없는 사유가 실려 온 경우를 흉내 낸다.
    expect(metricUnavailableCopy("future_reason_code")).toBe(
      "future reason code",
    );
  });
});
