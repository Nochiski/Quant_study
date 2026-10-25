import { useContext, useMemo } from "react";

import { StrategyPreview } from "../../../features/debug-strategy";
import type {
  DocumentState,
  ExecutionPlansState,
} from "../../../features/edit-strategy";
import type { RunEnvironment } from "../../../shared/api";
import type { RunParameterContext } from "../../../entities/backtest";
import { buildStrategyDebuggerAvailability } from "../model/strategy-debugger-context";
import { OpenTraceWorkspaceContext } from "../model/workspace-context";

/** 문서·실행 계획의 현재성 판정은 기존 debugger 조합 경로와 공유한다. */
export const StrategyPreviewPanel = ({
  document,
  executionPlans,
  environment,
  parameters,
  onTraceSelection,
}: {
  document: DocumentState;
  executionPlans: ExecutionPlansState;
  environment: RunEnvironment | null;
  parameters?: RunParameterContext;
  /** "왜 골랐나": 추적할 종목·기준일을 URL 에 싣는다. 주면 추적 작업공간도 연다(RD-04-C). */
  onTraceSelection?: (selection: { asOf: string; security: string }) => void;
}) => {
  const openTrace = useContext(OpenTraceWorkspaceContext);
  const availability = useMemo(
    () =>
      buildStrategyDebuggerAvailability(document, executionPlans, environment, parameters),
    [document, executionPlans, environment, parameters],
  );
  return (
    <StrategyPreview
      context={availability.context}
      unavailableReason={availability.reason}
      onExplain={
        onTraceSelection === undefined
          ? undefined
          : (selection) => {
              onTraceSelection(selection);
              openTrace?.();
            }
      }
    />
  );
};
