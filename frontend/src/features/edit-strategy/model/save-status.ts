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
  if (status.kind === "conflict") return t("save.conflict");
  if (status.kind === "invalid") {
    return status.detail
      ? `${t("save.invalid")}: ${status.detail}`
      : t("save.invalid");
  }
  if (status.kind === "failed") return t("save.failed");
  if (state.source.trim().length === 0) return t("save.empty");
  if (state.dirty) return t("save.unsaved");
  if (status.kind === "saved") return t("ide.savedJustNow");
  if (state.baseRevision !== null) {
    return `${t("save.savedRevision")} v${state.baseRevision}`;
  }
  return t("page.newStrategy.draft");
};
