import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import {
  assistantDocumentRef,
  assistantTurnContext,
} from "../model/assistant-turn-context";
import { useAssistantTurnContext } from "../model/use-assistant-turn-context";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";

const SOURCE = 'schema_version: "1.1"\ntitle: "t"\n';

const parsed = (state: DocumentState): DocumentState =>
  documentReducer(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(state.source, "yaml"),
  });

describe("assistantDocumentRef", () => {
  it("저장된 revision은 전략·리비전으로, 저장 전 문서는 draft id로 가리킨다", () => {
    expect(assistantDocumentRef(null, null, "draft-1")).toEqual({
      draft_id: "draft-1",
    });
    expect(assistantDocumentRef("s-1", 3, "draft-1")).toEqual({
      strategy_id: "s-1",
      revision: 3,
    });
  });
});

describe("assistantTurnContext", () => {

  it("현재 텍스트와 실행 설정을 싣고, 같은 버전의 진단만 문장 그대로 옮긴다", () => {
    const state = parsed(
      documentReducer(initialDocumentState("yaml", SOURCE), {
        type: "edit",
        source: 'schema_version: "1.1"\ntitle: "edited"\n',
      }),
    );
    const compiled = documentReducer(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: {
        spec: null,
        canonicalJson: null,
        specHash: null,
        schemaVersion: null,
        sourceHash: "h",
        diagnostics: [
          {
            code: "structure.missing_key",
            kind: "structural",
            severity: "error",
            pointer: "/data",
            message: "data 섹션이 필요합니다.",
            range: null,
          },
        ],
      },
    });

    const turnContext = assistantTurnContext(compiled, {
      start: "2021-01-01",
    });
    expect(turnContext).toEqual({
      source_text: 'schema_version: "1.1"\ntitle: "edited"\n',
      source_format: "yaml",
      diagnostics: ["[error] /data: data 섹션이 필요합니다."],
      environment: { start: "2021-01-01" },
    });
  });

  it("편집기가 reducer보다 앞서 있으면 편집기 텍스트를 싣고 진단은 비운다", () => {
    const state = parsed(initialDocumentState("yaml", SOURCE));
    const live = 'schema_version: "1.1"\ntitle: "방금 친 제목"\n';
    const withDiagnostics = documentReducer(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: {
        spec: null,
        canonicalJson: null,
        specHash: null,
        schemaVersion: null,
        sourceHash: "h",
        diagnostics: [
          {
            code: "structure.missing_key",
            kind: "structural",
            severity: "error",
            pointer: "/data",
            message: "data 섹션이 필요합니다.",
            range: null,
          },
        ],
      },
    });

    expect(
      assistantTurnContext(withDiagnostics, null, {
        source: live,
        format: "yaml",
      }),
    ).toEqual({
      source_text: live,
      source_format: "yaml",
      diagnostics: [],
      environment: null,
    });
    // 같은 텍스트면 진단을 그대로 싣는다.
    expect(
      assistantTurnContext(withDiagnostics, null, {
        source: SOURCE,
        format: "yaml",
      }).diagnostics,
    ).toEqual(["[error] /data: data 섹션이 필요합니다."]);
  });

  it("텍스트가 진단보다 앞서 있으면 진단을 싣지 않는다", () => {
    const state = parsed(initialDocumentState("yaml", SOURCE));
    const typing = documentReducer(state, {
      type: "edit",
      source: 'schema_version: "1.1"\ntitle: "typing"\n',
    });
    const turnContext = assistantTurnContext(typing);
    expect(turnContext.diagnostics).toEqual([]);
    expect(turnContext.source_text).toContain("typing");
  });
});

describe("useAssistantTurnContext", () => {
  it("호출 시점의 편집기 텍스트를 싣는다 — reducer가 아직 따라오지 못했어도", () => {
    // 페이지 배선을 그대로 흉내 낸다: reducer는 SOURCE에 머물러 있고 편집기는 이미 앞서 있다.
    let live = 'schema_version: "1.1"\ntitle: "편집기가 먼저"\n';
    const readSource = vi.fn(() => live);
    const { result, rerender } = renderHook(
      ({ state }) =>
        useAssistantTurnContext(state, readSource, { start: "2021-01-01" }),
      { initialProps: { state: parsed(initialDocumentState("yaml", SOURCE)) } },
    );
    expect(result.current().source_text).toBe(live);
    // 손잡이를 다시 부르면 값만 따라온다.
    live = 'schema_version: "1.1"\ntitle: "그 다음 글자"\n';
    const later = parsed(
      documentReducer(initialDocumentState("yaml", SOURCE), {
        type: "edit",
        source: 'schema_version: "1.1"\ntitle: "중간"\n',
      }),
    );
    act(() => rerender({ state: later }));
    expect(result.current()).toEqual({
      source_text: live,
      source_format: "yaml",
      diagnostics: [],
      environment: { start: "2021-01-01" },
    });
  });

  it("포맷 전환 중에는 편집기가 든 텍스트의 포맷을 싣는다", () => {
    // 포맷 전환은 reducer를 먼저 바꾸고 편집기 텍스트는 effect가 뒤따라 민다. 그 틈에 턴이 시작되면
    // 새 포맷 라벨에 옛 포맷 텍스트가 실리면 안 된다(B-04 리뷰 P3).
    let live = SOURCE;
    const { result, rerender } = renderHook(
      ({ state }) => useAssistantTurnContext(state, () => live, null),
      { initialProps: { state: initialDocumentState("yaml", SOURCE) } },
    );
    expect(result.current().source_format).toBe("yaml");

    const switched = documentReducer(initialDocumentState("yaml", SOURCE), {
      type: "load",
      format: "json",
      source: '{ "schema_version": "1.1" }',
      strategyId: null,
      baseRevision: null,
      baseSpecHash: null,
    });
    act(() => rerender({ state: switched }));
    expect(result.current()).toMatchObject({
      source_text: SOURCE,
      source_format: "yaml",
    });

    // 편집기가 따라온 뒤에는 새 포맷이다.
    live = '{ "schema_version": "1.1" }';
    expect(result.current()).toMatchObject({
      source_text: live,
      source_format: "json",
    });
  });
});
