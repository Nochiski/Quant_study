export { StrategyDraftProvider } from "./model/strategy-draft-provider";
export { useStrategyDraft } from "./model/strategy-draft-context";
export { StrategyEditorWorkspace } from "./ui/strategy-editor-workspace";
export { PortfolioEditor } from "./ui/portfolio-editor";
export { RiskEditor } from "./ui/risk-editor";
export { ExecutionEditor } from "./ui/execution-editor";
export {
  currentDiagnostics,
  currentSpec,
  documentReducer,
  initialDocumentState,
  isSpecStale,
  shouldCompile,
  shouldParse,
  type CompileOutcome,
  type DocumentAction,
  type DocumentDiagnostic,
  type DocumentPhase,
  type DocumentState,
} from "./model/document-state";
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
export { saveStatusText } from "./model/save-status";
export { useSchemaAssist, type SchemaAssist } from "./model/use-schema-assist";
export {
  toDocumentDiagnostics,
  useCompileDocument,
} from "./model/use-compile-document";
export { DiagnosticsPanel } from "./ui/diagnostics-panel";
export {
  decideBacktestSource,
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
export { SourceEditor } from "./ui/source-editor";
export { SaveAction } from "./ui/save-action";
export { DirtyLeaveGuard } from "./ui/dirty-leave-guard";
