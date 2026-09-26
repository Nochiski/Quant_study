import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

// 이 파일은 `PW_*` 포트 변수를 읽지 않는다. vitest·dev serve 가 같은 설정을 읽으므로, 셸에
// `PW_BACKEND_PORT` 가 export 돼 있다는 이유만으로 SDK 기본 주소가 바뀌면 MSW 핸들러가 등록된
// 주소와 어긋나 단위 테스트가 통째로 깨진다(1차 리뷰 DEFECT-P105-003, 실측 5 failed).
// E2E 빌드에 필요한 `VITE_API_BASE_URL` 은 `e2e/run-playwright.mjs` 가 **빌드 자식 프로세스에만**
// 명시적으로 넘긴다 — 주변 환경이 아니라 그 한 번의 빌드에만 적용된다.

// Browser scenarios have their own real-server Playwright lifecycle and must never be
// collected into the jsdom unit/integration runner. 제외 목록의 정본은 이 상수 하나다.
// Playwright spec 만 뺀다. `e2e/` 의 잠금·포트 유틸은 단위 테스트가 있는 평범한 모듈이라
// vitest 가 수집해야 한다(`e2e/lock.test.mjs`).
const EXCLUDE = [...configDefaults.exclude, "e2e/**/*.spec.ts"];
/**
 * 워커 경합에 민감한 테스트 파일 — `routes` 프로젝트로 나머지가 끝난 뒤 혼자 돈다(아래 `projects`). 단독 실행이
 * 1분을 넘기는 page 트리 테스트가 새로 생기면 여기에 더한다.
 */
const ISOLATED_TESTS = [
  "src/app/__tests__/document-routes.test.tsx",
  // 입력 지연 p95 예산(16ms)을 재는 테스트 — 53파일 병렬에서는 예산이 아니라 이웃 워커를 잰다(#150 재검토 관측).
  "src/shared/ui/code-editor/__tests__/code-editor.performance.test.tsx",
];

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
    exclude: EXCLUDE,
    // route 테스트(page 트리 전체 + CodeMirror, 단독 65초)는 다른 53파일과 워커를 다투면 비동기 예산을 넘겨
    // 부하 flake가 났다(Phase 5 backlog 15·19). 별도 프로젝트로 나누고 `groupOrder`를 뒤에 두어 나머지가 끝난
    // 뒤 혼자 돈다 — 경합을 없애는 대신 전체 시간이 직렬화된 만큼 늘어난다. cold 실행(vite 캐시 없음)의
    // 잔여 flake는 backlog 20.
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          exclude: [...EXCLUDE, ...ISOLATED_TESTS],
        },
      },
      {
        extends: true,
        test: {
          name: "routes",
          include: ISOLATED_TESTS,
          sequence: { groupOrder: 1 },
        },
      },
    ],
  },
});
