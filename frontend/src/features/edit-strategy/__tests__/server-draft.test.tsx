import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useReducer } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiRequestError,
  strategyWorkbenchApi,
  type SaveStrategyDraftRequest,
  type StrategyDraft,
} from "../../../shared/api";
import { t } from "../../../shared/config";
import { documentReducer, initialDocumentState } from "../model/document-state";
import { useServerDraft } from "../model/use-server-draft";
import { ServerDraftBanner } from "../ui/server-draft-banner";

const DRAFT_ID = "draft-0123456789abcdef0123456789abcdef";
const BASE = 'schema_version: "1.0"\ntitle: base\n';
const SPEC_HASH = "a".repeat(64);

const remoteDraft = (source: string, version = 1): StrategyDraft => ({
  draft_id: DRAFT_ID,
  version,
  source,
  format: "yaml",
  source_hash: "b".repeat(64),
  schema_version: "1.0",
  updated_at: `2026-09-05T00:00:0${version}Z`,
  strategy_id: "s1",
  base_revision: 1,
  base_spec_hash: SPEC_HASH,
});

const initial = {
  ...initialDocumentState("yaml", BASE),
  strategyId: "s1",
  baseRevision: 1,
  baseSpecHash: SPEC_HASH,
  savedSource: BASE,
  savedVersion: 0,
  phase: "saved" as const,
};

const Harness = () => {
  const [state, dispatch] = useReducer(documentReducer, initial);
  const sync = useServerDraft(state, dispatch, {
    draftId: DRAFT_ID,
    schemaVersion: "1.0",
    delayMs: 5,
  });
  return (
    <>
      <label htmlFor="source">Source</label>
      <textarea
        id="source"
        value={state.source}
        onChange={(event) =>
          dispatch({ type: "edit", source: event.target.value })
        }
      />
      <button
        type="button"
        onClick={() => dispatch({ type: "edit", source: "title: [broken\r\n" })}
      >
        Inject exact invalid source
      </button>
      <button
        type="button"
        onClick={() =>
          dispatch({
            type: "saved",
            strategyId: "s1",
            revision: 2,
            specHash: "c".repeat(64),
            canonicalJson: null,
            source: state.source,
            documentEpoch: state.documentEpoch,
            sourceVersion: state.sourceVersion,
          })
        }
      >
        Commit immutable revision
      </button>
      <ServerDraftBanner sync={sync} />
    </>
  );
};

