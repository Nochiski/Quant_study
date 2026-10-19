/**
 * 문서의 팩터가 내 팩터의 예전 버전을 복사한 것이면 새 버전을 알리고, 그 팩터의 `graph` 만 바꾸게 한다
 * (US-CS-08). 어느 버전과 같은지는 backend `matches` 가 compile 로 판정하고, 바꾸기는 source 트랜잭션 한 번이라
 * 되돌리기 한 번에 취소된다. 라이브러리를 고쳐도 문서는 사용자가 바꾸기 전까지 그대로다.
 */
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import {
  libraryFactorsQuery,
  useLibraryMatches,
  type LibraryFactorBody,
  type LibraryFactorMatchBody,
} from "../../../entities/factor-library";
import { t, tFill } from "../../../shared/config";
import { valueAtPointer } from "../../../shared/lib/yaml12";
import { Button } from "../../../shared/ui";
import { currentCompileOutcome, type DocumentState } from "../model/document-state";
import { libraryReplacement } from "../model/library-factors";
import { NO_FOCUS } from "../model/use-field-editing";
import type { FormProjectionState } from "../model/use-form-projection";
import type { SourceTransactions } from "../model/use-source-transactions";
import { TransactionFeedbackNote } from "./transaction-feedback";
import "./library-factor.css";

const NOTICE_OWNER = "library-notice";

export const LibraryUpdateNotice = ({
  document,
  form,
  transactions,
}: {
  document: DocumentState;
  form: FormProjectionState;
  transactions: SourceTransactions;
}) => {
  const titleId = useId();
  const compiled = currentCompileOutcome(document);
  const specHash = compiled?.specHash ?? null;
  const matches = useLibraryMatches(specHash === null ? null : document.source, specHash);
  const library = useQuery({
    ...libraryFactorsQuery(),
    enabled: (matches.data?.matches.length ?? 0) > 0,
  });
  const shown = (matches.data?.matches ?? []).filter(
    (match) => match.status !== "current",
  );
  const entries = new Map(
    (library.data?.items ?? []).map((entry) => [entry.library_id, entry]),
  );
  if (shown.length === 0 || entries.size === 0) return null;
  return (
    <section className="library-notice" aria-labelledby={titleId}>
      <h2 id={titleId}>{t("library.notice.title")}</h2>
      <ul>
        {shown.map((match) => {
          const entry = entries.get(match.library_id);
          return entry === undefined ? null : (
            <NoticeRow
              key={`${match.factor_index}:${match.library_id}`}
              match={match}
              entry={entry}
              form={form}
              transactions={transactions}
            />
          );
        })}
      </ul>
      <TransactionFeedbackNote
        feedback={transactions.feedbackFor(NOTICE_OWNER)}
        owner={NOTICE_OWNER}
      />
    </section>
  );
};

const NoticeRow = ({
  match,
  entry,
  form,
  transactions,
}: {
  match: LibraryFactorMatchBody;
  entry: LibraryFactorBody;
  form: FormProjectionState;
  transactions: SourceTransactions;
}) => {
  const item = valueAtPointer(form.tree, `/factors/${match.factor_index}`).value;
  const factorName =
    typeof item === "object" && item !== null && "label" in item && typeof item.label === "string"
      ? item.label
      : match.factor_id;
  if (match.status === "modified")
    return (
      <li>
        {tFill("library.notice.modified", { factor: factorName, name: entry.name })}
      </li>
    );
  const latest = entry.versions.at(-1)!;
  const operations = libraryReplacement(
    match.factor_index,
    { factor: latest.factor, parameters: latest.parameters },
    form.tree,
    form.projection,
  );
  return (
    <li>
      {tFill("library.notice.outdated", {
        factor: factorName,
        name: entry.name,
        matched: match.matched_version ?? "",
        latest: match.latest_version,
      })}{" "}
      {operations === null ? (
        <span role="note">{t("library.notice.blocked")}</span>
      ) : (
        <Button
          size="small"
          disabled={transactions.disabled !== null || transactions.settling}
          onClick={() =>
            transactions.apply(operations, factorName, NOTICE_OWNER, NO_FOCUS)
          }
        >
          {tFill("library.notice.replace", { latest: match.latest_version })}
        </Button>
      )}{" "}
      <small>{t("library.notice.trial")}</small>
    </li>
  );
};
