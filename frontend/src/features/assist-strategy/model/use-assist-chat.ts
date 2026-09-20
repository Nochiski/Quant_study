import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";

import {
  AssistantRequestError,
  assistantChatReducer,
  assistantSessionKey,
  assistantSessionQuery,
  assistantSessionsQuery,
  assistantStreamTarget,
  emptyAssistantChatState,
  runningAssistantTurn,
  useAssistantEventStream,
  useCancelAssistantTurn,
  useCreateAssistantSession,
  useStartAssistantTurn,
  type AssistantEventEnvelopeView,
  type AssistantStreamClose,
  type AssistantStreamStatus,
  type AssistantTurnState,
  type DocumentRefView,
  type SessionHistoryView,
  type SessionView,
  type TurnContextPayload,
} from "../../../entities/assistant";
import { TURN_IN_PROGRESS, assistRejection } from "./assist-copy";
import { assistTranscript, type AssistTranscriptEntry } from "./transcript";

/**
 * 어느 세션을 보고 있는가. `auto`는 "이 문서의 가장 최근 대화"라는 뜻이고, 사용자가 고른 뒤에는
 * 목록이 갱신되어도 그 선택이 남는다. `new`는 아직 서버에 없는 대화다 — 첫 전송에서 만든다.
 */
type Selection =
  { kind: "auto" } | { kind: "new" } | { kind: "session"; id: string };

/** 턴 시작 시점에 실어 보낸 질문과 문서 원문. 원문은 제안 적용 전 확인의 기준이다(spec D9). */
type TurnPrompt = { text: string; sourceText: string };

export type UseAssistChatOptions = {
  documentRef: DocumentRefView;
  /** 턴을 시작하는 순간의 편집기 상태. 값이 아니라 함수인 이유는 타이핑마다 사이드바를 다시 그리지 않기 위함이다. */
  readContext: () => TurnContextPayload;
};

export type AssistChat = {
  sessions: readonly SessionView[];
  sessionId: string | null;
  selectSession: (sessionId: string) => void;
  startNewSession: () => void;
  entries: readonly AssistTranscriptEntry[];
  /** 서버가 아직 턴 id를 주지 않은 질문. 도착하면 `entries`로 옮겨 간다. */
  pendingPrompt: string | null;
  running: AssistantTurnState | null;
  busy: boolean;
  rejection: string | null;
  historyFailed: boolean;
  /** 이벤트 스트림의 현재 상태. `exhausted`면 화면이 다시 연결을 권한다(spec D7, B-02 리뷰 P1-1). */
  streamStatus: AssistantStreamStatus;
  /** 상한을 소진한 스트림을 다시 연다. 되살릴지는 화면이 정한다. */
  retryStream: () => void;
  /** 이 대화를 보는 동안 갈래를 좁히지 못해 버린 프레임 수. 0이 아니면 서버와 계약이 어긋났다. */
  droppedFrames: number;
  baseSourceText: (turnId: string) => string | null;
  send: (text: string) => void;
  cancel: () => void;
};

/** 세션 제목은 첫 질문의 한 줄로 만든다. 서버는 빈 제목도 받지만 목록이 구분되지 않는다. */
const sessionTitle = (text: string): string => {
  const line = text.split("\n", 1)[0].trim();
  return line.length > 40 ? `${line.slice(0, 40)}…` : line;
};

/**
 * 사이드바 한 벌의 상태를 조립한다.
 *
 * 서버 이력의 owner는 query cache이고 이 훅이 드는 것은 그 위의 투영(리듀서)과 UI 선택뿐이다
 * (spec D9). 스트림을 여닫는 판단은 투영에서 뽑은 `assistantStreamTarget`이 한다.
 */
