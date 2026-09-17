import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { decideDocumentUpgrade } from "../model/document-upgrade";

const LEGACY = 'schema_version: "1.0"\ntitle: 옛 문서\n';
const CURRENT = 'schema_version: "1.1"\ntitle: 새 문서\n';

const UNSUPPORTED = {
  code: "structure.unsupported_schema_version",
  kind: "structural" as const,
  severity: "error" as const,
  pointer: "/schema_version",
  message: "unsupported schema_version",
  range: null,
  nodeId: null,
};

/** 파싱·컴파일이 현재 텍스트를 따라잡은 상태를 만든다. */
const settled = (
  source: string,
  diagnostics: (typeof UNSUPPORTED)[] = [],
): DocumentState => {
  let state = documentReducer(initialDocumentState("yaml", ""), {
    type: "load",
    format: "yaml",
    source,
    strategyId: "s1",
    baseRevision: 1,
    baseSpecHash: "a".repeat(64),
  });
  state = documentReducer(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(source, "yaml"),
  });
  return documentReducer(state, {
    type: "compiled",
    version: state.sourceVersion,
    outcome: {
      diagnostics,
      spec: null,
      canonicalJson: null,
      specHash: null,
      schemaVersion: null,
      sourceHash: "",
    },
  });
};

const STORED = { revision: 1, generated: false, requires_upgrade: true };

describe("decideDocumentUpgrade", () => {
  it("offers the upgrade only when backend rejects the version and the text says 1.0", () => {
    expect(
      decideDocumentUpgrade(settled(LEGACY, [UNSUPPORTED]), STORED),
    ).toEqual({ kind: "upgradeable" });
    // backend 진단 없이 텍스트만 1.0이면 (아직 컴파일 전) 제안하지 않는다.
    expect(decideDocumentUpgrade(settled(LEGACY), STORED)).toEqual({
      kind: "none",
    });
    // backend가 거부했지만 텍스트가 1.0이 아니면 (예: "2.0") 변환 입력이 아니다.
    expect(
      decideDocumentUpgrade(
        settled('schema_version: "2.0"\ntitle: x\n', [UNSUPPORTED]),
        STORED,
      ),
    ).toEqual({ kind: "none" });
    expect(decideDocumentUpgrade(settled(CURRENT), STORED)).toEqual({
      kind: "none",
    });
  });

  it("never proposes an upgrade for a compile result that belongs to older text", () => {
    const stale = documentReducer(settled(LEGACY, [UNSUPPORTED]), {
      type: "edit",
      source: `${LEGACY}description: 편집\n`,
    });
    expect(decideDocumentUpgrade(stale, STORED)).toEqual({ kind: "none" });
  });

  it("marks a generated frozen row as save-only while it is still the base", () => {
    const frozen = { revision: 1, generated: true, requires_upgrade: true };
    expect(decideDocumentUpgrade(settled(CURRENT), frozen)).toEqual({
      kind: "frozen-generated",
    });
    // 새 revision으로 저장되면 base가 바뀌어 배너가 사라진다.
    const before = settled(CURRENT);
    const saved = documentReducer(before, {
      type: "saved",
      strategyId: "s1",
      revision: 2,
      specHash: "b".repeat(64),
      canonicalJson: null,
      source: CURRENT,
      documentEpoch: before.documentEpoch,
      sourceVersion: before.sourceVersion,
    });
    expect(decideDocumentUpgrade(saved, frozen)).toEqual({ kind: "none" });
    expect(
      decideDocumentUpgrade(settled(CURRENT), {
        ...frozen,
        requires_upgrade: false,
      }),
    ).toEqual({ kind: "none" });
    expect(decideDocumentUpgrade(settled(CURRENT), null)).toEqual({
      kind: "none",
    });
  });
});
