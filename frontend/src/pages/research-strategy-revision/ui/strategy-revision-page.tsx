import { ApiRequestError } from "../../../shared/api";
import { useSuspenseQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { strategyDocumentQuery } from "../../../entities/strategy";
import { RunParameterSelection, useRunParameters } from "../../../entities/backtest";
import {
  ContractInspector,
  ProposalApplyDialog,
  ProposalApplyFeedback,
  ConflictBanner,
  DiagnosticsPanel,
  DirtyLeaveGuard,
  DocumentHistoryActions,
  DocumentStatus,
  DocumentToolbar,
  DocumentResearchActions,
  FactorGraphPanel,
  LibraryUpdateNotice,
  RecipePanel,
  factorIndexAtPointer,
  PipelinePanel,
  RecoveryBanner,
  ServerDraftBanner,
  SnippetCatalog,
  SourceEditor,
  StrategyDiffPanel,
  StrategyOutline,
  UpgradeBanner,
  STRATEGY_VIEWS,
  canValidateDocument,
  currentCompileOutcome,
  currentDiagnostics,
  revisionDraftId,
  saveStatusText,
  saveStatusSummary,
  saveStatusTone,
  useApplyAssistantProposal,
  useStrategyAssistant,
  useAutosave,
  useWholeDocumentReplacement,
  useEditorHandle,
  useCompileDocument,
  useDiagnosticNavigation,
  useDocumentHistory,
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
import { AssistStrategySidebar } from "../../../features/assist-strategy";
import {
  BacktestRunSettings,
  RunEnvironmentSummary,
  useBacktestRunSettings,
} from "../../../features/run-backtest";
import { t } from "../../../shared/config";
import { useNavigate, useParams, useSearch } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import {
  StrategyDebuggerPanel,
  StrategyPreviewPanel,
  StrategyIde,
} from "../../../widgets/strategy-ide";

const ROUTE = "/research/strategies/$strategyId/revisions/$revision";

/** ISO timestamp → "YYYY-MM-DD HH:MM" without locale surprises. */
const shortTimestamp = (iso: string): string =>
  iso.length >= 16 ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso;

/**
 * 저장된 revision 진입점(WORKFLOW P2-04). 저장된 문서 원문은 route loader가 데운 query cache에서 오고
 * (ADR D4) draft의 출발점이 된다. 저장은 다음 revision을 덧붙이고 URL이 따라간다. 선택된 view는 URL search에
 * 있으며 navigation을 막지 않는다(ADR D3): 저장된 포맷을 제자리에서 편집하고, JSON/Diff는 현재 backend
 * compile(또는 명시적으로 stale인 같은 문서의 fallback)의 읽기 전용 투영이며, Form·Graph는 같은 문서 위의
 * source 트랜잭션 편집기다(ADR D5 2026-09-18 개정). 아직 없는 view는 빈 탭 대신 안내와 함께 저장된 포맷으로
 * 돌아간다.
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
  const editorOwner = useEditorHandle();
  const replacement = useWholeDocumentReplacement(editorOwner);
  const serverDraft = useServerDraft(document, replacement.replace, {
    draftId: serverDraftId,
    schemaVersion: assist.schemaVersion ?? stored.schema_version,
    schemaPending: assist.loading,
  });
  const { validateNow, validating } = useCompileDocument(document, dispatch);
  const canValidate = !validating && canValidateDocument(document);
  const autosave = useAutosave(document, replacement.replace, {
    schemaVersion: assist.schemaVersion,
  });
  // 실행 설정(시장·기간·유니버스·체결·비용·결측)은 전략 문서 밖에 있고 패널이 owner 다(schema 1.2,
  // P3-02). 마지막 사용값은 이 전략의 local UI state 다.
  const runSettings = useBacktestRunSettings(strategyId);
  const runParameters = useRunParameters(search.run, { strategyId, revision: Number(revision), specHash: stored.spec_hash });
  const runOptions = useMemo(() => {
    if (!runParameters.ready || runSettings.requestOptions === null) return null;
    return runParameters.state === "selected"
      ? { ...runSettings.requestOptions, parameter_values: runParameters.values }
      : runSettings.requestOptions;
  }, [runParameters.ready, runParameters.state, runParameters.values, runSettings.requestOptions]);
  // 실행 계획 sandbox 도 실행과 같은 결측 정책을 싣는다(Phase 2 감사 #3). 기간·유니버스가 비어도
  // 결측 정책 칸 값은 따로 싣는다(#357 C-P3-16).
  const executionPlans = useExecutionPlans(
    document,
    assist.inspectorSource,
    runSettings.missing,
  );
  const backtest = useRunBacktest(
    document,
    executionPlans,
    runOptions,
    search.run,
  );
  const startBacktest = backtest.run;
  // 저장된 1.0 revision 참조로 보낸 실행을 backend가 거부한 경우: 같은 배너로 안내하고 실행을 막는다.
  const backtestRejectedForUpgrade =
    backtest.status.kind === "failed" &&
    backtest.status.error instanceof ApiRequestError &&
    backtest.status.error.code === "backtest.strategy.requires_upgrade";
  const canRun = backtest.canRun && !backtestRejectedForUpgrade;
  const storedMeta = useMemo(
    () => ({
      revision: stored.revision,
      generated: stored.generated,
      requires_upgrade: stored.requires_upgrade,
    }),
    [stored.generated, stored.requires_upgrade, stored.revision],
  );
  const documentUpgrade = useUpgradeDocument(editorOwner, document, storedMeta);
  // AI 제안은 업그레이드 적용과 같은 전체 범위 교체 경로를 쓴다(SoT 규칙의 두 번째 예외).
  const proposalApply = useApplyAssistantProposal(editorOwner, document);
  const current = currentCompileOutcome(document);
  const availableViews: readonly StrategyView[] = STRATEGY_VIEWS;
  const view: StrategyView = search.view === "yaml" ? "yaml" : "graph";
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
        // 커서 게시는 parse가 끝난 뒤 effect에서 늦게 올 수 있다. 렌더 시점 `search`를 펼치면 그사이 탭 전환이
        // 바꾼 `view`를 옛 값으로 되돌린다 — 라우터의 최신 위치(`prev`)에 `path`만 얹는다.
        search: (prev) => ({
          ...prev,
          path,
          recipe: path?.includes("/graph") ? prev.recipe : undefined,
          view: origin === "outline" ? "yaml" : prev.view,
        }),
        replace: true,
        // 문서 안 선택은 reveal 경로가 스크롤을 소유한다. URL 갱신이 페이지를 맨 위로 돌리면 안 된다.
        resetScroll: false,
      });
    },
    [navigate, revision, strategyId],
  );
  const outline = useOutlineNavigation(editorOwner, {
    state: document,
    schema: assist.schema,
    revealSelectedPointer: view === "yaml",
    selectedPointer: search.path,
    onSelectedPointer: selectPointer,
  });
  // source 트랜잭션 인스턴스는 page가 하나 만들어 Form·스니펫이 공유한다(WORKFLOW P4-04). hidden 편집기가
  // 살아 있으므로 editorActive는 참이고, 스니펫만 source view 게이트를 따로 지킨다.
  const transactions = useSourceTransactions(editorOwner, document, true);
  const snippets = useSnippetInsertion(
    assist.snippetSource,
    view === "yaml",
    transactions,
  );
  const form = useFormProjection(document, assist.schema);
  const catalogs = assist.fieldPickerCatalogs;
  const openGraph = useCallback(
    (pointer: string): void => {
      void navigate({
        to: ROUTE,
        params: { strategyId, revision },
        search: { ...search, path: pointer, view: "graph", recipe: true },
        resetScroll: false,
      });
    },
    [navigate, search, revision, strategyId],
  );
  const openSourceAt = useCallback(
    (pointer: string | undefined): void => {
      if (pointer !== undefined) outline.requestSourceReveal(pointer);
      selectPointer(pointer, "outline");
    },
    [outline, selectPointer],
  );
  // outline 다음에 부른다 — 탭 전환 뒤 진단 범위로 가는 effect가 outline의 pointer reveal 뒤에 서야
  // 더 정확한 범위가 남는다(WORKFLOW P1-01).
  const problems = useDiagnosticNavigation(editorOwner, {
    state: document,
    view,
    sourceView: "yaml",
    form: form.projection,
    tree: form.tree,
    schema: assist.schema,
    onSelectPointer: (pointer) => selectPointer(pointer, "graph"),
    onOpenSource: openSourceAt,
  });
  // 되돌리기·다시 실행은 편집기 이력 하나가 owner다(WORKFLOW P1-02). 탭 목록 줄의 버튼(탭 패널 밖)과
  // IDE 전역 단축키가 같은 명령을 부른다.
  const history = useDocumentHistory(editorOwner);
  const runBacktest = useCallback(() => void startBacktest(), [startBacktest]);
  // 어시스턴트 사이드바 배선(문서 참조·턴 컨텍스트·"적용 후 백테스트"·제안 카드 동작). 두 전략 화면이
  // 같은 훅을 써서 한쪽만 콜백을 잃지 않는다(Phase B 감사 NB-8).
  const strategyAssistant = useStrategyAssistant(proposalApply, document, {
    draftId: serverDraftId,
    environment: runSettings.environment,
    backtest: { canRun, settling: backtest.settling, run: runBacktest },
  });
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
        research={({ openEditor }) => <DocumentResearchActions
          state={document} request={backtest.request}
          requiresUpgrade={documentUpgrade.availability.kind !== "none"}
          parametersReady={runParameters.ready}
          selectedParameters={runParameters.state === "selected"}
          recipeOpen={view === "graph" && search.recipe === true}
          onToggleRecipe={() => { const closing = view === "graph" && search.recipe === true; openEditor(); void navigate({ to: ROUTE, params: { strategyId, revision }, search: { ...search, view: "graph", recipe: closing ? undefined : true, path: closing ? undefined : `/factors/${factorIndexAtPointer(search.path) ?? 0}/graph` }, replace: true, resetScroll: false }); }}
          onSend={(request) => void navigate({ to: "/research/experiments/new", search: {}, state: { experimentSeed: request } })}
        />}
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
        saveStatusSummary={saveStatusSummary(document, status)}
        onRunBacktest={runBacktest}
        runDisabled={!canRun}
        onValidate={validateNow}
        validateDisabled={!canValidate}
        onSave={save}
        saveDisabled={!canSave}
        onUndo={history.undo}
        onRedo={history.redo}
        symbols={outline.symbols}
        onSelectSymbol={selectSymbol}
        saveTone={saveStatusTone(document, status)}
        view={view}
        sourceView="yaml"
        availableViews={availableViews}
        onViewChange={(next) =>
          void navigate({
            to: ROUTE,
            params: { strategyId, revision },
            search: {
              ...search,
              view: next,
            },
            replace: true,
          })
        }
        runEnvironment={<RunEnvironmentSummary controller={runSettings} />}
        documentHistory={<DocumentHistoryActions history={history} />}
        documentStatus={<DocumentStatus state={document} />}
        problems={
          <DiagnosticsPanel
            diagnostics={problems.diagnostics}
            stale={problems.stale}
            onSelect={problems.selectDiagnostic}
          />
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
                : (runSettings.blockedReason ?? undefined)
            }
            runSettings={
              <BacktestRunSettings
                controller={runSettings}
                disabled={backtest.status.kind === "starting"}
                request={backtest.request}
              />
            }
            runFieldLabel={runSettings.runFieldLabel}
            runRejectionFix={runSettings.rejectionFix}
            decision={backtest.decision}
            runStatus={backtest.status}
          />
        }
        comparisonOpen={search.compare}
        onComparisonOpenChange={(open) =>
          void navigate({
            to: ROUTE,
            params: { strategyId, revision },
            search: { ...search, compare: open || undefined },
            replace: true,
          })
        }
        revisionDiff={(active) => (
          <StrategyDiffPanel
            state={document}
            active={active}
            revision={{ strategyId, currentRevision: Number(revision) }}
          />
        )}
        projections={{
          graph:
            !search.recipe &&
            !(
              factorIndexAtPointer(search.path) !== null &&
              search.path?.includes("/graph")
            ) ? (
              <section
                className="pipeline-comparison"
                aria-label={t("graph.pipeline.comparison")}
              >
                <nav
                  className="pipeline-comparison__navigation"
                  aria-label={t("graph.pipeline.comparison")}
                >
                  <a className="ui-button" href="#strategy-rules">
                    {t("graph.pipeline.rulesSection")}
                  </a>
                  <a className="ui-button" href="#selection-results">
                    {t("graph.pipeline.resultsSection")}
                  </a>
                </nav>
                <div className="pipeline-comparison__body">
                  <div id="strategy-rules" tabIndex={-1}>
                    <PipelinePanel
                      librarySave
                      form={form}
                      schema={assist.schema}
                      transactions={transactions}
                      catalogs={catalogs}
                      onOpenGraph={openGraph}
                      onOpenAdvanced={(pointer) =>
                        void navigate({
                          to: ROUTE,
                          params: { strategyId, revision },
                          search: {
                            ...search,
                            path: pointer,
                            view: "graph",
                            recipe: undefined,
                          },
                          resetScroll: false,
                        })
                      }
                      selectedPointer={search.path}
                      revealSignal={problems.revealSignal}
                    />
                  </div>
                  <aside
                    id="selection-results"
                    tabIndex={-1}
                    className="pipeline-comparison__result"
                    aria-label={t("graph.pipeline.selectionResult")}
                  >
                    <StrategyPreviewPanel
                      document={document}
                      executionPlans={executionPlans}
                      environment={runSettings.environment}
                      parameters={runParameters}
                      onTraceSelection={(selection) =>
                        void navigate({
                          to: ROUTE,
                          params: { strategyId, revision },
                          search: { ...search, ...selection },
                          replace: true,
                          resetScroll: false,
                        })
                      }
                    />
                  </aside>
                </div>
              </section>
            ) : search.recipe &&
              !form.firstParsePending &&
              assist.schema !== null ? (
              <RecipePanel
                tree={form.tree}
                schema={assist.schema}
                factorIndex={factorIndexAtPointer(search.path) ?? 0}
                transactions={transactions}
                catalogs={catalogs}
                operators={assist.operators}
                diagnostics={currentDiagnostics(document)}
                plans={executionPlans}
                selectedPointer={search.path}
                revealSignal={problems.revealSignal}
                onSelectPointer={(pointer) => selectPointer(pointer, "graph")}
                onAdvanced={() =>
                  void navigate({
                    to: ROUTE,
                    params: { strategyId, revision },
                    search: {
                      ...search,
                      path: search.path?.includes("/graph")
                        ? search.path
                        : `/factors/${factorIndexAtPointer(search.path) ?? 0}/graph`,
                      recipe: undefined,
                    },
                    resetScroll: false,
                  })
                }
                onBack={() =>
                  void navigate({
                    to: ROUTE,
                    params: { strategyId, revision },
                    search: { ...search, recipe: undefined, path: undefined },
                    resetScroll: false,
                  })
                }
              />
            ) : (
              <section className="graph-authoring">
                <header className="graph-authoring__navigation">
                  <Button
                    tone="ghost"
                    onClick={() =>
                      void navigate({
                        to: ROUTE,
                        params: { strategyId, revision },
                        search: {
                          ...search,
                          recipe: undefined,
                          path: undefined,
                        },
                        resetScroll: false,
                      })
                    }
                  >
                    {t("graph.backToStrategy")}
                  </Button>
                </header>
                <FactorGraphPanel
                  state={executionPlans}
                  schema={assist.schema}
                  diagnostics={currentDiagnostics(document)}
                  selectedPointer={search.path}
                  revealSignal={problems.revealSignal}
                  // 첫 parse 전 tree 는 비어 있어 편집기가 "팩터가 없습니다"를 그린다. 캔버스처럼 기다린다(#413).
                  editing={
                    form.firstParsePending
                      ? undefined
                      : {
                          tree: form.tree,
                          transactions,
                          catalogs,
                          operators: assist.operators,
                          onOpenSource: openSourceAt,
                          documentKey: document.documentEpoch,
                          sourceVersion: document.sourceVersion,
                          active: view === "graph" && !search.recipe,
                        }
                  }
                  onSelectPointer={(pointer) => selectPointer(pointer, "graph")}
                  onOpenSource={(pointer) => {
                    outline.requestSourceReveal(pointer);
                    selectPointer(pointer, "outline");
                  }}
                />
              </section>
            ),
        }}
        notice={
          <>
            <RunParameterSelection selection={runParameters} clear={() => {
              void navigate({ to: ROUTE, params: { strategyId, revision }, search: { ...search, run: undefined }, replace: true, resetScroll: false });
            }} />
            {autosave.recovery ? (
              <RecoveryBanner recovery={autosave.recovery} />
            ) : null}
            <ServerDraftBanner sync={serverDraft} />
            {/* 제안 적용 결과는 문서 알림 줄에 둔다 — 사이드바 레일에는 사이드바가 소유한
                라이브 영역 하나만 있어야 한다(B-03 리뷰). 알림 줄은 배너들의 원래 자리라 저장 충돌
                배너와 동시에 뜨면 라이브 영역이 둘이 될 수 있으나, 동시 노출이 드물고 두 문장 모두
                읽히는 편이 나아 그대로 둔다(B-04 리뷰 P3). */}
            <ProposalApplyFeedback
              apply={proposalApply}
              chain={strategyAssistant.chain}
              blockedReason={runSettings.blockedReason}
            />
            <LibraryUpdateNotice
              document={document}
              form={form}
              transactions={transactions}
            />
            <UpgradeBanner
              upgrade={documentUpgrade}
              backtestRejected={backtestRejectedForUpgrade}
              onApplyEnvironment={runSettings.applyEnvironment}
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
        outline={({ inspect }) => (
          <StrategyOutline
            snapshot={outline.snapshot}
            selectedPointer={search.path}
            onSelect={(node) => {
              outline.onSelectOutlineNode(node);
              inspect();
            }}
            onCollapse={outline.onCollapseOutlineNode}
          />
        )}
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
        assistant={({ close }) => (
          <AssistStrategySidebar
            documentRef={strategyAssistant.documentRef}
            readContext={strategyAssistant.readContext}
            {...strategyAssistant.proposalHandlers}
            // 닫기는 사이드바가 그린다(슬롯 헤더는 제목만) — 진행 중 턴 취소를 확인한 뒤 패널을
            // 접는다(spec D7). 상단 바 토글과 Alt+A는 대화를 끝내지 않는 패널 조작이라 그대로다.
            onClose={close}
          />
        )}
        debugger={
          <StrategyDebuggerPanel
            document={document}
            executionPlans={executionPlans}
            environment={runSettings.environment}
            parameters={runParameters}
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
            <SourceEditor
              state={document}
              dispatch={dispatch}
              assist={assist}
              onEditorReady={editorOwner.onEditorReady}
              onSelectionChange={outline.onEditorSelectionChange}
            />
          </>
        }
      />
      <ProposalApplyDialog apply={proposalApply} />
      <DirtyLeaveGuard dirty={document.dirty} acceptedRunId={backtest.status.kind === "accepted" ? backtest.status.runId : null} />
    </>
  );
};
