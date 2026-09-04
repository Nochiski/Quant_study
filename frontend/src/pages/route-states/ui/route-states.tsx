import { useEffect } from "react";

import { t } from "../../../shared/config";
import { Link, useRouter } from "../../../shared/lib/router";
import { Button, EmptyState } from "../../../shared/ui";

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

/** Localised failure state inside the shell; the raw error goes to the console only. */
export const RouteErrorPage = ({ error }: { error: unknown }) => {
  const router = useRouter();
  useEffect(() => {
    console.error("route error", error);
  }, [error]);
  return (
    <div className="page-state page-state--error" role="alert">
      <p>
        <strong>{t("page.error.title")}</strong>
      </p>
      <p>{t("page.error.description")}</p>
      <p className="page-state__actions">
        <Button tone="primary" onClick={() => void router.invalidate()}>
          {t("page.error.retry")}
        </Button>
        <Link
          to="/research/strategies/new"
          className="ui-button ui-button--secondary"
        >
          {t("page.notFound.action")}
        </Link>
      </p>
    </div>
  );
};

export const RoutePendingPage = () => (
  <p className="page-state" role="status">
    {t("page.loading")}
  </p>
);
