import { describe, expect, expectTypeOf, it } from "vitest";

import type { MessageKey } from "../../../shared/config";
import { readOpenApi } from "../../../shared/testing/openapi-codes";
import {
  constraintEffectCopy,
  contributionStatusCopy,
  exclusionReasonLabel,
  traceStatusCopy,
  type ConstraintEffect,
  type ContributionStatus,
  type ExclusionReason,
  type TraceStatus,
} from "../model/trace-copy";

const HANGUL = /[가-힣]/;

describe("trace vocabulary copy (P3-01, #350)", () => {
  // `tCode` 가 생성 enum 누락을 typecheck 에서 막고(en 은 `Record<MessageKey, string>`), 이 테스트는
  // 실행 시 계약 파일과 대조한다. 값 목록의 owner 는 backend 다(여기 손으로 적지 않는다).
  it.each([
    ["ExclusionReason", exclusionReasonLabel],
    ["TraceValueStatus", traceStatusCopy],
    ["FactorContributionStatus", contributionStatusCopy],
    ["PortfolioConstraintEffect", constraintEffectCopy],
  ] as const)("names every %s value the backend can send", (schema, copy) => {
    const values = readOpenApi().components.schemas[schema]?.enum ?? [];
    expect(values.length).toBeGreaterThan(0);
    const untranslated = values.filter(
      (value) => !HANGUL.test((copy as (code: string) => string)(value)),
    );
    expect(untranslated).toEqual([]);
  });

  it("keeps no copy for a value the generated SDK no longer has", () => {
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `debugger.exclusion.${string}`>,
        `debugger.exclusion.${ExclusionReason}`
      >
    >().toBeNever();
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `debugger.status.${string}`>,
        `debugger.status.${TraceStatus}`
      >
    >().toBeNever();
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `debugger.contributionStatus.${string}`>,
        `debugger.contributionStatus.${ContributionStatus}`
      >
    >().toBeNever();
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `debugger.constraintEffect.${string}`>,
        `debugger.constraintEffect.${ConstraintEffect}`
      >
    >().toBeNever();
  });

  it("reads a cross-sectional rank cut differently from a broken eligibility rule", () => {
    // `top_percent`·`top_count`에서 잘린 종목은 규칙을 어긴 것이 아니다(P2-05). 같은 말로 보이면
    // "상위 20%에 못 들었다"가 "조건을 어겼다"로 읽힌다.
    expect(exclusionReasonLabel("eligibility_rank_cut")).toBe(
      "상위 비율·개수 밖(순위로 잘림)",
    );
    expect(exclusionReasonLabel("eligibility_failed")).toBe(
      "거르기 조건 불통과",
    );
  });

  it("tells a cell the ledger masked apart from a missing input", () => {
    // 두 입력이 모두 값인데 사이에 가린 칸이 든 칸이 "입력 없음"으로 보이면 원인을 알 수 없다(#350).
    expect(traceStatusCopy("masked")).toBe("원장이 가림");
    expect(traceStatusCopy("missing_input")).toBe("입력 없음");
  });

  it("reads a value newer than the generated SDK without inventing copy", () => {
    // @ts-expect-error 생성 SDK에 없는 상태가 실려 온 경우를 흉내 낸다.
    expect(traceStatusCopy("future_status")).toBe("future status");
  });
});
