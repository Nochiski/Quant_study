import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { useDatasetCatalog } from "../../../entities/dataset";
import { useFactorCatalog } from "../../../entities/factor";
import { useCommittedRef } from "../../../shared/lib/react";
import {
  strategyContractQuery,
  strategyOperatorsQuery,
  strategySchemaQuery,
} from "../../../entities/strategy";
import type {
  EditorCompletionSource,
  EditorHoverSource,
} from "../../../shared/ui/code-editor";
import type { SnippetCatalogSource } from "./canonical-snippets";
import type { OperatorCatalogState } from "./operator-palette";
import type { DocumentState } from "./document-state";
import type {
  ContractInspectorSource,
  ContractResourceState,
} from "./contract-inspector";
import {
  buildCompletionSource,
  buildHoverSource,
  factorCatalogCoherence,
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
  /**
   * 연산자 카탈로그(P1-03, spec D8). Graph 팔레트가 읽는다. `loading`은 편집기 assist의 `loading`과
   * 분리한다 — 카탈로그가 늦어도 편집기 완성·hover는 기다릴 이유가 없다.
   */
  operators: OperatorCatalogState;
  /** Runtime-schema projection plus only the factor catalog pinned to that contract version. */
  snippetSource: SnippetCatalogSource;
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
  const operatorCatalog = useQuery(strategyOperatorsQuery());
  const fields = useDatasetCatalog(CATALOG_PAGE);
  const factors = useFactorCatalog(CATALOG_PAGE);

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
  // 편집기가 한 번 등록해 두고 부르는 두 소스는 호출 시점의 문서 상태·메타데이터를 ref로 읽는다. commit과
  // 같은 시점에 비춘다 — 타이핑이 곧 completion을 부르므로 passive effect 거울이면 한 편집 전 상태로
  // 후보가 계산됐다(backlog 21, `.claude/rules/frontend-react-effects.md`).
  const deps = useMemo<AssistDeps>(
    () => ({ ...assistMetadata, getState: () => state }),
    [assistMetadata, state],
  );
  const latest = useCommittedRef(deps);

  const completionSource = useCallback<EditorCompletionSource>(
    (context) => buildCompletionSource(latest.current)(context),
    [latest],
  );
  const hoverSource = useCallback<EditorHoverSource>(
    (offset) => buildHoverSource(latest.current)(offset),
    [latest],
  );
  const loading =
    schema.isPending ||
    contract.isPending ||
    fields.isPending ||
    factors.isPending;
  const schemaVersion = schema.data?.schema_version ?? null;
  const runtimeSchema = (schemaData as JsonSchema | undefined) ?? null;
  const snippetCoherence = factorCatalogCoherence(
    schema.data ?? null,
    contract.data ?? null,
    factors.data ?? null,
  );
  const snippetSource = useMemo<SnippetCatalogSource>(
    () => ({
      schema: assistMetadata.schema,
      factors: assistMetadata.catalogs.factors,
      status:
        schema.isError || contract.isError || factors.isError
          ? "unavailable"
          : schema.isPending || contract.isPending || factors.isPending
            ? "loading"
            : snippetCoherence,
    }),
    [
      assistMetadata.catalogs.factors,
      assistMetadata.schema,
      contract.isPending,
      contract.isError,
      factors.isPending,
      factors.isError,
      schema.isPending,
      schema.isError,
      snippetCoherence,
    ],
  );
  const operators = useMemo<OperatorCatalogState>(
    () =>
      operatorCatalog.data
        ? { status: "ready", definitions: operatorCatalog.data.operators }
        : operatorCatalog.isPending
          ? { status: "loading" }
          : { status: "unavailable" },
    [operatorCatalog.data, operatorCatalog.isPending],
  );
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
      operators,
      snippetSource,
    }),
    [
      completionSource,
      hoverSource,
      inspectorSource,
      loading,
      operators,
      schemaVersion,
      snippetSource,
      runtimeSchema,
    ],
  );
};
