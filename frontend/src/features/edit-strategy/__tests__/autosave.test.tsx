import { EditorView } from "@codemirror/view";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import {
  clearDraft,
  draftKey,
  readDraft,
  writeDraft,
  type DraftRecord,
  type DraftStorage,
} from "../model/draft-store";
import { useAutosave } from "../model/use-autosave";
import { useStrategyDocument } from "../model/use-strategy-document";
import type { DocumentSource } from "../model/document-source";
import { RecoveryBanner } from "../ui/recovery-banner";
import { SourceEditor } from "../ui/source-editor";

const memoryStorage = (): DraftStorage & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
    removeItem: (key) => {
      data.delete(key);
    },
  };
};

const ORIGINAL = 'schema_version: "1.0"\ntitle: 원본\n';

const revisionSource = (source = ORIGINAL): DocumentSource => ({
  kind: "revision",
  document: {
    strategy_id: "s1",
    revision: 2,
    schema_version: "1.0",
    format: "yaml",
    source,
    source_hash: "b".repeat(64),
    spec: { title: "원본" } as never,
    spec_hash: "2".repeat(64),
    origin: "document",
    generated: false,
    created_at: "2026-09-04T09:30:00+00:00",
  },
});

const Harness = ({
  storage,
  schemaVersion = "1.0",
  source = revisionSource(),
}: {
  storage: DraftStorage;
  schemaVersion?: string | null;
  source?: DocumentSource;
}) => {
  const [state, dispatch] = useStrategyDocument(source);
  const autosave = useAutosave(state, dispatch, {
    schemaVersion,
    storage,
    now: () => "2026-09-04T10:00:00.000Z",
  });
  return (
    <>
      <output data-testid="dirty">{String(state.dirty)}</output>
      <output data-testid="lastSaved">{autosave.lastSavedAt ?? "-"}</output>
      {autosave.recovery ? (
        <RecoveryBanner
          recovery={autosave.recovery}
          original={state.savedSource ?? state.source}
        />
      ) : null}
      <SourceEditor state={state} dispatch={dispatch} />
      <button
        type="button"
        onClick={() =>
          dispatch({
            type: "saved",
            strategyId: "s1",
            revision: 3,
            specHash: "3".repeat(64),
            source: state.source,
            documentEpoch: state.documentEpoch,
            sourceVersion: state.sourceVersion,
          })
        }
      >
        fake-save
      </button>
    </>
  );
};

const mount = async (props: Parameters<typeof Harness>[0]) => {
  render(<Harness {...props} />);
  await screen.findByRole("textbox", { name: "편집기" });
  let view: EditorView | null = null;
  await waitFor(() => {
    const content = document.querySelector(".cm-content");
    view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
    expect(view).not.toBeNull();
  });
  return view as unknown as EditorView;
};

const type = (view: EditorView, text: string) =>
  act(() => {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: text },
    });
  });

afterEach(cleanup);

describe("draft store", () => {
  it("keys drafts by base, round-trips records and survives corrupt entries", () => {
    const storage = memoryStorage();
    expect(draftKey(null, null)).toBe("new");
    expect(draftKey("s1", 3)).toBe("s1@3");
    const record: DraftRecord = {
      key: "s1@3",
      format: "yaml",
      source: "title: x\n",
      strategyId: "s1",
      baseRevision: 3,
      baseSpecHash: "h",
      schemaVersion: "1.0",
      savedAt: "2026-09-04T10:00:00.000Z",
    };
    expect(writeDraft(storage, record)).toBe(true);
    expect(readDraft(storage, "s1@3")).toEqual(record);
    storage.data.set("strategy-workbench.draft.new", "{not json");
    expect(readDraft(storage, "new")).toBeNull();
    storage.data.set(
      "strategy-workbench.draft.other",
      JSON.stringify({ ...record, key: "s1@3" }),
    );
    expect(readDraft(storage, "other")).toBeNull(); // key mismatch is not trusted
    clearDraft(storage, "s1@3");
    expect(readDraft(storage, "s1@3")).toBeNull();
    expect(readDraft(null, "new")).toBeNull();
    expect(writeDraft(null, record)).toBe(false);
  });
});

