import { useSuspenseQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { strategyDocumentQuery } from "../../../entities/strategy";
import {
  ContractInspector,
  ProposalApplyDialog,
  ProposalApplyFeedback,
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
  assistantDocumentRef,
  assistantProposalOf,
  useAssistantTurnContext,
  useApplyAssistantProposal,
  useApplyProposalThenBacktest,
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
import { AssistStrategySidebar } from "../../../features/assist-strategy";
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
  // AI 제안은 업그레이드 적용과 같은 전체 범위 교체 경로를 쓴다(SoT 규칙의 두 번째 예외).
  const proposalApply = useApplyAssistantProposal(document);
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
  const openForm = useCallback(
    (pointer: string): void => {
      void navigate({
        to: ROUTE,
        params: { strategyId, revision },
        search: { ...search, path: pointer, view: "form" },
        replace: true,
      });
    },
    [navigate, search, revision, strategyId],
  );
  const onOutlineEditorReady = outline.onEditorReady;
  const onSnippetEditorReady = snippets.onEditorReady;
  const onTransactionsEditorReady = transactions.onEditorReady;
  const onUpgradeEditorReady = documentUpgrade.onEditorReady;
  const onProposalEditorReady = proposalApply.onEditorReady;
  const onEditorReady = useCallback(
    (editor: CodeEditorHandle | null): void => {
      onOutlineEditorReady(editor);
      onSnippetEditorReady(editor);
      onTransactionsEditorReady(editor);
      onUpgradeEditorReady(editor);
      onProposalEditorReady(editor);
    },
    [
      onOutlineEditorReady,
      onProposalEditorReady,
      onSnippetEditorReady,
      onTransactionsEditorReady,
      onUpgradeEditorReady,
    ],
  );
  // 세션이 붙은 문서는 타자마다 바뀌지 않는다 — 문서 텍스트가 아니라 이 세 값에만 묶는다.
  const assistantDocumentRefValue = useMemo(
    () =>
      assistantDocumentRef(
        document.strategyId,
        document.baseRevision,
        serverDraftId,
      ),
    [document.strategyId, document.baseRevision, serverDraftId],
  );
  // 턴을 시작하는 순간의 문서·실행 설정을 읽는 손잡이. commit된 값을 비추는 ref라 사이드바에 넘기는
  // 함수는 그대로 두고도 늘 지금 화면의 값을 읽는다(`.claude/rules/frontend-react-effects.md`).
  // 턴은 세션 생성 왕복 뒤에 시작될 수 있다. 텍스트는 그 순간 편집기에서 직접 읽는다.
  const readAssistantContext = useAssistantTurnContext(
    document,
    proposalApply.readSource,
    runSettings.requestOptions,
  );
  const runBacktest = useCallback(() => void startBacktest(), [startBacktest]);
  // "적용 후 백테스트"는 적용 → 검증 → 실행을 한 동작으로 잇는다(WORKFLOW B-04).
  const proposalBacktest = useApplyProposalThenBacktest(
    proposalApply,
    document,
    { canRun, run: runBacktest },
  );
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
              selectedPointer={search.path}
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
            {/* 제안 적용 결과는 문서 알림 줄에 둔다 — 사이드바 레일에는 사이드바가 소유한
                라이브 영역 하나만 있어야 한다(B-03 리뷰). 알림 줄은 배너들의 원래 자리라 저장 충돌
                배너와 동시에 뜨면 라이브 영역이 둘이 될 수 있으나, 동시 노출이 드물고 두 문장 모두
                읽히는 편이 나아 그대로 둔다(B-04 리뷰 P3). */}
            <ProposalApplyFeedback
              apply={proposalApply}
              chain={proposalBacktest}
            />
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
        assistant={({ close }) => (
          <AssistStrategySidebar
            documentRef={assistantDocumentRefValue}
            readContext={readAssistantContext}
            onPreviewProposal={(action) =>
              proposalApply.preview(assistantProposalOf(action))
            }
            onApplyProposal={(action) =>
              proposalApply.apply(assistantProposalOf(action))
            }
            onApplyProposalAndBacktest={(action) =>
              proposalBacktest.applyThenBacktest(assistantProposalOf(action))
            }
            // 사이드바의 "닫기"는 진행 중 턴 취소를 확인한 뒤 패널을 접는다(spec D7). 패널 헤더의
            // 접기 버튼은 대화를 끝내지 않는 패널 조작이라 확인을 거치지 않는다.
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
      <ProposalApplyDialog apply={proposalApply} />
      <DirtyLeaveGuard dirty={document.dirty} />
    </>
  );
};
