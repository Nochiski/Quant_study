import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  ApiRequestError,
  failureReason,
  strategyWorkbenchApi,
} from "../strategy-workbench";

const API = "http://localhost:8000";
const REASON = "그래프를 설명할 수 없습니다 — node_id='ratio'";

const rejected = () =>
  HttpResponse.json(
    { detail: { code: "factor.graph.invalid", message: REASON } },
    { status: 422 },
  );

const server = setupServer(
  http.get(`${API}/api/v1/equity/catalog`, rejected),
  http.get(`${API}/api/v1/factors/catalog`, rejected),
  http.post(`${API}/api/v1/factors/explain`, rejected),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// #357 C-P3-15: 카탈로그·실행 계획 설명도 다른 호출처럼 `unwrap` 을 거친다. 건너뛰면 개발자 문장("API
// response did not contain data: …")이 접힌 서버 사유 칸에 뜨고 서버가 보낸 사유는 버려진다(#270).
describe("catalog and explain failures", () => {
  it.each([
    ["getEquityCatalog", () => strategyWorkbenchApi.getEquityCatalog()],
    ["getFactorCatalog", () => strategyWorkbenchApi.getFactorCatalog()],
    [
      "explainFactorGraph",
      () =>
        strategyWorkbenchApi.explainFactorGraph(
          {} as Parameters<typeof strategyWorkbenchApi.explainFactorGraph>[0],
        ),
    ],
  ])("%s carries the server reason", async (_name, call) => {
    const error = await call().then(
      () => null,
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(failureReason(error)).toBe(REASON);
  });
});
