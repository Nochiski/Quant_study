import { useSuspenseQuery } from "@tanstack/react-query";
import { useId } from "react";

import { strategyRevisionQuery } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { useNavigate, useParams, useSearch } from "../../../shared/lib/router";
import { Badge, Tabs, panelId } from "../../../shared/ui";
import {
  IMPLEMENTED_VIEWS,
  STRATEGY_VIEWS,
  type StrategyView,
} from "../model/strategy-views";

const VIEW_ITEMS = STRATEGY_VIEWS.map((id) => ({
  id,
  label: id.toUpperCase(),
  disabled: !(IMPLEMENTED_VIEWS as readonly string[]).includes(id),
}));

/**
 * Saved revision entry. Data comes from the query cache the route loader warmed up (ADR D4);
 * the selected view lives in the URL search and never blocks navigation (ADR D3). A URL naming
 * a view that is not implemented yet shows the JSON projection with a notice instead of an
 * empty, unreachable tab.
 */
export const StrategyRevisionPage = () => {
  const { strategyId, revision } = useParams({
    from: "/research/strategies/$strategyId/revisions/$revision",
  });
  const search = useSearch({
    from: "/research/strategies/$strategyId/revisions/$revision",
  });
  const navigate = useNavigate();
  const idBase = useId();
  const { data } = useSuspenseQuery(
    strategyRevisionQuery(strategyId, Number(revision)),
  );
  const requested: StrategyView = search.view ?? "json";
  const implemented = (IMPLEMENTED_VIEWS as readonly string[]).includes(
    requested,
  );
  const view: StrategyView = implemented ? requested : "json";

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
        idBase={idBase}
        onChange={(next) =>
          navigate({
            to: "/research/strategies/$strategyId/revisions/$revision",
            params: { strategyId, revision },
            search: { ...search, view: next === "json" ? undefined : next },
            replace: true,
          })
        }
      />
      {implemented ? null : (
        <p className="page-state" role="status">
          {t("page.revision.viewPending")} ({requested.toUpperCase()})
        </p>
      )}
      <pre
        id={panelId(idBase, view)}
        role="tabpanel"
        className="page-state"
        aria-label={t("page.revision.jsonProjection")}
      >
        {JSON.stringify(data.spec, null, 2)}
      </pre>
    </>
  );
};
