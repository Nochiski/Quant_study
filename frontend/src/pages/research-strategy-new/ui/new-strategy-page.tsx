import { useCallback, useEffect } from "react";

import {
  ContractInspector,
  DirtyLeaveGuard,
  DocumentToolbar,
  FactorGraphPanel,
  RecoveryBanner,
  SnippetCatalog,
  SourceEditor,
  StrategyProjectionPanel,
  StrategyDiffPanel,
  StrategyOutline,
  PROJECTION_VIEWS,
  currentDiagnostics,
  projectStrategySpec,
  saveStatusText,
  saveStatusTone,
  useAutosave,
  useCompileDocument,
  useExecutionPlans,
  useRunBacktest,
  useSaveDocument,
  useSchemaAssist,
  useOutlineNavigation,
  useSnippetInsertion,
  useStrategyDocument,
  type DocumentSource,
  type StrategyView,
} from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { useNavigate, useSearch } from "../../../shared/lib/router";
import { Badge, type CodeEditorHandle } from "../../../shared/ui";
import {
  StrategyDebuggerPanel,
  StrategyIde,
} from "../../../widgets/strategy-ide";

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
  const autosave = useAutosave(document, dispatch, {
    schemaVersion: assist.schemaVersion,
  });
  const executionPlans = useExecutionPlans(document, assist.inspectorSource);
  const backtest = useRunBacktest(document, executionPlans);
  const current =
    document.compiled !== null &&
    document.compiledVersion === document.sourceVersion
      ? document.compiled
      : null;
  const projection = projectStrategySpec(document);
  const availableViews: readonly StrategyView[] = PROJECTION_VIEWS;
  const requested: StrategyView = search.view ?? document.format;
  const implemented = availableViews.includes(requested);
  const view: StrategyView = implemented ? requested : document.format;
  const selectPointer = useCallback(
    (
      path: string | undefined,
      origin: "cursor" | "outline" | "outline-collapse" | "graph" = "outline",
    ) => {
      void navigate({
        to: ROUTE,
        search: {
          ...search,
          path,
          view: origin === "outline" ? undefined : search.view,
        },
        replace: true,
      });
    },
    [navigate, search],
  );
  const outline = useOutlineNavigation({
    state: document,
    schema: assist.schema,
    revealSelectedPointer: view === document.format,
    selectedPointer: search.path,
    onSelectedPointer: selectPointer,
  });
  const snippets = useSnippetInsertion(
    document,
    assist.snippetSource,
    view === document.format,
  );
  const onOutlineEditorReady = outline.onEditorReady;
  const onSnippetEditorReady = snippets.onEditorReady;
  const onEditorReady = useCallback(
    (editor: CodeEditorHandle | null): void => {
      onOutlineEditorReady(editor);
      onSnippetEditorReady(editor);
    },
    [onOutlineEditorReady, onSnippetEditorReady],
  );

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
        view={view}
        sourceView={document.format}
        availableViews={availableViews}
        onViewChange={(next) =>
          void navigate({
            to: ROUTE,
            search: {
              ...search,
              view: next === document.format ? undefined : next,
            },
            replace: true,
          })
        }
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
        projections={{
          json: <StrategyProjectionPanel projection={projection} view="json" />,
          form: <StrategyProjectionPanel projection={projection} view="form" />,
          graph: (
            <FactorGraphPanel
              state={executionPlans}
              diagnostics={currentDiagnostics(document)}
              selectedPointer={search.path}
              onSelectPointer={(pointer) => selectPointer(pointer, "graph")}
              onOpenSource={(pointer) => {
                outline.requestSourceReveal(pointer);
                selectPointer(pointer, "outline");
              }}
            />
          ),
          diff: <StrategyDiffPanel state={document} active={view === "diff"} />,
        }}
        outline={
          <StrategyOutline
            snapshot={outline.snapshot}
            selectedPointer={search.path}
            onSelect={outline.onSelectOutlineNode}
            onCollapse={outline.onCollapseOutlineNode}
          />
        }
        snippets={
          <SnippetCatalog
            snippets={snippets.snippets}
            sourceStatus={snippets.sourceStatus}
            feedback={snippets.feedback}
            onInsert={snippets.insert}
          />
        }
        inspector={
          <ContractInspector
            source={assist.inspectorSource}
            selectedPointer={search.path}
            tree={outline.snapshot?.parsed.tree}
            stale={outline.snapshot?.stale ?? false}
          />
        }
        debugger={
          <StrategyDebuggerPanel
            document={document}
            executionPlans={executionPlans}
            asOf={search.asOf}
            security={search.security}
            selectedPointer={search.path}
            onSelectPointer={(pointer) => selectPointer(pointer, "outline")}
            onSearchSelection={(selection) =>
              void navigate({
                to: ROUTE,
                search: { ...search, ...selection },
                replace: true,
              })
            }
          />
        }
        editor={
          <>
            {implemented ? null : (
              <p className="page-state" role="status">
                {t("page.revision.viewPending")} ({requested.toUpperCase()})
              </p>
            )}
            {autosave.recovery ? (
              <RecoveryBanner recovery={autosave.recovery} />
            ) : null}
            <SourceEditor
              state={document}
              dispatch={dispatch}
              assist={assist}
              onEditorReady={onEditorReady}
              onSelectionChange={outline.onEditorSelectionChange}
            />
          </>
        }
      />
      <DirtyLeaveGuard dirty={document.dirty && !leaving} />
    </>
  );
};
