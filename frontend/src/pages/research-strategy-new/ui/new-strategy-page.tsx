import { useCallback, useEffect, useState } from "react";

import {
  ContractInspector,
  DiagnosticsPanel,
  ProposalApplyDialog,
  ProposalApplyFeedback,
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
  StrategyGuide,
  StrategyOutline,
  UpgradeBanner,
  STRATEGY_VIEWS,
  canValidateDocument,
  currentCompileOutcome,
  currentDiagnostics,
  createNewDraftId,
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
import { useNavigate, useSearch } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import {
  StrategyDebuggerPanel,
  StrategyPreviewPanel,
  StrategyIde,
} from "../../../widgets/strategy-ide";

/** 새 전략 화면의 실행 설정 마지막 사용값 칸(`useBacktestRunSettings` 의 저장 키). */
const NEW_STRATEGY_RUN_SETTINGS_KEY = "new";

/**
 * 새 문서 시작 텍스트. `schema_version` 리터럴은 backend runtime schema의 `const`와 같아야 하며
 * `__tests__/new-strategy-starter.test.ts`가 fixture로 단언한다(Phase 2 감사 DEFECT-P2X-001).
 */
export const NEW_STRATEGY_STARTER = 'schema_version: "1.2"\ntitle: ""\n';
const ROUTE = "/research/strategies/new";
const NEW_DRAFT: DocumentSource = {
  kind: "new",
  format: "yaml",
  source: NEW_STRATEGY_STARTER,
};

/**
 * New-strategy entry (WORKFLOW P2-04): a draft with no base. Saving creates the strategy, after
 * which the URL moves to revision 1 so a reload lands on the saved document (router ADR D3).
 */
export const NewStrategyPage = () => {
  const navigate = useNavigate();
  const search = useSearch({ from: ROUTE });
  const [document, dispatch] = useStrategyDocument(NEW_DRAFT);
  // The recovery identity exists before the first paint. A URL-only effect leaves a short
  // draftId=null window in which an immediate edit can be mistaken for an already-synced base.
  const [entryDraftId] = useState(createNewDraftId);
  const { save, status, canSave } = useSaveDocument(document, dispatch);
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
      : (search.draft ?? entryDraftId);
  const editorOwner = useEditorHandle();
  const replacement = useWholeDocumentReplacement(editorOwner);
  const serverDraft = useServerDraft(document, replacement.replace, {
    draftId: serverDraftId,
    schemaVersion: assist.schemaVersion,
    schemaPending: assist.loading,
  });
  const { validateNow, validating } = useCompileDocument(document, dispatch);
  // 붙여 넣은 은퇴 버전 문서도 revision 화면과 같은 경로로 올린다(#257). 저장본이 없으므로 판정은
  // 현재 텍스트의 compile 진단만 읽는다.
  const documentUpgrade = useUpgradeDocument(editorOwner, document, null);
  // AI 제안은 업그레이드 적용과 같은 전체 범위 교체 경로를 쓴다(SoT 규칙의 두 번째 예외).
  const proposalApply = useApplyAssistantProposal(editorOwner, document);
  const canValidate = !validating && canValidateDocument(document);
  const autosave = useAutosave(document, replacement.replace, {
    schemaVersion: assist.schemaVersion,
  });
  // 실행 설정은 전략 문서 밖에 있고 패널이 owner 다(schema 1.2, P3-02). 새 전략은 전략 id 가 없어 한
  // 칸(`new`)에 두고, 저장 뒤 revision 화면은 마지막 사용값으로 이어받는다.
  const runSettings = useBacktestRunSettings(NEW_STRATEGY_RUN_SETTINGS_KEY);
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
    runSettings.requestOptions,
  );
  const startBacktest = backtest.run;
  const current = currentCompileOutcome(document);
  const availableViews: readonly StrategyView[] = STRATEGY_VIEWS;
  const view: StrategyView = search.view === "yaml" ? "yaml" : "graph";
  // The page/router owns URL generations. The debugger treats this as an opaque publication
  // lease, so an older async trace cannot replay a callback that captured an older search object.
  const debuggerPublicationOwner = JSON.stringify([ROUTE, search]);
  const selectPointer = useCallback(
    (
      path: string | undefined,
      origin: "cursor" | "outline" | "outline-collapse" | "graph" = "outline",
    ) => {
      void navigate({
        to: ROUTE,
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
    [navigate],
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

        search: { ...search, path: pointer, view: "graph", recipe: true },
        resetScroll: false,
      });
    },
    [navigate, search],
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
    backtest: {
      canRun: backtest.canRun,
      settling: backtest.settling,
      run: runBacktest,
    },
  });
  const selectSymbol = useCallback(
    (pointer: string): void => {
      outline.requestSourceReveal(pointer);
      selectPointer(pointer, "outline");
    },
    [outline, selectPointer],
  );

  useEffect(() => {
    if (search.draft !== undefined) return;
    void navigate({
      to: ROUTE,
      search: { ...search, draft: entryDraftId },
      replace: true,
    });
  }, [entryDraftId, navigate, search]);

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
        draft: undefined,
      },
      replace: true,
    });
  }, [leaving, document.strategyId, document.baseRevision, navigate]);

  // 단계별 안내(RD-04-D). 같은 문서·같은 트랜잭션을 쓰고, 편집기는 숨긴 채 살아 있어 되돌리기 이력이 이어진다.
  // 안내를 닫으면 같은 초안을 편집기 그래프 보기로 연다.
  const leaveGuide = (next: Partial<typeof search>) =>
    void navigate({
      to: ROUTE,
      // 숨은 편집기의 커서가 남긴 선택(`path`)은 버린다. 남기면 그 팩터의 노드 편집기가 열린다.
      search: { ...search, guide: undefined, step: undefined, path: undefined, recipe: undefined, ...next },
      resetScroll: false,
    });
  if (search.guide)
    return (
      <>
        <div className="page">
          {autosave.recovery ? (
            <RecoveryBanner recovery={autosave.recovery} />
          ) : null}
          <StrategyGuide
            status={<ServerDraftBanner sync={serverDraft} />}
            form={form}
            schema={assist.schema}
            transactions={transactions}
            catalogs={catalogs}
            snippetSource={assist.snippetSource}
            source={document.source}
            step={search.step}
            onStep={(step) =>
              void navigate({
                to: ROUTE,
                search: { ...search, step },
                replace: true,
                resetScroll: false,
              })
            }
            onOpenGraph={(pointer) =>
              leaveGuide({ path: pointer, view: "graph", recipe: true })
            }
            onOpenEditor={() => leaveGuide({ view: "graph" })}
            onUndo={history.undo}
            canUndo={history.depth.undo > 0}
            review={
              <>
                <DiagnosticsPanel
                  diagnostics={problems.diagnostics}
                  stale={problems.stale}
                  onSelect={(diagnostic) =>
                    leaveGuide({
                      view: "yaml",
                      path: diagnostic.pointer === "" ? undefined : diagnostic.pointer,
                    })
                  }
                />
                <StrategyPreviewPanel
                  document={document}
                  executionPlans={executionPlans}
                  environment={runSettings.environment}
                />
                <div className="strategy-guide__review-actions">
                  <Button tone="primary" onClick={save} disabled={!canSave}>
                    {t("guide.saveAndPrepare")}
                  </Button>
                  <Button tone="secondary" onClick={() => leaveGuide({ view: "graph" })}>
                    {t("guide.openInEditor")}
                  </Button>
                  <span role="status">
                    {canSave ? saveStatusText(document, status) : t("guide.saveBlocked")}
                  </span>
                </div>
              </>
            }
          />
          {/* 트랜잭션은 편집기 핸들로 적용된다. 안내 동안에도 편집기를 띄워 두되 보이지 않게 한다. */}
          <div hidden>
            <SourceEditor
              state={document}
              dispatch={dispatch}
              assist={assist}
              onEditorReady={editorOwner.onEditorReady}
              onSelectionChange={outline.onEditorSelectionChange}
            />
          </div>
        </div>
        <DirtyLeaveGuard dirty={document.dirty && !leaving} acceptedRunId={null} />
      </>
    );

  return (
    <>
      <StrategyIde
        notice={
          <>
            {autosave.recovery ? (
              <RecoveryBanner recovery={autosave.recovery} />
            ) : null}
            <ServerDraftBanner sync={serverDraft} />
            {/* 제안 적용 결과는 문서 알림 줄에 둔다 — 사이드바 레일에는 사이드바가 소유한
                라이브 영역 하나만 있어야 한다(B-03 리뷰). */}
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
              onApplyEnvironment={runSettings.applyEnvironment}
            />
          </>
        }
        title={t("page.newStrategy.title")}
        versionLabel={t("page.newStrategy.draft")}
        badges={<Badge tone="info">{t("page.newStrategy.draft")}</Badge>}
        research={({ openEditor }) => <DocumentResearchActions
          state={document} request={backtest.request}
          requiresUpgrade={documentUpgrade.availability.kind !== "none"}
          recipeOpen={view === "graph" && search.recipe === true}
          onToggleRecipe={() => { const closing = view === "graph" && search.recipe === true; openEditor(); void navigate({ to: ROUTE, search: { ...search, view: "graph", recipe: closing ? undefined : true, path: closing ? undefined : `/factors/${factorIndexAtPointer(search.path) ?? 0}/graph` }, replace: true, resetScroll: false }); }}
          onSend={(request) => void navigate({ to: "/research/experiments/new", search: {}, state: { experimentSeed: request } })}
        />}
        meta={{
          schemaVersion: current?.schemaVersion ?? null,
          sourceHash: current?.sourceHash ?? null,
          specHash: current?.specHash ?? null,
        }}
        saveStatus={saveStatusText(document, status)}
        saveStatusSummary={saveStatusSummary(document, status)}
        onRunBacktest={runBacktest}
        runDisabled={!backtest.canRun}
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
            canRun={backtest.canRun}
            runBlockedReason={runSettings.blockedReason ?? undefined}
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
                      onTraceSelection={(selection) =>
                        void navigate({
                          to: ROUTE,
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
            publicationOwnerKey={debuggerPublicationOwner}
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
      <DirtyLeaveGuard dirty={document.dirty && !leaving} acceptedRunId={backtest.status.kind === "accepted" ? backtest.status.runId : null} />
    </>
  );
};
