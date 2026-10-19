import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import {
  RUN_STATUS,
  RunHeadline,
  runStageLabel,
  runStatusLabel,
  backtestHistoryQuery,
  trialLedgerQuery,
} from "../../../entities/backtest";
import {
  experimentStatusLabel,
  experimentStatusTone,
  experimentsQuery,
  TrialProgress,
} from "../../../entities/experiment";
import {
  strategyDocumentQuery,
  strategyRevisionsQuery,
} from "../../../entities/strategy";
import { t, tFill } from "../../../shared/config";
import { displayTime } from "../../../shared/lib/display-time";
import { Link, useParams } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";
import {
  ledgerRoleCounts,
  RECENT_VERSIONS,
  sessionVersions,
  type SessionEntry,
  type SessionVersion,
} from "../model/strategy-session";
import { ROLE_TONE, roleLabel } from "../model/trial-roles";
import { TrialLedgerPanel } from "./trial-ledger";
import "./strategy-session.css";
import "./trial-ledger.css";

const ROUTE = "/research/strategies/$strategyId";
// 저장소 목록 상한(500) 안에서 한 번에 읽는다. 버전·실행이 더 많으면 최근 것만 묶인다.
const REVISION_LIMIT = 500;
const BACKTEST_LIMIT = 200;

const EntryRow = ({ entry }: { entry: SessionEntry }) => {
  if (entry.kind === "backtest") {
    const run = entry.summary.run;
    return (
      <li className="strategy-session__entry">
        <span className="strategy-session__entry-head">
          <Link to="/research/backtests/$runId" params={{ runId: run.run_id }}>
            {t("history.session.backtest")}
          </Link>
          <Badge tone={RUN_STATUS[run.status].tone}>{runStatusLabel(run.status)}</Badge>
          {run.status === "failed" ? (
            <small>{tFill("history.session.failedAt", { stage: runStageLabel(run.stage) })}</small>
          ) : null}
          <small className="strategy-session__time">{displayTime(run.created_at)}</small>
        </span>
        {run.status === "completed" ? <RunHeadline headline={entry.summary.headline} /> : null}
      </li>
    );
  }
  const experiment = entry.experiment;
  const id = experiment.record.experiment_id;
  return (
    <li className="strategy-session__entry">
      <span className="strategy-session__entry-head">
        <Link to="/research/experiments/$experimentId" params={{ experimentId: id }}>
          {t("history.session.experiment")}
        </Link>
        <Badge tone={experimentStatusTone(experiment.status)}>
          {experimentStatusLabel(experiment.status)}
        </Badge>
        {experiment.status === "completed" && experiment.selections.length === 0 ? (
          <Badge tone="info">{t("experiments.awaitingCandidate")}</Badge>
        ) : null}
        <small className="strategy-session__time">{displayTime(entry.at)}</small>
      </span>
      {experiment.finished ? null : <TrialProgress counts={experiment.trial_counts} />}
    </li>
  );
};

const VersionCard = ({
  strategyId,
  version,
}: {
  strategyId: string;
  version: SessionVersion;
}) => {
  const titleId = useId();
  const revision = version.revision.revision;
  return (
    <article className="strategy-session__version" aria-labelledby={titleId}>
      <header className="strategy-session__version-head">
        <h3 id={titleId}>
          v{revision}
          {version.revision.requires_upgrade ? (
            <>
              {" "}
              <Badge tone="warn">{t("history.frozen")}</Badge>
            </>
          ) : null}
        </h3>
        <small className="strategy-session__time">{displayTime(version.revision.created_at)}</small>
        <span className="data-list-page__actions">
          <Link
            className="ui-button ui-button--ghost ui-button--small"
            to="/research/strategies/$strategyId/revisions/$revision"
            params={{ strategyId, revision: String(revision) }}
            search={{}}
          >
            {tFill("history.session.openVersion", { revision })}
          </Link>
          {revision > 1 ? (
            // 비교 화면은 이 버전과 바로 앞 버전을 기본으로 고른다(`strategy-diff-panel`).
            <Link
              className="ui-button ui-button--ghost ui-button--small"
              to="/research/strategies/$strategyId/revisions/$revision"
              params={{ strategyId, revision: String(revision) }}
              search={{ view: "graph", compare: true }}
            >
              {tFill("history.session.compare", { base: revision - 1, target: revision })}
            </Link>
          ) : null}
        </span>
      </header>
      <p className="strategy-session__note">
        {version.revision.change_note || t("history.session.noNote")}
      </p>
      {version.entries.length === 0 ? (
        <p className="strategy-session__empty">{t("history.session.noRuns")}</p>
      ) : (
        <ul className="strategy-session__entries">
          {version.entries.map((entry) => (
            <EntryRow
              key={
                entry.kind === "backtest"
                  ? entry.summary.run.run_id
                  : entry.experiment.record.experiment_id
              }
              entry={entry}
            />
          ))}
        </ul>
      )}
    </article>
  );
};

