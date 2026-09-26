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
  FactorGraphPanel,
  RecoveryBanner,
  ServerDraftBanner,
  SnippetCatalog,
  SourceEditor,
  StrategyFormPanel,
  StrategyProjectionPanel,
  StrategyDiffPanel,
  StrategyOutline,
  PROJECTION_VIEWS,
  canValidateDocument,
  currentDiagnostics,
  createNewDraftId,
  projectStrategySpec,
  revisionDraftId,
  saveStatusText,
  saveStatusTone,
  useApplyAssistantProposal,
  useStrategyAssistant,
  useAutosave,
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
  type DocumentSource,
  type StrategyView,
} from "../../../features/edit-strategy";
import { AssistStrategySidebar } from "../../../features/assist-strategy";
import {
  BacktestRunSettings,
  useBacktestRunSettings,
} from "../../../features/run-backtest";
import { t } from "../../../shared/config";
import { useNavigate, useSearch } from "../../../shared/lib/router";
import { Badge, type CodeEditorHandle } from "../../../shared/ui";
import {
  StrategyDebuggerPanel,
  StrategyIde,
} from "../../../widgets/strategy-ide";

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
  const serverDraft = useServerDraft(document, dispatch, {
    draftId: serverDraftId,
    schemaVersion: assist.schemaVersion,
    schemaPending: assist.loading,
  });
  const { validateNow, validating } = useCompileDocument(document, dispatch);
  // AI 제안은 업그레이드 적용과 같은 전체 범위 교체 경로를 쓴다(SoT 규칙의 두 번째 예외).
  const proposalApply = useApplyAssistantProposal(document);
  const canValidate = !validating && canValidateDocument(document);
  const autosave = useAutosave(document, dispatch, {
    schemaVersion: assist.schemaVersion,
  });
  const executionPlans = useExecutionPlans(document, assist.inspectorSource);
  // 실행 기간의 owner가 전략 문서에서 실행 설정으로 옮겨갔다(schema 1.2). 그 값을 편집하는
  // 실행 설정 패널은 P3-01·P3-02에서 붙으므로 그때까지 기간은 지정되지 않은 상태다.
  const runDateRange = null;
  const runSettings = useBacktestRunSettings(runDateRange);
  const backtest = useRunBacktest(
    document,
    executionPlans,
    runSettings.requestOptions,
  );
  const startBacktest = backtest.run;
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
  // source 트랜잭션 인스턴스는 page가 하나 만들어 Form·스니펫이 공유한다(WORKFLOW P4-04). hidden 편집기가
  // 살아 있으므로 editorActive는 참이고, 스니펫만 source view 게이트를 따로 지킨다.
  const transactions = useSourceTransactions(document, true);
  const snippets = useSnippetInsertion(
    document,
    assist.snippetSource,
    view === document.format,
    transactions,
  );
  const form = useFormProjection(document, assist.schema);
  const openGraph = useCallback(
    (pointer: string): void => {
      void navigate({
        to: ROUTE,

        search: { ...search, path: pointer, view: "graph" },
        replace: true,
      });
    },
    [navigate, search],
  );
  const openForm = useCallback(
    (pointer: string): void => {
      void navigate({
        to: ROUTE,

        search: { ...search, path: pointer, view: "form" },
        replace: true,
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
  const problems = useDiagnosticNavigation({
    state: document,
    view,
    sourceView: document.format,
    form: form.projection,
    tree: form.tree,
    schemaLoaded: assist.schema !== null,
    onSelectPointer: (pointer) => selectPointer(pointer, "graph"),
    onOpenSource: openSourceAt,
  });
  // 되돌리기·다시 실행은 편집기 이력 하나가 owner다(WORKFLOW P1-02). 탭 목록 줄의 버튼(탭 패널 밖)과
  // IDE 전역 단축키가 같은 명령을 부른다.
  const history = useDocumentHistory(document);
  const onOutlineEditorReady = outline.onEditorReady;
  const onSnippetEditorReady = snippets.onEditorReady;
  const onTransactionsEditorReady = transactions.onEditorReady;
  const onProblemsEditorReady = problems.onEditorReady;
  const onHistoryEditorReady = history.onEditorReady;
  const onProposalEditorReady = proposalApply.onEditorReady;
  const onEditorReady = useCallback(
    (editor: CodeEditorHandle | null): void => {
      onOutlineEditorReady(editor);
      onSnippetEditorReady(editor);
      onTransactionsEditorReady(editor);
      onProblemsEditorReady(editor);
      onHistoryEditorReady(editor);
      onProposalEditorReady(editor);
    },
    [
      onOutlineEditorReady,
      onProposalEditorReady,
      onSnippetEditorReady,
      onTransactionsEditorReady,
      onProblemsEditorReady,
      onHistoryEditorReady,
    ],
  );
  const runBacktest = useCallback(() => void startBacktest(), [startBacktest]);
  // 어시스턴트 사이드바 배선(문서 참조·턴 컨텍스트·"적용 후 백테스트"·제안 카드 동작). 두 전략 화면이
  // 같은 훅을 써서 한쪽만 콜백을 잃지 않는다(Phase B 감사 NB-8).
  const strategyAssistant = useStrategyAssistant(proposalApply, document, {
    draftId: serverDraftId,
    environment: runSettings.requestOptions,
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

  return (
    <>
      <StrategyIde
        notice={
          /* 제안 적용 결과는 문서 알림 줄에 둔다 — 사이드바 레일에는 사이드바가 소유한
             라이브 영역 하나만 있어야 한다(B-03 리뷰). */
          <ProposalApplyFeedback
            apply={proposalApply}
            chain={strategyAssistant.chain}
          />
        }
        title={t("page.newStrategy.title")}
        versionLabel={t("page.newStrategy.draft")}
        badges={<Badge tone="info">{t("page.newStrategy.draft")}</Badge>}
        meta={{
          schemaVersion: current?.schemaVersion ?? null,
          sourceHash: current?.sourceHash ?? null,
          specHash: current?.specHash ?? null,
        }}
        saveStatus={saveStatusText(document, status)}
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
            runBlockedReason={
              runSettings.result.valid
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
          json: <StrategyProjectionPanel projection={projection} />,
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
              selectedPointer={search.path}
              revealSignal={problems.revealSignal}
            />
          ),
          graph: (
            <FactorGraphPanel
              state={executionPlans}
              diagnostics={currentDiagnostics(document)}
              selectedPointer={search.path}
              revealSignal={problems.revealSignal}
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
                operators: assist.operators,
                onOpenForm: openForm,
                documentKey: document.documentEpoch,
              }}
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
      <ProposalApplyDialog apply={proposalApply} />
      <DirtyLeaveGuard dirty={document.dirty && !leaving} />
    </>
  );
};
