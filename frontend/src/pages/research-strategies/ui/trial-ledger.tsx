import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import {
  backtestErrorSentence,
  useMergeTrialLineage,
  type TrialLedger,
} from "../../../entities/backtest";
import { strategiesQuery } from "../../../entities/strategy";
import { ApiRequestError, failureReason } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import {
  Badge,
  Button,
  FailureNotice,
  type BadgeTone,
} from "../../../shared/ui";

type Role = TrialLedger["trials"][number]["runs"][number]["role"];

// 실행 역할은 backend 원장 집계(`summarize_trial_ledger`)가 정한다. 화면은 이름과 색만 붙인다.
const ROLE_TONE: Record<Role, BadgeTone> = {
  counted: "ok",
  recheck: "info",
  pending: "neutral",
  no_result: "warn",
};

const roleLabel = (role: Role): string => {
  const key: MessageKey = `history.trials.role.${role}`;
  return t(key);
};

const ShortKey = ({ value }: { value: string }) => (
  <code className="data-list-page__hash" title={value}>
    {value.slice(0, 12)}
  </code>
);

// 합칠 계열 후보는 저장 전략 목록 한 쪽(서버 상한)에서 고른다.
const CANDIDATE_LIMIT = 500;

const MergeDialog = ({
  strategyId,
  strategyLabel,
  onClose,
}: {
  strategyId: string;
  strategyLabel: string;
  onClose: () => void;
}) => {
  const titleId = useId();
  const selectId = useId();
  const select = useRef<HTMLSelectElement>(null);
  const [source, setSource] = useState("");
  const candidates = useQuery(
    strategiesQuery({ offset: 0, limit: CANDIDATE_LIMIT }),
  );
  const merge = useMergeTrialLineage();
  const others =
    candidates.data?.items.filter((item) => item.strategy_id !== strategyId) ??
    [];

  useEffect(() => {
    select.current?.focus();
  }, [candidates.isSuccess]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    onClose();
  };

  return (
    <div
      role="dialog"
      aria-labelledby={titleId}
      className="trial-ledger__dialog"
      onKeyDown={onKeyDown}
    >
      <h3 id={titleId}>{t("history.trials.merge.title")}</h3>
      <p>
        {t("history.trials.merge.description").replace(
          "{target}",
          strategyLabel,
        )}
      </p>
      <p className="trial-ledger__warning">
        {t("history.trials.merge.warning")}
      </p>
      {candidates.isPending ? (
        <p role="status">{t("page.loading")}</p>
      ) : candidates.isError ? (
        <FailureNotice
          message={t("history.strategies.error")}
          reason={failureReason(candidates.error)}
        />
      ) : others.length === 0 ? (
        <p>{t("history.trials.merge.noCandidates")}</p>
      ) : (
        <p className="data-list-page__filter">
          <label htmlFor={selectId}>{t("history.trials.merge.source")}</label>
          <select
            id={selectId}
            ref={select}
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            <option value="">{t("history.trials.merge.choose")}</option>
            {others.map((item) => (
              <option key={item.strategy_id} value={item.strategy_id}>
                {item.title
                  ? `${item.title} (${item.strategy_id})`
                  : item.strategy_id}
              </option>
            ))}
          </select>
        </p>
      )}
      {merge.isError ? (
        <FailureNotice
          message={
            backtestErrorSentence(
              merge.error instanceof ApiRequestError
                ? (merge.error.code ?? null)
                : null,
            ) ?? t("history.trials.merge.failed")
          }
          reason={failureReason(merge.error)}
        />
      ) : null}
      <span className="data-list-page__actions">
        <Button size="small" tone="ghost" onClick={onClose}>
          {t("history.trials.merge.cancel")}
        </Button>
        <Button
          size="small"
          tone="danger"
          disabled={source === "" || merge.isPending}
          onClick={() =>
            merge.mutate(
              { strategyId, sourceStrategyId: source },
              { onSuccess: onClose },
            )
          }
        >
          {t("history.trials.merge.confirm")}
        </Button>
      </span>
    </div>
  );
};

/**
 * 계열 시도 원장(검증 랩 spec D2, US-SM-12). 시도 묶음·실행 역할·N 은 backend 응답 그대로다. 지우기·나누기는
 * 없고 "다른 계열과 합치기"만 있다.
 */
export const TrialLedgerPanel = ({
  strategyId,
  strategyLabel,
  ledger,
}: {
  strategyId: string;
  strategyLabel: string;
  ledger: UseQueryResult<TrialLedger>;
}) => {
  const [merging, setMerging] = useState(false);
  if (ledger.isPending) {
    return <p role="status">{t("history.trials.loading")}</p>;
  }
  if (ledger.isError) {
    return (
      <div className="data-list-page__feedback" role="alert">
        <p>{t("history.trials.error")}</p>
        <Button size="small" tone="ghost" onClick={() => ledger.refetch()}>
          {t("page.error.retry")}
        </Button>
      </div>
    );
  }
  const { data } = ledger;
  return (
    <>
      <div className="data-list-page__feedback">
        <p>
          {t("history.trials.count").replace(
            "{count}",
            String(data.trial_count),
          )}
          {data.lineage_id === strategyId
            ? null
            : ` ${t("history.trials.mergedInto").replace("{lineage}", data.lineage_id)}`}
        </p>
        {merging ? null : (
          <Button
            size="small"
            tone="secondary"
            onClick={() => setMerging(true)}
          >
            {t("history.trials.merge")}
          </Button>
        )}
      </div>
      {merging ? (
        <MergeDialog
          strategyId={strategyId}
          strategyLabel={strategyLabel}
          onClose={() => setMerging(false)}
        />
      ) : null}
      {data.trials.length === 0 && data.blocked.length === 0 ? (
        <p>{t("history.trials.empty")}</p>
      ) : (
        <div className="data-list-page__scroll">
          <table className="data-list-page__table">
            <caption className="sr-only">
              {`${t("history.trials.caption")}: ${strategyLabel}`}
            </caption>
            <thead>
              <tr>
                <th scope="col">{t("history.trials.trial")}</th>
                <th scope="col">{t("history.trials.runs")}</th>
              </tr>
            </thead>
            <tbody>
              {data.trials.map((trial) => (
                <tr key={trial.trial_key}>
                  <td>
                    <ShortKey value={trial.trial_key} />
                  </td>
                  <td>
                    <ul className="trial-ledger__runs">
                      {trial.runs.map((run) => (
                        <li key={run.run_id}>
                          <Badge tone={ROLE_TONE[run.role]}>
                            {roleLabel(run.role)}
                          </Badge>{" "}
                          <Link
                            to="/research/backtests/$runId"
                            params={{ runId: run.run_id }}
                          >
                            <code>{run.run_id.slice(0, 12)}</code>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
              {data.blocked.map((attempt) => (
                <tr key={`${attempt.trial_key}-${attempt.blocked_at}`}>
                  <td>
                    <ShortKey value={attempt.trial_key} />
                  </td>
                  <td>
                    <Badge tone={ROLE_TONE.no_result}>
                      {roleLabel("no_result")}
                    </Badge>{" "}
                    {t("history.trials.blocked").replace(
                      "{start}",
                      attempt.start,
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
};
