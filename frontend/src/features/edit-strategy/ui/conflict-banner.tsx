import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { strategyDiffQuery } from "../../../entities/strategy";
import type { DiffEntry } from "../../../shared/api";
import { t } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import "./conflict-banner.css";

type ConflictBannerProps = {
  strategyId: string;
  /** Revision the draft was based on. */
  baseRevision: number;
  /** Structured latest revision from the backend 409 contract. */
  latestRevision: number;
  /** The user's current text, preserved verbatim. */
  source: string;
};

const render = (value: unknown): string =>
  value === undefined ? "—" : JSON.stringify(value);

const DIFF_KIND_MESSAGE = {
  added: "conflict.diff.added",
  removed: "conflict.diff.removed",
  changed: "conflict.diff.changed",
} as const;

const DiffList = ({ entries }: { entries: DiffEntry[] }) =>
  entries.length === 0 ? (
    <p className="conflict__empty">{t("conflict.diff.empty")}</p>
  ) : (
    <table className="conflict__diff">
      <thead>
        <tr>
          <th scope="col">{t("conflict.diff.pointer")}</th>
          <th scope="col">{t("conflict.diff.kind")}</th>
          <th scope="col">{t("conflict.diff.before")}</th>
          <th scope="col">{t("conflict.diff.after")}</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={`${entry.kind}:${entry.pointer}`}>
            <td>
              <code>{entry.pointer}</code>
            </td>
            <td>{t(DIFF_KIND_MESSAGE[entry.kind])}</td>
            <td>
              <code>{render(entry.before)}</code>
            </td>
            <td>
              <code>{render(entry.after)}</code>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

/**
 * Shown when a save was refused because the server holds a newer revision (409). The draft
 * text stays as typed; the banner names both revisions and offers exactly three ways out —
 * open the server version, copy the current text, or look at the semantic diff between the
 * base and the server revision. No automatic merge exists before conflict resolution (P4-08).
 */
export const ConflictBanner = ({
  strategyId,
  baseRevision,
  latestRevision,
  source,
}: ConflictBannerProps) => {
  const [diffOpen, setDiffOpen] = useState(false);
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  const diff = useQuery({
    ...strategyDiffQuery(strategyId, baseRevision, latestRevision),
    enabled: diffOpen && latestRevision !== baseRevision,
  });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(source);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <section className="conflict" aria-label={t("conflict.title")}>
      <div className="conflict__summary" role="alert">
        <Badge tone="error">{t("conflict.title")}</Badge>
        <span>
          {t("conflict.revisions")
            .replace("{server}", `v${latestRevision}`)
            .replace("{base}", `v${baseRevision}`)}
        </span>
        <span className="conflict__note">{t("conflict.note")}</span>
      </div>
      <div className="conflict__actions">
        <Link
          className="ui-button ui-button--primary ui-button--small"
          to="/research/strategies/$strategyId/revisions/$revision"
          params={{ strategyId, revision: String(latestRevision) }}
          search={{
            view: undefined,
            path: undefined,
            asOf: undefined,
            security: undefined,
          }}
        >
          {t("conflict.openServer").replace(
            "{server}",
            `v${latestRevision}`,
          )}
        </Link>
        <Button size="small" onClick={() => void copy()}>
          {t("conflict.copy")}
        </Button>
        {copied === "done" ? (
          <span role="status">{t("conflict.copied")}</span>
        ) : null}
        {copied === "failed" ? (
          <span role="status">{t("conflict.copyFailed")}</span>
        ) : null}
        <Button
          size="small"
          onClick={() => setDiffOpen((open) => !open)}
          aria-expanded={diffOpen}
          disabled={latestRevision === baseRevision}
        >
          {diffOpen ? t("conflict.closeDiff") : t("conflict.openDiff")}
        </Button>
      </div>
      {diffOpen ? (
        <div className="conflict__diff-panel" aria-live="polite">
          {diff.isPending ? (
            <p className="conflict__empty">{t("page.loading")}</p>
          ) : diff.isError ? (
            <p className="conflict__empty" role="alert">
              {t("conflict.diff.error")}
            </p>
          ) : diff.data ? (
            <>
              <p className="conflict__diff-title">
                {t("conflict.diff.title")
                  .replace("{base}", `v${diff.data.base_revision}`)
                  .replace("{target}", `v${diff.data.target_revision}`)}
              </p>
              <DiffList entries={diff.data.changes} />
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};
