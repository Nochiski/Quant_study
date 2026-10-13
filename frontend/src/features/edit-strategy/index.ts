export { canValidateDocument, currentDiagnostics, currentCompile, initialDocumentState, type DocumentState } from "./model/document-state";
export { PROJECTION_VIEWS, migrateStrategyView, type StrategyView } from "./model/strategy-views";

export { useStrategyDocument } from "./model/use-strategy-document";
export { type DocumentSource } from "./model/document-source";
export { useSaveDocument } from "./model/use-save-document";
export { saveStatusText, saveStatusTone } from "./model/save-status";
export { useSchemaAssist } from "./model/use-schema-assist";
export { useCompileDocument } from "./model/use-compile-document";
export { DiagnosticsPanel } from "./ui/diagnostics-panel";
export { DocumentStatus } from "./ui/document-status";

export { useDiagnosticNavigation } from "./model/use-diagnostic-navigation";
export { useDocumentHistory } from "./model/use-document-history";
export { DocumentHistoryActions } from "./ui/document-history";
export { decideBacktestSource, gateBacktestSourceWithFactorPlans } from "./model/backtest-source";
export { useRunBacktest } from "./model/use-run-backtest";
export { DocumentToolbar } from "./ui/document-toolbar";
export { useAutosave } from "./model/use-autosave";

export { RecoveryBanner } from "./ui/recovery-banner";
export { ServerDraftBanner } from "./ui/server-draft-banner";
export { createNewDraftId, isNewDraftId, revisionDraftId } from "./model/server-draft";
export { useServerDraft } from "./model/use-server-draft";
export { ConflictBanner } from "./ui/conflict-banner";
export { UpgradeBanner } from "./ui/upgrade-banner";

export { useUpgradeDocument } from "./model/use-upgrade-document";


export { useStrategyAssistant } from "./model/use-strategy-assistant";
export { useApplyAssistantProposal } from "./model/use-apply-assistant-proposal";

export {
  ProposalApplyDialog,
  ProposalApplyFeedback,
} from "./ui/proposal-apply-dialog";
export { SourceEditor } from "./ui/source-editor";
export { StrategyOutline } from "./ui/strategy-outline";
export { ContractInspector } from "./ui/contract-inspector";
export { ExecutionPlanPanel } from "./ui/execution-plan-panel";
export { FactorGraphPanel } from "./ui/factor-graph-panel";
export { PipelinePanel } from "./ui/pipeline-panel";
export { SnippetCatalog } from "./ui/snippet-catalog";
export { StrategyDiffPanel } from "./ui/strategy-diff-panel";



export { useOutlineNavigation } from "./model/use-outline-navigation";
export type { StrategyOutlineSymbol } from "./model/strategy-outline";
export { useSnippetInsertion } from "./model/use-snippet-insertion";

export { useSourceTransactions } from "./model/use-source-transactions";

export { useFormProjection } from "./model/use-form-projection";



// 팔레트 투영·항목 타입은 slice 안에서만 쓴다(pages는 `FactorGraphEditing.operators`로 타입이
// 흘러 들어가 직접 import하지 않는다). 공개 API는 실제 소비자가 있는 것만 둔다 — 넓히면 slice
// 내부 리팩터가 밖으로 샌다(리뷰 P3, `frontend-fsd.md`).




export { compiledNodeOrigin, documentOutputNodeId, factorIndexAtPointer, factorGraphPointer, nodePointerById, useExecutionPlans, type ExecutionPlansState } from "./model/use-execution-plans";

export { DirtyLeaveGuard } from "./ui/dirty-leave-guard";

export { RecipePanel } from "./ui/recipe-panel";

export { useWholeDocumentReplacement } from "./model/replace-whole-document";
