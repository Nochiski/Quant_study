import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { NEW_STRATEGY_STARTER } from "../ui/new-strategy-page";

/** Phase 2 감사 DEFECT-P2X-001: 새 문서 템플릿의 버전 리터럴이 backend 발행 버전과 같은지 고정한다. */
describe("new strategy starter", () => {
  it("starts on the schema version the backend runtime schema publishes", () => {
    const schema = JSON.parse(
      readBackendFixture("strategy_documents/runtime-schema.json"),
    ) as { properties: { schema_version: { const: string } } };
    const parsed = parseSource(NEW_STRATEGY_STARTER, "yaml");
    expect(parsed.status).toBe("ok");
    if (parsed.status !== "ok") return;
    expect(parsed.tree.schema_version).toBe(
      schema.properties.schema_version.const,
    );
  });
});