describe("useAutosave", () => {
  it("writes the dirty text under the base key and clears it after a successful save", async () => {
    const user = userEvent.setup();
    const storage = memoryStorage();
    const view = await mount({ storage });
    type(view, `${ORIGINAL}description: 작업 중\n`);
    await waitFor(() =>
      expect(screen.getByTestId("lastSaved")).toHaveTextContent(
        "2026-09-04T10:00:00.000Z",
      ),
    );
    expect(readDraft(storage, "s1@2")).toMatchObject({
      source: `${ORIGINAL}description: 작업 중\n`,
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "2".repeat(64),
      schemaVersion: "1.0",
    });
    await user.click(screen.getByRole("button", { name: "fake-save" }));
    await waitFor(() =>
      expect(screen.getByTestId("dirty")).toHaveTextContent("false"),
    );
    expect(readDraft(storage, "s1@2")).toBeNull();
    expect(readDraft(storage, "s1@3")).toBeNull();
    expect(screen.getByTestId("lastSaved")).toHaveTextContent("-");
  });

  it("offers a differing local draft on load with a diff summary, restores it, or discards it", async () => {
    const user = userEvent.setup();
    const storage = memoryStorage();
    writeDraft(storage, {
      key: "s1@2",
      format: "yaml",
      source: `${ORIGINAL}description: 복구\n`,
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "2".repeat(64),
      schemaVersion: "1.0",
      savedAt: "2026-09-03T23:59:00.000Z",
    });
    const view = await mount({ storage });
    const banner = screen.getByRole("region", { name: "복구본" });
    expect(banner).toHaveTextContent("+1 / −0 줄");
    expect(banner).toHaveTextContent("2026-09-03 23:59");
    expect(banner).toHaveTextContent("+description: 복구");
    await user.click(screen.getByRole("button", { name: "복구본 불러오기" }));
    await waitFor(() =>
      expect(view.state.doc.toString()).toBe(`${ORIGINAL}description: 복구\n`),
    );
    expect(
      screen.queryByRole("region", { name: "복구본" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");

    cleanup();
    writeDraft(storage, {
      key: "s1@2",
      format: "yaml",
      source: `${ORIGINAL}description: 버림\n`,
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "2".repeat(64),
      schemaVersion: "1.0",
      savedAt: "2026-09-03T23:59:00.000Z",
    });
    await mount({ storage });
    await user.click(screen.getByRole("button", { name: "복구본 삭제" }));
    expect(readDraft(storage, "s1@2")).toBeNull();
    expect(
      screen.queryByRole("region", { name: "복구본" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer a draft identical to the server original", async () => {
    const storage = memoryStorage();
    writeDraft(storage, {
      key: "s1@2",
      format: "yaml",
      source: ORIGINAL,
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "2".repeat(64),
      schemaVersion: "1.0",
      savedAt: "2026-09-03T23:59:00.000Z",
    });
    await mount({ storage });
    expect(
      screen.queryByRole("region", { name: "복구본" }),
    ).not.toBeInTheDocument();
  });

  it("offers only a raw download when the draft targets another schema version", async () => {
    const storage = memoryStorage();
    writeDraft(storage, {
      key: "s1@2",
      format: "yaml",
      source: 'schema_version: "0.9"\ntitle: 옛 초안\n',
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "2".repeat(64),
      schemaVersion: "0.9",
      savedAt: "2026-09-03T23:59:00.000Z",
    });
    await mount({ storage, schemaVersion: "1.0" });
    const banner = screen.getByRole("region", { name: "복구본" });
    expect(banner).toHaveTextContent("schema 0.9");
    expect(
      screen.queryByRole("button", { name: "복구본 불러오기" }),
    ).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "원문 다운로드" });
    expect(link).toHaveAttribute("download", "strategy-draft-s1_2.yaml");
    expect(decodeURIComponent(link.getAttribute("href")!.split(",")[1])).toBe(
      'schema_version: "0.9"\ntitle: 옛 초안\n',
    );
  });
});
