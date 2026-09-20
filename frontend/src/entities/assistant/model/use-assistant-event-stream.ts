import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import {
  ASSISTANT_SSE_MAX_RETRY_ATTEMPTS,
  assistantEventEnvelope,
  openAssistantEventStream,
  type AssistantEventEnvelopeView,
  type AssistantStreamRejection,
  type SessionHistoryView,
} from "../../../shared/api";
import { useCommittedRef } from "../../../shared/lib/react";
import { runningAssistantTurn, type AssistantChatState } from "./chat-state";
import { assistantSessionQuery } from "./session-queries";

/** 스트림을 열 대상. 진행 중 턴이 없으면 null이고, 그때는 열지 않는다(spec D7). */
export type AssistantStreamTarget = {
  sessionId: string;
  turnId: string;
};

/**
 * 투영에서 스트림 대상을 고른다.
 *
 * "진행 중 턴이 있을 때만 열고 종료 상태에서 닫는다"는 규칙을 화면이 아니라 여기서 집행한다 —
 * 턴이 종료 상태로 바뀌면 대상이 사라지고 리더가 연결을 정리한다.
 */
export const assistantStreamTarget = (
  state: AssistantChatState,
): AssistantStreamTarget | null => {
  const running = runningAssistantTurn(state);
  if (state.sessionId === null || running === null) return null;
  return { sessionId: state.sessionId, turnId: running.turnId };
};

export type AssistantStreamClose =
  /** 서버가 턴 종료와 함께 닫았다. */
  | { reason: "ended" }
  /** 서버가 열어 주지 않았다 — 409 `no_running_turn`이 대표다. */
  | { reason: "rejected"; rejection: AssistantStreamRejection }
  /** 재시도 상한을 다 썼다. */
  | { reason: "exhausted" };

export type UseAssistantEventStreamOptions = {
  target: AssistantStreamTarget | null;
  /**
   * 이미 반영한 마지막 sequence. 최초 연결의 `after_sequence`로 나간다.
   *
   * 이력을 읽고 연 사이드바에서는 이 값이 턴 시작 응답의 `accepted_sequence`와 같다(둘 다 턴
   * 직전 세션의 마지막 번호다). 이력 없이 연 경우에는 더 앞이라 서버가 이미 반영한 프레임을
   * 몇 개 다시 보내는데, 리듀서가 무시한다 — 뒤에서 여는 쪽이 이벤트를 흘리는 것보다 안전하다.
   */
  lastSequence: number;
  onEvent: (envelope: AssistantEventEnvelopeView) => void;
  /** 스트림이 닫힌 뒤 이력으로 확정한 결과. 턴의 최종 상태는 여기서만 온다(spec D3). */
  onHistory: (history: SessionHistoryView) => void;
  onClose?: (close: AssistantStreamClose) => void;
  maxRetryAttempts?: number;
  /** 첫 재시도까지의 대기(ms). 생략하면 생성 SSE 클라이언트 기본값. */
  retryDelayMs?: number;
};

/**
 * 진행 중 턴의 SSE를 읽고, 닫히면 이력으로 턴 상태를 확정한다.
 *
 * - 대상이 없으면 열지 않는다. 진행 중 턴이 없을 때 여는 것은 서버가 409로 막는 backstop이 있지만,
 *   그 전에 프론트가 열지 않는 것이 규칙이다(spec D7).
 * - 거절(4xx)에는 다시 연결하지 않는다. 진행 중 턴이 없다는 답은 몇 번을 물어도 같고, 그 턴의
 *   결과는 이력에 이미 적혀 있다 — 턴이 스트림을 열기 전에 끝난 경우가 그렇다.
 * - 스트림이 정상으로 닫혀도 이력을 다시 읽는다. `Done`은 턴 종료 판정이 아니고, 마지막 이벤트와
 *   저장된 턴 상태는 서버만 안다(spec D3).
 * - 콜백과 `lastSequence`는 commit과 같은 시점에 비친 ref로 읽는다. 읽는 쪽이 React 렌더 밖의
 *   async 루프라 passive effect로 갱신하면 옛 값을 읽는 틈이 생긴다(`frontend-react-effects.md`).
 */
export const useAssistantEventStream = ({
  target,
  lastSequence,
  onEvent,
  onHistory,
  onClose,
  maxRetryAttempts = ASSISTANT_SSE_MAX_RETRY_ATTEMPTS,
  retryDelayMs,
}: UseAssistantEventStreamOptions): void => {
  const queryClient = useQueryClient();
  const sessionId = target?.sessionId ?? null;
  const turnId = target?.turnId ?? null;
  const lastSequenceRef = useCommittedRef(lastSequence);
  const onEventRef = useCommittedRef(onEvent);
  const onHistoryRef = useCommittedRef(onHistory);
  const onCloseRef = useCommittedRef(onClose);

  useEffect(() => {
    if (sessionId === null || turnId === null) return;
    const controller = new AbortController();
    let rejection: AssistantStreamRejection | null = null;
    let lastAttemptOk = false;

    const recover = async (): Promise<void> => {
      try {
        const history = await queryClient.fetchQuery(
          assistantSessionQuery(sessionId),
        );
        if (!controller.signal.aborted) onHistoryRef.current(history);
      } catch {
        // 이력 조회 실패는 세션 query의 오류로 남는다 — 스트림이 다시 열리지는 않는다.
      }
    };

    const read = async (): Promise<void> => {
      const { stream } = await openAssistantEventStream({
        sessionId,
        afterSequence: lastSequenceRef.current,
        signal: controller.signal,
        maxRetryAttempts,
        retryDelayMs,
        onRejected: (value) => {
          rejection = value;
        },
        onAttempt: (ok) => {
          lastAttemptOk = ok;
        },
      });
      try {
        for await (const frame of stream) {
          if (controller.signal.aborted) return;
          const envelope = assistantEventEnvelope(frame);
          // 좁히지 못한 프레임은 버린다. keepalive 주석은 여기까지 오지 않는다.
          if (envelope !== null) onEventRef.current(envelope);
        }
      } catch {
        // 생성 클라이언트는 상한을 넘긴 뒤에도 던지지 않지만, 예기치 못한 예외도 이력 복구로 떨어뜨린다.
      }
      if (controller.signal.aborted) return;
      const close: AssistantStreamClose =
        rejection !== null
          ? { reason: "rejected", rejection }
          : lastAttemptOk
            ? { reason: "ended" }
            : { reason: "exhausted" };
      onCloseRef.current?.(close);
      await recover();
    };

    void read();
    return () => controller.abort();
    // 콜백·lastSequence는 committed ref로 읽으므로 스트림을 다시 열 이유가 아니다.
  }, [
    sessionId,
    turnId,
    maxRetryAttempts,
    retryDelayMs,
    queryClient,
    lastSequenceRef,
    onEventRef,
    onHistoryRef,
    onCloseRef,
  ]);
};
