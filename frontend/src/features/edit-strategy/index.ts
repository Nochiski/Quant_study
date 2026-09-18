export {
  canValidateDocument,
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
  type BacktestRunOptions,
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
export { ServerDraftBanner } from "./ui/server-draft-banner";
export {
  createNewDraftId,
  isNewDraftId,
  matchesServerDraftBase,
  revisionDraftId,
  type ServerDraftBase,
} from "./model/server-draft";
export { useServerDraft, type ServerDraftSync } from "./model/use-server-draft";
export { ConflictBanner } from "./ui/conflict-banner";
export { UpgradeBanner } from "./ui/upgrade-banner";
export {
  decideDocumentUpgrade,
  type StoredRevisionMeta,
  type UpgradeAvailability,
} from "./model/document-upgrade";
export {
  useUpgradeDocument,
  type DocumentUpgrade,
  type UpgradeStatus,
} from "./model/use-upgrade-document";
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
  describeApplicabilityConditions,
  isApplicableWhen,
  projectApplicability,
  type ApplicabilityCondition,
  type DefaultResolver,
  type FieldApplicability,
} from "./model/field-applicability";
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
export type { StrategyOutlineSymbol } from "./model/strategy-outline";
export {
  useSnippetInsertion,
  type SnippetFailure,
  type SnippetFeedback,
  type SnippetInsertion,
} from "./model/use-snippet-insertion";
export {
  projectForm,
  type FormControl,
  type FormField,
  type FormListItem,
  type FormProjection,
  type FormSection,
} from "./model/form-projection";
export {
  useSourceTransactions,
  type SourcePlanner,
  type SourceTransactions,
  type TransactionDisabledReason,
  type TransactionFailure,
  type TransactionFeedback,
} from "./model/use-source-transactions";
export type { SourceOperation } from "./model/source-transactions";
export {
  useFormProjection,
  type FormProjectionState,
} from "./model/use-form-projection";
export {
  addItemOperation,
  addPresetItemOperation,
  draftOf,
  fieldOperation,
  itemKinds,
  itemSection,
  parseDraft,
  removalBlockers,
  removeItemOperation,
  resetOperation,
  unsetOperation,
  type DraftParse,
  type ListSection,
  type ObjectSection,
} from "./model/form-transactions";
export {
  findReferences,
  type DocumentReference,
  type FindReferencesOptions,
} from "./model/document-references";
export {
  addNode,
  graphNodeIds,
  nodeKinds,
  nodePointerOf,
  removeNode,
  rewireInput,
  setMissingPolicy,
  setNodeField,
  setOutput,
  suggestNodeId,
} from "./model/graph-transactions";
export { StrategyFormPanel, type FormCatalogs } from "./ui/strategy-form-panel";
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
export { DirtyLeaveGuard } from "./ui/dirty-leave-guard";
