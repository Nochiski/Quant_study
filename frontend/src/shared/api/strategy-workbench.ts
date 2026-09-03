import { client } from "./generated/client.gen";
import {
  createStrategy,
  explainFactorGraph,
  getEquityCatalog,
  getFactorCatalog,
  getStrategyTemplate,
  previewFactorGraph,
  previewEquityData,
  previewEquityPanel,
  previewEquityUniverse,
  reviseStrategy,
  validateFactorGraph,
  validateStrategy,
} from "./generated/sdk.gen";
import type {
  DataStep,
  DatasetFieldProfile,
  FactorCatalog,
  FactorDefinition,
  FactorExplanation,
  FactorGraph,
  FactorGraphRequest,
  FactorGraphValidation,
  FactorPreview,
  FactorPreviewRequest,
  FactorSignal,
  FactorValidationIssue,
  GetEquityCatalogData,
  GetFactorCatalogData,
  NodeContract,
  ResearchCatalog,
  ResearchPanelCell,
  ResearchPanelPreview,
  ResearchPanelPreviewRequest,
  ResearchPanelQuery,
  ResearchPreview,
  SavedStrategy,
  StrategySpec,
  StrategyValidation,
  UniverseHistoryQuery,
  UniversePreview,
} from "./generated/types.gen";

export const configureStrategyWorkbenchApi = (baseUrl: string): void => {
  client.setConfig({ baseUrl });
};

configureStrategyWorkbenchApi(
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
);

const requireData = <T>(data: T | undefined, context: string): T => {
  if (data === undefined) {
    throw new Error(`API response did not contain data: ${context}`);
  }
  return data;
};

export const strategyWorkbenchApi = {
  async getTemplate(): Promise<StrategySpec> {
    const response = await getStrategyTemplate();
    return requireData(response.data, "getStrategyTemplate");
  },

  async getEquityCatalog(
    query: EquityCatalogQuery = {},
  ): Promise<ResearchCatalog> {
    const response = await getEquityCatalog({ query });
    return requireData(response.data, "getEquityCatalog");
  },

  async previewUniverse(query: UniverseHistoryQuery): Promise<UniversePreview> {
    const response = await previewEquityUniverse({ body: query });
    return requireData(response.data, "previewEquityUniverse");
  },

  async previewPanel(
    request: ResearchPanelPreviewRequest,
  ): Promise<ResearchPanelPreview> {
    const response = await previewEquityPanel({ body: request });
    return requireData(response.data, "previewEquityPanel");
  },

  async previewEquity(
    query: ResearchPanelQuery,
    venue = "XKRX",
  ): Promise<ResearchPreview> {
    const response = await previewEquityData({ body: query, query: { venue } });
    return requireData(response.data, "previewEquityData");
  },

  async getFactorCatalog(
    query: FactorCatalogQuery = {},
  ): Promise<FactorCatalog> {
    const response = await getFactorCatalog({ query });
    return requireData(response.data, "getFactorCatalog");
  },

  async validateFactorGraph(
    request: FactorGraphRequest,
  ): Promise<FactorGraphValidation> {
    const response = await validateFactorGraph({ body: request });
    return requireData(response.data, "validateFactorGraph");
  },

  async explainFactorGraph(
    request: FactorGraphRequest,
  ): Promise<FactorExplanation> {
    const response = await explainFactorGraph({ body: request });
    return requireData(response.data, "explainFactorGraph");
  },

  async previewFactorGraph(
    request: FactorPreviewRequest,
  ): Promise<FactorPreview> {
    const response = await previewFactorGraph({ body: request });
    return requireData(response.data, "previewFactorGraph");
  },

  async validate(spec: StrategySpec): Promise<StrategyValidation> {
    const response = await validateStrategy({ body: spec });
    return requireData(response.data, "validateStrategy");
  },

  async create(spec: StrategySpec): Promise<SavedStrategy> {
    const response = await createStrategy({ body: spec });
    return requireData(response.data, "createStrategy");
  },

  async revise(
    identity: { strategyId: string; revision: number },
    spec: StrategySpec,
  ): Promise<SavedStrategy> {
    const response = await reviseStrategy({
      body: {
        expected_revision: identity.revision,
        spec,
      },
      path: { strategy_id: identity.strategyId },
    });
    return requireData(response.data, "reviseStrategy");
  },
};

export type EquityCatalogQuery = NonNullable<GetEquityCatalogData["query"]>;
export type FactorCatalogQuery = NonNullable<GetFactorCatalogData["query"]>;

export type {
  DataStep,
  DatasetFieldProfile,
  FactorCatalog,
  FactorDefinition,
  FactorExplanation,
  FactorGraph,
  FactorGraphRequest,
  FactorGraphValidation,
  FactorPreview,
  FactorPreviewRequest,
  FactorSignal,
  FactorValidationIssue,
  NodeContract,
  ResearchCatalog,
  ResearchPanelCell,
  ResearchPanelPreview,
  ResearchPanelPreviewRequest,
  ResearchPanelQuery,
  ResearchPreview,
  SavedStrategy,
  StrategySpec,
  StrategyValidation,
  UniverseHistoryQuery,
  UniversePreview,
};
