import { useCallback, useState } from "react";

import type { DocumentRefView, TurnContextPayload } from "../../../entities/assistant";
import { useAssistChat, type AssistChat } from "./use-assist-chat";

export type UseAssistSidebarStateOptions = {
  documentRef: DocumentRefView;
  readContext?: () => TurnContextPayload;
  /** 패널이 보이는가(`useAssistChat`의 `enabled`). */
  enabled?: boolean;
};

/**
 * 사이드바 한 벌의 대화·입력칸 상태. 사이드바가 스스로 만들 수도 있고, 같은 문서를 두 자리(새 전략 안내의 AI 탭과
 * 편집기 AI 열)가 번갈아 보이는 화면은 위쪽에서 한 번 만들어 두 사이드바에 넘긴다 — 자리를 옮겨도 치던 문장과
 * 진행 중 답변이 이어진다(가이드 재리뷰 4).
 */
export type AssistSidebarState = {
  chat: AssistChat;
  /** 입력칸 문장. 되돌아온 질문·추천 질문은 여기서 한 번만 소비된다(가이드 리뷰 3). */
  draft: string;
  setDraft: (text: string) => void;
  /** 추천 질문을 넣는다. 보내지 않는다. */
  pickSuggestion: (suggestion: string) => void;
  /**
   * 추천 질문이 속한 맥락(안내 단계 id)을 알린다. 바뀌면 고치지 않은 채 남은 앞 맥락의 추천 질문을 입력칸에서
   * 지운다. 맥락은 그리는 자리(사이드바)가 알므로 그 자리가 commit 때 부른다. identity 는 바뀌지 않는다.
   */
  noteSuggestionsKey: (key: string | undefined) => void;
  /** 입력칸 문장을 보낸다. 보낼 수 없으면 아무것도 하지 않는다. */
  submitDraft: () => void;
};

type Composer = {
  draft: string;
  /** 마지막으로 넣은 추천 질문. 입력칸이 아직 그 문장 그대로면 사용자가 쓴 글이 아니다. */
  suggested: string | null;
  seenSuggestionsKey: string | undefined;
};

const EMPTY: Composer = { draft: "", suggested: null, seenSuggestionsKey: undefined };

export const useAssistSidebarState = ({
  documentRef,
  readContext,
  enabled = true,
}: UseAssistSidebarStateOptions): AssistSidebarState => {
  const chat = useAssistChat({ documentRef, readContext, enabled });
  const [composer, setComposer] = useState<Composer>(EMPTY);
  const [restoredNonce, setRestoredNonce] = useState<number | null>(null);

  // 되돌아온 질문을 렌더 중에 입력칸으로 옮긴다. effect로 미루면 빈 칸이 한 프레임 보이고,
  // 그 사이 타이핑은 다음 렌더에 덮인다(`frontend-react-effects.md`).
  //
  // 왕복이 도는 동안 사용자가 이미 다른 문장을 치고 있으면 덮지 않는다 — 되돌려 받는 문장보다
  // 지금 치고 있는 문장이 새 것이다. 보내지 못한 질문은 배너가 알린다(B-03 2차 리뷰 P3).
  if (chat.draftRestore !== null && chat.draftRestore.nonce !== restoredNonce) {
    setRestoredNonce(chat.draftRestore.nonce);
    if (composer.draft.trim() === "")
      setComposer({ ...composer, draft: chat.draftRestore.text, suggested: null });
  }

  // 맥락(안내 단계)이 바뀌면 고치지 않은 앞 단계 추천 질문을 지운다. 사용자가 고친 문장은 남긴다(가이드 리뷰 6).
  const noteSuggestionsKey = useCallback((key: string | undefined) => {
    setComposer((previous) =>
      previous.seenSuggestionsKey === key
        ? previous
        : {
            draft:
              previous.suggested !== null && previous.draft === previous.suggested
                ? ""
                : previous.draft,
            suggested: null,
            seenSuggestionsKey: key,
          },
    );
  }, []);

  /**
   * 사용자가 직접 쓴 미전송 문장이 있으면 덮지 않고 줄을 바꿔 뒤에 잇는다 — 쓴 글을 경고 없이 지우면 되돌릴 길이
   * 없고, 확인 대화상자는 칩 한 번의 가벼운 동작에 비해 무겁다. 입력칸이 앞서 넣은 추천 질문 그대로이면 사용자의
   * 글이 아니므로 바꿔 넣는다(가이드 리뷰 4).
   */
  const pickSuggestion = (suggestion: string) => {
    const userText = composer.draft.trim() !== "" && composer.draft !== composer.suggested;
    setComposer({
      ...composer,
      draft: userText ? `${composer.draft.trimEnd()}\n${suggestion}` : suggestion,
      suggested: userText ? null : suggestion,
    });
  };

  const submitDraft = () => {
    if (chat.running !== null || chat.busy || composer.draft.trim() === "") return;
    chat.send(composer.draft);
    setComposer({ ...composer, draft: "", suggested: null });
  };

  return {
    chat,
    draft: composer.draft,
    setDraft: (text) => setComposer((previous) => ({ ...previous, draft: text })),
    pickSuggestion,
    noteSuggestionsKey,
    submitDraft,
  };
};
