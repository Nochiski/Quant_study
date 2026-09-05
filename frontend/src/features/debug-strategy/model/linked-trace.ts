import type { StrategyTraceResponse } from "../../../shared/api";

export type LinkedTraceRow = {
  securityId: string;
  raw: StrategyTraceResponse["raw"];
  nodes: StrategyTraceResponse["trace"]["rows"];
  construction: NonNullable<
    StrategyTraceResponse["target"]
  >["construction"][number];
};

/** Join backend projections by identity only; every displayed calculation remains server-owned. */
export const projectLinkedTraceRows = (
  response: StrategyTraceResponse,
): LinkedTraceRow[] => {
  if (response.target === null) return [];
  return response.target.construction.map((construction) => ({
    securityId: construction.security_id,
    construction,
    raw: response.raw.filter(
      (row) => row.security_id === construction.security_id,
    ),
    nodes: response.trace.rows.filter(
      (row) => row.security_id === construction.security_id,
    ),
  }));
};
