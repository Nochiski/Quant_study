import type { StrategyTraceResponse } from "../../../shared/api";

export type LinkedTraceRow = {
  securityId: string;
  raw: StrategyTraceResponse["raw"];
  nodes: StrategyTraceResponse["trace"]["rows"];
  construction:
    | NonNullable<StrategyTraceResponse["target"]>["construction"][number]
    | null;
};

/** Join backend projections by identity only; every displayed calculation remains server-owned. */
export const projectLinkedTraceRows = (
  response: StrategyTraceResponse,
  securityIds: readonly string[],
  nodeRows: StrategyTraceResponse["trace"]["rows"] = response.trace.rows,
): LinkedTraceRow[] => {
  const construction = new Map(
    (response.target?.construction ?? []).map((row) => [row.security_id, row]),
  );
  return securityIds.map((securityId) => ({
    securityId,
    construction: construction.get(securityId) ?? null,
    raw: response.raw.filter(
      (row) => row.security_id === securityId,
    ),
    nodes: nodeRows.filter((row) => row.security_id === securityId),
  }));
};
