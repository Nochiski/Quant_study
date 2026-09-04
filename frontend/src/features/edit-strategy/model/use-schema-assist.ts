import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

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
import { buildCompletionSource, buildHoverSource } from "./schema-assist";
import type { JsonSchema } from "./schema-navigator";

export type SchemaAssist = {
  completionSource: EditorCompletionSource;
  hoverSource: EditorHoverSource;
  /** True while any of the schema, contract or catalogs is still loading. */
  loading: boolean;
};

/** One page holds every mock field/factor today; a larger catalog is paged by search (P6). */
const CATALOG_PAGE = { page_size: 100 } as const;

/**
 * Binds the schema-driven editor assistance to live data: runtime schema and contract (P1-05)
 * and the equity/factor catalogs the contract links to. The sources read the latest document
 * state through a ref, so the editor keeps one stable function per data change.
 */
export const useSchemaAssist = (state: DocumentState): SchemaAssist => {
  // A stable holder the sources read at call time (after commit), so the editor keeps one
  // function per data change instead of re-registering on every keystroke.
  const [latest] = useState(() => ({ state }));
  useEffect(() => {
    latest.state = state;
  }, [latest, state]);
  const getState = useCallback(() => latest.state, [latest]);
  const schema = useQuery(strategySchemaQuery());
  const contract = useQuery(strategyContractQuery());
  const fields = useDatasetCatalog(CATALOG_PAGE);
  const factors = useFactorCatalog(CATALOG_PAGE);

  const schemaData = schema.data?.schema;
  const contractRows = contract.data?.contract.fields;
  const fieldRows = fields.data?.fields;
  const factorRows = factors.data?.factors;
  return useMemo(() => {
    const deps = {
      schema: (schemaData as JsonSchema | undefined) ?? null,
      contract: contractRows ?? [],
      catalogs: { equityFields: fieldRows ?? [], factors: factorRows ?? [] },
      getState,
    };
    return {
      completionSource: buildCompletionSource(deps),
      hoverSource: buildHoverSource(deps),
      loading:
        schema.isPending ||
        contract.isPending ||
        fields.isPending ||
        factors.isPending,
    };
  }, [
    getState,
    schemaData,
    contractRows,
    fieldRows,
    factorRows,
    schema.isPending,
    contract.isPending,
    fields.isPending,
    factors.isPending,
  ]);
};
