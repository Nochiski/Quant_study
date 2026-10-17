import { useEffect, useRef, type KeyboardEvent } from "react";

import { requestRejectionMessage } from "../../../entities/backtest";
import {
  useCreateRobustnessCheck,
  useRobustnessPreview,
} from "../../../entities/experiment";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Button, FailureNotice } from "../../../shared/ui";

/**
 * "튼튼한지 확인하기" 확인 창. 새로 세는 시도 수를 먼저 보이고(US-DM-11), 실험 설계(탐색 축·창)는 backend
 * 미리 계산을 그대로 보인다. 시작하면 실험이 대기열에 들어가고 창이 닫힌다.
 */
export const RobustnessConfirm = ({
  runId,
  onClose,
}: {
  runId: string;
  /** `started` 는 실험을 대기열에 넣고 닫았는가다. */
  onClose: (started: boolean) => void;
}) => {
  const preview = useRobustnessPreview(runId, true);
  const create = useCreateRobustnessCheck(runId);
  const close = useRef<HTMLButtonElement>(null);
  const start = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    close.current?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose(false);
      return;
    }
    if (event.key !== "Tab") return;
    // 포커스는 창 안 두 버튼 사이에서만 돈다. 시작 버튼이 없으면(계산 중·거절) 닫기 버튼에 머문다.
    const last = start.current ?? close.current;
    if (event.shiftKey && event.target === close.current) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && event.target === last) {
      event.preventDefault();
      close.current?.focus();
    }
  };

  const data = preview.data;
  const axes = data?.design.search.axes ?? [];
  return (
    <div className="robustness-confirm__backdrop" onKeyDown={onKeyDown}>
      <section
        className="robustness-confirm"
        role="dialog"
        aria-modal="true"
        aria-label={t("backtest.robustness.confirm.title")}
      >
        <h5>{t("backtest.robustness.confirm.title")}</h5>
        {preview.isPending ? (
          <p role="status">{t("backtest.robustness.confirm.loading")}</p>
        ) : null}
        {preview.isError ? (
          <FailureNotice
            message={requestRejectionMessage(
              preview.error,
              "backtest.robustness.loadFailed",
            )}
            reason={failureReason(preview.error)}
          />
        ) : null}
        {data ? (
          <>
            <p className="robustness-confirm__trials">
              {tFill("backtest.robustness.confirm.trials", {
                count: data.new_trial_count,
                before: data.trial_count,
                after: data.trial_count_after,
              })}
            </p>
            <p>{t("backtest.robustness.confirm.trialsMeaning")}</p>
            <p>
              {tFill("backtest.robustness.confirm.plan", {
                combinations: data.combination_count,
                runs: data.run_count,
              })}
            </p>
            <p>
              {axes.length === 0
                ? t("backtest.robustness.confirm.noAxes")
                : tFill("backtest.robustness.confirm.axes", {
                    axes: axes
                      .map(
                        (axis) =>
                          `${axis.parameter_id} ${axis.values.join(", ")}`,
                      )
                      .join(" · "),
                  })}
            </p>
            <table aria-label={t("backtest.robustness.confirm.windows")}>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">{t("backtest.robustness.confirm.train")}</th>
                  <th scope="col">{t("backtest.robustness.confirm.test")}</th>
                </tr>
              </thead>
              <tbody>
                {data.design.windows.map((window, index) => (
                  <tr key={window.test_start}>
                    <td>{index + 1}</td>
                    <td>
                      {window.train_start} ~ {window.train_end}
                    </td>
                    <td>
                      {window.test_start} ~ {window.test_end}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : null}
        {create.isError ? (
          <FailureNotice
            message={requestRejectionMessage(
              create.error,
              "backtest.robustness.loadFailed",
            )}
            reason={failureReason(create.error)}
          />
        ) : null}
        <footer className="robustness-confirm__actions">
          <Button ref={close} tone="secondary" onClick={() => onClose(false)}>
            {t("backtest.robustness.confirm.cancel")}
          </Button>
          {data ? (
            <Button
              ref={start}
              disabled={create.isPending}
              onClick={() =>
                create.mutate(undefined, { onSuccess: () => onClose(true) })
              }
            >
              {t(
                create.isPending
                  ? "backtest.robustness.confirm.starting"
                  : "backtest.robustness.confirm.start",
              )}
            </Button>
          ) : null}
        </footer>
      </section>
    </div>
  );
};