export const useAssistChat = ({
  documentRef,
  readContext,
}: UseAssistChatOptions): AssistChat => {
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<Selection>({ kind: "auto" });
  const [prompts, setPrompts] = useState<Record<string, TurnPrompt>>({});
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const [droppedFrames, setDroppedFrames] = useState(0);
  const [state, dispatch] = useReducer(
    assistantChatReducer,
    emptyAssistantChatState,
  );

  const sessions = useQuery(assistantSessionsQuery(documentRef));
  const list = useMemo(() => sessions.data ?? [], [sessions.data]);
  // 목록은 오래된 대화부터 온다 — 문서를 다시 열면 마지막 대화를 이어 본다.
  const sessionId =
    selection.kind === "session"
      ? selection.id
      : selection.kind === "new"
        ? null
        : (list.at(-1)?.session_id ?? null);

  // 아직 세션이 없으면 조회하지 않는다. 빈 id는 `enabled: false`인 동안 쓰이지 않는 자리 표시다.
  const history = useQuery({
    ...assistantSessionQuery(sessionId ?? ""),
    enabled: sessionId !== null,
  });

  /**
   * 보는 대화를 바꾼다. 선택과 투영 비우기를 같은 호출에서 한다.
   *
   * effect로 미루면 그 사이에 들어온 이벤트·턴이 reset에 쓸려 나간다 — 세션을 만든 직후의 턴 시작
   * 응답이 같은 배치에 담기면 새 세션의 첫 턴이 사라졌다.
   */
  const openSession = (next: string | null) => {
    setSelection(
      next === null ? { kind: "new" } : { kind: "session", id: next },
    );
    dispatch({ type: "session", sessionId: next });
  };

  /** 사용자가 대화를 바꿀 때. 앞 대화에 매달린 질문과 거부 문구를 함께 버린다. */
  const switchSession = (next: string | null) => {
    setPendingPrompt(null);
    setRejection(null);
    setDroppedFrames(0);
    openSession(next);
  };

  // 목록에서 저절로 고른 대화(auto)만 여기로 온다. 이미 그 세션이면 리듀서가 아무 것도 하지 않는다.
  useEffect(() => {
    dispatch({ type: "session", sessionId });
  }, [sessionId]);

  useEffect(() => {
    if (history.data !== undefined) {
      dispatch({ type: "history", history: history.data });
    }
  }, [history.data]);

  const onEvent = useCallback(
    (envelope: AssistantEventEnvelopeView) =>
      dispatch({ type: "event", envelope }),
    [],
  );
  const onHistory = useCallback(
    (value: SessionHistoryView) =>
      dispatch({ type: "history", history: value }),
    [],
  );

  /**
   * 스트림이 닫힌 사정 중 화면이 쥐는 것은 버린 프레임 수뿐이다.
   *
   * 사유(`exhausted`·`rejected`)는 훅의 `status`가 이미 들고 있고, 턴의 최종 상태는 이력이 정한다 —
   * 여기서 다시 세면 같은 사실이 두 곳에 산다.
   */
  const onClose = useCallback((close: AssistantStreamClose) => {
    if (close.droppedFrames > 0) {
      setDroppedFrames((previous) => previous + close.droppedFrames);
    }
  }, []);

  const { status: streamStatus, retry: retryStream } = useAssistantEventStream({
    target: assistantStreamTarget(state),
    lastSequence: state.lastSequence,
    onEvent,
    onHistory,
    onClose,
  });

  const createSession = useCreateAssistantSession();
  const startTurn = useStartAssistantTurn();
  const cancelTurn = useCancelAssistantTurn();
  const running = runningAssistantTurn(state);
  const busy = createSession.isPending || startTurn.isPending;

  const promptTexts = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(prompts).map(([turnId, prompt]) => [
          turnId,
          prompt.text,
        ]),
      ),
    [prompts],
  );
  const entries = useMemo(
    () => assistTranscript(state, promptTexts),
    [state, promptTexts],
  );

  const beginTurn = (id: string, text: string) => {
    const context = readContext();
    startTurn.mutate(
      { sessionId: id, request: { text, context } },
      {
        onSuccess: (turn) => {
          setPrompts((previous) => ({
            ...previous,
            [turn.turn_id]: { text, sourceText: context.source_text },
          }));
          setPendingPrompt(null);
          dispatch({ type: "turn", turn });
        },
        onError: (error) => {
          setPendingPrompt(null);
          // 이미 도는 턴이 있다는 답은 배너가 아니라 이력으로 따라잡을 신호다(spec D7).
          if (
            error instanceof AssistantRequestError &&
            error.code === TURN_IN_PROGRESS
          ) {
            void queryClient.invalidateQueries({
              queryKey: assistantSessionKey(id),
            });
            return;
          }
          setRejection(assistRejection(error));
        },
      },
    );
  };

  const send = (text: string) => {
    const trimmed = text.trim();
    if (trimmed === "" || running !== null || busy) return;
    setRejection(null);
    setPendingPrompt(trimmed);
    if (sessionId !== null) {
      beginTurn(sessionId, trimmed);
      return;
    }
    createSession.mutate(
      { document_ref: documentRef, title: sessionTitle(trimmed) },
      {
        onSuccess: (created) => {
          openSession(created.session_id);
          beginTurn(created.session_id, trimmed);
        },
        onError: (error) => {
          setPendingPrompt(null);
          setRejection(assistRejection(error));
        },
      },
    );
  };

  const cancel = () => {
    if (sessionId === null || running === null) return;
    cancelTurn.mutate(
      { sessionId, turnId: running.turnId },
      {
        onSuccess: (turn) => dispatch({ type: "turn", turn }),
        onError: (error) => setRejection(assistRejection(error)),
      },
    );
  };

  return {
    sessions: list,
    sessionId,
    selectSession: switchSession,
    startNewSession: () => switchSession(null),
    entries,
    pendingPrompt,
    running,
    busy,
    rejection,
    historyFailed: history.isError,
    streamStatus,
    retryStream,
    droppedFrames,
    baseSourceText: (turnId: string) => prompts[turnId]?.sourceText ?? null,
    send,
    cancel,
  };
};
