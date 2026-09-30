import { useId, useMemo, useRef, useState } from "react";

import {
  BacktestResultFailure,
  BacktestRunDetail,
  BacktestRunFailure,
  runEnvironmentFields,
  useBacktestRequest,
  useBacktestResult,
  useBacktestStatus,
  useRunEnvironmentSchema,
} from "../../../entities/backtest";
import { AssistStrategySidebar } from "../../../features/assist-strategy";
import { BacktestRunActions } from "../../../features/run-backtest";
import { t } from "../../../shared/config";
import { Link, useNavigate, useParams } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import "./backtest-run-page.css";

const TONE = {
  queued: "info",
  running: "info",
  cancel_requested: "warn",
  cancelled: "warn",
  failed: "error",
  completed: "ok",
} as const;

/**
 * 결과 화면 우측 AI 패널의 열림 상태(결과 설명 spec R1). 페이지의 local UI state다.
 *
 * `mounted`는 처음 연 뒤로 계속 true다 — 닫았다 열어도 대화가 이어지고, 열기 전에는 어시스턴트
 * 질의가 나가지 않는다. 다른 실행으로 옮기면 둘 다 처음으로 되돌린다. 앞 실행에서 고른 대화가
 * 다음 실행의 사이드바에 남지 않게 하려는 것이다.
 */
type AssistantPanel = { runId: string; open: boolean; mounted: boolean };

/** Backtest run entry: live status while running, the full result once completed. */
export const BacktestRunPage = () => {
  const { runId } = useParams({ from: "/research/backtests/$runId" });
  const navigate = useNavigate();
  const status = useBacktestStatus(runId);
  const request = useBacktestRequest(runId);
  const completed = status.data?.status === "completed";
  const result = useBacktestResult(runId, completed);
  // run 상세의 실행 설정 칸 이름·단위·값 이름은 실행 설정 스키마에서 읽는다(DEFECT-242-04).
  const runEnvironmentSchema = useRunEnvironmentSchema();
  const environmentFields = useMemo(
    () =>
      runEnvironmentSchema.data === undefined
        ? null
        : runEnvironmentFields(runEnvironmentSchema.data.schema),
    [runEnvironmentSchema.data],
  );
  const assistantId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [panel, setPanel] = useState<AssistantPanel>({
    runId,
    open: false,
    mounted: false,
  });
  // 렌더 중에 실행 전환을 반영한다. effect로 미루면 앞 실행의 대화가 한 프레임 새 실행 화면에 보인다
  // (`frontend-react-effects.md`).
  if (panel.runId !== runId) {
    setPanel({ runId, open: false, mounted: false });
  }
  const toggleAssistant = () =>
    setPanel((previous) => ({
      ...previous,
      open: !previous.open,
      mounted: true,
    }));
  const closeAssistant = () => {
    setPanel((previous) => ({ ...previous, open: false }));
    toggleRef.current?.focus();
  };

  if (status.isPending) {
    return (
      <div className="page">
        <p className="page-state">{t("page.loading")}</p>
      </div>
    );
  }
  if (status.isError || !status.data) {
    return (
      <div className="page">
        <p className="page-state page-state--error" role="alert">
          {t("page.backtest.loadError")}
        </p>
      </div>
    );
  }
  const state = status.data;
  // 설명할 결과는 완료된 실행에만 있다. 진행 중·실패한 실행에는 진입점을 두지 않는다(spec R6).
  const assistantAvailable = completed && result.data !== undefined;
  const assistantOpen = assistantAvailable && panel.open;
  return (
    <div
      className={
        assistantOpen
          ? "page backtest-run-layout backtest-run-layout--assistant"
          : "page backtest-run-layout"
      }
    >
      <div className="backtest-run-layout__main">
        <header className="page-header">
          <h1>{t("page.backtest.title")}</h1>
          <code>{state.run_id}</code>
          <span role="status" aria-label={t("page.backtest.status")}>
            <Badge tone={TONE[state.status]}>{state.status}</Badge>
          </span>
          <BacktestRunActions
            runId={runId}
            status={state.status}
            request={request.data}
            requestFailed={request.isError}
            onReplayed={(nextRunId) =>
              void navigate({
                to: "/research/backtests/$runId",
                params: { runId: nextRunId },
              })
            }
          />
          {/* 새 실험 화면이 이 실행의 요청을 기반으로 읽는다. 저장 리비전만 기반이 되는지는 backend 가 판정한다. */}
          <Link
            className="ui-button ui-button--secondary ui-button--small"
            to="/research/experiments/new"
            search={{ run: runId }}
          >
            {t("backtest.actions.experiment")}
          </Link>
          {assistantAvailable ? (
            <Button
              ref={toggleRef}
              size="small"
              aria-expanded={assistantOpen}
              aria-controls={assistantId}
              onClick={toggleAssistant}
            >
              {t("page.backtest.askAi")}
            </Button>
          ) : null}
        </header>
        <p
          className="page-state"
          role="status"
          aria-label={t("page.backtest.progress")}
        >
          {state.stage} · {Math.round(state.progress * 100)}% · {state.message}
        </p>
        <BacktestRunFailure
          run={state}
          className="page-state page-state--error"
        />
        {completed && result.isPending ? (
          <p className="page-state" role="status">
            {t("page.loading")}
          </p>
        ) : null}
        <BacktestResultFailure
          error={result.error}
          className="page-state page-state--error"
        />
        {result.data ? (
          <BacktestRunDetail
            result={result.data}
            environmentFields={environmentFields}
          />
        ) : null}
      </div>
      {assistantAvailable && panel.mounted ? (
        // 결과 화면의 AI 패널. landmark·이름·제목은 여기가 소유하고 채팅 feature는 이름 없는
        // `<section>`이다(B-04 슬롯 계약과 같다). 모드는 서버가 세션(run_id)으로 정한다(spec R5).
        <aside
          id={assistantId}
          className="backtest-run-layout__assistant"
          aria-labelledby={`${assistantId}-title`}
          hidden={!assistantOpen}
        >
          <h2 id={`${assistantId}-title`}>{t("page.backtest.assistant")}</h2>
          <AssistStrategySidebar
            documentRef={{ run_id: runId }}
            copy="result"
            onClose={closeAssistant}
          />
        </aside>
      ) : null}
    </div>
  );
};
