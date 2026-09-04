import { useCallback, useEffect } from "react";

import {
  DirtyLeaveGuard,
  DocumentToolbar,
  RecoveryBanner,
  SourceEditor,
  StrategyOutline,
  saveStatusText,
  saveStatusTone,
  useAutosave,
  useCompileDocument,
  useRunBacktest,
  useSaveDocument,
  useSchemaAssist,
  useOutlineNavigation,
  useStrategyDocument,
  type DocumentSource,
} from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { useNavigate, useSearch } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";

const STARTER = 'schema_version: "1.0"\ntitle: ""\n';
const ROUTE = "/research/strategies/new";
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
  const search = useSearch({ from: ROUTE });
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
  const selectPointer = useCallback(
    (path: string | undefined) => {
      void navigate({
        to: ROUTE,
        search: { ...search, path },
        replace: true,
      });
    },
    [navigate, search],
  );
  const outline = useOutlineNavigation({
    state: document,
    schema: assist.schema,
    selectedPointer: search.path,
    onSelectedPointer: selectPointer,
  });

  // If the user types while create is in flight, stay on this page and preserve the newer text.
  // A second save appends it to the newly created strategy; navigate only once the current text
  // is the saved base. This gives P2-04 lossless behavior without depending on P3-06 autosave.
  const leaving =
    document.strategyId !== null &&
    document.baseRevision !== null &&
    !document.dirty;

  useEffect(() => {
    if (
      !leaving ||
      document.strategyId === null ||
      document.baseRevision === null
    )
      return;
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
  }, [leaving, document.strategyId, document.baseRevision, navigate]);

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
        onRunBacktest={() => void backtest.run()}
        runDisabled={!backtest.canRun}
        saveTone={saveStatusTone(document, status)}
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
        outline={
          <StrategyOutline
            snapshot={outline.snapshot}
            selectedPointer={search.path}
            onSelect={outline.onSelectOutlineNode}
          />
        }
        editor={
          <>
            {autosave.recovery ? (
              <RecoveryBanner recovery={autosave.recovery} />
            ) : null}
            <SourceEditor
              state={document}
              dispatch={dispatch}
              assist={assist}
              onEditorReady={outline.onEditorReady}
              onSelectionChange={outline.onEditorSelectionChange}
            />
          </>
        }
      />
      <DirtyLeaveGuard dirty={document.dirty && !leaving} />
    </>
  );
};
