import type { ReactNode } from "react";

import { t, tOptional } from "../../../shared/config";
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
  canRun: boolean;
  runBlockedReason?: string;
  runSettings?: ReactNode;
  /** 거절이 가리킨 요청 본문의 칸(점 경로) → 실행 설정 칸 이름. 모르면 null. */
  runFieldLabel?: (field: string) => string | null;
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
 * 시작 거절 한 줄. 본문은 코드의 번역, 번역이 없으면 일반 문구다. 서버 사유는 접힌 상세로 내린다 —
 * 결과 화면의 run 실패 표시와 같은 방식이다(이슈 #260, `.claude/rules/frontend-api-state.md`). 줄은 버튼 줄
 * 아래에 따로 두어 긴 문장이 버튼 폭을 빼앗지 않게 한다.
 */
const RunFailure = ({
  detail,
  code,
  fieldLabel,
}: {
  detail: string | null;
  code: string | null;
  fieldLabel: string | null;
}) => (
  <div className="doc-toolbar__error" role="alert">
    {t("toolbar.run.failed")}:{" "}
    {/* 칸 이름을 알면 `<code>.named` 문장에 넣는다. 번역이 없는 코드는 일반 문구로 떨어진다. */}
    {(code === null
      ? null
      : fieldLabel === null
        ? tOptional(`backtest.error.${code}`)
        : (tOptional(`backtest.error.${code}.named`)?.replace(
            "{field}",
            fieldLabel,
          ) ?? tOptional(`backtest.error.${code}`))) ??
      t("toolbar.run.failedGeneric")}
    {detail === null ? null : (
      <details className="doc-toolbar__error-reason">
        <summary>{t("toolbar.run.serverReason")}</summary>
        {detail}
      </details>
    )}
  </div>
);

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
  canRun,
  runBlockedReason,
  runSettings,
  runFieldLabel,
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
        {runSettings}
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
        <Tooltip content={runBlockedReason ?? decisionLabel(decision)}>
          <Button
            size="small"
            onClick={onRun}
            disabled={!canRun || runStatus.kind === "starting"}
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
      </div>
      {runStatus.kind === "failed" ? (
        <RunFailure
          detail={runStatus.detail}
          code={runStatus.code}
          fieldLabel={
            runStatus.field === null || runFieldLabel === undefined
              ? null
              : runFieldLabel(runStatus.field)
          }
        />
      ) : null}
    </div>
  );
};
