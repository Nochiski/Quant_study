import { useId, useState } from "react";

import { t } from "../../../shared/config";
import { Badge, Button, FailureNotice } from "../../../shared/ui";
import type {
  StrategyDebuggerContext,
  StrategyDebuggerUnavailableReason,
} from "../model/strategy-trace";
import { useStrategyTrace } from "../model/use-strategy-trace";
import "./strategy-preview.css";

/** 응답은 trace query cache만 소유한다. 이 패널은 마지막 요청 신원과 날짜 입력만 보관한다. */
export const StrategyPreview = ({
  context,
  unavailableReason,
}: {
  context: StrategyDebuggerContext | null;
  unavailableReason: StrategyDebuggerUnavailableReason | null;
}) => {
  const titleId = useId();
  const [asOf, setAsOf] = useState("");
  const [requestedOwner, setRequestedOwner] = useState<string | null>(null);
  const { prepared, state, run } = useStrategyTrace(context, {
    summaryOnly: true,
    asOf,
    security: "",
    factorId: "",
    nodeId: "",
  });
  const owner = prepared.kind === "ready" ? prepared.ownerKey : null;
  const stale = requestedOwner !== null && requestedOwner !== owner;
  const response =
    state.kind === "success" && requestedOwner === owner
      ? state.response
      : null;
  const summary = response?.summary;
  return (
    <section className="strategy-preview" aria-labelledby={titleId}>
      <header className="strategy-preview__controls">
        <h3 id={titleId}>{t("strategy.preview.title")}</h3>
        {stale ? (
          <Badge tone="warn">{t("strategy.preview.stale")}</Badge>
        ) : null}
        <label>
          {t("strategy.preview.date")}
          <input
            type="date"
            value={asOf}
            min={context?.environment.start}
            max={context?.environment.end}
            onChange={(event) => setAsOf(event.target.value)}
          />
        </label>
        <Button
          disabled={prepared.kind !== "ready" || state.kind === "loading"}
          onClick={() => {
            setRequestedOwner(owner);
            void run();
          }}
        >
          {t("strategy.preview.refresh")}
        </Button>
      </header>
      <p>{t("strategy.preview.description")}</p>
      {unavailableReason !== null ? (
        <p>{t(`debugger.unavailable.${unavailableReason}`)}</p>
      ) : null}
      {state.kind === "blocked" && state.reason === "date" ? (
        <p>{t("debugger.blocked.date")}</p>
      ) : null}
      {state.kind === "loading" ? (
        <p role="status">{t("strategy.preview.loading")}</p>
      ) : null}
      {state.kind === "error" ? (
        <FailureNotice message={state.message} reason={state.reason} />
      ) : null}
      {state.kind === "discarded" ? (
        <p role="alert">{t("strategy.preview.discarded")}</p>
      ) : null}
      {response === null ? null : (
        <>
          <p>
            {t("strategy.preview.resolvedDate")}: <time>{response.as_of}</time>
          </p>
          {summary == null ? (
            <p>{t("strategy.preview.noFrame")}</p>
          ) : (
            <>
              <dl className="strategy-preview__counts">
                {(
                  [
                    "universe",
                    "eligible",
                    "eligibility_failed",
                    "eligibility_rank_cut",
                    "missing",
                  ] as const
                ).map((key) => (
                  <div key={key}>
                    <dt>{t(`strategy.preview.count.${key}`)}</dt>
                    <dd>{summary.counts[key]}</dd>
                  </div>
                ))}
              </dl>
              <div className="strategy-preview__table">
                <table>
                  <caption>{t("strategy.preview.targets")}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("strategy.preview.rank")}</th>
                      <th scope="col">{t("strategy.preview.name")}</th>
                      <th scope="col">{t("strategy.preview.score")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.targets.map(({ position, security }) => (
                      <tr key={position.security_id}>
                        <td>{position.rank}</td>
                        <td>
                          {security?.name ?? t("strategy.preview.unknownName")}
                        </td>
                        <td>{position.composite_score}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {summary.targets.length === 0 ? (
                  <p>{t("strategy.preview.empty")}</p>
                ) : null}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
};
