import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { StrategySpec } from "../../../entities/strategy";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  documentReducer,
  initialDocumentState,
  type CompileOutcome,
  type DocumentState,
} from "../model/document-state";
import { projectStrategySpec } from "../model/strategy-projection";
import { StrategyProjectionPanel } from "../ui/strategy-projection-panel";

afterEach(cleanup);

const authored = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.json"),
) as Record<string, unknown>;
delete authored.schema_version;
const SPEC = {
  ...authored,
  identity: { strategy_id: "s1", revision: 2, schema_version: "1.0" },
} as unknown as StrategySpec;
// Exact representative bytes returned by backend canonical_strategy_json: root schema version,
// no storage identity, sorted keys, and Python float lexical forms preserved.
const CANONICAL =
  '{"execution":{"fee_bps":15.0},"risk":{"minimum_trade_weight":0.0},"schema_version":"1.0"}';

const outcome = (valid = true): CompileOutcome => ({
  spec: valid ? SPEC : null,
  canonicalJson: valid ? CANONICAL : null,
  specHash: valid ? "a".repeat(64) : null,
  schemaVersion: "1.0",
  sourceHash: "b".repeat(64),
  diagnostics: valid
    ? []
    : [
        {
          code: "strategy.invalid",
          kind: "semantic",
          severity: "error",
          pointer: "/title",
          message: "invalid",
          range: null,
        },
      ],
});

const compile = (source = "title: valid\n"): DocumentState => {
  let state = documentReducer(initialDocumentState("yaml", ""), {
    type: "edit",
    source,
  });
  state = documentReducer(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(source, "yaml"),
  });
  return documentReducer(state, {
    type: "compiled",
    version: state.sourceVersion,
    outcome: outcome(),
  });
};

describe("StrategySpec projection model", () => {
  it("uses the backend canonical JSON and marks only the current compile as current", () => {
    const state = compile();
    const projection = projectStrategySpec(state);

    expect(projection).toMatchObject({
      status: "ready",
      spec: SPEC,
      specHash: "a".repeat(64),
      schemaVersion: "1.0",
      stale: false,
    });
    if (projection.status === "ready") {
      expect(projection.canonicalJson).toBe(CANONICAL);
      expect(projection.canonicalJson).toContain('"schema_version":"1.0"');
      expect(projection.canonicalJson).toContain('"fee_bps":15.0');
      expect(projection.canonicalJson).not.toContain("identity");
    }
  });

  it("fails closed instead of manufacturing canonical JSON on the client", () => {
    const state = documentReducer(
      initialDocumentState("yaml", "title: valid\n"),
      {
        type: "compiled",
        version: 0,
        outcome: { ...outcome(), canonicalJson: null },
      },
    );

    expect(state.lastValidCompiled).toBeNull();
    expect(projectStrategySpec(state)).toEqual({ status: "unavailable" });
  });

  it("retains the same-document last valid compile across syntax and semantic errors", () => {
    let state = compile();
    state = documentReducer(state, {
      type: "edit",
      source: "title: [broken\n",
    });
    state = documentReducer(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    expect(projectStrategySpec(state)).toMatchObject({
      status: "ready",
      spec: SPEC,
      stale: true,
    });

    state = documentReducer(state, { type: "edit", source: "title: bad\n" });
    state = documentReducer(state, {
      type: "parsed",
      version: state.sourceVersion,
      result: parseSource(state.source, "yaml"),
    });
    state = documentReducer(state, {
      type: "compiled",
      version: state.sourceVersion,
      outcome: outcome(false),
    });
    expect(state.compiled?.spec).toBeNull();
    expect(state.lastValidCompiled?.outcome.spec).toBe(SPEC);
    expect(projectStrategySpec(state)).toMatchObject({
      status: "ready",
      spec: SPEC,
      stale: true,
    });
  });

  it("does not manufacture a projection from an uncompiled saved revision", () => {
    const state = documentReducer(initialDocumentState(), {
      type: "load",
      format: "yaml",
      source: "title: saved\n",
      strategyId: "s1",
      baseRevision: 2,
      baseSpecHash: "a".repeat(64),
    });
    expect(projectStrategySpec(state)).toEqual({ status: "unavailable" });
  });
});

describe("StrategySpec projection UI", () => {
  const ready = projectStrategySpec(compile());

  it("shows canonical raw values in a read-only professional summary", () => {
    render(<StrategyProjectionPanel projection={ready} view="form" />);

    expect(
      screen.getByRole("region", { name: "StrategySpec 요약 Form" }),
    ).toBeInTheDocument();
    for (const heading of [
      "기본 정보",
      "데이터",
      "포트폴리오",
      "리스크",
      "실행",
    ])
      expect(
        screen.getByRole("heading", { name: heading }),
      ).toBeInTheDocument();
    expect(screen.getByText("max_name_weight")).toBeInTheDocument();
    expect(screen.getByText("0.05")).toBeInTheDocument();
    expect(screen.queryByText("strategy_id")).not.toBeInTheDocument();
    expect(screen.queryByText("revision")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("labels stale and unavailable projections without offering them as current", () => {
    if (ready.status !== "ready") throw new Error("fixture must project");
    const { rerender } = render(
      <StrategyProjectionPanel
        projection={{ ...ready, stale: true }}
        view="json"
      />,
    );
    const region = screen.getByRole("region", { name: "StrategySpec JSON" });
    expect(region).toHaveTextContent("STALE");
    expect(
      region.querySelector(".strategy-projection__json")?.textContent,
    ).toBe(CANONICAL);
    expect(screen.getByRole("status")).toHaveTextContent(
      "저장·실행에는 사용되지 않습니다",
    );

    rerender(
      <StrategyProjectionPanel
        projection={{ status: "unavailable" }}
        view="json"
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "검증을 통과한 StrategySpec이 아직 없습니다",
    );
  });
});
