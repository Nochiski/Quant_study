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
   * 직전 세션의 마지막 번호다). 이력 없이 연 경우에는 번호가 그보다 앞이라 서버가 그 턴의 앞부분을
   * 다시 흘리는데, 아직 반영한 적이 없으므로 그대로 적용된다 — 흘려보내는 것보다 안전하다. 중복이
   * 실제로 겹치는 자리는 이력 병합이고, 거기서는 턴별 watermark가 막는다.
   */
  lastSequence: number;
  onEvent: (envelope: AssistantEventEnvelopeView) => void;
  /** 스트림이 닫힌 뒤 이력으로 확정한 결과. 턴의 최종 상태는 여기서만 온다(spec D3). */
  onHistory: (history: SessionHistoryView) => void;
  /**
   * 스트림이 닫히고 **이력 복구까지 끝난 뒤** 온다(`turnStatus`·`recovered`를 채우려면 그 순서여야
   * 한다). 그 사이에 대상이 바뀌거나 언마운트되면 오지 않으므로 자원 정리를 여기에 걸지 않는다 —
   * 정리는 `status`와 effect cleanup의 몫이다(리뷰 R2-6).
   */
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
   *
   * `status`가 `open`일 때 부르면 **살아 있는 연결을 끊고 처음부터 다시 연다.** 보통은 `status`가
   * 종료 사유(`exhausted`가 대표)일 때만 부른다(리뷰 R2-7).
   */
  retry: () => void;
};

/** 좁히지 못한 프레임을 로그에 남길 때 쓰는 최소 단서. 본문은 싣지 않는다. */
const frameLabel = (frame: unknown): { type: string; text: string } => {
  if (typeof frame !== "object" || frame === null) {
    return { type: typeof frame, text: `frame=${typeof frame}` };
  }
  const sequence = "sequence" in frame ? String(frame.sequence) : "-";
  const event = "event" in frame ? frame.event : null;
  const type =
    typeof event === "object" && event !== null && "type" in event
      ? String(event.type)
      : "-";
  return { type, text: `sequence=${sequence} type=${type}` };
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
  /**
   * 연결을 다시 여는 입력. 이 문자열이 바뀌면 새 스트림이고, 앞 연결의 종료 사유는 버려야 한다.
   *
   * 종료 사유를 이 입력에 매달면 안 된다 — 같은 조합이 다시 나타나기 때문이다(진행 중 턴을 두고
   * 세션을 떠났다 돌아오면 `attempt`가 0인 채로 같은 세션·턴이 다시 선다). 그래서 사유는 아래
   * **단조 증가하는 실행 번호**에 매단다(리뷰 R2-1).
   */
  const streamInputs = `${sessionId ?? "-"}:${turnId ?? "-"}:${attempt}:${maxRetryAttempts}:${retryDelayMs ?? "-"}`;
  const [openedFor, setOpenedFor] = useState(streamInputs);
  const [run, setRun] = useState(0);
  const [closed, setClosed] = useState<{
    run: number;
    reason: AssistantStreamCloseReason;
  } | null>(null);

  if (openedFor !== streamInputs) {
    // 렌더 중 상태 조정 — React가 "입력이 바뀔 때 상태를 버리는" 자리로 권하는 형태다. effect로
    // 미루면 새 연결이 열린 렌더에서 옛 사유가 한 번 보이고, effect 본문의 setState는 lint가 막는다.
    setOpenedFor(streamInputs);
    setRun(run + 1);
    setClosed(null);
  }
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
    // 좁히지 못한 갈래는 처음 볼 때만 남긴다. 서버가 늘린 갈래가 토큰 단위로 오면 한 턴에 수백 줄이
    // 쌓여 정작 봐야 할 경고가 묻힌다 — 개수는 `droppedFrames`가 전한다(리뷰 R2-5).
    const warnedFrameTypes = new Set<string>();

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
            const label = frameLabel(frame);
            if (!warnedFrameTypes.has(label.type)) {
              warnedFrameTypes.add(label.type);
              console.warn(
                `어시스턴트 SSE 프레임을 좁히지 못해 버린다 — session_id=${sessionId} ${label.text}`,
              );
            }
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
      setClosed({ run, reason });
    };

    void read();
    return () => controller.abort();
    // 콜백·lastSequence는 committed ref로 읽으므로 스트림을 다시 열 이유가 아니다. `attempt`는
    // 화면이 `retry()`로 올리는 재연결 손잡이다.
  }, [
    run,
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

  const status: AssistantStreamStatus =
    sessionId === null || turnId === null
      ? "idle"
      : closed !== null && closed.run === run
        ? closed.reason
        : "open";

  return { status, retry };
};
