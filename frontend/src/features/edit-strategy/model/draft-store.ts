/**
 * Local autosave of the editor text (WORKFLOW P3-06). One record per draft base: a new draft
 * or `strategyId@baseRevision`. The record carries what a recovery needs to be judged against
 * the server original later: the exact text and format, the base identity and hash, the schema
 * version the text was written for, and when it was taken. Storage is injectable so tests and
 * environments without `localStorage` (private mode, quota) degrade to "no recovery".
 */
import type { SourceFormat } from "../../../shared/lib/yaml12";

export type DraftRecord = {
  key: string;
  format: SourceFormat;
  source: string;
  strategyId: string | null;
  baseRevision: number | null;
  baseSpecHash: string | null;
  schemaVersion: string | null;
  savedAt: string;
};

export type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const PREFIX = "strategy-workbench.draft.";

export const draftKey = (
  strategyId: string | null,
  baseRevision: number | null,
): string =>
  strategyId === null || baseRevision === null
    ? "new"
    : `${strategyId}@${baseRevision}`;

const isNullableString = (value: unknown): value is string | null =>
  value === null || typeof value === "string";

const isRecord = (value: unknown): value is DraftRecord => {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<keyof DraftRecord, unknown>;
  const strategyId = record.strategyId;
  const baseRevision = record.baseRevision;
  if (
    typeof record.key !== "string" ||
    typeof record.source !== "string" ||
    (record.format !== "yaml" && record.format !== "json") ||
    !isNullableString(strategyId) ||
    (baseRevision !== null &&
      (typeof baseRevision !== "number" ||
        !Number.isInteger(baseRevision) ||
        baseRevision <= 0)) ||
    (strategyId === null) !== (baseRevision === null) ||
    strategyId === "" ||
    !isNullableString(record.baseSpecHash) ||
    !isNullableString(record.schemaVersion) ||
    typeof record.savedAt !== "string"
  ) {
    return false;
  }
  return record.key === draftKey(strategyId, baseRevision);
};

export const readDraft = (
  storage: Pick<Storage, "getItem"> | null,
  key: string,
): DraftRecord | null => {
  if (!storage) return null;
  try {
    const raw = storage.getItem(PREFIX + key);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return isRecord(parsed) && parsed.key === key ? parsed : null;
  } catch {
    return null; // corrupt or unreadable: no recovery rather than a crash
  }
};

export const writeDraft = (
  storage: DraftStorage | null,
  record: DraftRecord,
): boolean => {
  if (!storage) return false;
  try {
    storage.setItem(PREFIX + record.key, JSON.stringify(record));
    return true;
  } catch {
    return false; // quota exceeded or blocked: autosave silently unavailable
  }
};

export const clearDraft = (storage: DraftStorage | null, key: string): void => {
  try {
    storage?.removeItem(PREFIX + key);
  } catch {
    // nothing to clear
  }
};

/**
 * 보관된 로컬 초안 전부(연구 홈의 "저장 안 됨", RD-01-A). 각 줄은 `readDraft`와 같은 검사를 거치고 최근에
 * 보관한 것부터다. 보관함을 읽을 수 없으면 빈 목록이다.
 */
export const listDrafts = (
  storage: Pick<Storage, "getItem" | "key" | "length"> | null,
): DraftRecord[] => {
  if (!storage) return [];
  try {
    const records: DraftRecord[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const name = storage.key(index);
      if (name === null || !name.startsWith(PREFIX)) continue;
      const record = readDraft(storage, name.slice(PREFIX.length));
      if (record !== null) records.push(record);
    }
    return records.sort((left, right) => right.savedAt.localeCompare(left.savedAt));
  } catch {
    return [];
  }
};
