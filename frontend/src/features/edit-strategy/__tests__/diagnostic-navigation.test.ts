import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  formCoversPointer,
  graphCoversPointer,
  resolveDiagnosticDestination,
} from "../model/diagnostic-navigation";
import { projectForm } from "../model/form-projection";
import type { JsonSchema } from "../model/schema-navigator";
import type { StrategyView } from "../model/strategy-views";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");

const parsed = parseSource(VERBOSE, "yaml");
const FORM = projectForm(SCHEMA, parsed, []);
const TREE = parsed.tree ?? {};

const destination = (view: StrategyView, pointer: string) =>
  resolveDiagnosticDestination({
    view,
    sourceView: "yaml",
    pointer,
    form: FORM,
    tree: TREE,
    schemaLoaded: true,
  });

describe("formCoversPointer", () => {
  it("covers the sections, items and fields the Form actually draws", () => {
    // schema 1.2(P2-03)에서 `data` 절이 빠져 Form 이 그리는 스칼라 필드로 `/risk/max_name_weight` 를 쓴다.
    expect(formCoversPointer(FORM, "/risk/max_name_weight")).toBe(true);
    expect(formCoversPointer(FORM, "/factors/0")).toBe(true);
    // graph 링크 필드가 그 아래 진단을 모두 받는다(`projectField`의 link 필드 규칙).
    expect(formCoversPointer(FORM, "/factors/0/graph/nodes/1")).toBe(true);
    // 문서에 없는 항목의 진단은 목록 섹션 카드가 받는다(`unabsorbedDiagnostics`).
    expect(formCoversPointer(FORM, "/factors/9")).toBe(true);
  });

  it("covers neither an unknown root key nor the whole document", () => {
    expect(formCoversPointer(FORM, "/nope")).toBe(false);
    expect(formCoversPointer(FORM, "/nope/deeper")).toBe(false);
    expect(formCoversPointer(FORM, "")).toBe(false);
  });

  it("covers nothing before the runtime schema arrives", () => {
    expect(formCoversPointer(null, "/risk/max_name_weight")).toBe(false);
  });
});

describe("graphCoversPointer", () => {
  it("covers only pointers inside the graph of a factor the document has", () => {
    expect(graphCoversPointer(TREE, "/factors/0/graph")).toBe(true);
    expect(
      graphCoversPointer(TREE, "/factors/0/graph/nodes/1/input_node_id"),
    ).toBe(true);
    // 팩터 카드(Form 소유)와 문서에 없는 팩터는 그래프 편집 표면이 그리지 않는다.
    expect(graphCoversPointer(TREE, "/factors/0/factor_id")).toBe(false);
    expect(graphCoversPointer(TREE, "/factors/9/graph")).toBe(false);
    expect(graphCoversPointer(TREE, "/risk/max_name_weight")).toBe(false);
  });
});

describe("resolveDiagnosticDestination", () => {
  it("stays in the source tab, which jumps to the line itself", () => {
    expect(destination("yaml", "/factors/0/graph/nodes/1")).toBe("source");
    expect(destination("yaml", "")).toBe("source");
  });

  it("keeps the Graph tab only when that graph is on screen", () => {
    expect(destination("graph", "/factors/0/graph/nodes/1")).toBe(
      "current-view",
    );
    expect(destination("graph", "/risk/max_name_weight")).toBe("source");
  });

  it("keeps the Form tab only when that card is on screen", () => {
    expect(destination("form", "/risk/max_name_weight")).toBe("current-view");
    expect(destination("form", "/nope")).toBe("source");
  });

  it("sends the read-only projection tabs back to the source tab", () => {
    expect(destination("json", "/risk/max_name_weight")).toBe("source");
    expect(destination("diff", "/risk/max_name_weight")).toBe("source");
  });

  it("sends the Graph tab to the source before the runtime schema arrives", () => {
    // 은퇴한 조건(`form !== null`)과 구분한다: 투영이 있어도 schema가 안 왔다고 말하면
    // 그래프 편집 표면이 렌더되지 않으므로 원문 탭으로 보낸다.
    expect(
      resolveDiagnosticDestination({
        view: "graph",
        sourceView: "yaml",
        pointer: "/factors/0/graph/nodes/1",
        form: FORM,
        tree: TREE,
        schemaLoaded: false,
      }),
    ).toBe("source");
  });

  it("sends a whole-document diagnostic back to the source tab", () => {
    expect(destination("graph", "")).toBe("source");
    expect(destination("form", "")).toBe("source");
  });
});
