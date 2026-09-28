import type { StrategyTraceResponse } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";

/** 추적 응답의 탈락·보류 사유 코드(OpenAPI enum, owner backend `ExclusionReason`). 생성 타입에서 파생한다. */
export type ExclusionReason = NonNullable<
  StrategyTraceResponse["target"]
>["construction"][number]["exclusion_reasons"][number];

/**
 * 사유 코드 → 사람 말(P3-01, PLAN P2-05 결정 4). 코드는 backend 가 소유하고 여기는 문장만 둔다.
 * `Record`라 enum 에 값이 늘면 typecheck 가 번역 누락을 막는다. 특히 횡단면 거르기의 순위 탈락
 * (`eligibility_rank_cut`)을 규칙 위반(`eligibility_failed`)과 다르게 읽히게 하는 것이 목적이다.
 */
const EXCLUSION_REASON_LABELS: Record<ExclusionReason, MessageKey> = {
  not_in_universe: "debugger.exclusion.not_in_universe",
  future_data: "debugger.exclusion.future_data",
  missing_eligibility: "debugger.exclusion.missing_eligibility",
  eligibility_failed: "debugger.exclusion.eligibility_failed",
  eligibility_rank_cut: "debugger.exclusion.eligibility_rank_cut",
  missing_factor: "debugger.exclusion.missing_factor",
  score_threshold: "debugger.exclusion.score_threshold",
  regime_blocked: "debugger.exclusion.regime_blocked",
  liquidity_failed: "debugger.exclusion.liquidity_failed",
  outside_selection: "debugger.exclusion.outside_selection",
  missing_risk: "debugger.exclusion.missing_risk",
  turnover_buffer: "debugger.exclusion.turnover_buffer",
  minimum_trade: "debugger.exclusion.minimum_trade",
};

export const exclusionReasonLabel = (reason: ExclusionReason): string =>
  t(EXCLUSION_REASON_LABELS[reason]);
