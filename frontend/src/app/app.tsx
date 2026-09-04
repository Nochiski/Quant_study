import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, type RouterHistory } from "@tanstack/react-router";
import { useState } from "react";

import { operationsEnabledFromEnv } from "./config";
import { AppProvider } from "./providers/app-provider";
import { createQueryClient } from "./providers/query-client";
import { createAppRouter } from "./router";
import "./styles/global.css";

type AppProps = {
  history?: RouterHistory;
  queryClient?: QueryClient;
  operationsEnabled?: boolean;
};

/** Composition root: one QueryClient, one router, providers only (no page logic here). */
export const App = ({
  history,
  queryClient,
  operationsEnabled = operationsEnabledFromEnv(),
}: AppProps = {}) => {
  const [client] = useState(() => queryClient ?? createQueryClient());
  const [router] = useState(() =>
    createAppRouter({ queryClient: client, operationsEnabled }, history),
  );
  return (
    <AppProvider client={client}>
      <RouterProvider router={router} />
    </AppProvider>
  );
};
