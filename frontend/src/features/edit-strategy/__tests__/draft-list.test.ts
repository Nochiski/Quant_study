import { describe, expect, it } from "vitest";

import { listDrafts, writeDraft, type DraftRecord } from "../model/draft-store";

const record = (key: string, savedAt: string, strategyId: string | null = null): DraftRecord => ({
  key,
  format: "yaml",
  source: "title: 초안",
  strategyId,
  baseRevision: strategyId === null ? null : 2,
  baseSpecHash: null,
  schemaVersion: "1.2",
  savedAt,
});

describe("listDrafts", () => {
  it("보관된 초안을 최근부터 돌려주고 다른 키·깨진 값은 건너뛴다", () => {
    const storage = window.localStorage;
    storage.clear();
    writeDraft(storage, record("new", "2026-09-01T00:00:00Z"));
    writeDraft(storage, record("s1@2", "2026-09-03T00:00:00Z", "s1"));
    storage.setItem("strategy-workbench.draft.broken", "{");
    storage.setItem("other.key", "x");
    expect(listDrafts(storage).map((draft) => draft.key)).toEqual(["s1@2", "new"]);
    storage.clear();
  });

  it("보관함이 없으면 빈 목록이다", () => {
    expect(listDrafts(null)).toEqual([]);
  });
});
