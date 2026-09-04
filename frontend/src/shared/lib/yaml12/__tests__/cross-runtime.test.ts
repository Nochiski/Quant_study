/**
 * P0-03 YAML 1.2 cross-runtime contract (frontend side).
 *
 * ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md
 *
 * backend/tests/fixtures/strategy_documents/yaml12/manifest.json 의 case를 `yaml`(YAML 1.2 core
 * schema)로 읽어 accepted case는 기대 JSON과 같은 tree를, rejected case는 기대 reason code를 내는지
 * 검증한다. backend는 같은 manifest를 ruamel.yaml로 검증한다.
 *
 * 정책 구현은 `shared/lib/yaml12/parse.ts` 하나다 (P3-01). 이 테스트는 그 구현을 manifest로 검증한다.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, expect, test } from "vitest";
import { parseSource } from "..";

// fixture는 backend 디렉터리 한 곳에만 둔다 (ADR D3). cwd가 frontend/든 저장소 루트든 위로 올라가며 찾는다.
const FIXTURE_RELATIVE = "backend/tests/fixtures/strategy_documents/yaml12";
const findFixtures = (): string => {
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, FIXTURE_RELATIVE);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir)
      throw new Error(
        `fixtures not found — from=${process.cwd()} want=${FIXTURE_RELATIVE}`,
      );
    dir = parent;
  }
};
const FIXTURES = findFixtures();

type ManifestCase =
  | { name: string; file: string; expect: "accept"; json: unknown }
  | { name: string; file: string; expect: "reject"; reason: string };

const manifest = JSON.parse(
  readFileSync(resolve(FIXTURES, "manifest.json"), "utf8"),
) as { cases: ManifestCase[] };

const read = (file: string): string =>
  readFileSync(resolve(FIXTURES, file), "utf8");

describe("YAML 1.2 cross-runtime manifest", () => {
  const accepted = manifest.cases.filter((c) => c.expect === "accept");
  const rejected = manifest.cases.filter((c) => c.expect === "reject");

  test.each(accepted)("accepted: $name", (item) => {
    if (item.expect !== "accept") throw new Error("unreachable");
    const parsed = parseSource(read(item.file), "yaml");
    expect(parsed.status).toBe("ok");
    // JSON 호환 tree가 계약이다. `-0`은 JSON round-trip에서 `0`이 되며 backend도 -0.0 == 0.0으로 본다.
    expect(JSON.parse(JSON.stringify(parsed.tree))).toEqual(item.json);
  });

  test.each(rejected)("rejected: $name → $reason", (item) => {
    if (item.expect !== "reject") throw new Error("unreachable");
    const parsed = parseSource(read(item.file), "yaml");
    expect(parsed.status).toBe("rejected");
    expect(parsed.diagnostics[0]?.code).toBe(`yaml.${item.reason}`);
  });
});
