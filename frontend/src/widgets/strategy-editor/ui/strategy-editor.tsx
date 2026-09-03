import {
  StrategyDraftProvider,
  StrategyEditorWorkspace,
} from "../../../features/edit-strategy";

export const StrategyEditor = () => (
  <StrategyDraftProvider>
    <StrategyEditorWorkspace />
  </StrategyDraftProvider>
);
