import { useMemo, useRef, useState } from "react";

import { t, type MessageKey } from "../../../shared/config";
import type {
  DiagnosticKind,
  DocumentDiagnostic,
} from "../model/document-state";
import {
  DEFAULT_PROBLEM_FILTERS,
  DIAGNOSTIC_KINDS,
  diagnosticIdentity,
  projectProblems,
  type ProblemFilters,
} from "../model/problem-list";
import "./diagnostics-panel.css";

type DiagnosticsPanelProps = {
  diagnostics: readonly DocumentDiagnostic[];
  /** Compile result belongs to an older text: entries are shown dimmed and cannot be selected. */
  stale: boolean;
  onSelect: (diagnostic: DocumentDiagnostic) => void;
};

const KIND_LABEL: Record<DiagnosticKind, MessageKey> = {
  syntax: "problems.kind.syntax",
  structural: "problems.kind.structural",
  semantic: "problems.kind.semantic",
  capability: "problems.kind.capability",
};

type CopyState =
  { status: "idle" } | { status: "done"; label: string } | { status: "failed" };

type ProblemRowProps = {
  diagnostic: DocumentDiagnostic;
  stale: boolean;
  onSelect: (diagnostic: DocumentDiagnostic) => void;
  onCopy: (value: string, label: string) => void;
};

const ProblemRow = ({
  diagnostic,
  stale,
  onSelect,
  onCopy,
}: ProblemRowProps) => {
  const position = diagnostic.range
    ? `${diagnostic.range.start.line + 1}:${diagnostic.range.start.column + 1}`
    : null;
  const pointerLabel =
    diagnostic.pointer === "" ? t("problems.rootPointer") : diagnostic.pointer;
  const nodeId = diagnostic.nodeId;
  const severityLabel =
    diagnostic.severity === "error"
      ? t("problems.error")
      : t("problems.warning");
  const jumpLabel = t("problems.jump")
    .replace("{severity}", severityLabel)
    .replace("{message}", diagnostic.message)
    .replace("{pointer}", pointerLabel);

  return (
    <li className={`problems__item problems__item--${diagnostic.severity}`}>
      <button
        type="button"
        className="problems__navigate"
        disabled={stale || diagnostic.range === null}
        onClick={() => onSelect(diagnostic)}
        aria-label={jumpLabel}
      >
        <span className="problems__severity">{severityLabel}</span>
        <span className="problems__kind">{t(KIND_LABEL[diagnostic.kind])}</span>
        <span className="problems__message">{diagnostic.message}</span>
        <code className="problems__where">
          {position ? `${position} ` : ""}
          {pointerLabel}
        </code>
      </button>
      <span className="problems__actions">
        <button
          type="button"
          className="problems__copy"
          onClick={() =>
            onCopy(
              diagnostic.pointer,
              `${t("problems.pointer")} ${pointerLabel}`,
            )
          }
          aria-label={t("problems.copyPointer").replace(
            "{pointer}",
            pointerLabel,
          )}
        >
          {t("problems.copyPath")}
        </button>
        {nodeId ? (
          <button
            type="button"
            className="problems__copy"
            onClick={() => onCopy(nodeId, `${t("problems.nodeId")} ${nodeId}`)}
            aria-label={t("problems.copyNodeId").replace("{nodeId}", nodeId)}
          >
            {t("problems.copyNode")}
          </button>
        ) : null}
      </span>
    </li>
  );
};

/**
 * Professional Problems panel (WORKFLOW P4-03). Backend/parser diagnostics remain immutable
 * truth; this component owns only exact deduplication, kind filtering, severity grouping,
 * editor navigation and copying diagnostic identifiers.
 */
export const DiagnosticsPanel = ({
  diagnostics,
  stale,
  onSelect,
}: DiagnosticsPanelProps) => {
  const [filters, setFilters] = useState<ProblemFilters>(
    DEFAULT_PROBLEM_FILTERS,
  );
  const [copyState, setCopyState] = useState<CopyState>({ status: "idle" });
  const copyRequest = useRef(0);
  const projection = useMemo(
    () => projectProblems(diagnostics, filters),
    [diagnostics, filters],
  );

  if (projection.diagnostics.length === 0) return null;

  const toggleKind = (kind: DiagnosticKind) => {
    setFilters((current) => ({ ...current, [kind]: !current[kind] }));
  };
  const copy = async (value: string, label: string) => {
    const request = ++copyRequest.current;
    try {
      await navigator.clipboard.writeText(value);
      if (request === copyRequest.current)
        setCopyState({ status: "done", label });
    } catch {
      if (request === copyRequest.current) setCopyState({ status: "failed" });
    }
  };
  const sections = [
    {
      severity: "error" as const,
      label: t("problems.errors"),
      diagnostics: projection.errors,
    },
    {
      severity: "warning" as const,
      label: t("problems.warnings"),
      diagnostics: projection.warnings,
    },
  ];

  return (
    <section
      className={`problems ${stale ? "problems--stale" : ""}`.trim()}
      aria-label={t("problems.title")}
    >
      <header className="problems__header">
        <p className="problems__summary" role="status">
          {t("problems.summary")
            .replace("{errors}", String(projection.errorCount))
            .replace("{warnings}", String(projection.warningCount))}
          {stale ? ` · ${t("document.stale")}` : ""}
        </p>
        <div
          className="problems__filters"
          role="group"
          aria-label={t("problems.filters")}
        >
          {DIAGNOSTIC_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              className="problems__filter"
              aria-pressed={filters[kind]}
              onClick={() => toggleKind(kind)}
            >
              <span>{t(KIND_LABEL[kind])}</span>
              <span className="problems__filter-count">
                {projection.countsByKind[kind]}
              </span>
            </button>
          ))}
        </div>
      </header>

      <div className="problems__body">
        {projection.errors.length + projection.warnings.length === 0 ? (
          <p className="problems__empty">{t("problems.noneFiltered")}</p>
        ) : (
          sections.map((section) =>
            section.diagnostics.length > 0 ? (
              <section
                className="problems__section"
                key={section.severity}
                aria-label={`${section.label} ${section.diagnostics.length}`}
              >
                <h3 className="problems__section-title">
                  {section.label}
                  <span>{section.diagnostics.length}</span>
                </h3>
                <ul className="problems__list">
                  {section.diagnostics.map((diagnostic) => (
                    <ProblemRow
                      key={diagnosticIdentity(diagnostic)}
                      diagnostic={diagnostic}
                      stale={stale}
                      onSelect={onSelect}
                      onCopy={(value, label) => void copy(value, label)}
                    />
                  ))}
                </ul>
              </section>
            ) : null,
          )
        )}
      </div>

      <p className="problems__copy-status" role="status" aria-live="polite">
        {copyState.status === "done"
          ? t("problems.copied").replace("{target}", copyState.label)
          : copyState.status === "failed"
            ? t("problems.copyFailed")
            : ""}
      </p>
    </section>
  );
};
