import type { BacktestRunSpec } from "../../../entities/backtest";
import { decideBacktestSource } from "./backtest-source";
import type { DocumentState } from "./document-state";

/** 실행 입력 소유자의 요청만 넘긴다. 변경 전 저장본으로 조용히 대체하지 않는다. */
export const experimentEntryBlock = (
  state: DocumentState,
  request: BacktestRunSpec | null,
  requiresUpgrade: boolean,
  parametersReady: boolean,
) => {
  if (requiresUpgrade) return "research.ide.upgrade" as const;
  if (state.strategyId === null || state.baseRevision === null || state.dirty)
    return "research.ide.saveFirst" as const;
  if (!parametersReady) return "research.ide.parametersPending" as const;
  const decision = decideBacktestSource(state);
  if (decision.kind === "blocked") return "research.ide.validateFirst" as const;
  if (decision.kind !== "saved_revision")
    return "research.ide.saveFirst" as const;
  if (request === null) return "research.ide.environmentFirst" as const;
  if (request.strategy_source?.kind !== "saved_revision")
    return "research.ide.saveFirst" as const;
  return null;
};
