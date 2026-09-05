import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist-cm6lite",
    rollupOptions: { input: { cm6lite: "src/cm6lite.js" } },
    chunkSizeWarningLimit: 10000,
  },
});
