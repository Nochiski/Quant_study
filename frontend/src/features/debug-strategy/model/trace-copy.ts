import type { StrategyTraceResponse } from "../../../shared/api";
import { tCode } from "../../../shared/config";

/** 추적 응답의 탈락·보류 사유 코드(OpenAPI enum, owner backend `ExclusionReason`). 생성 타입에서 파생한다. */
export type ExclusionReason = NonNullable<
  StrategyTraceResponse["target"]
>["construction"][number]["exclusion_reasons"][number];

/**
 * 사유 코드 → 사람 말(P3-01, PLAN P2-05 결정 4). 코드는 backend 가 소유하고 여기는 문장만 둔다.
 * 특히 횡단면 거르기의 순위 탈락(`eligibility_rank_cut`)을 규칙 위반(`eligibility_failed`)과 다르게
 * 읽히게 하는 것이 목적이다. 사유가 늘었는데 문구가 없으면 `tCode` 가 typecheck 에서 막는다.
 */
export const exclusionReasonLabel = (reason: ExclusionReason): string =>
  tCode(`debugger.exclusion.${reason}`, reason);
