import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { messages, type MessageKey } from "../../../shared/config";
import { metricPlainCopy, metricUnavailableCopy } from "../model/metric-copy";

/**
 * backend Metric Registry의 id 목록(결과 설명 spec R4). backend 테스트가 이 파일과 registry가
 * 같은지 지키고, 여기서는 id마다 ko·en 쉬운 이름·뜻이 있는지 본다. 지표가 늘면 두 테스트가 차례로
 * 깨져 문구를 쓰라고 알린다.
 */
const FIXTURE_RELATIVE = "backend/tests/fixtures/analytics/metric_ids.json";
const REASON_FIXTURE_RELATIVE =
  "backend/tests/fixtures/analytics/metric_unavailable_reasons.json";
const readFixture = (relative: string): string[] => {
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, relative);
    if (existsSync(candidate))
      return JSON.parse(readFileSync(candidate, "utf8")) as string[];
    const parent = dirname(dir);
    if (parent === dir)
      throw new Error(
        `fixture not found — path=${relative} from=${process.cwd()}`,
      );
    dir = parent;
  }
};
const registryMetricIds = (): string[] => readFixture(FIXTURE_RELATIVE);

describe("지표 쉬운 이름·뜻", () => {
  const ids = registryMetricIds();

  it("registry 목록이 비어 있지 않다", () => {
    // 목록이 비면 아래 검사가 공허하게 통과한다. 그 통과가 이 가드의 유일한 실패 모드다.
    expect(ids.length).toBeGreaterThan(0);
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
  // backend 지표 계산의 사유 목록(이슈 #241). backend 테스트가 골든과 사유 enum 이 같은지 지키고,
  // 여기서는 사유마다 ko·en 문구가 있는지 본다.
  const reasons = readFixture(REASON_FIXTURE_RELATIVE);

  it("사유 목록이 비어 있지 않다", () => {
    expect(reasons.length).toBeGreaterThan(0);
  });

  it.each(reasons)("%s는 ko·en 문구가 있다", (reason) => {
    const key = `backtest.metricUnavailable.${reason}`;
    const ko: Record<string, string> = messages.ko;
    const en: Record<string, string> = messages.en;
    expect(ko[key]?.trim()).toBeTruthy();
    expect(en[key]?.trim()).toBeTruthy();
    expect(metricUnavailableCopy(reason)).toBe(ko[key]);
  });

  it("모르는 사유는 원문을 읽기 쉽게만 바꿔 보인다", () => {
    expect(metricUnavailableCopy("future_reason_code")).toBe(
      "future reason code",
    );
  });
});
