import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import type { LedgerRefreshBody } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import {
  REFRESH_STEPS,
  currentStep,
  downloadFraction,
  isRefreshing,
  ledgerRefreshQuery,
  useCheckLedgerRefresh,
  useStartLedgerRefresh,
  type RefreshStep,
} from "../model/refresh-queries";
import "./ledger-refresh.css";

const STEP_LABEL = {
  download: "refresh.stage.download",
  catalog: "refresh.stage.catalog",
  verify: "refresh.stage.verify",
  apply: "refresh.stage.apply",
} as const satisfies Record<RefreshStep, string>;

const megabytes = (bytes: number): string => `${(bytes / 1e6).toFixed(1)}MB`;

const minutesSince = (
  from: string | null | undefined,
  now: number,
): number | null => {
  if (!from) return null;
  const started = Date.parse(from);
  return Number.isNaN(started)
    ? null
    : Math.max(0, Math.floor((now - started) / 60_000));
};

const StatusBadge = ({ status }: { status: LedgerRefreshBody }) => {
  if (isRefreshing(status))
    return <Badge tone="info">{t("refresh.badge.running")}</Badge>;
  if (status.code === "partial")
    return <Badge tone="warn">{t("refresh.badge.partial")}</Badge>;
  if (status.remote.state === "behind")
    return <Badge tone="warn">{t("refresh.badge.behind")}</Badge>;
  if (status.remote.state === "up_to_date")
    return <Badge tone="neutral">{t("refresh.badge.upToDate")}</Badge>;
  return null;
};

const Progress = ({
  status,
  now,
}: {
  status: LedgerRefreshBody;
  now: number;
}) => {
  const step = currentStep(status);
  const at = step === null ? -1 : REFRESH_STEPS.indexOf(step);
  const fraction = downloadFraction(status);
  const progress = status.progress;
  const elapsed = minutesSince(
    progress?.stage_started_at ?? status.started_at,
    now,
  );
  return (
    <>
      <ol className="refresh-ledger__steps">
        {REFRESH_STEPS.map((name, index) => {
          const state =
            index < at ? "done" : index === at ? "active" : "waiting";
          const detail =
            state === "active" && name === "download" && progress?.tables_total
              ? tFill("refresh.tables", {
                  done: progress.tables_done,
                  total: progress.tables_total,
                })
              : state === "active" && elapsed !== null
                ? tFill("refresh.elapsed", { minutes: elapsed })
                : t(`refresh.step.${state}`);
          return (
            <li key={name} data-state={state}>
              <span className="refresh-ledger__dot" aria-hidden="true" />
              <span className="refresh-ledger__step-name">
                {t(STEP_LABEL[name])}
              </span>
              <span className="refresh-ledger__muted">{detail}</span>
            </li>
          );
        })}
      </ol>
      <progress
        className="refresh-ledger__bar"
        aria-label={tFill("refresh.progressLabel", {
          stage: step === null ? "" : t(STEP_LABEL[step]),
        })}
        {...(fraction === null ? {} : { value: fraction, max: 1 })}
      />
    </>
  );
};

/**
 * 홈 머리의 "DB 새로고침": 지금 상태 배지, 버튼, 확인·진행·결과 패널. 상태 판정·문장은 backend 가 정하고
 * 여기서는 그대로 보인다. 연습용 데이터로 켜졌으면 아무것도 그리지 않는다.
 */
export const LedgerRefreshControl = () => {
  const status = useQuery(ledgerRefreshQuery());
  const check = useCheckLedgerRefresh();
  const start = useStartLedgerRefresh();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const checked = useRef(false);
  const panelId = useId();
  const titleId = useId();
  const reasonId = useId();
  const data = status.data;
  const checkNow = check.mutate;

  // 홈을 열 때 마지막 확인이 오래됐으면 서버 판을 한 번 확인한다.
  useEffect(() => {
    if (data?.needs_check && data.can_start && !checked.current) {
      checked.current = true;
      checkNow();
    }
  }, [data?.needs_check, data?.can_start, checkNow]);

  if (data === undefined) return null;
  if (data.code === "unavailable" && data.reason === "practice_data")
    return null;

  const blocked = !data.can_start && !isRefreshing(data);
  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") close();
  };
  const remote = data.remote;
  const finished =
    data.code === "succeeded" ||
    data.code === "failed" ||
    data.code === "partial";

  return (
    <div className="refresh-ledger">
      <StatusBadge status={data} />
      <Button
        ref={button}
        aria-expanded={open}
        aria-controls={panelId}
        aria-describedby={blocked ? reasonId : undefined}
        title={t("refresh.hint")}
        disabled={blocked}
        onClick={() => {
          if (!open && data.can_start && !isRefreshing(data)) check.mutate();
          setOpen(!open);
        }}
      >
        {t("refresh.button")}
      </Button>
      {blocked ? (
        <span id={reasonId} className="refresh-ledger__reason">
          {data.message}
        </span>
      ) : null}
      {open && !blocked ? (
        <div
          id={panelId}
          className="refresh-ledger__panel"
          ref={panel}
          role="dialog"
          aria-labelledby={titleId}
          tabIndex={-1}
          onKeyDown={onKeyDown}
        >
          <strong id={titleId}>
            {isRefreshing(data)
              ? t("refresh.dialog.running")
              : finished
                ? t(`refresh.notice.${data.code as "succeeded" | "failed" | "partial"}`)
                : t("refresh.dialog.confirm")}
          </strong>
          {isRefreshing(data) ? (
            <Progress status={data} now={status.dataUpdatedAt} />
          ) : null}
          {!isRefreshing(data) && !finished && remote.state === "behind" ? (
            <p>
              {tFill("refresh.dialog.size", {
                tables: remote.new_tables,
                size: megabytes(remote.download_bytes),
              })}
            </p>
          ) : null}
          <p
            className={
              data.code === "failed" || data.code === "partial"
                ? "refresh-ledger__warning"
                : "refresh-ledger__muted"
            }
            role={isRefreshing(data) ? "status" : undefined}
          >
            {data.message}
          </p>
          {data.can_start ? (
            <>
              <p className="refresh-ledger__note">
                {t("refresh.dialog.reload")}
              </p>
              <details className="ui-disclosure">
                <summary>{t("refresh.dialog.details")}</summary>
                <ul className="refresh-ledger__details">
                  <li>{t("refresh.dialog.kept")}</li>
                  <li>{t("refresh.dialog.rerun")}</li>
                </ul>
              </details>
            </>
          ) : null}
          {start.isError ? (
            <p className="refresh-ledger__warning" role="alert">
              {t("refresh.startFailed")}
            </p>
          ) : null}
          <div className="refresh-ledger__actions">
            <Button tone="ghost" onClick={close}>
              {t("refresh.close")}
            </Button>
            {data.can_start ? (
              <Button
                tone="primary"
                disabled={start.isPending}
                onClick={() =>
                  // 누른 버튼이 진행 화면에서 사라진다 — 초점을 패널로 옮겨 Escape·Tab 이 이어지게 한다.
                  start.mutate(undefined, {
                    onSettled: () => panel.current?.focus(),
                  })
                }
              >
                {t("refresh.start")}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
};
