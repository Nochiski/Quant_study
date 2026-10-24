/**
 * 안내 단계 목록의 단계 상태(`guideStepStatuses`·`guideStepState`)와 추천 질문 키. 입력은 backend runtime schema
 * fixture 와 손으로 쓴 원문이고, 화면과 같은 길(Form 투영 → 파이프라인 투영 → 안내 단계)로 만든다.
 */
import { describe, expect, it } from "vitest";

import { tOptional } from "../../../shared/config";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { itemErrorCount } from "../model/diagnostic-counts";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectForm } from "../model/form-projection";
import {
  guideStepState,
  guideStepStatuses,
  guideSteps,
  guideSuggestionKeys,
  type GuideStep,
} from "../model/guide-steps";
import { projectPipeline } from "../model/pipeline-projection";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const pipelineOf = (source: string, diagnostics: DocumentDiagnostic[] = []) => {
  const parse = parseSource(source, "yaml");
  return projectPipeline(
    SCHEMA,
    projectForm(SCHEMA, parse, diagnostics),
    parse.status === "ok" ? parse.tree : {},
  );
};

const statuses = (source: string, diagnostics: DocumentDiagnostic[] = []) => {
  const pipeline = pipelineOf(source, diagnostics);
  const steps = guideSteps(pipeline);
  const all = guideStepStatuses(steps, pipeline);
  return Object.fromEntries(steps.map((step, index) => [step.id, all[index]!]));
};

const error = (pointer: string): DocumentDiagnostic => ({
  code: "test.error",
  kind: "semantic",
  severity: "error",
  pointer,
  message: "오류",
  range: null,
});

const NAMED = 'schema_version: "1.2"\ntitle: 모멘텀\n';
const RULES = [
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

describe("guideStepStatuses", () => {
  it("빈 이름은 필수 미충족이고, 그 칸의 오류 진단은 이름 단계 오류로 센다", () => {
    const empty = statuses('schema_version: "1.2"\ntitle: ""\n', [error("/title")]);
    expect(empty.document).toEqual({ errors: 1, filled: false, required: "missing" });
    expect(statuses(NAMED).document).toEqual({ errors: 0, filled: true, required: "met" });
  });

  it("어느 칸에도 붙지 않은 문서 전체 진단은 이름 단계(루트 섹션)가 센다(가이드 리뷰 1·SoT F1)", () => {
    expect(statuses(NAMED, [error("")]).document.errors).toBe(1);
  });

  it("단계 섹션 자신의 진단은 그 단계가 세고, 목록만 떼어 낸 단계는 세지 않는다(가이드 리뷰 1·SoT F1)", () => {
    const universe = statuses(NAMED, [error("/eligibility")]);
    expect(universe.eligibility!.errors).toBe(1);

    const signal = statuses(NAMED, [error("/signal")]);
    expect(signal.signal!.errors).toBe(1);
    expect(signal["signal.factors"]!.errors).toBe(0);
  });

  it("거르기 규칙 항목이 있으면 유니버스 단계를 채운 것이고, 항목 칸의 오류는 그 단계로 모인다", () => {
    const result = statuses(RULES, [error("/eligibility/rules/1/field_id")]);
    expect(result.eligibility).toMatchObject({ errors: 1, filled: true });
    expect(result.document).toMatchObject({ errors: 0, filled: true });
    expect(result.portfolio).toMatchObject({ errors: 0, filled: false });
  });

  it("경고는 오류로 세지 않는다", () => {
    const result = statuses(NAMED, [{ ...error("/title"), severity: "warning" }]);
    expect(result.document!.errors).toBe(0);
  });
});

describe("guideStepState", () => {
  const stage: GuideStep = {
    kind: "stage",
    id: "portfolio",
    stage: "portfolio",
    listOnly: false,
    split: false,
    listPointers: [],
    primary: [],
    more: [],
  };
  const document: GuideStep = { kind: "document", id: "document" };

  it("오류가 있으면 방문했어도 오류다", () => {
    expect(
      guideStepState(stage, { errors: 1, filled: true, required: "none" }, true),
    ).toBe("error");
  });

  it("기본값 없는 필수 칸이 비면 미작성이고, 다 채우면 지나가지 않아도 완료다(가이드 리뷰 5)", () => {
    expect(
      guideStepState(document, { errors: 0, filled: false, required: "missing" }, true),
    ).toBe("todo");
    expect(
      guideStepState(document, { errors: 0, filled: true, required: "met" }, false),
    ).toBe("done");
  });

  it("필수 칸이 없는 단계는 값을 쓰지 않아도 한 번 지나가면 기본값을 받아들인 완료다(가이드 리뷰 5)", () => {
    const untouched = { errors: 0, filled: false, required: "none" } as const;
    expect(guideStepState(stage, untouched, false)).toBe("todo");
    expect(guideStepState(stage, untouched, true)).toBe("done");
  });

  it("확인 단계는 지나가도 완료가 아니다", () => {
    expect(
      guideStepState(
        { kind: "review", id: "review" },
        { errors: 0, filled: false, required: "none" },
        true,
      ),
    ).toBe("todo");
  });
});

describe("itemErrorCount", () => {
  it("항목 자신과 항목 칸의 오류만 세고 경고는 세지 않는다(가이드 리뷰 8)", () => {
    const pipeline = pipelineOf(RULES, [
      error("/eligibility/rules/1/field_id"),
      error("/eligibility/rules/1"),
      { ...error("/eligibility/rules/0/value"), severity: "warning" },
    ]);
    const rules = pipeline.stages
      .find((stage) => stage.stage === "eligibility")!
      .lists.find((list) => list.key === "rules")!;
    expect(rules.items.map(itemErrorCount)).toEqual([0, 2]);
  });
});

describe("guideSuggestionKeys", () => {
  it("모든 안내 단계 id 에 AI 추천 질문 첫 문구가 있다(SoT F5: id 가 바뀌면 문구가 조용히 사라진다)", () => {
    const steps = guideSteps(pipelineOf(NAMED));
    expect(steps.length).toBeGreaterThan(2);
    for (const step of steps)
      expect(tOptional(guideSuggestionKeys(step)[0]!), step.id).not.toBeNull();
  });
});
