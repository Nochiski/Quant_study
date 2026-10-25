import { describe, expect, it } from "vitest";

import type { BacktestRunSpec, StrategySpec } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { parseSource } from "../../../shared/lib/yaml12";
import { documentReducer, initialDocumentState } from "../model/document-state";
import { experimentEntryBlock } from "../model/experiment-entry";

const source = readBackendFixture("strategy_documents/quality_momentum.yaml");
const spec = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.json"),
) as StrategySpec;
const loaded = documentReducer(initialDocumentState(), {
  type: "load",
  source,
  format: "yaml",
  strategyId: "s1",
  baseRevision: 2,
  baseSpecHash: "base",
});
const parsed = documentReducer(loaded, {
  type: "parsed",
  version: loaded.sourceVersion,
  result: parseSource(source, "yaml"),
});
const saved = documentReducer(parsed, {
  type: "compiled",
  version: parsed.sourceVersion,
  outcome: {
    spec,
    specHash: "base",
    sourceHash: "source",
    schemaVersion: "1.2",
    canonicalJson: JSON.stringify(spec),
    diagnostics: [],
  },
});
const request: BacktestRunSpec = {
  strategy_source: {
    kind: "saved_revision",
    strategy_id: "s1",
    revision: 2,
    expected_spec_hash: "base",
  },
  parameter_values: { scale: 0 },
};

describe("IDE 실험 진입의 원문 소유권", () => {
  it("동일한 저장 원문과 해소된 현재 요청만 허용하고 입력을 바꾸지 않는다", () => {
    const before = JSON.stringify(request);
    expect(experimentEntryBlock(saved, request, false, true)).toBeNull();
    expect(JSON.stringify(request)).toBe(before);
  });
  it("dirty 원문은 옛 저장 요청이 있어도 보내지 않는다", () => {
    const edited = documentReducer(saved, {
      type: "edit",
      source: `${source}\n# 새 원문\n`,
    });
    expect(experimentEntryBlock(edited, request, false, true)).toBe(
      "research.ide.saveFirst",
    );
  });
  it("새 문서와 동결 판본은 먼저 저장·업그레이드를 안내한다", () => {
    expect(
      experimentEntryBlock(initialDocumentState(), request, false, true),
    ).toBe("research.ide.saveFirst");
    expect(experimentEntryBlock(saved, request, true, true)).toBe(
      "research.ide.upgrade",
    );
  });
  it("미해소 선택값과 빈 환경을 기본값으로 대체하지 않는다", () => {
    expect(experimentEntryBlock(saved, request, false, false)).toBe(
      "research.ide.parametersPending",
    );
    expect(experimentEntryBlock(saved, null, false, true)).toBe(
      "research.ide.environmentFirst",
    );
  });
  it("현재 검증이 없거나 저장 hash와 다른 원문은 옛 요청이 있어도 거절한다", () => {
    expect(experimentEntryBlock(loaded, request, false, true)).toBe(
      "research.ide.validateFirst",
    );
    expect(
      experimentEntryBlock(
        { ...saved, baseSpecHash: "old" },
        request,
        false,
        true,
      ),
    ).toBe("research.ide.saveFirst");
  });
});
