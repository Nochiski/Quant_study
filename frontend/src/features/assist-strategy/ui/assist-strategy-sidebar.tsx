import { useId, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  assistantProvidersQuery,
  hasActiveAssistantProvider,
  type DocumentRefView,
  type StrategyProposalView,
  type TurnContextPayload,
} from "../../../entities/assistant";
import { t, type MessageKey } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Button, EmptyState } from "../../../shared/ui";
import {
  useAssistSidebarState,
  type AssistSidebarState,
} from "../model/use-assist-sidebar";
import { AssistComposer } from "./assist-composer";
import { AssistTranscript } from "./assist-transcript";
import "./assist-strategy-sidebar.css";

/**
 * 제안 하나를 문서에 쓰려는 요청.
 *
 * `baseSourceText`는 그 턴이 시작될 때 모델이 본 원문이다. 적용 시점 텍스트와 다르면 바로 덮어쓰지
 * 않고 확인을 받아야 하므로(spec D7) 적용을 맡는 쪽에 함께 넘긴다.
 */
export type AssistProposalAction = {
  proposal: StrategyProposalView;
  baseSourceText: string | null;
};

/**
 * 사이드바가 붙은 화면의 종류. 안내 문구만 가른다.
 *
 * `result`는 백테스트 결과 화면이다(결과 설명 spec R1). 그 화면에서 "무엇을 만들고 싶은지 적으세요"는
 * 틀린 안내다. `guide`는 새 전략 단계별 안내로, 아직 문서가 비어 있어 무슨 전략을 만들지부터 묻는다.
 * 모드 자체(도구·제안 여부)는 서버가 세션으로 정하므로 여기서 다시 판단하지 않는다.
 */
export type AssistCopy = "strategy" | "guide" | "result";

const COPY = {
  strategy: {
    empty: "assistant.chat.empty.description",
    placeholder: "assistant.chat.input.placeholder",
  },
  guide: {
    empty: "assistant.chat.empty.description.guide",
    placeholder: "assistant.chat.input.placeholder.guide",
  },
  result: {
    empty: "assistant.chat.empty.description.result",
    placeholder: "assistant.chat.input.placeholder.result",
  },
} as const satisfies Record<
  AssistCopy,
  { empty: MessageKey; placeholder: MessageKey }
>;

/**
 * 붙는 화면에 따라 갈리는 props(D 스택 리뷰 P3-4).
 *
 * 전략 화면은 편집기 컨텍스트가 **필수**다. 빠뜨리면 첫 질문에서야 422
 * `assistant.turn_context_mismatch`로 드러나므로 타입이 먼저 막는다. 결과 화면(`copy: "result"`)은
 * 서버가 실행 결과를 직접 읽어서 컨텍스트를 받지 않는다(결과 설명 spec R2·R5).
 */
type AssistScreenProps =
  | {
      /** 안내 문구의 종류. 기본은 전략 화면이다. */
      copy?: "strategy" | "guide";
      /** 턴 시작 순간의 편집기 텍스트·진단·실행 설정. 서버가 문서를 들지 않으므로 턴마다 싣는다. */
      readContext: () => TurnContextPayload;
    }
  | { copy: "result"; readContext?: never };

