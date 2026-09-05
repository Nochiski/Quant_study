import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import {
  strategyDiffQuery,
  strategyDocumentQuery,
  strategyRevisionsQuery,
} from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { lineDiff, type TextDiff } from "../../../shared/lib/text-diff";
import { Badge } from "../../../shared/ui";
import {
  projectDraftDiff,
  type DraftSemanticDiff,
} from "../model/diff-projection";
import type { DocumentState } from "../model/document-state";
import { SemanticDiffTable } from "./semantic-diff-table";
import "./strategy-diff-panel.css";

type StrategyDiffPanelProps = {
  state: DocumentState;
  active: boolean;
  revision?: { strategyId: string; currentRevision: number };
};

const shortHash = (value: string): string => `${value.slice(0, 12)}…`;

const SourceDiff = ({ diff }: { diff: TextDiff }) => (
  <div className="strategy-diff__source">
    <p className="strategy-diff__counts">
      <Badge tone={diff.added + diff.removed === 0 ? "ok" : "warn"}>
        {t("diff.source.counts")
          .replace("{added}", String(diff.added))
          .replace("{removed}", String(diff.removed))}
      </Badge>
    </p>
    {diff.rows.length === 0 ? (
      <p className="strategy-diff__empty">
        {diff.truncated ? t("diff.source.truncated") : t("diff.source.empty")}
      </p>
    ) : (
      <div className="strategy-diff__source-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">{t("diff.source.beforeLine")}</th>
              <th scope="col">{t("diff.source.afterLine")}</th>
              <th scope="col">{t("diff.source.change")}</th>
              <th scope="col">{t("diff.source.text")}</th>
            </tr>
          </thead>
          <tbody>
            {diff.rows.map((row, index) => (
              <tr
                key={`${row.kind}:${row.beforeLine ?? ""}:${row.afterLine ?? ""}:${index}`}
                data-kind={row.kind}
              >
                <td>{row.beforeLine ?? "—"}</td>
                <td>{row.afterLine ?? "—"}</td>
                <td aria-label={t(`diff.kind.${row.kind}`)}>
                  {row.kind === "added" ? "+" : "−"}
                </td>
                <td>
                  <code>{row.text || " "}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
    {diff.truncated && diff.rows.length > 0 ? (
      <p className="strategy-diff__notice">{t("diff.source.truncated")}</p>
    ) : null}
  </div>
);

const SemanticState = ({ semantic }: { semantic: DraftSemanticDiff }) => {
  if (semantic.status !== "ready") {
    return (
      <p className="strategy-diff__notice" role="status">
        {t(`diff.semantic.${semantic.status}`)}
      </p>
    );
  }
  return (
    <>
      <dl className="strategy-diff__hashes">
        <div>
          <dt>{t("diff.semantic.baseHash")}</dt>
          <dd>
            <code title={semantic.baseSpecHash}>
              {shortHash(semantic.baseSpecHash)}
            </code>
          </dd>
        </div>
        <div>
          <dt>{t("diff.semantic.currentHash")}</dt>
          <dd>
            <code title={semantic.currentSpecHash}>
              {shortHash(semantic.currentSpecHash)}
            </code>
          </dd>
        </div>
      </dl>
      <SemanticDiffTable entries={semantic.changes} />
    </>
  );
};

type RevisionSelection = {
  strategyId: string;
  base: number;
  target: number;
};

const RevisionComparison = ({
  strategyId,
  currentRevision,
}: {
  strategyId: string;
  currentRevision: number;
}) => {
  const baseId = useId();
  const targetId = useId();
  const [selection, setSelection] = useState<RevisionSelection>({
    strategyId: "",
    base: 0,
    target: 0,
  });
  const revisions = useQuery(strategyRevisionsQuery(strategyId));
  const choices = useMemo(
    () =>
      [...(revisions.data?.items ?? [])].sort(
        (left, right) => left.revision - right.revision,
      ),
    [revisions.data?.items],
  );
  const available = new Set(choices.map((item) => item.revision));
  const defaultTarget = available.has(currentRevision)
    ? currentRevision
    : (choices.at(-1)?.revision ?? 0);
  const defaultBase =
    [...choices].reverse().find((item) => item.revision < defaultTarget)
      ?.revision ?? defaultTarget;
  const active =
    selection.strategyId === strategyId &&
    available.has(selection.base) &&
    available.has(selection.target)
      ? selection
      : { strategyId, base: defaultBase, target: defaultTarget };
  const comparable = active.base > 0 && active.target > 0;
  const semantic = useQuery({
    ...strategyDiffQuery(
      strategyId,
      comparable ? active.base : 1,
      comparable ? active.target : 1,
    ),
    enabled: comparable && active.base !== active.target,
  });
  const baseDocument = useQuery({
    ...strategyDocumentQuery(strategyId, comparable ? active.base : 1),
    enabled: comparable,
  });
  const targetDocument = useQuery({
    ...strategyDocumentQuery(strategyId, comparable ? active.target : 1),
    enabled: comparable,
  });
  const source =
    baseDocument.data && targetDocument.data
      ? lineDiff(baseDocument.data.source, targetDocument.data.source)
      : null;

  if (revisions.isPending) {
    return (
      <p className="strategy-diff__notice">{t("diff.revision.loading")}</p>
    );
  }
  if (revisions.isError) {
    return (
      <p className="strategy-diff__notice" role="alert">
        {t("diff.revision.error")}
      </p>
    );
  }
  if (!comparable) {
    return <p className="strategy-diff__notice">{t("diff.revision.empty")}</p>;
  }

  const loadingDocuments = baseDocument.isPending || targetDocument.isPending;
  const documentError = baseDocument.isError || targetDocument.isError;
  const semanticChanges =
    active.base === active.target ? [] : semantic.data?.changes;

  return (
    <div className="strategy-diff__revision">
      <div className="strategy-diff__selectors">
        <label htmlFor={baseId}>{t("diff.revision.base")}</label>
        <select
          id={baseId}
          value={active.base}
          onChange={(event) =>
            setSelection({
              ...active,
              base: Number(event.target.value),
            })
          }
        >
          {choices.map((item) => (
            <option key={item.revision} value={item.revision}>
              v{item.revision}
            </option>
          ))}
        </select>
        <label htmlFor={targetId}>{t("diff.revision.target")}</label>
        <select
          id={targetId}
          value={active.target}
          onChange={(event) =>
            setSelection({
              ...active,
              target: Number(event.target.value),
            })
          }
        >
          {choices.map((item) => (
            <option key={item.revision} value={item.revision}>
              v{item.revision}
            </option>
          ))}
        </select>
      </div>

      <section aria-labelledby={`${baseId}-source`}>
        <h4 id={`${baseId}-source`}>{t("diff.revision.sourceTitle")}</h4>
        {loadingDocuments ? (
          <p className="strategy-diff__notice">{t("diff.revision.loading")}</p>
        ) : documentError || source === null ? (
          <p className="strategy-diff__notice" role="alert">
            {t("diff.revision.sourceError")}
          </p>
        ) : (
          <SourceDiff diff={source} />
        )}
      </section>

      <section aria-labelledby={`${baseId}-semantic`}>
        <h4 id={`${baseId}-semantic`}>{t("diff.revision.semanticTitle")}</h4>
        {active.base !== active.target && semantic.isPending ? (
          <p className="strategy-diff__notice">{t("diff.revision.loading")}</p>
        ) : semantic.isError ? (
          <p className="strategy-diff__notice" role="alert">
            {t("diff.revision.semanticError")}
          </p>
        ) : semanticChanges ? (
          <>
            {semantic.data ? (
              <dl className="strategy-diff__hashes">
                <div>
                  <dt>{t("diff.semantic.baseHash")}</dt>
                  <dd>
                    <code title={semantic.data.base_spec_hash}>
                      {shortHash(semantic.data.base_spec_hash)}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>{t("diff.semantic.currentHash")}</dt>
                  <dd>
                    <code title={semantic.data.target_spec_hash}>
                      {shortHash(semantic.data.target_spec_hash)}
                    </code>
                  </dd>
                </div>
              </dl>
            ) : null}
            <SemanticDiffTable entries={semanticChanges} />
          </>
        ) : null}
      </section>
    </div>
  );
};

export const StrategyDiffPanel = ({
  state,
  active,
  revision,
}: StrategyDiffPanelProps) => {
  if (!active) return null;
  const draft = projectDraftDiff(state);
  return (
    <section className="strategy-diff" aria-label={t("diff.title")}>
      <header>
        <div>
          <strong>{t("diff.title")}</strong>
          <span>{t("diff.readOnly")}</span>
        </div>
        <Badge tone={state.dirty ? "warn" : "ok"}>
          {state.dirty ? t("diff.dirty") : t("diff.clean")}
        </Badge>
      </header>

      <section aria-labelledby="draft-source-diff-title">
        <h3 id="draft-source-diff-title">{t("diff.draft.sourceTitle")}</h3>
        <p className="strategy-diff__caption">
          {t(`diff.sourceBase.${draft.sourceBase}`)}
        </p>
        <SourceDiff diff={draft.source} />
      </section>

      <section aria-labelledby="draft-semantic-diff-title">
        <h3 id="draft-semantic-diff-title">{t("diff.draft.semanticTitle")}</h3>
        <p className="strategy-diff__caption">
          {t("diff.semantic.backendOwned")}
        </p>
        <SemanticState semantic={draft.semantic} />
      </section>

      {revision ? (
        <section aria-labelledby="revision-diff-title">
          <h3 id="revision-diff-title">{t("diff.revision.title")}</h3>
          <p className="strategy-diff__caption">{t("diff.revision.caption")}</p>
          <RevisionComparison
            strategyId={revision.strategyId}
            currentRevision={revision.currentRevision}
          />
        </section>
      ) : null}
    </section>
  );
};
