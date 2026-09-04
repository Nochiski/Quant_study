import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { useDatasetCatalog } from "../../../entities/dataset";
import { useFactorCatalog } from "../../../entities/factor";
import {
  strategyContractQuery,
  strategySchemaQuery,
} from "../../../entities/strategy";
import type {
  EditorCompletionSource,
  EditorHoverSource,
} from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  buildCompletionSource,
  buildHoverSource,
  type AssistDeps,
} from "./schema-assist";
import type { JsonSchema } from "./schema-navigator";

export type SchemaAssist = {
  completionSource: EditorCompletionSource;
  hoverSource: EditorHoverSource;
  /** True while any of the schema, contract or catalogs is still loading. */
  loading: boolean;
  /** Schema version the runtime schema declares; null until loaded. */
  schemaVersion: string | null;
  /** Backend-owned runtime schema used by read-only projections such as the outline. */
  schema: JsonSchema | null;
};

/** One page holds every mock field/factor today; a larger catalog is paged by search (P6). */
const CATALOG_PAGE = { page_size: 100 } as const;

/**
 * Binds the schema-driven editor assistance to live data: runtime schema and contract (P1-05)
 * and the equity/factor catalogs the contract links to. The sources read the latest document
 * state through a ref, so the editor keeps one stable function per data change.
 */
export const useSchemaAssist = (state: DocumentState): SchemaAssist => {
  const schema = useQuery(strategySchemaQuery());
  const contract = useQuery(strategyContractQuery());
  const fields = useDatasetCatalog(CATALOG_PAGE);
  const factors = useFactorCatalog(CATALOG_PAGE);

  // The sources are two stable functions the editor registers once; they read the latest
  // document state and data through refs at call time (after commit), never during render.
  const latest = useRef<AssistDeps>({
    schema: null,
    contract: [],
    catalogs: { equityFields: [], factors: [] },
    getState: () => state,
  });
  const schemaData = schema.data?.schema;
  const contractRows = contract.data?.contract.fields;
  const fieldRows = fields.data?.fields;
  const factorRows = factors.data?.factors;
  useEffect(() => {
    latest.current = {
      schema: (schemaData as JsonSchema | undefined) ?? null,
      contract: contractRows ?? [],
      catalogs: { equityFields: fieldRows ?? [], factors: factorRows ?? [] },
      getState: () => state,
    };
  }, [schemaData, contractRows, fieldRows, factorRows, state]);

  const completionSource = useCallback<EditorCompletionSource>(
    (context) => buildCompletionSource(latest.current)(context),
    [],
  );
  const hoverSource = useCallback<EditorHoverSource>(
    (offset) => buildHoverSource(latest.current)(offset),
    [],
  );
  const loading =
    schema.isPending ||
    contract.isPending ||
    fields.isPending ||
    factors.isPending;
  const schemaVersion = schema.data?.schema_version ?? null;
  const runtimeSchema = (schemaData as JsonSchema | undefined) ?? null;
  return useMemo(
    () => ({
      completionSource,
      hoverSource,
      loading,
      schemaVersion,
      schema: runtimeSchema,
    }),
    [completionSource, hoverSource, loading, schemaVersion, runtimeSchema],
  );
};
