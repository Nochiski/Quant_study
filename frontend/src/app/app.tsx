import { StrategyBuilderPage } from "../pages/strategy-builder";
import { AppProvider } from "./providers/app-provider";
import "./styles/global.css";

export const App = () => (
  <AppProvider>
    <StrategyBuilderPage />
  </AppProvider>
);
