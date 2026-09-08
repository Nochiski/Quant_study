import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ThemePreferenceProvider } from "../../shared/lib/theme";

export const AppProvider = ({
  client,
  children,
}: {
  client: QueryClient;
  children: ReactNode;
}) => (
  <ThemePreferenceProvider>
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  </ThemePreferenceProvider>
);