const mount = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("server draft CAS", () => {
  it("persists exact invalid source without waiting for compilation", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockImplementation(async (_id, request) => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        return remoteDraft(request.source, request.expected_version + 1);
      });
    mount();
    await screen.findByText("서버 초안 동기화됨");

    fireEvent.click(
      screen.getByRole("button", { name: "Inject exact invalid source" }),
    );

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]).toEqual([
      DRAFT_ID,
      expect.objectContaining({
        expected_version: 0,
        source: "title: [broken\r\n",
      }),
    ]);
  });

  it("serializes this tab's writes and advances expected_version", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    let resolveFirst: ((draft: StrategyDraft) => void) | undefined;
    const first = new Promise<StrategyDraft>((resolve) => {
      resolveFirst = resolve;
    });
    const requests: SaveStrategyDraftRequest[] = [];
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockImplementation(async (_id, request) => {
        requests.push(request);
        if (requests.length === 1) return first;
        return remoteDraft(request.source, request.expected_version + 1);
      });
    mount();
    await screen.findByText("서버 초안 동기화됨");
    const source = screen.getByLabelText("Source");
    fireEvent.change(source, { target: { value: "first invalid: [" } });
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    fireEvent.change(source, { target: { value: "second invalid: [" } });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(save).toHaveBeenCalledTimes(1);
    resolveFirst?.(remoteDraft("first invalid: [", 1));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(requests.map((request) => request.expected_version)).toEqual([0, 1]);
    expect(requests[1].source).toBe("second invalid: [");
  });

  it("never overwrites a competing client until the user keeps local explicitly", async () => {
    const competing = remoteDraft("title: remote\n", 3);
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockRejectedValueOnce(
        new ApiRequestError(
          "save",
          409,
          "strategy.draft.conflict",
          "conflict",
          null,
          competing,
        ),
      )
      .mockImplementation(async (_id, request) =>
        remoteDraft(request.source, request.expected_version + 1),
      );
    mount();
    await screen.findByText("서버 초안 동기화됨");
    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: "title: local\n" },
    });
    const conflict = await screen.findByRole("region", {
      name: "서버 초안 충돌",
    });
    expect(save).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Source")).toHaveValue("title: local\n");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(save).toHaveBeenCalledTimes(1);

    await userEvent.click(
      screen.getByRole("button", { name: "현재 문서 유지·재시도" }),
    );
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(conflict).not.toBeInTheDocument();
    expect(save.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        expected_version: 3,
        source: "title: local\n",
      }),
    );
  });

  it("offers a compatible remote recovery and blocks an incompatible base", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockResolvedValue(
      remoteDraft("title: recovered\n", 2),
    );
    mount();
    await screen.findByRole("region", { name: "복구할 서버 초안" });
    await userEvent.click(
      screen.getByRole("button", { name: "서버 초안 적용" }),
    );
    expect(screen.getByLabelText("Source")).toHaveValue("title: recovered\n");

    cleanup();
    vi.restoreAllMocks();
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockResolvedValue({
      ...remoteDraft("title: wrong base\n", 2),
      base_revision: 9,
    });
    mount();
    expect(
      await screen.findByText("호환되지 않는 서버 초안"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "서버 초안 적용" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "현재 문서 유지·재시도" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "원문 다운로드" })).toHaveAttribute(
      "download",
      expect.stringContaining("strategy-server-draft"),
    );
  });

  it("deletes a recovered register when a clean local base is kept", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockResolvedValue(
      remoteDraft("title: abandoned recovery\n", 4),
    );
    const remove = vi
      .spyOn(strategyWorkbenchApi, "deleteStrategyDraft")
      .mockResolvedValue();
    mount();
    await screen.findByRole("region", { name: "복구할 서버 초안" });
    await userEvent.click(
      screen.getByRole("button", { name: "현재 문서 유지·재시도" }),
    );
    await waitFor(() => expect(remove).toHaveBeenCalledWith(DRAFT_ID, 4));
    expect(screen.getByLabelText("Source")).toHaveValue(BASE);
    expect(await screen.findByText("서버 초안 동기화됨")).toBeInTheDocument();
  });

  it("CAS-deletes this base's draft after an immutable revision save", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockImplementation(async (_id, request) =>
        remoteDraft(request.source, request.expected_version + 1),
      );
    const remove = vi
      .spyOn(strategyWorkbenchApi, "deleteStrategyDraft")
      .mockResolvedValue();
    mount();
    await screen.findByText("서버 초안 동기화됨");
    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: "title: ready to publish\n" },
    });
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await screen.findByText("서버 초안 동기화됨");
    fireEvent.click(
      screen.getByRole("button", { name: "Commit immutable revision" }),
    );

    await waitFor(() => expect(remove).toHaveBeenCalledWith(DRAFT_ID, 1));
  });

  it("CAS-deletes a persisted edit when the document returns to its immutable base", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockImplementation(async (_id, request) => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        return remoteDraft(request.source, request.expected_version + 1);
      });
    const remove = vi
      .spyOn(strategyWorkbenchApi, "deleteStrategyDraft")
      .mockResolvedValue();
    mount();
    await screen.findByText(t("draft.server.synced"));

    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: "title: changed\n" },
    });
    await screen.findByText(t("draft.server.saving"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await screen.findByText(t("draft.server.synced"));

    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: BASE },
    });

    await waitFor(() => expect(remove).toHaveBeenCalledWith(DRAFT_ID, 1));
    expect(screen.getByLabelText("Source")).toHaveValue(BASE);
    expect(
      await screen.findByText(t("draft.server.synced")),
    ).toBeInTheDocument();
  });

  it("preserves a newer writer when reverting the local document races with CAS delete", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockImplementation(async (_id, request) => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        return remoteDraft(request.source, request.expected_version + 1);
      });
    const newer = remoteDraft("title: newer writer\n", 2);
    const remove = vi
      .spyOn(strategyWorkbenchApi, "deleteStrategyDraft")
      .mockRejectedValue(
        new ApiRequestError(
          "delete",
          409,
          "strategy.draft.conflict",
          "newer version exists",
          null,
          newer,
        ),
      );
    mount();
    await screen.findByText(t("draft.server.synced"));
    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: "title: changed\n" },
    });
    await screen.findByText(t("draft.server.saving"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await screen.findByText(t("draft.server.synced"));

    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: BASE },
    });

    await waitFor(() => expect(remove).toHaveBeenCalledWith(DRAFT_ID, 1));
    const conflict = await screen.findByRole("region", {
      name: t("draft.server.conflictTitle"),
    });
    expect(screen.getByLabelText("Source")).toHaveValue(BASE);
    expect(
      within(conflict).getByRole("button", {
        name: t("draft.server.applyRemote"),
      }),
    ).toBeInTheDocument();
  });

  it("shows a typed server rejection instead of misreporting it as offline", async () => {
    vi.spyOn(strategyWorkbenchApi, "getStrategyDraft").mockRejectedValue(
      new ApiRequestError("get", 404, "strategy.draft.not_found"),
    );
    const save = vi
      .spyOn(strategyWorkbenchApi, "saveStrategyDraft")
      .mockRejectedValue(
        new ApiRequestError(
          "save",
          422,
          "strategy.draft.invalid",
          "source is not valid UTF-8",
        ),
      );
    mount();
    await screen.findByText(t("draft.server.synced"));

    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: "title: rejected\n" },
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(t("draft.server.rejected"));
    expect(alert).toHaveTextContent("source is not valid UTF-8");
    expect(alert).not.toHaveTextContent(t("draft.server.offline"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(save).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: t("draft.server.applyRemote") }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: t("draft.server.keepLocal") }),
    ).not.toBeInTheDocument();
  });
});
