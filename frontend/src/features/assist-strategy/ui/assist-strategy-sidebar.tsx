import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  assistantProvidersQuery,
  type DocumentRefView,
  type StrategyProposalView,
  type TurnContextPayload,
} from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Button, EmptyState } from "../../../shared/ui";
import { useAssistChat } from "../model/use-assist-chat";
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

export type AssistStrategySidebarProps = {
  /** 이 사이드바가 붙은 문서. 세션 목록의 범위다. */
  documentRef: DocumentRefView;
  /** 턴 시작 순간의 편집기 텍스트·진단·실행 설정. 서버가 문서를 들지 않으므로 턴마다 싣는다. */
  readContext: () => TurnContextPayload;
  onPreviewProposal?: (action: AssistProposalAction) => void;
  onApplyProposal?: (action: AssistProposalAction) => void;
  onApplyProposalAndBacktest?: (action: AssistProposalAction) => void;
  /** 사이드바를 닫는 동작. 주면 닫기 버튼이 생긴다. */
  onClose?: () => void;
};

/**
 * 전략 화면 우측의 AI 채팅 사이드바.
 *
 * 그래프·YAML 어느 탭에서 보든 같은 세션이며, 문서를 바꾸는 것은 제안 카드의 버튼이 부르는 콜백뿐이다
 * (적용 경로는 spec D7에 따라 바깥이 소유한다). 서버 상태는 전부 query cache가 들고, 이 컴포넌트가
 * 소유하는 것은 열린 대화·확인 대화상자 같은 UI 상태다(spec D9).
 */
export const AssistStrategySidebar = ({
  documentRef,
  readContext,
  onPreviewProposal,
  onApplyProposal,
  onApplyProposalAndBacktest,
  onClose,
}: AssistStrategySidebarProps) => {
  const titleId = useId();
  const providers = useQuery(assistantProvidersQuery());
  const chat = useAssistChat({ documentRef, readContext });
  const [closeConfirm, setCloseConfirm] = useState(false);

  const hasActiveProvider =
    providers.data?.profiles.some((profile) => profile.active) ?? false;

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

  const cancelAndClose = () => {
    chat.cancel();
    setCloseConfirm(false);
    onClose?.();
  };

  return (
    <aside className="assist" aria-labelledby={titleId}>
      <header className="assist__head">
        <h2 className="assist__title" id={titleId}>
          {t("assistant.chat.title")}
        </h2>
        <div className="assist__head-actions">
          {chat.sessions.length === 0 ? null : (
            <select
              className="assist__sessions"
              aria-label={t("assistant.chat.session")}
              value={chat.sessionId ?? ""}
              onChange={(event) => chat.selectSession(event.target.value)}
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
          onKeyDown={(event) => {
            if (event.key === "Escape") setCloseConfirm(false);
          }}
        >
          <p className="assist__confirm-text">
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
              onClick={() => setCloseConfirm(false)}
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
              description={t("assistant.chat.empty.description")}
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
          {chat.running === null ? null : (
            <p className="assist__progress" role="status">
              {t("assistant.chat.running")}
            </p>
          )}
          <AssistComposer
            running={chat.running !== null}
            busy={chat.busy}
            onSend={chat.send}
            onCancel={chat.cancel}
          />
        </>
      )}
    </aside>
  );
};
