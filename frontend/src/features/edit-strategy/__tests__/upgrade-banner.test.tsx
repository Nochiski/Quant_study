import { redo, undo } from "@codemirror/commands";
import { EditorView } from "@codemirror/view";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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
let upgradeMode: "ok" | "drift" | "network" | "array-422" | "no-environment" =
  "ok";
/** 업그레이드 응답의 옛 문서 실행 설정(P2-09 `environment`). */
const OLD_ENVIRONMENT = {
  market: "KRX",
  frequency: "daily",
  start: "2021-01-01",
  end: "2026-08-31",
  universe_id: "krx.common-stock",
  timing: "next_open",
  participation_rate: 0.1,
  fee_bps: 15,
  slippage_bps: 10,
  missing: "drop",
} as const;
const WARNING = {
  code: "strategy_document.upgrade_weighting_rule_changed",
  pointer: "/portfolio/weighting",
  message:
    "점수 비례 비중은 이제 기준점 위의 몫으로 나눕니다: weighting='factor_score'",
} as const;
const appliedEnvironments: unknown[] = [];

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
    if (upgradeMode === "array-422") {
      // 코드 없는 FastAPI 기본 422(배열 detail).
      return HttpResponse.json(
        {
          detail: [
            { type: "missing", loc: ["body", "format"], msg: "Field required" },
          ],
        },
        { status: 422 },
      );
    }
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
      environment: upgradeMode === "no-environment" ? null : OLD_ENVIRONMENT,
      warnings:
        upgradeMode === "no-environment"
          ? [
              {
                code: "strategy_document.upgrade_environment_unavailable",
                pointer: "",
                message: "옛 문서의 기간을 읽지 못했습니다: start=None",
              },
            ]
          : [WARNING],
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  upgradeRequests.length = 0;
  appliedEnvironments.length = 0;
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
      <button
        type="button"
        onClick={() =>
          dispatch({
            type: "saved",
            strategyId: "frozen-doc",
            revision: 2,
            specHash: "h".repeat(64),
            canonicalJson: null,
            source: state.source,
            documentEpoch: state.documentEpoch,
            sourceVersion: state.sourceVersion,
          })
        }
      >
        Commit revision
      </button>
      <UpgradeBanner
        upgrade={upgrade}
        onApplyEnvironment={(environment) =>
          appliedEnvironments.push(environment)
        }
      />
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
  screen.getByRole("button", { name: "현재 버전으로 업그레이드" });

