import { useId, useState, type ReactNode } from "react";

import {
  assistantFailureMessage,
  type AssistantSearchActivity,
  type AssistantToolActivity,
  type StrategyProposalView,
} from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import { assistToolLabel } from "../model/assist-copy";
import type { AssistTranscriptEntry } from "../model/transcript";
import { AssistProposalCard } from "./assist-proposal-card";
import { AssistSourceList } from "./assist-source-list";

/**
 * 접히는 진행 정보.
 *
 * 네이티브 `<details>` 대신 `aria-expanded` 버튼을 쓰는 이유는 열림 상태를 우리가 쥐고 있어야
 * 토글이 키보드·테스트 환경에서 같은 의미를 갖기 때문이다(jsdom은 summary 클릭을 구현하지 않는다).
 */
const AssistDisclosure = ({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) => {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  return (
    <div className="assist-activity">
      <button
        type="button"
        className="assist-activity__toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        {label}
      </button>
      <div className="assist-activity__panel" id={panelId} hidden={!open}>
        {children}
      </div>
    </div>
  );
};

const AssistToolList = ({
  tools,
}: {
  tools: readonly AssistantToolActivity[];
}) => (
  <ul className="assist-tools">
    {tools.map((tool, index) => (
      <li className="assist-tool" key={`${index}-${tool.callId}`}>
        <span className="assist-tool__name">{assistToolLabel(tool.name)}</span>
        <span className="assist-tool__state">
          {tool.ok === null ? (
            t("assistant.chat.tool.running")
          ) : (
            <>
              <Badge tone={tool.ok ? "ok" : "error"}>
                {t(
                  tool.ok
                    ? "assistant.chat.tool.ok"
                    : "assistant.chat.tool.failed",
                )}
              </Badge>
              <span className="assist-tool__summary">{tool.summary}</span>
            </>
          )}
        </span>
      </li>
    ))}
  </ul>
);

const AssistSearchList = ({
  searches,
}: {
  searches: readonly AssistantSearchActivity[];
}) => (
  <ul className="assist-searches">
    {searches.map((search, index) => (
      <li className="assist-search" key={`${index}-${search.query}`}>
        <p className="assist-search__query">
          <Badge tone="info">{t("assistant.chat.search")}</Badge>
          <span className="assist-text">{search.query}</span>
        </p>
        <AssistSourceList sources={search.sources} />
      </li>
    ))}
  </ul>
);

/** 사용자·어시스턴트 말풍선. 본문은 언제나 평문이고 줄바꿈만 CSS가 살린다(spec D7). */
const AssistBubble = ({
  role,
  children,
}: {
  role: "user" | "assistant";
  children: ReactNode;
}) => (
  <div className={`assist-message assist-message--${role}`}>
    <span className="sr-only">
      {t(
        role === "user"
          ? "assistant.chat.role.user"
          : "assistant.chat.role.assistant",
      )}
    </span>
    {children}
  </div>
);

export type AssistProposalHandlers = {
  onPreview?: (proposal: StrategyProposalView, turnId: string) => void;
  onApply?: (proposal: StrategyProposalView, turnId: string) => void;
  onApplyAndBacktest?: (proposal: StrategyProposalView, turnId: string) => void;
};

const AssistTurnBlock = ({
  entry,
  handlers,
}: {
  entry: AssistTranscriptEntry;
  handlers: AssistProposalHandlers;
}) => {
  const { turn } = entry;
  const proposal = turn.proposal;
  // 아직 아무 것도 오지 않은 턴은 빈 말풍선을 만들지 않는다. 진행 중이라는 사실은 진행 표시가 알린다.
  const answered =
    turn.text !== "" ||
    turn.thinking.length > 0 ||
    turn.tools.length > 0 ||
    turn.searches.length > 0 ||
    proposal !== null ||
    turn.failure !== null;
  return (
    <article className="assist-turn">
      {entry.prompt === null ? null : (
        <AssistBubble role="user">
          <p className="assist-text">{entry.prompt}</p>
        </AssistBubble>
      )}
      {!answered ? null : (
        <AssistBubble role="assistant">
          {turn.thinking.length === 0 ? null : (
            <AssistDisclosure label={t("assistant.chat.thinking")}>
              <ul className="assist-thinking">
                {turn.thinking.map((text, index) => (
                  <li className="assist-text" key={index}>
                    {text}
                  </li>
                ))}
              </ul>
            </AssistDisclosure>
          )}
          {turn.tools.length === 0 ? null : (
            <AssistDisclosure label={t("assistant.chat.tools")}>
              <AssistToolList tools={turn.tools} />
            </AssistDisclosure>
          )}
          {turn.searches.length === 0 ? null : (
            <AssistSearchList searches={turn.searches} />
          )}
          {turn.text === "" ? null : (
            // 델타마다 같은 텍스트 노드가 갈리므로 라이브 영역에서 뺀다 — 그러지 않으면 토큰 하나마다
            // 누적된 문단 전체가 다시 낭독된다. 진행·완료는 사이드바의 status가 한 번씩 알린다.
            <p className="assist-text" aria-live="off">
              {turn.text}
            </p>
          )}
          {proposal === null ? null : (
            <AssistProposalCard
              proposal={proposal}
              onPreview={
                handlers.onPreview === undefined
                  ? undefined
                  : () => handlers.onPreview?.(proposal, entry.turnId)
              }
              onApply={
                handlers.onApply === undefined
                  ? undefined
                  : () => handlers.onApply?.(proposal, entry.turnId)
              }
              onApplyAndBacktest={
                handlers.onApplyAndBacktest === undefined
                  ? undefined
                  : () => handlers.onApplyAndBacktest?.(proposal, entry.turnId)
              }
            />
          )}
          {turn.failure === null ? null : (
            <p className="assist-turn__failure">
              {assistantFailureMessage(turn.failure)}
            </p>
          )}
        </AssistBubble>
      )}
    </article>
  );
};

/**
 * 대화 본문.
 *
 * `role="log"`는 "대화가 한 덩어리 늘었다"만 알린다. `aria-relevant="additions"`로 추가만 보고,
 * 토큰마다 갈리는 스트리밍 본문과 제안 카드는 `aria-live="off"`로 빼 둔다 — 그러지 않으면 델타
 * 하나마다 누적된 문단 전체가 다시 낭독된다(B-03 리뷰 P2). 진행과 완료는 사이드바의 `role="status"`
 * 영역이 한 번씩 알린다.
 *
 * 서버가 보낸 문자열은 전부 React 자식으로만 들어가므로 HTML로 해석될 자리가 없다.
 */
export const AssistTranscript = ({
  entries,
  pendingPrompt,
  handlers,
}: {
  entries: readonly AssistTranscriptEntry[];
  pendingPrompt: string | null;
  handlers: AssistProposalHandlers;
}) => (
  <div
    className="assist__log"
    role="log"
    aria-live="polite"
    aria-relevant="additions"
    aria-label={t("assistant.chat.log")}
  >
    {entries.map((entry) => (
      <AssistTurnBlock entry={entry} handlers={handlers} key={entry.turnId} />
    ))}
    {pendingPrompt === null ? null : (
      <article className="assist-turn">
        <AssistBubble role="user">
          <p className="assist-text">{pendingPrompt}</p>
        </AssistBubble>
      </article>
    )}
  </div>
);
