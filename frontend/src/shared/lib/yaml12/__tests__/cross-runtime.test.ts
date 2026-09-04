/**
 * P0-03 YAML 1.2 cross-runtime contract (frontend side).
 *
 * ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md
 *
 * backend/tests/fixtures/strategy_documents/yaml12/manifest.json 의 case를 `yaml`(YAML 1.2 core
 * schema)로 읽어 accepted case는 기대 JSON과 같은 tree를, rejected case는 기대 reason code를 내는지
 * 검증한다. backend는 같은 manifest를 ruamel.yaml로 검증한다.
 *
 * `loadYaml12Mapping`은 ADR D2 허용 문법의 임시 구현이다. P3-01 document state machine이
 * CST/source map과 함께 `shared/lib/yaml12`로 승격하면 이 helper는 그 구현을 호출한다.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, expect, test } from "vitest";
import {
  isAlias,
  isMap,
  isScalar,
  parseAllDocuments,
  visit,
  type Document,
} from "yaml";

// fixture는 backend 디렉터리 한 곳에만 둔다 (ADR D3). cwd가 frontend/든 저장소 루트든 위로 올라가며 찾는다.
const FIXTURE_RELATIVE = "backend/tests/fixtures/strategy_documents/yaml12";
const findFixtures = (): string => {
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, FIXTURE_RELATIVE);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`fixtures not found — from=${process.cwd()} want=${FIXTURE_RELATIVE}`);
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

class Yaml12Rejected extends Error {
  constructor(
    readonly reason: string,
    detail: string,
  ) {
    super(`yaml 1.2 document rejected — reason=${reason} detail=${detail}`);
  }
}

// ruamel.yaml 1.2 resolver가 숫자로 읽는 plain scalar 모양(`_` 구분자, `0b` 포함)과 YAML 1.2 core schema.
// 두 집합의 차가 backend는 숫자, frontend는 문자열로 읽는 silent corrupt 경로이므로 거부한다.
const RUAMEL_INT =
  /^[-+]?(?:0b[0-1_]+|0o?[0-7_]+|(?:0|[1-9][0-9_]*)|0x[0-9a-fA-F_]+)$/;
const RUAMEL_FLOAT =
  /^[-+]?(?:[0-9][0-9_]*\.[0-9_]*(?:[eE][-+]?[0-9]+)?|[0-9][0-9_]*[eE][-+]?[0-9]+|\.[0-9_]+(?:[eE][-+]?[0-9]+)?)$/;
const CORE_INT = /^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$/;
const CORE_FLOAT = /^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$/;
// ruamel 1.2 resolver의 float regex는 선행 `.` 분기에서 부호 없는 지수(`.5e3`)를 빠뜨려 문자열로 읽는다
// (`.5e+3`은 float). core에는 맞으므로 frontend가 숫자로 읽는 역방향 불일치. 양쪽 모두 거부한다.
const RUAMEL_MISSES_FLOAT = /^[-+]?\.[0-9]+[eE][0-9]+$/;
const DEFAULT_TAG_HANDLES: Record<string, string> = { "!!": "tag:yaml.org,2002:" };

const rejectAnchorOrTag = (
  anchor: string | undefined,
  tag: string | undefined,
): void => {
  if (anchor !== undefined) {
    throw new Yaml12Rejected("anchor_or_alias", `anchor=${anchor}`);
  }
  if (tag !== undefined) {
    throw new Yaml12Rejected("tag", `tag=${tag}`);
  }
};

const rejectPolicy = (doc: Document): void => {
  // backend(scan → policy → compose)와 같은 순서: syntax → directive → tag → duplicate → tree.
  for (const error of doc.errors) {
    if (error.code !== "DUPLICATE_KEY") {
      throw new Yaml12Rejected("syntax", error.message);
    }
  }
  if (doc.directives?.yaml.explicit) {
    throw new Yaml12Rejected("directive", `yaml=${doc.directives.yaml.version}`);
  }
  const tagHandles = Object.entries(doc.directives?.tags ?? {});
  if (tagHandles.some(([handle, prefix]) => DEFAULT_TAG_HANDLES[handle] !== prefix)) {
    throw new Yaml12Rejected("directive", `tag handles=${tagHandles.map(([h]) => h).join(",")}`);
  }
  for (const warning of doc.warnings) {
    if (warning.code === "TAG_RESOLVE_FAILED") {
      throw new Yaml12Rejected("tag", warning.message);
    }
  }
  for (const error of doc.errors) {
    throw new Yaml12Rejected("duplicate_key", error.message);
  }
  visit(doc, {
    Alias(_key, node) {
      throw new Yaml12Rejected("anchor_or_alias", `alias=${node.source}`);
    },
    // `Node`는 특정 visitor가 없을 때만 불리므로 Scalar/Collection 양쪽에서 anchor/tag를 검사한다.
    Collection(_key, node) {
      rejectAnchorOrTag(node.anchor, node.tag);
    },
    Pair(_key, pair) {
      if (!isScalar(pair.key) || typeof pair.key.value !== "string") {
        throw new Yaml12Rejected("non_string_key", `key=${String(pair.key)}`);
      }
      if (pair.key.type === "PLAIN" && pair.key.value === "<<") {
        throw new Yaml12Rejected("merge_key", "key=<<");
      }
    },
    Scalar(_key, node) {
      rejectAnchorOrTag(node.anchor, node.tag);
      const source = node.source;
      if (node.type === "PLAIN" && source === "<<") {
        throw new Yaml12Rejected("merge_key", "scalar=<<");
      }
      if (source !== undefined && node.type === "PLAIN") {
        const ruamelNumber = RUAMEL_INT.test(source) || RUAMEL_FLOAT.test(source);
        const coreNumber = CORE_INT.test(source) || CORE_FLOAT.test(source);
        if ((ruamelNumber && !coreNumber) || RUAMEL_MISSES_FLOAT.test(source)) {
          throw new Yaml12Rejected("non_core_number", `scalar=${source}`);
        }
      }
      if (typeof node.value === "number" && !Number.isFinite(node.value)) {
        throw new Yaml12Rejected("non_finite_number", `value=${String(node.value)}`);
      }
      // 정수 범위 검사는 정수 표기(core int)에만 적용한다. JS는 `1e16`도 정수로 보기 때문이다.
      if (
        typeof node.value === "number" &&
        source !== undefined &&
        node.type === "PLAIN" &&
        CORE_INT.test(source) &&
        !Number.isSafeInteger(node.value)
      ) {
        throw new Yaml12Rejected("integer_out_of_range", `value=${String(node.value)}`);
      }
    },
  });
};

export const loadYaml12Mapping = (text: string): Record<string, unknown> => {
  const docs = parseAllDocuments(text, {
    version: "1.2",
    schema: "core",
    merge: false,
    uniqueKeys: true,
  });
  if (docs.length === 0) {
    throw new Yaml12Rejected("not_a_mapping", "root=empty");
  }
  if (docs.length > 1) {
    throw new Yaml12Rejected("multiple_documents", `documents=${docs.length}`);
  }
  const doc = docs[0];
  if (isAlias(doc.contents)) {
    throw new Yaml12Rejected("anchor_or_alias", "root alias");
  }
  rejectPolicy(doc);
  if (!isMap(doc.contents)) {
    throw new Yaml12Rejected("not_a_mapping", `root=${doc.contents?.constructor.name ?? "null"}`);
  }
  return doc.toJS() as Record<string, unknown>;
};

const read = (file: string): string =>
  readFileSync(resolve(FIXTURES, file), "utf8");

describe("YAML 1.2 cross-runtime manifest", () => {
  const accepted = manifest.cases.filter((c) => c.expect === "accept");
  const rejected = manifest.cases.filter((c) => c.expect === "reject");

  test.each(accepted)("accepted: $name", (item) => {
    if (item.expect !== "accept") throw new Error("unreachable");
    const loaded = loadYaml12Mapping(read(item.file));
    // JSON 호환 tree가 계약이다. `-0`은 JSON round-trip에서 `0`이 되며 backend도 -0.0 == 0.0으로 본다.
    expect(JSON.parse(JSON.stringify(loaded))).toEqual(item.json);
  });

  test.each(rejected)("rejected: $name → $reason", (item) => {
    if (item.expect !== "reject") throw new Error("unreachable");
    let reason: string | null = null;
    try {
      loadYaml12Mapping(read(item.file));
    } catch (error) {
      if (!(error instanceof Yaml12Rejected)) throw error;
      reason = error.reason;
    }
    expect(reason).toBe(item.reason);
  });
});