/** 문서 속성의 설명을 전략의 의도로 다시 읽는다. 따로 저장한 가설 칸은 없다(RD-03-B). */
const Intent = ({
  strategyId,
  revision,
  description,
}: {
  strategyId: string;
  revision: number;
  description: string | undefined;
}) =>
  description !== undefined && description.trim() !== "" ? (
    <div className="strategy-session__intent">
      <p>{description}</p>
      <small>{tFill("history.session.intentSource", { revision })}</small>
    </div>
  ) : (
    <div className="strategy-session__intent strategy-session__intent--empty">
      <p>{t("history.session.intentEmpty")}</p>
      <Link
        className="ui-button ui-button--secondary ui-button--small"
        to="/research/strategies/$strategyId/revisions/$revision"
        params={{ strategyId, revision: String(revision) }}
        search={{ view: "graph" }}
      >
        {t("history.session.addIntent")}
      </Link>
    </div>
  );

export const StrategySessionPage = () => {
  const { strategyId } = useParams({ from: ROUTE });
  const [olderOpen, setOlderOpen] = useState(false);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const olderId = useId();
  const revisions = useQuery(strategyRevisionsQuery(strategyId, { limit: REVISION_LIMIT }));
  const latest = revisions.data?.items.reduce(
    (best, item) => (best === undefined || item.revision > best.revision ? item : best),
    undefined as (typeof revisions.data.items)[number] | undefined,
  );
  const document = useQuery({
    ...strategyDocumentQuery(strategyId, latest?.revision ?? 1),
    enabled: latest !== undefined,
  });
  const backtests = useQuery(
    backtestHistoryQuery({ strategyId, kind: "single", limit: BACKTEST_LIMIT }),
  );
  const experiments = useInfiniteQuery(experimentsQuery());
  const ledger = useQuery(trialLedgerQuery(strategyId));

  if (revisions.isPending)
    return (
      <section className="page">
        <p className="page-state" role="status">
          {t("page.loading")}
        </p>
      </section>
    );
  if (revisions.isError || latest === undefined)
    return (
      <section className="page">
        <div className="page-state page-state--error" role="alert">
          <p>{t("history.session.error")}</p>
          <Button tone="ghost" onClick={() => revisions.refetch()}>
            {t("page.error.retry")}
          </Button>
        </div>
      </section>
    );

  const versions = sessionVersions(
    strategyId,
    revisions.data.items,
    backtests.data?.items ?? [],
    experiments.data?.pages.flatMap((page) => page.items) ?? [],
  );
  const recent = versions.slice(0, RECENT_VERSIONS);
  const older = versions.slice(RECENT_VERSIONS);
  const title = document.data?.spec.title || t("page.revision.untitled");
  const roles = ledger.data === undefined ? null : ledgerRoleCounts(ledger.data);

  return (
    <section className="page strategy-session" aria-labelledby="strategy-session-title">
      <header className="strategy-session__header">
        <Link to="/research/strategies" search={{}}>
          {t("history.session.back")}
        </Link>
        <h1 id="strategy-session-title">{title}</h1>
        <code className="data-list-page__hash">{strategyId}</code>
        {document.isSuccess ? (
          <Intent
            strategyId={strategyId}
            revision={latest.revision}
            description={document.data.spec.description}
          />
        ) : null}
        <span className="data-list-page__actions">
          <Link
            className="ui-button ui-button--primary"
            to="/research/strategies/$strategyId/revisions/$revision"
            params={{ strategyId, revision: String(latest.revision) }}
            search={{}}
          >
            {latest.requires_upgrade ? t("history.session.upgrade") : t("history.openLatest")}
          </Link>
        </span>
      </header>
      <div className="strategy-session__body">
        <div className="strategy-session__timeline">
          <h2>{t("history.session.timeline")}</h2>
          {backtests.isError || experiments.isError ? (
            <p role="alert">{t("history.session.runsError")}</p>
          ) : null}
          {recent.map((version) => (
            <VersionCard
              key={version.revision.revision}
              strategyId={strategyId}
              version={version}
            />
          ))}
          {older.length > 0 ? (
            <>
              <Button
                tone="ghost"
                size="small"
                aria-expanded={olderOpen}
                aria-controls={olderId}
                onClick={() => setOlderOpen((open) => !open)}
              >
                {olderOpen
                  ? t("history.session.olderClose")
                  : tFill("history.session.olderOpen", { count: older.length })}
              </Button>
              <div id={olderId} hidden={!olderOpen}>
                {olderOpen
                  ? older.map((version) => (
                      <VersionCard
                        key={version.revision.revision}
                        strategyId={strategyId}
                        version={version}
                      />
                    ))
                  : null}
              </div>
            </>
          ) : null}
          {ledgerOpen ? (
            <section
              className="strategy-session__ledger"
              aria-label={t("history.session.ledgerFull")}
            >
              <TrialLedgerPanel
                strategyId={strategyId}
                strategyLabel={title}
                ledger={ledger}
              />
            </section>
          ) : null}
        </div>
        <aside className="strategy-session__aside">
          <section className="strategy-session__card" aria-labelledby="session-ledger-title">
            <h2 id="session-ledger-title">{t("history.session.ledgerTitle")}</h2>
            {ledger.isPending ? (
              <p role="status">{t("history.trials.loading")}</p>
            ) : ledger.isError || roles === null ? (
              <p role="alert">{t("history.session.ledgerError")}</p>
            ) : (
              <>
                <p className="strategy-session__count">
                  {tFill("history.session.trialCount", { count: ledger.data.trial_count })}
                </p>
                <ul className="strategy-session__roles">
                  {(Object.keys(roles) as (keyof typeof roles)[]).map((role) => (
                    <li key={role}>
                      <Badge tone={ROLE_TONE[role]}>{roleLabel(role)}</Badge>
                      <span>{roles[role]}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <Button
              size="small"
              tone="ghost"
              aria-expanded={ledgerOpen}
              onClick={() => setLedgerOpen((open) => !open)}
            >
              {ledgerOpen ? t("history.session.ledgerClose") : t("history.session.ledgerOpen")}
            </Button>
          </section>
          <section className="strategy-session__card" aria-labelledby="session-fingerprint-title">
            <h2 id="session-fingerprint-title">{t("history.session.fingerprint")}</h2>
            <dl className="strategy-session__hashes">
              <div>
                <dt>{t("history.revisions.specHash")}</dt>
                <dd>
                  <code title={latest.spec_hash}>{latest.spec_hash.slice(0, 12)}</code>
                </dd>
              </div>
              <div>
                <dt>{t("history.session.sourceHash")}</dt>
                <dd>
                  {latest.source_hash == null ? (
                    t("history.revisions.sourceUnavailable")
                  ) : (
                    <code title={latest.source_hash}>{latest.source_hash.slice(0, 12)}</code>
                  )}
                </dd>
              </div>
            </dl>
            <p className="strategy-session__hint">{t("history.session.fingerprintHint")}</p>
          </section>
        </aside>
      </div>
    </section>
  );
};
