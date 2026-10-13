import type {
  FactorCatalog,
  ResearchCatalog,
  StrategyDocumentContractResponse,
  StrategyDocumentSchema,
} from "../../../shared/api";

export type MetadataSource = {
  schema: StrategyDocumentSchema | null;
  contract: StrategyDocumentContractResponse | null;
  equityCatalog?: ResearchCatalog | null;
  factorCatalog?: FactorCatalog | null;
};

type Coherence = "ready" | "unavailable" | "incompatible";
const status = (available: boolean, matches: boolean): Coherence =>
  !available ? "unavailable" : matches ? "ready" : "incompatible";

/** 계약과 카탈로그 판본의 일치 여부를 보조 입력·계약 패널·실행 계획이 함께 읽는다. */
export const metadataCoherence = ({ schema, contract, equityCatalog, factorCatalog }: MetadataSource) => {
  const schemaContract = status(
    schema !== null && contract !== null,
    schema?.schema_hash === contract?.contract.schema_hash &&
      schema?.schema_version === contract?.contract.schema_version,
  );
  const baseAvailable = schema !== null && contract !== null;
  return {
    schemaContract,
    dataset: status(
      baseAvailable && equityCatalog != null,
      schemaContract === "ready" && equityCatalog?.snapshot.snapshot_id === contract?.contract.dataset_snapshot_id,
    ),
    factorRegistry: status(
      baseAvailable && factorCatalog != null,
      schemaContract === "ready" && factorCatalog?.registry_version === contract?.contract.factor_registry_version,
    ),
  };
};
