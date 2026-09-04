import { t } from "../../../shared/config";
import type { DocumentState } from "./document-state";
import type { SaveStatus } from "./use-save-document";

/**
 * One line for the top bar. Text-derived facts (empty, dirty) win over the last save outcome so
 * "방금 저장됨" disappears as soon as the user types again; a failure stays visible until the
 * next attempt because the draft is still unsaved.
 */
export const saveStatusText = (
  state: DocumentState,
  status: SaveStatus,
): string => {
  if (status.kind === "saving") return t("save.saving");
  if (status.kind === "conflict") {
    return status.detail
      ? `${t("save.conflict")} (${status.detail})`
      : t("save.conflict");
  }
  if (status.kind === "invalid") {
    return status.detail
      ? `${t("save.invalid")}: ${status.detail}`
      : t("save.invalid");
  }
  if (status.kind === "failed") return t("save.failed");
  if (state.source.trim().length === 0) return t("save.empty");
  if (state.dirty && state.parse?.status === "rejected")
    return t("save.blocked.syntax");
  if (
    state.dirty &&
    state.compiled !== null &&
    state.compiledVersion === state.sourceVersion &&
    state.compiled.diagnostics.some(
      (diagnostic) => diagnostic.severity === "error",
    )
  )
    return t("save.blocked.invalid");
  if (state.dirty) return t("save.unsaved");
  if (status.kind === "saved") return t("ide.savedJustNow");
  if (state.baseRevision !== null) {
    return `${t("save.savedRevision")} v${state.baseRevision}`;
  }
  return t("page.newStrategy.draft");
};

export const saveStatusTone = (
  state: DocumentState,
  status: SaveStatus,
): "ok" | "warn" | "error" => {
  if (
    status.kind === "conflict" ||
    status.kind === "invalid" ||
    status.kind === "failed"
  )
    return "error";
  if (state.dirty && state.parse?.status === "rejected") return "error";
  if (state.dirty) return "warn";
  return "ok";
};