export type AssistStrategySidebarProps = AssistScreenProps & {
  /** 이 사이드바가 붙은 문서(또는 백테스트 실행). 세션 목록의 범위다. */
  documentRef: DocumentRefView;
  onPreviewProposal?: (action: AssistProposalAction) => void;
  onApplyProposal?: (action: AssistProposalAction) => void;
  onApplyProposalAndBacktest?: (action: AssistProposalAction) => void;
  /** 사이드바를 닫는 동작. 주면 닫기 버튼이 생긴다. */
  onClose?: () => void;
  /**
   * 입력칸 위에 두는 추천 질문. 누르면 입력칸을 그 문장으로 채울 뿐 보내지 않는다 — 사용자가 고쳐 보낼
   * 수 있어야 한다. 화면이 지금 맥락(안내 단계)에 맞춰 고른다.
   */
  suggestions?: readonly string[];
  /**
   * 추천 질문이 속한 맥락(안내 단계 id). 바뀌면 고치지 않은 채 남은 앞 맥락의 추천 질문을 입력칸에서 지운다.
   */
  suggestionsKey?: string;
  /**
   * 붙는 자리. `rail`은 전략 편집기의 세로 레일, `docked`는 고정 높이 칸 안에 들어가는 자리(새 전략 안내의
   * 오른쪽 탭)로, 대화가 비어 있어도 추천 질문과 입력칸을 칸 바닥에 붙인다.
   */
  layout?: "rail" | "docked";
  /**
   * 위쪽에서 만든 대화·입력칸 상태(`useAssistSidebarState`). 주면 사이드바는 그리기만 한다.
   */
  state?: AssistSidebarState;
};

/**
 * 전략 화면 우측의 AI 채팅 사이드바.
 *
 * 그래프·YAML 어느 탭에서 보든 같은 세션이며, 문서를 바꾸는 것은 제안 카드의 버튼이 부르는 콜백뿐이다
 * (적용 경로는 spec D7에 따라 바깥이 소유한다). 서버 상태는 전부 query cache가 들고, 대화·입력칸 상태는
 * `useAssistSidebarState`가 든다. `state`를 주지 않으면 사이드바가 스스로 만들고, 주면 그 상태를 그린다 —
 * 같은 문서를 두 자리가 번갈아 보이는 화면이 대화·입력을 잇는 길이다(가이드 재리뷰 4).
 */
export const AssistStrategySidebar = (props: AssistStrategySidebarProps) =>
  props.state === undefined ? (
    <OwnedSidebar {...props} />
  ) : (
    <SidebarView {...props} state={props.state} />
  );

const OwnedSidebar = (props: AssistStrategySidebarProps) => {
  const state = useAssistSidebarState({
    documentRef: props.documentRef,
    readContext: props.readContext,
  });
  return <SidebarView {...props} state={state} />;
};