describe("retired schema upgrade banner", () => {
  it("rewrites the editor text through the backend and leaves the document dirty with one undo step", async () => {
    const view = await mount();
    expect(await screen.findByText("이전 schema 문서")).toBeVisible();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    expect(screen.getByTestId("dirty")).toHaveTextContent("false");

    fireEvent.click(upgradeButton());

    await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));
    expect(upgradeRequests).toEqual([{ source: LEGACY, format: "yaml" }]);
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");
    expect(
      await screen.findByText(
        "현재 버전으로 다시 썼습니다. 검토 후 새 revision으로 저장하세요.",
      ),
    ).toBeVisible();
    // 현재 버전 텍스트가 다시 컴파일되어 배너의 업그레이드 제안이 사라진다.
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent(
        "semantically-valid",
      ),
    );
    expect(
      screen.queryByRole("button", { name: "현재 버전으로 업그레이드" }),
    ).toBeNull();

    act(() => {
      undo(view);
    });
    expect(view.state.doc.toString()).toBe(LEGACY);
  });

  it("keeps the upgrade as its own undo step even when the user types right after it", async () => {
    // P2-02 리뷰 P1-002: 500ms 안에 친 글자와 업그레이드가 한 undo로 묶이면 안 된다.
    const view = await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    fireEvent.click(upgradeButton());
    await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));

    act(() => {
      view.dispatch({
        changes: { from: view.state.doc.length, insert: "z" },
        userEvent: "input.type",
      });
    });
    expect(view.state.doc.toString()).toBe(`${UPGRADED}z`);
    act(() => {
      undo(view);
    });
    expect(view.state.doc.toString()).toBe(UPGRADED);
    act(() => {
      undo(view);
    });
    expect(view.state.doc.toString()).toBe(LEGACY);
  });

  it("brings the run-settings fill back when an undone upgrade is redone", async () => {
    // #267 DEFECT-1: 상태가 버전 번호에 묶이면 다시 실행한 글이 새 버전이라 채우기가 사라졌다.
    const view = await mount();
    const fill = () =>
      screen.queryByRole("button", { name: "실행 설정에 채우기" });
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    fireEvent.click(upgradeButton());
    await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));
    await waitFor(() => expect(fill()).not.toBeNull());

    act(() => {
      undo(view);
    });
    expect(view.state.doc.toString()).toBe(LEGACY);
    await waitFor(() => expect(fill()).toBeNull());
    // 되돌린 옛 글에는 업그레이드 제안이 다시 뜬다.
    await waitFor(() => expect(upgradeButton()).toBeEnabled());

    act(() => {
      redo(view);
    });
    expect(view.state.doc.toString()).toBe(UPGRADED);
    await waitFor(() => expect(fill()).not.toBeNull());
    expect(upgradeRequests).toHaveLength(1);
  });

  it("drops the applied notice once the text is saved as a new revision", async () => {
    // P2-02 리뷰 P2-003: 상태는 savedVersion에도 묶인다.
    const view = await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    fireEvent.click(upgradeButton());
    await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));
    expect(
      await screen.findByText(
        "현재 버전으로 다시 썼습니다. 검토 후 새 revision으로 저장하세요.",
      ),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Commit revision" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "이전 schema 문서" }),
      ).toBeNull(),
    );
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
    expect(alert).not.toHaveTextContent("API request failed");
    expect(view.state.doc.toString()).toBe(LEGACY);
    expect(upgradeRequests).toHaveLength(1);
  });

  it("keeps a code-less refusal out of the body and folds the server reason away (#270)", async () => {
    upgradeMode = "array-422";
    await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());

    fireEvent.click(upgradeButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "업그레이드 요청이 실패했습니다. 원문은 그대로입니다.",
    );
    expect(alert).not.toHaveTextContent("API request failed");
    // 서버 원문은 접힌 "서버 사유" 안에만 있다.
    const reason = within(alert).getByText("서버 사유").closest("details");
    expect(reason).not.toBeNull();
    expect(reason).toHaveTextContent("format: Field required");
  });

  it("only suggests saving a new revision for a generated frozen row", async () => {
    await mount({ revision: 1, generated: true, requires_upgrade: true });
    expect(
      await screen.findByText(
        "이전 schema로 동결된 revision입니다. 생성된 문서는 이미 현재 버전이므로 편집 후 새 revision으로 저장하세요.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "현재 버전으로 업그레이드" }),
    ).toBeNull();
    expect(upgradeRequests).toEqual([]);
  });

  it("shows the upgrade warnings and fills the run settings only when the user asks", async () => {
    await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    fireEvent.click(upgradeButton());

    const banner = await screen.findByRole("region", {
      name: "이전 schema 문서",
    });
    // warning 은 코드별 제목(P3-01)과 backend 가 한글로 완성한 문장을 함께 보인다.
    expect(banner).toHaveTextContent(
      "점수 비례 비중의 계산 규칙이 바뀌었습니다",
    );
    expect(banner).toHaveTextContent(WARNING.message);
    expect(banner).toHaveTextContent(
      "옛 문서에 있던 실행 설정: 2021-01-01 → 2026-08-31 · krx.common-stock",
    );
    // 채우기 전에 저장하면 옛 값을 되찾을 길이 없다는 것을 미리 알린다(#267 DEFECT-3).
    const unfilled =
      "채우지 않고 저장하거나 이 화면을 떠나면 이 실행 설정은 다시 볼 수 없습니다.";
    expect(banner).toHaveTextContent(unfilled);
    expect(appliedEnvironments).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "실행 설정에 채우기" }));

    expect(appliedEnvironments).toEqual([OLD_ENVIRONMENT]);
    expect(banner).toHaveTextContent("옛 문서의 실행 설정을 채웠습니다.");
    expect(banner).not.toHaveTextContent(unfilled);
    expect(
      screen.queryByRole("button", { name: "실행 설정에 채우기" }),
    ).toBeNull();
  });

  it("does not fill defaults when the old document's run settings could not be carried over", async () => {
    upgradeMode = "no-environment";
    await mount();
    await waitFor(() => expect(upgradeButton()).toBeEnabled());
    fireEvent.click(upgradeButton());

    const banner = await screen.findByRole("region", {
      name: "이전 schema 문서",
    });
    await waitFor(() =>
      expect(banner).toHaveTextContent(
        "옛 문서의 실행 설정을 옮기지 못했습니다",
      ),
    );
    expect(banner).toHaveTextContent("옛 문서의 기간을 읽지 못했습니다");
    expect(banner).toHaveTextContent(
      "옛 문서의 실행 설정을 옮기지 못해 실행 설정을 채우지 않았습니다.",
    );
    expect(
      screen.queryByRole("button", { name: "실행 설정에 채우기" }),
    ).toBeNull();
    expect(appliedEnvironments).toEqual([]);
  });
});
