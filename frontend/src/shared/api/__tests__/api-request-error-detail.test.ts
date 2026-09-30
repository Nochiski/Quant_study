import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiRequestError, strategyWorkbenchApi } from "../strategy-workbench";

const API = "http://localhost:8000";

const VALIDATION_422 = {
  detail: [{ type: "missing", loc: ["body", "source"], msg: "Field required" }],
};

const server = setupServer(
  http.post(`${API}/api/v1/strategy-documents`, () =>
    HttpResponse.json(VALIDATION_422, { status: 422 }),
  ),
  http.post(`${API}/api/v1/strategy-documents/upgrade`, () =>
    HttpResponse.json(VALIDATION_422, { status: 422 }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const rejection = async (call: Promise<unknown>): Promise<ApiRequestError> => {
  try {
    await call;
  } catch (error) {
    if (error instanceof ApiRequestError) return error;
    throw error;
  }
  throw new Error("expected the request to be rejected");
};

// #268 리뷰 P3-4: `detail` 은 저장 상태 줄(409·422)이 본문으로 쓴다. FastAPI 기본 배열 422의 진단 요약이 여기에
// 들어가면 영문 경로(`source: Field required`)가 본문으로 샌다. 요약은 접힌 진단 상세용 `diagnostic` 에만
// 싣는다(업그레이드·추적 실패는 둘 다 접힌 서버 사유로만 보인다, #270).
describe("ApiRequestError detail vs diagnostic", () => {
  it.each([
    [
      "save",
      () =>
        strategyWorkbenchApi.createStrategyDocument({
          format: "yaml",
          source: "",
        }),
    ],
    [
      "upgrade",
      () =>
        strategyWorkbenchApi.upgradeStrategyDocument({
          format: "yaml",
          source: "",
        } as Parameters<
          typeof strategyWorkbenchApi.upgradeStrategyDocument
        >[0]),
    ],
  ])(
    "keeps a %s validation summary out of the body detail",
    async (_, call) => {
      const error = await rejection(call());
      expect(error.status).toBe(422);
      expect(error.detail).toBeUndefined();
      expect(error.diagnostic).toBe("source: Field required");
    },
  );
});
