import {
  queryOptions,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  assistantSessionApi,
  type CreateSessionRequest,
  type DocumentRefView,
  type StartTurnRequest,
} from "../../../shared/api";

export const assistantSessionsKey = (documentRef: DocumentRefView) =>
  [
    "assistant",
    "sessions",
    documentRef.strategy_id ?? null,
    documentRef.revision ?? null,
    documentRef.draft_id ?? null,
  ] as const;

export const assistantSessionKey = (sessionId: string) =>
  ["assistant", "session", sessionId] as const;

/** 문서 하나에 달린 세션 목록. 사이드바의 세션 전환 메뉴가 읽는다. */
export const assistantSessionsQuery = (documentRef: DocumentRefView) =>
  queryOptions({
    queryKey: assistantSessionsKey(documentRef),
    queryFn: () => assistantSessionApi.listSessions(documentRef),
  });

/**
 * 메시지·턴·이벤트 이력. 서버 이력의 owner는 이 캐시 하나다.
 *
 * SSE 리더가 스트림이 닫힐 때마다 이 query로 턴 상태를 확정한다 — `staleTime`을 두지 않는 이유가
 * 그것이다(그 시점의 저장된 상태를 다시 읽어야 한다).
 */
export const assistantSessionQuery = (sessionId: string) =>
  queryOptions({
    queryKey: assistantSessionKey(sessionId),
    queryFn: () => assistantSessionApi.getSession(sessionId),
  });

const refreshSessions = (
  queryClient: QueryClient,
  documentRef: DocumentRefView,
): Promise<void> =>
  queryClient.invalidateQueries({ queryKey: assistantSessionsKey(documentRef) });

export const useCreateAssistantSession = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSessionRequest) =>
      assistantSessionApi.createSession(input),
    onSuccess: (session) => refreshSessions(queryClient, session.document_ref),
  });
};

export type StartAssistantTurnInput = {
  sessionId: string;
  request: StartTurnRequest;
};

/**
 * 턴 시작. 202 응답의 `accepted_sequence`가 스트림을 여는 자리다.
 *
 * 이력을 다시 읽지 않는다: 지금 막 시작한 턴의 이벤트는 스트림이 흘려 주고, 그때 도착하는 이력
 * 스냅샷은 스트림보다 뒤쳐진 채로 리듀서에 들어간다. 턴 상태는 응답을 그대로 리듀서에 넣는다.
 */
export const useStartAssistantTurn = () =>
  useMutation({
    mutationFn: ({ sessionId, request }: StartAssistantTurnInput) =>
      assistantSessionApi.startTurn(sessionId, request),
  });

export type CancelAssistantTurnInput = { sessionId: string; turnId: string };

/** 취소 요청. 저장된 턴 상태를 그대로 돌려주므로 이력을 다시 읽지 않는다. */
export const useCancelAssistantTurn = () =>
  useMutation({
    mutationFn: ({ sessionId, turnId }: CancelAssistantTurnInput) =>
      assistantSessionApi.cancelTurn(sessionId, turnId),
  });
