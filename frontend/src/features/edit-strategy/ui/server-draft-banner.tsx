import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { ServerDraftSync } from "../model/use-server-draft";
import "./server-draft-banner.css";

const downloadHref = (source: string): string =>
  `data:text/plain;charset=utf-8,${encodeURIComponent(source)}`;

export const ServerDraftBanner = ({ sync }: { sync: ServerDraftSync }) => {
  if (sync.phase === "loading") {
    return (
      <p className="server-draft server-draft--quiet" role="status">
        {t("draft.server.loading")}
      </p>
    );
  }
  if (sync.phase === "saving" || sync.phase === "synced") {
    return (
      <p className="server-draft server-draft--quiet" role="status">
        {sync.phase === "saving"
          ? t("draft.server.saving")
          : t("draft.server.synced")}
      </p>
    );
  }
  if (sync.phase === "offline") {
    return (
      <section className="server-draft" role="status">
        <span>{t("draft.server.offline")}</span>
        <Button size="small" tone="ghost" onClick={sync.retry}>
          {t("draft.server.retry")}
        </Button>
      </section>
    );
  }
  if (sync.phase === "rejected") {
    return (
      <section className="server-draft server-draft--conflict" role="alert">
        <span>
          {t("draft.server.rejected")}
          {sync.errorMessage ? ` ${sync.errorMessage}` : ""}
        </span>
        <Button size="small" tone="ghost" onClick={sync.retry}>
          {t("draft.server.retry")}
        </Button>
      </section>
    );
  }
  const recovery = sync.phase === "recovery";
  const label = sync.incompatible
    ? t("draft.server.incompatibleTitle")
    : recovery
      ? t("draft.server.recoveryTitle")
      : t("draft.server.conflictTitle");
  return (
    <section
      className="server-draft server-draft--conflict"
      role="region"
      aria-label={label}
    >
      <div>
        <strong>{label}</strong>
        <p>
          {sync.incompatible
            ? t("draft.server.incompatible")
            : recovery
              ? t("draft.server.recovery")
              : t("draft.server.conflict")}
        </p>
        {sync.updatedAt ? (
          <time dateTime={sync.updatedAt}>{sync.updatedAt}</time>
        ) : null}
      </div>
      <div className="server-draft__actions">
        {!sync.incompatible && sync.remote ? (
          <Button size="small" tone="secondary" onClick={sync.applyRemote}>
            {t("draft.server.applyRemote")}
          </Button>
        ) : null}
        {!sync.incompatible ? (
          <Button size="small" tone="primary" onClick={sync.keepLocal}>
            {t("draft.server.keepLocal")}
          </Button>
        ) : null}
        {sync.incompatible && sync.remote ? (
          <a
            className="ui-button ui-button--secondary ui-button--small"
            href={downloadHref(sync.remote.source)}
            download={`strategy-server-draft-${sync.remote.draft_id.replace(/[^\w.-]+/g, "_")}.${sync.remote.format}`}
          >
            {t("recovery.download")}
          </a>
        ) : null}
        <Button size="small" tone="ghost" onClick={sync.retry}>
          {t("draft.server.retry")}
        </Button>
      </div>
    </section>
  );
};
