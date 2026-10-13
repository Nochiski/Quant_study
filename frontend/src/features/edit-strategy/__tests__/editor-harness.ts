/** 단일 명령 훅의 테스트에만 준비 owner를 주입한다. 화면 통합 테스트는 실제 공용 owner를 쓴다. */
import { useEditorHandle } from "../model/use-editor-handle";
import { useUpgradeDocument as useUpgradeDocumentWithOwner } from "../model/use-upgrade-document";
import { useApplyAssistantProposal as useApplyAssistantProposalWithOwner } from "../model/use-apply-assistant-proposal";
import { useSourceTransactions as useSourceTransactionsWithOwner } from "../model/use-source-transactions";
import { useWholeDocumentReplacement as useWholeDocumentReplacementWithOwner } from "../model/replace-whole-document";
import { useOutlineNavigation as useOutlineNavigationWithOwner } from "../model/use-outline-navigation";
import { useDiagnosticNavigation as useDiagnosticNavigationWithOwner } from "../model/use-diagnostic-navigation";

type Tail<T extends readonly unknown[]> = T extends [unknown, ...infer Rest] ? Rest : never;

export const useUpgradeDocument = (...args: Tail<Parameters<typeof useUpgradeDocumentWithOwner>>) => {
  const owner = useEditorHandle();
  return { ...useUpgradeDocumentWithOwner(owner, ...args), onEditorReady: owner.onEditorReady };
};

export const useApplyAssistantProposal = (...args: Tail<Parameters<typeof useApplyAssistantProposalWithOwner>>) => {
  const owner = useEditorHandle();
  return { ...useApplyAssistantProposalWithOwner(owner, ...args), onEditorReady: owner.onEditorReady };
};

export const useSourceTransactions = (...args: Tail<Parameters<typeof useSourceTransactionsWithOwner>>) => {
  const owner = useEditorHandle();
  return { ...useSourceTransactionsWithOwner(owner, ...args), onEditorReady: owner.onEditorReady };
};

export const useWholeDocumentReplacement = (...args: Tail<Parameters<typeof useWholeDocumentReplacementWithOwner>>) => {
  const owner = useEditorHandle();
  return { ...useWholeDocumentReplacementWithOwner(owner, ...args), onEditorReady: owner.onEditorReady };
};

export const useOutlineNavigation = (...args: Tail<Parameters<typeof useOutlineNavigationWithOwner>>) => {
  const owner = useEditorHandle();
  return { ...useOutlineNavigationWithOwner(owner, ...args), onEditorReady: owner.onEditorReady };
};

export const useDiagnosticNavigation = (...args: Tail<Parameters<typeof useDiagnosticNavigationWithOwner>>) => {
  const owner = useEditorHandle();
  return { ...useDiagnosticNavigationWithOwner(owner, ...args), onEditorReady: owner.onEditorReady };
};

export type { SourceTransactions, TransactionFeedback } from "../model/use-source-transactions";

import { useSnippetInsertion as useSnippetInsertionWithOwner } from "../model/use-snippet-insertion";
export const useSnippetInsertion = (
  source: Parameters<typeof useSnippetInsertionWithOwner>[0],
  active: Parameters<typeof useSnippetInsertionWithOwner>[1],
  transactions: ReturnType<typeof useSourceTransactions>,
) => ({ ...useSnippetInsertionWithOwner(source, active, transactions), onEditorReady: transactions.onEditorReady });
