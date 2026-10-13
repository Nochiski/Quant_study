import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse, delay } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import type { StrategySpec } from "../../../shared/api";
import {
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { useDraftSemanticDiff } from "../model/use-draft-semantic-diff";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
const state = (source = "title: new", version = 1): DocumentState => ({
  ...initialDocumentState("yaml", source),
  strategyId: "s1",
  baseRevision: 2,
  baseSpecHash: "base",
  sourceVersion: version,
  compiledVersion: version,
  compiled: {
    spec: {} as StrategySpec,
    canonicalJson: "{}",
    specHash: source,
    schemaVersion: "1.2",
    sourceHash: source,
    diagnostics: [],
  },
});
const response = (source: string) => ({
  strategy_id: "s1",
  base_revision: 2,
  base_spec_hash: "base",
  target_spec_hash: source,
  source_hash: source,
  changes: [
    { pointer: "/title", kind: "changed", before: "old", after: source },
  ],
});
const View = ({ document }: { document: DocumentState }) => {
  const diff = useDraftSemanticDiff(document, true);
  return (
    <p role="status">
      {diff.status === "ready" ? JSON.stringify(diff.changes) : diff.status}
    </p>
  );
};
const mount = (document: DocumentState) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<View document={document} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
};
it("backend 변경 행을 그대로 표시하고 늦은 옛 원문 응답은 현재 문서를 덮지 않는다", async () => {
  let started!: () => void;
  const firstStarted = new Promise<void>((resolve) => { started = resolve; });
  server.use(
    http.post(
      "http://localhost:8000/api/v1/strategies/s1/draft-diff",
      async ({ request }) => {
        const body = (await request.json()) as {
          source: string;
          base_revision: number;
          format: string;
        };
        expect(body.base_revision).toBe(2);
        expect(body.format).toBe("yaml");
        if (body.source === "old request") { started(); await delay(80); }
        return HttpResponse.json(response(body.source));
      },
    ),
  );
  const view = mount(state("old request"));
  await firstStarted;
  view.rerender(<View document={state("new request", 2)} />);
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(
      '"after":"new request"',
    ),
  );
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(screen.getByRole("status")).not.toHaveTextContent("old request");
});
it.each(["source_hash", "base_spec_hash", "target_spec_hash"])(
  "%s 불일치는 의미 결과로 표시하지 않는다",
  async (key) => {
    server.use(
      http.post("http://localhost:8000/api/v1/strategies/s1/draft-diff", () =>
        HttpResponse.json({ ...response("title: new"), [key]: "wrong" }),
      ),
    );
    mount(state());
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("incoherent"),
    );
  },
);
it("일시 실패는 다음 편집의 새 요청으로 회복한다", async () => {
  let calls = 0;
  server.use(
    http.post(
      "http://localhost:8000/api/v1/strategies/s1/draft-diff",
      async ({ request }) => {
        calls += 1;
        const body = (await request.json()) as { source: string };
        return calls === 1
          ? new HttpResponse(null, { status: 503 })
          : HttpResponse.json(response(body.source));
      },
    ),
  );
  const view = mount(state());
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("unavailable"),
  );
  view.rerender(<View document={state("edited", 2)} />);
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent('"after":"edited"'),
  );
});

it("같은 원문 재검증의 새 compile hash는 새 서버 비교를 요청한다", async () => {
  let calls = 0;
  server.use(http.post("http://localhost:8000/api/v1/strategies/s1/draft-diff", () => {
    calls += 1;
    return HttpResponse.json({ ...response("title: new"), target_spec_hash: calls === 1 ? "title: new" : "new-compile" });
  }));
  const before = state();
  const view = mount(before);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent('"pointer"'));
  view.rerender(<View document={{ ...before, compiled: { ...before.compiled!, specHash: "new-compile" } }} />);
  await waitFor(() => expect(calls).toBe(2));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent('"pointer"'));
});
it("같은 원문이라도 새 문서 epoch·저장 기준으로 전환하면 새 기준을 조회한다", async () => {
  const bases: number[] = [];
  server.use(http.post("http://localhost:8000/api/v1/strategies/s1/draft-diff", async ({ request }) => {
    const body = await request.json() as { base_revision: number };
    bases.push(body.base_revision);
    return HttpResponse.json({ ...response("title: new"), base_revision: body.base_revision, base_spec_hash: body.base_revision === 2 ? "base" : "new-base" });
  }));
  const view = mount(state());
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent('"pointer"'));
  view.rerender(<View document={{ ...state(), documentEpoch: 2, baseRevision: 3, baseSpecHash: "new-base" }} />);
  await waitFor(() => expect(bases).toEqual([2, 3]));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent('"pointer"'));
});
