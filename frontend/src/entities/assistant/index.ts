/**
 * 어시스턴트 어휘의 단일 입구.
 *
 * 상위 레이어는 `shared/api/assistant*`를 직접 import하지 않고 이 배럴을 쓴다 — 같은 값을 두 자리에서
 * 가져오면 어느 한쪽이 바뀌었을 때 그 사실이 나뉜다. 그래서 생성 타입과 `shared` 상수·오류 타입도
 * 여기서 다시 내보낸다(B-02 리뷰 P3-3 결정, 2026-09-21).
 */
export {
  assistantProvidersKey,
  assistantProvidersQuery,
  refreshAssistantProviders,
  refreshIfProviderGone,
  useActivateAssistantProvider,
  useDeleteAssistantProvider,
} from "./model/provider-queries";
export {
  assistantSessionKey,
  assistantSessionQuery,
  assistantSessionsKey,
  assistantSessionsQuery,
  useCancelAssistantTurn,
  useCreateAssistantSession,
  useStartAssistantTurn,
  type CancelAssistantTurnInput,
  type StartAssistantTurnInput,
} from "./model/session-queries";
export {
  assistantChatReducer,
  assistantTurn,
  emptyAssistantChatState,
  runningAssistantTurn,
  unsettledAssistantTurn,
  type AssistantChatAction,
  type AssistantChatState,
  type AssistantFailure,
  type AssistantSearchActivity,
  type AssistantToolActivity,
  type AssistantTurnState,
  type AssistantTurnTokens,
} from "./model/chat-state";
export { assistantFailureMessage } from "./model/failure-copy";
export {
  ASSISTANT_TURN_IN_PROGRESS,
  assistantRejectionMessage,
  isAssistantRejectionCode,
  type AssistantRejectionCode,
} from "./model/rejection-copy";
export {
  assistantStreamTarget,
  useAssistantEventStream,
  type AssistantStreamClose,
  type AssistantStreamCloseReason,
  type AssistantStreamStatus,
  type AssistantStreamTarget,
  type UseAssistantEventStreamOptions,
  type UseAssistantEventStreamResult,
} from "./model/use-assistant-event-stream";
export {
  ASSISTANT_SSE_MAX_RETRY_ATTEMPTS,
  AssistantRequestError,
  assistantProviderApi,
} from "../../shared/api";
export type {
  AssistantEventEnvelopeView,
  ChatMessageView,
  ChatRole,
  CreateProviderProfileRequestWritable as CreateProviderProfileInput,
  CreateSessionRequest,
  DocumentRefView,
  FailureCode,
  ProbeFailure,
  ProbeResultView,
  ProviderKind,
  ProviderKindView,
  ProviderProfileView,
  ProvidersView,
  SessionHistoryView,
  SessionUsageView,
  SessionView,
  SourceView,
  StartTurnRequest,
  StrategyProposalView,
  TokenTotalsView,
  TurnAcceptedView,
  TurnContextPayload,
  TurnStatus,
  TurnUsageView,
  TurnView,
  UsageView,
} from "../../shared/api";
