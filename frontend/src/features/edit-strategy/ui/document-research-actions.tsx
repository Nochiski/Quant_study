import { useQuery } from "@tanstack/react-query";

import {
  trialLedgerQuery,
  type BacktestRunSpec,
} from "../../../entities/backtest";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Badge, Button, FailureNotice } from "../../../shared/ui";
import type { DocumentState } from "../model/document-state";
import { experimentEntryBlock } from "../model/experiment-entry";
import "./document-research-actions.css";

/** 계열 N은 기존 원장 조회 그대로 읽고, 실험 접수는 기존 생성 화면에 맡긴다. */
export const DocumentResearchActions = ({
  state,
  request,
  requiresUpgrade = false,
  parametersReady = true,
  selectedParameters = false,
  recipeOpen,
  onToggleRecipe,
  onSend,
}: {
  state: DocumentState;
  request: BacktestRunSpec | null;
  requiresUpgrade?: boolean;
  parametersReady?: boolean;
  selectedParameters?: boolean;
  recipeOpen: boolean;
  onToggleRecipe: () => void;
  onSend: (request: BacktestRunSpec) => void;
}) => {
  const ledger = useQuery({
    ...trialLedgerQuery(state.strategyId ?? ""),
    enabled: state.strategyId !== null,
  });
  const blocked = experimentEntryBlock(
    state,
    request,
    requiresUpgrade,
    parametersReady,
  );
  return (
    <details
      className="document-research ui-disclosure"
      onKeyDown={(event) => {
        if (event.defaultPrevented || event.key !== "Escape" || !event.currentTarget.open) return;
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.open = false;
        event.currentTarget.querySelector("summary")?.focus();
      }}
      onBlurCapture={(event) => {
        if (
          event.relatedTarget instanceof Node &&
          !event.currentTarget.contains(event.relatedTarget)
        )
          event.currentTarget.open = false;
      }}
    >
      <summary>
        {t("research.ide.title")}
        {state.strategyId !== null ? (
          <Badge tone="neutral">
            {ledger.data ? `N ${ledger.data.trial_count}` : "N —"}
          </Badge>
        ) : null}
      </summary>
      <div className="document-research__content">
        <p>
          {state.strategyId === null
            ? t("research.ide.noLineage")
            : ledger.isPending
              ? t("research.ide.ledgerLoading")
              : ledger.isError
                ? t("research.ide.ledgerFailed")
                : tFill("research.ide.count", {
                    count: ledger.data.trial_count,
                  })}
        </p>
        {state.strategyId !== null ? (
          <>
            {ledger.isError ? (
              <FailureNotice
                message={t("research.ide.ledgerFailed")}
                reason={failureReason(ledger.error)}
              >
                <Button size="small" onClick={() => void ledger.refetch()}>
                  {t("research.ide.retry")}
                </Button>
              </FailureNotice>
            ) : null}
            <Link to="/research/strategies" search={{}}>
              {t("research.ide.ledgerLink")}
            </Link>
          </>
        ) : null}
        <p>
          {selectedParameters
            ? t("research.ide.selectedParameters")
            : t("research.ide.defaultParameters")}
        </p>
        <p>{t("research.ide.environment")}</p>
        {blocked !== null ? (
          <p id="research-entry-reason">{t(blocked)}</p>
        ) : null}
        <Button
          disabled={blocked !== null}
          aria-describedby={
            blocked !== null ? "research-entry-reason" : undefined
          }
          onClick={(event) => {
            const disclosure = event.currentTarget.closest("details");
            if (disclosure !== null) disclosure.open = false;
            if (blocked === null && request !== null) onSend(request);
          }}
        >
          {t("research.ide.send")}
        </Button>
        <Button
          aria-pressed={recipeOpen}
          onClick={(event) => {
            const disclosure = event.currentTarget.closest("details");
            if (disclosure !== null) {
              disclosure.open = false;
              disclosure.querySelector("summary")?.focus();
            }
            onToggleRecipe();
          }}
        >
          {t("research.ide.recipe")}
        </Button>
      </div>
    </details>
  );
};
