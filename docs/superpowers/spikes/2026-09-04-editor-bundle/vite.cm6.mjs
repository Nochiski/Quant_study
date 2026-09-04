import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist-cm6",
    rollupOptions: { input: { cm6: "src/cm6.js" } },
    chunkSizeWarningLimit: 10000,
  },
});
