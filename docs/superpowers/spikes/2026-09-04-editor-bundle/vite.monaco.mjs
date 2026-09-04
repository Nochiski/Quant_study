import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist-monaco",
    rollupOptions: { input: { monaco: "src/monaco.js" } },
    chunkSizeWarningLimit: 10000,
  },
});
