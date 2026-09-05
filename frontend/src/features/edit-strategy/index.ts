export { StrategyDraftProvider } from "./model/strategy-draft-provider";
export { useStrategyDraft } from "./model/strategy-draft-context";
export { StrategyEditorWorkspace } from "./ui/strategy-editor-workspace";
export { PortfolioEditor } from "./ui/portfolio-editor";
export { RiskEditor } from "./ui/risk-editor";
export { ExecutionEditor } from "./ui/execution-editor";
export {
  currentDiagnostics,
  currentCompile,
  currentSpec,
  documentReducer,
  initialDocumentState,
  isSpecStale,
  isCompleteCompileOutcome,
  shouldCompile,
  shouldParse,
  type CompileOutcome,
  type CompleteCompileOutcome,
  type DocumentAction,
  type DocumentDiagnostic,
  type DocumentPhase,
  type DocumentState,
} from "./model/document-state";
export {
  PROJECTION_VIEWS,
  STRATEGY_VIEWS,
  type StrategyView,
} from "./model/strategy-views";
export {
  projectStrategySpec,
  type StrategyProjection,
} from "./model/strategy-projection";
export { useStrategyDocument } from "./model/use-strategy-document";
export {
  documentSourceKey,
  loadAction,
  type DocumentSource,
} from "./model/document-source";
export {
  canSaveDocument,
  useSaveDocument,
  type SaveStatus,
} from "./model/use-save-document";
export { saveStatusText, saveStatusTone } from "./model/save-status";
export { useSchemaAssist, type SchemaAssist } from "./model/use-schema-assist";
export {
  toDocumentDiagnostics,
  useCompileDocument,
} from "./model/use-compile-document";
export { DiagnosticsPanel } from "./ui/diagnostics-panel";
export {
  decideBacktestSource,
  gateBacktestSourceWithFactorPlans,
  type BacktestSourceDecision,
} from "./model/backtest-source";
export {
  useRunBacktest,
  type RunBacktestStatus,
} from "./model/use-run-backtest";
export { DocumentToolbar } from "./ui/document-toolbar";
export {
  useAutosave,
  type Autosave,
  type Recovery,
} from "./model/use-autosave";
export {
  clearDraft,
  draftKey,
  readDraft,
  writeDraft,
  type DraftRecord,
  type DraftStorage,
} from "./model/draft-store";
export { RecoveryBanner } from "./ui/recovery-banner";
export { ConflictBanner } from "./ui/conflict-banner";
export { SourceEditor } from "./ui/source-editor";
export { StrategyOutline } from "./ui/strategy-outline";
export { ContractInspector } from "./ui/contract-inspector";
export { ExecutionPlanPanel } from "./ui/execution-plan-panel";
export { FactorGraphPanel } from "./ui/factor-graph-panel";
export { SnippetCatalog } from "./ui/snippet-catalog";
export { StrategyProjectionPanel } from "./ui/strategy-projection-panel";
export { StrategyDiffPanel } from "./ui/strategy-diff-panel";
export {
  diffCanonicalJson,
  projectDraftDiff,
  type DraftDiffProjection,
  type DraftSemanticDiff,
} from "./model/diff-projection";
export {
  projectContractField,
  projectContractInspector,
  type ContractFieldProjection,
  type ContractInspectorProjection,
  type ContractInspectorSource,
} from "./model/contract-inspector";
export {
  useOutlineNavigation,
  type StrategyOutlineNavigation,
} from "./model/use-outline-navigation";
export {
  useSnippetInsertion,
  type SnippetFeedback,
  type SnippetInsertion,
} from "./model/use-snippet-insertion";
export {
  buildCanonicalSnippetCatalog,
  planSnippetEdit,
  SNIPPET_CATEGORIES,
  type CanonicalSnippet,
  type SnippetCatalogSource,
  type SnippetCategory,
  type SnippetEditFailure,
  type SnippetEditResult,
} from "./model/canonical-snippets";
export {
  factorIndexAtPointer,
  factorGraphPointer,
  factorNodePointer,
  nodePointerById,
  pointerSelectsNode,
  prepareExecutionPlans,
  useExecutionPlans,
  type ExecutionPlansState,
  type FactorPlanRequest,
  type PlannedFactor,
} from "./model/use-execution-plans";
export {
  projectFactorGraphs,
  type FactorGraphProjection,
  type GraphFactorProjection,
  type GraphInputProjection,
  type GraphNodeDetail,
  type GraphNodeProjection,
} from "./model/factor-graph-projection";
export { SaveAction } from "./ui/save-action";
export { DirtyLeaveGuard } from "./ui/dirty-leave-guard";
