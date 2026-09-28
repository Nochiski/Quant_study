import { useMemo, type ComponentProps } from "react";

import { StrategyDebugger } from "../../../features/debug-strategy";
import type { RunEnvironment } from "../../../shared/api";
import {
  ExecutionPlanPanel,
  type DocumentState,
  type ExecutionPlansState,
} from "../../../features/edit-strategy";
import { buildStrategyDebuggerAvailability } from "../model/strategy-debugger-context";

type StrategyDebuggerPanelProps = {
  document: DocumentState;
  executionPlans: ExecutionPlansState;
  /** 실행 설정 패널이 검증한 실행 설정. 없으면 추적이 막힌다(P3-02). */
  environment: RunEnvironment | null;
  publicationOwnerKey: string;
  asOf?: string;
  security?: string;
  selectedPointer?: string;
  onSearchSelection: ComponentProps<
    typeof StrategyDebugger
  >["onSearchSelection"];
  onSelectPointer: (pointer: string) => void;
};

/** Page-level composition of two sibling features; neither feature imports the other. */
export const StrategyDebuggerPanel = ({
  document,
  executionPlans,
  environment,
  publicationOwnerKey,
  asOf,
  security,
  selectedPointer,
  onSearchSelection,
  onSelectPointer,
}: StrategyDebuggerPanelProps) => {
  const availability = useMemo(
    () =>
      buildStrategyDebuggerAvailability(document, executionPlans, environment),
    [document, environment, executionPlans],
  );
  return (
    <StrategyDebugger
      context={availability.context}
      unavailableReason={availability.reason}
      publicationOwnerKey={publicationOwnerKey}
      asOf={asOf}
      security={security}
      selectedPointer={selectedPointer}
      onSearchSelection={onSearchSelection}
      onSelectPointer={onSelectPointer}
      executionPlan={
        <ExecutionPlanPanel
          state={executionPlans}
          selectedPointer={selectedPointer}
          onSelectPointer={onSelectPointer}
        />
      }
    />
  );
};
