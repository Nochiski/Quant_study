import { useSuspenseQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { strategyDocumentQuery } from "../../../entities/strategy";
import {
  DirtyLeaveGuard,
  DocumentToolbar,
  SourceEditor,
  saveStatusText,
  useCompileDocument,
  useRunBacktest,
  useSaveDocument,
  useSchemaAssist,
  useStrategyDocument,
  type DocumentSource,
} from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { useNavigate, useParams, useSearch } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";
import { PROJECTION_VIEWS, type StrategyView } from "../model/strategy-views";

const ROUTE = "/research/strategies/$strategyId/revisions/$revision";

/** ISO timestamp → "YYYY-MM-DD HH:MM" without locale surprises. */
const shortTimestamp = (iso: string): string =>
  iso.length >= 16 ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso;

/**
 * Saved revision entry (WORKFLOW P2-04). The exact stored document comes from the query cache
 * the route loader warmed up (ADR D4) and becomes the draft base. Saving appends the next
 * revision and the URL follows it. The selected view lives in the URL search and never blocks
 * navigation (ADR D3): the stored format is edited in place, JSON is a read-only projection of
 * the base spec, and a view that is not implemented yet falls back to the stored format with a
 * notice instead of an empty tab.
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
  const { save, status, canSave } = useSaveDocument(document, dispatch);
  const assist = useSchemaAssist(document);
  const { validateNow, validating } = useCompileDocument(document, dispatch);
  const backtest = useRunBacktest(document);
  const current =
    document.compiled !== null &&
    document.compiledVersion === document.sourceVersion
      ? document.compiled
      : null;

  useEffect(() => {
    if (
      document.strategyId !== strategyId ||
      document.baseRevision === null ||
      document.baseRevision === Number(revision)
    ) {
      return;
    }
    void navigate({
      to: ROUTE,
      params: { strategyId, revision: String(document.baseRevision) },
      search: { ...search },
      replace: true,
    });
  }, [
    document.strategyId,
    document.baseRevision,
    strategyId,
    revision,
    search,
    navigate,
  ]);

  const availableViews: readonly StrategyView[] =
    stored.format === "yaml" ? PROJECTION_VIEWS : ["json"];
  const requested: StrategyView = search.view ?? stored.format;
  const implemented = availableViews.includes(requested);
  const view: StrategyView = implemented ? requested : stored.format;
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
        onRunBacktest={() => void backtest.run()}
        runDisabled={!backtest.canRun}
        view={view}
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
            validating={validating}
            onSave={save}
            canSave={canSave}
            saving={status.kind === "saving"}
            onRun={() => void backtest.run()}
            decision={backtest.decision}
            runStatus={backtest.status}
          />
        }
        editor={
          <>
            {implemented ? null : (
              <p className="page-state" role="status">
                {t("page.revision.viewPending")} ({requested.toUpperCase()})
              </p>
            )}
            {view === stored.format ? (
              <SourceEditor
                state={document}
                dispatch={dispatch}
                assist={assist}
              />
            ) : (
              <pre
                className="page-state"
                aria-label={t("page.revision.jsonProjection")}
              >
                {JSON.stringify(stored.spec, null, 2)}
              </pre>
            )}
          </>
        }
      />
      <DirtyLeaveGuard dirty={document.dirty} />
    </>
  );
};
