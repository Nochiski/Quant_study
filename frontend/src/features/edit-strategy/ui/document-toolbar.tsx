import { t } from "../../../shared/config";
import { Badge, Button, Tooltip } from "../../../shared/ui";
import type { BacktestSourceDecision } from "../model/backtest-source";
import type { DocumentState } from "../model/document-state";
import type { RunBacktestStatus } from "../model/use-run-backtest";
import "./document-toolbar.css";

type DocumentToolbarProps = {
  state: DocumentState;
  onValidate: () => void;
  canValidate: boolean;
  validating: boolean;
  onSave: () => void;
  canSave: boolean;
  saving: boolean;
  onRun: () => void;
  decision: BacktestSourceDecision;
  runStatus: RunBacktestStatus;
};

const short = (hash: string | null): string =>
  hash === null || hash === "" ? "—" : `${hash.slice(0, 12)}…`;

const decisionLabel = (decision: BacktestSourceDecision): string => {
  switch (decision.kind) {
    case "saved_revision":
      return t("toolbar.run.savedRevision").replace(
        "{revision}",
        String(decision.reference.revision),
      );
    case "inline_draft":
      return t("toolbar.run.inlineDraft");
    case "blocked":
      return t(`toolbar.run.blocked.${decision.reason}`);
  }
};

/**
 * Editor header actions and identity line (WORKFLOW P3-05): schema version, source hash,
 * backend canonical spec hash (never a frontend-computed one), then Validate · Save · Backtest.
 * Save and Backtest are disabled while the document is invalid or its compile result is stale.
 */
export const DocumentToolbar = ({
  state,
  onValidate,
  canValidate,
  validating,
  onSave,
  canSave,
  saving,
  onRun,
  decision,
  runStatus,
}: DocumentToolbarProps) => {
  const current =
    state.compiled !== null && state.compiledVersion === state.sourceVersion
      ? state.compiled
      : null;
  return (
    <div className="doc-toolbar">
      <dl className="doc-toolbar__identity">
        <div>
          <dt>{t("toolbar.schemaVersion")}</dt>
          <dd>{current?.schemaVersion ?? "—"}</dd>
        </div>
        <div>
          <dt>{t("toolbar.sourceHash")}</dt>
          <dd>
            <code title={current?.sourceHash ?? undefined}>
              {short(current?.sourceHash ?? null)}
            </code>
          </dd>
        </div>
        <div>
          <dt>{t("toolbar.specHash")}</dt>
          <dd>
            <code title={current?.specHash ?? undefined}>
              {short(current?.specHash ?? null)}
            </code>
          </dd>
        </div>
        <div>
          <dt>{t("toolbar.dirty")}</dt>
          <dd>
            <Badge tone={state.dirty ? "warn" : "neutral"}>
              {state.dirty ? t("toolbar.dirty.yes") : t("toolbar.dirty.no")}
            </Badge>
          </dd>
        </div>
      </dl>
      <div className="doc-toolbar__actions">
        <Button
          size="small"
          onClick={onValidate}
          disabled={!canValidate}
          aria-busy={validating || undefined}
          aria-keyshortcuts="Control+Enter Meta+Enter"
        >
          {t("toolbar.validate")}
        </Button>
        <Button
          size="small"
          tone="primary"
          onClick={onSave}
          disabled={!canSave}
          aria-busy={saving || undefined}
          aria-keyshortcuts="Control+S Meta+S"
        >
          {t("toolbar.saveRevision")}
        </Button>
        <Tooltip content={decisionLabel(decision)}>
          <Button
            size="small"
            onClick={onRun}
            disabled={
              decision.kind === "blocked" || runStatus.kind === "starting"
            }
            aria-busy={runStatus.kind === "starting" || undefined}
            aria-keyshortcuts="Control+Shift+Enter Meta+Shift+Enter"
          >
            {runStatus.kind === "accepted"
              ? t("toolbar.run.open")
              : t("toolbar.backtest")}
          </Button>
        </Tooltip>
        {runStatus.kind === "accepted" ? (
          <span className="doc-toolbar__run-status" role="status">
            {t("toolbar.run.accepted").replace("{runId}", runStatus.runId)}
          </span>
        ) : null}
        {runStatus.kind === "failed" ? (
          <span className="doc-toolbar__error" role="alert">
            {t("toolbar.run.failed")}: {runStatus.detail}
          </span>
        ) : null}
      </div>
    </div>
  );
};
