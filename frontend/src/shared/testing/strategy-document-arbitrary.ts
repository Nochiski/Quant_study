import fc from "fast-check";
import { stringify } from "yaml";

/**
 * property test용 임의 1.1 문서 생성기(WORKFLOW P3-01). 실제 계약의 필드 목록을 복제하지 않는다:
 * 트랜잭션은 pointer·range만 보므로 "block 스타일 mapping/sequence/scalar가 섞인 YAML"이면 충분하다.
 * 키는 YAML plain 키로 안전한 식별자만, 문자열 값은 `yaml.stringify`가 왕복 가능한 것만 만든다.
 */
export type ArbitraryTree = Record<string, unknown>;

const identifier = fc
  .stringMatching(/^[a-z][a-z0-9_]{0,7}$/)
  .filter(
    (key) => !["yes", "no", "on", "off", "null", "true", "false"].includes(key),
  );

const scalar = fc.oneof(
  fc.integer({ min: -1000, max: 1000 }),
  fc
    .double({ min: -100, max: 100, noNaN: true, noDefaultInfinity: true })
    .map((n) => Number(n.toFixed(3))),
  fc.boolean(),
  fc.constant(null),
  fc
    .stringMatching(/^[a-zA-Z가-힣][a-zA-Z가-힣0-9 _.-]{0,15}$/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0),
  fc.constantFrom("1.0", "2026-01-02", "yes", "", "a: b", "#tag", "- item"),
);

const leafMapping = fc.dictionary(identifier, scalar, {
  minKeys: 1,
  maxKeys: 4,
});

const node = fc.letrec<{
  value: unknown;
  mapping: Record<string, unknown>;
  sequence: unknown[];
}>((tie) => ({
  value: fc.oneof(
    { depthSize: "small" },
    scalar,
    tie("mapping"),
    tie("sequence"),
  ),
  mapping: fc.dictionary(identifier, tie("value"), { minKeys: 1, maxKeys: 4 }),
  sequence: fc.array(tie("value"), { minLength: 1, maxLength: 3 }),
}));

/** 루트는 mapping이며 `schema_version`을 첫 키로 둔다(문서 모양). */
export const strategyDocumentArbitrary = (): fc.Arbitrary<ArbitraryTree> =>
  fc
    .tuple(
      leafMapping,
      fc.dictionary(identifier, node.value, { minKeys: 0, maxKeys: 4 }),
    )
    .map(([leaf, rest]) => ({ schema_version: "1.1", ...leaf, ...rest }));

export const toYaml = (
  tree: ArbitraryTree,
  eol: "\n" | "\r\n" = "\n",
): string => {
  const text = stringify(tree, { lineWidth: 0 });
  return eol === "\n" ? text : text.replaceAll("\n", "\r\n");
};

export type PointerKind = "scalar" | "mapping" | "sequence";

/** tree의 모든 pointer와 종류(연산 선택용). 루트는 제외한다. */
export const pointersOf = (
  tree: unknown,
  prefix = "",
): { pointer: string; kind: PointerKind }[] => {
  const out: { pointer: string; kind: PointerKind }[] = [];
  const escape = (s: string) => s.replaceAll("~", "~0").replaceAll("/", "~1");
  const visit = (value: unknown, pointer: string): void => {
    if (Array.isArray(value)) {
      if (pointer !== "") out.push({ pointer, kind: "sequence" });
      value.forEach((item, index) => visit(item, `${pointer}/${index}`));
    } else if (typeof value === "object" && value !== null) {
      if (pointer !== "") out.push({ pointer, kind: "mapping" });
      for (const [key, item] of Object.entries(value))
        visit(item, `${pointer}/${escape(key)}`);
    } else if (pointer !== "") {
      out.push({ pointer, kind: "scalar" });
    }
  };
  visit(tree, prefix);
  return out;
};

export const scalarArbitrary = scalar;
export const valueArbitrary = node.value;
export const identifierArbitrary = identifier;
