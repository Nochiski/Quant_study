import { useSuspenseQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { strategyDocumentQuery } from "../../../entities/strategy";
import {
  ContractInspector,
  ConflictBanner,
  DirtyLeaveGuard,
  DocumentToolbar,
  ExecutionPlanPanel,
  FactorGraphPanel,
  RecoveryBanner,
  SnippetCatalog,
  SourceEditor,
  StrategyProjectionPanel,
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
import { useNavigate, useParams, useSearch } from "../../../shared/lib/router";
import { Badge, type CodeEditorHandle } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";

const ROUTE = "/research/strategies/$strategyId/revisions/$revision";

/** ISO timestamp → "YYYY-MM-DD HH:MM" without locale surprises. */
const shortTimestamp = (iso: string): string =>
  iso.length >= 16 ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso;

/**
 * Saved revision entry (WORKFLOW P2-04). The exact stored document comes from the query cache
 * the route loader warmed up (ADR D4) and becomes the draft base. Saving appends the next
 * revision and the URL follows it. The selected view lives in the URL search and never blocks
 * navigation (ADR D3): the stored format is edited in place, JSON/Form are read-only projections
 * of the current backend compile (or an explicitly stale same-document fallback), and a view that
 * is not implemented yet falls back to the stored format with a notice instead of an empty tab.
 */
export const StrategyRevisionPage = () => {
  const { strategyId, revision } = useParams({ from: ROUTE });
  const search = useSearch({ from: ROUTE });
  const navigate = useNavigate();
  const { data: stored } = useSuspenseQuery(
    strategyDocumentQuery(strategyId, Number(revision)),
  );
  const source = useMemo<DocumentSource>(
    () => ({ kind: "revision", document: stored }),
    [stored],
  );
  const [document, dispatch] = useStrategyDocument(source);
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
  const availableViews: readonly StrategyView[] =
    stored.format === "yaml" ? PROJECTION_VIEWS : ["json", "form", "graph"];
  const requested: StrategyView = search.view ?? stored.format;
  const implemented = availableViews.includes(requested);
  const view: StrategyView = implemented ? requested : stored.format;
  const selectPointer = useCallback(
    (
      path: string | undefined,
      origin: "cursor" | "outline" | "outline-collapse" | "graph",
    ) => {
      void navigate({
        to: ROUTE,
        params: { strategyId, revision },
        search: {
          ...search,
          path,
          view: origin === "outline" ? undefined : search.view,
        },
        replace: true,
      });
    },
    [navigate, revision, search, strategyId],
  );
  const outline = useOutlineNavigation({
    state: document,
    schema: assist.schema,
    revealSelectedPointer: view === stored.format,
    selectedPointer: search.path,
    onSelectedPointer: selectPointer,
  });
  const snippets = useSnippetInsertion(
    document,
    assist.snippetSource,
    view === stored.format,
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

  // A save can complete while the user is still typing. Keep that newer text on the current
  // route and let the next save append from the updated base; only follow the revision when the
  // current source is actually saved.
  const followedSave = useRef(status.kind === "saved" ? status.document : null);

  useEffect(() => {
    if (
      status.kind !== "saved" ||
      followedSave.current === status.document ||
      document.strategyId !== strategyId ||
      document.baseRevision === null ||
      document.baseRevision === Number(revision) ||
      document.dirty
    )
      return;
    // A route/base mismatch can also mean the user intentionally opened another revision.
    // Follow only the fresh save event once; the URL is not derived from reducer lag alone.
    followedSave.current = status.document;
    void navigate({
      to: ROUTE,
      params: { strategyId, revision: String(document.baseRevision) },
      search: { ...search },
      replace: true,
    });
  }, [
    document.strategyId,
    document.baseRevision,
    document.dirty,
    status,
    strategyId,
    revision,
    search,
    navigate,
  ]);

  const title = stored.spec.title || t("page.revision.untitled");

  return (
    <>
      <StrategyIde
        title={title}
        versionLabel={`v${stored.revision}`}
        badges={
          <>
            <Badge tone="accent">
              {t("page.revision.label")} {stored.revision}
            </Badge>
            {stored.generated ? (
              <Badge tone="neutral">{t("page.revision.generated")}</Badge>
            ) : null}
          </>
        }
        meta={{
          createdAt: shortTimestamp(stored.created_at),
          schemaVersion: current?.schemaVersion ?? stored.schema_version,
          sourceHash: current?.sourceHash ?? stored.source_hash,
          specHash: current?.specHash ?? null,
        }}
        saveStatus={saveStatusText(document, status)}
        onRunBacktest={() => void backtest.run()}
        runDisabled={!backtest.canRun}
        saveTone={saveStatusTone(document, status)}
        view={view}
        sourceView={stored.format}
        availableViews={availableViews}
        onViewChange={(next) =>
          void navigate({
            to: ROUTE,
            params: { strategyId, revision },
            search: {
              ...search,
              view: next === stored.format ? undefined : next,
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
          json:
            stored.format === "yaml" ? (
              <StrategyProjectionPanel projection={projection} view="json" />
            ) : undefined,
          form: <StrategyProjectionPanel projection={projection} view="form" />,
          graph: (
            <FactorGraphPanel
              state={executionPlans}
              diagnostics={currentDiagnostics(document)}
              selectedPointer={search.path}
              onSelectPointer={(pointer) => selectPointer(pointer, "graph")}
              onOpenSource={(pointer) => selectPointer(pointer, "outline")}
            />
          ),
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
          <ExecutionPlanPanel
            state={executionPlans}
            selectedPointer={search.path}
            onSelectPointer={(pointer) => selectPointer(pointer, "outline")}
          />
        }
        editor={
          <>
            {implemented ? null : (
              <p className="page-state" role="status">
                {t("page.revision.viewPending")} ({requested.toUpperCase()})
              </p>
            )}
            {status.kind === "conflict" &&
            status.strategyId !== null &&
            status.baseRevision !== null &&
            status.latestRevision !== null ? (
              <ConflictBanner
                strategyId={status.strategyId}
                baseRevision={status.baseRevision}
                latestRevision={status.latestRevision}
                source={document.source}
              />
            ) : null}
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
      <DirtyLeaveGuard dirty={document.dirty} />
    </>
  );
};
