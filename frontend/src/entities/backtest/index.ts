export { backtestHistoryQuery, trialLedgerQuery, useBacktestRequest, useBacktestResult, useBacktestStatus, useBacktestSummary, useBacktestTrialPreview, useCancelBacktest, useMergeTrialLineage, useRunEnvironmentSchema, useStartBacktest } from "./model/backtest-queries";
export { RUN_KINDS, isRunKind, runKindLabel } from "./model/run-kind";
export { runEnvironmentApplies, runEnvironmentDisplayValue, runEnvironmentFields, runEnvironmentLabel, runEnvironmentName, runEnvironmentOptionLabel, runEnvironmentWireNumber, runEnvironmentWireText, type RunEnvironmentField } from "./model/run-environment-fields";
export {
  backtestErrorSentence,
  backtestStartRejectionMessage,
  requestRejectionMessage,
} from "./model/backtest-error";
export { BacktestRunDetail } from "./ui/backtest-run-detail";
export {
  BacktestResultFailure,
  BacktestRunFailure,
} from "./ui/backtest-run-failure";
export type { BacktestRunResult, BacktestRunSpec, BacktestRunState, BacktestRunSummary, RunKind, TrialLedger } from "../../shared/api";

export { RUN_STATUS } from "./model/run-status";

/** 실행 요청에서 전략 본문과 참조를 제외한 옵션. 두 실행 feature의 공통 계약이다. */
export type BacktestRequestOptions = Omit<import("../../shared/api").BacktestRunSpec, "strategy" | "strategy_source">;
