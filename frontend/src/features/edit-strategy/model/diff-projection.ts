import type { DiffEntry } from "../../../shared/api";
import { lineDiff, type TextDiff } from "../../../shared/lib/text-diff";
import { type DocumentState } from "./document-state";

export type DraftSemanticDiff =
  | { status: "no-base" }
  | { status: "base-pending" }
  | { status: "current-unavailable" }
  | { status: "incoherent" }
  | { status: "unavailable" }
  | {
      status: "ready";
      baseSpecHash: string;
      currentSpecHash: string;
      changes: DiffEntry[];
    };

export type DraftDiffProjection = {
  source: TextDiff;
  sourceBase: "saved-revision" | "empty-draft";
  semantic: DraftSemanticDiff;
};

/** 원문 차이만 로컬 투영한다. 의미·승격 해제·배열 비교는 backend가 소유한다. */
export const projectDraftDiff = (
  state: DocumentState,
): Pick<DraftDiffProjection, "source" | "sourceBase"> => ({
  source: lineDiff(state.savedSource ?? "", state.source),
  sourceBase: state.savedSource === null ? "empty-draft" : "saved-revision",
});
