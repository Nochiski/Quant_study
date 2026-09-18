import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

/** 워커 경합에 민감한 route 테스트 — 별도 프로젝트로 뒤에 혼자 돈다(아래 `projects`). */
const ROUTE_TESTS = "src/app/__tests__/document-routes.test.tsx";

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
    // route 테스트(page 트리 전체 + CodeMirror, 단독 65초)는 다른 53파일과 워커를 다투면 비동기 예산을 넘겨
    // 부하 flake가 났다(Phase 5 backlog 15·19). 별도 프로젝트로 나누고 `groupOrder`를 뒤에 두어 나머지가 끝난
    // 뒤 혼자 돈다 — 파일 안 timeout이나 예산 상향이 아니라 경합 자체를 없앤다.
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          exclude: [...configDefaults.exclude, "e2e/**", ROUTE_TESTS],
        },
      },
      {
        extends: true,
        test: {
          name: "routes",
          include: [ROUTE_TESTS],
          sequence: { groupOrder: 1 },
        },
      },
    ],
  },
});
