import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { t } from "../../../shared/config";
import { lineDiff } from "../../../shared/lib/text-diff";
import { Badge, Button } from "../../../shared/ui";
import type {
  AssistantProposalApply,
  ProposalApplyStatus,
} from "../model/use-apply-assistant-proposal";
import type { ProposalBacktestChain } from "../model/use-apply-then-backtest";
import "./proposal-apply-dialog.css";

type ProposalApplyProps = { apply: AssistantProposalApply };

const failureText = (
  status: Extract<ProposalApplyStatus, { kind: "failed" }>,
): string => t(`assistant.apply.error.${status.reason}`);

/**
 * 제안 적용 결과 알림. 적용은 성공해도 문서가 통째로 바뀌는 일이라 화면에 흔적을 남긴다. 확인
 * 화면이 떠 있는 동안에는 다이얼로그가 같은 사실을 말하므로 아무것도 그리지 않는다.
 */
export const ProposalApplyFeedback = ({
  apply,
  chain,
}: ProposalApplyProps & { chain?: ProposalBacktestChain }) => {
  const { status } = apply;
  if (chain?.waiting === true)
    return (
      <p className="proposal-apply__feedback" role="status">
        {t("assistant.apply.backtestWaiting")}
      </p>
    );
  if (status.kind === "applied")
    return (
      <p className="proposal-apply__feedback" role="status">
        {t("assistant.apply.applied")}
      </p>
    );
  if (status.kind === "failed")
    return (
      <p className="proposal-apply__feedback proposal-apply__feedback--error" role="alert">
        {failureText(status)}
      </p>
    );
  return null;
};

/**
 * "문서가 바뀌었습니다" 확인 창(spec D7). 제안을 만든 뒤 문서가 바뀌었거나 기준을 알 수 없을 때만
 * 열리고, 미리보기는 현재 문서와 제안의 줄 단위 차이를 기존 diff 투영(`lineDiff`)으로 보여 준다.
 * 덮어쓰기도 같은 전체 범위 교체 한 번이라 실행 취소 한 번으로 되돌아간다.
 */
export const ProposalApplyDialog = ({ apply }: ProposalApplyProps) =>
  apply.status.kind === "confirming" ? (
    <OpenProposalApplyDialog apply={apply} status={apply.status} />
  ) : null;

const OpenProposalApplyDialog = ({
  apply,
  status,
}: ProposalApplyProps & {
  status: Extract<ProposalApplyStatus, { kind: "confirming" }>;
}) => {
  const preview = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  useEffect(() => {
    opener.current = document.activeElement as HTMLElement | null;
    queueMicrotask(() => preview.current?.focus());
    return () => {
      // 적용은 편집기로 포커스를 옮긴다. 아무도 가져가지 않았을 때만 열었던 자리로 돌려준다.
      queueMicrotask(() => {
        const active = document.activeElement;
        if (active === null || active === document.body)
          opener.current?.focus();
      });
    };
  }, []);

  const diff = showDiff
    ? lineDiff(status.currentSource, status.proposal.source)
    : null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      apply.cancel();
      return;
    }
    if (event.key !== "Tab") return;
    // 포커스는 창 안에서만 돈다: 첫 버튼에서 Shift+Tab, 마지막 버튼에서 Tab만 되돌린다.
    if (event.shiftKey && event.target === preview.current) {
      event.preventDefault();
      cancel.current?.focus();
    } else if (!event.shiftKey && event.target === cancel.current) {
      event.preventDefault();
      preview.current?.focus();
    }
  };

  return (
    <div className="proposal-apply__backdrop" onKeyDown={onKeyDown}>
      <section
        className="proposal-apply"
        role="dialog"
        aria-modal="true"
        aria-label={t("assistant.apply.title")}
      >
        <header className="proposal-apply__header">
          <Badge tone="warn">{t("assistant.apply.title")}</Badge>
          <p>{t(`assistant.apply.${status.reason}`)}</p>
          <p className="proposal-apply__note">{t("assistant.apply.undoNote")}</p>
        </header>
        {diff === null ? null : (
          <div className="proposal-apply__diff">
            <p>
              <Badge tone={diff.added + diff.removed === 0 ? "ok" : "warn"}>
                {t("diff.source.counts")
                  .replace("{added}", String(diff.added))
                  .replace("{removed}", String(diff.removed))}
              </Badge>
            </p>
            {diff.rows.length === 0 ? (
              <p>{t("diff.source.empty")}</p>
            ) : (
              <div className="proposal-apply__diff-scroll">
                <table aria-label={t("assistant.apply.previewLabel")}>
                  <thead>
                    <tr>
                      <th scope="col">{t("diff.source.beforeLine")}</th>
                      <th scope="col">{t("diff.source.afterLine")}</th>
                      <th scope="col">{t("diff.source.change")}</th>
                      <th scope="col">{t("diff.source.text")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diff.rows.map((row, index) => (
                      <tr
                        key={`${row.kind}:${row.beforeLine ?? ""}:${row.afterLine ?? ""}:${index}`}
                        data-kind={row.kind}
                      >
                        <td>{row.beforeLine ?? "—"}</td>
                        <td>{row.afterLine ?? "—"}</td>
                        <td aria-label={t(`diff.kind.${row.kind}`)}>
                          {row.kind === "added" ? "+" : "−"}
                        </td>
                        <td>
                          <code>
                            {row.marker === "final-newline"
                              ? t("diff.source.finalNewline")
                              : row.text}
                          </code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {diff.truncated ? <p>{t("diff.source.truncated")}</p> : null}
          </div>
        )}
        <div className="proposal-apply__actions">
          <Button
            ref={preview}
            size="small"
            onClick={() => setShowDiff((shown) => !shown)}
            aria-expanded={showDiff}
          >
            {showDiff
              ? t("assistant.apply.previewHide")
              : t("assistant.apply.preview")}
          </Button>
          <Button size="small" tone="primary" onClick={apply.confirm}>
            {t("assistant.apply.overwrite")}
          </Button>
          <Button ref={cancel} size="small" tone="ghost" onClick={apply.cancel}>
            {t("assistant.apply.cancel")}
          </Button>
        </div>
      </section>
    </div>
  );
};
