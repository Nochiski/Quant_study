import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * schema 1.2 가드(WORKFLOW P3-01): 전략 문서에서 빠진 `data`·`execution` 섹션과
 * `graph.missing_policy`(실행 설정으로 이동, P2-02·P2-03)를 가리키는 JSON Pointer 가 소스·테스트·e2e
 * 어디에도 남지 않는다. 남으면 진단·선택·되돌리기 경로가 1.2 문서에 없는 자리를 가리키거나, 테스트가
 * 1.1 계약을 계속 고정한다. 실행 설정 값은 문서 밖(`RunEnvironment`)이 소유한다.
 */
const FRONTEND = path.resolve(__dirname, "..", "..", "..");
const ROOTS = ["src", "e2e"].map((root) => path.join(FRONTEND, root));
const SELF = path.resolve(__filename);
const GENERATED = path.join(FRONTEND, "src", "shared", "api", "generated");

/** 따옴표 바로 뒤에서 시작하는 은퇴 포인터(`"/data"`·`'/execution/fee_bps'`)와 그래프 결측 정책 포인터. */
const RETIRED = [
  /["'`]\/(?:data|execution)(?:\/[^"'`\s]*)?["'`]/u,
  /\/graph\/missing_policy/u,
];

const sources = (): string[] =>
  ROOTS.flatMap((root) =>
    readdirSync(root, { recursive: true, withFileTypes: true })
      .filter(
        (entry) =>
          entry.isFile() &&
          /\.(?:ts|tsx|mjs)$/u.test(entry.name) &&
          !entry.parentPath.startsWith(GENERATED),
      )
      .map((entry) => path.join(entry.parentPath, entry.name)),
  )
    .filter((file) => path.resolve(file) !== SELF)
    .sort();

const retiredHits = (file: string): string[] => {
  const name = path.relative(FRONTEND, file).split(path.sep).join("/");
  return readFileSync(file, "utf8")
    .split(/\r?\n/u)
    .flatMap((line, index) =>
      RETIRED.some((pattern) => pattern.test(line))
        ? [`${name}:${index + 1}: ${line.trim()}`]
        : [],
    );
};

describe("schema 1.2 retired pointers", () => {
  it("scans the frontend sources and e2e specs", () => {
    expect(sources().length).toBeGreaterThan(100);
  });

  it("no source, test or e2e refers to /data, /execution or /graph/missing_policy", () => {
    expect(sources().flatMap(retiredHits)).toEqual([]);
  });

  it("recognizes the retired pointer shapes and leaves look-alikes alone", () => {
    expect(RETIRED.some((pattern) => pattern.test('pointer: "/data"'))).toBe(
      true,
    );
    expect(
      RETIRED.some((pattern) => pattern.test("'/execution/fee_bps'")),
    ).toBe(true);
    expect(
      RETIRED.some((pattern) =>
        pattern.test("`/factors/${index}/graph/missing_policy`"),
      ),
    ).toBe(true);
    expect(
      RETIRED.some((pattern) => pattern.test('"/api/v1/data-sets"')),
    ).toBe(false);
    expect(
      RETIRED.some((pattern) => pattern.test('"/database/path"')),
    ).toBe(false);
  });
});
