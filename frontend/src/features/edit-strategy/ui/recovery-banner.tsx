import { useMemo } from "react";

import { t } from "../../../shared/config";
import { lineDiffSummary } from "../../../shared/lib/text-diff";
import { Badge, Button } from "../../../shared/ui";
import type { Recovery } from "../model/use-autosave";
import "./recovery-banner.css";

type RecoveryBannerProps = { recovery: Recovery };

const shortTime = (iso: string): string =>
  iso.length >= 16 ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso;

/** `data:` URL for the raw recovered text; works in the sandbox without object URLs. */
const downloadHref = (source: string): string =>
  `data:text/plain;charset=utf-8,${encodeURIComponent(source)}`;

/**
 * Offers a locally recovered draft (WORKFLOW P3-06): how it differs from the server original,
 * restore or discard it, or — when it was written for another schema version — download the
 * raw text instead of loading it into an editor that would misread it.
 */
export const RecoveryBanner = ({ recovery }: RecoveryBannerProps) => {
  const { record, original, compatibility, restore, discard } = recovery;
  const restorable = compatibility === "compatible";
  const diff = useMemo(
    () => lineDiffSummary(original, record.source),
    [original, record.source],
  );
  return (
    <section className="recovery" aria-label={t("recovery.title")}>
      <div className="recovery__summary">
        <Badge tone={restorable ? "info" : "warn"}>
          {t("recovery.title")}
        </Badge>
        <span>
          {t("recovery.savedAt")}: {shortTime(record.savedAt)}
        </span>
        <span>
          {t("recovery.diff")
            .replace("{added}", String(diff.added))
            .replace("{removed}", String(diff.removed))}
        </span>
        {restorable ? null : (
          <span className="recovery__mismatch">
            {t(
              compatibility === "unverified"
                ? "recovery.unverified"
                : "recovery.incompatible",
            )}
          </span>
        )}
      </div>
      {diff.preview.length > 0 ? (
        <pre className="recovery__preview" tabIndex={0}>
          {diff.preview.join("\n")}
        </pre>
      ) : null}
      <div className="recovery__actions">
        {restorable ? (
          <Button size="small" tone="primary" onClick={restore}>
            {t("recovery.restore")}
          </Button>
        ) : (
          <a
            className="ui-button ui-button--primary ui-button--small"
            href={downloadHref(record.source)}
            download={`strategy-draft-${record.key.replace(/[^\w.-]+/g, "_")}.${record.format}`}
          >
            {t("recovery.download")}
          </a>
        )}
        <Button size="small" onClick={discard}>
          {t("recovery.discard")}
        </Button>
      </div>
    </section>
  );
};
