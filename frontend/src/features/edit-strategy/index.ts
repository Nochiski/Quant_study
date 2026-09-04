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
export { SourceEditor } from "./ui/source-editor";
