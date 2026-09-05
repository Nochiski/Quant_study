/** Feature flags read once at startup; tests inject the value instead of the env. */
export const operationsEnabledFromEnv = () =>
  import.meta.env.VITE_ENABLE_OPERATIONS === "true";
