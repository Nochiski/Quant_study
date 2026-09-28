import { useId } from "react";

import { t, tName } from "../../../shared/config";
import {
  SNIPPET_CATEGORIES,
  type CanonicalSnippet,
  type SnippetCatalogSource,
} from "../model/canonical-snippets";
import type { SnippetFeedback } from "../model/use-snippet-insertion";
import "./snippet-catalog.css";

type SnippetCatalogProps = {
  snippets: readonly CanonicalSnippet[];
  sourceStatus: SnippetCatalogSource["status"];
  feedback: SnippetFeedback;
  onInsert: (snippet: CanonicalSnippet) => void;
};

const feedbackText = (feedback: SnippetFeedback): string | null => {
  if (feedback.status === "idle") return null;
  if (feedback.status === "inserted")
    return `${feedback.label}: ${t("snippet.inserted")}`;
  return `${feedback.label}: ${t(`snippet.error.${feedback.reason}`)}`;
};

/** 섹션·예시 두 그룹의 스키마 기반 스니펫 카탈로그. 적용은 model 훅이 맡는다. */
export const SnippetCatalog = ({
  snippets,
  sourceStatus,
  feedback,
  onInsert,
}: SnippetCatalogProps) => {
  const idBase = useId();
  const message = feedbackText(feedback);
  return (
    <div className="snippet-catalog">
      <p className="snippet-catalog__help">{t("snippet.help")}</p>
      {sourceStatus === "loading" ? (
        <p className="snippet-catalog__state" role="status">
          {t("snippet.loading")}
        </p>
      ) : sourceStatus === "unavailable" ? (
        <p className="snippet-catalog__state" role="alert">
          {t("snippet.unavailable")}
        </p>
      ) : sourceStatus === "incompatible" ? (
        <p className="snippet-catalog__state" role="alert">
          {t("snippet.incompatible")}
        </p>
      ) : (
        <div className="snippet-catalog__groups">
          {SNIPPET_CATEGORIES.map((category) => {
            const entries = snippets.filter(
              (snippet) => snippet.category === category,
            );
            const headingId = `${idBase}-${category}`;
            return (
              <section
                key={category}
                className="snippet-catalog__group"
                aria-labelledby={headingId}
              >
                <h3 id={headingId}>{t(`snippet.category.${category}`)}</h3>
                {category === "example" ? (
                  <p className="snippet-catalog__note">
                    {t("snippet.example.note")}
                  </p>
                ) : null}
                {entries.length === 0 ? (
                  <p className="snippet-catalog__empty">{t("snippet.empty")}</p>
                ) : (
                  <ul>
                    {entries.map((snippet) => (
                      <li key={snippet.id}>
                        <button
                          type="button"
                          className="snippet-catalog__insert"
                          aria-label={`${tName(snippet.descriptionKey) ?? snippet.label} · ${t("snippet.insert")}`}
                          onClick={() => onInsert(snippet)}
                        >
                          <span className="snippet-catalog__label">
                            {snippet.kind === "section" ? (
                              <>
                                {tName(snippet.descriptionKey) ?? null}{" "}
                                <code>{snippet.label}</code>
                              </>
                            ) : (
                              snippet.label
                            )}
                          </span>
                          <span aria-hidden="true">+</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}
      {message ? (
        <p
          className={`snippet-catalog__feedback snippet-catalog__feedback--${feedback.status}`}
          role={feedback.status === "error" ? "alert" : "status"}
        >
          {message}
        </p>
      ) : null}
    </div>
  );
};
