import { t, type MessageKey } from "../../../shared/config";
import type { DocumentDiagnostic } from "../model/document-state";
import "./diagnostics-panel.css";

type DiagnosticsPanelProps = {
  diagnostics: readonly DocumentDiagnostic[];
  /** Compile result belongs to an older text: entries are shown dimmed and cannot be selected. */
  stale: boolean;
  onSelect: (diagnostic: DocumentDiagnostic) => void;
};

const KIND_LABEL: Record<DocumentDiagnostic["kind"], MessageKey> = {
  syntax: "problems.kind.syntax",
  structural: "problems.kind.structural",
  semantic: "problems.kind.semantic",
  capability: "problems.kind.capability",
} as const;

/**
 * Problems list under the editor (WORKFLOW P3-04): errors before warnings, each entry a button
 * that moves the editor selection to the diagnostic's source range. Entries without a range
 * (transport failures, root-level problems) are announced but not navigable.
 */
export const DiagnosticsPanel = ({
  diagnostics,
  stale,
  onSelect,
}: DiagnosticsPanelProps) => {
  if (diagnostics.length === 0) return null;
  const errors = diagnostics.filter((d) => d.severity === "error");
  const warnings = diagnostics.filter((d) => d.severity !== "error");
  const ordered = [...errors, ...warnings];
  return (
    <section
      className={`problems ${stale ? "problems--stale" : ""}`.trim()}
      aria-label={t("problems.title")}
    >
      <p className="problems__summary" role="status">
        {t("problems.summary")
          .replace("{errors}", String(errors.length))
          .replace("{warnings}", String(warnings.length))}
        {stale ? ` · ${t("document.stale")}` : ""}
      </p>
      <ul className="problems__list">
        {ordered.map((diagnostic, index) => {
          const position = diagnostic.range
            ? `${diagnostic.range.start.line + 1}:${diagnostic.range.start.column + 1}`
            : null;
          return (
            <li key={`${diagnostic.code}-${diagnostic.pointer}-${index}`}>
              <button
                type="button"
                className={`problems__item problems__item--${diagnostic.severity}`}
                disabled={stale || diagnostic.range === null}
                onClick={() => onSelect(diagnostic)}
              >
                <span className="problems__severity">
                  {diagnostic.severity === "error"
                    ? t("problems.error")
                    : t("problems.warning")}
                </span>
                <span className="problems__kind">
                  {t(KIND_LABEL[diagnostic.kind])}
                </span>
                <span className="problems__message">{diagnostic.message}</span>
                <code className="problems__where">
                  {position ? `${position} ` : ""}
                  {diagnostic.pointer || "/"}
                </code>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
};
