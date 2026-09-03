import { client } from "./generated/client.gen";
import {
  createStrategy,
  getEquityCatalog,
  getStrategyTemplate,
  previewEquityData,
  reviseStrategy,
  validateStrategy,
} from "./generated/sdk.gen";
import type {
  ResearchCatalog,
  ResearchPanelQuery,
  ResearchPreview,
  SavedStrategy,
  StrategySpec,
  StrategyValidation,
} from "./generated/types.gen";

client.setConfig({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
});

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

  async getEquityCatalog(): Promise<ResearchCatalog> {
    const response = await getEquityCatalog();
    return requireData(response.data, "getEquityCatalog");
  },

  async previewEquity(
    query: ResearchPanelQuery,
    venue = "XKRX",
  ): Promise<ResearchPreview> {
    const response = await previewEquityData({ body: query, query: { venue } });
    return requireData(response.data, "previewEquityData");
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

export type {
  ResearchCatalog,
  ResearchPanelQuery,
  ResearchPreview,
  SavedStrategy,
  StrategySpec,
  StrategyValidation,
};
