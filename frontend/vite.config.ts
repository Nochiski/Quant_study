import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        // Keep framework/runtime code cacheable without absorbing the lazy CodeMirror graph.
        codeSplitting: {
          groups: [
            {
              name: "react-vendor",
              test: /node_modules[\\/](?:react|react-dom|scheduler)[\\/]/,
              priority: 20,
            },
            {
              name: "tanstack-vendor",
              test: /node_modules[\\/]@tanstack[\\/]/,
              priority: 20,
            },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
  },
  test: {
    // 케이스 timeout 15초: 비동기 대기 예산(test-setup.ts, 10초)보다 길어야 `waitFor` 실패가 timeout이 아니라
    // 단언 내용·DOM으로 보고된다(#149 재검토 관찰). 두 손잡이의 관계는 여기서만 정한다.
    testTimeout: 15_000,
    environment: "jsdom",
    setupFiles: "./src/shared/config/test-setup.ts",
    // Browser scenarios have their own real-server Playwright lifecycle and must never be
    // collected into the jsdom unit/integration runner.
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
