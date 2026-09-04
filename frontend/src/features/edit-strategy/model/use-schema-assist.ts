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
import type {
  ContractInspectorSource,
  ContractResourceState,
} from "./contract-inspector";
import {
  buildCompletionSource,
  buildHoverSource,
  projectAssistMetadata,
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
  /** Same query-owned metadata, exposed intact for the read-only Contract Inspector. */
  inspectorSource: ContractInspectorSource;
};

/** One page holds every mock field/factor today; a larger catalog is paged by search (P6). */
const CATALOG_PAGE = { page_size: 100 } as const;

const resourceState = (
  pending: boolean,
  error: boolean,
  hasData: boolean,
): ContractResourceState =>
  hasData ? "ready" : pending ? "loading" : error ? "error" : "error";

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
  const contractData = contract.data?.contract;
  const assistMetadata = useMemo(
    () =>
      projectAssistMetadata(
        schema.data ?? null,
        contract.data ?? null,
        fields.data ?? null,
        factors.data ?? null,
      ),
    [schema.data, contract.data, fields.data, factors.data],
  );
  useEffect(() => {
    latest.current = {
      ...assistMetadata,
      getState: () => state,
    };
  }, [assistMetadata, state]);

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
  const inspectorSource = useMemo<ContractInspectorSource>(
    () => ({
      schema: schema.data ? schema.data : null,
      contract: contract.data ?? null,
      equityCatalog: fields.data ?? null,
      factorCatalog: factors.data ?? null,
      state: {
        schema: resourceState(
          schema.isPending,
          schema.isError,
          schema.data !== undefined,
        ),
        contract: resourceState(
          contract.isPending,
          contract.isError,
          contractData !== undefined,
        ),
        equityCatalog: resourceState(
          fields.isPending,
          fields.isError,
          fields.data !== undefined,
        ),
        factorCatalog: resourceState(
          factors.isPending,
          factors.isError,
          factors.data !== undefined,
        ),
      },
    }),
    [
      contract.isError,
      contract.isPending,
      contract.data,
      contractData,
      factors.data,
      factors.isError,
      factors.isPending,
      fields.data,
      fields.isError,
      fields.isPending,
      schema.data,
      schema.isError,
      schema.isPending,
    ],
  );
  return useMemo(
    () => ({
      completionSource,
      hoverSource,
      loading,
      schemaVersion,
      schema: runtimeSchema,
      inspectorSource,
    }),
    [
      completionSource,
      hoverSource,
      inspectorSource,
      loading,
      schemaVersion,
      runtimeSchema,
    ],
  );
};
