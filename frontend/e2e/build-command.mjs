/**
 * 브라우저 번들을 만드는 명령과 그 자식에게만 주는 환경 변수.
 *
 * 러너와 테스트가 같은 값을 보도록 부작용 없는 모듈에 둔다 — 러너 자체는 최상위 await 로 빌드를
 * 돌리고 잠금을 잡으므로 테스트가 import 할 수 없다. 소스 텍스트를 정규식으로 보던 단언이
 * Prettier 서식에 깨지던 것을 이 상수로 대신한다(2차 리뷰 P3-5).
 */

import { backendOrigin } from "./ports.mjs";

export const BUILD_ARGS = ["run", "build"];

/**
 * 빌드 자식에게만 주는 환경 변수.
 *
 * backend 주소는 번들에 **빌드 때 박히므로** 포트를 옮겼으면 이 빌드가 그 주소를 알아야 한다.
 * 그렇다고 `vite.config.ts` 가 주변 환경의 `PW_*` 를 읽게 하면 같은 설정을 쓰는 vitest·dev 까지
 * 끌려가 MSW 핸들러 주소와 어긋난다(1차 리뷰 DEFECT-P105-003). 그래서 값을 만드는 곳을 여기
 * 하나로 두고, 러너가 이 결과를 빌드 자식에게만 넘긴다 — Playwright 자식은 받지 않는다.
 *
 * 이미 `VITE_API_BASE_URL` 을 준 사람이 있으면 그 뜻을 덮지 않는다.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {{ VITE_API_BASE_URL: string }}
 */
export const buildEnv = (env = process.env) => ({
  VITE_API_BASE_URL: env.VITE_API_BASE_URL ?? backendOrigin(env),
});
