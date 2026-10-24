/**
 * 안내 단계 목록의 단계 상태(`guideStepStatus`). 입력은 backend runtime schema fixture 와 손으로 쓴 원문이고,
 * 화면과 같은 길(Form 투영 → 파이프라인 투영 → 안내 단계)로 만든다.
 */
import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectForm } from "../model/form-projection";
import { guideStepStatus, guideSteps } from "../model/guide-steps";
import { projectPipeline } from "../model/pipeline-projection";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const statuses = (source: string, diagnostics: DocumentDiagnostic[] = []) => {
  const parse = parseSource(source, "yaml");
  const pipeline = projectPipeline(
    SCHEMA,
    projectForm(SCHEMA, parse, diagnostics),
    parse.status === "ok" ? parse.tree : {},
  );
  return Object.fromEntries(
    guideSteps(pipeline).map((step) => [step.id, guideStepStatus(step, pipeline)]),
  );
};

const error = (pointer: string): DocumentDiagnostic => ({
  code: "test.error",
  kind: "semantic",
  severity: "error",
  pointer,
  message: "오류",
  range: null,
});

describe("guideStepStatus", () => {
  it("빈 이름은 채운 것이 아니고, 그 칸의 오류 진단은 이름 단계 오류로 센다", () => {
    const empty = statuses('schema_version: "1.2"\ntitle: ""\n', [error("/title")]);
    expect(empty.document).toEqual({ errors: 1, filled: false });
    expect(empty.eligibility).toEqual({ errors: 0, filled: false });

    const named = statuses('schema_version: "1.2"\ntitle: 모멘텀\n');
    expect(named.document).toEqual({ errors: 0, filled: true });
  });

  it("거르기 규칙 항목이 있으면 유니버스 단계를 채운 것이고, 항목 칸의 오류는 그 단계로 모인다", () => {
    const source = [
      'schema_version: "1.2"',
      "title: 모멘텀",
      "eligibility:",
      "  rules:",
      "    - field_id: price.market_cap",
      "      operator: gt",
      "      value: 0",
      "    - field_id: \"\"",
      "      operator: gte",
      "      value: 1",
      "",
    ].join("\n");
    const result = statuses(source, [error("/eligibility/rules/1/field_id")]);
    expect(result.eligibility).toEqual({ errors: 1, filled: true });
    // 다른 단계의 칸은 그 오류를 세지 않는다.
    expect(result.document).toEqual({ errors: 0, filled: true });
    expect(result.portfolio).toEqual({ errors: 0, filled: false });
  });

  it("경고는 오류로 세지 않고, 확인 단계는 늘 미작성이다", () => {
    const result = statuses('schema_version: "1.2"\ntitle: 모멘텀\n', [
      { ...error("/title"), severity: "warning" },
    ]);
    expect(result.document).toEqual({ errors: 0, filled: true });
    expect(result.review).toEqual({ errors: 0, filled: false });
  });
});
