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
  return (
    <li className="assist-turn">
      {entry.prompt === null ? null : (
        <AssistBubble role="user">
          <p className="assist-text">{entry.prompt}</p>
        </AssistBubble>
      )}
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
        {turn.text === "" ? null : <p className="assist-text">{turn.text}</p>}
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
    </li>
  );
};

/**
 * 대화 본문.
 *
 * `role="log"` + `aria-live="polite"`라 스트리밍으로 붙는 텍스트가 보조 기술에 이어서 읽힌다.
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
  <ol
    className="assist__log"
    role="log"
    aria-live="polite"
    aria-label={t("assistant.chat.log")}
  >
    {entries.map((entry) => (
      <AssistTurnBlock entry={entry} handlers={handlers} key={entry.turnId} />
    ))}
    {pendingPrompt === null ? null : (
      <li className="assist-turn">
        <AssistBubble role="user">
          <p className="assist-text">{pendingPrompt}</p>
        </AssistBubble>
      </li>
    )}
  </ol>
);
