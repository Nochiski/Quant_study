import { undo } from "@codemirror/commands";
import { EditorView } from "@codemirror/view";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { useCompileDocument } from "../model/use-compile-document";
import { useStrategyDocument } from "../model/use-strategy-document";
import { useUpgradeDocument } from "../model/use-upgrade-document";
import { SourceEditor } from "../ui/source-editor";
import { UpgradeBanner } from "../ui/upgrade-banner";

const API = "http://localhost:8000";
const LEGACY =
  'schema_version: "1.0"\ntitle: 옛 문서\nfactors:\n  factors: []\n';
const UPGRADED = 'schema_version: "1.1"\ntitle: 옛 문서\nfactors: []\n';
const upgradeRequests: { source: string; format: string }[] = [];
let upgradeMode: "ok" | "drift" | "network" = "ok";

const compiledResponse = (source: string) => {
  const legacy = source.includes('schema_version: "1.0"');
  return {
    format: "yaml",
    source_hash: "s".repeat(64),
    schema_version: legacy ? null : "1.1",
    spec: legacy ? null : { title: "옛 문서" },
    canonical_json: legacy
      ? null
      : '{"schema_version":"1.1","title":"옛 문서"}',
    spec_hash: legacy ? null : "h".repeat(64),
    diagnostics: legacy
      ? [
          {
            code: "structure.unsupported_schema_version",
            kind: "structural",
            severity: "error",
            pointer: "/schema_version",
            message: "unsupported schema_version",
          },
        ]
      : [],
    echo: source,
  };
};

const server = setupServer(
  http.post(`${API}/api/v1/strategy-documents/compile`, async ({ request }) => {
    const body = (await request.json()) as { source: string };
    return HttpResponse.json(compiledResponse(body.source));
  }),
  http.post(`${API}/api/v1/strategy-documents/upgrade`, async ({ request }) => {
    const body = (await request.json()) as { source: string; format: string };
    upgradeRequests.push(body);
    if (upgradeMode === "network") return HttpResponse.error();
    if (upgradeMode === "drift") {
      return HttpResponse.json(
        {
          detail: {
            code: "strategy_document.upgrade_drift",
            message: "upgraded source drifted from the dict transform",
            pointer: "/factors",
          },
        },
        { status: 422 },
      );
    }
    return HttpResponse.json({
      format: "yaml",
      source: UPGRADED,
      source_hash: "u".repeat(64),
      compiled: compiledResponse(UPGRADED),
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  upgradeRequests.length = 0;
  upgradeMode = "ok";
});
afterAll(() => server.close());

const Harness = ({
  stored,
}: {
  stored: { revision: number; generated: boolean; requires_upgrade: boolean };
}) => {
  const [state, dispatch] = useStrategyDocument({
    kind: "revision",
    document: {
      strategy_id: "frozen-doc",
      revision: stored.revision,
      schema_version: "1.0",
      format: "yaml",
      source: stored.generated ? UPGRADED : LEGACY,
      source_hash: "b".repeat(64),
      spec: { title: "옛 문서" } as never,
      spec_hash: "2".repeat(64),
      origin: stored.generated ? "legacy_json" : "document",
      generated: stored.generated,
      requires_upgrade: stored.requires_upgrade,
      created_at: "2026-09-04T09:30:00+00:00",
    },
  });
  useCompileDocument(state, dispatch);
  const upgrade = useUpgradeDocument(state, stored);
  return (
    <>
      <output data-testid="dirty">{String(state.dirty)}</output>
      <output data-testid="phase">{state.phase}</output>
      <UpgradeBanner upgrade={upgrade} />
      <SourceEditor
        state={state}
        dispatch={dispatch}
        onEditorReady={upgrade.onEditorReady}
      />
    </>
  );
};

const mount = async (
  stored = { revision: 1, generated: false, requires_upgrade: true },
) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Harness stored={stored} />
    </QueryClientProvider>,
  );
  await screen.findByRole("textbox", { name: "편집기" });
  let view: EditorView | null = null;
  await waitFor(() => {
    const content = document.querySelector(".cm-content");
    view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
    expect(view).not.toBeNull();
  });
  return view as unknown as EditorView;
};

const upgradeButton = () =>
  screen.getByRole("button", { name: "1.1로 업그레이드" });

describe("schema 1.0 upgrade banner", () => {
  it("rewrites the editor text through the backend and leaves the document dirty with one undo step", async () => {
    const view = await mount();
    expect(await screen.findByText("schema 1.0 문서")).toBeVisible();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    expect(screen.getByTestId("dirty")).toHaveTextContent("false");

    fireEvent.click(upgradeButton());

    await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));
    expect(upgradeRequests).toEqual([{ source: LEGACY, format: "yaml" }]);
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");
    expect(
      await screen.findByText(
        "1.1로 다시 썼습니다. 검토 후 새 revision으로 저장하세요.",
      ),
    ).toBeVisible();
    // 1.1 텍스트가 다시 컴파일되어 배너의 업그레이드 제안이 사라진다.
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent(
        "semantically-valid",
      ),
    );
    expect(
      screen.queryByRole("button", { name: "1.1로 업그레이드" }),
    ).toBeNull();

    act(() => {
      undo(view);
    });
    expect(view.state.doc.toString()).toBe(LEGACY);
  });

  it("keeps the source untouched and names the backend refusal on 422 drift", async () => {
    upgradeMode = "drift";
    const view = await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());

    fireEvent.click(upgradeButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "업그레이드 결과가 변환 규칙과 어긋나 중단했습니다. 원문은 그대로입니다.",
    );
    expect(view.state.doc.toString()).toBe(LEGACY);
    expect(screen.getByTestId("dirty")).toHaveTextContent("false");
    expect(upgradeButton()).toBeEnabled();
  });

  it("reports a transport failure without changing the text", async () => {
    upgradeMode = "network";
    const view = await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());

    fireEvent.click(upgradeButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("업그레이드 요청이 실패했습니다");
    expect(view.state.doc.toString()).toBe(LEGACY);
    expect(upgradeRequests).toHaveLength(1);
  });

  it("only suggests saving a new revision for a generated frozen row", async () => {
    await mount({ revision: 1, generated: true, requires_upgrade: true });
    expect(
      await screen.findByText(
        "schema 1.0 동결 revision입니다. 생성된 문서는 이미 1.1이므로 편집 후 새 revision으로 저장하세요.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "1.1로 업그레이드" }),
    ).toBeNull();
    expect(upgradeRequests).toEqual([]);
  });
});
