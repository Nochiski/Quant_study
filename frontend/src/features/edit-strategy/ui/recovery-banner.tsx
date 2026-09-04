import { useMemo } from "react";

import { t } from "../../../shared/config";
import { lineDiffSummary } from "../../../shared/lib/text-diff";
import { Badge, Button } from "../../../shared/ui";
import type { Recovery } from "../model/use-autosave";
import "./recovery-banner.css";

type RecoveryBannerProps = {
  recovery: Recovery;
  /** Text the base was loaded with (server original or starter). */
  original: string;
};

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
export const RecoveryBanner = ({ recovery, original }: RecoveryBannerProps) => {
  const { record, schemaMismatch, restore, discard } = recovery;
  const diff = useMemo(
    () => lineDiffSummary(original, record.source),
    [original, record.source],
  );
  return (
    <section className="recovery" aria-label={t("recovery.title")}>
      <div className="recovery__summary">
        <Badge tone={schemaMismatch ? "warn" : "info"}>
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
        {schemaMismatch ? (
          <span className="recovery__mismatch">
            {t("recovery.schemaMismatch").replace(
              "{draft}",
              record.schemaVersion ?? "?",
            )}
          </span>
        ) : null}
      </div>
      {diff.preview.length > 0 ? (
        <pre className="recovery__preview">{diff.preview.join("\n")}</pre>
      ) : null}
      <div className="recovery__actions">
        {schemaMismatch ? (
          <a
            className="ui-button ui-button--primary ui-button--small"
            href={downloadHref(record.source)}
            download={`strategy-draft-${record.key.replace(/[^\w.-]+/g, "_")}.${record.format}`}
          >
            {t("recovery.download")}
          </a>
        ) : (
          <Button size="small" tone="primary" onClick={restore}>
            {t("recovery.restore")}
          </Button>
        )}
        <Button size="small" onClick={discard}>
          {t("recovery.discard")}
        </Button>
      </div>
    </section>
  );
};
