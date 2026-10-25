import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  documentReducer,
  initialDocumentState,
  type CompileOutcome,
  type DocumentState,
} from "../model/document-state";
import { saveStatusSummary, saveStatusText } from "../model/save-status";
import type { SaveStatus } from "../model/use-save-document";

const validSource = readBackendFixture("strategy_documents/quality_momentum.yaml");
const unknownFieldSource = readBackendFixture("strategy_documents/quality_momentum.unknown_key.yaml");
const idle: SaveStatus = { kind: "idle" };

const edited = (source: string) => {
  const state = documentReducer(initialDocumentState(), { type: "edit", source });
  return documentReducer(state, { type: "parsed", version: state.sourceVersion, result: parseSource(source, "yaml") });
};

const invalidCompile: CompileOutcome = {
  spec: null,
  canonicalJson: null,
  specHash: null,
  schemaVersion: "1.2",
  sourceHash: "source-hash",
  diagnostics: [{ kind: "structural", severity: "error", code: "unknown_field", pointer: "/risk/max_name_weigth", message: "정의되지 않은 필드입니다", range: null }],
};

const rejectedDocument = (): DocumentState => {
  const state = edited(unknownFieldSource);
  return documentReducer(state, { type: "compiled", version: state.sourceVersion, outcome: invalidCompile });
};

describe("저장 상태의 짧은 안내", () => {
  it("저장 시도 전의 구문 오류는 저장 실패가 아니라 수정할 다음 행동을 알린다", () => {
    const state = edited(`${validSource}\nbroken: [\n`);
    expect(state.parse?.status).toBe("rejected");
    expect(saveStatusSummary(state, idle)).toBe("저장 전 구문 오류 수정");
    expect(saveStatusText(state, idle)).toBe("구문 오류가 있어 저장할 수 없습니다");
  });

  it("저장 시도 없이 받은 현재 compile 오류도 저장 전 검증 안내로 구분한다", () => {
    const state = rejectedDocument();
    expect(state.dirty).toBe(true);
    expect(saveStatusSummary(state, idle)).toBe("저장 전 검증 오류 수정");
    expect(saveStatusText(state, idle)).toBe("검증 오류가 있어 저장할 수 없습니다");
  });

  it.each([
    [{ kind: "failed", documentEpoch: 0 }, "저장 실패", "저장 실패: 서버에 연결할 수 없습니다"],
    [{ kind: "invalid", detail: "서버 검증 원문", documentEpoch: 0 }, "저장 요청 거절", "저장 실패: 문서 검증 오류: 서버 검증 원문"],
    [{ kind: "conflict", detail: "서버 충돌 원문", latestRevision: 2, strategyId: "s1", baseRevision: 1, documentEpoch: 0 }, "저장 충돌", "충돌: 다른 곳에서 새 리비전이 저장되었습니다. 최신 리비전을 연 뒤 다시 저장하세요. (서버 충돌 원문)"],
  ] satisfies [SaveStatus, string, string][])("실제 저장 응답 %o는 편집 오류보다 우선하며 상세 문구를 보존한다", (status, summary, detail) => {
    const state = edited(`${validSource}\nbroken: [\n`);
    expect(saveStatusSummary(state, status)).toBe(summary);
    expect(saveStatusText(state, status)).toBe(detail);
  });

  it("새 입력 뒤 남은 이전 compile 오류를 현재 저장 문제로 표시하지 않는다", () => {
    const state = documentReducer(rejectedDocument(), { type: "edit", source: validSource });
    expect(state.compiled?.diagnostics).toHaveLength(1);
    expect(state.compiledVersion).not.toBe(state.sourceVersion);
    expect(saveStatusSummary(state, idle)).toBeNull();
  });

  it("새 입력 뒤 남은 이전 parse 오류도 수정 완료 여부를 추측하지 않는다", () => {
    const rejected = edited(`${validSource}\nbroken: [\n`);
    const state = documentReducer(rejected, { type: "edit", source: validSource });
    expect(state.parse?.status).toBe("rejected");
    expect(state.parsedVersion).not.toBe(state.sourceVersion);
    expect(saveStatusSummary(state, idle)).toBeNull();
  });

  it("저장 중 상태는 이전 편집 오류 대신 기존 저장 중 상세 안내를 유지한다", () => {
    const state = rejectedDocument();
    const status: SaveStatus = { kind: "saving", documentEpoch: state.documentEpoch };
    expect(saveStatusSummary(state, status)).toBeNull();
    expect(saveStatusText(state, status)).toBe("저장 중…");
  });

  it("빈 문서·저장된 원문·아직 검증 전인 일반 편집에는 오류 안내를 만들지 않는다", () => {
    const clean = documentReducer(initialDocumentState(), { type: "load", format: "yaml", source: validSource, strategyId: "s1", baseRevision: 1, baseSpecHash: "hash" });
    for (const state of [initialDocumentState(), clean, edited(validSource)]) {
      expect(saveStatusSummary(state, idle)).toBeNull();
    }
  });
});
