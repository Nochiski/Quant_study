import { useEffect } from "react";

import {
  DirtyLeaveGuard,
  DocumentToolbar,
  RecoveryBanner,
  SourceEditor,
  draftKey,
  saveStatusText,
  saveStatusTone,
  useAutosave,
  writeDraft,
  useCompileDocument,
  useRunBacktest,
  useSaveDocument,
  useSchemaAssist,
  useStrategyDocument,
  type DocumentSource,
} from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { useNavigate } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";

const STARTER = 'schema_version: "1.0"\ntitle: ""\n';
const defaultDraftStorageForPage = () => {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
};

const NEW_DRAFT: DocumentSource = {
  kind: "new",
  format: "yaml",
  source: STARTER,
};

/**
 * New-strategy entry (WORKFLOW P2-04): a draft with no base. Saving creates the strategy, after
 * which the URL moves to revision 1 so a reload lands on the saved document (router ADR D3).
 */
export const NewStrategyPage = () => {
  const navigate = useNavigate();
  const [document, dispatch] = useStrategyDocument(NEW_DRAFT);
  const { save, status, canSave } = useSaveDocument(document, dispatch);
  const assist = useSchemaAssist(document);
  const { validateNow, validating } = useCompileDocument(document, dispatch);
  const backtest = useRunBacktest(document);
  const autosave = useAutosave(document, dispatch, {
    schemaVersion: assist.schemaVersion,
  });
  const current =
    document.compiled !== null &&
    document.compiledVersion === document.sourceVersion
      ? document.compiled
      : null;

  // Create succeeded: the draft now has a base, so the URL moves to that revision (ADR D3).
  // Text typed while the request was in flight is not lost: it is parked as the local draft of
  // the new base (P3-06 recovery) before the page changes. `leaving` is derived, so the guard
  // below already sees `dirty=false` (its own effect runs first) when the navigation fires: a
  // save that just succeeded never triggers an "unsaved changes" prompt.
  const leaving =
    document.strategyId !== null && document.baseRevision !== null;
  useEffect(() => {
    if (document.strategyId === null || document.baseRevision === null) return;
    if (document.dirty) {
      writeDraft(defaultDraftStorageForPage(), {
        key: draftKey(document.strategyId, document.baseRevision),
        format: document.format,
        source: document.source,
        strategyId: document.strategyId,
        baseRevision: document.baseRevision,
        baseSpecHash: document.baseSpecHash,
        schemaVersion: assist.schemaVersion,
        savedAt: new Date().toISOString(),
      });
    }
    void navigate({
      to: "/research/strategies/$strategyId/revisions/$revision",
      params: {
        strategyId: document.strategyId,
        revision: String(document.baseRevision),
      },
      search: {
        view: undefined,
        path: undefined,
        asOf: undefined,
        security: undefined,
      },
      replace: true,
    });
  }, [
    document.strategyId,
    document.baseRevision,
    document.dirty,
    document.format,
    document.source,
    document.baseSpecHash,
    assist.schemaVersion,
    navigate,
  ]);

  return (
    <>
      <StrategyIde
        title={t("page.newStrategy.title")}
        versionLabel={t("page.newStrategy.draft")}
        badges={<Badge tone="info">{t("page.newStrategy.draft")}</Badge>}
        meta={{
          schemaVersion: current?.schemaVersion ?? null,
          sourceHash: current?.sourceHash ?? null,
          specHash: current?.specHash ?? null,
        }}
        saveStatus={saveStatusText(document, status)}
        saveTone={saveStatusTone(document, status)}
        onRunBacktest={() => void backtest.run()}
        runDisabled={!backtest.canRun}
        view={document.format}
        availableViews={[document.format]}
        editorActions={
          <DocumentToolbar
            state={document}
            onValidate={validateNow}
            validating={validating}
            onSave={save}
            canSave={canSave}
            saving={status.kind === "saving"}
            onRun={() => void backtest.run()}
            decision={backtest.decision}
            runStatus={backtest.status}
          />
        }
        editor={
          <>
            {autosave.recovery ? (
              <RecoveryBanner
                recovery={autosave.recovery}
                original={document.savedSource ?? STARTER}
              />
            ) : null}
            <SourceEditor
              state={document}
              dispatch={dispatch}
              assist={assist}
            />
          </>
        }
      />
      <DirtyLeaveGuard dirty={document.dirty && !leaving} />
    </>
  );
};
