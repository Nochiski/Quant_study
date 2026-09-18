import fc from "fast-check";
import { stringify } from "yaml";

/**
 * property test용 임의 YAML 문서 생성기(WORKFLOW P3-01). 실제 계약의 필드 목록을 복제하지 않는다:
 * 트랜잭션은 pointer·range만 보므로 "block 스타일 mapping/sequence/scalar에 주석·빈 컨테이너·block
 * scalar가 섞인 YAML"이면 충분하다. 키는 YAML plain 키로 안전한 식별자만 만든다(따옴표가 필요한 키는
 * 범위 밖, P3-01 리뷰 P2-3에 기록).
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
  // 여러 줄 문자열 → `yaml.stringify`가 block scalar(`|-`)로 찍는다.
  fc.constantFrom("l1\nl2", "first\nsecond\nthird"),
);

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
  mapping: fc.dictionary(identifier, tie("value"), { minKeys: 0, maxKeys: 4 }),
  sequence: fc.array(tie("value"), { minLength: 0, maxLength: 3 }),
}));

/** 루트는 mapping이며 `schema_version`을 첫 키로 둔다(문서 모양). */
export const strategyDocumentArbitrary = (): fc.Arbitrary<ArbitraryTree> =>
  fc
    .tuple(
      fc.dictionary(identifier, scalar, { minKeys: 1, maxKeys: 4 }),
      fc.dictionary(identifier, node.value, { minKeys: 0, maxKeys: 4 }),
    )
    .map(([leaf, rest]) => ({ schema_version: "1.1", ...leaf, ...rest }));

/**
 * 줄 인덱스 목록: 각 줄 앞에 그 줄의 들여쓰기로 `# c<n>` 주석 줄을 넣을지 결정한다. 문서가 대개 10줄
 * 안팎이므로 줄마다 독립적으로(약 1/3) 뽑아, "두 항목 사이의 주석"·"첫 형제 위의 주석"·"머리말 주석"
 * 같은 모양이 자주 나오게 한다(P3-02 리뷰 P1-2: 0~40 균등 3개로는 그 모양이 6000 샘플에 0건이었다).
 */
export const commentPlanArbitrary = (): fc.Arbitrary<readonly number[]> =>
  fc
    .array(fc.integer({ min: 0, max: 2 }), { minLength: 48, maxLength: 48 })
    .map((rolls) =>
      rolls.flatMap((roll, index) => (roll === 0 ? [index] : [])),
    );

/**
 * tree → YAML 텍스트. 주석은 block scalar 본문 안이 아닌 줄 앞에만 넣는다(들여쓰기가 같은 다음 줄의
 * 주석으로 읽힌다). 결과 텍스트가 정본이므로 property는 이 텍스트를 다시 parse한 tree를 기준으로 삼는다.
 */
export const toYaml = (
  tree: ArbitraryTree,
  eol: "\n" | "\r\n" = "\n",
  commentBefore: readonly number[] = [],
): string => {
  const lines = stringify(tree, { lineWidth: 0 })
    .replace(/\n$/, "")
    .split("\n");
  const out: string[] = [];
  let inBlockScalar = false;
  let blockIndent = 0;
  lines.forEach((line, index) => {
    const indent = line.length - line.trimStart().length;
    if (inBlockScalar && line.trim() !== "" && indent <= blockIndent)
      inBlockScalar = false;
    if (!inBlockScalar && commentBefore.includes(index)) {
      out.push(`${" ".repeat(indent)}# c${index}`);
    }
    out.push(line);
    if (!inBlockScalar && /(^|\s)[|>][-+]?$/.test(line)) {
      inBlockScalar = true;
      blockIndent = indent;
    }
  });
  const text = `${out.join("\n")}\n`;
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
