import type { StrategyTraceResponse } from "../../../shared/api";

export type LinkedTraceRow = {
  securityId: string;
  raw: StrategyTraceResponse["raw"];
  nodes: StrategyTraceResponse["trace"]["rows"];
  construction:
    NonNullable<StrategyTraceResponse["target"]>["construction"][number] | null;
};

/** Join backend projections by identity only; every displayed calculation remains server-owned. */
export const projectLinkedTraceRows = (
  response: StrategyTraceResponse,
  securityIds: readonly string[],
  nodeRows: StrategyTraceResponse["trace"]["rows"] = response.trace.rows,
  selectedRows: StrategyTraceResponse["trace"]["rows"] = [],
): LinkedTraceRow[] => {
  const identities = new Set(
    nodeRows.map(
      (row) => `${row.security_id}\u0000${row.node_id}\u0000${row.as_of}`,
    ),
  );
  const completeRows = [
    ...nodeRows,
    ...selectedRows.filter(
      (row) =>
        !identities.has(
          `${row.security_id}\u0000${row.node_id}\u0000${row.as_of}`,
        ),
    ),
  ];
  const construction = new Map(
    (response.target?.construction ?? []).map((row) => [row.security_id, row]),
  );
  return securityIds.map((securityId) => ({
    securityId,
    construction: construction.get(securityId) ?? null,
    raw: response.raw.filter((row) => row.security_id === securityId),
    nodes: completeRows.filter((row) => row.security_id === securityId),
  }));
};
