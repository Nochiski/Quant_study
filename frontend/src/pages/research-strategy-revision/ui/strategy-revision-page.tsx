import { useSuspenseQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { strategyDocumentQuery } from "../../../entities/strategy";
import {
  ContractInspector,
  ConflictBanner,
  DirtyLeaveGuard,
  DocumentToolbar,
  FactorGraphPanel,
  RecoveryBanner,
  ServerDraftBanner,
  SnippetCatalog,
  SourceEditor,
  StrategyFormPanel,
  StrategyProjectionPanel,
  StrategyDiffPanel,
  StrategyOutline,
  UpgradeBanner,
  PROJECTION_VIEWS,
  canValidateDocument,
  currentDiagnostics,
  currentSpec,
  projectStrategySpec,
  revisionDraftId,
  saveStatusText,
  saveStatusTone,
  useAutosave,
  useCompileDocument,
  useExecutionPlans,
  useRunBacktest,
  useServerDraft,
  useSaveDocument,
  useSchemaAssist,
  useOutlineNavigation,
  useSnippetInsertion,
  useFormProjection,
  useSourceTransactions,
  useStrategyDocument,
  useUpgradeDocument,
  type DocumentSource,
  type StrategyView,
} from "../../../features/edit-strategy";
import {
  BacktestRunSettings,
  useBacktestRunSettings,
} from "../../../features/run-backtest";
import { t } from "../../../shared/config";
import { useNavigate, useParams, useSearch } from "../../../shared/lib/router";
import { Badge, type CodeEditorHandle } from "../../../shared/ui";
import {
  StrategyDebuggerPanel,
  StrategyIde,
} from "../../../widgets/strategy-ide";

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
  const {
    save,
    status,
    canSave,
    createRevisionFromConflict,
    canCreateRevisionFromConflict,
  } = useSaveDocument(document, dispatch);
  const assist = useSchemaAssist(document);
  const serverDraftId =
    document.strategyId !== null &&
    document.baseRevision !== null &&
    document.baseSpecHash !== null
      ? revisionDraftId(
          document.strategyId,
          document.baseRevision,
          document.baseSpecHash,
        )
      : null;
  const serverDraft = useServerDraft(document, dispatch, {
    draftId: serverDraftId,
    schemaVersion: assist.schemaVersion ?? stored.schema_version,
    schemaPending: assist.loading,
  });
  const { validateNow, validating } = useCompileDocument(document, dispatch);
  const canValidate = !validating && canValidateDocument(document);
  const autosave = useAutosave(document, dispatch, {
    schemaVersion: assist.schemaVersion,
  });
  const executionPlans = useExecutionPlans(document, assist.inspectorSource);
  const executableSpec = currentSpec(document);
  const runDateRange = useMemo(
    () =>
      executableSpec === null
        ? null
        : { start: executableSpec.data.start, end: executableSpec.data.end },
    [executableSpec],
  );
  const runSettings = useBacktestRunSettings(runDateRange);
  const backtest = useRunBacktest(
    document,
    executionPlans,
    runSettings.requestOptions,
  );
  const startBacktest = backtest.run;
  // 저장된 1.0 revision 참조로 보낸 실행을 backend가 거부한 경우: 같은 배너로 안내하고 실행을 막는다.
  const backtestRejectedForUpgrade =
    backtest.status.kind === "failed" &&
    backtest.status.code === "backtest.strategy.requires_upgrade";
  const canRun = backtest.canRun && !backtestRejectedForUpgrade;
  const storedMeta = useMemo(
    () => ({
      revision: stored.revision,
      generated: stored.generated,
      requires_upgrade: stored.requires_upgrade,
    }),
    [stored.generated, stored.requires_upgrade, stored.revision],
  );
  const documentUpgrade = useUpgradeDocument(document, storedMeta);
  const current =
    document.compiled !== null &&
    document.compiledVersion === document.sourceVersion
      ? document.compiled
      : null;
  const projection = projectStrategySpec(document);
  const availableViews: readonly StrategyView[] =
    stored.format === "yaml"
      ? PROJECTION_VIEWS
      : ["json", "form", "graph", "diff"];
  const requested: StrategyView = search.view ?? stored.format;
  const implemented = availableViews.includes(requested);
  const view: StrategyView = implemented ? requested : stored.format;
  // Include route params as well as the validated search generation; the debug feature only
  // compares this opaque lease and never interprets router state.
  const debuggerPublicationOwner = JSON.stringify([
    ROUTE,
    strategyId,
    revision,
    search,
  ]);
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
  // source 트랜잭션 인스턴스는 page가 하나 만들어 Form·스니펫이 공유한다(WORKFLOW P4-04). hidden 편집기가
  // 살아 있으므로 editorActive는 참이고, 스니펫만 source view 게이트를 따로 지킨다.
  const transactions = useSourceTransactions(document, true);
  const snippets = useSnippetInsertion(
    document,
    assist.snippetSource,
    view === stored.format,
    transactions,
  );
  const form = useFormProjection(document, assist.schema);
  const openGraph = useCallback(
    (pointer: string): void => {
      void navigate({
        to: ROUTE,
        params: { strategyId, revision },
        search: { ...search, path: pointer, view: "graph" },
        replace: true,
      });
    },
    [navigate, search, revision, strategyId],
  );
  const onOutlineEditorReady = outline.onEditorReady;
  const onSnippetEditorReady = snippets.onEditorReady;
  const onTransactionsEditorReady = transactions.onEditorReady;
  const onUpgradeEditorReady = documentUpgrade.onEditorReady;
  const onEditorReady = useCallback(
    (editor: CodeEditorHandle | null): void => {
      onOutlineEditorReady(editor);
      onSnippetEditorReady(editor);
      onTransactionsEditorReady(editor);
      onUpgradeEditorReady(editor);
    },
    [
      onOutlineEditorReady,
      onSnippetEditorReady,
      onTransactionsEditorReady,
      onUpgradeEditorReady,
    ],
  );
  const runBacktest = useCallback(() => void startBacktest(), [startBacktest]);
  const selectSymbol = useCallback(
    (pointer: string): void => {
      outline.requestSourceReveal(pointer);
      selectPointer(pointer, "outline");
    },
    [outline, selectPointer],
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
        onRunBacktest={runBacktest}
        runDisabled={!canRun}
        onValidate={validateNow}
        validateDisabled={!canValidate}
        onSave={save}
        saveDisabled={!canSave}
        symbols={outline.symbols}
        onSelectSymbol={selectSymbol}
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
            canValidate={canValidate}
            validating={validating}
            onSave={save}
            canSave={canSave}
            saving={status.kind === "saving"}
            onRun={runBacktest}
            canRun={canRun}
            runBlockedReason={
              backtestRejectedForUpgrade
                ? t("upgrade.backtestBlocked")
                : runSettings.result.valid
                  ? undefined
                  : t("backtest.settings.blocked")
            }
            runSettings={
              <BacktestRunSettings
                controller={runSettings}
                disabled={backtest.status.kind === "starting"}
              />
            }
            decision={backtest.decision}
            runStatus={backtest.status}
          />
        }
        projections={{
          json:
            stored.format === "yaml" ? (
              <StrategyProjectionPanel projection={projection} />
            ) : undefined,
          form: (
            <StrategyFormPanel
              projection={form.projection}
              stale={form.stale}
              tree={form.tree}
              schema={assist.schema}
              transactions={transactions}
              catalogs={{
                equityFields:
                  assist.inspectorSource.equityCatalog?.fields ?? null,
                factors: assist.inspectorSource.factorCatalog?.factors ?? null,
              }}
              catalogSnippets={snippets.snippets}
              onOpenGraph={openGraph}
            />
          ),
          graph: (
            <FactorGraphPanel
              state={executionPlans}
              diagnostics={currentDiagnostics(document)}
              selectedPointer={search.path}
              editing={{
                tree: form.tree,
                schema: assist.schema,
                transactions,
                catalogs: {
                  equityFields:
                    assist.inspectorSource.equityCatalog?.fields ?? null,
                  factors:
                    assist.inspectorSource.factorCatalog?.factors ?? null,
                },
              }}
              onSelectPointer={(pointer) => selectPointer(pointer, "graph")}
              onOpenSource={(pointer) => {
                outline.requestSourceReveal(pointer);
                selectPointer(pointer, "outline");
              }}
            />
          ),
          diff: (
            <StrategyDiffPanel
              state={document}
              active={view === "diff"}
              revision={{
                strategyId,
                currentRevision: Number(revision),
              }}
            />
          ),
        }}
        notice={
          <>
            <UpgradeBanner
              upgrade={documentUpgrade}
              backtestRejected={backtestRejectedForUpgrade}
            />
            {status.kind === "conflict" &&
            status.strategyId !== null &&
            status.baseRevision !== null &&
            status.latestRevision !== null ? (
              <ConflictBanner
                strategyId={status.strategyId}
                baseRevision={status.baseRevision}
                latestRevision={status.latestRevision}
                source={document.source}
                onCreateRevision={createRevisionFromConflict}
                canCreateRevision={canCreateRevisionFromConflict}
              />
            ) : null}
          </>
        }
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
            publicationOwnerKey={debuggerPublicationOwner}
            asOf={search.asOf}
            security={search.security}
            selectedPointer={search.path}
            onSelectPointer={(pointer) => selectPointer(pointer, "outline")}
            onSearchSelection={(selection) =>
              void navigate({
                to: ROUTE,
                params: { strategyId, revision },
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
            <ServerDraftBanner sync={serverDraft} />
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
