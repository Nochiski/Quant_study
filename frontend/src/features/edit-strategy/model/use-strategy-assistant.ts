import { useMemo } from "react";

import type {
  DocumentRefView,
  TurnContextPayload,
} from "../../../entities/assistant";
import { assistantDocumentRef } from "./assistant-turn-context";
import type { DocumentState } from "./document-state";
import {
  assistantProposalOf,
  type AssistantProposalApply,
} from "./use-apply-assistant-proposal";
import {
  useApplyProposalThenBacktest,
  type BacktestTrigger,
  type ProposalBacktestChain,
} from "./use-apply-then-backtest";
import { useAssistantTurnContext } from "./use-assistant-turn-context";

/** 사이드바 제안 카드가 넘기는 동작. `assist-strategy` 타입을 모른 채 구조로만 받는다(FSD). */
type ProposalAction = Parameters<typeof assistantProposalOf>[0];

/** 제안 카드의 세 동작. 사이드바 props 이름과 같아 그대로 펼쳐 넘긴다. */
export type AssistantProposalHandlers = {
  onPreviewProposal: (action: ProposalAction) => void;
  onApplyProposal: (action: ProposalAction) => void;
  onApplyProposalAndBacktest: (action: ProposalAction) => void;
};

export type StrategyAssistant = {
  /** 세션이 붙는 문서. 타자마다 바뀌지 않도록 세 원시 값에만 묶는다. */
  documentRef: DocumentRefView;
  /** 턴을 시작하는 순간의 문서·실행 설정을 읽는다. commit된 값을 비추는 손잡이다. */
  readContext: () => TurnContextPayload;
  /** "적용 후 백테스트" 대기·알림 상태. 문서 알림 줄이 읽는다. */
  chain: ProposalBacktestChain;
  proposalHandlers: AssistantProposalHandlers;
};

export type StrategyAssistantOptions = {
  /** 초안 화면의 서버 초안 id. 저장된 revision이면 무시된다(`assistantDocumentRef`). */
  draftId: string | null;
  /** 턴에 실어 보낼 실행 설정. 무효이면 null이다. */
  environment: Record<string, unknown> | null;
  backtest: BacktestTrigger;
};

/**
 * 두 전략 화면(새 전략·저장된 리비전)의 어시스턴트 배선을 한 곳에서 만든다(Phase B 감사 NB-8).
 *
 * 문서 참조, 턴 컨텍스트, "적용 후 백테스트", 제안 카드의 세 동작은 두 화면에서 같아야 한다. 화면마다
 * 따로 적으면 한쪽만 콜백 하나를 잃어도 컴파일러가 알리지 않는다(사이드바 props가 선택이다). 화면에
 * 따라 다른 값은 실행 게이트(`backtest`)의 출처 하나뿐이라 인자로 받는다.
 *
 * 적용 훅(`useApplyAssistantProposal`)은 받기만 한다 — 화면이 편집기 준비 콜백을 모으려고 그 훅을
 * 이 훅보다 먼저 부르기 때문이다.
 */
export const useStrategyAssistant = (
  apply: AssistantProposalApply,
  state: DocumentState,
  { draftId, environment, backtest }: StrategyAssistantOptions,
): StrategyAssistant => {
  const { strategyId, baseRevision } = state;
  const documentRef = useMemo(
    () => assistantDocumentRef(strategyId, baseRevision, draftId),
    [strategyId, baseRevision, draftId],
  );
  // 턴은 세션 생성 왕복 뒤에 시작될 수 있다. 텍스트는 그 순간 편집기에서 직접 읽는다
  // (`.claude/rules/frontend-react-effects.md`).
  const readContext = useAssistantTurnContext(
    state,
    apply.readSource,
    environment,
  );
  const chain = useApplyProposalThenBacktest(apply, state, backtest);

  const { preview, apply: applyProposal } = apply;
  const { applyThenBacktest } = chain;
  const proposalHandlers = useMemo<AssistantProposalHandlers>(
    () => ({
      onPreviewProposal: (action) => preview(assistantProposalOf(action)),
      onApplyProposal: (action) => applyProposal(assistantProposalOf(action)),
      onApplyProposalAndBacktest: (action) =>
        applyThenBacktest(assistantProposalOf(action)),
    }),
    [applyProposal, applyThenBacktest, preview],
  );

  return { documentRef, readContext, chain, proposalHandlers };
};
