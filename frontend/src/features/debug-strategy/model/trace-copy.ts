import type { StrategyTraceResponse } from "../../../shared/api";
import { tCode } from "../../../shared/config";

type Construction = NonNullable<
  StrategyTraceResponse["target"]
>["construction"][number];

/** 추적 응답의 탈락·보류 사유 코드(OpenAPI enum, owner backend `ExclusionReason`). 생성 타입에서 파생한다. */
export type ExclusionReason = Construction["exclusion_reasons"][number];
/** 노드 값 상태(owner backend `TraceValueStatus`). `masked` 는 원장이 가린 칸 때문에 결측이 된 칸이다(#350). */
export type TraceStatus =
  StrategyTraceResponse["trace"]["rows"][number]["status"];
/** 합성 점수 항 하나의 상태(owner backend `FactorContributionStatus`). */
export type ContributionStatus =
  Construction["factor_contributions"][number]["status"];
/** 위험 제약이 목표 비중에 한 일(owner backend `PortfolioConstraintEffect`). */
export type ConstraintEffect = Construction["constraint_effect"];

/**
 * 사유 코드 → 사람 말(P3-01, PLAN P2-05 결정 4). 코드는 backend 가 소유하고 여기는 문장만 둔다.
 * 특히 횡단면 거르기의 순위 탈락(`eligibility_rank_cut`)을 규칙 위반(`eligibility_failed`)과 다르게
 * 읽히게 하는 것이 목적이다. 사유가 늘었는데 문구가 없으면 `tCode` 가 typecheck 에서 막는다.
 */
export const exclusionReasonLabel = (reason: ExclusionReason): string =>
  tCode(`debugger.exclusion.${reason}`, reason);

/** 추적 응답의 나머지 backend 어휘도 원문 대신 문구로 보인다(#350, 도메인 리뷰 A DR-A-11). */
export const traceStatusCopy = (status: TraceStatus): string =>
  tCode(`debugger.status.${status}`, status);

export const contributionStatusCopy = (status: ContributionStatus): string =>
  tCode(`debugger.contributionStatus.${status}`, status);

export const constraintEffectCopy = (effect: ConstraintEffect): string =>
  tCode(`debugger.constraintEffect.${effect}`, effect);
