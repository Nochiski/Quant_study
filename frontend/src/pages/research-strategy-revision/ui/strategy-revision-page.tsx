import { useSuspenseQuery } from "@tanstack/react-query";

import { strategyRevisionQuery } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { useNavigate, useParams, useSearch } from "../../../shared/lib/router";
import { Badge, Tabs } from "../../../shared/ui";
import { STRATEGY_VIEWS, type StrategyView } from "../model/strategy-views";

const VIEW_ITEMS = STRATEGY_VIEWS.map((id) => ({
  id,
  label: id.toUpperCase(),
  disabled: id !== "json",
}));

/**
 * Saved revision entry. Data comes from the query cache the route loader warmed up (ADR D4);
 * the selected view lives in the URL search and never blocks navigation (ADR D3). Until P3/P4
 * deliver the editor and projections only the JSON projection is available.
 */
export const StrategyRevisionPage = () => {
  const { strategyId, revision } = useParams({
    from: "/research/strategies/$strategyId/revisions/$revision",
  });
  const search = useSearch({
    from: "/research/strategies/$strategyId/revisions/$revision",
  });
  const navigate = useNavigate();
  const { data } = useSuspenseQuery(
    strategyRevisionQuery(strategyId, Number(revision)),
  );
  const view: StrategyView = search.view ?? "json";

  return (
    <>
      <header className="page-header">
        <h1>{data.spec.title}</h1>
        <Badge tone="accent">
          {t("page.revision.label")} {data.spec.identity.revision}
        </Badge>
        <code title={data.spec_hash}>{data.spec_hash.slice(0, 12)}…</code>
      </header>
      <Tabs
        label={t("ui.tabs.view")}
        items={VIEW_ITEMS}
        value={view}
        onChange={(next) =>
          navigate({
            to: "/research/strategies/$strategyId/revisions/$revision",
            params: { strategyId, revision },
            search: { ...search, view: next === "json" ? undefined : next },
            replace: true,
          })
        }
      />
      <pre
        className="page-state"
        aria-label={t("page.revision.jsonProjection")}
      >
        {JSON.stringify(data.spec, null, 2)}
      </pre>
    </>
  );
};
