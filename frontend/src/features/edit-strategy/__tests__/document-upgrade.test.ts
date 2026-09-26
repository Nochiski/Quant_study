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

/** 버전 줄은 현재 버전인데 본문이 1.0 문법일 때 backend가 다는 힌트(P1-05). */
const LEGACY_SHAPE = {
  ...UNSUPPORTED,
  code: "structure.legacy_shape",
  pointer: "/factors",
  message:
    "1.0 문법입니다. factors 아래에 또 factors 목록을 두던 방식이라 지금 버전에서는 읽지 못합니다.",
};

const STORED = { revision: 1, generated: false, requires_upgrade: true };

describe("decideDocumentUpgrade", () => {
  it("offers the upgrade only when the backend rejects the document's schema version", () => {
    expect(
      decideDocumentUpgrade(settled(LEGACY, [UNSUPPORTED]), STORED),
    ).toEqual({ kind: "upgradeable" });
    // backend 진단 없이 텍스트만 1.0이면 (아직 컴파일 전) 제안하지 않는다.
    expect(decideDocumentUpgrade(settled(LEGACY), STORED)).toEqual({
      kind: "none",
    });
    // backend가 거부하면 텍스트의 버전 문자열이 무엇이든 제안한다: 변환 가능 여부는 업그레이드
    // endpoint(422 not_upgradeable)가 판정한다(Phase 2 감사 DEFECT-P2X-002: frontend 버전 리터럴 없음).
    expect(
      decideDocumentUpgrade(
        settled('schema_version: "2.0"\ntitle: x\n', [UNSUPPORTED]),
        STORED,
      ),
    ).toEqual({ kind: "upgradeable" });
    expect(decideDocumentUpgrade(settled(CURRENT), STORED)).toEqual({
      kind: "none",
    });
  });

  it("offers the upgrade when only the body is 1.0, not the version line", () => {
    // P1-05: `structure.legacy_shape`도 사용자가 할 일이 업그레이드라 같은 배너를 띄운다.
    expect(
      decideDocumentUpgrade(settled(CURRENT, [LEGACY_SHAPE]), STORED),
    ).toEqual({ kind: "upgradeable" });
    // 다른 구조 오류는 배너를 띄우지 않는다: 고칠 곳은 문제 목록이 가리키는 그 줄이다.
    expect(
      decideDocumentUpgrade(
        settled(CURRENT, [{ ...LEGACY_SHAPE, code: "structure.unknown_key" }]),
        STORED,
      ),
    ).toEqual({ kind: "none" });
  });

  it("never proposes an upgrade for a compile result that belongs to older text", () => {
    const stale = documentReducer(settled(LEGACY, [UNSUPPORTED]), {
      type: "edit",
      source: `${LEGACY}description: 편집\n`,
    });
    expect(decideDocumentUpgrade(stale, STORED)).toEqual({ kind: "none" });
  });

  it("lets 1.0 text pasted into a generated frozen row upgrade instead of dead-ending", () => {
    // P2-02 리뷰 P2-001: 봉투(legacy 동결)보다 현재 텍스트(1.0)가 우선한다.
    const frozen = { revision: 1, generated: true, requires_upgrade: true };
    expect(
      decideDocumentUpgrade(settled(LEGACY, [UNSUPPORTED]), frozen),
    ).toEqual({ kind: "upgradeable" });
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
