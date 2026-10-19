/**
 * 내 팩터(US-CS-08). 저장한 팩터와 버전 기록, 지금 데이터로 쓸 수 있는지를 보이고 숨기기만 한다. 저장·새 버전은
 * 편집기 팩터 카드에서, 꺼내 쓰기는 새 전략 안내와 편집기에서 한다. 전략은 저장할 때의 정의를 복사하므로
 * 여기서 무엇을 해도 기존 전략 문서는 그대로다.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  libraryFactorsQuery,
  useSetLibraryFactorHidden,
  type LibraryFactorBody,
} from "../../../entities/factor-library";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { displayTime } from "../../../shared/lib/display-time";
import { Badge, Button, EmptyState, FailureNotice } from "../../../shared/ui";
import "./factor-library-page.css";

export const FactorLibraryPage = () => {
  const [showHidden, setShowHidden] = useState(false);
  const library = useQuery(libraryFactorsQuery(showHidden));
  return (
    <section className="page data-list-page" aria-labelledby="factor-library-title">
      <header className="data-list-page__header">
        <div>
          <h1 id="factor-library-title">{t("library.page.title")}</h1>
          <p>{t("library.page.description")}</p>
        </div>
        <label className="factor-library__toggle">
          <input
            type="checkbox"
            checked={showHidden}
            onChange={(event) => setShowHidden(event.target.checked)}
          />{" "}
          {t("library.page.showHidden")}
        </label>
      </header>
      {library.isPending ? (
        <p className="page-state" role="status">
          {t("library.mine.loading")}
        </p>
      ) : library.isError ? (
        <FailureNotice
          message={t("library.mine.failed")}
          reason={failureReason(library.error)}
        />
      ) : library.data.items.length === 0 ? (
        <EmptyState
          title={t("library.page.title")}
          description={t("library.page.empty")}
        />
      ) : (
        <ul className="factor-library__list">
          {library.data.items.map((entry) => (
            <LibraryEntry key={entry.library_id} entry={entry} />
          ))}
        </ul>
      )}
    </section>
  );
};

const LibraryEntry = ({ entry }: { entry: LibraryFactorBody }) => {
  const visibility = useSetLibraryFactorHidden();
  return (
    <li className="factor-library__entry">
      <header>
        <h2>{entry.name}</h2>
        {entry.hidden ? <Badge tone="neutral">{t("library.page.hidden")}</Badge> : null}
        {entry.availability.available ? null : (
          <Badge tone="warn">{t("library.mine.unavailable")}</Badge>
        )}
        <Button
          size="small"
          tone="ghost"
          disabled={visibility.isPending}
          onClick={() =>
            visibility.mutate({ libraryId: entry.library_id, hidden: !entry.hidden })
          }
          aria-label={`${entry.name} · ${t(entry.hidden ? "library.page.unhide" : "library.page.hide")}`}
        >
          {t(entry.hidden ? "library.page.unhide" : "library.page.hide")}
        </Button>
      </header>
      <dl>
        <div>
          <dt>{t("library.page.key")}</dt>
          <dd>
            <code>{entry.key}</code>
          </dd>
        </div>
        <div>
          <dt>{t("library.page.history")}</dt>
          <dd>
            {tFill("library.page.versions", {
              count: entry.versions.length,
              latest: entry.latest_version,
            })}
          </dd>
        </div>
      </dl>
      {entry.description === "" ? null : <p>{entry.description}</p>}
      {entry.availability.available ? null : (
        <p className="factor-library__missing">
          {tFill("library.page.missing", {
            fields: entry.availability.missing_fields.join(", "),
          })}{" "}
          {entry.availability.reasons.join(" ")}
        </p>
      )}
      <details className="ui-disclosure">
        <summary>{t("library.page.history")}</summary>
        <ol>
          {entry.versions.map((version) => (
            <li key={version.version}>
              {tFill("library.page.versionCreated", {
                version: version.version,
                date: displayTime(version.created_at),
              })}
              {version.note ? ` · ${version.note}` : null}
            </li>
          ))}
        </ol>
      </details>
    </li>
  );
};
