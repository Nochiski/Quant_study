import { EditorView } from "@codemirror/view";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
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
  const pendingSave = useRef<{
    source: string;
    documentEpoch: number;
    sourceVersion: number;
  } | null>(null);
  const autosave = useAutosave(state, dispatch, {
    schemaVersion,
    storage,
    now: () => "2026-09-04T10:00:00.000Z",
  });
  return (
    <>
      <output data-testid="dirty">{String(state.dirty)}</output>
      <output data-testid="lastSaved">{autosave.lastSavedAt ?? "-"}</output>
      <output data-testid="available">{String(autosave.available)}</output>
      <output data-testid="recoveryOriginal">
        {autosave.recovery?.original ?? "-"}
      </output>
      {autosave.recovery ? (
        <RecoveryBanner recovery={autosave.recovery} />
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
      <button
        type="button"
        onClick={() => {
          pendingSave.current = {
            source: state.source,
            documentEpoch: state.documentEpoch,
            sourceVersion: state.sourceVersion,
          };
        }}
      >
        capture-save
      </button>
      <button
        type="button"
        onClick={() => {
          if (pendingSave.current === null) return;
          dispatch({
            type: "saved",
            strategyId: "s1",
            revision: 3,
            specHash: "3".repeat(64),
            ...pendingSave.current,
          });
        }}
      >
        accept-save
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
    storage.data.set(
      "strategy-workbench.draft.s1@3",
      JSON.stringify({ ...record, strategyId: "other" }),
    );
    expect(readDraft(storage, "s1@3")).toBeNull(); // embedded identity must derive the key
    storage.data.set(
      "strategy-workbench.draft.s1@3",
      JSON.stringify({ ...record, baseRevision: "3" }),
    );
    expect(readDraft(storage, "s1@3")).toBeNull(); // untrusted fields keep their runtime types
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

  it("clears the pre-save base and autosaves later edits under the advanced base", async () => {
    const user = userEvent.setup();
    const storage = memoryStorage();
    const view = await mount({ storage });
    const submitted = `${ORIGINAL}description: 제출됨\n`;
    const later = `${submitted}notes: 저장 중 추가 편집\n`;
    type(view, submitted);
    await waitFor(() =>
      expect(readDraft(storage, "s1@2")?.source).toBe(submitted),
    );
    await user.click(screen.getByRole("button", { name: "capture-save" }));
    type(view, later);
    await user.click(screen.getByRole("button", { name: "accept-save" }));

    await waitFor(() => expect(readDraft(storage, "s1@2")).toBeNull());
    expect(screen.getByTestId("dirty")).toHaveTextContent("true");
    await waitFor(() =>
      expect(readDraft(storage, "s1@3")?.source).toBe(later),
    );
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
    expect(banner).toHaveTextContent("현재 문서와 달라");
    expect(
      screen.queryByRole("button", { name: "복구본 불러오기" }),
    ).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "원문 다운로드" });
    expect(link).toHaveAttribute("download", "strategy-draft-s1_2.yaml");
    expect(decodeURIComponent(link.getAttribute("href")!.split(",")[1])).toBe(
      'schema_version: "0.9"\ntitle: 옛 초안\n',
    );
  });

  it("allows restore only when schema, format, base identity, and hash all match", async () => {
    const storage = memoryStorage();
    const baseRecord: DraftRecord = {
      key: "s1@2",
      format: "yaml",
      source: `${ORIGINAL}description: 복구\n`,
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "2".repeat(64),
      schemaVersion: "1.0",
      savedAt: "2026-09-03T23:59:00.000Z",
    };

    writeDraft(storage, { ...baseRecord, baseSpecHash: "9".repeat(64) });
    await mount({ storage });
    expect(
      screen.queryByRole("button", { name: "복구본 불러오기" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "원문 다운로드" })).toBeVisible();

    cleanup();
    writeDraft(storage, { ...baseRecord, format: "json" });
    await mount({ storage });
    expect(
      screen.queryByRole("button", { name: "복구본 불러오기" }),
    ).not.toBeInTheDocument();

    cleanup();
    writeDraft(storage, { ...baseRecord, schemaVersion: null });
    await mount({ storage, schemaVersion: null });
    expect(screen.getByRole("region", { name: "복구본" })).toHaveTextContent(
      "schema 계약을 확인할 수 없어",
    );
    expect(
      screen.queryByRole("button", { name: "복구본 불러오기" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the recovery diff baseline owned by the newly accepted saved source", async () => {
    const user = userEvent.setup();
    const storage = memoryStorage();
    const recovered =
      'schema_version: "1.0"\ntitle: 저장됨\ndescription: 복구\n';
    writeDraft(storage, {
      key: "s1@3",
      format: "yaml",
      source: recovered,
      strategyId: "s1",
      baseRevision: 3,
      baseSpecHash: "3".repeat(64),
      schemaVersion: "1.0",
      savedAt: "2026-09-03T23:59:00.000Z",
    });
    const view = await mount({ storage });
    const submitted = 'schema_version: "1.0"\ntitle: 저장됨\n';
    type(view, submitted);
    await user.click(screen.getByRole("button", { name: "capture-save" }));
    type(view, `${submitted}notes: 후속 편집\n`);
    await user.click(screen.getByRole("button", { name: "accept-save" }));

    await waitFor(() =>
      expect(screen.getByTestId("recoveryOriginal").textContent).toBe(
        submitted,
      ),
    );
    expect(screen.getByRole("region", { name: "복구본" })).toHaveTextContent(
      "+1 / −0 줄",
    );
  });

  it("reports autosave unavailable after a storage write failure", async () => {
    const storage = memoryStorage();
    storage.setItem = () => {
      throw new DOMException("quota", "QuotaExceededError");
    };
    const view = await mount({ storage });
    type(view, `${ORIGINAL}description: 저장 실패\n`);
    await waitFor(() =>
      expect(screen.getByTestId("available")).toHaveTextContent("false"),
    );
    expect(screen.getByTestId("lastSaved")).toHaveTextContent("-");
  });
});
