import { useQuery } from "@tanstack/react-query";
import { strategyWorkbenchApi } from "../../../shared/api";
import { currentCompile, type DocumentState } from "./document-state";
import type { DraftSemanticDiff } from "./diff-projection";

/** 요청 신원과 현재 compile의 hash가 맞는 backend 결과만 보여 준다. */
export const useDraftSemanticDiff = (
  state: DocumentState,
  active: boolean,
): DraftSemanticDiff => {
  const current = currentCompile(state);
  const hasBase =
    state.strategyId !== null &&
    state.baseRevision !== null &&
    state.baseSpecHash !== null;
  const query = useQuery({
    queryKey: [
      "strategy-draft-diff",
      state.strategyId,
      state.baseRevision,
      state.documentEpoch,
      state.sourceVersion,
      state.format,
      state.source,
    ],
    queryFn: ({ signal }) =>
      strategyWorkbenchApi.diffStrategyDraft(
        state.strategyId!,
        {
          base_revision: state.baseRevision!,
          source: state.source,
          format: state.format,
        },
        signal,
      ),
    enabled: active && hasBase && current !== null,
    retry: false,
  });
  if (!hasBase) return { status: "no-base" };
  if (current === null) return { status: "current-unavailable" };
  if (query.isError) return { status: "unavailable" };
  if (query.data === undefined) return { status: "base-pending" };
  const result = query.data;
  if (
    result.strategy_id !== state.strategyId ||
    result.base_revision !== state.baseRevision ||
    result.base_spec_hash !== state.baseSpecHash ||
    result.target_spec_hash !== current.specHash ||
    result.source_hash !== current.sourceHash
  )
    return { status: "incoherent" };
  return {
    status: "ready",
    baseSpecHash: result.base_spec_hash,
    currentSpecHash: result.target_spec_hash,
    changes: result.changes,
  };
};