const SidebarView = ({
  copy = "strategy",
  onPreviewProposal,
  onApplyProposal,
  onApplyProposalAndBacktest,
  onClose,
  suggestions = [],
  suggestionsKey,
  layout = "rail",
  state,
}: AssistStrategySidebarProps & { state: AssistSidebarState }) => {
  // 추천 질문의 맥락은 그리는 자리가 안다(안내라면 안내가 해석한 지금 단계, 최종 리뷰 4). commit 때 알린다.
  const { noteSuggestionsKey } = state;
  useLayoutEffect(() => {
    noteSuggestionsKey(suggestionsKey);
  }, [noteSuggestionsKey, suggestionsKey]);
  const confirmTextId = useId();
  const suggestionsId = useId();
  const providers = useQuery(assistantProvidersQuery());
  const { chat, draft, setDraft, submitDraft } = state;
  const [closeConfirm, setCloseConfirm] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const pickSuggestion = (suggestion: string) => {
    state.pickSuggestion(suggestion);
    inputRef.current?.focus();
  };

  const hasActiveProvider =
    providers.data === undefined ? false : hasActiveAssistantProvider(providers.data);

  const proposalAction = (
    proposal: StrategyProposalView,
    turnId: string,
  ): AssistProposalAction => ({
    proposal,
    baseSourceText: chat.baseSourceText(turnId),
  });

  const requestClose = () => {
    if (onClose === undefined) return;
    if (chat.running === null) {
      onClose();
      return;
    }
    setCloseConfirm(true);
  };

  /** 확인을 물리면 초점을 부른 자리로 되돌린다 — 대화상자가 사라지며 body로 떨어지지 않게. */
  const dismissConfirm = () => {
    setCloseConfirm(false);
    closeButtonRef.current?.focus();
  };

  const cancelAndClose = () => {
    chat.cancel();
    setCloseConfirm(false);
    onClose?.();
  };

  /**
   * 진행 상태를 한 번만 알리는 문구. 스트리밍 본문은 라이브 영역 밖이다(B-03 리뷰 P2).
   *
   * 종결 종류를 가른다. 사용자가 스스로 중지시킨 답변을 "완료"라고 선언하면 바로 위 말풍선의
   * 취소 문구와 모순되고, 본문이 `aria-live="off"`라 스크린리더에는 그 모순된 "완료"만 남는다
   * (2차 리뷰 P1). 실패는 말풍선의 실패 문구가 이미 알리므로 여기서 같은 말을 또 하지 않는다.
   * 다음 턴이 시작되면 진행 문구가 이 자리를 덮는다.
   */
  const announcement =
    chat.running !== null
      ? t("assistant.chat.running")
      : chat.finishedTurn === null
        ? null
        : chat.finishedTurn.status === "cancelled"
          ? t("assistant.chat.stopped")
          : chat.finishedTurn.failure !== null ||
              chat.finishedTurn.status !== "completed"
            ? null
            : t(
                chat.finishedTurn.proposal === null
                  ? "assistant.chat.finished"
                  : "assistant.chat.finished.proposal",
              );

  return (
    // 이름 없는 `<section>`은 landmark로 노출되지 않는다. 패널의 landmark·이름·제목은 이 feature를
    // 꽂는 슬롯이 소유하고 여기서는 그리지 않는다 — 계약은 WORKFLOW B-04 Acceptance에 있다.
    <section className={layout === "docked" ? "assist assist--docked" : "assist"}>
      <header className="assist__head">
        <div className="assist__head-actions">
          {chat.sessions.length === 0 ? null : (
            <select
              className="assist__sessions ui-select"
              aria-label={t("assistant.chat.session")}
              value={chat.sessionId ?? ""}
              onChange={(event) =>
                // 빈 값은 "아직 서버에 없는 대화"다. 세션 id로 넘기면 빈 id를 조회한다.
                event.target.value === ""
                  ? chat.startNewSession()
                  : chat.selectSession(event.target.value)
              }
            >
              {chat.sessionId === null ? (
                <option value="">{t("assistant.chat.session.new")}</option>
              ) : null}
              {chat.sessions.map((session) => (
                <option key={session.session_id} value={session.session_id}>
                  {session.title}
                </option>
              ))}
            </select>
          )}
          <Button size="small" onClick={chat.startNewSession}>
            {t("assistant.chat.session.new")}
          </Button>
          {onClose === undefined ? null : (
            <Button
              ref={closeButtonRef}
              size="small"
              tone="ghost"
              aria-label={t("assistant.chat.close")}
              onClick={requestClose}
            >
              ✕
            </Button>
          )}
        </div>
      </header>

      {closeConfirm ? (
        <div
          className="assist__confirm"
          role="alertdialog"
          aria-label={t("assistant.chat.close")}
          aria-describedby={confirmTextId}
          onKeyDown={(event) => {
            if (event.key === "Escape") dismissConfirm();
          }}
        >
          <p className="assist__confirm-text" id={confirmTextId}>
            {t("assistant.chat.close.confirm")}
          </p>
          <div className="assist__confirm-actions">
            <Button size="small" tone="danger" onClick={cancelAndClose}>
              {t("assistant.chat.close.confirm.submit")}
            </Button>
            {/* 진행 중 답변을 버리지 않는 쪽에 처음 초점을 둔다 — Enter가 실수로 취소를 부르지 않는다. */}
            <Button
              size="small"
              tone="ghost"
              autoFocus
              onClick={dismissConfirm}
            >
              {t("assistant.chat.close.confirm.cancel")}
            </Button>
          </div>
        </div>
      ) : null}

      {providers.isPending ? (
        <p className="assist__notice" role="status">
          {t("page.loading")}
        </p>
      ) : null}
      {providers.isError ? (
        <p className="assist__notice assist__notice--error" role="alert">
          {t("assistant.provider.loadError")}
        </p>
      ) : null}

      {providers.data === undefined ? null : !hasActiveProvider ? (
        <EmptyState
          title={t("assistant.chat.noProvider")}
          description={t("assistant.chat.noProvider.description")}
          action={
            <Link className="ui-button ui-button--primary" to="/settings">
              {t("assistant.chat.noProvider.action")}
            </Link>
          }
        />
      ) : (
        <>
          {chat.entries.length === 0 && chat.pendingPrompt === null ? (
            <EmptyState
              title={t("assistant.chat.empty")}
              description={t(COPY[copy].empty)}
            />
          ) : (
            <AssistTranscript
              entries={chat.entries}
              pendingPrompt={chat.pendingPrompt}
              handlers={{
                onPreview:
                  onPreviewProposal === undefined
                    ? undefined
                    : (proposal, turnId) =>
                        onPreviewProposal(proposalAction(proposal, turnId)),
                onApply:
                  onApplyProposal === undefined
                    ? undefined
                    : (proposal, turnId) =>
                        onApplyProposal(proposalAction(proposal, turnId)),
                onApplyAndBacktest:
                  onApplyProposalAndBacktest === undefined
                    ? undefined
                    : (proposal, turnId) =>
                        onApplyProposalAndBacktest(
                          proposalAction(proposal, turnId),
                        ),
              }}
            />
          )}
          {chat.historyFailed ? (
            <p className="assist__notice assist__notice--error" role="alert">
              {t("assistant.chat.loadError")}
            </p>
          ) : null}
          {chat.rejection === null ? null : (
            <p className="assist__notice assist__notice--error" role="alert">
              {chat.rejection}
            </p>
          )}
          {/* 상한을 소진한 연결. 턴은 서버에서 계속 도므로 다시 연결하면 이어서 본다(spec D7). */}
          {chat.streamStatus === "exhausted" ? (
            <div className="assist__reconnect" role="alert">
              <span>{t("assistant.chat.stream.exhausted")}</span>
              <Button size="small" onClick={chat.retryStream}>
                {t("assistant.chat.stream.retry")}
              </Button>
            </div>
          ) : null}
          {chat.droppedFrames === 0 ? null : (
            <p className="assist__notice assist__notice--warn" role="status">
              {t("assistant.chat.stream.dropped")}
            </p>
          )}
          {/*
            라이브 영역은 늘 자리를 지키고 텍스트만 바뀐다. 내용과 함께 삽입되는 영역은 보조 기술이
            놓치기 쉽고, 본문과 제안 카드를 라이브 영역에서 뺀 뒤로 이 한 곳이 유일한 통로다
            (3차 리뷰 P2). 빈 영역은 낭독되지 않으므로 "실패는 말하지 않는다"는 결정은 그대로다.
          */}
          <p
            className="assist__progress"
            role="status"
            aria-label={t("assistant.chat.progress")}
          >
            {announcement ?? ""}
          </p>
          {suggestions.length === 0 ? null : (
            <div
              className="assist__suggestions"
              role="group"
              aria-labelledby={suggestionsId}
            >
              <p className="assist__suggestions-title" id={suggestionsId}>
                {t("assistant.chat.suggestions")}
              </p>
              <div className="assist__suggestion-list">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className="assist__suggestion"
                    // 답변이 진행 중이거나 질문을 보내는 중이면 넣지 않는다 — 그 사이 넣은 문장은 보낼 수 없다.
                    disabled={chat.running !== null || chat.busy}
                    onClick={() => pickSuggestion(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}
          <AssistComposer
            text={draft}
            onTextChange={setDraft}
            inputRef={inputRef}
            running={chat.running !== null}
            busy={chat.busy}
            placeholder={t(COPY[copy].placeholder)}
            onSubmit={submitDraft}
            onCancel={chat.cancel}
          />
        </>
      )}
    </section>
  );
};
