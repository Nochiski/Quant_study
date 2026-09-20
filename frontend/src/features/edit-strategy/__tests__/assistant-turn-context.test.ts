import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { assistantDocumentContext } from "../model/assistant-turn-context";
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

describe("assistantDocumentContext", () => {
  it("저장된 revision은 전략·리비전으로, 저장 전 문서는 draft id로 가리킨다", () => {
    const draft = initialDocumentState("yaml", SOURCE);
    expect(
      assistantDocumentContext(draft, { draftId: "draft-1" }).documentRef,
    ).toEqual({ draft_id: "draft-1" });

    const saved = documentReducer(draft, {
      type: "load",
      format: "yaml",
      source: SOURCE,
      strategyId: "s-1",
      baseRevision: 3,
      baseSpecHash: "hash",
    });
    expect(
      assistantDocumentContext(saved, { draftId: "draft-1" }).documentRef,
    ).toEqual({ strategy_id: "s-1", revision: 3 });
  });

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

    const { turnContext } = assistantDocumentContext(compiled, {
      draftId: "draft-1",
      environment: { start: "2021-01-01" },
    });
    expect(turnContext).toEqual({
      source_text: 'schema_version: "1.1"\ntitle: "edited"\n',
      source_format: "yaml",
      diagnostics: ["[error] /data: data 섹션이 필요합니다."],
      environment: { start: "2021-01-01" },
    });
  });

  it("텍스트가 진단보다 앞서 있으면 진단을 싣지 않는다", () => {
    const state = parsed(initialDocumentState("yaml", SOURCE));
    const typing = documentReducer(state, {
      type: "edit",
      source: 'schema_version: "1.1"\ntitle: "typing"\n',
    });
    const { turnContext } = assistantDocumentContext(typing, {
      draftId: "draft-1",
    });
    expect(turnContext.diagnostics).toEqual([]);
    expect(turnContext.source_text).toContain("typing");
  });
});
