import { t } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { EmptyState } from "../../../shared/ui";

export const NotFoundPage = () => (
  <EmptyState
    title={t("page.notFound.title")}
    description={t("page.notFound.description")}
    action={
      <Link
        to="/research/strategies/new"
        className="ui-button ui-button--primary"
      >
        {t("page.notFound.action")}
      </Link>
    }
  />
);

export const RouteErrorPage = ({ error }: { error: unknown }) => (
  <div className="page-state page-state--error" role="alert">
    <p>
      <strong>{t("page.error.title")}</strong>
    </p>
    <p>{error instanceof Error ? error.message : String(error)}</p>
  </div>
);

export const RoutePendingPage = () => (
  <p className="page-state" role="status">
    {t("page.loading")}
  </p>
);
