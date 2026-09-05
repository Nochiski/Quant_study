import { defineConfig } from "astro/config";

export default defineConfig({
  base: "/app",
  outDir: "../result/html/app",
  server: {
    host: "127.0.0.1",
    port: 4321
  }
});
