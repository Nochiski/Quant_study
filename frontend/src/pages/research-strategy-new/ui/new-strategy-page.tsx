import { t } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Badge, EmptyState } from "../../../shared/ui";

/**
 * New-strategy entry of the App Shell. The source editor and IDE layout land in P2-03/P3; this
 * page only establishes the route, the draft-base wording and the way back to the legacy editor.
 */
export const NewStrategyPage = () => (
  <>
    <header className="page-header">
      <h1>{t("page.newStrategy.title")}</h1>
      <Badge tone="info">{t("page.newStrategy.draft")}</Badge>
    </header>
    <EmptyState
      title={t("page.newStrategy.placeholderTitle")}
      description={t("page.newStrategy.placeholder")}
      action={
        <Link
          to="/legacy/builder"
          search={{}}
          className="ui-button ui-button--secondary"
        >
          {t("nav.legacyBuilder")}
        </Link>
      }
    />
  </>
);
