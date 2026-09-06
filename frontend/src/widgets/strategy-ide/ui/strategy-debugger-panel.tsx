import { useMemo, type ComponentProps } from "react";

import { StrategyDebugger } from "../../../features/debug-strategy";
import {
  ExecutionPlanPanel,
  type DocumentState,
  type ExecutionPlansState,
} from "../../../features/edit-strategy";
import { buildStrategyDebuggerAvailability } from "../model/strategy-debugger-context";

type StrategyDebuggerPanelProps = {
  document: DocumentState;
  executionPlans: ExecutionPlansState;
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
  publicationOwnerKey,
  asOf,
  security,
  selectedPointer,
  onSearchSelection,
  onSelectPointer,
}: StrategyDebuggerPanelProps) => {
  const availability = useMemo(
    () => buildStrategyDebuggerAvailability(document, executionPlans),
    [document, executionPlans],
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
