import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

export const AppProvider = ({
  client,
  children,
}: {
  client: QueryClient;
  children: ReactNode;
}) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
