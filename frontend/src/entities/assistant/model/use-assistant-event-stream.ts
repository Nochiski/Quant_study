import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import {
  ASSISTANT_SSE_MAX_RETRY_ATTEMPTS,
  assistantEventEnvelope,
  openAssistantEventStream,
  type AssistantEventEnvelopeView,
  type AssistantStreamRejection,
  type SessionHistoryView,
  type TurnStatus,
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

export type AssistantStreamCloseReason =
  /** 서버가 스트림을 끝까지 흘리고 닫았다. */
  | "ended"
  /** 서버가 열어 주지 않았다 — 409 `no_running_turn`이 대표다. */
  | "rejected"
  /** 연결 시도 상한을 다 썼다. */
  | "exhausted";

/** 스트림이 닫힌 사정. 화면이 "다시 연결"을 권할지 판단하는 데 필요한 것만 싣는다. */
export type AssistantStreamClose = {
  reason: AssistantStreamCloseReason;
  rejection: AssistantStreamRejection | null;
  /**
   * 닫은 뒤 이력으로 확인한 그 턴의 상태. 복구에 실패했거나 이력에 없으면 null이다.
   *
   * 이 값이 `running`인데 사유가 `exhausted`면 턴은 서버에서 계속 도는데 화면만 연결을 잃은
   * 것이다 — 화면이 `retry()`를 권할 자리다(리뷰 P1-1).
   */
  turnStatus: TurnStatus | null;
  /** 이력 복구 성공 여부. false면 화면이 턴 상태를 갱신하지 못했다는 뜻이다. */
  recovered: boolean;
  /** 좁히지 못해 버린 프레임 수. 0이 아니면 서버와 이벤트 갈래 계약이 어긋났다는 신호다. */
  droppedFrames: number;
};

export type AssistantStreamStatus =
  /** 열 대상이 없다. */
  | "idle"
  /** 연결을 붙잡고 있다(재연결 대기 포함). */
  | "open"
  | AssistantStreamCloseReason;

export type UseAssistantEventStreamOptions = {
  target: AssistantStreamTarget | null;
  /**
   * 이미 반영한 마지막 sequence. 최초 연결의 `after_sequence`로 나간다.
   *
   * 이력을 읽고 연 사이드바에서는 이 값이 턴 시작 응답의 `accepted_sequence`와 같다(둘 다 턴
   * 직전 세션의 마지막 번호다). 이력 없이 연 경우에는 더 앞이라 서버가 이미 반영한 프레임을 몇 개
   * 다시 보내는데, 리듀서가 턴별 watermark로 무시한다.
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

export type UseAssistantEventStreamResult = {
  status: AssistantStreamStatus;
  /**
   * 같은 대상의 스트림을 다시 연다.
   *
   * 훅은 상한을 소진한 뒤 스스로 되살아나지 않는다. 되살릴지는 화면이 정한다 — 턴이 아직
   * `running`인지, 사용자가 사이드바를 보고 있는지는 화면만 안다. 훅이 무한히 다시 열면 서버가
   * 죽은 동안 탭이 조용히 재연결을 반복한다(리뷰 P1-1).
   */
  retry: () => void;
};

/** 좁히지 못한 프레임을 로그에 남길 때 쓰는 최소 단서. 본문은 싣지 않는다. */
const frameLabel = (frame: unknown): string => {
  if (typeof frame !== "object" || frame === null) return `frame=${typeof frame}`;
  const sequence = "sequence" in frame ? String(frame.sequence) : "-";
  const event = "event" in frame ? frame.event : null;
  const type =
    typeof event === "object" && event !== null && "type" in event
      ? String(event.type)
      : "-";
  return `sequence=${sequence} type=${type}`;
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
 * - 상한을 소진하면 멈추고 `status`를 `exhausted`로 둔다. 다시 여는 것은 `retry()`뿐이다.
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
}: UseAssistantEventStreamOptions): UseAssistantEventStreamResult => {
  const queryClient = useQueryClient();
  const sessionId = target?.sessionId ?? null;
  const turnId = target?.turnId ?? null;
  const [attempt, setAttempt] = useState(0);
  // 닫힌 사유는 스트림 한 번(세션·턴·재시도 횟수)에 매인다. 상태를 effect 본문에서 동기로 쓰지 않고
  // 이 값에서 파생하면 연결이 바뀔 때 옛 사유가 남지 않는다(`react-hooks/set-state-in-effect`).
  const streamKey = `${sessionId ?? "-"}:${turnId ?? "-"}:${attempt}`;
  const [closed, setClosed] = useState<{
    streamKey: string;
    reason: AssistantStreamCloseReason;
  } | null>(null);
  const lastSequenceRef = useCommittedRef(lastSequence);
  const onEventRef = useCommittedRef(onEvent);
  const onHistoryRef = useCommittedRef(onHistory);
  const onCloseRef = useCommittedRef(onClose);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    if (sessionId === null || turnId === null) return;
    const controller = new AbortController();
    let rejection: AssistantStreamRejection | null = null;
    let lastOutcomeOk = false;
    let droppedFrames = 0;

    const recover = async (): Promise<SessionHistoryView | null> => {
      try {
        const history = await queryClient.fetchQuery(
          assistantSessionQuery(sessionId),
        );
        if (!controller.signal.aborted) onHistoryRef.current(history);
        return history;
      } catch (error) {
        // 침묵하면 사이드바가 이유 없이 멈춘 것으로 보인다 — 조치할 사람에게 단서를 남긴다.
        console.warn(
          `어시스턴트 이력 복구 실패 — session_id=${sessionId} turn_id=${turnId}`,
          error,
        );
        return null;
      }
    };

    const read = async (): Promise<void> => {
      try {
        const { stream } = await openAssistantEventStream({
          sessionId,
          afterSequence: lastSequenceRef.current,
          signal: controller.signal,
          maxRetryAttempts,
          retryDelayMs,
          onRejected: (value) => {
            rejection = value;
          },
          onStreamOutcome: (ok) => {
            lastOutcomeOk = ok;
          },
        });
        for await (const frame of stream) {
          if (controller.signal.aborted) return;
          const envelope = assistantEventEnvelope(frame);
          if (envelope === null) {
            // 버리는 것 자체는 옳다(누적 텍스트에 `undefined`가 섞이는 것보다 낫다). 다만 서버가
            // 갈래를 늘렸을 때 화면이 조용히 비어 가는 것을 막으려면 흔적이 필요하다.
            droppedFrames += 1;
            console.warn(
              `어시스턴트 SSE 프레임을 좁히지 못해 버린다 — session_id=${sessionId} ${frameLabel(frame)}`,
            );
            continue;
          }
          onEventRef.current(envelope);
        }
      } catch (error) {
        // 생성 클라이언트는 상한을 넘긴 뒤에도 던지지 않지만, 예기치 못한 예외도 이력 복구로 떨어뜨린다.
        console.warn(
          `어시스턴트 SSE 읽기가 예외로 끝났다 — session_id=${sessionId} turn_id=${turnId}`,
          error,
        );
      }
      if (controller.signal.aborted) return;
      const reason: AssistantStreamCloseReason =
        rejection !== null ? "rejected" : lastOutcomeOk ? "ended" : "exhausted";
      const history = await recover();
      if (controller.signal.aborted) return;
      onCloseRef.current?.({
        reason,
        rejection,
        turnStatus:
          history?.turns.find((turn) => turn.turn_id === turnId)?.status ?? null,
        recovered: history !== null,
        droppedFrames,
      });
      setClosed({ streamKey, reason });
    };

    void read();
    return () => controller.abort();
    // 콜백·lastSequence는 committed ref로 읽으므로 스트림을 다시 열 이유가 아니다. `attempt`는
    // 화면이 `retry()`로 올리는 재연결 손잡이다.
  }, [
    sessionId,
    turnId,
    streamKey,
    maxRetryAttempts,
    retryDelayMs,
    queryClient,
    lastSequenceRef,
    onEventRef,
    onHistoryRef,
    onCloseRef,
  ]);

  const status: AssistantStreamStatus =
    sessionId === null || turnId === null
      ? "idle"
      : closed !== null && closed.streamKey === streamKey
        ? closed.reason
        : "open";

  return { status, retry };
};
