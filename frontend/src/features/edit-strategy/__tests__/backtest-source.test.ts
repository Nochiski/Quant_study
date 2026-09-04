import { describe, expect, it } from "vitest";

import type { StrategySpec } from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { decideBacktestSource } from "../model/backtest-source";
import {
  documentReducer,
  initialDocumentState,
  type CompileOutcome,
  type DocumentState,
} from "../model/document-state";
import { canSaveDocument } from "../model/use-save-document";

const SPEC = { title: "t" } as unknown as StrategySpec;
const BASE_HASH = "b".repeat(64);
const TEXT = 'schema_version: "1.0"\ntitle: t\n';

const outcome = (specHash: string | null, errors = false): CompileOutcome => ({
  spec: errors ? null : SPEC,
  canonicalJson: errors ? null : JSON.stringify(SPEC),
  specHash,
  schemaVersion: "1.0",
  sourceHash: "s".repeat(64),
  diagnostics: errors
    ? [
        {
          code: "x",
          kind: "semantic",
          severity: "error",
          pointer: "/title",
          message: "m",
          range: null,
        },
      ]
    : [],
});

const loaded = (base: boolean): DocumentState =>
  documentReducer(initialDocumentState(), {
    type: "load",
    format: "yaml",
    source: TEXT,
    strategyId: base ? "s1" : null,
    baseRevision: base ? 2 : null,
    baseSpecHash: base ? BASE_HASH : null,
  });

const parsed = (state: DocumentState): DocumentState =>
  documentReducer(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(state.source, "yaml"),
  });

const compiled = (
  state: DocumentState,
  result: CompileOutcome,
): DocumentState =>
  documentReducer(state, {
    type: "compiled",
    version: state.sourceVersion,
    outcome: result,
  });

describe("decideBacktestSource", () => {
  it("uses the saved revision only for a clean base whose backend hash matches", () => {
    const state = compiled(parsed(loaded(true)), outcome(BASE_HASH));
    expect(decideBacktestSource(state)).toEqual({
      kind: "saved_revision",
      reference: {
        kind: "saved_revision",
        strategy_id: "s1",
        revision: 2,
        expected_spec_hash: BASE_HASH,
      },
    });
  });

  it("falls back to an inline draft for a new strategy, after edits, and on a hash mismatch", () => {
    const fresh = compiled(parsed(loaded(false)), outcome("c".repeat(64)));
    expect(decideBacktestSource(fresh)).toMatchObject({
      kind: "inline_draft",
      draft: { kind: "inline_draft", spec: SPEC, source_hash: "s".repeat(64) },
    });
    const edited = documentReducer(loaded(true), {
      type: "edit",
      source: `${TEXT}description: x\n`,
    });
    const editedCompiled = compiled(parsed(edited), outcome(BASE_HASH));
    expect(editedCompiled.dirty).toBe(true);
    expect(decideBacktestSource(editedCompiled).kind).toBe("inline_draft");
    const mismatch = compiled(parsed(loaded(true)), outcome("d".repeat(64)));
    expect(decideBacktestSource(mismatch).kind).toBe("inline_draft");
  });

  it("blocks without any fallback when the document is invalid, stale, empty or composing", () => {
    const invalid = compiled(parsed(loaded(true)), outcome(null, true));
    expect(decideBacktestSource(invalid)).toEqual({
      kind: "blocked",
      reason: "invalid",
    });
    const stale = documentReducer(
      compiled(parsed(loaded(true)), outcome(BASE_HASH)),
      {
        type: "edit",
        source: `${TEXT}description: y\n`,
      },
    );
    expect(decideBacktestSource(stale)).toEqual({
      kind: "blocked",
      reason: "stale",
    });
    expect(decideBacktestSource(parsed(loaded(true)))).toEqual({
      kind: "blocked",
      reason: "stale",
    });
    const empty = documentReducer(loaded(false), {
      type: "edit",
      source: "  \n",
    });
    expect(decideBacktestSource(empty)).toEqual({
      kind: "blocked",
      reason: "empty",
    });
    const composing = documentReducer(
      compiled(parsed(loaded(true)), outcome(BASE_HASH)),
      {
        type: "composing",
        composing: true,
      },
    );
    expect(decideBacktestSource(composing)).toEqual({
      kind: "blocked",
      reason: "composing",
    });
  });

  it.each(["spec", "canonicalJson", "specHash", "schemaVersion"] as const)(
    "blocks Save and Backtest when a current compile omits %s",
    (field) => {
      const edited = documentReducer(loaded(true), {
        type: "edit",
        source: `${TEXT}description: changed\n`,
      });
      const incomplete = {
        ...outcome("c".repeat(64)),
        [field]: null,
      } as CompileOutcome;
      const state = compiled(parsed(edited), incomplete);

      expect(canSaveDocument(state)).toBe(false);
      expect(decideBacktestSource(state)).toEqual({
        kind: "blocked",
        reason: "invalid",
      });
    },
  );
});
