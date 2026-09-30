import { useMemo } from "react";

import { StrategyPreview } from "../../../features/debug-strategy";
import type {
  DocumentState,
  ExecutionPlansState,
} from "../../../features/edit-strategy";
import type { RunEnvironment } from "../../../shared/api";
import { buildStrategyDebuggerAvailability } from "../model/strategy-debugger-context";

/** 문서·실행 계획의 현재성 판정은 기존 debugger 조합 경로와 공유한다. */
export const StrategyPreviewPanel = ({
  document,
  executionPlans,
  environment,
}: {
  document: DocumentState;
  executionPlans: ExecutionPlansState;
  environment: RunEnvironment | null;
}) => {
  const availability = useMemo(
    () =>
      buildStrategyDebuggerAvailability(document, executionPlans, environment),
    [document, executionPlans, environment],
  );
  return (
    <StrategyPreview
      context={availability.context}
      unavailableReason={availability.reason}
    />
  );
};
