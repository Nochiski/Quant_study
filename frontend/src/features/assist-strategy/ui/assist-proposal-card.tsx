import { useId } from "react";

import type { StrategyProposalView } from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import { AssistSourceList } from "./assist-source-list";

type AssistProposalCardProps = {
  proposal: StrategyProposalView;
  onPreview?: () => void;
  onApply?: () => void;
  onApplyAndBacktest?: () => void;
};

/**
 * 모델이 제출한 전략 제안 카드.
 *
 * 문서를 바꾸는 일은 여기서 하지 않는다 — 버튼은 콜백만 부르고 편집기 적용·확인은 바깥(B-04)이
 * 한다(spec D7: feature가 feature를 import하지 않는다). 제목·요약·근거는 모델이 쓴 문자열이라
 * 평문으로 그리고, 출처는 `AssistSourceList`가 스킴을 확인한 것만 링크로 만든다.
 *
 * `compile`은 application이 채운 서버 검증 결과다. 통과 여부와 진단을 그대로 보여 주고 프론트가
 * 다시 판정하지 않는다.
 */
export const AssistProposalCard = ({
  proposal,
  onPreview,
  onApply,
  onApplyAndBacktest,
}: AssistProposalCardProps) => {
  const titleId = useId();
  return (
    <article className="assist-proposal" aria-labelledby={titleId}>
      <header className="assist-proposal__head">
        <h4 className="assist-proposal__title" id={titleId}>
          {proposal.title}
        </h4>
        <Badge tone={proposal.compile.ok ? "ok" : "error"}>
          {t(
            proposal.compile.ok
              ? "assistant.chat.proposal.compileOk"
              : "assistant.chat.proposal.compileFailed",
          )}
        </Badge>
      </header>
      <p className="assist-text">{proposal.summary}</p>
      <p className="assist-proposal__label">
        {t("assistant.chat.proposal.rationale")}
      </p>
      <p className="assist-text assist-proposal__rationale">
        {proposal.rationale}
      </p>
      {proposal.sources.length === 0 ? null : (
        <>
          <p className="assist-proposal__label">
            {t("assistant.chat.proposal.sources")}
          </p>
          <AssistSourceList sources={proposal.sources} />
        </>
      )}
      {proposal.compile.ok ? null : (
        <ul className="assist-proposal__diagnostics">
          {proposal.compile.diagnostics.map((diagnostic, index) => (
            <li key={`${index}-${diagnostic.code}`}>
              {diagnostic.pointer === ""
                ? diagnostic.message
                : `${diagnostic.pointer} — ${diagnostic.message}`}
            </li>
          ))}
        </ul>
      )}
      <div className="assist-proposal__actions">
        {onPreview === undefined ? null : (
          <Button size="small" onClick={onPreview}>
            {t("assistant.chat.proposal.preview")}
          </Button>
        )}
        {onApply === undefined ? null : (
          <Button size="small" tone="primary" onClick={onApply}>
            {t("assistant.chat.proposal.apply")}
          </Button>
        )}
        {onApplyAndBacktest === undefined ? null : (
          <Button size="small" onClick={onApplyAndBacktest}>
            {t("assistant.chat.proposal.applyAndBacktest")}
          </Button>
        )}
      </div>
    </article>
  );
};
