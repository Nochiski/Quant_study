import type { StrategyTraceResponse } from "../../../shared/api";

export type TargetTapeProjectionRow = {
  securityId: string;
  score: number | null;
  rank: number | null;
  selected: boolean | null;
  exclusionReasons: string[];
  targetWeight: number | null;
  nodeValue: number | boolean | null;
  nodeStatus: string | null;
};

/** Join only server-returned TargetTape/trace fields; no portfolio value is recomputed here. */
export const projectTargetTapeRows = (
  response: StrategyTraceResponse,
  securityIds: readonly string[],
  nodeId: string,
  selectedRows: StrategyTraceResponse["trace"]["rows"] = response.trace.rows,
): TargetTapeProjectionRow[] => {
  const candidates = new Map(
    (response.target?.candidates ?? []).map((row) => [row.security_id, row]),
  );
  const traceRows = new Map(
    selectedRows
      .filter((row) => row.node_id === nodeId)
      .map((row) => [row.security_id, row]),
  );
  return securityIds.map((securityId) => {
    const candidate = candidates.get(securityId);
    const trace = traceRows.get(securityId);
    return {
      securityId,
      score: candidate?.composite_score ?? null,
      rank: candidate?.rank ?? null,
      selected: candidate?.selected ?? null,
      exclusionReasons: candidate?.exclusion_reasons ?? [],
      targetWeight: candidate?.target_weight ?? null,
      nodeValue: trace?.value ?? null,
      nodeStatus: trace?.status ?? null,
    };
  });
};
